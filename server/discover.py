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

import database

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


def ensure_seed():
    """表为空时灌入精选模板。任何异常吞掉（种子缺失不能影响主服务）。"""
    try:
        conn = database.get_conn()
        n = conn.execute("SELECT COUNT(*) c FROM discover_items").fetchone()["c"]
        if n == 0:
            for title, desc, idea, cat, author, emoji in SEED_ITEMS:
                conn.execute(
                    "INSERT INTO discover_items(title,description,idea,category,author,emoji)"
                    " VALUES(?,?,?,?,?,?)", (title, desc, idea, cat, author, emoji))
            conn.commit()
        conn.close()
    except Exception:
        pass


def list_items():
    """发现页数据：按浏览量倒序，含 has_sample 标记（不回传大字段）。异常返回空列表。"""
    try:
        conn = database.get_conn()
        rows = conn.execute(
            "SELECT id,title,description,idea,category,author,emoji,views,"
            "(sample_html IS NOT NULL AND sample_html != '') AS has_sample "
            "FROM discover_items ORDER BY views DESC, id ASC").fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


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
        conn.execute("UPDATE discover_items SET views=views+1 WHERE id=?", (int(item_id),))
        conn.commit()
        conn.close()
        return pid
    except Exception:
        return None
