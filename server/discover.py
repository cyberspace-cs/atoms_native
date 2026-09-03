"""发现与模板（Discover）—— 对齐 atoms.dev 的「发现 / 模板」社区页。

产品视角：生成能力再强，新用户面对空白输入框也会「不知道能说啥」。
发现页用社区维护的精选模板降低第一秒的冷启动门槛：
看到别人造了什么 → 挑一个顺眼的 → 一键变成自己的项目 → 改一句话就是自己的。

设计原则（与 friction.py 一致的工程纪律）：
  - 惰性种子：表为空时自动灌入精选模板，部署零配置。
  - 读写分离：浏览量累加是 fire-and-forget，绝不阻塞页面主链路。
  - 永不抛错：发现页任何异常都不能影响生成主流程。
"""
from __future__ import annotations

import os

import database

# 本地真实示例：策展生成的完整可玩应用（回填到 sample_html，发现页可预览）
SAMPLE_APPS = {
    "贪吃蛇小游戏": os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample_apps", "snake.html"),
}

# 精选模板（官方 curated，社区扩展的口子留在 discover_items 表本身）
SEED_ITEMS = [
    ("团队饮水打卡工具", "帮团队记录每日饮水与目标打卡，自动出统计图表",
     "一个帮团队管理每日饮水与目标打卡的小工具，带统计图表", "工具", "taoxie", "💧"),
    ("贪吃蛇小游戏", "方向键控制，带得分与最高分记录",
     "一个贪吃蛇小游戏，键盘方向键控制，带得分和最高分记录", "游戏", "Atoms 团队", "🐍"),
    ("程序员个人主页", "深色极简风：头像、技能标签、项目卡片、联系方式",
     "一个程序员的个人主页，含头像区、技能标签、项目卡片和联系方式，深色极简风", "官网", "Atoms 团队", "🧑‍💻"),
    ("极简记账本", "记录收入支出，按月汇总并展示图表",
     "一个极简记账本，可添加收入支出记录，按月份统计汇总并展示图表", "生活", "Atoms 团队", "💰"),
    ("番茄钟专注计时", "25 分钟专注 + 5 分钟休息循环，带今日专注统计",
     "一个番茄钟计时器，25 分钟专注加 5 分钟休息循环，带今日专注统计", "工具", "Atoms 团队", "🍅"),
    ("K-Means 聚类可视化", "随机点集上逐步展示聚类过程的教学演示",
     "一个 K-Means 聚类算法的可视化演示页面，随机生成点集，逐步展示聚类过程", "可视化", "yangming", "📊"),
    ("活动倒计时页", "距离目标日期的天数时分秒，渐变背景可自定义标题",
     "一个活动倒计时页面，显示距离目标日期的天数时分秒，背景渐变，可自定义标题", "生活", "Atoms 团队", "⏳"),
    ("团队待办清单", "添加、勾选、删除任务，按完成状态分组",
     "一个团队待办清单工具，可添加、勾选、删除任务，按完成状态分组显示", "工具", "taoxie", "✅"),
]


def _backfill_samples(conn):
    """把本地示例 HTML 回填到缺少 sample_html 的模板。文件缺失则跳过，永不抛错。"""
    for title, path in SAMPLE_APPS.items():
        try:
            row = conn.execute(
                "SELECT id FROM discover_items WHERE title=? AND (sample_html IS NULL OR sample_html='')",
                (title,)).fetchone()
            if not row:
                continue
            with open(path, encoding="utf-8") as f:
                conn.execute("UPDATE discover_items SET sample_html=? WHERE id=?", (f.read(), row["id"]))
            conn.commit()
        except Exception:
            pass


def ensure_seed():
    """表为空时灌入精选模板，并回填真实示例。任何异常吞掉（种子缺失不能影响主服务）。"""
    try:
        conn = database.get_conn()
        n = conn.execute("SELECT COUNT(*) c FROM discover_items").fetchone()["c"]
        if n == 0:
            for title, desc, idea, cat, author, emoji in SEED_ITEMS:
                conn.execute(
                    "INSERT INTO discover_items(title,description,idea,category,author,emoji)"
                    " VALUES(?,?,?,?,?,?)", (title, desc, idea, cat, author, emoji))
            conn.commit()
        _backfill_samples(conn)
        conn.close()
    except Exception:
        pass


