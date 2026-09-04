"""Atoms_Native — FastAPI backend.
Routes: auth, projects CRUD, SSE generate/refine/race, preview, export.
"""
import json
import os
import secrets
import time
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse, Response, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from config import HOST, PORT, RATE_LIMIT
from database import (
    get_conn, execute, init_db, row_to_dict, log_audit, save_feedback,
    query_audit, audit_alerts, verify_audit_chain,
)
import observability as obs
from auth import create_user, authenticate, create_session, require_user, require_role
from models import UserCreate, ProjectCreate, GenerateReq, RefineReq, RaceReq, SelectVersionReq
from agent.llm import list_models, list_choices, provider_available, LLM_PROVIDER
from agent import pipeline
from generation_service import commit_generation, GenerationConflict
from agent import race as race_mod

init_db()

app = FastAPI(title="Atoms_Native")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- 多租户分布式限流 + 并发守卫（server/ratelimit.py，fail-open + 进程内降级）----
import ratelimit as rl

# ---- 摩擦信号（server/friction.py）：识别「你和 AI 搏斗过」的会话并建议沉淀经验 ----
import friction

# ---- 产品方案版本发展历史（server/plan_versions.py）：文档版本化的可编程数据源 ----
import plan_versions as pv

# ---- 发现与模板（server/discover.py）：对齐 atoms.dev 的社区发现页，降低冷启动门槛 ----
import discover as disc


def _audit_ctx(request: Request | None, authorization: str | None):
    """从 Request/Header 提取 SOC 2 审计所需的 source_ip 与 session_id。"""
    ip = request.client.host if (request and request.client) else None
    sid = (authorization or "").replace("Bearer ", "").strip() or None
    return ip, sid


# ---------------- Auth ----------------
@app.post("/api/auth/register")
def register(body: UserCreate):
    uid, err = create_user(body.username, body.password)
    if err:
        raise HTTPException(status_code=400, detail=err)
    token = create_session(uid)
    conn = get_conn()
    u = conn.execute("SELECT id,username,role,created_at FROM users WHERE id=?", (uid,)).fetchone()
    conn.close()
    return {"token": token, "user": dict(u)}


@app.post("/api/auth/login")
def login(body: UserCreate, request: Request, authorization: str | None = Header(default=None)):
    ip = request.client.host if request.client else None
    sid = (authorization or "").replace("Bearer ", "").strip() or None
    uid = authenticate(body.username, body.password)
    if not uid:
        # SOC 2 CC7：失败登录进入不可变审计（高危告警来源）
        log_audit(None, "login", target=f"user:{body.username}", detail="密码错误",
                  source_ip=ip, session_id=sid, outcome="failure")
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = create_session(uid)
    conn = get_conn()
    u = conn.execute("SELECT id,username,role,created_at FROM users WHERE id=?", (uid,)).fetchone()
    conn.close()
    log_audit(uid, "login", target=f"user:{u['username']}", source_ip=ip, session_id=token)
    return {"token": token, "user": dict(u)}


@app.get("/api/me")
def me(user=Depends(require_user)):
    return {"user": dict(user)}


@app.get("/api/models")
def models():
    return {
        "available": list_models(),
        "choices": list_choices(),
        "default": LLM_PROVIDER,
        "mock": not provider_available(LLM_PROVIDER),
    }


# ---------------- Projects ----------------
@app.get("/api/projects")
def list_projects(user=Depends(require_user)):
    conn = get_conn()
    rows = conn.execute(
        "SELECT id,title,idea,status,current_version,created_at,updated_at FROM projects WHERE user_id=? ORDER BY updated_at DESC",
        (user["id"],),
    ).fetchall()
    conn.close()
    return {"projects": [dict(r) for r in rows]}


@app.post("/api/projects")
def create_project(body: ProjectCreate, user=Depends(require_user),
                  request: Request = None, authorization: str | None = Header(default=None)):
    ip, sid = _audit_ctx(request, authorization)
    title = (body.title or body.idea[:30]).strip()
    pid = execute(
        get_conn(),
        "INSERT INTO projects(user_id,title,idea,status) VALUES(?,?,?,?)",
        (user["id"], title, body.idea, "draft"),
    )
    conn = get_conn()
    p = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    conn.close()
    log_audit(user["id"], "create_project", f"project:{pid}", title, source_ip=ip, session_id=sid)
    return {"project": dict(p)}


