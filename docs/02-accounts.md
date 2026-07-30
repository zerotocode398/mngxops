# 02 · 账户与个人中心（accounts）

## 1. 模块目标与范围

提供会话登录/登出、个人资料维护与修改密码。使用 Django 内置 `User`，本应用无独立业务模型。

**不做**：注册自助开户、SSO/LDAP、找回密码邮件流。

## 2. 角色与权限

- 登录页：匿名可访问。
- 资料/改密：已登录用户；不额外要求 `users.*` 权限。
- 锁定用户（`is_active=False`）不可登录。

## 3. 领域模型

无；依赖 `django.contrib.auth.models.User` 与可选 `UserProfile`（在 users 应用）。

## 4. 页面与路由

| 路径 | View | 模板 |
|------|------|------|
| `/login/` | `LoginView` | `accounts/login.html` |
| `/logout/` | `LogoutView` | — |
| `/profile/` | `ProfileView` | `accounts/profile.html` |
| `/password/change/` | `PasswordChangeView` | `accounts/password_change.html` |

实现：[`apps/accounts/views.py`](../apps/accounts/views.py)、[`forms.py`](../apps/accounts/forms.py)。

## 5. 用例

### 5.1 登录

1. 提交用户名密码（`LoginForm`）。
2. 校验失败：写 `LoginLog(status=failed)`，区分 `user_not_found` / `wrong_password` / `user_locked` / `user_inactive`。
3. 成功：建立 session；写 `LoginLog(success)`；可写 `AuditLog`。
4. 跳转 `LOGIN_REDIRECT_URL`（仪表盘）。

### 5.2 登出

清除 session，返回登录页。

### 5.3 个人资料

查看/更新与个人相关展示字段（与 Profile 表单一致）；头像等走 media。

### 5.4 修改密码

`CustomPasswordChangeForm`；成功后按 Django 惯例保持或要求重新登录（以实现为准）。

## 6. 实现要点

- 登录失败原因枚举与审计列表筛选对齐（Q70）。
- UI 对齐全局变量与去嵌套 container（Q42）。

## 7. 前后端约定

- 登录页独立于 app-shell 或精简壳（以实现模板为准）。
- 错误信息中文提示。

## 8. 异常与边界

- 已登录访问登录页：通常重定向首页。
- AJAX 场景下 session 过期由 `AjaxErrorMiddleware` 返回 JSON 401。

## 9. 关联模块

users（锁定）、audit（登录日志）、dashboard（登录落地页）。

## 10. 已落地优化索引

| Q | 摘要 |
|---|------|
| Q42 | 登录页样式对齐 |
| Q70 | 登录日志失败原因与筛选 |

## 11. 待确认缺口

无单独 Q；审计成功/失败口径见 Q94（全局）。
