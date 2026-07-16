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
from django.views.generic import ListView, DetailView, View

from apps.nodes.views import _get_node_credential
from apps.nodes.models import Node
from apps.configs.models import Config, ConfigNodeBinding, BindingVersion
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

from .models import ReleaseTask, ReleaseHistory, TaskCenterTask, generate_batch_number
from utils.pagination import PerPagePaginationMixin

logger = logging.getLogger(__name__)


class ReleaseExecutorMixin:
    """发布执行核心逻辑 - 适配 ConfigNodeBinding"""

    def _execute_release(self, task, action):
        node = task.node
        config = task.config

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

        # 获取发布内容：优先从 BindingVersion 读取
        content = task.content_to_publish if task.content_to_publish else ""
        remote_path = task.remote_path or (task.binding.remote_path if task.binding else "")

        if not remote_path:
            task.status = "failed"
            task.result = "未指定远程路径"
            task.finished_at = datetime.now()
            task.save()
            self._record_history(task, action, task.result)
            return False, "未指定远程路径"

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

        # SSH 连接预检
        add_log("正在测试 SSH 连接...")
        from utils.ssh import test_ssh_connection
        conn_ok, conn_msg = test_ssh_connection(**kwargs)
        if not conn_ok:
            add_log(f"SSH 连接失败: {conn_msg}")
            task.status = "failed"
            task.result = "\n".join(log_lines)
            task.finished_at = datetime.now()
            task.save()
            self._record_history(task, action, task.result)
            return False, f"SSH 连接失败: {conn_msg}"
        add_log("SSH 连接测试通过 ✓")

        version_label = f"v{task.publish_version}" if task.publish_version else "latest"
        add_log(f"开始发布: {config.name} {version_label} → {node.hostname}")
        add_log(f"目标路径: {remote_path}")

        if not content or not content.strip():
            add_log("配置内容为空，中止发布")
            task.status = "failed"
            task.result = "\n".join(log_lines)
            task.finished_at = datetime.now()
            task.save()
            self._record_history(task, action, task.result)
            return False, f"配置 {config.name} {version_label} 内容为空，无法发布"

        # 备份
        add_log("正在备份原配置...")
        success, backup_result = backup_remote_file(file_path=remote_path, **kwargs)
        if success:
            add_log(f"备份成功: {backup_result}")
            backup_size_ok, backup_size_msg = check_remote_file_size(file_path=backup_result, **kwargs)
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

        # 上传到 /tmp
        add_log("正在上传配置到 /tmp ...")
        tmp_path = f"/tmp/{remote_path.split('/')[-1]}.mngxops_tmp"
        success, upload_result = upload_file_via_sftp(remote_path=tmp_path, content=content, **kwargs)
        if not success:
            add_log(f"上传到 /tmp 失败: {upload_result}")
            task.status = "failed"
            task.result = "\n".join(log_lines)
            task.finished_at = datetime.now()
            task.save()
            self._record_history(task, action, task.result)
            return False, f"上传到 /tmp 失败: {upload_result}"

        add_log(f"已上传到 {tmp_path}，检查文件大小...")
        size_ok, size_msg = check_remote_file_size(file_path=tmp_path, **kwargs)
        add_log(f"/tmp 文件大小: {size_msg}")
        if not size_ok:
            add_log("/tmp 文件为空，中止发布")
            task.status = "failed"
            task.result = "\n".join(log_lines)
            task.finished_at = datetime.now()
            task.save()
            self._record_history(task, action, task.result)
            return False, f"/tmp 文件为空: {size_msg}"

        # 复制到目标路径
        add_log(f"从 /tmp 复制到目标路径 {remote_path} ...")
        copy_ok, copy_msg = copy_remote_file(src_path=tmp_path, dst_path=remote_path, **kwargs)
        if not copy_ok:
            add_log(f"复制失败: {copy_msg}")
            add_log("正在回滚备份...")
            self._rollback_backup(backup_result, remote_path, kwargs, log_lines)
            task.status = "failed"
            task.result = "\n".join(log_lines)
            task.finished_at = datetime.now()
            task.save()
            self._record_history(task, action, task.result)
            return False, f"复制失败: {copy_msg}"

        # 校验
        add_log("验证目标文件大小...")
        target_ok, target_msg = check_remote_file_size(file_path=remote_path, **kwargs)
        add_log(f"目标文件大小: {target_msg}")
        add_log("校验文件 md5...")
        tmp_md5_ok, tmp_md5 = check_remote_file_md5(file_path=tmp_path, **kwargs)
        target_md5_ok, target_md5 = check_remote_file_md5(file_path=remote_path, **kwargs)
        add_log(f"/tmp md5: {tmp_md5}")
        add_log(f"目标 md5: {target_md5}")
        if tmp_md5_ok and target_md5_ok and tmp_md5 == target_md5:
            add_log("md5 一致 ✓")
        else:
            add_log("md5 不一致 ✗")

        if not target_ok:
            add_log("目标文件为空，正在回滚备份...")
            self._rollback_backup(backup_result, remote_path, kwargs, log_lines)
            task.status = "failed"
            task.result = "\n".join(log_lines)
            task.finished_at = datetime.now()
            task.save()
            self._record_history(task, action, task.result)
            return False, f"目标文件为空: {target_msg}"

        add_log("上传成功")

        # nginx -t
        add_log("正在执行 nginx -t ...")
        nginx_path = node.nginx_path or None
        success, test_output = execute_nginx_test(config_path=remote_path, nginx_path=nginx_path, **kwargs)
        add_log(test_output)
        if not success:
            add_log("nginx -t 失败，正在回滚备份...")
            self._rollback_backup(backup_result, remote_path, kwargs, log_lines)
            task.status = "failed"
            task.result = "\n".join(log_lines)
            task.finished_at = datetime.now()
            task.save()
            self._record_history(task, action, task.result)
            return False, f"nginx -t 失败: {test_output}"

        # reload
        add_log("nginx -t 通过，正在执行 reload...")
        success, reload_output = execute_nginx_reload(nginx_path=nginx_path, **kwargs)
        add_log(reload_output)
        if success:
            add_log("发布成功!")
            task.status = "success"
            # 回写绑定状态
            self._on_release_success(task, target_md5)
        else:
            add_log("reload 失败，正在回滚备份...")
            self._rollback_backup(backup_result, remote_path, kwargs, log_lines)
            task.status = "failed"

        task.result = "\n".join(log_lines)
        task.finished_at = datetime.now()
        task.save()
        self._record_history(task, action, task.result)
        return True, f"配置 {config.name} {version_label} 发布到 {node.hostname} 成功"

    def _on_release_success(self, task, remote_md5):
        """发布成功后回写绑定状态"""
        binding = task.binding
        if not binding:
            return
        binding.synced_version = task.publish_version or binding.current_version
        binding.remote_content_hash = remote_md5
        binding.sync_status = "synced"
        binding.last_sync_time = timezone.now()
        binding.save(update_fields=[
            "synced_version", "remote_content_hash", "sync_status", "last_sync_time",
        ])

    def _record_history(self, task, action, result):
        ReleaseHistory.objects.create(
            release_task=task,
            node=task.node,
            config=task.config,
            version=task.publish_version or 0,
            operator=task.operator,
            action=action,
            result=result,
        )

    def _rollback_backup(self, backup_result, config_file_path, kwargs, log_lines):
        backup_size_ok, backup_size_msg = check_remote_file_size(file_path=backup_result, **kwargs)
        if not backup_size_ok:
            log_lines.append("警告: 备份文件为空，跳过回滚")
            return
        rollback_ok, rollback_msg = restore_backup_file(
            backup_path=backup_result, original_path=config_file_path, **kwargs,
        )
        if rollback_ok:
            log_lines.append("回滚完成")
        else:
            log_lines.append(f"回滚失败: {rollback_msg}")