def _get_project(pid: int, user):
    conn = get_conn()
    p = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    conn.close()
    if not p or p["user_id"] != user["id"]:
        return None
    return p


@app.get("/api/projects/{pid}")
def get_project(pid: int, user=Depends(require_user)):
    p = _get_project(pid, user)
    if not p:
        raise HTTPException(status_code=404, detail="项目不存在")
    conn = get_conn()
    versions = conn.execute(
        "SELECT id,version_no,model_used,race_winner,note,security_score,created_at,status,mock,parent_version,call_count FROM versions WHERE project_id=? ORDER BY version_no",
        (pid,),
    ).fetchall()
    messages = conn.execute(
        "SELECT id,role,content,created_at FROM messages WHERE project_id=? ORDER BY id", (pid,)
    ).fetchall()
    conn.close()
    cur_code = None
    if p["current_version"]:
        conn = get_conn()
        v = conn.execute("SELECT code FROM versions WHERE id=?", (p["current_version"],)).fetchone()
        conn.close()
        cur_code = v["code"] if v else None
    return {
        "project": dict(p),
        "versions": [dict(v) for v in versions],
        "messages": [dict(m) for m in messages],
        "current_code": cur_code,
        "app_state": p["app_state"] or "",
    }


@app.get("/api/projects/{pid}/state")
def get_project_state(pid: int, user=Depends(require_user)):
    """读取生成应用的沙箱数据快照（垫片恢复用）"""
    if not _get_project(pid, user):
        raise HTTPException(status_code=404, detail="项目不存在")
    conn = get_conn()
    row = conn.execute("SELECT app_state FROM projects WHERE id=?", (pid,)).fetchone()
    conn.close()
    return {"state": row["app_state"] or ""}


@app.post("/api/projects/{pid}/state")
def save_project_state(pid: int, body: dict, request: Request, user=Depends(require_user)):
    """保存预览 iframe 回传的 localStorage 快照（仅存 JSON，不执行任何应用代码）"""
    if not _get_project(pid, user):
        raise HTTPException(status_code=404, detail="项目不存在")
    s = body.get("state") if isinstance(body, dict) else None
    if not isinstance(s, str):
        raise HTTPException(status_code=422, detail="state 必须为字符串")
    if len(s.encode("utf-8")) > 256 * 1024:
        raise HTTPException(status_code=413, detail="状态超过 256KB 上限")
    conn = get_conn()
    conn.execute(
        "UPDATE projects SET app_state=?, updated_at=datetime('now') WHERE id=?",
        (s, pid),
    )
    conn.commit()
    conn.close()
    log_audit(user["id"], "save_project_state", f"project:{pid}", f"bytes={len(s)}",
              source_ip=request.client.host if request.client else None)
    return {"ok": True}


@app.delete("/api/projects/{pid}")
def delete_project(pid: int, user=Depends(require_user),
                  request: Request = None, authorization: str | None = Header(default=None)):
    p = _get_project(pid, user)
    if not p:
        raise HTTPException(status_code=404, detail="项目不存在")
    ip, sid = _audit_ctx(request, authorization)
    conn = get_conn()
    conn.execute("DELETE FROM versions WHERE project_id=?", (pid,))
    conn.execute("DELETE FROM messages WHERE project_id=?", (pid,))
    conn.execute("DELETE FROM projects WHERE id=?", (pid,))
    conn.commit()
    conn.close()
    log_audit(user["id"], "delete_project", f"project:{pid}", source_ip=ip, session_id=sid)
    return {"ok": True}


def _next_version(pid: int) -> int:
    conn = get_conn()
    row = conn.execute("SELECT COALESCE(MAX(version_no),0) AS m FROM versions WHERE project_id=?", (pid,)).fetchone()
    conn.close()
    return (row["m"] + 1) if row else 1


def _save_message(pid: int, role: str, content: str):
    execute(get_conn(), "INSERT INTO messages(project_id,role,content) VALUES(?,?,?)", (pid, role, content))


