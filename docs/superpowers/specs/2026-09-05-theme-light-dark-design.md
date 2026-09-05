# 全站浅色/深色双主题设计（2026-09-05）

## 目标
每个页面都有浅色+深色两套主题；颜色模式跨页面绑定（localStorage），消除"简约首页(浅) → Studio(深)"的突兀跳变；全站默认浅色。

## 方案（已确认：方案 1 反向覆盖层）
不重写现有规则，给每套样式末尾追加"反向主题覆盖块"：

| 文件 | 现默认 | 追加 |
|---|---|---|
| home.css + index.html 内联 | 浅 | `html[data-theme="dark"]` 深色化 |
| overview.css（覆盖 landing） | 浅 | `html[data-theme="dark"]` 回落 landing 原生深色 |
| styles.css（studio/team） | 深 | `html[data-theme="light"]` 浅色化（终端/代码区保留深色） |
| discover/plan/portfolio/admin 内联 :root | 深 | 各自 `html[data-theme="light"]` 浅色化 |

## 数据流
```
localStorage['an_theme'] ('light'|'dark'，无值=light)
  → <head> 内联防闪烁脚本（先于渲染）→ <html data-theme>
  → theme.js: #themeToggle 点击 → 切 data-theme + 写 localStorage + 换图标
  → CSS 反向覆盖块生效；color-scheme 同步
```

## 关键决策
- 全站默认浅色（用户选定 B）；无 JS 时无 data-theme → 各页维持现状默认
- 浅色模式下终端/代码/预览面板保留深色（对比度 + 酷感）
- 深色块禁止使用 #475569 / #64748b（F3 低对比守卫同样适用）
- 新增 public/theme.css：仅 .theme-toggle 按钮样式（currentColor，两主题通用）
- localStorage key：`an_theme`；读写均 try/catch（隐私模式不炸）

## loop 测试
- frontend_checks.py 新增 I 节：8 页均有防闪烁内联 + #themeToggle + theme.js/theme.css 引用；4 套样式均有反向覆盖块；无低对比灰
- homepage_journeys.py 新增主题旅程：切换→跳 Studio 仍 dark→刷新仍 dark→切回 light
- regression_guard.py：theme.js/theme.css 存在 + index 防闪烁特征

## 回滚
纯增量 CSS/JS + 每页 4 行内联，revert 单 commit 即可全量回退。
