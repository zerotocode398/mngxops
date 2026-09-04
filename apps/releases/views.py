import json
import re
import logging
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.db import OperationalError
from django.core.paginator import Paginator
from django.db.models import Max, Q
from django.utils import timezone
from django.http import JsonResponse
from django.shortcuts import redirect, get_object_or_404, render
from django.urls import reverse
from django.views.generic import ListView, DetailView, View

from apps.nodes.models import Node
from apps.configs.models import ConfigNodeBinding, BindingVersion
from apps.users.permissions import (
    PermissionRequiredMixin,
    user_has_permission,
    forbidden_response,
    task_center_limited_ops_for_user,
    user_can_access_limited_task_center,
)

from .models import ReleaseTask, TaskCenterTask, generate_batch_number
from .task_cancel import (
    close_registered_ssh,
    mark_cancelled,
)
from .task_progress import (
    _clear_release_progress_state,
    _format_current_steps,
)
from .services import (
    _start_release_executor,
)
from utils.pagination import PerPagePaginationMixin

logger = logging.getLogger(__name__)


class ReleaseCreateAPIView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """发布任务创建 API — 处理 JSON 格式的发布任务创建请求"""

    permission_resource = "releases"
    permission_action = "create"

    def post(self, request):
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse(
                {"success": False, "message": "请求数据格式错误"}, status=400
            )

        bindings_data = data.get("bindings", [])
        auto_execute = data.get("auto_execute", False)

        if not bindings_data:
            return JsonResponse(
                {"success": False, "message": "请至少选择一个配置绑定"}, status=400
            )

        from utils.setting_service import get_setting

        try:
            batch_max = max(1, int(get_setting("node.batch_max_count", "3") or 3))
        except (TypeError, ValueError):
            batch_max = 3

        # 预检唯一节点数是否超限（跳过无法解析的 binding）
        preview_node_ids = set()
        for item in bindings_data:
            binding_id = item.get("binding_id", 0)
            try:
                binding = ConfigNodeBinding.objects.select_related("node").get(
                    pk=binding_id
                )
            except ConfigNodeBinding.DoesNotExist:
                continue
            if binding.node.is_locked or binding.node.is_deleted:
                continue
            from apps.nodes.services import nginx_ops_gate_message

            if nginx_ops_gate_message(binding.node):
                continue
            preview_node_ids.add(binding.node_id)
        if len(preview_node_ids) > batch_max:
            return JsonResponse(
                {"success": False, "message": f"最多只能勾选 {batch_max} 个节点"},
                status=400,
            )

        batch_number = generate_batch_number()
        task_ids = []
        skipped_offline = False

        for item in bindings_data:
            binding_id = item.get("binding_id", 0)
            version = item.get("version")

            try:
                binding = ConfigNodeBinding.objects.select_related(
                    "node", "config"
                ).get(pk=binding_id)
            except ConfigNodeBinding.DoesNotExist:
                continue

            if binding.node.is_locked or binding.node.is_deleted:
                continue
            from apps.nodes.services import nginx_ops_gate_message

            if nginx_ops_gate_message(binding.node):
                skipped_offline = True
                continue

            publish_version = version if version else binding.current_version

            task = ReleaseTask.objects.create(
                batch_number=batch_number,
                binding=binding,
                config=binding.config,
                node=binding.node,
                version=binding.versions.filter(version=publish_version).first(),
                publish_version=publish_version,
                remote_path=binding.remote_path,
                operator=request.user,
                status="pending",
            )
            task_ids.append(task.id)

        if not task_ids:
            msg = (
                "所选配置绑定均不可发布（含非在线、未检测到 Nginx 或已锁定/已删除节点）"
                if skipped_offline
                else "未找到可发布的配置绑定"
            )
            return JsonResponse({"success": False, "message": msg}, status=400)

        response_data = {
            "success": True,
            "batch_number": batch_number,
            "task_count": len(task_ids),
            "message": f"发布任务已创建，批次号: {batch_number}，共 {len(task_ids)} 个任务",
        }

        if auto_execute:
            from apps.releases.task_result import targets_from_release_tasks

            targets = targets_from_release_tasks(task_ids)
            task_center = TaskCenterTask.objects.create(
                operation_type="release_publish",
                status="running",
                source_batch=batch_number,
                detail=f"执行中：成功 0，失败 0，共 {len(task_ids)}",
                progress=0,
                started_at=timezone.now(),
                trigger_user=request.user,
                **targets,
            )

            _start_release_executor(task_ids, task_center.id)

            response_data["task_center_id"] = task_center.id
            response_data["async"] = True

        return JsonResponse(response_data)