# ---------------- SSE core ----------------
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _generation_events(p, user, model, ip, sid, message=None, base_code=None):
    """One terminal event; only persisted code is released to the browser."""
    action = 'refine' if message is not None else 'generate'
    gen = None
    try:
        log_audit(user['id'], action + '_start', f"project:{p['id']}",
                  message[:40] if message is not None else model or LLM_PROVIDER,
                  source_ip=ip, session_id=sid)
        gen = pipeline.run_pipeline(p['idea'], model=model, refine_code=base_code,
                                    refine_msg=message, base_spec=p['spec_json'],
                                    base_arch=p['arch_json'], project_id=p['id'])
        while True:
            try:
                ev = next(gen)
            except StopIteration as end:
                final = end.value or {}
                break
            if ev.get('type') != 'app_code':
                yield _sse(ev)
            if not rl.renew(user['id']):
                raise GenerationConflict('任务锁已失效，请刷新后重试；未保存新版本。')
        status = final.get('status', 'failed')
        if status == 'failed':
            log_audit(user['id'], action + '_failed', f"project:{p['id']}",
                      final.get('error'), source_ip=ip, session_id=sid, outcome='failure')
            yield _sse({'type': 'error', 'status': 'failed', 'message': final.get('error') or '生成失败，未保存新版本。',
                        'project_id': p['id'], 'call_count': final.get('call_count', 0)})
            return
        vid = commit_generation(p, final, message)
        if vid is None:
            yield _sse({'type': 'done', 'status': 'unchanged', 'project_id': p['id'],
                        'version_id': p['current_version'], 'mock': final.get('mock'),
                        'call_count': final.get('call_count', 0),
                        'message': final.get('error') or '代码没有变化，已保留上一版。'})
            return
        # A logging failure AFTER commit must not turn a saved version into a
        # fake failure that invites a second billable request.
        try:
            log_audit(user['id'], action + '_done', f"project:{p['id']}",
                      f"version:{vid} status={status} mock={final['mock']}", source_ip=ip, session_id=sid)
        except Exception:
            yield _sse({'type': 'system', 'message': '版本已保存，但审计记录失败，请联系管理员检查。'})
        yield _sse({'type': 'app_code', 'code': final['code']})
        yield _sse({'type': 'done', 'status': status, 'project_id': p['id'], 'version_id': vid,
                    'mock': final['mock'], 'call_count': final['call_count'],
                    'security': final.get('security', {}).get('score')})
    except GenerationConflict as exc:
        yield _sse({'type': 'error', 'status': 'failed', 'message': str(exc), 'project_id': p['id']})
    except Exception:
        yield _sse({'type': 'error', 'status': 'failed', 'message': '任务执行失败，未交付新版本，请刷新后重试。',
                    'project_id': p['id']})
    finally:
        try:
            if gen is not None:
                gen.close()
        finally:
            rl.release(user['id'])


@app.post("/api/generate")
def generate(req: GenerateReq, user=Depends(require_user),
             request: Request = None, authorization: str | None = Header(default=None)):
    p = _get_project(req.project_id, user)
    if not p:
        raise HTTPException(status_code=404, detail="项目不存在")
    if not rl.acquire(user["id"]):
        raise HTTPException(status_code=429, detail="你有一个生成任务正在进行中，请稍候")
    r = rl.allow(user["id"], "generate")
    if not r["allowed"]:
        rl.release(user["id"])
        raise HTTPException(status_code=429, detail="生成过于频繁，请稍后再试（每小时上限 %d 次）" % RATE_LIMIT)
    ip, sid = _audit_ctx(request, authorization)
    return StreamingResponse(_generation_events(p, user, req.model, ip, sid),
                             media_type="text/event-stream", headers=SSE_HEADERS)


@app.post("/api/refine")
def refine(req: RefineReq, user=Depends(require_user),
           request: Request = None, authorization: str | None = Header(default=None)):
    p = _get_project(req.project_id, user)
    if not p:
        raise HTTPException(status_code=404, detail="项目不存在")
    if not p["current_version"]:
        raise HTTPException(status_code=400, detail="请先生成初始版本")
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="修改请求不能为空")
    conn = get_conn()
    v = conn.execute("SELECT code FROM versions WHERE id=?", (p["current_version"],)).fetchone()
    conn.close()
    base_code = v["code"] if v else ""
    if not rl.acquire(user["id"]):
        raise HTTPException(status_code=429, detail="你有一个生成任务正在进行中，请稍候")
    r = rl.allow(user["id"], "refine")
    if not r["allowed"]:
        rl.release(user["id"])
        raise HTTPException(status_code=429, detail="操作过于频繁，请稍后再试")
    ip, sid = _audit_ctx(request, authorization)
    return StreamingResponse(_generation_events(p, user, req.model, ip, sid, req.message, base_code),
                             media_type="text/event-stream", headers=SSE_HEADERS)


