"""Nginx 启停：页面与异步执行 API"""
import json
import logging
import threading

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.generic import ListView, TemplateView, View

from apps.nodes.models import Node
from apps.releases.models import TaskCenterTask
from apps.releases.task_cancel import finish_if_active, is_cancelled, update_if_active
from apps.releases.task_result import (
    build_tree_result,
    item_failed,
    item_success,
    node_header,
)
from apps.users.permissions import PermissionRequiredMixin, user_has_permission
from utils.nginx_ops import reload_nginx, restart_nginx, start_nginx, stop_nginx
from utils.pagination import PerPagePaginationMixin
from utils.setting_service import get_setting

logger = logging.getLogger(__name__)

# 支持的服务动作
_ACTION_MAP = {
    "start": ("启动", start_nginx),
    "stop": ("停止", stop_nginx),
    "reload": ("重载", reload_nginx),
    "restart": ("重启", restart_nginx),
}

def _batch_max_count():
    """读取批量操作最大节点数"""
    try:
        return max(1, int(get_setting("node.batch_max_count", "3") or 3))
    except (TypeError, ValueError):
        return 3


def _auth_kwargs(credential):
    """按凭证类型组装 nginx_ops 认证参数"""
    if credential.auth_type == "password":
        return {"password": credential.get_password()}
    return {"private_key": credential.get_private_key()}


class NginxServiceIndexView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    """Nginx 启停操作台页面"""

    template_name = "nginx_service/index.html"
    permission_resource = "nodes"
    permission_action = "read"

    def get_context_data(self, **kwargs):
        """注入批量上限与执行权限"""
        context = super().get_context_data(**kwargs)
        context["batch_max_count"] = _batch_max_count()
        context["can_execute"] = user_has_permission(self.request.user, "nodes", "update")
        return context


class NginxServiceHistoryView(LoginRequiredMixin, PermissionRequiredMixin, PerPagePaginationMixin, ListView):
    """Nginx 启停历史（基于 TaskCenterTask）"""

    model = TaskCenterTask
    template_name = "nginx_service/history.html"
    context_object_name = "tasks"
    paginate_by = None
    ordering = ["-created_at"]
    permission_resource = "nodes"
    permission_action = "read"

    def get_queryset(self):
        """仅 nginx_service_control，支持搜索与状态筛选"""
        qs = TaskCenterTask.objects.filter(
            operation_type="nginx_service_control"
        ).select_related("trigger_user")
        search = (self.request.GET.get("search") or "").strip()
        if search:
            qs = qs.filter(
                Q(target_hostnames__icontains=search)
                | Q(target_ips__icontains=search)
                | Q(target_configs__icontains=search)
                | Q(detail__icontains=search)
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
        if not user_has_permission(request.user, "nodes", "update"):
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
            if node.status != "online":
                rejected.append(f"{node.hostname}（非在线）")
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
        task = TaskCenterTask.objects.create(
            operation_type="nginx_service_control",
            status="pending",
            detail=f"任务已创建，等待执行 Nginx {action_label}",
            target_hostnames=",".join(n.hostname for n in eligible),
            target_ips=",".join(n.ip for n in eligible),
            target_configs=action,
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
                "skipped": rejected,
            }
        )


def _run_nginx_service_task(task_id, node_ids, action):
    """后台串行逐节点执行启停，刷活进度步骤与结果树"""
    from apps.releases.views import _clear_release_progress_state, _set_current_step

    action_label, action_fn = _ACTION_MAP[action]
    TaskCenterTask.objects.filter(pk=task_id).update(
        status="running",
        progress=5,
        detail=f"正在执行 Nginx {action_label}...",
        started_at=timezone.now(),
    )

    nodes = list(
        Node.objects.filter(id__in=node_ids)
        .select_related("credential")
        .order_by("id")
    )
    total = len(nodes)
    success_count = 0
    fail_count = 0
    done = 0
    node_blocks = []
    item_label = f"Nginx {action_label}"

    try:
        for node in nodes:
            if is_cancelled(task_id):
                return

            hostname = node.hostname or node.ip
            _set_current_step(task_id, hostname, item_label)
            node_blocks.append(node_header(node.ip, node.hostname))
            try:
                cred = node.credential
                if not cred or not cred.is_enabled:
                    fail_count += 1
                    node_blocks.append(item_failed(item_label, "凭证不可用"))
                else:
                    ok, msg = action_fn(
                        node.ip,
                        node.port,
                        cred.username,
                        nginx_path=node.nginx_path or None,
                        **_auth_kwargs(cred),
                    )
                    if ok:
                        success_count += 1
                        node_blocks.append(item_success(item_label))
                    else:
                        fail_count += 1
                        node_blocks.append(item_failed(item_label, msg or "执行失败"))
            except Exception as exc:
                logger.exception("Nginx %s 失败 node=%s", action, node.id)
                fail_count += 1
                node_blocks.append(item_failed(item_label, str(exc)))

            done += 1
            _set_current_step(task_id, hostname, None)
            # 刷入已完成节点的活树，供进度遮罩动态展示
            update_if_active(
                task_id,
                progress=int(done * 100 / total) if total else 100,
                detail=(
                    f"执行中：成功 {success_count}，失败 {fail_count}，"
                    f"已完成 {done}/{total}"
                ),
                result="\n".join(node_blocks),
            )

        if is_cancelled(task_id):
            return

        status = "success" if fail_count == 0 else "failed"
        finish_if_active(
            task_id,
            status=status,
            progress=100,
            finished_at=timezone.now(),
            detail=f"执行完成：成功 {success_count}，失败 {fail_count}，共 {total}",
            result=build_tree_result(success_count, fail_count, total, node_blocks),
        )
    finally:
        _clear_release_progress_state(task_id)