class ReleaseListView(
    LoginRequiredMixin, PermissionRequiredMixin, PerPagePaginationMixin, ListView
):
    """发布历史 - 按批次内节点分页，批次→节点→配置树形展示"""

    model = ReleaseTask
    template_name = "releases/list.html"
    context_object_name = "tasks"
    paginate_by = 10
    ordering = ["-created_at"]
    permission_resource = "releases"
    permission_action = "read"

    def get_queryset(self):
        """按搜索条件过滤发布任务"""
        queryset = (
            super()
            .get_queryset()
            .select_related("node", "config", "binding", "operator")
            .prefetch_related("node__groups", "binding__versions")
        )
        search = self.request.GET.get("search", "")
        status_filter = self.request.GET.get("status", "")
        batch = self.request.GET.get("batch", "")
        node_ip = self.request.GET.get("node_ip", "")
        if search:
            terms = [t.strip() for t in search.split(",") if t.strip()]
            for term in terms:
                queryset = queryset.filter(
                    Q(config__name__icontains=term)
                    | Q(node__hostname__icontains=term)
                    | Q(batch_number__icontains=term)
                    | Q(operator__username__icontains=term)
                )
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        if batch:
            queryset = queryset.filter(batch_number__icontains=batch)
        if node_ip:
            queryset = queryset.filter(node__ip__icontains=node_ip)
        return queryset

    def paginate_queryset(self, queryset, page_size):
        """按 batch_number 批次分页，再加载本页全部配置任务"""
        batches = list(
            queryset.values("batch_number")
            .annotate(latest=Max("created_at"))
            .order_by("-latest")
        )
        paginator = Paginator(batches, page_size)
        page_number = self.request.GET.get("page") or 1
        page = paginator.get_page(page_number)
        self._page_batches = [str(b["batch_number"] or "") for b in page.object_list]

        if not self._page_batches:
            return (paginator, page, [], page.has_other_pages())

        tasks = list(
            queryset.filter(batch_number__in=self._page_batches)
            .select_related("node", "config", "binding", "operator")
            .prefetch_related("node__groups")
            .order_by("-created_at")
        )
        return (paginator, page, tasks, page.has_other_pages())

    def get_context_data(self, **kwargs):
        """组装本页批次→节点→任务树，供统一表格渲染"""
        from collections import OrderedDict

        context = super().get_context_data(**kwargs)
        search = self.request.GET.get("search", "")
        status_filter = self.request.GET.get("status", "")
        batch = self.request.GET.get("batch", "")
        node_ip = self.request.GET.get("node_ip", "")
        context["search"] = search
        context["status_filter"] = status_filter
        context["batch_filter"] = batch
        context["node_ip_filter"] = node_ip
        context["status_choices"] = ReleaseTask.STATUS_CHOICES

        # 先按本页批次顺序建空组，再填入任务，保证分页顺序
        batch_groups = OrderedDict()
        for batch_key in getattr(self, "_page_batches", []):
            batch_groups[batch_key] = {
                "batch_number": batch_key,
                "created_at": None,
                "operator": "-",
                "total": 0,
                "success": 0,
                "failed": 0,
                "other": 0,
                "nodes": OrderedDict(),
            }

        for task in context["tasks"]:
            batch_key = str(task.batch_number or "")
            if batch_key not in batch_groups:
                continue
            batch_data = batch_groups[batch_key]
            if batch_data["created_at"] is None:
                batch_data["created_at"] = task.created_at
                batch_data["operator"] = (
                    task.operator.username if task.operator else "-"
                )
            node_id = int(task.node_id)
            if node_id not in batch_data["nodes"]:
                batch_data["nodes"][node_id] = {
                    "node": task.node,
                    "tasks": [],
                }
            batch_data["nodes"][node_id]["tasks"].append(task)
            batch_data["total"] += 1
            if task.status == "success":
                batch_data["success"] += 1
            elif task.status == "failed":
                batch_data["failed"] += 1
            else:
                batch_data["other"] += 1

        # 去掉本页无任务的空批次（异常兜底）
        context["batch_groups"] = OrderedDict(
            (k, v) for k, v in batch_groups.items() if v["total"] > 0
        )
        context["expand_all_nodes"] = bool(search or status_filter or batch or node_ip)
        context["has_any_filter"] = bool(
            search
            or status_filter
            or context["batch_filter"]
            or context["node_ip_filter"]
        )
        # 本页各任务可回滚目标版本（publish_version 的上一版），供明细弹窗展示
        for task in context["tasks"]:
            prev_ver = None
            if task.binding_id and task.publish_version is not None:
                for ver in task.binding.versions.all():
                    if ver.version < task.publish_version:
                        if prev_ver is None or ver.version > prev_ver:
                            prev_ver = ver.version
            task.rollback_target_version = prev_ver
        return context


