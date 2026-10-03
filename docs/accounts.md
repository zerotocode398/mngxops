# 账户与个人中心

## 页面

| 方法与路径 | 访问 | 行为 |
|---|---|---|
| `GET /` | 匿名或已登录 | 匿名跳转登录页；已登录显示仪表盘 |
| `GET /login/` | 匿名 | 显示独立登录页；已登录时跳转仪表盘 |
| `POST /login/` | 匿名 + CSRF | 校验凭证、记录结果并建立或确认会话 |
| `POST /logout/` | 浏览器会话 + CSRF | 撤销当前会话并跳回登录页 |
| `GET /profile/` | 已登录 | 展示用户名、邮箱、账户状态和时间 |
| `GET /password/change/` | 已登录 | 显示修改密码表单 |
| `POST /password/change/` | 已登录 + CSRF | 校验旧密码和新密码，撤销旧会话并轮换当前会话 |

这些是 HTML 页面路由，不作为 JSON API 发布。页面沿用 Jinja2；写请求使用 URL 编码表单及 `csrf_token` 隐藏字段。

## 登录与锁定

- 登录名按 `auth_user.username` 精确匹配。禁用账户显示联系管理员提示；未知账户和密码错误使用相同提示。
- 登录日志保存在 `ngxops_login_logs`，失败原因固定为 `user_not_found`、`wrong_password`、`user_locked`、`user_inactive`；不记录提交的密码。
- 已启用账户连续输错达到 `auth.login_fail_lock_count` 后锁定 `auth.login_fail_lock_minutes` 分钟，默认分别为 5 次和 15 分钟。已锁账户不继续累加；锁到期后错误次数从 1 重新计算；登录成功清除锁定状态。管理员手动清锁由 `clear_login_fail_lock(db_session, user_id)` 提供给 NX-011 用户管理调用。
- 失败阈值与时长由 NX-061 系统设置控制；管理员用户管理页面和提前解锁按钮由 NX-011 提供。
- 浏览器有已登录会话时，同一 `device_id` 的再次登录会撤销旧会话并继续；另一设备登录展示 IP、浏览器和 30 秒确认提示。确认后撤销该用户旧会话，取消则保留旧会话。

## 密码与会话

- 密码使用 Django 默认 `pbkdf2_sha256` 编码（600,000 次迭代），可验证原项目默认 PBKDF2 摘要。修改密码要求旧密码正确、两次新密码相同、长度至少 8 且不与用户名过于相似；另外拒绝纯数字以及内置的小型常见弱密码集合。
- 成功改密会撤销 `ngxops_sessions` 中该用户全部会话，再轮换并保存当前浏览器会话。停用账户在后续请求由认证依赖退出。
- 初始超级管理员可通过 `ngxops admin create` 创建，默认用户名为 `admin`；也可附加自定义用户名。命令要求数据库至少迁移到版本 3、数据库中没有用户，并使用隐藏输入两次确认密码。统一 CLI 的本机密码重置命令为 `ngxops admin reset`，默认重置 `admin`，也可附加其他超级管理员用户名；操作会清除其锁定状态并撤销旧会话。旧源码入口 `python -m ngxops.accounts <username>` 仍保留用于兼容。

## 与参考项目的边界

- `/logout/` 使用 POST，以满足 ngxops 全局 CSRF 策略；参考项目路由接受 GET。
- 登录成功默认进入仪表盘；显式的安全 next 参数仍优先。
- 个人中心只读；参考项目 `ProfileView` 当前同样只实现 GET，文档中提到的资料编辑和头像上传没有对应表单实现。
- 登录锁定设置可在系统设置页面调整，范围和生效时机见 [settings.md](settings.md)。
- 参考项目的 `check-session/` 没有模板或静态脚本调用，ngxops 未迁移该轮询路由；登录冲突通过登录时读取服务端会话实现。
- Django 的常见密码校验依赖完整弱口令词表；ngxops 当前使用维护在 `passwords.py` 的紧凑集合，其他校验规则保持一致。
