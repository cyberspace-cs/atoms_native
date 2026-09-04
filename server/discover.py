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

# 本地真实示例：策展生成的完整可玩应用（回填到 sample_html，发现页可预览）。
# 2026-09-04 模板扩充：对标 NoCode/豆包类模板市场品类（小游戏/工具/页面），
# 著名单文件小游戏（2048/俄罗斯方块/扫雷/记忆翻牌/打砖块）全部可试玩。
_APPS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample_apps")
SAMPLE_APPS = {
    "贪吃蛇小游戏": os.path.join(_APPS_DIR, "snake.html"),
    "2048 数字滑块": os.path.join(_APPS_DIR, "game_2048.html"),
    "俄罗斯方块": os.path.join(_APPS_DIR, "game_tetris.html"),
    "扫雷": os.path.join(_APPS_DIR, "game_minesweeper.html"),
    "记忆翻牌": os.path.join(_APPS_DIR, "game_memory.html"),
    "打砖块": os.path.join(_APPS_DIR, "game_breakout.html"),
    "BMI 健康计算器": os.path.join(_APPS_DIR, "tool_bmi.html"),
    "密码生成器": os.path.join(_APPS_DIR, "tool_password.html"),
    "单位换算器": os.path.join(_APPS_DIR, "tool_converter.html"),
    "SaaS 产品落地页": os.path.join(_APPS_DIR, "page_landing.html"),
    "婚礼邀请函": os.path.join(_APPS_DIR, "page_wedding.html"),
}

# 精选模板（官方 curated，社区扩展的口子留在 discover_items 表本身）。
# 品类对标：小游戏（经典街机/益智）、工具（计算/生成/换算）、生活（打卡/记账/邀请函）、
# 官网（个人主页/产品落地页）、可视化（教学演示）——覆盖「新用户第一秒不知道说什么」的高频意图。
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
    # ── 2026-09-04 模板扩充（对标 NoCode 模板市场品类）──────────
    ("2048 数字滑块", "经典数字合并游戏，方向键/滑动操作，最高分本地保存",
     "一个 2048 数字合并小游戏，键盘方向键和触屏滑动操作，记录得分和最高分", "游戏", "Atoms 团队", "🔢"),
    ("俄罗斯方块", "经典 Tetris，旋转加速消行，带等级与触屏按键",
     "一个俄罗斯方块小游戏，方块旋转下落消行得分，等级越高速度越快", "游戏", "Atoms 团队", "🧱"),
    ("扫雷", "Windows 经典扫雷，三档难度，首击必安全，右键插旗",
     "一个扫雷小游戏，三档难度，第一次点击保证安全，支持插旗标记", "游戏", "Atoms 团队", "💣"),
    ("记忆翻牌", "8 对 emoji 配对，记步数与用时，锻炼记忆力",
     "一个记忆翻牌配对小游戏，8 对卡片翻开配对，记录步数和用时", "游戏", "Atoms 团队", "🎴"),
    ("打砖块", "街机经典 Breakout，3 关 3 命，挡板角度控制反弹",
     "一个打砖块小游戏，移动挡板弹球击碎砖块，三个关卡三条生命", "游戏", "Atoms 团队", "🧨"),
    ("BMI 健康计算器", "输入身高体重即时算 BMI，四档健康分级与建议",
     "一个 BMI 健康计算器，输入身高体重计算身体质量指数并给出健康分级建议", "工具", "Atoms 团队", "⚖️"),
    ("密码生成器", "长度/字符集自定义，密码学随机源，实时强度评估",
     "一个密码生成器，可自定义长度和字符类型，使用密码学随机数生成高强度密码", "工具", "Atoms 团队", "🔐"),
    ("单位换算器", "长度/重量/面积/数据/温度五类实时换算",
     "一个单位换算器，支持长度重量面积数据存储和温度的实时相互换算", "工具", "Atoms 团队", "📐"),
    ("SaaS 产品落地页", "Hero/数据/功能/定价四段式，转化导向深色设计",
     "一个 SaaS 产品落地页，含 Hero 区数据展示功能介绍和定价方案，深色渐变风格", "官网", "Atoms 团队", "🛰️"),
    ("婚礼邀请函", "渐变姓名 + 婚礼倒计时 + 场地信息 + 在线回执",
     "一个婚礼电子邀请函，显示新人姓名婚礼倒计时和场地信息，带在线出席回执表单", "生活", "Atoms 团队", "💌"),
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
    """精选模板按标题幂等补种（存量库也能长出新增模板，绝不重复），并回填真实示例。

    2026-09-04 之前是「表空才灌全量」，导致已部署环境永远拿不到新模板；
    改为逐条检查 title 是否存在，缺哪条补哪条。任何异常吞掉（种子缺失不能影响主服务）。
    """
    try:
        conn = database.get_conn()
        for title, desc, idea, cat, author, emoji in SEED_ITEMS:
            exists = conn.execute(
                "SELECT 1 FROM discover_items WHERE title=?", (title,)).fetchone()
            if not exists:
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
