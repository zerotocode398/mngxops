"""升级模块模板过滤器"""
import re
from django import template

register = template.Library()


@register.filter
def nginx_ver(value):
    """展示用：去掉 nginx/ 或 nginx- 前缀，仅保留版本号"""
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    # 去掉常见前缀 nginx/、nginx-
    text = re.sub(r"(?i)^nginx[/\\-]", "", text)
    return text
