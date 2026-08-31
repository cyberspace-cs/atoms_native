"""SQLite helpers (raw sqlite3) + schema init. Single-file app generation demo."""
import sqlite3
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
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
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
