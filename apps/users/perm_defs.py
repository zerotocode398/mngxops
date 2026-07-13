RESOURCE_CHOICES = (
    ("nodes", "\u8282\u70b9"),
    ("credentials", "\u51ed\u8bc1"),
    ("configs", "\u914d\u7f6e"),
    ("releases", "\u53d1\u5e03"),
    ("upgrade", "Nginx \u5347\u7ea7"),
    ("users", "\u7528\u6237"),
    ("roles", "\u89d2\u8272"),
    ("teams", "\u7528\u6237\u7ec4"),
    ("audit", "\u5ba1\u8ba1"),
    ("settings", "\u7cfb\u7edf\u8bbe\u7f6e"),
)

ACTION_CHOICES = (
    ("read", "\u67e5\u770b"),
    ("create", "\u65b0\u589e"),
    ("update", "\u7f16\u8f91"),
    ("delete", "\u5220\u9664"),
)

PERM_DISPLAY_NAMES = {
    "nodes": {
        "read": "\u8282\u70b9\u67e5\u770b",
        "create": "\u65b0\u5efa\u8282\u70b9",
        "update": "\u7f16\u8f91\u8282\u70b9",
        "delete": "\u5220\u9664\u8282\u70b9",
    },
    "credentials": {
        "read": "\u51ed\u8bc1\u67e5\u770b",
        "create": "\u65b0\u5efa\u51ed\u8bc1",
        "update": "\u7f16\u8f91\u51ed\u8bc1",
        "delete": "\u5220\u9664\u51ed\u8bc1",
    },
    "configs": {
        "read": "\u914d\u7f6e\u67e5\u770b",
        "create": "\u65b0\u5efa\u914d\u7f6e",
        "update": "\u7f16\u8f91\u914d\u7f6e",
        "delete": "\u5220\u9664\u914d\u7f6e",
    },
    "releases": {
        "read": "\u4efb\u52a1\u67e5\u770b",
        "create": "\u65b0\u5efa\u53d1\u5e03\u4efb\u52a1",
        "update": "\u53d1\u5e03/\u56de\u6eda\u4efb\u52a1",
        "delete": "\u5220\u9664\u4efb\u52a1",
    },
    "upgrade": {
        "read": "\u5347\u7ea7\u5386\u53f2\u67e5\u770b",
        "create": "\u521b\u5efa\u5347\u7ea7\u4efb\u52a1",
        "update": "\u56de\u6eda\u5347\u7ea7",
        "delete": "\u5220\u9664\u5347\u7ea7\u8bb0\u5f55",
    },
    "users": {
        "read": "\u7528\u6237\u67e5\u770b",
        "create": "\u65b0\u5efa\u7528\u6237",
        "update": "\u7f16\u8f91\u7528\u6237",
        "delete": "\u5220\u9664\u7528\u6237",
    },
    "roles": {
        "read": "\u89d2\u8272\u67e5\u770b",
        "create": "\u65b0\u5efa\u89d2\u8272",
        "update": "\u7f16\u8f91\u89d2\u8272",
        "delete": "\u5220\u9664\u89d2\u8272",
    },
    "teams": {
        "read": "\u7528\u6237\u7ec4\u67e5\u770b",
        "create": "\u65b0\u5efa\u7528\u6237\u7ec4",
        "update": "\u7f16\u8f91\u7528\u6237\u7ec4",
        "delete": "\u5220\u9664\u7528\u6237\u7ec4",
    },
    "audit": {
        "read": "\u65e5\u5fd7\u67e5\u770b",
        "create": "\u65b0\u589e\u65e5\u5fd7",
        "update": "\u7f16\u8f91\u65e5\u5fd7",
        "delete": "\u5220\u9664\u65e5\u5fd7",
    },
    "settings": {
        "read": "\u7cfb\u7edf\u8bbe\u7f6e\u67e5\u770b",
        "create": "\u65b0\u589e\u8bbe\u7f6e",
        "update": "\u4fee\u6539\u7cfb\u7edf\u8bbe\u7f6e",
        "delete": "\u5220\u9664\u8bbe\u7f6e",
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