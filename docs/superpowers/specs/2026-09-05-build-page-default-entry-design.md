# build.html 简约生成页：默认入口设计（2026-09-05）

## 背景
首页"开始构建"目前直达 studio.html：先撞登录墙，再是三栏工作台，与首页简约气质割裂。
用户决策：简约生成画面成为默认页面，Studio 降级为备选完整工作台。

## 产品调研证据（同类范式）
- v0.app：默认 prompt-first（居中大输入框+建议 chip），匿名可生成、保存才注册；复杂工作台全部收纳为生成后的第二层。（https://v0.app 、https://v0.dev/faq）
- bolt.new：官方 QuickStart 明确"点 Build now 后才弹登录"。（https://support.bolt.new/get-started/quickstart）
- lovable.dev：输入框始终是第一视觉主体。（https://lovable.dev）
- 共性：输入零门槛 → 登录卡在生成时刻 → 流式+预览同屏 → 工作台是第二层。
（趋势：https://www.interpixdesign.com/blog-the-prompt-is-the-new-interface "空状态 prompt 框是最重要的 UX 时刻"）

## 测试调研证据（落地 15 条，节选全量见 journeys 注释）
- SSE mock：page.route + route.fulfill(text/event-stream)，只断言最终态不断中间帧。（https://playwright.dev/docs/mock）
- auth：401 用 route.fulfill({status:401})；token 持久化断言 localStorage。（https://playwright.dev/docs/auth）
- a11y：WCAG 4.1.3——live region 容器必须先于内容存在（F103）；progressbar 必须有 aria-label + valuenow。（https://w3c.github.io/wcag/understanding/status-messages 、https://www.w3.org/WAI/WCAG22/Techniques/failures/F103.html）
- iframe：title 必须描述性；sandbox 严禁 allow-scripts+allow-same-origin 组合；srcdoc 是 injection sink 必须进沙箱。（https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/iframe）
- 状态机：sad path（失败后状态复位）、终态吸收、重复提交防抖、刷新恢复。（https://getautonoma.com/blog/happy-path-testing-beyond-the-happy-path）

## 入口流改造（主漏斗全部指向 build.html）
| 页面 | 改动 |
|---|---|
| index.html | composer action → ./build.html；4 快捷场景 href → ./build.html?idea=…；nav"进入 Studio"→"完整工作台 ↗" |
| overview.html | nav-cta 与 hero CTA → ./build.html |
| discover.html | 使用模板跳转 → ./build.html?idea=…（footer"进入工作台"保留 studio） |
| portfolio.html | 同 discover |
| team/studio | 不动（studio 成为备选完整工作台） |

## build.html 状态机（同页四态：input → auth → building → done）
- input：居中"把想法变成应用" + 大输入框（?idea= 预填 + localStorage an_idea_draft 草稿回填）+ 3 个示例 chip
- auth：无 an_token 点生成 → 输入框下展开内嵌登录卡（登录/注册 tab，POST /api/auth/login|register，写 an_token 与 app.js 互通），成功自动继续，想法全程保留
- building：极简时间线——4 个智能体节点（Emma/Bob/Alex/Mike）随 SSE phase 点亮 + role="status" 当前行（初始空容器在 HTML 中先存在）+ role="progressbar"（aria-label，valuenow 20/50/80/95/100）；building 态生成按钮 disabled（防重复提交）
- done：iframe（sandbox="allow-scripts allow-forms allow-modals allow-popups allow-downloads"，title="生成的应用预览"，srcdoc 注入）平滑展开 + 操作条：🔄 再来一个（回 input）/ 🧭 进完整工作台精修（sessionStorage an_open_project=pid → studio.html）/ 📋 复制 / ⬇️ 导出
- 数据流：POST /api/projects（Bearer）→ pid → SSE POST /api/generate（model 走 /api/models 默认）
- 错误处理：401 → 回 auth 态并清 token；SSE error/断流 → 时间线错误行 + 重试按钮；finally 保证状态可复用；终态吸收（done 后不可再触发生成）
- 复用：home.css tokens + theme.css/theme.js/themeToggle 三件套 + 防闪烁内联；独立内联 JS 不引 app.js

## 测试（loop）
- frontend_checks：H3/H5 改指 build；THEME_PAGES + "build"；B 节 API 接线；新增 J 节（≈15 条静态断言：role=status 空容器先存在、progressbar aria 全家、sandbox token 无 allow-same-origin、title 非占位、登录卡、4 节点、终态吸收按钮、an_idea_draft）
- homepage_journeys：主路径改落 build?idea=；新旅程——mock SSE（route.fulfill）主路径（时间线→预览→frameLocator 断言 h1）、未登录弹卡→注册→token 持久化→自动续跑、401 回退清 token、重复提交防抖（挂起 route 计数）、刷新草稿回填
- regression_guard：'action="./studio.html"' → build；build.html 关键特征
- smoke：/build.html 200

## 部署
gate 全绿 → 推送 gitee/github → taoxie.vip 备份+拉取+tmux 重启 → 线上验证（build 200、theme 200、主漏斗跳转正确、未登录 use 401）。
