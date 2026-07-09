import json
import re
import threading
import logging
from datetime import datetime

from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.db import OperationalError
from django.db.models import Q
from django.utils import timezone
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect, get_object_or_404, render
from django.urls import reverse
from django.views.generic import ListView, DetailView, CreateView, View

from apps.nodes.views import _get_node_credential
from apps.nodes.models import Node
from apps.configs.models import Config, ConfigVersion
from apps.users.permissions import (
    PermissionRequiredMixin,
    user_has_permission,
    forbidden_response,
)
from utils.ssh import (
    backup_remote_file,
    upload_file_via_sftp,
    restore_backup_file,
    check_remote_file_size,
    check_remote_file_md5,
    copy_remote_file,
    execute_nginx_test,
    execute_nginx_reload,
)

from .forms import ReleaseCreateForm
from .models import ReleaseTask, ReleaseHistory, TaskCenterTask, generate_batch_number
from utils.pagination import PerPagePaginationMixin

logger = logging.getLogger(__name__)


class ReleaseExecutorMixin:
    def _execute_release(self, task, action):
        node = task.node
        config = task.config
        version = task.version

        if node.is_locked:
            task.status = "failed"
            task.result = f"节点 {node.hostname} 已锁定，无法执行发布"
            task.finished_at = datetime.now()
            task.save()
            self._record_history(task, action, task.result)
            return False, task.result

        credential = _get_node_credential(node)
        if not credential:
            task.status = "failed"
            task.result = f"节点 {node.hostname} 未配置 SSH 凭证"
            task.finished_at = datetime.now()
            task.save()
            self._record_history(task, action, task.result)
            return False, task.result

        log_lines = []

        def add_log(msg):
            log_lines.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

        task.status = "running"
        task.started_at = datetime.now()
        task.save()

        kwargs = {
            "host": node.ip,
            "port": node.port,
            "username": credential.username,
        }
        if credential.auth_type == "password":
            kwargs["password"] = credential.get_password()
        else:
            kwargs["private_key"] = credential.get_private_key()

        add_log(f"开始发布: {config.name} v{version.version} → {node.hostname}")
        add_log(f"目标路径: {config.file_path}")

        if not version.content or not version.content.strip():
            add_log("配置内容为空，中止发布")
            task.status = "failed"
            task.result = "\n".join(log_lines)
            task.finished_at = datetime.now()
            task.save()
            self._record_history(task, action, task.result)
            return False, f"配置 {config.name} v{version.version} 内容为空，无法发布"

        add_log("正在备份原配置...")
        success, backup_result = backup_remote_file(
            file_path=config.file_path,
            **kwargs,
        )
        if success:
            add_log(f"备份成功: {backup_result}")
            backup_size_ok, backup_size_msg = check_remote_file_size(
                file_path=backup_result,
                **kwargs,
            )
            add_log(f"备份文件大小: {backup_size_msg}")
            if not backup_size_ok:
                add_log("警告: 备份文件为空，回滚将无法恢复原配置")
        else:
            add_log(f"备份失败: {backup_result}")
            task.status = "failed"
            task.result = "\n".join(log_lines)
            task.finished_at = datetime.now()
            task.save()
            self._record_history(task, action, task.result)
            return False, f"备份失败: {backup_result}"

        add_log("正在上传配置到 /tmp ...")
        tmp_path = f"/tmp/{config.file_path.split('/')[-1]}.mngxops_tmp"
        success, upload_result = upload_file_via_sftp(
            remote_path=tmp_path,
            content=version.content,
            **kwargs,
        )
        if not success:
            add_log(f"上传到 /tmp 失败: {upload_result}")
            task.status = "failed"
            task.result = "\n".join(log_lines)
            task.finished_at = datetime.now()
            task.save()
            self._record_history(task, action, task.result)
            return False, f"上传到 /tmp 失败: {upload_result}"

        add_log(f"已上传到 {tmp_path}，检查文件大小...")
        size_ok, size_msg = check_remote_file_size(
            file_path=tmp_path,
            **kwargs,
        )
        add_log(f"/tmp 文件大小: {size_msg}")

        if not size_ok:
            add_log("/tmp 文件为空，中止发布")
            task.status = "failed"
            task.result = "\n".join(log_lines)
            task.finished_at = datetime.now()
            task.save()
            self._record_history(task, action, task.result)
            return False, f"/tmp 文件为空: {size_msg}"

        add_log(f"从 /tmp 复制到目标路径 {config.file_path} ...")
        copy_ok, copy_msg = copy_remote_file(
            src_path=tmp_path,
            dst_path=config.file_path,
            **kwargs,
        )
        if not copy_ok:
            add_log(f"复制失败: {copy_msg}")
            add_log("正在回滚备份...")
            self._rollback_backup(backup_result, config.file_path, kwargs, log_lines)
            task.status = "failed"
            task.result = "\n".join(log_lines)
            task.finished_at = datetime.now()
            task.save()
            self._record_history(task, action, task.result)
            return False, f"复制失败: {copy_msg}"

        add_log(f"验证目标文件大小...")
        target_ok, target_msg = check_remote_file_size(
            file_path=config.file_path,
            **kwargs,
        )
        add_log(f"目标文件大小: {target_msg}")

        add_log("校验文件 md5 确保内容一致...")
        tmp_md5_ok, tmp_md5 = check_remote_file_md5(
            file_path=tmp_path,
            **kwargs,
        )
        target_md5_ok, target_md5 = check_remote_file_md5(
            file_path=config.file_path,
            **kwargs,
        )
        add_log(f"/tmp 文件 md5: {tmp_md5}")
        add_log(f"目标文件 md5: {target_md5}")
        if tmp_md5_ok and target_md5_ok and tmp_md5 == target_md5:
            add_log("md5 一致，内容替换成功")
        else:
            add_log("md5 不一致，内容替换失败！")

        if not target_ok:
            add_log("目标文件为空，正在回滚备份...")
            self._rollback_backup(backup_result, config.file_path, kwargs, log_lines)
            task.status = "failed"
            task.result = "\n".join(log_lines)
            task.finished_at = datetime.now()
            task.save()
            self._record_history(task, action, task.result)
            return False, f"目标文件为空: {target_msg}"

        add_log("上传成功")

        add_log("正在执行 nginx -t ...")
        nginx_path = node.nginx_path or None
        success, test_output = execute_nginx_test(
            config_path=config.file_path,
            nginx_path=nginx_path,
            **kwargs,
        )
        add_log(test_output)
        if not success:
            add_log("nginx -t 失败，正在回滚备份...")
            self._rollback_backup(backup_result, config.file_path, kwargs, log_lines)
            task.status = "failed"
            task.result = "\n".join(log_lines)
            task.finished_at = datetime.now()
            task.save()
            self._record_history(task, action, task.result)
            return False, f"nginx -t 失败: {test_output}"

        add_log("nginx -t 通过，正在执行 reload...")
        success, reload_output = execute_nginx_reload(
            nginx_path=nginx_path,
            **kwargs,
        )
        add_log(reload_output)
        if success:
            add_log("发布成功!")
            task.status = "success"
        else:
            add_log("reload 失败，正在回滚备份...")
            self._rollback_backup(backup_result, config.file_path, kwargs, log_lines)
            task.status = "failed"

        task.result = "\n".join(log_lines)
        task.finished_at = datetime.now()
        task.save()
        self._record_history(task, action, task.result)
        return (
            True,
            f"配置 {config.name} v{version.version} 发布到 {node.hostname} 成功",
        )

    def _record_history(self, task, action, result):
        ReleaseHistory.objects.create(
            release_task=task,
            node=task.node,
            config=task.config,
            version=task.version.version,
            operator=task.operator,
            action=action,
            result=result,
        )

    def _rollback_backup(self, backup_result, config_file_path, kwargs, log_lines):
        backup_size_ok, backup_size_msg = check_remote_file_size(
            file_path=backup_result,
            **kwargs,
        )
        if not backup_size_ok:
            log_lines.append("警告: 备份文件为空，跳过回滚，避免清空目标配置")
            return
        rollback_ok, rollback_msg = restore_backup_file(
            backup_path=backup_result,
            original_path=config_file_path,
            **kwargs,
        )
        if rollback_ok:
            log_lines.append("回滚完成")
        else:
            log_lines.append(f"回滚失败: {rollback_msg}")


