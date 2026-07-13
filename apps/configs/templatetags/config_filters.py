from django import template

register = template.Library()


@register.filter
def dict_get(d, key):
    return d.get(key, "") if d else ""


@register.filter
def binding_sync_status_badge(status):
    """绑定同步状态徽标"""
    badges = {
        "not_synced": ('<span class="badge bg-secondary">🆕 未同步</span>', "not_synced"),
        "synced": ('<span class="badge bg-success">✅ 已同步</span>', "synced"),
        "modified": ('<span class="badge bg-primary">📝 待推送</span>', "modified"),
        "conflict": ('<span class="badge bg-warning text-dark">⚠️ 冲突</span>', "conflict"),
        "orphaned": ('<span class="badge bg-danger">📭 远程已删除</span>', "orphaned"),
        "syncing": ('<span class="badge bg-info">🔄 同步中</span>', "syncing"),
        "failed": ('<span class="badge bg-danger">❌ 同步失败</span>', "failed"),
    }
    return badges.get(status, (f'<span class="badge bg-light text-dark">{status}</span>', "unknown"))


@register.filter
def binding_source_badge(source):
    """绑定来源徽标"""
    if source == "discovered":
        return '<span class="badge bg-info">🔍 远程发现</span>'
    return '<span class="badge bg-light text-dark">✋ 手动绑定</span>'