"""系统设置模块 - 视图"""
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import render
from django.views import View

from apps.users.permissions import PermissionRequiredMixin, user_has_permission
from .models import SystemSetting, preset_key_set
from utils.setting_service import refresh_setting_cache

# 分组导航图标与说明文案（仅保留已接线分组）
GROUP_META = {
    "仪表盘": {
        "icon": "bi-bar-chart",
        "description": "首页最近任务与失败绑定等展示条数。",
    },
    "节点管理": {
        "icon": "bi-plug",
        "description": "SSH 超时、默认端口、批量与探测相关参数。",
    },
    "凭证管理": {
        "icon": "bi-key",
        "description": "凭证启用测试时的并发上限。",
    },
    "配置管理": {
        "icon": "bi-pencil",
        "description": "发现深度、默认主配置路径与同步并发。",
    },
    "发布管理": {
        "icon": "bi-rocket-takeoff",
        "description": "并行任务数与远程备份路径。",
    },
    "系统": {
        "icon": "bi-display",
        "description": "任务进度轮询、仪表盘刷新间隔与历史数据保留天数。",
    },
    "Nginx升级": {
        "icon": "bi-box-seam",
        "description": "默认工作目录、并行编译核数与源码包大小限制。",
    },
}

# 配置项单位后缀（与 PRESET_SETTINGS 键一致）
UNIT_MAP = {
    "dashboard.recent_tasks_count": "条",
    "node.batch_max_count": "台",
    "node.ssh_connect_timeout": "秒",
    "node.ssh_default_port": "",
    "node.detect_retries": "次",
    "credential.test_max_concurrency": "个",
    "config.discover_max_depth": "层",
    "config.default_nginx_path": "",
    "config.default_nginx_bin": "",
    "config.sync_max_concurrency": "个",
    "release.max_parallel_tasks": "个",
    "release.backup_dir": "",
    "system.task_progress_poll_interval": "秒",
    "system.dashboard_refresh_interval": "秒",
    "system.retention_task_center_days": "天",
    "system.retention_release_history_days": "天",
    "system.retention_audit_log_days": "天",
    "system.retention_login_log_days": "天",
    "upgrade.default_work_dir": "",
    "upgrade.make_jobs_default": "核",
    "upgrade.package_max_size_mb": "MB",
}


def _active_settings_qs(extra_filter=None):
    """仅返回仍在 PRESET 中的配置项"""
    qs = SystemSetting.objects.filter(key__in=preset_key_set())
    if extra_filter:
        qs = qs.filter(**extra_filter)
    return qs.order_by("group", "sort_order")


class SettingsIndexView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """系统设置页面（左侧分组导航 + 右侧分区表单）"""
    template_name = "settings/index.html"
    permission_resource = "settings"
    permission_action = "read"

    def get(self, request):
        """渲染分组设置页"""
        settings_qs = _active_settings_qs()
        can_update = user_has_permission(request.user, "settings", "update")

        # 按分组整理为有序列表（含图标与说明，便于模板渲染）
        grouped = {}
        group_order = []
        total_count = 0
        for s in settings_qs:
            if s.group not in grouped:
                meta = GROUP_META.get(s.group, {
                    "icon": "bi-gear",
                    "description": "调整本模块相关运行参数，保存后立即生效。",
                })
                grouped[s.group] = {
                    "name": s.group,
                    "items": [],
                    "icon": meta["icon"],
                    "description": meta.get("description", ""),
                }
                group_order.append(s.group)
            s.help_unit = UNIT_MAP.get(s.key, "")
            grouped[s.group]["items"].append(s)
            total_count += 1

        group_list = [grouped[name] for name in group_order]

        default_group = group_list[0]["name"] if group_list else ""
        active_group = request.GET.get("group", default_group)
        if active_group not in grouped:
            active_group = default_group

        return render(request, self.template_name, {
            "group_list": group_list,
            "active_group": active_group,
            "can_update": can_update,
            "total_count": total_count,
        })


class SettingsSaveAPIView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """保存指定分组的配置 (Ajax)"""
    permission_resource = "settings"
    permission_action = "update"

    def post(self, request):
        """按分组更新已变更的配置项"""
        group = request.POST.get("group", "")
        if not group:
            return JsonResponse({"success": False, "message": "缺少配置分组"})

        settings_qs = _active_settings_qs({"group": group})
        saved = []
        for s in settings_qs:
            # boolean 未勾选时 POST 无键，按 false 处理
            if s.type == "boolean":
                new_value = "true" if request.POST.get(s.key) == "true" else "false"
            else:
                new_value = request.POST.get(s.key)
            if new_value is None:
                continue
            if s.type == "integer":
                text = str(new_value).strip()
                if text == "":
                    return JsonResponse({
                        "success": False,
                        "message": f"「{s.label}」不能为空",
                    })
                try:
                    int(text)
                except (TypeError, ValueError):
                    return JsonResponse({
                        "success": False,
                        "message": f"「{s.label}」必须是整数",
                    })
                new_value = text
            if new_value != s.value:
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
        """返回指定分组配置的 JSON 列表"""
        group = request.GET.get("group", "")
        settings_qs = _active_settings_qs({"group": group})
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
        """返回全部配置的扁平 key→value 映射"""
        settings_qs = _active_settings_qs()
        data = {}
        for s in settings_qs:
            data[s.key] = s.value
        return JsonResponse({"success": True, "settings": data})