class ReleaseCreateView(
    LoginRequiredMixin, PermissionRequiredMixin, ReleaseExecutorMixin, CreateView
):
    model = ReleaseTask
    form_class = ReleaseCreateForm
    template_name = "releases/create.html"
    permission_resource = "releases"
    permission_action = "create"
    recommended_nodes_per_batch = 5
    recommended_configs_per_batch = 10
    max_tasks_per_batch = 1000

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return context

    def post(self, request, *args, **kwargs):
        from apps.configs.models import Config, ConfigVersion

        node_config_pairs_raw = request.POST.getlist("node_config_pairs")
        node_config_pairs = []
        for item in node_config_pairs_raw:
            if ":" in item:
                nid, cid = item.split(":", 1)
                if nid.isdigit() and cid.isdigit():
                    node_config_pairs.append((int(nid), int(cid)))

        node_ids = request.POST.getlist("node_ids")
        config_ids = request.POST.getlist("config_ids")

        if node_config_pairs:
            node_ids = list({str(nid) for nid, _ in node_config_pairs})
            config_ids = [str(cid) for _, cid in node_config_pairs]

        # Parse version_ids mapping: config_id → version_id
        version_ids = {}
        version_ids_raw = request.POST.getlist("version_ids")
        for item in version_ids_raw:
            if ":" in item:
                cid, vid = item.split(":", 1)
                version_ids[int(cid)] = int(vid)

        if not node_ids or not config_ids:
            messages.error(request, "请至少选择一个节点和一个配置")
            return redirect("releases:create")

        selected_config_count = (
            len(node_config_pairs) if node_config_pairs else len(config_ids)
        )
        if len(node_ids) > self.recommended_nodes_per_batch:
            messages.warning(
                request,
                f"当前选择 {len(node_ids)} 个节点，已超过默认建议 {self.recommended_nodes_per_batch} 个节点，任务将继续创建",
            )
        if selected_config_count > self.recommended_configs_per_batch:
            messages.warning(
                request,
                f"当前选择 {selected_config_count} 个配置，已超过默认建议 {self.recommended_configs_per_batch} 个配置，任务将继续创建",
            )

        planned_task_count = (
            len(node_config_pairs)
            if node_config_pairs
            else len(node_ids) * len(config_ids)
        )
        if planned_task_count > self.max_tasks_per_batch:
            messages.error(
                request,
                f"单次最多创建 {self.max_tasks_per_batch} 个发布任务，当前将创建 {planned_task_count} 个",
            )
            return redirect("releases:create")

        batch_number = generate_batch_number()
        created_count = 0

        # Check if any selected node is locked
        for node_id in node_ids:
            node = get_object_or_404(Node, id=node_id)
            if node.is_locked:
                messages.error(
                    request, f"节点 {node.hostname} 已锁定，无法创建发布任务"
                )
                return redirect("releases:create")

        if node_config_pairs:
            for node_id, config_id in node_config_pairs:
                node = get_object_or_404(Node, id=node_id)
                config = get_object_or_404(Config, id=config_id, nodes__id=node_id)

                if config_id in version_ids:
                    version = get_object_or_404(
                        ConfigVersion, id=version_ids[config_id], config_id=config_id
                    )
                else:
                    version = config.versions.order_by("-version").first()

                if not version:
                    continue

                ReleaseTask.objects.create(
                    batch_number=batch_number,
                    node=node,
                    config=config,
                    version=version,
                    operator=request.user,
                    status="pending",
                )
                created_count += 1
        else:
            for node_id in node_ids:
                node = get_object_or_404(Node, id=node_id)
                for config_id in config_ids:
                    config = get_object_or_404(Config, id=config_id, nodes__id=node_id)

                    # Use specified version if available, otherwise latest
                    if config_id in version_ids:
                        version = get_object_or_404(
                            ConfigVersion,
                            id=version_ids[config_id],
                            config_id=config_id,
                        )
                    else:
                        version = config.versions.order_by("-version").first()

                    if not version:
                        continue

                    ReleaseTask.objects.create(
                        batch_number=batch_number,
                        node=node,
                        config=config,
                        version=version,
                        operator=request.user,
                        status="pending",
                    )
                    created_count += 1

        if created_count == 0:
            messages.error(request, "未找到可发布的配置版本")
            return redirect("releases:create")

        messages.success(
            request,
            f"发布任务已创建，批次号: {batch_number}，共 {created_count} 个任务，请在发布中心确认后执行",
        )
        return redirect("releases:center")


