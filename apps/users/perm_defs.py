RESOURCE_CHOICES = (
    ("nodes", "节点"),
    ("credentials", "凭证"),
    ("configs", "配置"),
    ("releases", "发布"),
    ("users", "用户"),
    ("roles", "角色"),
    ("teams", "用户组"),
    ("audit", "审计"),
)

ACTION_CHOICES = (
    ("read", "查看"),
    ("create", "新增"),
    ("update", "编辑"),
    ("delete", "删除"),
)

# 每个资源下各 action 的显示名称，用于角色权限页面展示
PERM_DISPLAY_NAMES = {
    "nodes": {
        "read": "节点查看",
        "create": "新建节点",
        "update": "编辑节点",
        "delete": "删除节点",
    },
    "credentials": {
        "read": "凭证查看",
        "create": "新建凭证",
        "update": "编辑凭证",
        "delete": "删除凭证",
    },
    "configs": {
        "read": "配置查看",
        "create": "新建配置",
        "update": "编辑配置",
        "delete": "删除配置",
    },
    "releases": {
        "read": "任务查看",
        "create": "新建发布任务",
        "update": "发布/回滚任务",
        "delete": "删除任务",
    },
    "users": {
        "read": "用户查看",
        "create": "新建用户",
        "update": "编辑用户",
        "delete": "删除用户",
    },
    "roles": {
        "read": "角色查看",
        "create": "新建角色",
        "update": "编辑角色",
        "delete": "删除角色",
    },
    "teams": {
        "read": "用户组查看",
        "create": "新建用户组",
        "update": "编辑用户组",
        "delete": "删除用户组",
    },
    "audit": {
        "read": "日志查看",
        "create": "新增日志",
        "update": "编辑日志",
        "delete": "删除日志",
    },
}


def permission_code(resource, action):
    return f"{resource}.{action}"


def all_permission_items():
    items = []
    for resource, resource_label in RESOURCE_CHOICES:
        names = PERM_DISPLAY_NAMES.get(resource, {})
        for action, action_label in ACTION_CHOICES:
            items.append(
                {
                    "code": permission_code(resource, action),
                    "name": names.get(action, f"{resource_label}-{action_label}"),
                    "resource": resource,
                    "action": action,
                }
            )
    return items