class TaskCenterListView(LoginRequiredMixin, PerPagePaginationMixin, ListView):
    model = TaskCenterTask
    template_name = "releases/task_center.html"
    context_object_name = "tasks"
    paginate_by = 15
    ordering = ["-created_at"]

    def dispatch(self, request, *args, **kwargs):
        self.can_read_release_tasks = user_has_permission(
            request.user, "releases", "read"
        )
        self.can_read_node_tasks = user_can_access_limited_task_center(request.user)
        if not (self.can_read_release_tasks or self.can_read_node_tasks):
            return forbidden_response(request, "当前账号无权限访问该功能")
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        queryset = super().get_queryset().select_related("trigger_user")
        # 无发布读权限时：仅本人触发的节点测/同步/运维任务
        if not self.can_read_release_tasks:
            allowed = task_center_limited_ops_for_user(self.request.user)
            queryset = queryset.filter(
                operation_type__in=allowed or ["__none__"],
                trigger_user=self.request.user,
            )
        search = self.request.GET.get("search", "")
        status_filter = self.request.GET.get("status", "")
        operation_type = self.request.GET.get("operation_type", "")
        if search:
            tags = [
                t.strip() for t in search.replace("，", ",").split(",") if t.strip()
            ]
            for tag in tags:
                queryset = queryset.filter(
                    Q(source_batch__icontains=tag)
                    | Q(target_hostnames__icontains=tag)
                    | Q(target_ips__icontains=tag)
                )
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        if operation_type:
            queryset = queryset.filter(operation_type=operation_type)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from apps.releases.task_result import format_task_center_summary

        # 为列表行注入格式化摘要（目标 + 结果）
        for task in context.get("tasks") or []:
            primary, secondary = format_task_center_summary(task)
            task.summary_primary = primary
            task.summary_secondary = secondary

        context["search"] = self.request.GET.get("search", "")
        context["status_filter"] = self.request.GET.get("status", "")
        context["operation_type_filter"] = self.request.GET.get("operation_type", "")
        context["status_choices"] = TaskCenterTask.STATUS_CHOICES
        # 筛选下拉仅展示实际会创建的任务类型（不含未启用的 discover/drift/glob）
        context["operation_type_choices"] = [
            c
            for c in TaskCenterTask.OPERATION_TYPE_CHOICES
            if c[0]
            not in ("config_discover", "config_drift_check", "config_glob_preview")
        ]
        context["has_any_filter"] = bool(
            context["search"]
            or context["status_filter"]
            or context["operation_type_filter"]
        )
        return context


