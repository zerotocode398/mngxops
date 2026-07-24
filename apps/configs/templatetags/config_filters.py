from django import template
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter
def dict_get(d, key):
    """从字典安全取值"""
    return d.get(key, "") if d else ""


@register.filter
def binding_sync_status_badge(status):
    """绑定同步状态徽标"""
    badges = {
        "not_synced": '<span class="badge bg-secondary"><i class="bi bi-plus-circle me-1"></i>未同步</span>',
        "synced": '<span class="badge bg-success"><i class="bi bi-check-circle me-1"></i>已同步</span>',
        "modified": '<span class="badge bg-primary"><i class="bi bi-pencil me-1"></i>待推送</span>',
        "conflict": '<span class="badge bg-warning text-dark"><i class="bi bi-exclamation-triangle me-1"></i>冲突</span>',
        "orphaned": '<span class="badge bg-danger"><i class="bi bi-trash me-1"></i>远程已删除</span>',
        "syncing": '<span class="badge bg-info"><i class="bi bi-arrow-repeat me-1"></i>同步中</span>',
        "failed": '<span class="badge bg-danger"><i class="bi bi-x-circle me-1"></i>同步失败</span>',
    }
    html = badges.get(status, f'<span class="badge bg-light text-dark">{status}</span>')
    return mark_safe(html)


@register.filter
def binding_source_badge(source):
    """绑定来源徽标"""
    if source == "discovered":
        return mark_safe('<span class="badge bg-info"><i class="bi bi-search me-1"></i>远程发现</span>')
    return mark_safe('<span class="badge bg-light text-dark"><i class="bi bi-hand-index me-1"></i>手动绑定</span>')


@register.simple_tag(takes_context=True)
def pagination_url(context, page_number):
    """生成分页链接，保留当前 GET 查询参数。"""
    request = context.get("request")
    if not request:
        return f"?page={page_number}"
    query = request.GET.copy()
    query["page"] = page_number
    return "?" + query.urlencode()
