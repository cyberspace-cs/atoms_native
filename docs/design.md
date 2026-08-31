# Atoms_Native — 技术设计文档（Design）

> SDD 阶段产物 #2。承接 `docs/spec.md`。

## 1. 系统架构

```
┌──────────────────────────────────────────────────────────┐
│  Browser (public/)  原生 JS 单页应用  ·  蓝白科技感 UI      │
│  ├─ Auth  (注册/登录)                                       │
│  ├─ Studio (想法输入 → 实时 Agent 活动流 → iframe 预览)     │
│  ├─ Race  (多模型并行 → 对比 → 选优)                        │
│  └─ Gallery (项目列表/重开/删除)                            │
└───────────────┬───────────────────────────────────────────┘
                │  REST + SSE(application/event-stream)
┌───────────────▼───────────────────────────────────────────┐
│  FastAPI (server/main.py)  :8000                            │
│  ├─ /api/auth/*        鉴权                                │
│  ├─ /api/projects/*    项目/版本 CRUD + 持久化             │
│  ├─ /api/generate      触发流水线 (SSE 流式返回 agent 步骤) │
│  ├─ /api/refine        对话式精修 (SSE)                    │
│  ├─ /api/race          Race Mode (SSE)                     │
│  └─ /api/projects/{id}/preview  返回当前版本 HTML          │
│  ┌────────────┐  ┌────────────┐  ┌─────────────────────┐  │
│  │ agent/llm  │  │agent/pipeline│  │ agent/race          │  │
│  │ 多厂商注册表│  │PM→Arch→Eng→│  │ 多模型并行+评审择优  │  │
│  │DeepSeek/兼容│  │ Reviewer    │  │                     │  │
│  └────────────┘  └────────────┘  └─────────────────────┘  │
│         │ HTTP(OpenAI-compatible)                          │
│  ┌──────▼─────────────────────────────────────────────┐   │
│  │ LLM Providers: DeepSeek / OpenAI-compatible(Qwen…)  │   │
│  └─────────────────────────────────────────────────────┘   │
│  SQLite (atoms_native.db)  users/sessions/projects/        │
│                              versions/messages/agent_runs  │
└──────────────────────────────────────────────────────────┘
        │  nginx 子路径 /atoms-native/  →  container:8000
┌───────▼────────┐
│ 43.143.231.106 │  taoxie.vip/atoms-native  (公网可测)
└────────────────┘
```

## 2. 数据模型（SQLite，raw sqlite3 + Pydantic）

```sql
users(id INTEGER PK, username TEXT UNIQUE, password_hash TEXT,
       salt TEXT, created_at TEXT)
sessions(token TEXT PK, user_id INTEGER, created_at TEXT)
projects(id INTEGER PK, user_id INTEGER, title TEXT, idea TEXT,
         spec_json TEXT, arch_json TEXT, status TEXT,
         current_version INTEGER, created_at TEXT, updated_at TEXT)
versions(id INTEGER PK, project_id INTEGER, version_no INTEGER,
         code TEXT, model_used TEXT, race_winner INTEGER,
         note TEXT, created_at TEXT)
messages(id INTEGER PK, project_id INTEGER, role TEXT,
         content TEXT, created_at TEXT)
agent_runs(id INTEGER PK, project_id INTEGER, version_id INTEGER,
           agent TEXT, model TEXT, input_json TEXT,
           output_json TEXT, created_at TEXT)
```

## 3. 多智能体流水线（核心能力）

顺序编排，每步通过 LLM 调用产出结构化结果；全过程用 SSE 向浏览器推送，前端渲染为"Agent 活动流"。

| Agent | 角色 | 输入 | 输出 |
|-------|------|------|------|
| **PM (Emma)** | 需求规格 | idea | 精简 spec：功能点、页面、数据字段、约束 |
| **Architect (Bob)** | 架构/数据模型 | spec | 组件清单 + 数据模型(字段) + 构建计划 |
| **Engineer (Alex)** | 代码生成 | spec+arch | **单文件 HTML 应用**（HTML+CSS+JS 自包含，无构建步骤） |
| **Reviewer (Mike)** | 自审修复 | 代码+spec | 评分/问题清单；严重问题则回灌 Engineer 修一次 |

**生成物约束**：单文件 `index.html`，自包含 CSS/JS，可 `srcdoc` 注入 sandbox iframe（`sandbox="allow-scripts"`）。不引入需要构建的依赖；如需图表用 CDN 允许的轻量内联或纯 CSS/SVG。

**Renderer**：浏览器 `<iframe sandbox="allow-scripts" srcdoc="...">` 渲染。绝不在服务端 eval 用户/AI 代码 → 落在 green zone。

## 4. LLM 多厂商注册表

`agent/llm.py`：零依赖 `.env` 加载（在 import 其他模块前执行）。注册表支持：
- `deepseek`：默认，`https://api.deepseek.com/v1/chat/completions`，model `deepseek-chat`（reasoner 可选）。
- `openai-compatible`：可配置 `base_url` + `api_key` + `model`，用于通义千问(DashScope)、Azure、自建等 → 满足"混元/千问可切"。
> 注：腾讯混元如需接入，走其 OpenAI-compatible 网关或 TC3 签名（v1 以兼容网关预留位，不阻塞）。

`LLMManager.chat(messages, model=..., stream=...)` 统一接口，返回完整或流式结果。

## 5. API 契约（摘要）

- `POST /api/auth/register` `{username,password}` → `{token,user}`
- `POST /api/auth/login` → `{token,user}`
- `GET  /api/projects` (auth) → 我的项目列表
- `POST /api/projects` `{title,idea}` → 创建项目
- `GET  /api/projects/{id}` → 项目详情(含版本列表)
- `POST /api/generate` `{project_id}` (SSE) → agent 步骤流 + 最终 code
- `POST /api/refine` `{project_id, message}` (SSE) → 基于上一版增量修改
- `POST /api/race` `{project_id, models:[...]}` (SSE) → 多候选 + 评审排序
- `POST /api/projects/{id}/select-version` `{version_id}` → 设当前版
- `GET  /api/projects/{id}/preview` → 当前版 HTML（供 iframe srcdoc 或直接 src）
- `POST /api/projects/{id}/export` → 返回代码文件下载

## 6. 部署架构

- **Dockerfile**：`python:3.11-slim` + `fastapi uvicorn[standard] httpx python-multipart`；暴露 8000；`ENV COACH_PREFIX=/atoms-native` 风格前缀适配子路径。
- **nginx**（服务器 43.143.231.106）：`location /atoms-native/ { proxy_pass http://127.0.0.1:PORT/; }` 且对 SSE 关闭 buffering；443 由 certbot 管理（已有 taoxie.vip 证书）。
- **密钥**：`.env`（gitignored）写入 `DEEPSEEK_API_KEY` 等；服务器部署时 scp `.env`，不入库。
- **持久化**：SQLite 文件挂载到卷，重启不丢。

## 7. 安全边界（green / red zone）
- 🟢 Green（可 AI 全权）：UI 生成、原型、应用代码生成、预览。
- 🔴 Red（仅增强、不自治）：鉴权/密钥管理、支付/外部敏感集成。
- 生成代码**只在浏览器 sandbox iframe 运行**，服务端永不 `eval`/`exec` → 杜绝远端代码执行风险。
- 公网 Demo 加基础限流 + 仅注册用户可生成，避免额度被刷。
