#!/usr/bin/env python3
"""CI smoke test for Atoms_Native (stdlib only, no third-party deps).

Exercises the productionization surface end-to-end against a running server:
  - auth (register) + projects CRUD
  - SSE generate -> `security` event + `done.security` present
  - version persistence -> /api/metrics reflects security score
  - /api/feedback + /api/projects/{pid}/rollback

Works in BOTH real mode and mock mode (no API key). Set ATOMS_BASE to point at
the server (default http://127.0.0.1:8099). If DEEPSEEK_API_KEY is present in
the environment when the server starts, agent_runs observability is also
exercised by the local e2e test; this CI job focuses on endpoint wiring.
"""
import json
import os
import random
import string
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("ATOMS_BASE", "http://127.0.0.1:8099").rstrip("/")
FAILS = []


def call(method, path, body=None, token=None):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
    )
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read().decode())


def stream_generate(token, pid, model=None):
    req = urllib.request.Request(
        BASE + "/api/generate",
        data=json.dumps({"project_id": pid, "model": model}).encode(),
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=600) as r:
        raw = r.read().decode()
    sec = done = None
    for chunk in raw.split("\n\n"):
        for line in chunk.split("\n"):
            if line.startswith("data: "):
                try:
                    ev = json.loads(line[6:])
                except Exception:
                    continue
                if ev.get("type") == "security":
                    sec = ev
                elif ev.get("type") == "done":
                    done = ev
    return sec, done


def stream_refine(token, pid, message, model=None):
    req = urllib.request.Request(
        BASE + "/api/refine",
        data=json.dumps({"project_id": pid, "message": message, "model": model}).encode(),
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=600) as r:
        raw = r.read().decode()
    done = None
    for chunk in raw.split("\n\n"):
        for line in chunk.split("\n"):
            if line.startswith("data: "):
                try:
                    ev = json.loads(line[6:])
                except Exception:
                    continue
                if ev.get("type") == "done":
                    done = ev
    return done


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" :: {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def main():
    uname = "ci_" + "".join(random.choices(string.ascii_lowercase, k=8))
    pw = "pw_" + "".join(random.choices(string.digits, k=6))
    u = call("POST", "/api/auth/register", {"username": uname, "password": pw})
    token = u.get("token")
    check("register returns token", bool(token))

    p = call("POST", "/api/projects", {"idea": "一个团队每日饮水打卡小工具", "title": "打卡ci"}, token)
    pid = (p.get("project") or {}).get("id")
    check("create project", pid is not None)

    sec, done = stream_generate(token, pid, model="deepseek")
    check("security SSE event present", sec is not None and isinstance(sec.get("score"), int),
          str(sec.get("score") if sec else None))
    check("done.security present", done is not None and isinstance(done.get("security"), int),
          str(done.get("security") if done else None))
    check("done has version_id", done is not None and done.get("version_id") is not None)

    pr = call("GET", "/api/projects/" + str(pid), token=token)
    check("current_code persisted", len(pr.get("current_code") or "") > 200)
    check("current_version set", pr["project"]["current_version"] is not None)
    check("version has security_score", any(v.get("security_score") is not None for v in pr["versions"]))

    m = call("GET", "/api/metrics", token=token)
    check("metrics 200 + versions avg_sec", m.get("versions", {}).get("avg_sec") is not None,
          str(m.get("versions")))
    check("metrics totals structure", "c" in m.get("totals", {}))

    fb = call("POST", "/api/feedback",
              {"project_id": pid, "version_id": pr["project"]["current_version"], "rating": 1}, token)
    check("feedback accepted", fb.get("ok") is True)

    # ── 对话式精修（应用重构修改）：SSE 流 → 新版本 + 对话记录 ──
    rf_msg = "把主色调改成深蓝，并在页脚加一个清空按钮"
    rf_done = stream_refine(token, pid, rf_msg)
    check("refine done event + version_id", rf_done is not None and rf_done.get("version_id") is not None)
    pr2 = call("GET", "/api/projects/" + str(pid), token=token)
    check("refine 落新版本（current_version 前进）",
          pr2["project"]["current_version"] not in (None, pr["project"]["current_version"]))
    notes = [v.get("note") or "" for v in pr2["versions"]]
    check("refine 版本 note 记录修改指令", any("精修" in n for n in notes),
          str(notes[-2:]))
    check("refine 对话留痕（messages 含用户指令）",
          any(m.get("role") == "user" and rf_msg[:10] in (m.get("content") or "")
              for m in pr2["messages"]))
    check("refine 产出合法 HTML（current_code）",
          "<html" in (pr2.get("current_code") or "").lower())

    # create a 2nd version then rollback to the previous one
    stream_generate(token, pid, model="deepseek")
    cur2 = call("GET", "/api/projects/" + str(pid), token=token)["project"]["current_version"]
    rb = call("POST", "/api/projects/" + str(pid) + "/rollback", {}, token=token)
    check("rollback ok", rb.get("ok") is True)
    cur_rb = call("GET", "/api/projects/" + str(pid), token=token)["project"]["current_version"]
    check("rollback changed current version", cur_rb != cur2 and cur_rb is not None,
          f"before={cur2} after={cur_rb}")

    print("\n" + ("ALL CHECKS PASSED" if not FAILS else f"FAILED: {FAILS}"))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