@app.post("/api/race")
def race(req: RaceReq, user=Depends(require_role("admin")),
         request: Request = None, authorization: str | None = Header(default=None)):
    """Race 模式不对普通用户开放（产品决策）：仅 admin 可调用，前端无入口。"""
    p = _get_project(req.project_id, user)
    if not p:
        raise HTTPException(status_code=404, detail="项目不存在")
    if not rl.acquire(user["id"]):
        raise HTTPException(status_code=429, detail="你有一个生成任务正在进行中，请稍候")
    r = rl.allow(user["id"], "race")
    if not r["allowed"]:
        rl.release(user["id"])
        raise HTTPException(status_code=429, detail="操作过于频繁，请稍后再试")
    ip, sid = _audit_ctx(request, authorization)
    log_audit(user["id"], "race_start", f"project:{p['id']}", ",".join(req.models),
              source_ip=ip, session_id=sid)

    def stream():
        final = {}
        try:
            gen = race_mod.run_race(p["idea"], req.models, project_id=p["id"])
            while True:
                try:
                    ev = next(gen)
                except StopIteration as e:
                    final = e.value or {}
                    break
                yield _sse(ev)
                rl.renew(user["id"])  # 锁续期
            # persist each candidate as a version
            mapping = {}
            conn = get_conn()
            for i, c in enumerate(final.get("candidates", [])):
                vno = _next_version(p["id"])
                vid = execute(conn,
                    "INSERT INTO versions(project_id,version_no,code,model_used,race_winner,note) VALUES(?,?,?,?,?,?)",
                    (p["id"], vno, c["code"], c["model"], 1 if i == 0 else 0, f"Race 候选 · 评分 {c['score']}"))
                mapping[c["model"]] = vid
            # set winner as current version
            if final.get("candidates"):
                winner_vid = mapping[final["candidates"][0]["model"]]
                conn.execute("UPDATE projects SET spec_json=?,arch_json=?,status='ready',current_version=?,updated_at=datetime('now') WHERE id=?",
                             (final.get("spec", ""), final.get("arch", ""), winner_vid, p["id"]))
            conn.commit()
            conn.close()
            winner = final.get("candidates", [{}])[0].get("model") if final.get("candidates") else None
            log_audit(user["id"], "race_done", f"project:{p['id']}", f"winner:{winner}",
                      source_ip=ip, session_id=sid)
            yield _sse({"type": "done", "project_id": p["id"], "version_map": mapping, "winner": winner})
        finally:
            rl.release(user["id"])

    return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)


@app.post("/api/projects/{pid}/select-version")
def select_version(pid: int, body: SelectVersionReq, user=Depends(require_user),
                   request: Request = None, authorization: str | None = Header(default=None)):
    p = _get_project(pid, user)
    if not p:
        raise HTTPException(status_code=404, detail="项目不存在")
    ip, sid = _audit_ctx(request, authorization)
    conn = get_conn()
    v = conn.execute("SELECT id FROM versions WHERE id=? AND project_id=?", (body.version_id, pid)).fetchone()
    if not v:
        conn.close()
        raise HTTPException(status_code=404, detail="版本不存在")
    conn.execute("UPDATE projects SET current_version=?,updated_at=datetime('now') WHERE id=?", (body.version_id, pid))
    conn.commit()
    conn.close()
    log_audit(user["id"], "select_version", f"project:{pid}", f"version:{body.version_id}",
              source_ip=ip, session_id=sid)
    return {"ok": True}


@app.get("/api/projects/{pid}/preview")
def preview(pid: int, user=Depends(require_user)):
    p = _get_project(pid, user)
    if not p or not p["current_version"]:
        raise HTTPException(status_code=404, detail="暂无预览")
    conn = get_conn()
    v = conn.execute("SELECT code FROM versions WHERE id=?", (p["current_version"],)).fetchone()
    conn.close()
    return Response(v["code"] if v else "", media_type="text/html; charset=utf-8")