class ReleaseCreateAPIView(LoginRequiredMixin, PermissionRequiredMixin, ReleaseExecutorMixin, View):
    """发布任务创建 API — 处理 JSON 格式的发布任务创建请求"""
    permission_resource = "releases"
    permission_action = "create"

    def post(self, request):
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"success": False, "message": "请求数据格式错误"}, status=400)

        bindings_data = data.get("bindings", [])
        auto_execute = data.get("auto_execute", False)

        if not bindings_data:
            return JsonResponse({"success": False, "message": "请至少选择一个配置绑定"}, status=400)

        batch_number = generate_batch_number()
        task_ids = []

        for item in bindings_data:
            binding_id = item.get("binding_id", 0)
            version = item.get("version")

            try:
                binding = ConfigNodeBinding.objects.select_related("node", "config").get(pk=binding_id)
            except ConfigNodeBinding.DoesNotExist:
                continue

            if binding.node.is_locked:
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
            return JsonResponse({"success": False, "message": "未找到可发布的配置绑定"}, status=400)

        response_data = {
            "success": True,
            "batch_number": batch_number,
            "task_count": len(task_ids),
            "message": f"发布任务已创建，批次号: {batch_number}，共 {len(task_ids)} 个任务",
        }

        if auto_execute:
            parallel = data.get("parallel", False)
            max_workers = 1
            if parallel:
                from utils.setting_service import get_setting
                try:
                    max_workers = int(get_setting("release.max_parallel_tasks", "3"))
                except (ValueError, TypeError):
                    max_workers = 3

            task_center = TaskCenterTask.objects.create(
                operation_type="release_publish",
                status="running",
                source_batch=batch_number,
                detail=f"执行中：成功 0，失败 0，共 {len(task_ids)}",
                progress=0,
                started_at=timezone.now(),
                trigger_user=request.user,
            )

            target_func = _run_release_tasks_parallel if parallel and max_workers > 1 else _run_release_tasks
            thread = threading.Thread(
                target=target_func,
                args=(task_ids, task_center.id) if target_func == _run_release_tasks else (task_ids, task_center.id, max_workers),
                daemon=True,
            )
            thread.start()

            response_data["task_center_id"] = task_center.id
            response_data["async"] = True

        return JsonResponse(response_data)


