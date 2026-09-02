# Atoms Native · Admin 管理端设计（2026-09-03）

## 背景与目标

后端 RBAC/审计/看板接口已存在并通过生产验证（`/api/metrics`、`/api/audit`、
`/api/admin/set-role`，均 `require_role("admin")` 拦截），但缺少可用的管理界面：
旧 `admin.html` 无鉴权接线、使用绝对路径（子路径部署 404）。

目标：把已验证的裸接口界面化，形成完整管理端——**看板 + 用户角色管理 + 审计
（含链完整性徽章）**。不新增鉴权机制，不动主流程。

## 范围

### 做
- 重写 `public/admin.html`：登录框（页内）→ 三 Tab（研效看板/用户管理/审计日志）
- 新增 `GET /api/admin/users`（仅 admin，返回 id/username/role/created_at，审计留痕）
- 新增 `scripts/make_admin.py` CLI 提权脚本（幂等）
- 单测（RBAC 403/200、脚本幂等）+ 前端接线检查 + 本地/生产 E2E

### 不做（YAGNI）
- 用户增删/禁用/重置密码、审计分页与筛选、发现页模板管理
- 导航栏 admin 入口（纯 URL 直达，保持低调）
- 后端鉴权/审计/看板接口的任何改动

## 页面结构

```
登录态检测（init）：
  localStorage an_token → GET ./api/me
    401/无 token → 登录框（POST ./api/auth/login）
    role != admin → 「无权限」卡 + 登出
    admin → 主界面

主界面三 Tab：
  1. 研效看板：KPI 网格 + 智能体×模型分布表 + 最近 20 次调用（原看板逻辑，
     全部改相对路径 ./api/metrics）；15s 自动刷新
  2. 用户管理：用户表（id/用户名/角色徽章/注册时间）+ 角色切换下拉
     （POST ./api/admin/set-role，仅 user↔admin）；手动刷新
  3. 审计日志：链完整性徽章（chain_intact → 🔒绿 / 红+断链位置）+
     高危告警卡（红色高亮，alerts 数组）+ 事件表（时间/操作者/动作/对象/
     详情/来源IP）；手动刷新
```

## 后端增量

### GET /api/admin/users
- 依赖：`require_role("admin")`
- 返回：`{"users": [{id, username, role, created_at}]}`
- 按 id 升序，无分页（当前用户量 <100，YAGNI）
- `log_audit(admin.id, "admin_view_users", ...)` 留痕

### scripts/make_admin.py
- 用法：`python scripts/make_admin.py <username>`
- 行为：users 表 role='user'→'admin'；已是 admin 则输出提示；用户不存在报错退出码 1
- 幂等；复用 `database.get_conn()`

## 错误处理

| 场景 | 行为 |
|------|------|
| token 失效（401） | 回登录框，toast 提示 |
| 非 admin（403） | 「无权限」卡 + 登出按钮 |
| set-role 失败（404/400） | toast 显示 detail，不阻塞 |
| 数据加载失败 | 空态 + 重试按钮 |
| 链断裂（chain_intact=false） | 红色徽章 + 显示 chain_broken_at |

## 视觉

沿用发现页「极简美学」：深色径向渐变背景、卡片流（--card 圆角 14px 边框
#243056）、渐变 KPI 数字（--accent→--accent2 clip text）、pill 状态徽章、
system-ui 字体。三 Tab 用顶部胶囊切换，激活态渐变描边。

## 测试与验收

1. **单测**（tests/unit_tests.py 追加）：
   - `/api/admin/users`：user 角色 403、admin 200、返回字段完整
   - make_admin：提权/幂等/不存在用户三态
2. **前端检查**（tests/frontend_checks.py 追加）：相对路径 `./api/`、
   三 Tab 元素存在、链徽章元素存在
3. **E2E**：本地 + 生产——非 admin 403 → make_admin 提权 → 三 Tab 数据全 200
4. 验收：生产 taoxie.vip/atoms-native/admin.html 可登录、三 Tab 正常、
   set-role 操作出现在审计事件流

## 依赖与风险

- 无新依赖、无 schema 变更（users.role 已存在）
- 风险低：全部只读接口 + 一个已验证的 set-role 写接口
