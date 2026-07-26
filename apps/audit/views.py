from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.urls import reverse
from django.views.generic import ListView

from apps.users.permissions import PermissionRequiredMixin
from .models import AuditLog, LoginLog
from utils.pagination import PerPagePaginationMixin


# 模块名 → 业务列表 URL name（无任务中心 ID 时的软链）
MODULE_LINK_MAP = {
    "凭证管理": "credentials:list",
    "节点管理": "nodes:list",
    "节点分组": "nodes:group_list",
    "配置管理": "configs:list",
    "配置绑定": "configs:list",
    "绑定版本": "configs:list",
    "配置版本": "configs:list",
    "发布管理": "releases:center",
    "发布任务": "releases:list",
    "发布历史": "releases:list",
    "Nginx升级": "upgrade:list",
    "Nginx 升级任务": "upgrade:list",
    "Nginx 源码包": "upgrade:package_list",
    "用户管理": "users:list",
    "角色管理": "users:group_list",
    "用户组管理": "users:team_list",
    "系统设置": "settings:index",
    "任务中心": "releases:history",
    "登录管理": "audit:login_list",
}


def _split_search_tags(search):
    """将逗号分隔的搜索标签拆成非空列表。"""
    if not search:
        return []
    return [t.strip() for t in search.replace("，", ",").split(",") if t.strip()]


def resolve_audit_module_link(log):
    """解析操作日志模块列跳转 URL：优先任务中心，否则业务列表。"""
    if getattr(log, "task_center_id", None):
        return reverse(
            "releases:task_center_detail", kwargs={"pk": log.task_center_id}
        )
    url_name = MODULE_LINK_MAP.get(log.module)
    if not url_name:
        return None
    try:
        return reverse(url_name)
    except Exception:
        return None


class AuditLogListView(
    LoginRequiredMixin, PermissionRequiredMixin, PerPagePaginationMixin, ListView
):
    model = AuditLog
    template_name = "audit/list.html"
    context_object_name = "logs"
    paginate_by = 10
    ordering = ["-created_at"]
    permission_resource = "audit"
    permission_action = "read"

    def get_queryset(self):
        queryset = super().get_queryset().select_related("user")
        search = self.request.GET.get("search", "")
        module_filter = self.request.GET.get("module", "")
        result_filter = self.request.GET.get("result", "")
        date_from = self.request.GET.get("date_from", "")
        date_to = self.request.GET.get("date_to", "")

        # 多标签 AND：每词匹配用户名 / 动作 / 详情
        for term in _split_search_tags(search):
            queryset = queryset.filter(
                Q(user__username__icontains=term)
                | Q(action__icontains=term)
                | Q(detail__icontains=term)
            )
        if module_filter:
            queryset = queryset.filter(module=module_filter)
        if result_filter in ("success", "failed"):
            queryset = queryset.filter(result=result_filter)
        if date_from:
            queryset = queryset.filter(created_at__gte=date_from)
        if date_to:
            queryset = queryset.filter(created_at__lte=date_to + " 23:59:59")

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["search"] = self.request.GET.get("search", "")
        context["module_filter"] = self.request.GET.get("module", "")
        context["result_filter"] = self.request.GET.get("result", "")
        context["date_from"] = self.request.GET.get("date_from", "")
        context["date_to"] = self.request.GET.get("date_to", "")
        context["modules"] = sorted(
            set(AuditLog.objects.values_list("module", flat=True).distinct())
        )
        context["has_filters"] = any([
            context["search"],
            context["module_filter"],
            context["result_filter"],
            context["date_from"],
            context["date_to"],
        ])
        # 为当前页每条日志解析模块软链
        for log in context["logs"]:
            log.module_link = resolve_audit_module_link(log)
        return context


class LoginLogListView(
    LoginRequiredMixin, PermissionRequiredMixin, PerPagePaginationMixin, ListView
):
    model = LoginLog
    template_name = "audit/login_list.html"
    context_object_name = "logs"
    paginate_by = 10
    ordering = ["-created_at"]
    permission_resource = "audit"
    permission_action = "read"

    def get_queryset(self):
        queryset = super().get_queryset()
        search = self.request.GET.get("search", "")
        status_filter = self.request.GET.get("status", "")
        date_from = self.request.GET.get("date_from", "")
        date_to = self.request.GET.get("date_to", "")

        for term in _split_search_tags(search):
            queryset = queryset.filter(
                Q(username__icontains=term) | Q(ip__icontains=term)
            )
        if status_filter in ("success", "failed"):
            queryset = queryset.filter(status=status_filter)
        if date_from:
            queryset = queryset.filter(created_at__gte=date_from)
        if date_to:
            queryset = queryset.filter(created_at__lte=date_to + " 23:59:59")

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["search"] = self.request.GET.get("search", "")
        context["status_filter"] = self.request.GET.get("status", "")
        context["date_from"] = self.request.GET.get("date_from", "")
        context["date_to"] = self.request.GET.get("date_to", "")
        context["has_filters"] = any([
            context["search"],
            context["status_filter"],
            context["date_from"],
            context["date_to"],
        ])
        return context