class ReleaseListView(
    LoginRequiredMixin, PermissionRequiredMixin, PerPagePaginationMixin, ListView
):
    """发布历史 - 按批次→节点→配置三级折叠展示"""
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
            .select_related("node", "config", "binding", "operator")
        )
        search = self.request.GET.get("search", "")
        status_filter = self.request.GET.get("status", "")
        if search:
            queryset = queryset.filter(
                Q(config__name__icontains=search)
                | Q(node__hostname__icontains=search)
                | Q(batch_number__icontains=search)
                | Q(operator__username__icontains=search)
            )
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        return queryset

    def get_context_data(self, **kwargs):
        from collections import OrderedDict
        context = super().get_context_data(**kwargs)
        search = self.request.GET.get("search", "")
        status_filter = self.request.GET.get("status", "")
        context["search"] = search
        context["status_filter"] = status_filter
        context["status_choices"] = ReleaseTask.STATUS_CHOICES

        # 三级分组：批次 → 节点 → 任务
        batch_groups = OrderedDict()
        for task in context["tasks"]:
            batch_key = task.batch_number or f"task-{task.id}"
            if batch_key not in batch_groups:
                batch_groups[batch_key] = {
                    "batch_number": task.batch_number,
                    "created_at": task.created_at,
                    "operator": task.operator.username,
                    "total": 0,
                    "success": 0,
                    "failed": 0,
                    "nodes": OrderedDict(),
                }
            batch = batch_groups[batch_key]
            node_key = task.node_id
            if node_key not in batch["nodes"]:
                batch["nodes"][node_key] = {
                    "node": task.node,
                    "tasks": [],
                }
            batch["nodes"][node_key]["tasks"].append(task)
            batch["total"] += 1
            if task.status == "success":
                batch["success"] += 1
            elif task.status == "failed":
                batch["failed"] += 1

        context["batch_groups"] = batch_groups
        context["has_any_filter"] = bool(search or status_filter)
        return context


class TaskCenterListView(LoginRequiredMixin, PerPagePaginationMixin, ListView):
    model = TaskCenterTask
    template_name = "releases/task_center.html"
    context_object_name = "tasks"
    paginate_by = 15
    ordering = ["-created_at"]

    def dispatch(self, request, *args, **kwargs):
        self.can_read_release_tasks = user_has_permission(request.user, "releases", "read")
        self.can_read_node_tasks = user_has_permission(request.user, "nodes", "update")
        if not (self.can_read_release_tasks or self.can_read_node_tasks):
            return forbidden_response(request, "当前账号无权限访问该功能")
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        queryset = super().get_queryset().select_related("trigger_user")
        if not self.can_read_release_tasks:
            queryset = queryset.filter(
                operation_type="node_batch_test", trigger_user=self.request.user,
            )
        search = self.request.GET.get("search", "")
        status_filter = self.request.GET.get("status", "")
        operation_type = self.request.GET.get("operation_type", "")
        if search:
            tags = [t.strip() for t in search.replace("，", ",").split(",") if t.strip()]
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
            context["search"] or context["status_filter"] or context["operation_type_filter"]
        )
        return context


