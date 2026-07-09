from django import template

register = template.Library()


@register.filter
def dict_get(d, key):
    return d.get(key, "") if d else ""


@register.filter
def nodes_hostnames(config):
    nodes = config.nodes.all()
    return ",".join(n.hostname for n in nodes) if nodes else "未关联节点"


@register.filter
def nodes_ips(config):
    nodes = config.nodes.all()
    return ",".join(n.ip for n in nodes) if nodes else ""


@register.filter
def any_node_locked(config):
    return any(n.is_locked for n in config.nodes.all())
