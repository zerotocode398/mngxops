RESOURCE_CHOICES = (
    ("nodes", "节点管理"),
    ("credentials", "凭证管理"),
    ("configs", "配置管理"),
    ("releases", "发布管理"),
    ("upgrade", "Nginx 升级"),
    ("nginx_install", "Nginx 安装"),
    ("nginx_service", "Nginx 启停"),
    ("nginx_uninstall", "Nginx 卸载"),
    ("users", "用户管理"),
    ("roles", "角色管理"),
    ("teams", "用户组管理"),
    ("audit", "审计日志"),
    ("settings", "系统设置"),
)

ACTION_CHOICES = (
    ("read", "查看"),
    ("create", "新增"),
    ("update", "编辑"),
    ("delete", "删除"),
    ("ssh_test", "SSH测试"),
    ("lock", "锁定"),
    ("unlock", "解锁"),
    ("enable", "启用"),
    ("sync", "配置同步"),
    ("publish", "发布/回滚"),
    ("operate", "启停操作"),
    ("execute", "执行操作"),
)

PERM_DISPLAY_NAMES = {
    "nodes": {
        "read": "节点查看",
        "create": "新建节点",
        "update": "编辑节点",
        "delete": "删除节点",
        "ssh_test": "SSH连接测试",
        "lock": "锁定节点",
        "unlock": "解锁节点",
    },
    "credentials": {
        "read": "凭证查看",
        "create": "新建凭证",
        "update": "编辑凭证",
        "delete": "删除凭证",
        "enable": "启用凭证",
    },
    "configs": {
        "read": "配置查看",
        "create": "新建配置",
        "update": "编辑配置",
        "delete": "删除配置",
        "sync": "同步配置",
    },
    "releases": {
        "read": "任务查看",
        "create": "新建发布任务",
        "update": "取消任务",
        "delete": "删除任务",
        "publish": "执行发布/回滚",
    },
    "upgrade": {
        "read": "升级历史查看",
        "create": "创建升级任务",
        "update": "回滚升级",
        "delete": "删除升级记录",
        "execute": "执行升级",
    },
    "nginx_install": {
        "read": "安装首页/历史查看",
        "create": "创建安装任务",
        "update": "编辑安装",
        "delete": "删除安装记录",
        "execute": "执行安装",
    },
    "nginx_service": {
        "read": "启停操作台/历史查看",
        "operate": "执行启停/重载",
    },
    "nginx_uninstall": {
        "read": "卸载首页/历史查看",
        "create": "创建卸载任务",
        "update": "编辑卸载",
        "delete": "删除卸载记录",
        "execute": "执行卸载",
    },
    "users": {
        "read": "用户查看",
        "create": "新建用户",
        "update": "编辑用户",
        "delete": "删除用户",
        "lock": "锁定用户",
        "unlock": "解锁用户",
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
    },
    "settings": {
        "read": "系统设置查看",
        "update": "修改系统设置",
    },
}


def permission_code(resource, action):
    return f"{resource}.{action}"


def all_permission_items():
    items = []
    for resource, resource_label in RESOURCE_CHOICES:
        names = PERM_DISPLAY_NAMES.get(resource, {})
        for action, display_name in names.items():
            items.append(
                {
                    "code": permission_code(resource, action),
                    "name": display_name,
                    "resource": resource,
                    "action": action,
                }
            )
    return items
