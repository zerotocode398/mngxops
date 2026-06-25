RESOURCE_CHOICES = (
    ("nodes", "节点"),
    ("credentials", "凭证"),
    ("configs", "配置"),
    ("releases", "发布"),
    ("users", "用户"),
    ("roles", "角色"),
    ("audit", "审计"),
)

ACTION_CHOICES = (
    ("read", "查看"),
    ("create", "新增"),
    ("update", "编辑"),
    ("delete", "删除"),
)


def permission_code(resource, action):
    return f"{resource}.{action}"


def all_permission_items():
    items = []
    for resource, resource_label in RESOURCE_CHOICES:
        for action, action_label in ACTION_CHOICES:
            items.append(
                {
                    "code": permission_code(resource, action),
                    "name": f"{resource_label}-{action_label}",
                    "resource": resource,
                    "action": action,
                }
            )
    return items

