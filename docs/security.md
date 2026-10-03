# 会话与访问控制

## 会话

- `sessionid` Cookie 只保存随机句柄；会话数据保存在 `ngxops_sessions`，JSON 序列化，默认 14 天过期。
- 登录辅助函数 `login_user(request, user)` 轮换 session key 后记录 `user_id`；`logout_user(request)` 清空并撤销当前会话。NX-010 登录成功会轮换会话键并保存非敏感设备识别信息；改密撤销该用户全部旧会话，再重新签发当前浏览器会话。
- 不活跃且停用、已删除或格式无效的账户不能继续使用登录态。过期会话在对应 Cookie 再次访问时惰性删除；全表过期数据清理留待数据保留阶段。
- `NGXOPS_SECRET_KEY` 优先用于 CSRF 签名；未配置时应用在数据目录创建 `.secret_key`。部署 HTTPS 时设置 `NGXOPS_HTTPS=1`，会话和 CSRF Cookie 使用 `Secure`。
- 运行日志位于数据目录 `logs/`，包含请求路径、状态、来源地址、业务操作摘要及异常信息；不会记录请求正文、Cookie、认证头或 SQL 绑定参数。日志目录仍包含运维标识信息，必须限制文件权限。
- SSH 凭证使用独立的 Fernet 密钥文件 `.fernet_key` 加密，不能与 `db.sqlite3` 分开备份。凭证解密 API 需要 `credentials.read` 权限并返回 `Cache-Control: no-store`；不得把密码、私钥写入日志、任务参数或审计详情。

## CSRF

- 应用全局保护所有非安全 HTTP 方法。浏览器获得签名的 `csrftoken` Cookie 后，URL 编码 HTML 表单提交 `csrf_token` 隐藏字段（模板值为 `request.state.csrf_token`）；JSON/AJAX 请求和文件上传脚本提交 `X-CSRFToken` 请求头。
- 校验令牌签名、Cookie 与请求令牌，并检查 `Origin` 或 `Referer`。同源默认可信；跨源部署通过 `NGXOPS_CSRF_TRUSTED_ORIGINS` 配置完整来源，例如 `https://ops.example.com`。
- 表单解析失败、缺失令牌或来源不可信均返回 HTTP 403。使用 Jinja2 返回页面请求错误；XHR、JSON Accept 或 `/api/` 请求返回公共 JSON API 错误结构。

## 认证与授权

- `get_current_user` 从服务端会话加载 `auth_user`，`require_authenticated_user`、`require_superuser` 和 `require_permission(resource, action)` 可用于页面/API 路由。
- `require_permission` 对超管直接放行；普通用户委托给 `app.state.permission_checker(db_session, user, resource, action)`。NX-011 已接入 RBAC 解析器；用户直授始终生效，个人角色优先于用户组角色，未配置任何授权时拒绝访问。
- 页面未登录跳转 `/login/?next=...`；JSON/AJAX 未登录返回 HTTP 401，并按 JSON API 公共错误结构提供登录跳转地址。页面无权访问按原规则写入一次性 `mngxops_perm_denied` 提示，同源 Referer 可回跳，否则由 Jinja2 渲染 403；JSON/AJAX 无权返回符合公共错误结构的 HTTP 403。接口约定见 [api.md](api.md)。
- 权限提示通过 `consume_permission_alert(session)` 读取并消费，供 NX-004 公共布局接入 `showAlert`。

`python-multipart` 固定为 0.0.20，用于解析 URL 编码及 multipart 表单。NX-021 节点工作簿导入只接受 `.xlsx`，路由最多读取 8 MiB + 1 byte 以拒绝超限文件，再交给 openpyxl 的只读模式解析；应用层限制不替代反向代理的请求体大小限制。

## 范围

本阶段实现会话、CSRF、认证/授权依赖与错误响应；NX-010 已增加账户页面、PBKDF2 密码校验、失败锁定、登录记录和设备冲突确认；NX-011 已接入用户、角色、用户组及 RBAC 管理；NX-020 已增加独立密钥加密的凭证存储和受 RBAC 保护的解密 API；NX-021 已增加带大小限制、全文件校验和 CSRF 保护的节点工作簿导入。原 Django 数据库及 session 序列化不会被自动接管。
