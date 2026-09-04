# v1.6 核心闭环：设计、验证与维护

日期：2026-09-04。基线：1d3eedf。范围由用户确认；授权 SSH 推送 Gitee，不部署。

## 需求与实际边界

自然语言生成 → 四角色活动流 → 评审/有限修复 → 已保存产物预览 → 精修 → 可回滚版本链。
保留原生前端、FastAPI、SQLite、分享、Remix 和沙箱持久化。Race 保留管理员内部入口，不扩充用户功能。

## 状态契约

| status | 含义 | 版本保存 | SSE 终态 |
| --- | --- | --- | --- |
| success | 新代码完整且评审通过 | 事务保存、切指针 | done |
| degraded | 明确离线模板；不声称真实评审通过 | 保存 mock=1 | done |
| failed | 调用/格式/评审/复审/预算/提交失败 | 不保存、不切指针 | error |
| unchanged | 离线无法精修，或模型未改代码 | 不保存、不切指针 | done，明确原因 |

mock 是产物来源，不是成功与否。历史版本的新增字段保持 NULL，不伪造历史事实。
新版本持久化 status、mock、parent_version、call_count，项目详情接口一并返回。

## 架构

- agent/llm.py：ATOMS_OFFLINE 硬开关，在可用性检查、模型选项和最终 HTTP 边界同时生效。
- agent/pipeline.py：阶段事件、有限调用、结果契约；供应商错误立即停止；格式错误最多修正一次且保留原任务、代码和修改指令。
- generation_service.py：BEGIN IMMEDIATE 事务内检查父版本，分配版本号、写产物/对话、切换指针；任一步失败回滚。
- main.py：共享 SSE 适配器，只在保存成功后发送 app_code；异常发 error，finally 释放任务锁。
- public/app.js：支持分块 UTF-8、LF/CRLF；不吞解析/处理异常，流无终态视为中断。

审查失败不能默认 approve。Reviewer 必须给出对象、有效 verdict、0–100 数值 score、字符串 issues 列表；fix 必须带指引。只修复一次，然后重新评审；仍失败则停止。

## 费用控制

ATOMS_MAX_LLM_CALLS 默认 8，范围 1–12，计数在每次调用前检查，覆盖 Planner、格式修正和复审。
普通成功生成 4 次；有缓存规格的成功精修 2 次。HTTP 失败不自动重试。
这是调用次数限制，不是精确货币或 Token 额度；现有 Token 指标仍是估计。

离线门禁默认真实调用预算为 0，不使用线上服务或真实密钥：

    python -m pip install -r requirements-dev.txt
    python -m playwright install chromium
    python scripts/ci_gate.py

需要 Node.js 22+。Linux 兼容入口为 bash scripts/ci_gate.sh；Actions 调用同一个 Python 门禁。
门禁清空 key、强制 offline、禁用 Redis，使用临时 DB/audit/report 和自有 uvicorn 子进程；不终止既有服务。缺少浏览器依赖直接 RED。

## 验证方式与局限

- 模型错误/非法输出/修复/复审/预算/事务/冲突由可控 chat 测试替身驱动真实流水线、API 和 SQLite；无真实模型费用。
- 原有单测、门禁负例、前端接线、防回退、HTTP smoke、真实 Chromium E2E、UI loop 全部进入统一门禁。
- mock 评估覆盖 47 个 case、每个 2 次，只检验 harness 健康。mock 结果不能算真实质量通过；真实精修失败后保留的旧代码也不再计作成功。
- 本轮没有真实模型生成质量/性能证据，没有部署生产环境，也未执行远端 Actions。
- 保留既有鉴权、限流及管理员 Race 实现；本轮不是安全合规认证或任意 HTML 功能正确性的证明。
