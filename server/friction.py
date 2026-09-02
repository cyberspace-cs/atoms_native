"""摩擦信号（Friction Signal）—— 对齐 teamai-cli 第 3 层「经验驱动的团队知识库」。

核心洞察（引自 TeamAI 原文）：
    那些又长又顺（工具调用很多但没有摩擦）的 session 不会触发，
    可一旦你「和 AI 搏斗过」，系统就会识别出这次会话里藏着值得记录的问题。

同样的逻辑落到本产品的「一次生成/精修」上：
顺畅跑通的生成没有信息量；凡是出现 LLM 报错、输出不合法回退、评审打回、
用户回滚或点踩的生成，都是「摩擦」——它们指向真实缺陷，值得沉淀成经验，
进而补进 evals/cases.json 的 historical_failure 层，形成
「线上摩擦 → 评估集 → 回归测试」的闭环。

设计原则：
  - 只观测、不阻断：摩擦信号永远不改变生成结果。
  - 可解释：每个信号带 detail，聚合后能说清「为什么值得记录」。
  - 永不抛错：观测逻辑的任何异常都不能拖垮主流程。
"""
from __future__ import annotations

import database

# 单次事件对「这次会话是否值得记录」的贡献（语义：0-100 的相对权重）
FRICTION_WEIGHTS = {
    "llm_error": 30,          # 真实 LLM 调用失败（402 / 429 / 超时 / 内容拦截…）
    "fell_back": 25,          # 调用成功但输出不合法，回退离线模板
    "mock_mode": 40,          # 整轮走离线模板（根本没真生成）
    "format_retry": 8,        # 工程师首次输出不是合法 HTML，触发格式重试
    "review_fix": 12,         # 评审打回要求修复
    "fix_failed": 15,         # 修复也失败，保留修复前版本
    "negative_feedback": 25,  # 用户点了 👎
    "rollback": 15,           # 用户回滚到旧版本
    "repeated_refine": 6,     # 同一项目反复精修
}

FRICTION_LABELS = {
    "llm_error": "LLM 调用失败",
    "fell_back": "输出不合法，回退离线模板",
    "mock_mode": "整轮走离线模板（非真实生成）",
    "format_retry": "首次输出非合法 HTML，触发格式重试",
    "review_fix": "评审打回要求修复",
    "fix_failed": "修复失败，保留修复前版本",
    "negative_feedback": "用户点了 👎",
    "rollback": "用户回滚到旧版本",
    "repeated_refine": "反复精修同一项目",
}

# 摩擦分达到该阈值即认为「这次会话藏着值得记录的问题」
SUGGEST_THRESHOLD = 40


def label(kind: str) -> str:
    return FRICTION_LABELS.get(kind, kind)


def record(project_id, kind: str, detail: str | None = None,
           session_id: str | None = None, user_id=None):
    """记录一次摩擦事件。任何异常都被吞掉（观测不能影响主流程）。"""
    weight = FRICTION_WEIGHTS.get(kind, 5)
    try:
        conn = database.get_conn()
        cur = conn.execute(
            "INSERT INTO friction_events(project_id, session_id, user_id, kind, weight, detail)"
            " VALUES (?,?,?,?,?,?)",
            (project_id, session_id, user_id, kind, weight, (detail or "")[:1000]),
        )
        conn.commit()
        eid = cur.lastrowid
        conn.close()
        return eid
    except Exception:
        return None


def _where(project_id=None, session_id=None, window_hours: float | None = None):
    conds, args = [], []
    if project_id is not None:
        conds.append("project_id = ?")
        args.append(project_id)
    if session_id is not None:
        conds.append("session_id = ?")
        args.append(session_id)
    if window_hours is not None:
        conds.append("created_at >= datetime('now', ?)")
        args.append(f"-{int(window_hours)} hours")
    return (" WHERE " + " AND ".join(conds)) if conds else "", args