class TaskCenterDetailView(LoginRequiredMixin, DetailView):
    """任务中心详情 - 按节点→配置树形展示执行结果"""

    model = TaskCenterTask
    template_name = "releases/task_detail.html"
    context_object_name = "task"

    def dispatch(self, request, *args, **kwargs):
        self.can_read_release_tasks = user_has_permission(
            request.user, "releases", "read"
        )
        self.can_read_node_tasks = user_can_access_limited_task_center(request.user)
        if not (self.can_read_release_tasks or self.can_read_node_tasks):
            return forbidden_response(request, "当前账号无权限访问该功能")
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.can_read_release_tasks:
            return queryset
        allowed = task_center_limited_ops_for_user(self.request.user)
        return queryset.filter(
            operation_type__in=allowed or ["__none__"],
            trigger_user=self.request.user,
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        task = self.object
        result_text = (task.result or "").strip()
        op = task.operation_type
        is_release_type = op in ("release_publish", "release_rollback")

        # 解析目标节点和目标配置/凭证
        target_nodes = []
        target_configs = []
        if task.target_hostnames:
            ips = (task.target_ips or "").split(",")
            hostnames = task.target_hostnames.split(",")
            seen = set()
            for i, hn_raw in enumerate(hostnames):
                hn = hn_raw.strip()
                if not hn or hn in seen:
                    continue
                seen.add(hn)
                ip = ips[i].strip() if i < len(ips) else ""
                target_nodes.append(f"{hn}({ip})" if ip else hn)
        if task.target_configs:
            configs_raw = task.target_configs.split(",")
            target_configs = [c.strip() for c in configs_raw if c.strip()]
            seen_c = set()
            target_configs = [
                c for c in target_configs if not (c in seen_c or seen_c.add(c))
            ]

        context["target_nodes"] = target_nodes[:50]
        context["target_configs"] = target_configs[:50]
        context["target_configs_count"] = len(target_configs)
        context["is_release_type"] = is_release_type
        # 配置同步详情默认展开结果树，便于查看新建/更新/删除/跳过明细
        context["is_config_sync_type"] = op == "config_batch_sync"
        context["target_configs_label"] = (
            "目标凭证" if op == "credential_enable_test" else "目标配置"
        )

        # 解析结果树（按节点分组 + 成功/失败明细）
        from apps.releases.task_result import (
            split_error_reason_lines,
            split_failed_item,
        )

        result_tree = []
        success_total = 0
        failed_total = 0

        if result_text:
            current_node = None
            for raw in result_text.splitlines():
                stripped = raw.strip()
                if not stripped:
                    continue
                if stripped.startswith("[节点] "):
                    node_text = stripped[len("[节点] ") :]
                    if current_node:
                        result_tree.append(current_node)
                    current_node = {
                        "name": node_text,
                        "configs": [],
                        "success": 0,
                        "failed": 0,
                    }
                elif raw.startswith("  [成功]") and current_node is not None:
                    raw_name = raw[len("  [成功] ") :].strip()
                    name = re.sub(r'\s+v(\d+).*', r' (V\1)', raw_name)
                    search_name = re.sub(r'\s+v\d+.*', '', raw_name).strip()
                    current_node["configs"].append(
                        {
                            "name": name,
                            "search_name": search_name,
                            "status": "success",
                        }
                    )
                    current_node["success"] += 1
                    success_total += 1
                elif raw.startswith("  [失败]") and current_node is not None:
                    raw_name = raw[len("  [失败] ") :].strip()
                    label, reason = split_failed_item(raw_name)
                    name = re.sub(r'\s+v(\d+).*', r' (V\1)', label)
                    search_name = re.sub(r'\s+v\d+.*', '', label).strip()
                    current_node["configs"].append(
                        {
                            "name": name,
                            "search_name": search_name,
                            "status": "failed",
                            "reason_lines": split_error_reason_lines(reason),
                        }
                    )
                    current_node["failed"] += 1
                    failed_total += 1

            if current_node:
                result_tree.append(current_node)

        # 失败节点排前面
        result_tree.sort(key=lambda n: (n["failed"] == 0, n["name"]))

        # 为每个结果树节点附加 IP（从目标主机列表匹配）
        host_to_ip = {}
        if task.target_hostnames and task.target_ips:
            hostnames = task.target_hostnames.split(",")
            ips = task.target_ips.split(",")
            for i in range(min(len(hostnames), len(ips))):
                hn = hostnames[i].strip()
                ip = ips[i].strip()
                if hn and ip:
                    host_to_ip[hn] = ip
        for node in result_tree:
            node_name = node["name"]
            # 从 "IP (hostname)" 格式提取纯 IP
            ip_match = re.match(r'^([\d.]+)\s*\(', node_name)
            if ip_match:
                node["node_ip"] = ip_match.group(1)
            elif node_name in host_to_ip:
                node["node_ip"] = host_to_ip[node_name]
            else:
                node["node_ip"] = node_name
            # 提取主机名（括号内文本）
            hn_match = re.search(r'\(([^)]+)\)', node_name)
            if hn_match:
                node["node_hostname"] = hn_match.group(1)
            elif node_name in host_to_ip:
                node["node_hostname"] = node_name
            else:
                node["node_hostname"] = node.get("node_ip", node_name)

        # 计算执行耗时
        duration = ""
        if task.started_at and task.finished_at:
            delta = (task.finished_at - task.started_at).total_seconds()
            if delta >= 60:
                duration = f"{delta / 60:.1f} 分钟"
            else:
                duration = f"{delta:.1f} 秒"

        # Nginx 升级 / 卸载：关联模块任务详情入口
        upgrade_task = None
        uninstall_task = None
        if op == "nginx_upgrade":
            try:
                from apps.upgrade.models import NginxUpgradeTask

                upgrade_task = (
                    NginxUpgradeTask.objects.filter(task_center_id=task.id)
                    .select_related("node")
                    .first()
                )
            except Exception:
                upgrade_task = None
        elif op == "nginx_uninstall":
            try:
                from apps.nginx_uninstall.models import NginxUninstallTask

                uninstall_task = (
                    NginxUninstallTask.objects.filter(task_center_id=task.id)
                    .select_related("node")
                    .first()
                )
            except Exception:
                uninstall_task = None

        # 系统信息 / 版本检测：特化展示
        system_info_rows = None
        nginx_version_text = None
        if op == "node_system_info" and result_text:
            try:
                import json as _json

                data = _json.loads(result_text)
                if isinstance(data, dict):
                    system_info_rows = [{"key": k, "value": v} for k, v in data.items()]
            except (ValueError, TypeError):
                system_info_rows = None
        elif op == "node_nginx_version" and result_text:
            nginx_version_text = result_text

        context["result_tree"] = result_tree
        context["result_summary"] = {"success": success_total, "failed": failed_total}
        context["execution_duration"] = duration
        context["upgrade_task"] = upgrade_task
        context["uninstall_task"] = uninstall_task
        context["system_info_rows"] = system_info_rows
        context["nginx_version_text"] = nginx_version_text
        context["can_cancel"] = task.status in ("pending", "running")
        return context


class TaskCenterCancelView(LoginRequiredMixin, View):
    """协作式取消任务中心任务：标 cancelled、关 SSH、级联发布/升级明细"""

    def dispatch(self, request, *args, **kwargs):
        self.can_read_release_tasks = user_has_permission(
            request.user, "releases", "read"
        )
        self.can_read_node_tasks = user_can_access_limited_task_center(request.user)
        if not (self.can_read_release_tasks or self.can_read_node_tasks):
            return forbidden_response(request, "当前账号无权限访问该功能")
        return super().dispatch(request, *args, **kwargs)

    def _get_task(self, pk):
        """按可见范围获取任务"""
        qs = TaskCenterTask.objects.filter(pk=pk)
        if not self.can_read_release_tasks:
            allowed = task_center_limited_ops_for_user(self.request.user)
            qs = qs.filter(
                operation_type__in=allowed or ["__none__"],
                trigger_user=self.request.user,
            )
        return qs.first()

    def post(self, request, pk):
        """执行取消：写库 → 级联 → 关闭已登记 SSH"""
        task = self._get_task(pk)
        if not task:
            return JsonResponse(
                {"success": False, "message": "任务不存在或无权操作"}, status=404
            )
        if task.status not in ("pending", "running"):
            return JsonResponse(
                {
                    "success": False,
                    "message": f"当前状态（{task.get_status_display()}）不可取消",
                },
                status=400,
            )

        detail = "用户手动取消"
        ok = mark_cancelled(task.id, detail=detail, result=detail)
        if not ok:
            return JsonResponse(
                {"success": False, "message": "取消失败，任务状态可能已变更"},
                status=409,
            )

        # 级联：同批次发布/回滚明细
        if (
            task.operation_type in ("release_publish", "release_rollback")
            and task.source_batch
        ):
            ReleaseTask.objects.filter(
                batch_number=task.source_batch,
                status__in=("pending", "running"),
            ).update(status="cancelled", result=detail, finished_at=timezone.now())

        # 级联：关联升级任务
        if task.operation_type in ("nginx_upgrade", "nginx_rollback"):
            try:
                from apps.upgrade.models import NginxUpgradeTask

                terminal = ("success", "failed", "rollback", "cancelled")
                NginxUpgradeTask.objects.filter(task_center_id=task.id).exclude(
                    status__in=terminal
                ).update(
                    status="cancelled",
                    error_message=detail,
                    finished_at=timezone.now(),
                )
            except Exception:
                logger.exception("级联取消升级任务失败 task_center=%s", task.id)

        # 级联：关联卸载任务
        if task.operation_type == "nginx_uninstall":
            try:
                from apps.nginx_uninstall.models import NginxUninstallTask

                terminal = ("success", "failed", "cancelled")
                NginxUninstallTask.objects.filter(task_center_id=task.id).exclude(
                    status__in=terminal
                ).update(
                    status="cancelled",
                    error_message=detail,
                    finished_at=timezone.now(),
                )
            except Exception:
                logger.exception("级联取消卸载任务失败 task_center=%s", task.id)

        closed = close_registered_ssh(task.id)
        _clear_release_progress_state(task.id)

        return JsonResponse(
            {
                "success": True,
                "message": "任务已取消",
                "ssh_closed": closed,
            }
        )


class ReleaseDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    model = ReleaseTask
    template_name = "releases/detail.html"
    context_object_name = "task"
    permission_resource = "releases"
    permission_action = "read"

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .select_related(
                "node",
                "config",
                "binding",
                "operator",
                "version",
            )
        )

    def get_context_data(self, **kwargs):
        """组装操作记录，并为历史版本号解析 BindingVersion.id 供预览"""
        context = super().get_context_data(**kwargs)
        histories = list(
            self.object.history.all().select_related("node", "config", "operator")
        )
        version_id_map = {}
        if self.object.binding_id:
            version_id_map = dict(
                BindingVersion.objects.filter(
                    binding_id=self.object.binding_id
                ).values_list("version", "id")
            )
        for h in histories:
            # 供模板挂 version-preview-link
            h.preview_version_id = version_id_map.get(h.version)
        context["histories"] = histories
        return context


