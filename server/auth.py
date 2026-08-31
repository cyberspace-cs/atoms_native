"""Auth: pbkdf2 password hashing, session tokens, bearer auth dependency."""
import hashlib
import secrets

from fastapi import Header, HTTPException
from database import get_conn, execute


def hash_password(pw: str, salt: str | None = None):
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt), 120_000)
    return h.hex(), salt


def verify(pw: str, salt: str, expected: str) -> bool:
    return hash_password(pw, salt)[0] == expected


def create_user(username: str, password: str):
    conn = get_conn()
    exists = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
    if exists:
        conn.close()
        return None, "用户名已被占用"
    if len(password) < 4:
        conn.close()
        return None, "密码至少 4 位"
    h, salt = hash_password(password)
    uid = execute(
        conn,
        "INSERT INTO users(username,password_hash,salt) VALUES(?,?,?)",
        (username, h, salt),
    )
    conn.close()
    return uid, None


def authenticate(username: str, password: str):
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    conn.close()
    if not row:
        return None
    if not verify(password, row["salt"], row["password_hash"]):
        return None
    return row["id"]


def create_session(user_id: int):
    conn = get_conn()
    token = secrets.token_hex(32)
    execute(conn, "INSERT INTO sessions(token,user_id) VALUES(?,?)", (token, user_id))
    conn.close()
    return token


def get_user_by_token(token: str | None):
    if not token:
        return None
    token = token.replace("Bearer ", "").strip()
    conn = get_conn()
    row = conn.execute(
        "SELECT u.id,u.username,u.created_at FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?",
        (token,),
    ).fetchone()
    conn.close()
    return row


def require_user(authorization: str | None = Header(default=None)):
    user = get_user_by_token(authorization)
    if not user:
        raise HTTPException(status_code=401, detail="未登录或登录已过期")
    return user
