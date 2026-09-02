# Atoms Native — 官网首页 + Studio 体验 设计文档

> 日期：2026-09-02 · 目标：今晚 18:00 推送 Gitee 并部署到 taoxie.vip/atoms-native/
> 对标：atoms.dev 官网（暗色科技风）+ 美团 NoCode（对话式零代码体验）

## 1. 背景与目标

项目已有完整核心链路（注册 → 多智能体生成 → 沙箱预览 → 对话精修 → Race → Gallery → 导出），
但缺少营销官网首页，且 Studio 演示体验有提升空间。本次交付：

1. 对标 atoms.dev 的营销首页（暗色科技风）
2. Studio 增强（对齐美团 NoCode 核心体验的 3 项）
3. Loop Engineering 全程浏览器验证，最终推 Gitee + 部署上线供笔试展示

## 2. 方案（已确认：方案 A）

### 2.1 路由与页面结构
- `public/index.html` → 新营销官网首页（landing）
- 现有应用改名 `public/studio.html`，引用 `app.js` / `styles.css` 不变
- 页面间链接全部使用相对路径（兼容 nginx 子路径部署）
- 后端零改动：`app.mount("/", StaticFiles(html=True))` 自动生效
- `admin.html`、nginx 配置不动

### 2.2 Landing 首页区块（atoms.dev 暗色科技风）
| 区块 | 内容 |
|---|---|
| Hero | 大标题 + 打字机动画示例 idea + CTA（开始构建 / 观看演示）+ 渐变光效背景 |
| 数据条 | 4 个统计卡片：4 个 AI Agent / <120s 生成 / 100% 沙箱隔离 / 0 行代码 |
| AI Team | Emma(PM) / Bob(架构师) / Alex(工程师) / Mike(评审) 卡片 |
| 工作流程 | 想法 → 多智能体协作 → 实时预览 → 对话精修 四步 |
| 笔试亮点 | 沙箱安全边界 / Race Mode / SDD 工程化 / 离线降级韧性 |
| CTA + Footer | 进入 Studio + 技术栈说明 |

单文件 landing.html + 独立 landing.css（内联亦可），原生 JS 打字机动画，零构建。

### 2.3 Studio 增强（仅 3 项，控制范围）
1. **示例想法一键开始**：输入框上方 4-6 个示例 chips（点单系统/数据看板/小游戏/活动页），点击填充输入框
2. **生成过程视觉润色**：Agent 活动流加阶段图标与进度动画
3. **移动端适配**：Studio 关键布局响应式修复

### 2.4 验证（Loop Engineering）
每完成一个模块：本地起 uvicorn → 浏览器逐项验证 → 发现问题立即修复回归。
验证清单：landing 各区块渲染、CTA 跳转、示例 chips 填充、离线模式全链路
（注册→生成→预览→精修）、移动端视口、控制台无报错。最后接入真实 LLM key 复验真实生成。

### 2.5 交付
`git push gitee master` → scp 到 `43.143.231.106:/home/ubuntu/atoms-native` →
重启 tmux `atoms` → 浏览器验证线上地址 → 更新 README。
回滚预案：服务器保留上一版本目录备份，异常一键还原。

## 3. 非目标
- 不做单页整合（方案 B）、不做可视化拖拽编辑器、不改后端接口与数据库结构

## 4. 验收标准
- [ ] `/` 展示官网首页，各区块渲染正常、控制台无报错
- [ ] CTA 可跳转 `/studio.html`，Studio 原有功能无回归
- [ ] 示例 chips 点击可填充输入框并触发生成
- [ ] 375px 视口下 landing 与 Studio 关键页面可用
- [ ] 离线模板模式全链路跑通；真实 key 下生成效果正常
- [ ] Gitee 已推送；taoxie.vip/atoms-native/ 线上可访问且功能一致