class ReleaseRollbackView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_resource = "releases"
    permission_action = "publish"
    # 仅成功或失败的发布允许人工回滚
    ROLLBACK_ALLOWED_STATUSES = ("success", "failed")

    def get(self, request, pk):
        from django.core.paginator import Paginator

        task = get_object_or_404(
            ReleaseTask.objects.select_related(
                "node", "config", "binding", "operator", "version"
            ),
            pk=pk,
        )
        if task.status not in self.ROLLBACK_ALLOWED_STATUSES:
            messages.error(request, "仅成功或失败的发布可回滚")
            return redirect("releases:detail", pk=task.pk)
        if task.node.is_deleted:
            messages.error(request, f"节点 {task.node.hostname} 已删除，无法回滚")
            return redirect("releases:detail", pk=task.pk)
        from apps.nodes.services import nginx_ops_gate_message

        gate_msg = nginx_ops_gate_message(task.node)
        if gate_msg:
            messages.error(request, gate_msg)
            return redirect("releases:detail", pk=task.pk)
        binding = task.binding
        versions = []
        if binding:
            versions = binding.versions.select_related("created_by").order_by(
                "-version"
            )
        paginator = Paginator(versions, 15)
        page_number = request.GET.get("page", 1)
        page_obj = paginator.get_page(page_number)
        return render(
            request,
            "releases/rollback.html",
            {
                "task": task,
                "config": task.config,
                "page_obj": page_obj,
            },
        )

    def post(self, request, pk):
        """创建回滚任务并立即异步执行（与批量回滚一致）"""
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
        task = get_object_or_404(
            ReleaseTask.objects.select_related("node", "config", "binding", "operator"),
            pk=pk,
        )
        if task.status not in self.ROLLBACK_ALLOWED_STATUSES:
            msg = "仅成功或失败的发布可回滚"
            if is_ajax:
                return JsonResponse({"success": False, "message": msg}, status=400)
            messages.error(request, msg)
            return redirect("releases:detail", pk=task.pk)
        if task.node.is_locked:
            msg = f"节点 {task.node.hostname} 已锁定，无法回滚"
            if is_ajax:
                return JsonResponse({"success": False, "message": msg}, status=400)
            messages.error(request, msg)
            return redirect("releases:rollback", pk=task.pk)
        if task.node.is_deleted:
            msg = f"节点 {task.node.hostname} 已删除，无法回滚"
            if is_ajax:
                return JsonResponse({"success": False, "message": msg}, status=400)
            messages.error(request, msg)
            return redirect("releases:detail", pk=task.pk)
        from apps.nodes.services import nginx_ops_gate_message

        gate_msg = nginx_ops_gate_message(task.node)
        if gate_msg:
            if is_ajax:
                return JsonResponse({"success": False, "message": gate_msg}, status=400)
            messages.error(request, gate_msg)
            return redirect("releases:detail", pk=task.pk)

        version_id = request.POST.get("version_id")
        if not version_id:
            msg = "请选择要回滚的版本"
            if is_ajax:
                return JsonResponse({"success": False, "message": msg}, status=400)
            messages.error(request, msg)
            return redirect("releases:rollback", pk=task.pk)

        version = get_object_or_404(BindingVersion, pk=version_id, binding=task.binding)
        batch_number = generate_batch_number()
        new_task = ReleaseTask.objects.create(
            binding=task.binding,
            node=task.node,
            config=task.config,
            version=version,
            publish_version=version.version,
            remote_path=task.remote_path
            or (task.binding.remote_path if task.binding else ""),
            operator=request.user,
            status="pending",
            batch_number=batch_number,
        )

        from apps.releases.task_result import targets_from_release_tasks

        targets = targets_from_release_tasks([new_task.id])
        task_center = TaskCenterTask.objects.create(
            operation_type="release_rollback",
            status="running",
            source_batch=batch_number,
            detail=f"回滚：{task.config.name} → {task.node.hostname} v{version.version}",
            progress=0,
            started_at=timezone.now(),
            trigger_user=request.user,
            **targets,
        )
        _start_release_executor([new_task.id], task_center.id)

        if is_ajax:
            return JsonResponse(
                {
                    "success": True,
                    "batch_number": batch_number,
                    "task_center_id": task_center.id,
                    "message": f"回滚已开始，批次号: {batch_number}",
                }
            )

        messages.success(request, f"回滚已开始，批次号: {batch_number}")
        return redirect("releases:task_center_detail", pk=task_center.id)


