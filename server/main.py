"""Atoms_Native — FastAPI backend.
Routes: auth, projects CRUD, SSE generate/refine/race, preview, export.
"""
import json
import os
import time
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from config import HOST, PORT, RATE_LIMIT
from database import get_conn, execute, init_db, row_to_dict
from auth import create_user, authenticate, create_session, require_user
from models import UserCreate, ProjectCreate, GenerateReq, RefineReq, RaceReq, SelectVersionReq
from agent.llm import list_models, provider_available, LLM_PROVIDER
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

# ---- rate limit (per user, per hour) ----
_hits: dict[int, list[float]] = {}

def _rate_ok(user_id: int) -> bool:
    now = time.time()
    lst = [t for t in _hits.get(user_id, []) if now - t < 3600]
    if len(lst) >= RATE_LIMIT:
        _hits[user_id] = lst
        return False
    lst.append(now)
    _hits[user_id] = lst
    return True


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
def login(body: UserCreate):
    uid = authenticate(body.username, body.password)
    if not uid:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = create_session(uid)
    conn = get_conn()
    u = conn.execute("SELECT id,username,created_at FROM users WHERE id=?", (uid,)).fetchone()
    conn.close()
    return {"token": token, "user": dict(u)}


@app.get("/api/me")
def me(user=Depends(require_user)):
    return {"user": dict(user)}


@app.get("/api/models")
def models():
    return {
        "available": list_models(),
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
def create_project(body: ProjectCreate, user=Depends(require_user)):
    title = (body.title or body.idea[:30]).strip()
    pid = execute(
        get_conn(),
        "INSERT INTO projects(user_id,title,idea,status) VALUES(?,?,?,?)",
        (user["id"], title, body.idea, "draft"),
    )
    conn = get_conn()
    p = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    conn.close()
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
        "SELECT id,version_no,model_used,race_winner,note,created_at FROM versions WHERE project_id=? ORDER BY version_no",
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
    }


@app.delete("/api/projects/{pid}")
def delete_project(pid: int, user=Depends(require_user)):
    p = _get_project(pid, user)
    if not p:
        raise HTTPException(status_code=404, detail="项目不存在")
    conn = get_conn()
    conn.execute("DELETE FROM versions WHERE project_id=?", (pid,))
    conn.execute("DELETE FROM messages WHERE project_id=?", (pid,))
    conn.execute("DELETE FROM projects WHERE id=?", (pid,))
    conn.commit()
    conn.close()
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
def generate(req: GenerateReq, user=Depends(require_user)):
    p = _get_project(req.project_id, user)
    if not p:
        raise HTTPException(status_code=404, detail="项目不存在")
    if not _rate_ok(user["id"]):
        raise HTTPException(status_code=429, detail="生成过于频繁，请稍后再试（每小时上限 %d 次）" % RATE_LIMIT)

    def stream():
        final = {}
        gen = pipeline.run_pipeline(p["idea"], base_spec=p["spec_json"], base_arch=p["arch_json"])
        while True:
            try:
                ev = next(gen)
            except StopIteration as e:
                final = e.value or {}
                break
            yield _sse(ev)
        # persist
        vno = _next_version(p["id"])
        vid = execute(get_conn(),
            "INSERT INTO versions(project_id,version_no,code,model_used,note) VALUES(?,?,?,?,?)",
            (p["id"], vno, final.get("code", ""), final.get("model"), ("离线模板" if final.get("mock") else "初版生成")))
        conn = get_conn()
        conn.execute("UPDATE projects SET spec_json=?,arch_json=?,status='ready',current_version=?,updated_at=datetime('now') WHERE id=?",
                     (final.get("spec", ""), final.get("arch", ""), vid, p["id"]))
        conn.commit()
        conn.close()
        yield _sse({"type": "done", "project_id": p["id"], "version_id": vid, "mock": final.get("mock")})

    return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)


@app.post("/api/refine")
def refine(req: RefineReq, user=Depends(require_user)):
    p = _get_project(req.project_id, user)
    if not p:
        raise HTTPException(status_code=404, detail="项目不存在")
    if not p["current_version"]:
        raise HTTPException(status_code=400, detail="请先生成初始版本")
    if not _rate_ok(user["id"]):
        raise HTTPException(status_code=429, detail="操作过于频繁，请稍后再试")
    conn = get_conn()
    v = conn.execute("SELECT code FROM versions WHERE id=?", (p["current_version"],)).fetchone()
    conn.close()
    base_code = v["code"] if v else ""

    def stream():
        final = {}
        gen = pipeline.run_pipeline(p["idea"], refine_code=base_code, refine_msg=req.message,
                                   base_spec=p["spec_json"], base_arch=p["arch_json"])
        while True:
            try:
                ev = next(gen)
            except StopIteration as e:
                final = e.value or {}
                break
            yield _sse(ev)
        _save_message(p["id"], "user", req.message)
        vno = _next_version(p["id"])
        vid = execute(get_conn(),
            "INSERT INTO versions(project_id,version_no,code,model_used,note) VALUES(?,?,?,?,?)",
            (p["id"], vno, final.get("code", base_code), final.get("model"), f"精修：{req.message[:30]}"))
        conn = get_conn()
        conn.execute("UPDATE projects SET current_version=?,updated_at=datetime('now') WHERE id=?", (vid, p["id"]))
        conn.commit()
        conn.close()
        yield _sse({"type": "done", "project_id": p["id"], "version_id": vid, "mock": final.get("mock")})

    return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)


@app.post("/api/race")
def race(req: RaceReq, user=Depends(require_user)):
    p = _get_project(req.project_id, user)
    if not p:
        raise HTTPException(status_code=404, detail="项目不存在")
    if not _rate_ok(user["id"]):
        raise HTTPException(status_code=429, detail="操作过于频繁，请稍后再试")

    def stream():
        final = {}
        gen = race_mod.run_race(p["idea"], req.models)
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
        yield _sse({"type": "done", "project_id": p["id"], "version_map": mapping, "winner": final.get("candidates", [{}])[0].get("model") if final.get("candidates") else None})

    return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)


@app.post("/api/projects/{pid}/select-version")
def select_version(pid: int, body: SelectVersionReq, user=Depends(require_user)):
    p = _get_project(pid, user)
    if not p:
        raise HTTPException(status_code=404, detail="项目不存在")
    conn = get_conn()
    v = conn.execute("SELECT id FROM versions WHERE id=? AND project_id=?", (body.version_id, pid)).fetchone()
    if not v:
        conn.close()
        raise HTTPException(status_code=404, detail="版本不存在")
    conn.execute("UPDATE projects SET current_version=?,updated_at=datetime('now') WHERE id=?", (body.version_id, pid))
    conn.commit()
    conn.close()
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


# ---------------- Static frontend ----------------
PUBLIC_DIR = str(Path(__file__).resolve().parent.parent / "public")
app.mount("/", StaticFiles(directory=PUBLIC_DIR, html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=HOST, port=PORT, reload=False)