def score(project_id=None, session_id=None, window_hours: float | None = 24) -> dict:
    """聚合摩擦分。可按项目查（判断这个项目值不值得沉淀），或按单次会话查。"""
    empty = {"score": 0, "n_events": 0, "by_kind": {}, "reasons": [],
             "worth_documenting": False, "events": []}
    try:
        where, args = _where(project_id, session_id, window_hours)
        conn = database.get_conn()
        rows = conn.execute(
            "SELECT kind, COUNT(*) n, COALESCE(SUM(weight),0) w "
            f"FROM friction_events{where} GROUP BY kind ORDER BY w DESC", args
        ).fetchall()
        ev_rows = conn.execute(
            f"SELECT id, project_id, session_id, kind, weight, detail, created_at"
            f" FROM friction_events{where} ORDER BY id DESC LIMIT 50", args
        ).fetchall()
        conn.close()
    except Exception:
        return empty

    by_kind = {}
    total = 0
    for r in rows:
        d = dict(r)
        by_kind[d["kind"]] = {"n": d["n"], "weight": d["w"]}
        total += int(d["w"] or 0)

    reasons = [f"{label(k)} ×{v['n']}" for k, v in by_kind.items()]
    events = [dict(r) for r in ev_rows]
    n_events = sum(v["n"] for v in by_kind.values())

    return {
        "score": total,
        "n_events": n_events,
        "by_kind": by_kind,
        "reasons": reasons,
        "worth_documenting": total >= SUGGEST_THRESHOLD,
        "events": events,
    }


def suggest(project_id, window_hours: float | None = 24) -> dict | None:
    """判断某个项目是否值得沉淀经验；值得则返回建议文案，否则返回 None。

    对齐 TeamAI 的交互：只在「你和 AI 搏斗过」时才打扰用户。
    """
    s = score(project_id=project_id, window_hours=window_hours)
    if not s["worth_documenting"]:
        return None

    task = ""
    try:
        conn = database.get_conn()
        row = conn.execute(
            "SELECT title, idea FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        conn.close()
        if row:
            d = dict(row)
            task = d.get("title") or (d.get("idea") or "")[:60]
    except Exception:
        pass

    return {
        "project_id": project_id,
        "score": s["score"],
        "task": task,
        "headline": "本次生成可能藏着值得记录的问题",
        "reasons": s["reasons"],
        "action": ("建议把这次踩坑补进 server/evals/cases.json 的 historical_failure 层，"
                   "让它变成永久回归用例（线上摩擦 → 评估集 → 回归测试的闭环）。"),
    }


def high_friction_projects(min_score: int | None = None, limit: int = 20,
                           window_hours: float | None = 24) -> list:
    """列出摩擦分最高的项目，供看板/知识沉淀排优先级。"""
    min_score = SUGGEST_THRESHOLD if min_score is None else min_score
    try:
        where, args = _where(window_hours=window_hours)
        conn = database.get_conn()
        rows = conn.execute(
            "SELECT project_id, COALESCE(SUM(weight),0) score, COUNT(*) n_events"
            f" FROM friction_events{where} GROUP BY project_id"
            " HAVING score >= ? ORDER BY score DESC LIMIT ?",
            tuple(args) + (min_score, limit),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            proj = conn.execute(
                "SELECT title, idea FROM projects WHERE id = ?", (d["project_id"],)
            ).fetchone()
            d["task"] = ""
            if proj:
                pd = dict(proj)
                d["task"] = pd.get("title") or (pd.get("idea") or "")[:60]
            out.append(d)
        conn.close()
        return out
    except Exception:
        return []


def summary(window_hours: float | None = 24) -> dict:
    """供 /api/metrics 使用：全局摩擦分总览。"""
    try:
        where, args = _where(window_hours=window_hours)
        conn = database.get_conn()
        tot = conn.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(weight),0) w"
            f" FROM friction_events{where}", args
        ).fetchone()
        by_kind = conn.execute(
            "SELECT kind, COUNT(*) n, COALESCE(SUM(weight),0) w"
            f" FROM friction_events{where} GROUP BY kind ORDER BY w DESC", args
        ).fetchall()
        conn.close()
        t = dict(tot) if tot else {"n": 0, "w": 0}
        return {
            "window_hours": window_hours,
            "n_events": t.get("n", 0),
            "total_score": t.get("w", 0),
            "by_kind": {dict(r)["kind"]: {"n": dict(r)["n"], "weight": dict(r)["w"]}
                        for r in by_kind},
            "high_friction_projects": high_friction_projects(window_hours=window_hours),
        }
    except Exception:
        return {"window_hours": window_hours, "n_events": 0, "total_score": 0,
                "by_kind": {}, "high_friction_projects": []}