class ReleaseListView(
    LoginRequiredMixin, PermissionRequiredMixin, PerPagePaginationMixin, ListView
):
    model = ReleaseTask
    template_name = "releases/list.html"
    context_object_name = "tasks"
    paginate_by = 10
    ordering = ["-created_at"]
    permission_resource = "releases"
    permission_action = "read"

    def get_queryset(self):
        queryset = (
            super()
            .get_queryset()
            .select_related("node", "config", "version", "operator")
        )
        search = self.request.GET.get("search", "")
        status_filter = self.request.GET.get("status", "")

        if search:
            queryset = queryset.filter(
                Q(config__name__icontains=search)
                | Q(node__hostname__icontains=search)
                | Q(operator__username__icontains=search)
            )
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        from collections import OrderedDict

        search = self.request.GET.get("search", "")
        status_filter = self.request.GET.get("status", "")

        context["search"] = search
        context["status_filter"] = status_filter
        context["status_choices"] = ReleaseTask.STATUS_CHOICES

        node_tasks = OrderedDict()
        for task in context["tasks"]:
            node_tasks.setdefault(task.node, []).append(task)

        context["node_tasks"] = node_tasks
        context["has_any_filter"] = bool(search or status_filter)
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
        self.can_read_node_tasks = user_has_permission(request.user, "nodes", "update")
        if not (self.can_read_release_tasks or self.can_read_node_tasks):
            return forbidden_response(request, "当前账号无权限访问该功能")
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        queryset = super().get_queryset().select_related("trigger_user")
        if not self.can_read_release_tasks:
            queryset = queryset.filter(
                operation_type="node_batch_test", trigger_user=self.request.user
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
        context["search"] = self.request.GET.get("search", "")
        context["status_filter"] = self.request.GET.get("status", "")
        context["operation_type_filter"] = self.request.GET.get("operation_type", "")
        context["status_choices"] = TaskCenterTask.STATUS_CHOICES
        context["operation_type_choices"] = TaskCenterTask.OPERATION_TYPE_CHOICES
        context["has_any_filter"] = bool(
            context["search"]
            or context["status_filter"]
            or context["operation_type_filter"]
        )
        return context


class TaskCenterDetailView(LoginRequiredMixin, DetailView):
    model = TaskCenterTask
    template_name = "releases/task_detail.html"
    context_object_name = "task"

    def dispatch(self, request, *args, **kwargs):
        self.can_read_release_tasks = user_has_permission(
            request.user, "releases", "read"
        )
        self.can_read_node_tasks = user_has_permission(request.user, "nodes", "update")
        if not (self.can_read_release_tasks or self.can_read_node_tasks):
            return forbidden_response(request, "当前账号无权限访问该功能")
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.can_read_release_tasks:
            return queryset
        return queryset.filter(
            operation_type="node_batch_test", trigger_user=self.request.user
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        task = self.object

        result_text = (task.result or "").strip()
        success_lines = []
        failed_lines = []
        other_lines = []

        if result_text:
            current_group = None
            for raw in result_text.splitlines():
                stripped = raw.strip()
                if not stripped:
                    continue

                if stripped.startswith("[节点] "):
                    other_lines.append(stripped)
                    current_group = "node"
                elif raw.startswith("  [") and current_group:
                    if current_group == "success" and success_lines:
                        success_lines[-1] = success_lines[-1] + "\n" + stripped
                    elif current_group == "failed" and failed_lines:
                        failed_lines[-1] = failed_lines[-1] + "\n" + stripped
                    elif current_group == "node" and other_lines:
                        other_lines[-1] = other_lines[-1] + "\n" + stripped
                elif stripped.startswith("[成功]"):
                    success_lines.append(stripped)
                    current_group = "success"
                elif stripped.startswith("[失败]"):
                    failed_lines.append(stripped)
                    current_group = "failed"
                else:
                    other_lines.append(stripped)

        context["task_result_groups"] = {
            "success": success_lines,
            "failed": failed_lines,
            "other": other_lines,
        }
        context["has_grouped_result"] = bool(
            success_lines or failed_lines or other_lines
        )

        # Parse tree-structured result (for release tasks)
        result_tree = []
        if result_text:
            current_node = None
            for raw in result_text.splitlines():
                stripped = raw.strip()
                if stripped.startswith("[节点] "):
                    node_text = stripped[len("[节点] ") :]
                    node_match = re.match(r"(.+?)\s+\((.+?)\)", node_text)
                    current_node = {
                        "node": node_text,
                        "ip": node_match.group(1) if node_match else "",
                        "hostname": node_match.group(2) if node_match else "",
                        "configs": [],
                    }
                    result_tree.append(current_node)
                elif raw.startswith("  [") and current_node is not None:
                    if stripped.startswith("[成功] "):
                        status = "success"
                        rest = stripped[len("[成功] ") :]
                    elif stripped.startswith("[失败] "):
                        status = "failed"
                        rest = stripped[len("[失败] ") :]
                    else:
                        continue
                    cfg_name = rest.split(" v")[0] if " v" in rest else rest
                    current_node["configs"].append(
                        {
                            "name": rest,
                            "raw_name": cfg_name,
                            "status": status,
                        }
                    )
        context["result_tree"] = result_tree
        context["has_result_tree"] = len(result_tree) > 0

        tree_success = 0
        tree_failed = 0
        for node in result_tree:
            for cfg in node["configs"]:
                if cfg["status"] == "success":
                    tree_success += 1
                else:
                    tree_failed += 1
        context["tree_summary"] = {
            "success": tree_success,
            "failed": tree_failed,
            "other": 0,
        }

        detail_text = (task.detail or "").strip()
        detail_summary = {"raw": detail_text, "kind": "raw"}

        done_match = re.search(
            r"执行完成：成功\s*(\d+)，失败\s*(\d+)，共\s*(\d+)(?:，新增\s*(\d+))?(?:，更新\s*(\d+))?",
            detail_text,
        )
        if done_match:
            detail_summary["kind"] = "done"
            detail_summary["success"] = int(done_match.group(1))
            detail_summary["failed"] = int(done_match.group(2))
            detail_summary["total"] = int(done_match.group(3))
            detail_summary["created"] = (
                int(done_match.group(4)) if done_match.group(4) else None
            )
            detail_summary["updated"] = (
                int(done_match.group(5)) if done_match.group(5) else None
            )
        else:
            running_match = re.search(
                r"执行中：成功\s*(\d+)，失败\s*(\d+)，已完成\s*(\d+)/(\d+)",
                detail_text,
            )
            if running_match:
                detail_summary["kind"] = "running"
                detail_summary["success"] = int(running_match.group(1))
                detail_summary["failed"] = int(running_match.group(2))
                detail_summary["done"] = int(running_match.group(3))
                detail_summary["total"] = int(running_match.group(4))
            else:
                detail_summary["kind"] = "raw"

        context["detail_summary"] = detail_summary

        node_config_map = {}
        if task.source_batch and task.operation_type in (
            "release_publish",
            "release_rollback",
        ):
            from apps.releases.models import ReleaseTask as RT

            release_tasks = (
                RT.objects.filter(batch_number=task.source_batch)
                .select_related("node", "config")
                .order_by("node__hostname", "config__name")
            )
            for rt in release_tasks:
                node_key = f"{rt.node.ip} ({rt.node.hostname})"
                if node_key not in node_config_map:
                    node_config_map[node_key] = []
                if rt.config.name not in node_config_map[node_key]:
                    node_config_map[node_key].append(rt.config.name)

        context["node_config_map"] = node_config_map
        return context


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
            .select_related("node", "config", "version", "operator")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["histories"] = self.object.history.all().select_related(
            "node", "config", "operator"
        )
        context["search"] = self.request.GET.get("search", "")
        context["status_filter"] = self.request.GET.get("status_filter", "")
        context["history_search"] = self.request.GET.get("history_search", "")
        context["history_status"] = self.request.GET.get("history_status", "")
        return context


class ReleaseRollbackView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_resource = "releases"
    permission_action = "update"

    def get(self, request, pk):
        from django.core.paginator import Paginator

        task = get_object_or_404(
            ReleaseTask.objects.select_related("node", "config", "version", "operator"),
            pk=pk,
        )
        config = task.config
        versions = (
            ConfigVersion.objects.filter(config=config)
            .select_related("created_by")
            .order_by("-version")
        )
        paginator = Paginator(versions, 15)
        page_number = request.GET.get("page", 1)
        page_obj = paginator.get_page(page_number)

        return render(
            request,
            "releases/rollback.html",
            {
                "task": task,
                "config": config,
                "page_obj": page_obj,
            },
        )

    def post(self, request, pk):
        task = get_object_or_404(
            ReleaseTask.objects.select_related("node", "config", "version", "operator"),
            pk=pk,
        )
        if task.node.is_locked:
            messages.error(request, f"节点 {task.node.hostname} 已锁定，无法回滚")
            return redirect("releases:center")
        version_id = request.POST.get("version_id")
        if not version_id:
            messages.error(request, "请选择要回滚的版本")
            return redirect("releases:rollback", pk=task.pk)

        version = get_object_or_404(ConfigVersion, pk=version_id, config=task.config)

        new_task = ReleaseTask.objects.create(
            node=task.node,
            config=task.config,
            version=version,
            operator=request.user,
            status="pending",
            batch_number=generate_batch_number(),
        )

        messages.success(
            request,
            f"回滚任务已创建，批次号: {new_task.batch_number}，请在发布中心确认后执行",
        )
        return redirect("releases:center")


class ReleaseCenterView(
    LoginRequiredMixin, PermissionRequiredMixin, PerPagePaginationMixin, ListView
):
    model = ReleaseTask
    template_name = "releases/center.html"
    context_object_name = "tasks"
    paginate_by = 10
    ordering = ["-created_at"]
    permission_resource = "releases"
    permission_action = "read"

    def get_queryset(self):
        queryset = (
            super()
            .get_queryset()
            .select_related("node", "config", "version", "operator")
            .filter(status__in=["pending", "running"])
        )
        search = self.request.GET.get("search", "").strip()
        status_filter = self.request.GET.get("status", "")

        if search:
            terms = [
                t.strip() for t in search.replace("，", ",").split(",") if t.strip()
            ]
            if terms:
                for term in terms:
                    queryset = queryset.filter(
                        Q(batch_number__icontains=term)
                        | Q(config__name__icontains=term)
                        | Q(node__hostname__icontains=term)
                    )
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        return queryset

    def get_context_data(self, **kwargs):
        from collections import OrderedDict
        from django.core.paginator import Paginator

        context = super().get_context_data(**kwargs)
        search = self.request.GET.get("search", "")
        status_filter = self.request.GET.get("status", "")

        context["search"] = search
        context["status_filter"] = status_filter
        context["status_choices"] = ReleaseTask.STATUS_CHOICES

        batch_tasks = OrderedDict()
        for task in context["tasks"]:
            key = task.batch_number or f"task-{task.id}"
            batch_tasks.setdefault(key, []).append(task)

        context["batch_tasks"] = batch_tasks
        context["has_any_filter"] = bool(search or status_filter)

        recent_history = (
            ReleaseTask.objects.select_related("node", "config", "version", "operator")
            .exclude(status__in=["pending", "running"])
            .order_by("-created_at")
        )

        history_search = self.request.GET.get("history_search", "")
        history_status = self.request.GET.get("history_status", "")
        if history_search:
            terms = [
                t.strip()
                for t in history_search.replace("，", ",").split(",")
                if t.strip()
            ]
            if terms:
                for term in terms:
                    recent_history = recent_history.filter(
                        Q(batch_number__icontains=term)
                        | Q(config__name__icontains=term)
                        | Q(node__hostname__icontains=term)
                    )
        if history_status:
            recent_history = recent_history.filter(status=history_status)

        paginator = Paginator(recent_history, 10)
        history_page = paginator.get_page(self.request.GET.get("history_page", 1))
        context["history_page"] = history_page
        context["history_search"] = history_search
        context["history_status"] = history_status
        context["is_history_paginated"] = history_page.has_other_pages()

        return context


def _run_release_tasks(task_ids, task_center_id=None):
    executor = ReleaseExecutorMixin()
    total = len(task_ids)
    success = 0
    failed = 0
    detail_lines = []

    if task_center_id:
        TaskCenterTask.objects.filter(pk=task_center_id).update(
            status="running",
            started_at=timezone.now(),
            progress=0,
        )

    # Group by node (ip + hostname)
    node_tasks = {}
    for task_id in task_ids:
        try:
            task = ReleaseTask.objects.select_related(
                "node", "config", "version", "operator"
            ).get(pk=task_id)
            node_key = f"{task.node.ip} ({task.node.hostname})"
            if node_key not in node_tasks:
                node_tasks[node_key] = []
            node_tasks[node_key].append(task)
        except ReleaseTask.DoesNotExist:
            failed += 1
            detail_lines.append(f"[失败] 任务#{task_id} 不存在")

    for node_key, tasks in node_tasks.items():
        detail_lines.append(f"[节点] {node_key}")
        for task in tasks:
            ok, _ = executor._execute_release(task, "publish")
            if ok:
                success += 1
                detail_lines.append(
                    f"  [成功] {task.config.name} v{task.version.version}"
                )
            else:
                failed += 1
                reason = (task.result or "").split("\n")[-1]
                detail_lines.append(
                    f"  [失败] {task.config.name} v{task.version.version} - {reason}"
                )

            if task_center_id:
                done = success + failed
                TaskCenterTask.objects.filter(pk=task_center_id).update(
                    progress=int(done * 100 / total) if total else 100,
                    detail=f"执行中：成功 {success}，失败 {failed}，共 {total}",
                    updated_at=timezone.now(),
                )

    if task_center_id:
        status = "success" if failed == 0 else "failed"
        TaskCenterTask.objects.filter(pk=task_center_id).update(
            status=status,
            progress=100,
            finished_at=timezone.now(),
            result="\n".join(
                [f"执行完成：成功 {success}，失败 {failed}，共 {total}"] + detail_lines
            ),
            detail=f"执行完成：成功 {success}，失败 {failed}，共 {total}",
        )


class TaskCenterProgressAPIView(LoginRequiredMixin, View):
    def dispatch(self, request, *args, **kwargs):
        self.can_read_release_tasks = user_has_permission(
            request.user, "releases", "read"
        )
        self.can_read_node_tasks = user_has_permission(request.user, "nodes", "update")
        if not (self.can_read_release_tasks or self.can_read_node_tasks):
            return forbidden_response(request, "当前账号无权限访问该功能")
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        ids_raw = request.GET.get("ids", "")
        id_list = [int(i) for i in ids_raw.split(",") if i.strip().isdigit()]
        if not id_list:
            return JsonResponse({"success": True, "tasks": []})

        tasks = TaskCenterTask.objects.filter(id__in=id_list).order_by("-created_at")
        if not self.can_read_release_tasks:
            tasks = tasks.filter(
                operation_type="node_batch_test", trigger_user=request.user
            )
        data = [
            {
                "id": t.id,
                "status": t.status,
                "progress": t.progress,
                "detail": t.detail,
                "result": t.result,
                "finished": t.status in ["success", "failed", "cancelled"],
            }
            for t in tasks
        ]
        return JsonResponse({"success": True, "tasks": data})


class ReleaseCenterExecuteView(
    LoginRequiredMixin, PermissionRequiredMixin, ReleaseExecutorMixin, View
):
    permission_resource = "releases"
    permission_action = "update"

    def post(self, request, batch_number):
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
        try:
            if ReleaseTask.objects.filter(status="running").exists():
                msg = "当前有批次正在执行中，请等待完成后再执行"
                if is_ajax:
                    return JsonResponse({"success": False, "message": msg})
                messages.error(request, msg)
                return redirect("releases:center")

            tasks = ReleaseTask.objects.filter(
                batch_number=batch_number,
                status__in=["pending"],
            ).select_related("node", "config", "version", "operator")

            if not tasks.exists():
                msg = f"批次 {batch_number} 没有可执行的任务"
                if is_ajax:
                    return JsonResponse({"success": False, "message": msg})
                messages.error(request, msg)
                return redirect("releases:center")

            task_ids = list(tasks.values_list("id", flat=True))

            task_center = TaskCenterTask.objects.create(
                operation_type="release_publish",
                status="pending",
                detail=f"待执行任务 {len(task_ids)} 个",
                source_batch=batch_number,
                target_hostnames=",".join(t.node.hostname for t in tasks),
                target_ips=",".join(t.node.ip for t in tasks),
                target_configs=",".join(sorted({t.config.name for t in tasks})),
                trigger_user=request.user,
            )

            thread = threading.Thread(
                target=_run_release_tasks,
                args=(task_ids, task_center.id),
                daemon=True,
            )
            thread.start()

            if is_ajax:
                return JsonResponse(
                    {
                        "success": True,
                        "message": f"批次 {batch_number} 开始异步执行，共 {len(task_ids)} 个任务",
                        "task_ids": task_ids,
                        "task_center_id": task_center.id,
                        "task_center_detail_url": reverse(
                            "releases:task_center_detail", args=[task_center.id]
                        ),
                        "task_center_home_url": reverse("releases:history"),
                        "async": True,
                    }
                )
            messages.info(
                request, f"批次 {batch_number} 开始异步执行，共 {len(task_ids)} 个任务"
            )
            return redirect("releases:center")
        except OperationalError:
            logger.exception("Batch execute failed due to database operation error")
            msg = "执行失败：数据库结构可能未同步，请先执行 migrate"
            if is_ajax:
                return JsonResponse({"success": False, "message": msg}, status=500)
            messages.error(request, msg)
            return redirect("releases:center")
        except Exception as exc:
            logger.exception("Batch execute failed unexpectedly")
            msg = f"执行失败：{exc}"
            if is_ajax:
                return JsonResponse({"success": False, "message": msg}, status=500)
            messages.error(request, msg)
            return redirect("releases:center")


class ReleaseCenterSingleExecuteView(
    LoginRequiredMixin, PermissionRequiredMixin, ReleaseExecutorMixin, View
):
    permission_resource = "releases"
    permission_action = "update"

    def post(self, request, task_id):
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
        try:
            if ReleaseTask.objects.filter(status="running").exists():
                msg = "当前有批次正在执行中，请等待完成后再执行"
                if is_ajax:
                    return JsonResponse({"success": False, "message": msg})
                messages.error(request, msg)
                return redirect("releases:center")

            task = get_object_or_404(
                ReleaseTask.objects.select_related(
                    "node", "config", "version", "operator"
                ),
                pk=task_id,
                status__in=["pending", "failed"],
            )

            task_center = TaskCenterTask.objects.create(
                operation_type="release_publish",
                status="pending",
                detail=f"节点 {task.node.hostname} / 配置 {task.config.name}",
                source_batch=task.batch_number or "",
                target_hostnames=task.node.hostname,
                target_ips=task.node.ip,
                target_configs=task.config.name,
                trigger_user=request.user,
            )
            thread = threading.Thread(
                target=_run_release_tasks,
                args=([task_id], task_center.id),
                daemon=True,
            )
            thread.start()

            if is_ajax:
                return JsonResponse(
                    {
                        "success": True,
                        "message": "任务开始异步执行",
                        "task_id": task.id,
                        "task_center_id": task_center.id,
                        "task_center_detail_url": reverse(
                            "releases:task_center_detail", args=[task_center.id]
                        ),
                        "task_center_home_url": reverse("releases:history"),
                        "async": True,
                    }
                )
            messages.info(request, "任务开始异步执行")
            return redirect("releases:center")
        except OperationalError:
            logger.exception("Single execute failed due to database operation error")
            msg = "执行失败：数据库结构可能未同步，请先执行 migrate"
            if is_ajax:
                return JsonResponse({"success": False, "message": msg}, status=500)
            messages.error(request, msg)
            return redirect("releases:center")
        except Exception as exc:
            logger.exception("Single execute failed unexpectedly")
            msg = f"执行失败：{exc}"
            if is_ajax:
                return JsonResponse({"success": False, "message": msg}, status=500)
            messages.error(request, msg)
            return redirect("releases:center")


class ReleaseTaskStatusView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_resource = "releases"
    permission_action = "read"

    def get(self, request, task_id):
        task = get_object_or_404(
            ReleaseTask.objects.select_related("node", "config", "version", "operator"),
            pk=task_id,
        )
        return JsonResponse(
            {
                "task_id": task.id,
                "status": task.status,
                "result": task.result,
                "node": task.node.hostname,
                "config": task.config.name,
                "finished": task.status not in ("pending", "running"),
            }
        )


class ReleaseCenterCancelView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_resource = "releases"
    permission_action = "update"

    def post(self, request, batch_number):
        updated = ReleaseTask.objects.filter(
            batch_number=batch_number,
            status__in=["pending"],
        ).update(status="cancelled")

        if updated:
            messages.success(
                request,
                f"批次 {batch_number} 已取消（{updated} 个任务）",
            )
        else:
            messages.warning(request, f"批次 {batch_number} 没有可取消的任务")
        return redirect("releases:center")


class VersionContentAPIView(LoginRequiredMixin, View):
    def get(self, request, version_id):
        version = get_object_or_404(
            ConfigVersion.objects.select_related("config", "created_by"),
            pk=version_id,
        )
        return JsonResponse(
            {
                "success": True,
                "data": {
                    "version_id": version.id,
                    "version_number": version.version,
                    "version_label": version.version_label,
                    "config_name": version.config.name,
                    "config_path": version.config.file_path,
                    "content": version.content,
                    "content_bytes": version.content_bytes,
                    "remark": version.remark or "",
                    "created_at": version.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                    "created_by": version.created_by.username,
                },
            }
        )
