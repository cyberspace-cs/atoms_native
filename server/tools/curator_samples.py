"""策展脚本：为「发现页」模板生成真实示例（server/tools/curator_samples.py）。

流程（在服务器上跑，对着本机 uvicorn 的 HTTP API）：
  登录策展账号 → 遍历无示例的模板 → 建项目 → 调 /api/generate（SSE，真实大模型）
  → done 事件里拿 version_id → 直查 SQLite 把 versions.code 回填 discover_items.sample_html

诚实性约束（对齐产品原则「生成必须真实发生」）：
  只有 mock=False 的结果才允许回填为示例；mock 结果跳过并记录，绝不伪装。

用法（服务器）：
  cd /home/ubuntu/atoms-native/server && nohup ./venv/bin/python tools/curator_samples.py >> /tmp/curator.log 2>&1 &
"""
import json
import os
import sqlite3
import sys
import time
import urllib.request

BASE = os.environ.get("CURATOR_BASE", "http://127.0.0.1:8088")
CURATOR_USER = os.environ.get("CURATOR_USER", "curator")
CURATOR_PASS = os.environ.get("CURATOR_PASS", "curator#20260902")
DB_PATH = os.environ.get("CURATOR_DB", os.path.join(os.path.dirname(__file__), "..", "data", "app.db"))
GEN_TIMEOUT = int(os.environ.get("CURATOR_TIMEOUT", "240"))


def call(method: str, path: str, token: str | None = None, data: dict | None = None):
    req = urllib.request.Request(BASE + path, method=method)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, body, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def login():
    s, d = call("POST", "/api/auth/login", data={"username": CURATOR_USER, "password": CURATOR_PASS})
    if s == 200:
        return d.get("token") or d.get("access_token")
    s, d = call("POST", "/api/auth/register", data={"username": CURATOR_USER, "password": CURATOR_PASS})
    if s == 200:
        return d.get("token") or d.get("access_token")
    print("auth failed:", s, d)
    return None


def generate_wait(token: str, project_id: int):
    """调 SSE 生成接口并等 done 事件，返回 {version_id, mock} 或 None。"""
    req = urllib.request.Request(BASE + "/api/generate", method="POST",
                                 data=json.dumps({"project_id": project_id}).encode())
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Content-Type", "application/json")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=GEN_TIMEOUT) as r:
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                try:
                    ev = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                if ev.get("type") == "done":
                    return {"version_id": ev.get("version_id"), "mock": ev.get("mock")}
                if time.time() - t0 > GEN_TIMEOUT:
                    break
    except Exception as e:
        print("  generate error:", e)
    return None


def backfill(item_id: int, version_id: int):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE discover_items SET sample_html="
                 "(SELECT code FROM versions WHERE id=?) WHERE id=?", (version_id, item_id))
    conn.commit()
    conn.close()


def main():
    token = login()
    if not token:
        sys.exit(1)
    s, d = call("GET", "/api/discover", token=token)
    items = [i for i in d.get("items", []) if not i.get("has_sample")]
    print(f"{len(items)} templates need samples")
    for it in items:
        print(f"-> [{it['id']}] {it['title']}")
        s, p = call("POST", "/api/projects", token=token, data={"title": it["title"], "idea": it["idea"]})
        proj = p.get("project") if isinstance(p.get("project"), dict) else p
        pid = proj.get("project_id") or proj.get("id")
        if not pid:
            print("  create failed:", s, p)
            continue
        res = generate_wait(token, pid)
        if res and res.get("version_id") and not res.get("mock"):
            backfill(it["id"], res["version_id"])
            print("  done: version", res["version_id"])
        else:
            print("  SKIPPED (mock or no result):", res)
        time.sleep(2)
    print("curator finished")


if __name__ == "__main__":
    main()