class ReleaseCenterView(
    LoginRequiredMixin, PermissionRequiredMixin, PerPagePaginationMixin, ListView
):
    """发布中心 - 节点为主维度选择 + 配置绑定展开（数据通过 AJAX 加载）"""

    model = ReleaseTask
    template_name = "releases/center.html"
    context_object_name = "tasks"
    paginate_by = 10
    ordering = ["-created_at"]
    permission_resource = "releases"
    permission_action = "read"

    def get_queryset(self):
        # 数据由前端 AJAX 加载，服务端无需查询
        return ReleaseTask.objects.none()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["pre_node_id"] = self.request.GET.get("node_id", "")
        context["pre_binding_id"] = self.request.GET.get("binding_id", "")
        context["environment_choices"] = Node.ENV_CHOICES
        from utils.setting_service import get_setting

        try:
            context["batch_max_count"] = max(
                1, int(get_setting("node.batch_max_count", "3") or 3)
            )
        except (TypeError, ValueError):
            context["batch_max_count"] = 3
        return context


class TaskCenterProgressAPIView(LoginRequiredMixin, View):
    def dispatch(self, request, *args, **kwargs):
        self.can_read_release_tasks = user_has_permission(
            request.user, "releases", "read"
        )
        self.can_read_node_tasks = user_can_access_limited_task_center(request.user)
        self.can_sync_configs = user_has_permission(request.user, "configs", "sync")
        self.can_read_upgrade = user_has_permission(request.user, "upgrade", "read")
        if not (
            self.can_read_release_tasks
            or self.can_read_node_tasks
            or self.can_sync_configs
            or self.can_read_upgrade
        ):
            return forbidden_response(request, "当前账号无权限访问该功能")
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        ids_raw = request.GET.get("ids", "")
        id_list = [int(i) for i in ids_raw.split(",") if i.strip().isdigit()]
        if not id_list:
            return JsonResponse({"success": True, "tasks": []})
        tasks = TaskCenterTask.objects.filter(id__in=id_list).order_by("-created_at")
        # 无发布读权限时：仅本人触发的节点测试 / 配置同步 / 运维任务
        if not self.can_read_release_tasks:
            allowed = set(task_center_limited_ops_for_user(request.user))
            if self.can_sync_configs:
                allowed.add("config_batch_sync")
            if self.can_read_upgrade:
                allowed.update(["nginx_upgrade", "nginx_rollback"])
            tasks = tasks.filter(
                operation_type__in=list(allowed) or ["__none__"],
                trigger_user=request.user,
            )
        data = [
            {
                "id": t.id,
                "status": t.status,
                "progress": t.progress,
                "detail": t.detail,
                "result": t.result,
                "log_output": t.log_output or "",
                "current_steps": (
                    _format_current_steps(t.id)
                    if t.status in ("pending", "running")
                    else ""
                ),
                "finished": t.status in ["success", "failed", "cancelled"],
            }
            for t in tasks
        ]
        return JsonResponse({"success": True, "tasks": data})


class ReleaseCenterExecuteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_resource = "releases"
    permission_action = "publish"

    def post(self, request, batch_number):
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
        try:
            if ReleaseTask.objects.filter(status="running").exists():
                msg = "当前有批次正在执行中，请等待完成后再执行"
                if is_ajax:
                    return JsonResponse({"success": False, "message": msg})
                messages.error(request, msg)
                return redirect("releases:center")
        except OperationalError:
            pass

        tasks_qs = ReleaseTask.objects.filter(
            batch_number=batch_number,
            status="pending",
        ).select_related("node", "config", "binding", "operator")
        task_ids = list(tasks_qs.values_list("id", flat=True))

        if not task_ids:
            msg = "没有可执行的发布任务"
            if is_ajax:
                return JsonResponse({"success": False, "message": msg})
            messages.error(request, msg)
            return redirect("releases:center")

        # 创建 TaskCenterTask
        from apps.releases.task_result import targets_from_release_tasks

        targets = targets_from_release_tasks(task_ids)
        task_center = TaskCenterTask.objects.create(
            operation_type="release_publish",
            status="running",
            source_batch=batch_number,
            detail=f"执行中：成功 0，失败 0，共 {len(task_ids)}",
            progress=0,
            started_at=timezone.now(),
            trigger_user=request.user,
            **targets,
        )

        _start_release_executor(task_ids, task_center.id)

        redirect_url = reverse(
            "releases:task_center_detail", kwargs={"pk": task_center.id}
        )
        if is_ajax:
            return JsonResponse(
                {
                    "success": True,
                    "async": True,
                    "task_center_id": task_center.id,
                    "task_center_detail_url": redirect_url,
                }
            )

        messages.success(
            request,
            f"发布任务已开始执行，{len(task_ids)} 个任务，批次号: {batch_number}",
        )
        return redirect(redirect_url)


class ReleaseCenterCancelView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_resource = "releases"
    permission_action = "update"

    def post(self, request, batch_number):
        updated = ReleaseTask.objects.filter(
            batch_number=batch_number,
            status="pending",
        ).update(status="cancelled", result="用户取消")
        if updated:
            messages.success(request, f"已取消 {updated} 个待执行任务")
        else:
            messages.info(request, "没有待执行的任务")
        return redirect("releases:center")


class ReleaseCenterSingleExecuteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_resource = "releases"
    permission_action = "update"

    def post(self, request, task_id):
        task = get_object_or_404(
            ReleaseTask.objects.select_related("node", "config", "binding", "operator"),
            pk=task_id,
        )
        if task.status != "pending":
            messages.error(request, "任务不是待发布状态")
            return redirect("releases:center")

        from apps.releases.task_result import targets_from_release_tasks

        targets = targets_from_release_tasks([task.id])
        task_center = TaskCenterTask.objects.create(
            operation_type="release_publish",
            status="running",
            source_batch=task.batch_number,
            detail="执行中...",
            progress=0,
            started_at=timezone.now(),
            trigger_user=request.user,
            **targets,
        )

        _start_release_executor([task.id], task_center.id)

        messages.success(request, f"发布任务 #{task_id} 已开始执行")
        return redirect("releases:center")


class ReleaseTaskStatusView(LoginRequiredMixin, View):
    """查询单个任务状态 (Ajax)"""

    def get(self, request, task_id):
        task = get_object_or_404(ReleaseTask, pk=task_id)
        return JsonResponse(
            {
                "id": task.id,
                "status": task.status,
                "result": task.result,
                "finished": task.status
                in ["success", "failed", "rollback", "cancelled"],
            }
        )


class VersionContentAPIView(LoginRequiredMixin, View):
    """获取版本内容 (Ajax)"""

    def get(self, request, version_id):
        from apps.configs.models import BindingVersion

        version = get_object_or_404(BindingVersion, pk=version_id)
        return JsonResponse(
            {
                "id": version.id,
                "version": version.version,
                "content": version.content,
                "remark": version.remark,
                "created_at": version.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                "created_by": version.created_by.username if version.created_by else "",
            }
        )


def _build_release_status_counts():
    """构建发布中心绑定状态全局统计（排除已删节点）"""
    from apps.configs.models import ConfigNodeBinding

    bindings = ConfigNodeBinding.objects.filter(node__is_deleted=False)
    return {
        "total": bindings.count(),
        "pending": bindings.filter(sync_status__in=["not_synced", "modified"]).count(),
        "synced": bindings.filter(sync_status="synced").count(),
        "failed": bindings.filter(sync_status="failed").count(),
        "orphaned": bindings.filter(sync_status="orphaned").count(),
        "marked_deleted": bindings.filter(sync_status="marked_deleted").count(),
    }


