"""Nginx 启停：页面与异步执行 API"""
import json
import threading

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.urls import reverse
from django.views.generic import DetailView, ListView, TemplateView, View

from apps.nodes.models import Node
from apps.releases.models import TaskCenterTask
from apps.releases.task_progress import _format_current_steps
from apps.users.permissions import PermissionRequiredMixin, user_has_permission
from utils.pagination import PerPagePaginationMixin
from utils.search import split_search_tags
from utils.setting_service import get_recent_tasks_limit, get_setting

from .services import (
    ACTION_LABELS,
    _ACTION_MAP,
    _run_nginx_service_task,
    generate_service_batch_number,
)


def _batch_max_count():
    """读取批量操作最大节点数"""
    try:
        return max(1, int(get_setting("node.batch_max_count", "3") or 3))
    except (TypeError, ValueError):
        return 3


def _service_control_qs():
    """仅 Nginx 启停类任务中心记录"""
    return TaskCenterTask.objects.filter(operation_type="nginx_service_control")


def _target_node_labels(task):
    """将目标主机名与 IP 按下标配对为 hostname(ip) 列表"""
    names = [x.strip() for x in (task.target_hostnames or "").split(",") if x.strip()]
    ips = [x.strip() for x in (task.target_ips or "").split(",") if x.strip()]
    n = max(len(names), len(ips))
    labels = []
    for i in range(n):
        name = names[i] if i < len(names) else ""
        ip = ips[i] if i < len(ips) else ""
        if name and ip:
            labels.append(f"{name}({ip})")
        elif name or ip:
            labels.append(name or ip)
    return labels


def _log_display_text(task):
    """详情页日志：优先流水 log_output，历史任务回退结果树"""
    text = (task.log_output or "").strip()
    if text:
        return text
    return (task.result or "").strip()


def _service_progress_dict(task):
    """序列化启停任务进度与详情页链接"""
    finished = task.status in ("success", "failed", "cancelled")
    action = (task.target_configs or "").strip()
    return {
        "id": task.id,
        "task_center_id": task.id,
        "status": task.status,
        "status_display": task.get_status_display(),
        "progress": task.progress or 0,
        "detail": task.detail or "",
        "result": task.result or "",
        "log_output": _log_display_text(task),
        "current_steps": "" if finished else _format_current_steps(task.id),
        "log_url": reverse("nginx_service:task_log", kwargs={"pk": task.id}),
        "source_batch": task.source_batch or "",
        "action": action,
        "action_label": ACTION_LABELS.get(action, action or "-"),
        "finished": finished,
    }


class NginxServiceIndexView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    """Nginx 启停操作台页面"""

    template_name = "nginx_service/index.html"
    permission_resource = "nginx_service"
    permission_action = "read"

    def get_context_data(self, **kwargs):
        """注入批量上限、执行权限与最近启停任务"""
        context = super().get_context_data(**kwargs)
        context["batch_max_count"] = _batch_max_count()
        context["can_execute"] = user_has_permission(self.request.user, "nginx_service", "create")
        recent = list(
            TaskCenterTask.objects.filter(operation_type="nginx_service_control")
            .select_related("trigger_user")
            .order_by("-created_at")[: get_recent_tasks_limit()]
        )
        for task in recent:
            task.action_label = ACTION_LABELS.get(
                (task.target_configs or "").strip(), task.target_configs or "-"
            )
        context["recent_tasks"] = recent
        return context


class NginxServiceHistoryView(LoginRequiredMixin, PermissionRequiredMixin, PerPagePaginationMixin, ListView):
    """Nginx 启停历史（基于 TaskCenterTask）"""

    model = TaskCenterTask
    template_name = "nginx_service/history.html"
    context_object_name = "tasks"
    paginate_by = None
    ordering = ["-created_at"]
    permission_resource = "nginx_service"
    permission_action = "read"

    def get_queryset(self):
        """仅 nginx_service_control，支持搜索与状态筛选"""
        qs = TaskCenterTask.objects.filter(
            operation_type="nginx_service_control"
        ).select_related("trigger_user")
        search = (self.request.GET.get("search") or "").strip()
        if search:
            for term in split_search_tags(search):
                qs = qs.filter(
                    Q(target_hostnames__icontains=term)
                    | Q(target_ips__icontains=term)
                    | Q(target_configs__icontains=term)
                    | Q(detail__icontains=term)
                    | Q(source_batch__icontains=term)
                )
        status = (self.request.GET.get("status") or "").strip()
        if status == "running":
            qs = qs.exclude(status__in=["success", "failed", "cancelled"])
        elif status:
            qs = qs.filter(status=status)
        return qs.order_by("-created_at")

    def get_context_data(self, **kwargs):
        """注入分页、筛选与动作标签"""
        context = super().get_context_data(**kwargs)
        tasks = self.get_queryset()
        per_page = self.get_paginate_by(None)
        paginator = Paginator(list(tasks), per_page)
        page_obj = paginator.get_page(self.request.GET.get("page", 1))
        context["tasks"] = page_obj.object_list
        context["page_obj"] = page_obj
        context["is_paginated"] = page_obj.has_other_pages()
        context["search"] = (self.request.GET.get("search") or "").strip()
        context["status_filter"] = (self.request.GET.get("status") or "").strip()
        context["status_choices"] = [("running", "进行中")] + list(
            TaskCenterTask.STATUS_CHOICES
        )
        context["per_page"] = per_page
        context["per_page_options"] = self.per_page_options
        return context


