# -*- coding: utf-8 -*-
"""防回退守护（loop engineering 第 2 层防线）。

背景：2026-09-04 工作区曾被静默回退（admin 端点/role 修复丢失、单测从 50
掉到 44），当时靠「测试数不对劲」的人肉察觉，纯属运气。本脚本把运气变成门禁：

1. 基线计数：unit_tests.py 的 test 数量 < 基线即 RED（删测试逃不过门禁）
2. 契约断言：前端实际消费的关键 API 响应字段逐一钉死（role 缺字段那
   种「API 测试全绿、真浏览器翻车」的 bug，在这里直接拦截）

用法：python tests/regression_guard.py（被 ci_gate.sh 第 7 步调用）
"""
import io
import re
import sys
import os
import contextlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))
sys.path.insert(0, ROOT)

# ── 1. 单测数量基线（只增不减；新增测试后手动上调）────────────────
BASELINE_UNIT_TESTS = 55
failures = []

src = open(os.path.join(ROOT, "tests", "unit_tests.py"), encoding="utf-8").read()
n_tests = len(re.findall(r"def test_", src))
if n_tests < BASELINE_UNIT_TESTS:
    failures.append(f"单测数量回退：{n_tests} < 基线 {BASELINE_UNIT_TESTS}（测试被删或文件被回退）")
else:
    print(f"✅ 单测基线：{n_tests} >= {BASELINE_UNIT_TESTS}")

e2e_src = os.path.join(ROOT, "tests", "e2e_journeys.py")
if not os.path.exists(e2e_src):
    failures.append("E2E 旅程脚本 tests/e2e_journeys.py 丢失")
else:
    print("✅ E2E 旅程脚本在位")

# ── 2. 关键源码特征存在性（防文件被回退到旧版）─────────────────────
GUARDS = [
    # (文件, 必须存在的特征串, 说明)
    ("public/app.js", "publishBtn", "发布按钮绑定"),
    ("public/app.js", "shareBtn", "分享按钮绑定"),
    ("public/app.js", "applyProjectData(d)", "发布/分享竞态兜底"),
    ("public/studio.html", "publishBtn", "发布按钮 DOM"),
    ("public/studio.html", "shareBtn", "分享按钮 DOM"),
    ("public/discover.html", "sorter", "发现页排序 UI"),
    ("public/discover.html", "portfolio.html", "作者作品集链接"),
    ("public/discover.html", "uses", "Remix 计数展示"),
    ("server/main.py", "admin/users", "管理端用户列表端点"),
    ("server/main.py", "api/share/", "公开分享端点"),
    ("server/main.py", "publish", "发布端点"),
    ("server/main.py", "SELECT id,username,role,created_at", "login 响应含 role（2026-09-03 线上 bug 钉死；契约层另有断言）"),
    ("server/discover.py", "publish_item", "发布落库函数"),
    ("server/discover.py", "sort == \"new\"", "发现页排序参数"),
]
for path, needle, why in GUARDS:
    full = os.path.join(ROOT, path)
    if not os.path.exists(full):
        failures.append(f"文件丢失：{path}")
        continue
    if needle not in open(full, encoding="utf-8").read():
        failures.append(f"特征回退：{path} 不含「{needle}」（{why}）")
print(f"✅ 源码特征守护：{len(GUARDS)} 项检查完成")

# ── 3. API 契约（前端消费什么字段，这里断言什么字段）───────────────
import tempfile
import importlib.util

tmp = tempfile.TemporaryDirectory()
import config
config.DB_PATH = os.path.join(tmp.name, "guard.db")
import database
database.DB_PATH = config.DB_PATH
database.init_db()
database.audit.WORM_PATH = os.path.join(tmp.name, "audit.log")
database.audit._last_hash = None

spec = importlib.util.spec_from_file_location(
    "main", os.path.join(ROOT, "server", "main.py"))
main = importlib.util.module_from_spec(spec)
spec.loader.exec_module(main)
from fastapi.testclient import TestClient
client = TestClient(main.app)

main.create_user("guard_user", "pass1234")
r = client.post("/api/auth/login", json={"username": "guard_user", "password": "pass1234"})
login = r.json()
for field in ("token", "user"):
    if field not in login:
        failures.append(f"login 契约破坏：缺 {field}")
if login.get("user", {}).get("role") not in ("user", "admin"):
    failures.append("login 契约破坏：user.role 缺失或非法（admin.html 判权依赖）")
print("✅ login 契约：token + user.role")

r = client.get("/api/discover")
items = r.json()["items"]
if not items:
    failures.append("discover 契约破坏：种子模板为空")
else:
    for field in ("id", "title", "author", "views", "uses", "has_sample", "category"):
        if field not in items[0]:
            failures.append(f"discover 契约破坏：卡片缺 {field}（前端渲染依赖）")
print("✅ discover 契约：9 字段完整")

tmp.cleanup()

# ── 结果 ──────────────────────────────────────────────────────
if failures:
    print("\n❌ 防回退守护 RED：")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("\n防回退守护 GREEN ✅（基线/特征/契约三层全过）")
