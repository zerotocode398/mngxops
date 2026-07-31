"""用户模块模板上下文处理器"""
from apps.users.permissions import SESSION_PERM_DENIED_KEY


def perm_denied_alert(request):
    """一次性取出无权访问弹窗内容，供 base.html 调用 showAlert"""
    data = request.session.pop(SESSION_PERM_DENIED_KEY, None)
    if not data:
        return {"perm_denied_alert": None}
    title = (data.get("title") or "").strip()
    message = (data.get("message") or "").strip()
    if not title and not message:
        return {"perm_denied_alert": None}
    return {
        "perm_denied_alert": {
            "title": title or "无访问权限",
            "message": message,
        }
    }
