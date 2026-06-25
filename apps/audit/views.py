from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.views.generic import ListView

from apps.users.permissions import PermissionRequiredMixin
from .models import AuditLog, LoginLog
from utils.pagination import PerPagePaginationMixin


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
        date_from = self.request.GET.get("date_from", "")
        date_to = self.request.GET.get("date_to", "")

        if search:
            queryset = queryset.filter(
                Q(user__username__icontains=search) | Q(action__icontains=search)
            )
        if module_filter:
            queryset = queryset.filter(module=module_filter)
        if date_from:
            queryset = queryset.filter(created_at__gte=date_from)
        if date_to:
            queryset = queryset.filter(created_at__lte=date_to + " 23:59:59")

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["search"] = self.request.GET.get("search", "")
        context["module_filter"] = self.request.GET.get("module", "")
        context["date_from"] = self.request.GET.get("date_from", "")
        context["date_to"] = self.request.GET.get("date_to", "")
        context["modules"] = sorted(
            set(AuditLog.objects.values_list("module", flat=True).distinct())
        )
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
        date_from = self.request.GET.get("date_from", "")
        date_to = self.request.GET.get("date_to", "")

        if search:
            queryset = queryset.filter(
                Q(username__icontains=search) | Q(ip__icontains=search)
            )
        if date_from:
            queryset = queryset.filter(created_at__gte=date_from)
        if date_to:
            queryset = queryset.filter(created_at__lte=date_to + " 23:59:59")

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["search"] = self.request.GET.get("search", "")
        context["date_from"] = self.request.GET.get("date_from", "")
        context["date_to"] = self.request.GET.get("date_to", "")
        return context
