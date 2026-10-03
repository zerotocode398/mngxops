"""集中定义 RBAC 资源、动作及其展示名称。"""

from typing import Dict, List, Tuple


RESOURCE_CHOICES: Tuple[Tuple[str, str], ...] = (
    ("nodes", "节点管理"),
    ("credentials", "凭证管理"),
    ("configs", "配置管理"),
    ("releases", "发布管理"),
    ("upgrade", "Nginx 安装/升级"),
    ("nginx_install", "Nginx 安装"),
    ("nginx_service", "Nginx 启停"),
    ("nginx_uninstall", "Nginx 卸载"),
    ("audit", "审计日志"),
    ("settings", "系统设置"),
)

ACTION_CHOICES: Tuple[Tuple[str, str], ...] = (
    ("read", "查看"),
    ("create", "新增"),
    ("update", "编辑"),
    ("delete", "删除"),
    ("ssh_test", "SSH 测试"),
    ("lock", "锁定"),
    ("unlock", "解锁"),
    ("enable", "启用"),
    ("sync", "配置同步"),
    ("publish", "发布/回滚"),
    ("operate", "启停操作"),
    ("execute", "执行操作"),
)

PERM_DISPLAY_NAMES: Dict[str, Dict[str, str]] = {
    "nodes": {
        "read": "节点查看",
        "create": "新建节点",
        "update": "编辑节点",
        "delete": "删除节点",
        "ssh_test": "SSH 连接测试",
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
    "releases": {"read": "任务查看", "publish": "执行发布/回滚"},
    "upgrade": {
        "read": "安装/升级历史查看",
        "create": "创建安装/升级任务",
        "delete": "删除安装/升级记录",
        "execute": "执行安装/升级",
    },
    "nginx_install": {"read": "安装历史查看", "create": "创建安装任务"},
    "nginx_service": {"read": "启停操作台/历史查看", "operate": "执行启停"},
    "nginx_uninstall": {"read": "卸载历史查看", "execute": "执行卸载"},
    "audit": {"read": "审计日志查看"},
    "settings": {"read": "系统设置查看"},
}


def permission_code(resource: str, action: str) -> str:
    """返回资源和动作组成的稳定权限编码。"""
    return "{}.{}".format(resource, action)


def all_permission_items() -> List[Dict[str, str]]:
    """按资源定义顺序构造完整的权限项种子。"""
    return [
        {
            "code": permission_code(resource, action),
            "name": name,
            "resource": resource,
            "action": action,
        }
        for resource, _label in RESOURCE_CHOICES
        for action, name in PERM_DISPLAY_NAMES.get(resource, {}).items()
    ]
