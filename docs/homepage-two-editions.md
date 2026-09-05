# 双版本首页设计与验证

2026-09-05。用户确认默认简约版与详细版并存，可互相切换。

## 调研依据

- [Atoms 官网](https://atoms.dev/)：已查看网页与截图，借鉴居中标题、输入主入口、浅色留白和分层介绍；保留自己的品牌文案与素材。
- [NoCode 官网](https://nocode.cn/)：搜索索引包含自然语言创建入口、案例分类；本次直接截图为空白，因此不推断未看到的视觉细节。
- [NoCode 官方指南](https://nocode.cn/docs/guide/quickstart.html)：以场景、框架和明确需求辅助用户开始，历史作品可继续编辑。
- [美团官方发布](https://www.meituan.com/news/NN250611100002065)：面向零基础用户的对话式构建定位。

## 实施范围

1. `/` 与 `index.html` 默认简约版：标题、真实想法表单、四个快捷场景、三步介绍。
2. `overview.html` 为详细版：完整团队、流程、能力、常见问题，导航回到简约版。
3. 两版共用现有 Studio；表单用原生 GET 带入 idea，支持无 JavaScript 环境。打开页面或提交想法只进行导航，不自动生成。
4. 浅色底与蓝紫强调色统一视觉。详细版的终端明确标为流程示意，更新精修失败和离线行为文案。
5. 不增加图片生成、第三方字体或模型请求。模板发现继续使用现有独立发现页。

## Loop engineering

浏览器验证两版切换、空输入校验、中文和特殊字符带入 Studio、快捷场景、无 JavaScript 提交、不同屏宽无溢出。接入统一离线门禁；浏览器截图人工复查排版。离线评测仅证明测试流程健康，不代表真实模型产物质量。

## 本轮结果

`python scripts/ci_gate.py` 完整退出 0：56 个原有单测、22 个核心契约测试、39 项前端接线、5 个 SSE 测试、27 项 E2E、23 项 UI 守护，以及新增双版本浏览器旅程通过。离线评测 47 个用例各运行 2 次，真实 LLM 调用为 0。复查了简约版桌面/手机与详细版截图。未部署。

## 测试补全（2026-09-05 第二轮，带来源证据）

用户指出"测试一般不够全"。调研同类实践后按零依赖原则补全（不引入 axe 等新依赖）：

1. **SEO/meta 契约**：两版 title、meta description（≥20 字符且互不相同）、viewport、lang 逐一断言。依据 Lighthouse SEO 审计的二元检查项（document-title / meta-description / viewport / http-status-code）。
   来源：https://unlighthouse.dev/learn-lighthouse/seo 、https://www.perfmasters.com/learn/seo
2. **a11y 结构性断言**：每页恰好一个 h1、无空文本链接、跳转链接（skip link）Tab 后可聚焦想法输入框、表单 label 关联。依据 WebAIM/Deque 研究结论——自动化 a11y 整体仅覆盖 30-57%，但 lang/title/label/结构类检查正是其可靠子集，适合 CI 回归。
   来源：https://www.davidmello.com/software-testing/test-automation/playwright-accessibility-testing-axe-lighthouse-limitations
3. **网络/控制台卫生**：静态页阶段监听 `pageerror`、`console(type=error)`、同源响应 `status>=400`（豁免 401/403 鉴权探测；HTTP 404 走 response 事件而非 requestfailed）。依据 Playwright 官方事件语义。
   来源：https://runebook.dev/zh/docs/playwright/api/class-request/request-failure
4. **内部链接全量可达**：两页所有同源 `.html` 链接逐一 GET 断言 200（crawlable anchors 子集）。
5. **深度内容契约**：详细版 4 张成员卡 / 4 步工作流 / 3 条 FAQ / 4 张指标卡；打字机与流程终端动效真实输出（`#typed` 非空、`#termBody .tl` 出现）；FAQ 点击可展开。
6. **源码守护**：frontend_checks 新增 H1-H8（overview 加入 F1 a11y CSS 守护），regression_guard 新增 5 条双版首页特征。
7. **静态页 200**：smoke 增加 `/`、`/index.html`、`/overview.html` 状态码检查。
