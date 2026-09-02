# -*- coding: utf-8 -*-
"""把指定用户提升为 admin（RBAC 最小权限：仅此一个提权入口，幂等）。

用法（在仓库根目录）：
  python scripts/make_admin.py <username>       # 提权 user -> admin
  python scripts/make_admin.py <username> --demote   # 降权 admin -> user

退出码：0 成功（含幂等已是目标角色）；1 用户不存在；2 参数错误。
"""
import os
import sys

# 让脚本能找到 server/ 下的模块
SERVER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server")
sys.path.insert(0, os.path.abspath(SERVER))

import database  # noqa: E402


def set_role(username: str, role: str):
    """返回 (ok, message)。"""
    conn = database.get_conn()
    row = conn.execute("SELECT id, role FROM users WHERE username=?", (username,)).fetchone()
    if not row:
        conn.close()
        return False, f"用户不存在: {username}"
    if row["role"] == role:
        conn.close()
        return True, f"幂等：{username} 已是 {role}"
    conn.execute("UPDATE users SET role=? WHERE id=?", (role, row["id"]))
    conn.commit()
    conn.close()
    return True, f"{username}: {row['role']} -> {role}"


def main(argv):
    if len(argv) < 2 or len(argv) > 3:
        print("用法: python scripts/make_admin.py <username> [--demote]")
        return 2
    username = argv[1]
    demote = "--demote" in argv[2:]
    if len(argv) == 3 and not demote:
        print("未知参数（仅支持 --demote）")
        return 2
    database.init_db()
    ok, msg = set_role(username, "user" if demote else "admin")
    print(("✅ " if ok else "❌ ") + msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