class ReleaseNodeListAPIView(LoginRequiredMixin, View):
    """获取发布中心可选节点列表（含绑定统计）"""

    def get(self, request):
        search = request.GET.get("search", "").strip()
        environment = request.GET.get("environment", "").strip()
        group_id = request.GET.get("group_id", "").strip()
        node_status = request.GET.get("status", "").strip()
        sync_status = request.GET.get("sync_status", "").strip()
        nginx_available = request.GET.get("nginx_available", "true").strip()
        page = int(request.GET.get("page", 1))
        page_size = int(request.GET.get("page_size", 20))

        queryset = Node.objects.all().prefetch_related("groups")

        if search:
            terms = [
                t.strip() for t in search.replace("，", ",").split(",") if t.strip()
            ]
            for term in terms:
                queryset = queryset.filter(
                    Q(hostname__icontains=term)
                    | Q(ip__icontains=term)
                    | Q(groups__name__icontains=term)
                    | Q(config_bindings__config__name__icontains=term)
                    | Q(config_bindings__remote_path__icontains=term)
                ).distinct()
        if environment:
            queryset = queryset.filter(environment=environment)
        if node_status:
            queryset = queryset.filter(status=node_status)
        if nginx_available == "true":
            queryset = queryset.filter(nginx_available=True)
        if group_id and group_id.isdigit():
            queryset = queryset.filter(groups__id=int(group_id)).distinct()

        # 按绑定同步状态过滤节点
        if sync_status:
            if sync_status == "pending":
                status_values = ["not_synced", "modified"]
            else:
                status_values = [sync_status]
            node_ids_with_status = (
                ConfigNodeBinding.objects.filter(sync_status__in=status_values)
                .values_list("node_id", flat=True)
                .distinct()
            )
            queryset = queryset.filter(id__in=node_ids_with_status)

        total = queryset.count()
        nodes_page = queryset[(page - 1) * page_size : page * page_size]

        node_ids = [n.id for n in nodes_page]
        binding_stats = {}
        if node_ids:
            from django.db.models import Count, Q as DQ

            stats_qs = (
                ConfigNodeBinding.objects.filter(node_id__in=node_ids)
                .values("node_id")
                .annotate(
                    total_bindings=Count("id"),
                    modified_bindings=Count("id", filter=DQ(sync_status="modified")),
                )
            )
            for row in stats_qs:
                binding_stats[row["node_id"]] = {
                    "total_bindings": row["total_bindings"],
                    "modified_bindings": row["modified_bindings"],
                }

        node_list = []
        for node in nodes_page:
            stats = binding_stats.get(
                node.id, {"total_bindings": 0, "modified_bindings": 0}
            )
            node_list.append(
                {
                    "id": node.id,
                    "hostname": node.hostname,
                    "ip": f"{node.ip}:{node.port}",
                    "environment": node.environment,
                    "status": node.status,
                    "is_locked": node.is_locked,
                    "has_credential": node.credential_id is not None,
                    "nginx_available": node.nginx_available,
                    "nginx_version": node.nginx_version or "",
                    "nginx_status_label": node.nginx_status_label,
                    "total_bindings": stats["total_bindings"],
                    "modified_bindings": stats["modified_bindings"],
                    "group_names": [g.name for g in node.groups.all()],
                }
            )

        return JsonResponse(
            {
                "success": True,
                "nodes": node_list,
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": max(1, (total + page_size - 1) // page_size),
                "status_counts": _build_release_status_counts(),
            }
        )


class ReleaseNodeBindingsAPIView(LoginRequiredMixin, View):
    """获取指定节点的所有绑定详情（含版本列表）"""

    def get(self, request, node_id):
        bindings = (
            ConfigNodeBinding.objects.filter(node_id=node_id)
            .select_related("config")
            .order_by("config__name")
        )

        result = []
        for binding in bindings:
            versions = binding.versions.order_by("-version").values(
                "id", "version", "created_at"
            )
            result.append(
                {
                    "id": binding.id,
                    "config_id": binding.config_id,
                    "config_name": binding.config.name,
                    "remote_path": binding.remote_path,
                    "current_version": binding.current_version,
                    "sync_status": binding.sync_status,
                    "synced_version": binding.synced_version,
                    "versions": [
                        {
                            "id": v["id"],
                            "version": v["version"],
                            "created_at": (
                                v["created_at"].strftime("%Y-%m-%d %H:%M:%S")
                                if v["created_at"]
                                else ""
                            ),
                        }
                        for v in versions
                    ],
                }
            )

        return JsonResponse({"success": True, "bindings": result})


class ReleaseRetryView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """重试单条失败的发布任务"""

    permission_resource = "releases"
    permission_action = "publish"

    def post(self, request, pk):
        task = get_object_or_404(
            ReleaseTask.objects.select_related("node", "config", "binding", "operator"),
            pk=pk,
        )
        if task.status not in ["failed"]:
            return JsonResponse(
                {"success": False, "message": "只能重试失败的任务"}, status=400
            )

        if task.node.is_locked:
            return JsonResponse(
                {"success": False, "message": f"节点 {task.node.hostname} 已锁定"},
                status=400,
            )
        if task.node.is_deleted:
            return JsonResponse(
                {
                    "success": False,
                    "message": f"节点 {task.node.hostname} 已删除，无法重试",
                },
                status=400,
            )
        from apps.nodes.services import nginx_ops_gate_message

        gate_msg = nginx_ops_gate_message(task.node)
        if gate_msg:
            return JsonResponse({"success": False, "message": gate_msg}, status=400)

        task.status = "pending"
        task.result = ""
        task.save(update_fields=["status", "result"])

        from apps.releases.task_result import targets_from_release_tasks

        targets = targets_from_release_tasks([task.id])
        task_center = TaskCenterTask.objects.create(
            operation_type="release_publish",
            status="running",
            source_batch=task.batch_number or f"retry-task-{task.id}",
            detail=f"重试: {task.config.name} → {task.node.hostname}",
            progress=0,
            started_at=timezone.now(),
            trigger_user=request.user,
            **targets,
        )

        _start_release_executor([task.id], task_center.id)

        return JsonResponse(
            {
                "success": True,
                "message": f"重试任务已开始: {task.config.name} → {task.node.hostname}",
                "task_center_id": task_center.id,
            }
        )