class TaskCenterDetailView(LoginRequiredMixin, DetailView):
    """任务中心详情 - 按节点→配置树形展示执行结果"""
    model = TaskCenterTask
    template_name = "releases/task_detail.html"
    context_object_name = "task"

    def dispatch(self, request, *args, **kwargs):
        self.can_read_release_tasks = user_has_permission(request.user, "releases", "read")
        self.can_read_node_tasks = user_has_permission(request.user, "nodes", "update")
        if not (self.can_read_release_tasks or self.can_read_node_tasks):
            return forbidden_response(request, "当前账号无权限访问该功能")
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.can_read_release_tasks:
            return queryset
        return queryset.filter(
            operation_type__in=["node_batch_test", "config_batch_sync"],
            trigger_user=self.request.user,
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        task = self.object
        result_text = (task.result or "").strip()

        # 解析目标节点和目标配置
        target_nodes = []
        target_configs = []
        if task.target_hostnames:
            ips = (task.target_ips or "").split(",")
            hostnames = task.target_hostnames.split(",")
            seen = set()
            for i in range(min(len(ips), len(hostnames))):
                ip = ips[i].strip()
                if ip and ip not in seen:
                    seen.add(ip)
                    hn = hostnames[i].strip() if i < len(hostnames) else ""
                    target_nodes.append(f"{ip} ({hn})" if hn else ip)
        if task.target_configs:
            configs_raw = task.target_configs.split(",")
            target_configs = [c.strip() for c in configs_raw if c.strip()]
            seen_c = set()
            target_configs = [c for c in target_configs if not (c in seen_c or seen_c.add(c))]

        context["target_nodes"] = target_nodes[:50]
        context["target_configs"] = target_configs[:50]
        context["target_configs_count"] = len(target_configs)

        # 解析结果树（按节点分组 + 成功/失败配置）
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
                    node_text = stripped[len("[节点] "):]
                    if current_node:
                        result_tree.append(current_node)
                    current_node = {
                        "name": node_text,
                        "configs": [],
                        "success": 0,
                        "failed": 0,
                    }
                elif raw.startswith("  [成功]") and current_node is not None:
                    name = raw[len("  [成功] "):].strip()
                    name = re.sub(r'\s+v\d+.*', '', name)
                    current_node["configs"].append({"name": name, "status": "success"})
                    current_node["success"] += 1
                    success_total += 1
                elif raw.startswith("  [失败]") and current_node is not None:
                    name = raw[len("  [失败] "):].strip()
                    name = re.sub(r'\s+v\d+.*', '', name)
                    current_node["configs"].append({"name": name, "status": "failed"})
                    current_node["failed"] += 1
                    failed_total += 1

            if current_node:
                result_tree.append(current_node)

        # 失败节点排前面
        result_tree.sort(key=lambda n: (n["failed"] == 0, n["name"]))

        # 计算执行耗时
        duration = ""
        if task.started_at and task.finished_at:
            delta = (task.finished_at - task.started_at).total_seconds()
            if delta >= 60:
                duration = f"{delta / 60:.1f} 分钟"
            else:
                duration = f"{delta:.1f} 秒"

        context["result_tree"] = result_tree
        context["result_summary"] = {"success": success_total, "failed": failed_total}
        context["execution_duration"] = duration
        return context


class ReleaseDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    model = ReleaseTask
    template_name = "releases/detail.html"
    context_object_name = "task"
    permission_resource = "releases"
    permission_action = "read"

    def get_queryset(self):
        return super().get_queryset().select_related("node", "config", "binding", "operator")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["histories"] = self.object.history.all().select_related("node", "config", "operator")
        return context


class ReleaseRollbackView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_resource = "releases"
    permission_action = "update"

    def get(self, request, pk):
        from django.core.paginator import Paginator
        task = get_object_or_404(
            ReleaseTask.objects.select_related("node", "config", "binding", "operator"), pk=pk,
        )
        binding = task.binding
        versions = []
        if binding:
            versions = binding.versions.select_related("created_by").order_by("-version")
        paginator = Paginator(versions, 15)
        page_number = request.GET.get("page", 1)
        page_obj = paginator.get_page(page_number)
        return render(request, "releases/rollback.html", {
            "task": task, "config": task.config, "page_obj": page_obj,
        })

    def post(self, request, pk):
        task = get_object_or_404(
            ReleaseTask.objects.select_related("node", "config", "binding", "operator"), pk=pk,
        )
        if task.node.is_locked:
            messages.error(request, f"节点 {task.node.hostname} 已锁定，无法回滚")
            return redirect("releases:center")

        version_id = request.POST.get("version_id")
        if not version_id:
            messages.error(request, "请选择要回滚的版本")
            return redirect("releases:rollback", pk=task.pk)

        version = get_object_or_404(BindingVersion, pk=version_id, binding=task.binding)
        new_task = ReleaseTask.objects.create(
            binding=task.binding,
            node=task.node,
            config=task.config,
            version=version,
            publish_version=version.version,
            remote_path=task.remote_path or (task.binding.remote_path if task.binding else ""),
            operator=request.user,
            status="pending",
            batch_number=generate_batch_number(),
        )
        messages.success(request, f"回滚任务已创建，批次号: {new_task.batch_number}")
        return redirect("releases:center")


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
        return context


def _run_release_tasks(task_ids, task_center_id=None):
    """异步执行发布任务（顺序模式），按节点分组输出结构化日志"""
    executor = ReleaseExecutorMixin()
    total = len(task_ids)
    success = 0
    failed = 0
    detail_lines = []
    node_results = {}

    if task_center_id:
        TaskCenterTask.objects.filter(pk=task_center_id).update(
            status="running", started_at=timezone.now(), progress=0,
        )

    node_tasks = {}
    for task_id in task_ids:
        try:
            task = ReleaseTask.objects.select_related(
                "node", "config", "binding", "operator",
            ).get(pk=task_id)
            node_key = f"{task.node.ip} ({task.node.hostname})"
            if node_key not in node_tasks:
                node_tasks[node_key] = []
            node_tasks[node_key].append(task)
        except ReleaseTask.DoesNotExist:
            failed += 1
            detail_lines.append(f"[失败] 任务#{task_id} 不存在")

    for node_key, tasks in node_tasks.items():
        node_success = 0
        node_failed = 0
        detail_lines.append(f"[节点] {node_key}")

        for task in tasks:
            ok, _ = executor._execute_release(task, "publish")
            if ok:
                success += 1
                node_success += 1
                detail_lines.append(f"  [成功] {task.config.name} v{task.publish_version}")
            else:
                failed += 1
                node_failed += 1
                reason = (task.result or "").split("\n")[-1]
                detail_lines.append(f"  [失败] {task.config.name} v{task.publish_version} - 失败原因: {reason}")

            if task_center_id:
                done = success + failed
                TaskCenterTask.objects.filter(pk=task_center_id).update(
                    progress=int(done * 100 / total) if total else 100,
                    detail=f"执行中：成功 {success}，失败 {failed}，共 {total}",
                    updated_at=timezone.now(),
                )

        node_results[node_key] = {"success": node_success, "failed": node_failed}

    if task_center_id:
        status = "success" if failed == 0 else "failed"
        result_lines = [f"执行完成：成功 {success}，失败 {failed}，共 {total}"]
        for nk, nr in node_results.items():
            result_lines.append(f"[节点摘要] {nk}: 成功 {nr['success']}, 失败 {nr['failed']}")
        result_lines.extend(detail_lines)

        TaskCenterTask.objects.filter(pk=task_center_id).update(
            status=status, progress=100, finished_at=timezone.now(),
            result="\n".join(result_lines),
            detail=f"执行完成：成功 {success}，失败 {failed}，共 {total}",
        )


def _run_release_tasks_parallel(task_ids, task_center_id=None, max_workers=3):
    """并行发布：使用 ThreadPoolExecutor 并行执行多个节点，每节点内逐配置执行"""
    from concurrent.futures import ThreadPoolExecutor

    executor = ReleaseExecutorMixin()
    total = len(task_ids)
    success = 0
    failed = 0
    detail_lines = []
    node_results = {}

    if task_center_id:
        TaskCenterTask.objects.filter(pk=task_center_id).update(
            status="running", started_at=timezone.now(), progress=0,
        )

    node_tasks = {}
    for task_id in task_ids:
        try:
            task = ReleaseTask.objects.select_related(
                "node", "config", "binding", "operator",
            ).get(pk=task_id)
            node_key = f"{task.node.ip} ({task.node.hostname})"
            if node_key not in node_tasks:
                node_tasks[node_key] = []
            node_tasks[node_key].append(task)
        except ReleaseTask.DoesNotExist:
            failed += 1
            detail_lines.append(f"[失败] 任务#{task_id} 不存在")

    def _execute_node(node_key, tasks):
        """执行单个节点的所有配置发布"""
        node_success = 0
        node_failed = 0
        node_lines = []
        node_lines.append(f"[节点] {node_key}")
        for t in tasks:
            ok, _ = executor._execute_release(t, "publish")
            if ok:
                node_success += 1
                node_lines.append(f"  [成功] {t.config.name} v{t.publish_version}")
            else:
                node_failed += 1
                reason = (t.result or "").split("\n")[-1]
                node_lines.append(f"  [失败] {t.config.name} v{t.publish_version} - 失败原因: {reason}")
        return node_key, node_success, node_failed, node_lines

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {}
        for node_key, tasks in node_tasks.items():
            futures[pool.submit(_execute_node, node_key, tasks)] = node_key

        for future in futures:
            try:
                node_key, node_success, node_failed, node_lines = future.result()
                success += node_success
                failed += node_failed
                detail_lines.extend(node_lines)
                node_results[node_key] = {"success": node_success, "failed": node_failed}

                if task_center_id:
                    done = success + failed
                    TaskCenterTask.objects.filter(pk=task_center_id).update(
                        progress=int(done * 100 / total) if total else 100,
                        detail=f"并行执行中：成功 {success}，失败 {failed}，共 {total}",
                        updated_at=timezone.now(),
                    )
            except Exception as e:
                logger.error(f"并行节点执行异常: {e}")

    if task_center_id:
        status = "success" if failed == 0 else "failed"
        result_lines = [f"执行完成（并行模式）：成功 {success}，失败 {failed}，共 {total}"]
        for nk, nr in node_results.items():
            result_lines.append(f"[节点摘要] {nk}: 成功 {nr['success']}, 失败 {nr['failed']}")
        result_lines.extend(detail_lines)

        TaskCenterTask.objects.filter(pk=task_center_id).update(
            status=status, progress=100, finished_at=timezone.now(),
            result="\n".join(result_lines),
            detail=f"执行完成：成功 {success}，失败 {failed}，共 {total}",
        )


class TaskCenterProgressAPIView(LoginRequiredMixin, View):
    def dispatch(self, request, *args, **kwargs):
        self.can_read_release_tasks = user_has_permission(request.user, "releases", "read")
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
            tasks = tasks.filter(operation_type="node_batch_test", trigger_user=request.user)
        data = [
            {
                "id": t.id, "status": t.status, "progress": t.progress,
                "detail": t.detail, "result": t.result,
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
        except OperationalError:
            pass

        tasks_qs = ReleaseTask.objects.filter(
            batch_number=batch_number, status="pending",
        ).select_related("node", "config", "binding", "operator")
        task_ids = list(tasks_qs.values_list("id", flat=True))

        if not task_ids:
            msg = "没有可执行的发布任务"
            if is_ajax:
                return JsonResponse({"success": False, "message": msg})
            messages.error(request, msg)
            return redirect("releases:center")

        # 创建 TaskCenterTask
        task_center = TaskCenterTask.objects.create(
            operation_type="release_publish",
            status="running",
            source_batch=batch_number,
            detail=f"执行中：成功 0，失败 0，共 {len(task_ids)}",
            progress=0,
            started_at=timezone.now(),
            trigger_user=request.user,
        )

        thread = threading.Thread(
            target=_run_release_tasks,
            args=(task_ids, task_center.id),
            daemon=True,
        )
        thread.start()

        redirect_url = reverse("releases:task_center_detail", kwargs={"pk": task_center.id})
        if is_ajax:
            return JsonResponse({
                "success": True,
                "async": True,
                "task_center_id": task_center.id,
                "task_center_detail_url": redirect_url,
            })

        messages.success(request, f"发布任务已开始执行，{len(task_ids)} 个任务，批次号: {batch_number}")
        return redirect(redirect_url)


class ReleaseCenterCancelView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_resource = "releases"
    permission_action = "update"

    def post(self, request, batch_number):
        updated = ReleaseTask.objects.filter(
            batch_number=batch_number, status="pending",
        ).update(status="cancelled", result="用户取消")
        if updated:
            messages.success(request, f"已取消 {updated} 个待执行任务")
        else:
            messages.info(request, "没有待执行的任务")
        return redirect("releases:center")


class ReleaseCenterSingleExecuteView(
    LoginRequiredMixin, PermissionRequiredMixin, ReleaseExecutorMixin, View
):
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

        task_center = TaskCenterTask.objects.create(
            operation_type="release_publish",
            status="running",
            source_batch=task.batch_number,
            detail="执行中...",
            progress=0,
            started_at=timezone.now(),
            trigger_user=request.user,
        )

        thread = threading.Thread(
            target=_run_release_tasks,
            args=([task.id], task_center.id),
            daemon=True,
        )
        thread.start()

        messages.success(request, f"发布任务 #{task_id} 已开始执行")
        return redirect("releases:center")


class ReleaseTaskStatusView(LoginRequiredMixin, View):
    """查询单个任务状态 (Ajax)"""

    def get(self, request, task_id):
        task = get_object_or_404(ReleaseTask, pk=task_id)
        return JsonResponse({
            "id": task.id,
            "status": task.status,
            "result": task.result,
            "finished": task.status in ["success", "failed", "rollback", "cancelled"],
        })


class VersionContentAPIView(LoginRequiredMixin, View):
    """获取版本内容 (Ajax)"""

    def get(self, request, version_id):
        from apps.configs.models import BindingVersion
        version = get_object_or_404(BindingVersion, pk=version_id)
        return JsonResponse({
            "id": version.id,
            "version": version.version,
            "content": version.content,
            "remark": version.remark,
            "created_at": version.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            "created_by": version.created_by.username if version.created_by else "",
        })



def _build_release_status_counts():
    """构建发布中心绑定状态全局统计（含 marked_deleted）"""
    from apps.configs.models import ConfigNodeBinding
    bindings = ConfigNodeBinding.objects.all()
    return {
        "total": bindings.count(),
        "pending": bindings.filter(sync_status__in=["not_synced", "modified"]).count(),
        "synced": bindings.filter(sync_status="synced").count(),
        "conflict": bindings.filter(sync_status="conflict").count(),
        "failed": bindings.filter(sync_status="failed").count(),
        "syncing": bindings.filter(sync_status="syncing").count(),
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
        page = int(request.GET.get("page", 1))
        page_size = int(request.GET.get("page_size", 20))

        queryset = Node.objects.all().prefetch_related("groups")

        if search:
            terms = [t.strip() for t in search.replace("，", ",").split(",") if t.strip()]
            for term in terms:
                queryset = queryset.filter(
                    Q(hostname__icontains=term)
                    | Q(ip__icontains=term)
                    | Q(groups__name__icontains=term)
                    | Q(config_bindings__config__name__icontains=term)
                ).distinct()
        if environment:
            queryset = queryset.filter(environment=environment)
        if node_status:
            queryset = queryset.filter(status=node_status)
        if group_id and group_id.isdigit():
            queryset = queryset.filter(groups__id=int(group_id)).distinct()

        # 按绑定同步状态过滤节点
        if sync_status:
            if sync_status == "pending":
                status_values = ["not_synced", "modified"]
            else:
                status_values = [sync_status]
            node_ids_with_status = (
                ConfigNodeBinding.objects
                .filter(sync_status__in=status_values)
                .values_list("node_id", flat=True)
                .distinct()
            )
            queryset = queryset.filter(id__in=node_ids_with_status)

        total = queryset.count()
        nodes_page = queryset[(page - 1) * page_size: page * page_size]

        node_ids = [n.id for n in nodes_page]
        binding_stats = {}
        if node_ids:
            from django.db.models import Count, Q as DQ
            stats_qs = (
                ConfigNodeBinding.objects
                .filter(node_id__in=node_ids)
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
            stats = binding_stats.get(node.id, {"total_bindings": 0, "modified_bindings": 0})
            node_list.append({
                "id": node.id,
                "hostname": node.hostname,
                "ip": f"{node.ip}:{node.port}",
                "environment": node.environment,
                "status": node.status,
                "is_locked": node.is_locked,
                "has_credential": node.credential_id is not None,
                "total_bindings": stats["total_bindings"],
                "modified_bindings": stats["modified_bindings"],
                "group_names": [g.name for g in node.groups.all()],
            })

        return JsonResponse({
            "success": True,
            "nodes": node_list,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": max(1, (total + page_size - 1) // page_size),
            "status_counts": _build_release_status_counts(),
        })


class ReleaseNodeBindingsAPIView(LoginRequiredMixin, View):
    """获取指定节点的所有绑定详情（含版本列表）"""

    def get(self, request, node_id):
        bindings = (
            ConfigNodeBinding.objects
            .filter(node_id=node_id)
            .select_related("config")
            .order_by("config__name")
        )

        result = []
        for binding in bindings:
            versions = (
                binding.versions
                .order_by("-version")
                .values("id", "version", "created_at")
            )
            result.append({
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
                        "created_at": v["created_at"].strftime("%Y-%m-%d %H:%M:%S") if v["created_at"] else "",
                    }
                    for v in versions
                ],
            })

        return JsonResponse({"success": True, "bindings": result})


class ReleaseRetryView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """重试单条失败的发布任务"""
    permission_resource = "releases"
    permission_action = "update"

    def post(self, request, pk):
        task = get_object_or_404(
            ReleaseTask.objects.select_related("node", "config", "binding", "operator"),
            pk=pk,
        )
        if task.status not in ["failed"]:
            return JsonResponse({"success": False, "message": "只能重试失败的任务"}, status=400)

        if task.node.is_locked:
            return JsonResponse({"success": False, "message": f"节点 {task.node.hostname} 已锁定"}, status=400)

        task.status = "pending"
        task.result = ""
        task.save(update_fields=["status", "result"])

        task_center = TaskCenterTask.objects.create(
            operation_type="release_publish",
            status="running",
            source_batch=task.batch_number or f"retry-task-{task.id}",
            detail=f"重试: {task.config.name} → {task.node.hostname}",
            progress=0,
            started_at=timezone.now(),
            trigger_user=request.user,
        )

        thread = threading.Thread(
            target=_run_release_tasks,
            args=([task.id], task_center.id),
            daemon=True,
        )
        thread.start()

        return JsonResponse({
            "success": True,
            "message": f"重试任务已开始: {task.config.name} → {task.node.hostname}",
            "task_center_id": task_center.id,
        })


class ReleaseBatchRollbackView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """按批次号批量回滚"""
    permission_resource = "releases"
    permission_action = "update"

    def post(self, request, batch_number):
        tasks = ReleaseTask.objects.filter(
            batch_number=batch_number,
            status__in=["success", "failed"],
        ).select_related("node", "config", "binding")

        if not tasks.exists():
            return JsonResponse({"success": False, "message": "未找到可回滚的任务"}, status=400)

        rollback_batch = generate_batch_number()
        rollback_task_ids = []

        for task in tasks:
            if task.node.is_locked:
                continue

            # 回滚使用 synced_version（发布前的版本）
            rollback_version = task.binding.synced_version if task.binding else None
            if rollback_version is None:
                continue

            binding = task.binding
            version_obj = binding.versions.filter(version=rollback_version).first()

            new_task = ReleaseTask.objects.create(
                batch_number=rollback_batch,
                binding=binding,
                config=task.config,
                node=task.node,
                version=version_obj,
                publish_version=rollback_version,
                remote_path=task.remote_path or (binding.remote_path if binding else ""),
                operator=request.user,
                status="pending",
            )
            rollback_task_ids.append(new_task.id)

        if not rollback_task_ids:
            return JsonResponse({"success": False, "message": "未生成任何回滚任务"}, status=400)

        task_center = TaskCenterTask.objects.create(
            operation_type="release_rollback",
            status="running",
            source_batch=rollback_batch,
            detail=f"批量回滚：{len(rollback_task_ids)} 个任务",
            progress=0,
            started_at=timezone.now(),
            trigger_user=request.user,
        )

        thread = threading.Thread(
            target=_run_release_tasks,
            args=(rollback_task_ids, task_center.id),
            daemon=True,
        )
        thread.start()

        return JsonResponse({
            "success": True,
            "message": f"批量回滚已开始，批次号: {rollback_batch}，共 {len(rollback_task_ids)} 个任务",
            "task_center_id": task_center.id,
            "batch_number": rollback_batch,
        })