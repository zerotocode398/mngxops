"""系统设置模块 - 视图"""
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import render
from django.views import View

from apps.users.permissions import PermissionRequiredMixin, user_has_permission
from .models import SystemSetting
from utils.setting_service import refresh_setting_cache


class SettingsIndexView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """系统设置页面（分组 Tab 展示）"""
    template_name = "settings/index.html"
    permission_resource = "settings"
    permission_action = "read"

    def get(self, request):
        settings_qs = SystemSetting.objects.all().order_by("group", "sort_order")
        can_update = user_has_permission(request.user, "settings", "update")

        # 配置项单位后缀映射
        unit_map = {
            "dashboard.recent_nodes_count": "条",
            "dashboard.recent_tasks_count": "条",
            "dashboard.recent_failed_bindings_count": "条",
            "node.batch_max_count": "台",
            "node.ssh_connect_timeout": "秒",
            "node.ssh_default_port": "",
            "node.detect_retries": "次",
            "credential.test_max_concurrency": "个",
            "config.discover_max_depth": "层",
            "config.default_nginx_path": "",
            "config.version_retention_days": "天",
            "config.sync_max_concurrency": "个",
            "config.sync_cache_timeout": "秒",
            "release.single_node_timeout": "秒",
            "release.max_parallel_tasks": "个",
            "release.backup_dir": "",
            "release.history_retention_days": "天",
            "audit.operation_log_retention_days": "天",
            "audit.login_log_retention_days": "天",
            "audit.login_max_fail_count": "次",
            "audit.login_lock_minutes": "分钟",
            "system.task_progress_poll_interval": "秒",
            "system.dashboard_refresh_interval": "秒",
            "task_center.retention_days": "天",
            "upgrade.max_parallel_compiles": "个",
            "upgrade.old_process_keep_minutes": "分钟",
            "upgrade.source_package_max_size_mb": "MB",
        }

        # 按分组整理
        groups = {}
        total_count = 0
        for s in settings_qs:
            if s.group not in groups:
                groups[s.group] = []
            s.help_unit = unit_map.get(s.key, "")
            groups[s.group].append(s)
            total_count += 1

        active_group = request.GET.get("group", list(groups.keys())[0] if groups else "")
        return render(request, self.template_name, {
            "groups": groups,
            "active_group": active_group,
            "can_update": can_update,
            "total_count": total_count,
        })


class SettingsSaveAPIView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """保存指定分组的配置 (Ajax)"""
    permission_resource = "settings"
    permission_action = "update"

    def post(self, request):
        group = request.POST.get("group", "")
        if not group:
            return JsonResponse({"success": False, "message": "缺少配置分组"})

        settings_qs = SystemSetting.objects.filter(group=group)
        saved = []
        for s in settings_qs:
            new_value = request.POST.get(s.key)
            if new_value is not None and new_value != s.value:
                s.value = new_value
                s.updated_by = request.user
                s.save(update_fields=["value", "updated_by", "updated_at"])
                refresh_setting_cache(s.key)
                saved.append(s.key)

        if saved:
            messages.success(request, f"已保存 {len(saved)} 项配置")
        else:
            messages.info(request, "配置未发生变化")

        return JsonResponse({"success": True, "saved": saved})


class SettingsGroupAPIView(LoginRequiredMixin, View):
    """获取指定分组的所有配置项"""

    def get(self, request):
        group = request.GET.get("group", "")
        settings_qs = SystemSetting.objects.filter(group=group).order_by("sort_order")
        data = [
            {
                "key": s.key,
                "value": s.value,
                "type": s.type,
                "label": s.label,
                "description": s.description,
                "placeholder": s.placeholder,
                "required": s.is_required,
            }
            for s in settings_qs
        ]
        return JsonResponse({"success": True, "settings": data})


class SettingsAllAPIView(LoginRequiredMixin, View):
    """获取所有配置项"""

    def get(self, request):
        settings_qs = SystemSetting.objects.all().order_by("group", "sort_order")
        data = {}
        for s in settings_qs:
            data[s.key] = s.value
        return JsonResponse({"success": True, "settings": data})