@app.get("/api/projects/{pid}/export")
def export(pid: int, user=Depends(require_user)):
    p = _get_project(pid, user)
    if not p or not p["current_version"]:
        raise HTTPException(status_code=404, detail="暂无可导出版本")
    conn = get_conn()
    v = conn.execute("SELECT code FROM versions WHERE id=?", (p["current_version"],)).fetchone()
    conn.close()
    code = v["code"] if v else ""
    title = (p["title"] or "atoms-app").replace(" ", "_")
    return Response(code, media_type="text/html", headers={"Content-Disposition": f'attachment; filename="{title}.html"'})


# ---------------- Rollback / Observability / Feedback ----------------

@app.post("/api/projects/{pid}/rollback")
def rollback(pid: int, body: dict = {}, user=Depends(require_user),
            request: Request = None, authorization: str | None = Header(default=None)):
    """错误恢复与回滚：回退到上一版本，或指定 version_id（git revert 式）。"""
    p = _get_project(pid, user)
    if not p:
        raise HTTPException(status_code=404, detail="项目不存在")
    ip, sid = _audit_ctx(request, authorization)
    if not p["current_version"]:
        raise HTTPException(status_code=400, detail="暂无可回滚的版本")
    target = body.get("version_id")
    conn = get_conn()
    if target:
        v = conn.execute("SELECT id FROM versions WHERE id=? AND project_id=?", (target, pid)).fetchone()
        if not v:
            conn.close()
            raise HTTPException(status_code=404, detail="目标版本不存在")
        new_vid = target
    else:
        cur = conn.execute("SELECT version_no FROM versions WHERE id=?", (p["current_version"],)).fetchone()
        if not cur:
            conn.close()
            raise HTTPException(status_code=400, detail="当前版本异常")
        prev = conn.execute(
            "SELECT id FROM versions WHERE project_id=? AND version_no < ? ORDER BY version_no DESC LIMIT 1",
            (pid, cur["version_no"])).fetchone()
        if not prev:
            conn.close()
            raise HTTPException(status_code=400, detail="没有更早的版本可回滚")
        new_vid = prev["id"]
    conn.execute("UPDATE projects SET current_version=?,updated_at=datetime('now') WHERE id=?", (new_vid, pid))
    conn.commit()
    conn.close()
    log_audit(user["id"], "rollback", f"project:{pid}", f"to_version:{new_vid}",
              source_ip=ip, session_id=sid)
    # 摩擦信号：回滚说明新版本不如旧版本，这类需求值得复盘
    friction.record(pid, "rollback", f"回滚到版本 {new_vid}", user_id=user["id"])
    return {"ok": True, "version_id": new_vid}


@app.get("/api/metrics")
def metrics(user=Depends(require_user)):
    """监控与可观测性：生成质量/成本/延迟/安全/反馈汇总（研效看板数据源）。"""
    conn = get_conn()
    runs = conn.execute(
        "SELECT agent, model, COUNT(*) n, SUM(mock) mock_n, AVG(latency_ms) avg_ms, SUM(tokens) toks "
        "FROM agent_runs GROUP BY agent, model").fetchall()
    total_runs = conn.execute(
        "SELECT COUNT(*) c, COALESCE(SUM(mock),0) m, AVG(latency_ms) avg_ms, COALESCE(SUM(tokens),0) toks "
        "FROM agent_runs").fetchone()
    versions = conn.execute(
        "SELECT COUNT(*) c, AVG(security_score) avg_sec FROM versions WHERE security_score IS NOT NULL").fetchone()
    fb = conn.execute("SELECT COUNT(*) c, AVG(rating) avg_rating FROM feedback").fetchone()
    recent = conn.execute(
        "SELECT agent, model, latency_ms, mock, security_score, created_at FROM agent_runs "
        "ORDER BY id DESC LIMIT 20").fetchall()
    conn.close()
    # SOC 2 审计告警（供看板展示高危行为）
    alerts = audit_alerts(limit=500)
    return {
        "by_agent_model": [dict(r) for r in runs],
        "totals": dict(total_runs),
        "versions": dict(versions) if versions else {},
        "feedback": dict(fb) if fb else {},
        "recent_runs": [dict(r) for r in recent],
        "audit_alerts": alerts,
        "observability": obs.summary(),
        # 限流后端自省：redis / inproc 一眼可见，杜绝沉默降级
        "ratelimit": rl.status(),
        # 摩擦信号：24h 内「和 AI 搏斗过」的会话聚合，供经验沉淀排优先级
        "friction": friction.summary(window_hours=24),
    }


