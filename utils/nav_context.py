"""侧栏导航上下文：跨模块共享页（如源码包）透传高亮目标"""
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl

# 允许覆盖侧栏 data-nav 的取值
ALLOWED_SIDEBAR_NAV = frozenset({"nginx_install", "upgrade", "nginx_service"})


def get_sidebar_nav(request):
    """从 GET/POST 读取合法的侧栏导航覆盖值，非法则空串"""
    nav = (request.POST.get("nav") or request.GET.get("nav") or "").strip()
    return nav if nav in ALLOWED_SIDEBAR_NAV else ""


def append_nav_query(url, nav):
    """在 URL 上追加 nav 查询参数（已有则覆盖）"""
    if not nav or nav not in ALLOWED_SIDEBAR_NAV:
        return url
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["nav"] = nav
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def nav_context(request):
    """供模板使用的 nav / nav_qs 上下文字典"""
    nav = get_sidebar_nav(request)
    return {
        "sidebar_nav": nav,
        "nav_qs": f"?nav={nav}" if nav else "",
        "nav_query_suffix": f"&nav={nav}" if nav else "",
    }
