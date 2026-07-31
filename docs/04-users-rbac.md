# 04 · 用户 / 角色 / 用户组 / RBAC（users）

## 1. 模块目标与范围

管理平台用户、角色（权限矩阵）、用户组（组织分组与角色继承），实现 `resource.action` 鉴权。

**不做**：按节点/环境的数据级 ACL、审批流。

## 2. 角色与权限码

资源：`users` / `roles` / `teams`，动作 read/create/update/delete。  
定义：[`apps/users/perm_defs.py`](../apps/users/perm_defs.py)。  
校验：[`apps/users/permissions.py`](../apps/users/permissions.py) `user_has_permission`、`PermissionRequiredMixin`。  
模板：`has_perm_code`。

### 2.1 生效规则

1. `is_superuser` → 全部允许  
2. `UserProfile.direct_permissions` 命中 code → 允许  
3. 若个人绑定了角色（`profile.groups`）→ **仅**用个人角色，忽略用户组角色  
4. 否则使用所属 `UserTeam.roles` 的权限并集  
5. 否则拒绝  

## 3. 领域模型

见 [`apps/users/models.py`](../apps/users/models.py)：

| 模型 | 要点 |
|------|------|
| `PermissionItem` | `code` 唯一，如 `configs.update` |
| `UserGroup` | 角色名唯一；M2M permissions |
| `UserTeam` | 组名唯一；members、roles |
| `UserProfile` | 1:1 User；mobile、avatar、groups、direct_permissions、remark |

Django `User`：`username` 限 `[-a-zA-Z0-9_]+`；中文放姓名字段（Q82）。

## 4. 页面与路由

| 区域 | 路径前缀 |
|------|----------|
| 用户 | `/users/` list/create/`<pk>/edit|delete|lock` |
| 角色 | `/users/groups/` 与 `/users/roles/` 别名 |
| 用户组 | `/users/teams/` + members / manage-members |

模板目录：`apps/users/templates/users/`。  
表单分区 + 角色/用户组弹窗多选 chips（Q68/Q83）。

## 5. 用例

### 5.1 用户 CRUD

- 创建：账号、密码、姓名、手机、角色、**所属用户组**（Q83）、直授可选。
- 编辑：同上；路由用 `pk`（Q82）。
- 删除：确认页。
- 锁定/解锁：切换 `is_active`。

### 5.2 角色 CRUD

- 权限矩阵勾选（perm-matrix 全局样式 Q42）。
- 管理角色下用户。

### 5.3 用户组 CRUD

- 维护成员与关联角色；列表展示成员数等。

## 6. 实现要点

- 权限项 seed：与 `all_permission_items()` 同步（应用启动或迁移逻辑以实现为准）。
- 操作列 icon-only + `btn-sm` 新增（Q68/Q78）。

## 7. 前后端约定

- 页面无权：回首页 + 全局 `showAlert`（Q116）；AJAX 无权仍返回 JSON 403。
- 侧栏「用户管理/角色/用户组」分别受权限控制；首页快捷入口/统计卡跳转亦按权限收口。

## 8. 异常与边界

- 非法用户名导致历史 `NoReverseMatch` 已通过 pk 路由修复（Q82）。
- 去掉个人角色后才会回落到用户组角色——文档与培训需说明优先级。

## 9. 关联模块

accounts、audit、全业务 View 鉴权。

## 10. 已落地优化索引

| Q | 摘要 |
|---|------|
| Q68 | 表单分区与角色弹窗 |
| Q78 | 新增按钮 btn-sm |
| Q82 | 路由改 pk、禁中文用户名 |
| Q83 | 用户可选所属用户组 |
| Q116 | 无权访问回首页 + showAlert |

## 11. 待确认缺口

无单独编号；任务中心权限不对称见 Q88（releases）。
