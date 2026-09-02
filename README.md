# ⚛ Atoms Native — AI Agent 驱动的应用生成平台

> 一个可运行的网页应用 Demo：用自然语言描述想法 → 多智能体团队（PM / 架构师 / 工程师 / 评审）协作 → 生成**一个可运行的单文件 Web 应用** → 实时预览 → 对话式迭代。对标 [Atoms](https://atoms.dev/) 的核心体验。

这是为 **AI Native 全栈工程师岗位笔试** 提交的 Atoms Demo。

> **在线演示**：`https://taoxie.vip/atoms-native/`（官网首页）· `https://taoxie.vip/atoms-native/studio.html`（工作台）
> **演示账号**：`demo` / `demo123456`（含预置项目，可直接打开体验，无需注册）
> 无 LLM Key 时自动进入「离线模板模式」，全链路仍可演示；配置 Key 后为真实大模型生成。

## 🌱 先说人话：这个项目是干什么的？

想象一下：你对电脑说「我想要一个帮团队记录每日饮水打卡的小工具」，一分多钟后，一个真的能点、能用、能存数据的应用就出现在你面前。

**Atoms Native 做的就是这件事。** 它在网页里养了一支「AI 开发小队」：

- 👩‍💼 **Emma（产品经理）**——把你随口说的想法，整理成一份正经的需求文档；
- 🧑‍🎨 **Bob（架构师）**——决定这个应用怎么搭、数据存在哪；
- 👨‍💻 **Alex（工程师）**——把代码一行行写出来，产出一个完整的网页应用；
- 🕵️ **Mike（评审员）**——像甲方一样挑毛病，不合格就打回让 Alex 重写。

你全程不用写一行代码，也全程不会「黑盒等待」——四个 AI 各自在干什么，屏幕上实时直播，像看一场施工直播。做出来的应用马上就能在预览窗口里点着玩；不满意就用一句话让它改（「主色调换成绿色」）；改坏了还能一键回退到上一个版本；满意了就导出成一个 HTML 文件，双击就能打开，发给谁都能用。

> **一句话总结：Atoms Native 让「把想法变成应用」这件事，简单得像发一条微信。**

### 它能做什么（功能清单 · 人话版）

| 你想干嘛             | 它怎么帮你                                                                    |
| -------------------- | ----------------------------------------------------------------------------- |
| 把一句话想法变成应用 | 描述需求 → AI 小队流水线干活 → 生成一个能直接用的网页应用                     |
| 看看 AI 都在干嘛     | 每个智能体的每一步工作实时显示，全程透明，拒绝黑盒                            |
| 改点细节             | 直接用嘴说：「把标题改大一点」，它就改                                        |
| 改坏了后悔           | 版本历史一键回滚，时光倒流                                                    |
| 选择困难症           | Race Mode：让几个大模型同时做同一道题，各自打分，你挑最好的那份               |
| 带走成果             | 一键导出 HTML 文件，双击就能用                                                |
| 担心安全             | 每次生成自动做「安全体检」打分；AI 生的代码关在沙箱里跑，碰不到你的账号和密钥 |

### 三步上手（不需要懂代码）

1. 打开 **[在线演示](https://taoxie.vip/atoms-native/studio.html)**，用演示账号登录：`demo` / `demo123456`
2. 在输入框里写下你想要的应用（或者直接打开预置好的项目）
3. 点「⚡ 生成应用」，等一分钟左右，然后随便玩

> 💡 想认真测试这个项目？下面准备了给评审的 10 项验收路径和自动化测试说明。
> 💡 本 README 其余部分偏技术（选型理由、架构、部署），写给工程师读者——但每一段都尽量说了人话。

## 📋 提交说明（评审请从这里开始）

### 提交物清单

| 项           | 内容                                                                                                                                                                                                                                                                                                                                                     |
| ------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **在线演示** | `https://taoxie.vip/atoms-native/`（官网首页）· [`/studio.html`](https://taoxie.vip/atoms-native/studio.html)（工作台）· [`/discover.html`](https://taoxie.vip/atoms-native/discover.html)（发现与模板）· [`/plan.html`](https://taoxie.vip/atoms-native/plan.html)（版本历史）· [`/admin.html`](https://taoxie.vip/atoms-native/admin.html)（研效看板） |
| **演示账号** | `demo` / `demo123456`（含预置项目，免注册直接体验）                                                                                                                                                                                                                                                                                                      |
| **源码仓库** | GitHub `cyberspace-cs/atoms_native` · Gitee `buleboy8065/atoms_native`（双源同 commit，国内访问 Gitee 更快）                                                                                                                                                                                                                                             |
| **文档**     | [笔试提交文档](docs/笔试提交文档.md)（官方模板四节，链接与说明齐）· 本 README（思路/取舍/完成度/扩展）· `docs/spec.md`（PRD）· `docs/design.md`（技术设计）· `SDD_ENTERPRISE_TODO.md`（企业化改造清单）                                                                                                                                                  |
| **生产环境** | 腾讯云 + nginx 子路径 + uvicorn（8088），真实 DeepSeek 生成已在线上端到端验证（`mock:false`）                                                                                                                                                                                                                                                            |

### 三条验证路径（由浅入深，任选）

1. **在线体验（1 分钟，零安装）**：打开在线演示 → 演示账号登录 → 输入想法 → `⚡ 生成应用` → 预览 → 对话精修。无需任何 Key。
2. **本地运行（3 分钟）**：
   ```bash
   cd server && pip install -r ../requirements.txt
   python -m uvicorn main:app --port 8000    # http://127.0.0.1:8000
   ```
   **无需 API Key**：无 key 自动走离线模板模式，注册→生成→预览→精修全链路可跑通；在 `server/.env` 填入 `DEEPSEEK_API_KEY` 后即为真实生成（一次完整生成约 60–90s，4 次顺序 LLM 调用；Race Mode 并行约 48s）。
3. **Docker（2 分钟）**：`cp server/.env.example .env && docker compose up -d --build` → `http://localhost:8088`。

### 自动化验证（不需要浏览器）

| 命令                                                     | 覆盖内容                                                                                      | 预期          |
| -------------------------------------------------------- | --------------------------------------------------------------------------------------------- | ------------- |
| `python tests/unit_tests.py`                             | 安全扫描（注入/XSS/泄露/越权）、审计 hash-chain、限流降级与恢复、可观测性、评估指标、摩擦信号 | 30 项全过     |
| `ATOMS_BASE=http://127.0.0.1:8099 python tests/smoke.py` | 注册→生成→持久化→安全分→metrics→反馈→回滚 全链路                                              | 12 项断言全过 |
| GitHub Actions                                           | push 自动跑 compileall + mock 启动 + smoke + 评估门禁（结构化输出有效性回归）                 | 全绿          |

### 人工验收路径（10 项逐条可核对）

| #   | 验收点       | 操作                                  | 预期结果                                                                              | 对应实现                                              |
| --- | ------------ | ------------------------------------- | ------------------------------------------------------------------------------------- | ----------------------------------------------------- |
| 1   | 账号鉴权     | `studio.html` 注册/登录               | 进入工作台；接口走 Bearer Token                                                       | [auth.py](server/auth.py)                             |
| 2   | 多智能体生成 | 输入想法 → `⚡ 生成应用`               | 活动流依次渲染 PM(Emma)→架构师(Bob)→工程师(Alex)→评审(Mike)；产物为可运行单文件 HTML  | [pipeline.py](server/agent/pipeline.py)               |
| 3   | 沙箱预览     | 在预览区实际操作生成的应用            | 可交互、可持久化（localStorage 垫片）；iframe 无 `allow-same-origin`，服务端永不 eval | [app.js](public/app.js)                               |
| 4   | 对话精修     | 输入「把主色调改成绿色」              | 基于上一版增量修改并生成新版本，版本历史完整                                          | `/api/refine`                                         |
| 5   | Race Mode    | 点「并行跑多个模型，评审打分选最优」  | 多模型线程并行、评分排序、自选最优                                                    | [race.py](server/agent/race.py)                       |
| 6   | 版本回滚     | 预览区 `🕘 版本` → 回退旧版            | 当前版本切换；回滚同时记入摩擦信号                                                    | [main.py](server/main.py) `rollback`                  |
| 7   | 导出         | 点 `导出`                             | 下载自包含 HTML，双击可独立运行                                                       | `exportBtn`                                           |
| 8   | 安全扫描     | 生成完成观察 security 事件            | 输出 OWASP LLM Top 10:2025 扫描 findings + 0–100 安全分                               | [security.py](server/security.py)                     |
| 9   | 研效看板     | 打开 `admin.html`                     | 智能体×模型调用分布、最近调用、限流后端自省、24h 摩擦信号总览                         | `/api/metrics`                                        |
| 10  | 测试与 CI    | 跑上面自动化验证，或看 GitHub Actions | 全绿；评估门禁防质量回归                                                              | [tests/](tests/) · [ci.yml](.github/workflows/ci.yml) |

> **诚实性说明**：无 Key 时走离线模板并明确标注 `mock`，绝不伪装成真实生成；真实 LLM 调用失败/输出不合法时给出明确原因（如 OpenRouter Provider 限制 404、429 限流），不静默降级——这是笔试题「质量与掌控性」的直接体现。

---

## ✨ 核心能力

| 能力                              | 说明                                                                                                                                                                                                                                |
| --------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 🧠 **多智能体流水线**              | PM(Emma) 出需求规格 → Architect(Bob) 出架构/数据模型 → Engineer(Alex) 生成单文件 HTML 应用 → Reviewer(Mike) 自审修复。前端实时可视化每个 Agent 的工作流。                                                                           |
| 📱 **实时预览**                    | 生成的单文件应用通过 sandbox iframe 即时渲染、可交互。                                                                                                                                                                              |
| 💬 **对话式迭代精修**              | "把主色调改成绿色" → 基于上一版代码增量修改，保留完整版本历史。                                                                                                                                                                     |
| 🏁 **Race Mode（多模型择优）**     | 同一需求并行跑多个 LLM（DeepSeek / Reasoner 等），评审打分排序，用户选最优。                                                                                                                                                        |
| 🗂️ **项目画廊**                    | 我的项目列表、重开、删除；每次生成/精修都是带模型标注的版本。                                                                                                                                                                       |
| 💾 **数据持久化**                  | 用户、项目、版本、对话、Agent 审计轨迹全部落 SQLite。                                                                                                                                                                               |
| 📦 **导出**                        | 一键导出生成的 HTML 文件。                                                                                                                                                                                                          |
| 🔥 **摩擦信号（Friction Signal）** | 识别「你和 AI 搏斗过」的会话：LLM 报错、输出不合法回退、评审打回、用户点踩/回滚都会被加权记录；摩擦分达阈值才建议把这次踩坑沉淀进评估集（`evals/cases.json`），形成「线上摩擦 → 评估集 → 回归测试」闭环。只观测、不阻断、永不抛错。 |
| 🛡️ **生产化工程**                  | Redis 分布式限流（故障自动恢复）+ SAST 安全扫描（OWASP LLM Top 10）+ SOC 2 审计日志（hash-chain）+ OpenTelemetry 风格可观测 + CI 评估门禁。                                                                                         |

## 🛡️ 安全边界（Green / Red Zone）

- 🟢 **Green（AI 全权）**：UI 生成、原型、应用代码生成、预览。
- 🔴 **Red（仅增强）**：鉴权 / 密钥管理。
- 生成代码**只在浏览器 sandbox iframe 中运行**（无 `allow-same-origin`），服务端**永不 `eval`/`exec`**，并从沙箱注入 `localStorage` 内存垫片——既让生成应用可持久化数据，又杜绝服务端代码执行与父页面 token 泄露。

---

## 🧩 技术选型与关键取舍

**栈**：`FastAPI` + `SQLite` + 原生 JS 单页前端（零构建）。部署 `Docker` / 或 uvicorn + nginx 子路径。

**为什么这样选（取舍）**：
1. **单文件应用生成** 而非全栈脚手架：把"可运行程度"和"预览可靠性"拉满，避免构建链路爆炸；代价是生成应用是前端单体（契合 Atoms "看得到、能交互"的演示定位）。
2. **原生 JS 而非 React**：零构建、易部署、易在 iframe 沙箱运行；代价是状态管理需手写（对这个规模完全够用）。
3. **多智能体顺序 + 评审修复环**：还原 Atoms 的"团队感"并提升一次成码率；Race Mode 用线程并行多模型，把耗时从"求和"降到"取最大"。
4. **LLM 多厂商注册表**：DeepSeek 默认（key 现成），OpenAI 兼容厂商（通义千问等）可切；无 key 时自动降级"离线模板模式"，全流程仍可跑通（韧性）。
5. **AI CI/CD 评估门禁**：`scripts/ci_gate.sh` 一键门禁（编译→单测→门禁自测→mock 起服→smoke→评估门禁）；`tests/gate_test.py` 用负例注入证明"回归失败则红"；门禁分两档——CI 冒烟级（`--expect-mock`，纯 mock 验证 harness 健康）与真实质量门禁（structured≥0.98 + valid_rate + 安全分）；`deploy/canary.sh` 真实 canary 发布（备份→部署→健康检查→异常自动回滚，服务器双路径实测）。

---

## 🚀 本地运行

```bash
cd server
python -m venv venv && ./venv/Scripts/pip install -r ../requirements.txt
cp .env.example .env          # 填入 DEEPSEEK_API_KEY 即启用真实生成
./venv/Scripts/python -m uvicorn main:app --host 0.0.0.0 --port 8000
# 浏览器打开 http://127.0.0.1:8000
```

## 🐳 Docker 运行

```bash
cp server/.env.example .env   # 填入 LLM key
docker compose up -d --build  # http://localhost:8088
```

## ☁️ 生产部署（nginx 子路径）

参考 `nginx-atoms-native.conf` 加入 443 块（SSE 需关闭 `proxy_buffering`），后端监听 `8088`。

---

## ✅ 完成度（做了 / 没做）

**已做（可运行、可体验）**
- [x] 账号体系（注册/登录/Bearer 鉴权）
- [x] 多智能体生成流水线 + 实时活动流
- [x] 单文件应用生成 + sandbox iframe 预览 + 导出
- [x] SQLite 全量持久化（用户/项目/版本/对话/Agent 审计）
- [x] 对话式迭代精修（版本管理）
- [x] Race Mode 多模型并行择优
- [x] 项目画廊（列表/重开/删除）
- [x] Docker + nginx 子路径部署方案
- [x] 无 key 离线模板降级（诚实标注 mock，杜绝「假成功」）
- [x] 生产化：分布式限流、SAST 安全扫描、SOC 2 审计、可观测看板（admin.html）
- [x] 摩擦信号 + 评估集 + CI 评估门禁
- [x] 单元测试 35 项 + 端到端 smoke 12 项

**未做（时间/范围控制，见扩展）**
- [ ] 真实 Stripe 支付 / 多租户企业权限
- [ ] 可视化拖拽编辑器（用代码生成替代）
- [ ] 通义千问/混元的专属适配网关（当前走 OpenAI 兼容预留位）

---

## 🔭 继续投入的扩展计划（含优先级）

| 优先级 | 扩展                                                                                        | 理由                                       |
| ------ | ------------------------------------------------------------------------------------------- | ------------------------------------------ |
| P0     | **生成应用数据真正落库**（预览 iframe 经 postMessage 把 localStorage 同步回平台，刷新不丢） | 当前沙箱持久化为会话级，平台级持久化可补强 |
| P1     | **可视化编辑器 + 多页路由**：单文件应用支持多"页面"与组件拖拽                               | 更接近 Atoms 的完整度                      |
| P1     | **GitHub 同步导出**：一键推到用户仓库（Atoms 的 Export to GitHub）                          | 契合"拥有你构建的东西"                     |
| P2     | **更多 Agent 角色**：Deep Researcher / SEO / Ads（对标 Atoms 团队）                         | 体验完整度                                 |
| P2     | **混元/千问原生接入 + 模型市场**                                                            | 多模型生态                                 |

---

## 📐 评估维度映射

- **完成度**：注册→生成→预览→精修全链路可跑；SQLite 持久化；结构化代码 + 错误处理 + CORS。
- **工程思维**：SDD（spec→design→tasks）驱动；技术选型有文档化取舍；复杂度受控。
- **用户体验**：登录→Studio→预览流程清晰；Agent 活动流让"黑盒"透明；预览即点即用。
- **创新性**：① 把 Atoms 多 Agent 协作**可视化**；② Race Mode 多模型择优；③ green/red-zone 安全边界 + 沙箱隔离；④ **摩擦信号**——借 TeamAI「和 AI 搏斗过才值得记录」的洞察，把线上失败自动转化为评估集用例，质量改进形成飞轮。
- **可交付性**：README 清晰（含评审快速上手）+ Docker/部署方案 + 公网可测链接 + 双远程源码 + CI 自动验证。

---

## 📁 目录结构

```
Atoms_Native/
├── docs/            # SDD: spec.md (PRD) + design.md (技术设计)
├── server/          # FastAPI 后端
│   ├── main.py      # 路由 + SSE + metrics + rollback/feedback + 摩擦信号接口
│   ├── auth.py      # 鉴权
│   ├── database.py  # SQLite（含 friction_events 表）
│   ├── models.py    # Pydantic
│   ├── config.py    # .env 加载
│   ├── security.py  # SAST 安全扫描（OWASP LLM Top 10:2025）
│   ├── audit.py     # SOC 2 审计日志（hash-chain 防篡改）
│   ├── ratelimit.py # Redis 分布式限流 + 并发守卫（fail-open）
│   ├── observability.py # 指标分位 / PII 脱敏 / prompt hash
│   ├── friction.py  # 摩擦信号（识别值得沉淀经验的会话）
│   ├── evals/       # 评估集 cases.json + 指标 metrics.py + 跑批 runner.py
│   ├── agent/
│   │   ├── llm.py        # 多厂商 LLM 注册表
│   │   ├── pipeline.py   # PM→Arch→Eng→Reviewer + 摩擦埋点
│   │   └── race.py       # 多模型并行择优
│   └── .env.example
├── public/          # 前端（index.html=官网首页 / studio.html=工作台 / admin.html 看板 / landing.css）
├── tests/           # unit_tests.py（39 项单测）+ smoke.py（12 项端到端）
├── .github/workflows/ci.yml  # CI: compileall + smoke + 评估门禁
├── Dockerfile / docker-compose.yml / nginx-atoms-native.conf
└── README.md
```
