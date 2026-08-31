"""SQLite helpers (raw sqlite3) + schema init. Single-file app generation demo."""
import sqlite3

import audit  # SOC 2 不可变审计（独立于业务库，hash-chain WORM）
from config import DB_PATH


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS sessions(
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS projects(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT,
            idea TEXT,
            spec_json TEXT,
            arch_json TEXT,
            status TEXT DEFAULT 'draft',
            current_version INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS versions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            version_no INTEGER NOT NULL,
            code TEXT NOT NULL,
            model_used TEXT,
            race_winner INTEGER DEFAULT 0,
            note TEXT,
            security_score INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS messages(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS agent_runs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER,
            version_id INTEGER,
            agent TEXT NOT NULL,
            model TEXT,
            input_json TEXT,
            output_json TEXT,
            latency_ms INTEGER,
            tokens INTEGER,
            mock INTEGER DEFAULT 0,
            error TEXT,
            security_score INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS audit_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action TEXT NOT NULL,
            target TEXT,
            detail TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS feedback(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER,
            version_id INTEGER,
            user_id INTEGER,
            rating INTEGER NOT NULL,
            comment TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS audit_events(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_id INTEGER,
            action TEXT NOT NULL,
            resource_type TEXT,
            resource_id TEXT,
            outcome TEXT NOT NULL DEFAULT 'success',
            source_ip TEXT,
            session_id TEXT,
            detail TEXT,
            hash TEXT,
            prev_hash TEXT,
            ts_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    # 迁移：对已有库补齐新增列（ALTER 失败即说明已存在，忽略）
    for col, typ in (
        ("latency_ms", "INTEGER"),
        ("tokens", "INTEGER"),
        ("mock", "INTEGER DEFAULT 0"),
        ("error", "TEXT"),
        ("security_score", "INTEGER"),
    ):
        try:
            conn.execute(f"ALTER TABLE agent_runs ADD COLUMN {col} {typ}")
        except Exception:
            pass
    try:
        conn.execute("ALTER TABLE versions ADD COLUMN security_score INTEGER")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
    except Exception:
        pass
    for tbl, ddl in (
        ("audit_log", """CREATE TABLE IF NOT EXISTS audit_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, action TEXT NOT NULL,
            target TEXT, detail TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')))"""),
        ("feedback", """CREATE TABLE IF NOT EXISTS feedback(
            id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER, version_id INTEGER,
            user_id INTEGER, rating INTEGER NOT NULL, comment TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')))"""),
    ):
        try:
            conn.execute(ddl)
        except Exception:
            pass
    conn.commit()
    conn.close()


def row_to_dict(row):
    return dict(row) if row else None


def execute(conn, sql: str, params: tuple = ()):
    """Run a write statement on an existing connection, commit, return lastrowid."""
    cur = conn.execute(sql, params)
    conn.commit()
    return cur.lastrowid


def query(conn, sql: str, params: tuple = ()):
    """Run a read statement on an existing connection, return list of dicts."""
    cur = conn.execute(sql, params)
    return [dict(r) for r in cur.fetchall()]


# ---------- agent run / audit / feedback logging ----------

def log_agent_run(project_id, version_id, agent, model, input_json, output_json,
                  latency_ms, tokens, mock, error, security_score):
    """Persist one agent call for observability (研效看板 / 调用追踪). Returns row id."""
    conn = get_conn()
    fid = execute(conn, (
        "INSERT INTO agent_runs(project_id,version_id,agent,model,input_json,"
        "output_json,latency_ms,tokens,mock,error,security_score) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)"
    ), (project_id, version_id, agent, model,
        input_json, output_json, latency_ms, tokens, int(bool(mock)),
        error, security_score))
    conn.close()
    return fid


def log_audit(user_id, action, target=None, detail=None,
              source_ip=None, session_id=None, outcome="success"):
    """SOC 2 结构化审计：同时写入 (1) 不可变 WORM 文件(hash-chain)、(2) 结构化
    audit_events 表、(3) 兼容旧 audit_log 表。覆盖 5W（Who/What/When/Where/result）。

    outcome: success | failure | denied
    """
    # 1) WORM 文件（独立于业务库，hash-chain）
    event = audit.emit(
        actor_id=user_id, action=action, resource_type=None,
        resource_id=target, outcome=outcome, source_ip=source_ip,
        session_id=session_id, detail=detail,
    )
    # 2) 结构化表
    conn = get_conn()
    execute(conn, (
        "INSERT INTO audit_events(actor_id,action,resource_type,resource_id,"
        "outcome,source_ip,session_id,detail,hash,prev_hash) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)"
    ), (user_id, action, None, target, outcome, source_ip, session_id, detail,
        event.get("hash"), event.get("prev_hash")))
    # 3) 兼容旧表
    execute(conn, (
        "INSERT INTO audit_log(user_id,action,target,detail) VALUES(?,?,?,?)"
    ), (user_id, action, target, detail))
    conn.close()
    return event


def query_audit(limit: int = 100, actor_id=None, action=None):
    """结构化审计查询（audit_events 表）。"""
    conn = get_conn()
    sql = "SELECT id,actor_id,action,resource_type,resource_id,outcome,source_ip,session_id,ts_utc,detail FROM audit_events WHERE 1=1"
    params = []
    if actor_id is not None:
        sql += " AND actor_id=?"
        params.append(actor_id)
    if action:
        sql += " AND action=?"
        params.append(action)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def audit_alerts(limit: int = 500):
    """基于近期审计事件的高危告警（SOC 2 CC7）。"""
    events = query_audit(limit=limit)
    return audit.high_fidelity_alerts(events)


def verify_audit_chain():
    """校验 WORM 文件 hash-chain 完整性。返回 (ok, broken_line)。"""
    return audit.verify_chain()


def save_feedback(project_id, version_id, user_id, rating, comment=None):
    conn = get_conn()
    fid = execute(conn, (
        "INSERT INTO feedback(project_id,version_id,user_id,rating,comment) "
        "VALUES(?,?,?,?,?)"
    ), (project_id, version_id, user_id, rating, comment))
    conn.close()
    return fid