def list_items(q: str = "", sort: str = "views", author: str = ""):
    """发现页数据。q 模糊匹配标题/描述/想法，sort=views(最热)|new(最新)，author 按作者过滤（作品集页）。

    对齐 atoms.dev 的 Prompt search 能力：社区内容多起来之后，搜索是
    找到「能被一句话变成项目」的模板的最短路径。异常返回空列表。
    """
    try:
        where, params = [], []
        if q:
            like = f"%{q.strip()}%"
            where.append("(title LIKE ? OR description LIKE ? OR idea LIKE ?)")
            params += [like, like, like]
        if author:
            where.append("author=?")
            params.append(author)
        w = (" WHERE " + " AND ".join(where)) if where else ""
        order = "created_at DESC, id DESC" if sort == "new" else "views DESC, id ASC"
        conn = database.get_conn()
        rows = conn.execute(
            "SELECT id,title,description,idea,category,author,emoji,views,uses,"
            "(sample_html IS NOT NULL AND sample_html != '') AS has_sample "
            f"FROM discover_items{w} ORDER BY {order}", params).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def publish_item(user_id: int, username: str, project_id: int, category: str = "社区"):
    """把用户项目的当前版本发布到发现页（Remix 闭环的起点）。

    同一作者同名模板视为重新发布：更新 idea/示例代码而非重复占卡。
    发布后他人「使用此模板」即为 Remix，uses 计数随之增长。
    返回 discover_item_id；项目不存在/无版本/无权限返回 None。
    """
    try:
        conn = database.get_conn()
        p = conn.execute(
            "SELECT id,title,idea,user_id FROM projects WHERE id=?", (int(project_id),)).fetchone()
        if not p or p["user_id"] != user_id or not p["title"]:
            conn.close()
            return None
        v = conn.execute(
            "SELECT code FROM versions WHERE id=(SELECT current_version FROM projects WHERE id=?)",
            (int(project_id),)).fetchone()
        code = v["code"] if v and v["code"] else ""
        desc = (p["idea"] or p["title"])[:60]
        row = conn.execute(
            "SELECT id FROM discover_items WHERE title=? AND author=?", (p["title"], username)).fetchone()
        if row:
            conn.execute(
                "UPDATE discover_items SET description=?,idea=?,category=?,sample_html=? WHERE id=?",
                (desc, p["idea"], category, code, row["id"]))
            item_id = row["id"]
        else:
            cur = conn.execute(
                "INSERT INTO discover_items(title,description,idea,category,author,emoji,sample_html)"
                " VALUES(?,?,?,?,?,?,?)",
                (p["title"], desc, p["idea"], category, username, "🚀", code))
            item_id = cur.lastrowid
        conn.commit()
        conn.close()
        return item_id
    except Exception:
        return None


def get_sample(item_id):
    """返回模板的真实示例 HTML（策展生成）；无则 None。"""
    try:
        conn = database.get_conn()
        row = conn.execute("SELECT sample_html FROM discover_items WHERE id=?", (int(item_id),)).fetchone()
        conn.close()
        if row and row["sample_html"]:
            return row["sample_html"]
    except Exception:
        pass
    return None


def add_view(item_id) -> bool:
    """浏览量 +1（fire-and-forget）。目标不存在返回 False。"""
    try:
        conn = database.get_conn()
        cur = conn.execute("UPDATE discover_items SET views=views+1 WHERE id=?", (int(item_id),))
        conn.commit()
        conn.close()
        return cur.rowcount > 0
    except Exception:
        return False


def use_template(item_id, user_id):
    """把模板变成用户自己的项目：复制 idea 建项目，返回 project_id；失败返回 None。"""
    try:
        conn = database.get_conn()
        row = conn.execute("SELECT title,idea FROM discover_items WHERE id=?", (int(item_id),)).fetchone()
        if not row:
            conn.close()
            return None
        pid = database.execute(conn,
            "INSERT INTO projects(user_id,title,idea,status) VALUES(?,?,?,?)",
            (user_id, row["title"], row["idea"], "draft"))
        conn.execute("UPDATE discover_items SET views=views+1, uses=uses+1 WHERE id=?", (int(item_id),))
        conn.commit()
        conn.close()
        return pid
    except Exception:
        return None
