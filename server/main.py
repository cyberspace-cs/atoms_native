"""Atoms_Native — FastAPI backend.
Routes: auth, projects CRUD, SSE generate/refine/race, preview, export.
"""
import json
import os
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
    u = conn.execute("SELECT id,username,created_at FROM users WHERE id=?", (uid,)).fetchone()
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
    u = conn.execute("SELECT id,username,created_at FROM users WHERE id=?", (uid,)).fetchone()
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
        "SELECT id,version_no,model_used,race_winner,note,security_score,created_at FROM versions WHERE project_id=? ORDER BY version_no",
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
    log_audit(user["id"], "generate_start", f"project:{p['id']}", req.model or LLM_PROVIDER,
              source_ip=ip, session_id=sid)

    def stream():
        final = {}
        try:
            gen = pipeline.run_pipeline(p["idea"], model=req.model, base_spec=p["spec_json"],
                                       base_arch=p["arch_json"], project_id=p["id"])
            while True:
                try:
                    ev = next(gen)
                except StopIteration as e:
                    final = e.value or {}
                    break
                yield _sse(ev)
            sec = final.get("security", {}).get("score")
            # persist
            vno = _next_version(p["id"])
            vid = execute(get_conn(),
                "INSERT INTO versions(project_id,version_no,code,model_used,note,security_score) VALUES(?,?,?,?,?,?)",
                (p["id"], vno, final.get("code", ""), final.get("model"),
                 ("离线模板" if final.get("mock") else "初版生成"), sec))
            conn = get_conn()
            conn.execute("UPDATE projects SET spec_json=?,arch_json=?,status='ready',current_version=?,updated_at=datetime('now') WHERE id=?",
                         (final.get("spec", ""), final.get("arch", ""), vid, p["id"]))
            conn.commit()
            conn.close()
            log_audit(user["id"], "generate_done", f"project:{p['id']}",
                      f"version:{vid} mock={final.get('mock')} sec={sec}", source_ip=ip, session_id=sid)
            yield _sse({"type": "done", "project_id": p["id"], "version_id": vid,
                        "mock": final.get("mock"), "security": sec})
        finally:
            rl.release(user["id"])

    return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)


@app.post("/api/refine")
def refine(req: RefineReq, user=Depends(require_user),
           request: Request = None, authorization: str | None = Header(default=None)):
    p = _get_project(req.project_id, user)
    if not p:
        raise HTTPException(status_code=404, detail="项目不存在")
    if not p["current_version"]:
        raise HTTPException(status_code=400, detail="请先生成初始版本")
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
    log_audit(user["id"], "refine_start", f"project:{p['id']}", req.message[:40],
              source_ip=ip, session_id=sid)

    def stream():
        final = {}
        try:
            gen = pipeline.run_pipeline(p["idea"], model=req.model, refine_code=base_code, refine_msg=req.message,
                                       base_spec=p["spec_json"], base_arch=p["arch_json"], project_id=p["id"])
            while True:
                try:
                    ev = next(gen)
                except StopIteration as e:
                    final = e.value or {}
                    break
                yield _sse(ev)
            _save_message(p["id"], "user", req.message)
            sec = final.get("security", {}).get("score")
            vno = _next_version(p["id"])
            vid = execute(get_conn(),
                "INSERT INTO versions(project_id,version_no,code,model_used,note,security_score) VALUES(?,?,?,?,?,?)",
                (p["id"], vno, final.get("code", base_code), final.get("model"), f"精修：{req.message[:30]}", sec))
            conn = get_conn()
            conn.execute("UPDATE projects SET current_version=?,updated_at=datetime('now') WHERE id=?", (vid, p["id"]))
            conn.commit()
            conn.close()
            log_audit(user["id"], "refine_done", f"project:{p['id']}",
                      f"version:{vid} mock={final.get('mock')} sec={sec}", source_ip=ip, session_id=sid)
            yield _sse({"type": "done", "project_id": p["id"], "version_id": vid,
                        "mock": final.get("mock"), "security": sec})
        finally:
            rl.release(user["id"])

    return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)


@app.post("/api/race")
def race(req: RaceReq, user=Depends(require_user),
         request: Request = None, authorization: str | None = Header(default=None)):
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
    return {"versions": pv.list_versions()}


@app.get("/api/plan/versions/{name}")
def plan_snapshot(name: str):
    """读取指定版本快照的 markdown 原文。文件名白名单校验，防路径穿越。"""
    text = pv.read_snapshot(name)
    if text is None:
        raise HTTPException(status_code=404, detail="版本快照不存在")
    return PlainTextResponse(text, media_type="text/markdown; charset=utf-8")


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
