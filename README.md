# ⚛ Atoms Native — AI Agent 驱动的应用生成平台

> 一个可运行的网页应用 Demo：用自然语言描述想法 → 多智能体团队（PM / 架构师 / 工程师 / 评审）协作 → 生成**一个可运行的单文件 Web 应用** → 实时预览 → 对话式迭代。对标 [Atoms](https://atoms.dev/) 的核心体验。

这是为 **AI Native 全栈工程师岗位笔试** 提交的 Atoms Demo。

---

## ✨ 核心能力

| 能力 | 说明 |
|------|------|
| 🧠 **多智能体流水线** | PM(Emma) 出需求规格 → Architect(Bob) 出架构/数据模型 → Engineer(Alex) 生成单文件 HTML 应用 → Reviewer(Mike) 自审修复。前端实时可视化每个 Agent 的工作流。 |
| 📱 **实时预览** | 生成的单文件应用通过 sandbox iframe 即时渲染、可交互。 |
| 💬 **对话式迭代精修** | "把主色调改成绿色" → 基于上一版代码增量修改，保留完整版本历史。 |
| 🏁 **Race Mode（多模型择优）** | 同一需求并行跑多个 LLM（DeepSeek / Reasoner 等），评审打分排序，用户选最优。 |
| 🗂️ **项目画廊** | 我的项目列表、重开、删除；每次生成/精修都是带模型标注的版本。 |
| 💾 **数据持久化** | 用户、项目、版本、对话、Agent 审计轨迹全部落 SQLite。 |
| 📦 **导出** | 一键导出生成的 HTML 文件。 |

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
- [x] 无 key 离线模板降级

**未做（时间/范围控制，见扩展）**
- [ ] 真实 Stripe 支付 / 多租户企业权限
- [ ] 可视化拖拽编辑器（用代码生成替代）
- [ ] 通义千问/混元的专属适配网关（当前走 OpenAI 兼容预留位）

---

## 🔭 继续投入的扩展计划（含优先级）

| 优先级 | 扩展 | 理由 |
|--------|------|------|
| P0 | **生成应用数据真正落库**（预览 iframe 经 postMessage 把 localStorage 同步回平台，刷新不丢） | 当前沙箱持久化为会话级，平台级持久化可补强 |
| P1 | **可视化编辑器 + 多页路由**：单文件应用支持多"页面"与组件拖拽 | 更接近 Atoms 的完整度 |
| P1 | **GitHub 同步导出**：一键推到用户仓库（Atoms 的 Export to GitHub） | 契合"拥有你构建的东西" |
| P2 | **更多 Agent 角色**：Deep Researcher / SEO / Ads（对标 Atoms 团队） | 体验完整度 |
| P2 | **混元/千问原生接入 + 模型市场** | 多模型生态 |

---

## 📐 评估维度映射

- **完成度**：注册→生成→预览→精修全链路可跑；SQLite 持久化；结构化代码 + 错误处理 + CORS。
- **工程思维**：SDD（spec→design→tasks）驱动；技术选型有文档化取舍；复杂度受控。
- **用户体验**：登录→Studio→预览流程清晰；Agent 活动流让"黑盒"透明；预览即点即用。
- **创新性**：① 把 Atoms 多 Agent 协作**可视化**；② Race Mode 多模型择优；③ green/red-zone 安全边界 + 沙箱隔离。
- **可交付性**：README 清晰 + Docker/部署方案 + 公网可测链接 + 双远程源码。

---

## 📁 目录结构

```
Atoms_Native/
├── docs/            # SDD: spec.md (PRD) + design.md (技术设计)
├── server/          # FastAPI 后端
│   ├── main.py      # 路由 + SSE
│   ├── auth.py      # 鉴权
│   ├── database.py  # SQLite
│   ├── models.py    # Pydantic
│   ├── config.py    # .env 加载
│   ├── agent/
│   │   ├── llm.py        # 多厂商 LLM 注册表
│   │   ├── pipeline.py   # PM→Arch→Eng→Reviewer
│   │   └── race.py       # 多模型并行择优
│   └── .env.example
├── public/          # 前端（index.html / app.js / styles.css）
├── Dockerfile / docker-compose.yml / nginx-atoms-native.conf
└── README.md
```