class NginxServiceExecuteAPIView(LoginRequiredMixin, View):
    """异步执行 Nginx start/stop/reload/restart"""

    def post(self, request):
        """校验节点与动作后创建 TaskCenter 后台任务"""
        if not user_has_permission(request.user, "nginx_service", "create"):
            return JsonResponse({"success": False, "message": "无权限执行该操作"}, status=403)

        try:
            data = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return JsonResponse({"success": False, "message": "请求数据格式错误"})

        action = (data.get("action") or "").strip().lower()
        if action not in _ACTION_MAP:
            return JsonResponse(
                {"success": False, "message": "无效操作，仅支持 start/stop/reload/restart"}
            )

        node_ids = data.get("node_ids") or []
        if not node_ids:
            return JsonResponse({"success": False, "message": "未选择任何节点"})

        try:
            node_ids = [int(nid) for nid in node_ids]
        except (TypeError, ValueError):
            return JsonResponse({"success": False, "message": "节点 ID 无效"})

        max_batch = _batch_max_count()
        if len(node_ids) > max_batch:
            return JsonResponse(
                {"success": False, "message": f"最多只能操作 {max_batch} 个节点"}
            )

        nodes = list(
            Node.objects.filter(id__in=node_ids)
            .select_related("credential")
            .order_by("id")
        )
        if not nodes:
            return JsonResponse({"success": False, "message": "节点不存在"})

        eligible = []
        rejected = []
        for node in nodes:
            if node.is_locked:
                rejected.append(f"{node.hostname}（已锁定）")
                continue
            from apps.nodes.services import nginx_ops_gate_message

            gate_msg = nginx_ops_gate_message(node)
            if gate_msg:
                rejected.append(f"{node.hostname}（{gate_msg}）")
                continue
            cred = node.credential
            if not cred:
                rejected.append(f"{node.hostname}（未配置凭证）")
                continue
            if not cred.is_enabled:
                rejected.append(f"{node.hostname}（凭证已禁用）")
                continue
            eligible.append(node)

        if not eligible:
            msg = "没有可执行的节点"
            if rejected:
                msg += "：" + "；".join(rejected[:5])
            return JsonResponse({"success": False, "message": msg})

        action_label, _ = _ACTION_MAP[action]
        batch_number = generate_service_batch_number()
        task = TaskCenterTask.objects.create(
            operation_type="nginx_service_control",
            status="pending",
            detail=f"任务已创建，等待执行 Nginx {action_label}",
            target_hostnames=",".join(n.hostname for n in eligible),
            target_ips=",".join(n.ip for n in eligible),
            target_configs=action,
            source_batch=batch_number,
            trigger_user=request.user,
        )

        node_id_list = [n.id for n in eligible]
        thread = threading.Thread(
            target=_run_nginx_service_task,
            args=(task.id, node_id_list, action),
            daemon=True,
        )
        thread.start()

        message = f"已创建 Nginx {action_label} 任务（{len(eligible)} 台）"
        if rejected:
            message += f"；已跳过 {len(rejected)} 台"
        return JsonResponse(
            {
                "success": True,
                "async": True,
                "message": message,
                "task_center_id": task.id,
                "source_batch": batch_number,
                "skipped": rejected,
            }
        )


class NginxServiceTaskLogView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    """启停任务详情与完整执行日志页"""

    model = TaskCenterTask
    template_name = "nginx_service/task_log.html"
    context_object_name = "task"
    permission_resource = "nginx_service"
    permission_action = "read"

    def get_queryset(self):
        """限定启停任务并预加载操作人"""
        return _service_control_qs().select_related("trigger_user")

    def get_context_data(self, **kwargs):
        """注入动作标签、目标节点列表与日志展示文本"""
        context = super().get_context_data(**kwargs)
        action = (self.object.target_configs or "").strip()
        context["action_label"] = ACTION_LABELS.get(action, action or "-")
        context["log_output_display"] = _log_display_text(self.object)
        context["target_node_labels"] = _target_node_labels(self.object)
        return context


class NginxServiceTaskLogAPIView(LoginRequiredMixin, View):
    """返回单条启停任务日志（供详情页轮询）"""

    def get(self, request, pk):
        """读取启停任务状态与日志"""
        if not user_has_permission(request.user, "nginx_service", "read"):
            return JsonResponse({"success": False, "message": "无权限"}, status=403)
        try:
            task = _service_control_qs().get(pk=pk)
        except TaskCenterTask.DoesNotExist:
            return JsonResponse({"success": False, "message": "任务不存在"}, status=404)
        payload = _service_progress_dict(task)
        payload["success"] = True
        return JsonResponse(payload)


class NginxServiceBatchProgressAPIView(LoginRequiredMixin, View):
    """按任务 id 或批次号返回启停执行进度"""

    def get(self, request):
        """读取进行中或已完成的启停任务进度"""
        if not user_has_permission(request.user, "nginx_service", "read"):
            return JsonResponse({"success": False, "message": "无权限"}, status=403)
        task_id = (request.GET.get("task_id") or "").strip()
        batch = (request.GET.get("source_batch") or "").strip()
        qs = _service_control_qs()
        task = None
        if task_id:
            try:
                task = qs.get(pk=int(task_id))
            except (TypeError, ValueError, TaskCenterTask.DoesNotExist):
                task = None
        elif batch:
            task = qs.filter(source_batch=batch).order_by("-id").first()
        if not task:
            return JsonResponse({"success": False, "message": "任务不存在"}, status=404)
        payload = _service_progress_dict(task)
        payload["success"] = True
        return JsonResponse(payload)
