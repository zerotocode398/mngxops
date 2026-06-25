import json
import threading
from datetime import datetime

from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.db.models import Q
from django.shortcuts import redirect, get_object_or_404, render
from django.urls import reverse
from django.views.generic import ListView, DetailView, CreateView, View
from django.http import JsonResponse

from apps.nodes.views import _get_node_credential
from apps.nodes.models import Node
from apps.configs.models import Config, ConfigVersion
from apps.users.permissions import PermissionRequiredMixin
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
from .models import ReleaseTask, ReleaseHistory, generate_batch_number
from utils.pagination import PerPagePaginationMixin


class ReleaseExecutorMixin:
    def _execute_release(self, task, action):
        node = task.node
        config = task.config
        version = task.version

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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return context

    def post(self, request, *args, **kwargs):
        from apps.configs.models import Config, ConfigVersion

        node_ids = request.POST.getlist("node_ids")
        config_ids = request.POST.getlist("config_ids")

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

        if len(node_ids) > 5 or len(config_ids) > 5:
            messages.error(request, "单次发布最多选择 5 个节点和 5 个配置")
            return redirect("releases:create")

        batch_number = generate_batch_number()
        created_count = 0

        for node_id in node_ids:
            node = get_object_or_404(Node, id=node_id)
            for config_id in config_ids:
                config = get_object_or_404(Config, id=config_id, node_id=node_id)

                # Use specified version if available, otherwise latest
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
        search = self.request.GET.get("search", "")
        status_filter = self.request.GET.get("status", "")

        if search:
            queryset = queryset.filter(
                Q(batch_number__icontains=search)
                | Q(config__name__icontains=search)
                | Q(node__hostname__icontains=search)
                | Q(operator__username__icontains=search)
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
            recent_history = recent_history.filter(
                Q(config__name__icontains=history_search)
                | Q(node__hostname__icontains=history_search)
                | Q(operator__username__icontains=history_search)
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


def _run_release_tasks(task_ids):
    executor = ReleaseExecutorMixin()
    for task_id in task_ids:
        try:
            task = ReleaseTask.objects.select_related(
                "node", "config", "version", "operator"
            ).get(pk=task_id)
            executor._execute_release(task, "publish")
        except ReleaseTask.DoesNotExist:
            pass


class ReleaseCenterExecuteView(
    LoginRequiredMixin, PermissionRequiredMixin, ReleaseExecutorMixin, View
):
    permission_resource = "releases"
    permission_action = "update"

    def post(self, request, batch_number):
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

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
        thread = threading.Thread(
            target=_run_release_tasks,
            args=(task_ids,),
            daemon=True,
        )
        thread.start()

        if is_ajax:
            return JsonResponse(
                {
                    "success": True,
                    "message": f"批次 {batch_number} 开始异步执行，共 {len(task_ids)} 个任务",
                    "task_ids": task_ids,
                    "async": True,
                }
            )
        messages.info(
            request, f"批次 {batch_number} 开始异步执行，共 {len(task_ids)} 个任务"
        )
        return redirect("releases:center")


class ReleaseCenterSingleExecuteView(
    LoginRequiredMixin, PermissionRequiredMixin, ReleaseExecutorMixin, View
):
    permission_resource = "releases"
    permission_action = "update"

    def post(self, request, task_id):
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

        if ReleaseTask.objects.filter(status="running").exists():
            msg = "当前有批次正在执行中，请等待完成后再执行"
            if is_ajax:
                return JsonResponse({"success": False, "message": msg})
            messages.error(request, msg)
            return redirect("releases:center")

        task = get_object_or_404(
            ReleaseTask.objects.select_related("node", "config", "version", "operator"),
            pk=task_id,
            status__in=["pending", "failed"],
        )

        thread = threading.Thread(
            target=_run_release_tasks,
            args=([task_id],),
            daemon=True,
        )
        thread.start()

        if is_ajax:
            return JsonResponse(
                {
                    "success": True,
                    "message": "任务开始异步执行",
                    "task_id": task.id,
                    "async": True,
                }
            )
        messages.info(request, "任务开始异步执行")
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
