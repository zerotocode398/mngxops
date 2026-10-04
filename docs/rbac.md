# 用户、角色与用户组

## 权限解析

权限编码使用 `resource.action`。NX-011 在迁移版本 4 中创建权限项、角色、用户组、用户资料及其关联表，并从 `ngxops.rbac.permission_defs` 写入 29 个权限项。运行 Web 服务不会初始化或修改这些表；先停止服务并备份数据库，再执行 `python -m ngxops.database upgrade`。

每次权限判断按以下顺序执行：

1. 超级管理员允许所有权限。
2. 用户直授权限命中时允许。
3. 用户属于的用户组关联了角色时，合并这些用户组角色权限，个人角色不参与。
4. 没有生效的用户组角色时，合并个人角色权限。
5. 其他情况拒绝。

个人角色最多 3 个；用户组角色不占该上限。组角色按所有所属用户组取并集；移除组角色关联后，该角色立即不再对组成员生效，用户组成员关系和个人角色关系仍分别保留。用户直授权限独立叠加。用户、角色和用户组管理只允许超级管理员访问。相同解析函数接入 `require_permission` 与全局导航，菜单和页面门禁使用同一规则。

Nginx 全新安装和编译安装/升级共用 `upgrade.read/create/delete/execute` 权限资源。迁移 v15 会将旧安装查看权限转换为 `upgrade.read`，旧安装创建权限转换为 `upgrade.execute`，并移除独立安装权限项；任务仍按 `nginx_install`、`nginx_upgrade` 等操作类型区分。

## 页面

| 页面 | 路径 |
|---|---|
| 用户列表 | `/users/` |
| 新建用户 | `/users/create/` |
| 编辑/删除/启停用户 | `/users/{user_id}/edit/`、`/users/{user_id}/delete/`、`/users/{user_id}/lock/` |
| 角色列表 | `/users/roles/`，兼容别名 `/users/groups/` |
| 角色新建/编辑/删除 | `/users/roles/create/`、`/users/roles/{role_id}/edit/`、`/users/roles/{role_id}/delete/` |
| 角色成员 | `/users/roles/{role_id}/manage-users/` |
| 用户组列表、新建、编辑、删除 | `/users/teams/`、`/users/teams/create/`、`/users/teams/{team_id}/edit/`、`/users/teams/{team_id}/delete/` |

用户编辑可设置账号、邮箱、备注、可选密码、超级管理员标记、个人角色、用户组和直授权限；角色和用户组通过可搜索的选择弹窗关联。创建用户从普通账户开始；不能删除或停用当前登录账户。解锁操作清除 NX-010 的连续失败锁定状态。角色表单使用资源动作矩阵；用户组表单维护角色继承，成员选择在用户组列表弹窗中分页搜索并批量加入/移出。

## JSON 接口

| 方法与路径 | 行为 |
|---|---|
| `GET /api/users/teams/{team_id}/members?page=1&per_page=10&search=...` | 默认每页 10 人（可选 1–100），按逗号分隔的用户名/邮箱搜索词过滤，返回成员标记、当前生效角色数和分页状态 |
| `POST /api/users/teams/{team_id}/members` | JSON 请求体为 `{"action":"add"|"remove","user_ids":[...]}`，单批最多 200 人；重复加入/移出幂等 |

两个接口都要求超级管理员会话；POST 另外要求全局 CSRF Cookie 和 `X-CSRFToken` 头。响应模型、状态码和安全方案在 `/openapi.json` 中登记，错误结构遵循 [api.md](api.md)。

## 与参考实现的差异

Jinja 页面继续使用表单提交；用户组成员查询和变更统一迁到 `/api/`，由 FastAPI 请求/响应模型描述。原 Django 的个人角色优先规则与本项目第 11 项验收要求不一致；本项目按用户确认的组角色优先级计算权限。个人角色、用户组成员和用户组角色分开保存，不把组角色复制进成员个人角色，因此修改或解除组角色关联会动态反映到成员权限。NX-011 不迁入原 Django 数据库；原数据库接管仍受 schema 基线保护。