@app.get("/api/projects/{pid}/friction")
def project_friction(pid: int, user=Depends(require_user)):
    """单项目摩擦信号：值得沉淀经验时返回建议（对齐 TeamAI「搏斗过才打扰」）。"""
    _get_project(pid, user)  # 越权校验
    return friction.suggest(pid, window_hours=24) or {
        "project_id": pid, "worth_documenting": False, "score": 0, "reasons": []}


@app.post("/api/feedback")
def feedback(body: dict, user=Depends(require_user),
            request: Request = None, authorization: str | None = Header(default=None)):
    """用户反馈（👍/👎 或 1-5 评分），用于生成质量持续优化。"""
    pid = body.get("project_id")
    vid = body.get("version_id")
    rating = int(body.get("rating", 0))
    comment = body.get("comment")
    p = _get_project(pid, user) if pid else None
    ip, sid = _audit_ctx(request, authorization)
    fid = save_feedback(p["id"] if p else None, vid, user["id"], rating, comment)
    # 摩擦信号：用户点踩是「这次生成不达标」最直接的证据
    if rating < 0 and p:
        friction.record(p["id"], "negative_feedback",
                        f"rating:{rating}" + (f" · {str(comment)[:200]}" if comment else ""),
                        user_id=user["id"])
    log_audit(user["id"], "feedback", f"project:{pid}", f"rating:{rating}",
              source_ip=ip, session_id=sid)
    return {"ok": True, "id": fid}


# ---------------- Product plan version history (public) ----------------

@app.get("/api/plan/versions")
def plan_versions():
    """产品方案版本发展历史：索引表解析结果，时间倒序（最新在上）。公开接口。"""
    return {"versions": pv.list_versions(), "milestones": pv.milestones()}


@app.get("/api/plan/versions/{name}")
def plan_snapshot(name: str):
    """读取指定版本快照的 markdown 原文。文件名白名单校验，防路径穿越。"""
    text = pv.read_snapshot(name)
    if text is None:
        raise HTTPException(status_code=404, detail="版本快照不存在")
    return PlainTextResponse(text, media_type="text/markdown; charset=utf-8")


# ---------------- Discover / Templates (public) ----------------

@app.get("/api/discover")
def discover_list(q: str = "", sort: str = "views", author: str = ""):
    """发现页：社区精选模板。q 模糊搜索，sort=views(最热)|new(最新)，author=某人作品集。公开接口，惰性种子。"""
    disc.ensure_seed()
    return {"items": disc.list_items(q=q, sort=sort, author=author)}


@app.post("/api/discover/{item_id}/view")
def discover_view(item_id: int):
    """浏览量 +1（fire-and-forget）。"""
    return {"ok": disc.add_view(item_id)}


@app.get("/api/discover/{item_id}/sample")
def discover_sample(item_id: int):
    """模板真实示例：策展流程用真实大模型生成一次后回填的完整应用，公开直读。"""
    html = disc.get_sample(item_id)
    if not html:
        raise HTTPException(status_code=404, detail="该模板暂无真实示例")
    return Response(content=html, media_type="text/html; charset=utf-8")


@app.post("/api/discover/{item_id}/use")
def discover_use(item_id: int, user=Depends(require_user),
                request: Request = None, authorization: str | None = Header(default=None)):
    """一键把模板变成自己的项目（复制 idea 建项目，回工作台即可生成）。"""
    pid = disc.use_template(item_id, user["id"])
    if pid is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    ip, sid = _audit_ctx(request, authorization)
    log_audit(user["id"], "discover_use", f"project:{pid}", f"template:{item_id}",
              source_ip=ip, session_id=sid)
    return {"ok": True, "project_id": pid}


@app.post("/api/projects/{pid}/publish")
def project_publish(pid: int, user=Depends(require_user),
                     request: Request = None, authorization: str | None = Header(default=None)):
    """把我的项目发布到发现页（社区 Remix 闭环：他人「使用此模板」即改编）。"""
    item_id = disc.publish_item(user["id"], user["username"], pid)
    if item_id is None:
        raise HTTPException(status_code=404, detail="项目不存在或暂无版本，无法发布")
    ip, sid = _audit_ctx(request, authorization)
    log_audit(user["id"], "publish", f"discover:{item_id}", f"project:{pid}",
              source_ip=ip, session_id=sid)
    return {"ok": True, "item_id": item_id}


@app.post("/api/projects/{pid}/share")
def project_share(pid: int, user=Depends(require_user),
                  request: Request = None, authorization: str | None = Header(default=None)):
    """生成/复用项目只读分享 token。链接形如 /api/share/{token}，无需登录即可预览。"""
    p = _get_project(pid, user)
    if not p or not p["current_version"]:
        raise HTTPException(status_code=404, detail="项目不存在或暂无版本")
    token = p["share_token"] or secrets.token_urlsafe(10)
    conn = get_conn()
    conn.execute("UPDATE projects SET share_token=? WHERE id=?", (token, pid))
    conn.commit()
    conn.close()
    ip, sid = _audit_ctx(request, authorization)
    log_audit(user["id"], "share", f"project:{pid}", token[:6] + "…",
              source_ip=ip, session_id=sid)
    return {"ok": True, "token": token}


@app.get("/api/share/{token}")
def share_view(token: str):
    """公开只读预览（分享链接落点）：任何人可看，无需登录，不暴露源码以外的信息。"""
    conn = get_conn()
    p = conn.execute(
        "SELECT current_version FROM projects WHERE share_token=?", (token,)).fetchone()
    v = None
    if p and p["current_version"]:
        v = conn.execute("SELECT code FROM versions WHERE id=?", (p["current_version"],)).fetchone()
    conn.close()
    if not v or not v["code"]:
        raise HTTPException(status_code=404, detail="分享不存在或已失效")
    return Response(content=v["code"], media_type="text/html; charset=utf-8")


# ---------------- SOC 2 Audit (admin) ----------------

@app.get("/api/audit")
def audit_view(user=Depends(require_role("admin"))):
    """结构化审计事件 + 高危告警（SOC 2 CC6/CC7）。仅 admin。"""
    events = query_audit(limit=200)
    alerts = audit_alerts(limit=500)
    ok, broken = verify_audit_chain()
    return {
        "events": events,
        "alerts": alerts,
        "chain_intact": ok,
        "chain_broken_at": broken,
        "retention_days": int(__import__("os").environ.get("AUDIT_RETENTION_DAYS", "365")),
    }


@app.get("/api/admin/users")
def admin_users(user=Depends(require_role("admin"))):
    """用户列表（id/username/role/created_at），供管理端角色管理。仅 admin。"""
    ip, sid = _audit_ctx(None, None)
    log_audit(user["id"], "admin_view_users", "users:all",
              f"n_shown", source_ip=ip, session_id=sid)
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, username, role, created_at FROM users ORDER BY id ASC").fetchall()
    conn.close()
    return {"users": [dict(r) for r in rows]}


@app.post("/api/admin/set-role")
def admin_set_role(body: dict, admin=Depends(require_role("admin"))):
    """RBAC：为指定用户设置角色（最小权限）。仅 admin。"""
    username = body.get("username")
    role = body.get("role")
    if role not in ("user", "admin"):
        raise HTTPException(status_code=400, detail="role 仅支持 user/admin")
    conn = get_conn()
    r = conn.execute("UPDATE users SET role=? WHERE username=?", (role, username))
    conn.commit()
    conn.close()
    if r.rowcount == 0:
        raise HTTPException(status_code=404, detail="用户不存在")
    log_audit(admin["id"], "role_change", f"user:{username}", f"-> {role}")
    return {"ok": True, "username": username, "role": role}


# ---------------- Static frontend ----------------
PUBLIC_DIR = str(Path(__file__).resolve().parent.parent / "public")
app.mount("/", StaticFiles(directory=PUBLIC_DIR, html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=HOST, port=PORT, reload=False)
