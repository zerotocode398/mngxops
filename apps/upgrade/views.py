"""Nginx 升级模块 - 视图"""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import ListView, CreateView, TemplateView, DetailView

from .forms import (
    NginxSourcePackageForm,
    NginxThirdPartyModulePackageForm,
    NginxUpgradeTaskForm,
)
from .models import (
    NginxSourcePackage,
    NginxThirdPartyModulePackage,
    NginxUpgradeTask,
    generate_upgrade_batch_number,
)
from .services import (
    fetch_nginx_v_from_node,
    parse_nginx_v_output,
    compute_target_configure_opts,
    enrich_third_party_module_paths,
    run_upgrade_task,
    _tokenize_configure_args,
)

from apps.users.permissions import PermissionRequiredMixin
from utils.nav_context import append_nav_query, get_sidebar_nav, nav_context
from utils.pagination import PerPagePaginationMixin
from utils.setting_service import get_recent_tasks_limit, get_setting
from apps.nodes.models import Node
from apps.nodes.views import _get_node_credential


# ==================== 源码包管理 ====================

class PackageListView(LoginRequiredMixin, PermissionRequiredMixin, PerPagePaginationMixin, ListView):
    """源码包列表"""
    model = NginxSourcePackage
    template_name = "upgrade/package_list.html"
    context_object_name = "packages"
    paginate_by = None
    ordering = ["-created_at"]
    permission_resource = "upgrade"
    permission_action = "read"

    def get_queryset(self):
        return super().get_queryset().select_related("uploaded_by")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        packages = self.get_queryset()
        per_page = self.get_paginate_by(None)
        paginator = Paginator(list(packages), per_page)
        page_num = self.request.GET.get("page", 1)
        page_obj = paginator.get_page(page_num)

        context["packages"] = page_obj.object_list
        context["page_obj"] = page_obj
        context["is_paginated"] = page_obj.has_other_pages()
        context["per_page"] = per_page
        context["per_page_options"] = self.per_page_options
        context.update(nav_context(self.request))
        return context


class PackageUploadView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    """上传源码包（支持 AJAX、同版本覆盖、上传进度由前端 XHR 展示）"""
    model = NginxSourcePackage
    form_class = NginxSourcePackageForm
    template_name = "upgrade/package_upload.html"
    permission_resource = "upgrade"
    permission_action = "create"

    def get_form_kwargs(self):
        """传入当前用户供版本唯一性校验"""
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        """传入源码包大小限制供前端校验"""
        from utils.setting_service import get_setting
        context = super().get_context_data(**kwargs)
        try:
            context["package_max_size_mb"] = max(
                1, int(get_setting("upgrade.package_max_size_mb", "20") or 20)
            )
        except (TypeError, ValueError):
            context["package_max_size_mb"] = 20
        context.update(nav_context(self.request))
        return context

    def _wants_json(self):
        """是否返回 JSON（XHR 上传）"""
        return (
            self.request.headers.get("X-Requested-With") == "XMLHttpRequest"
            or "application/json" in self.request.headers.get("Accept", "")
        )

    def _has_version_exists_error(self, form):
        """判断是否为同版本冲突错误"""
        for err in form.non_field_errors().as_data():
            if getattr(err, "code", None) == "version_exists":
                return True
        return False

    def form_valid(self, form):
        """新建或覆盖已有同版本源码包"""
        user = self.request.user
        version = form.cleaned_data["version"]
        overwrite = form.cleaned_data.get("overwrite")
        existing = NginxSourcePackage.objects.filter(
            version=version,
            uploaded_by=user,
        ).first()

        if existing and overwrite:
            if existing.package_file:
                existing.package_file.delete(save=False)
            existing.name = form.cleaned_data["name"]
            existing.description = form.cleaned_data.get("description") or ""
            existing.is_official = bool(form.cleaned_data.get("is_official"))
            existing.package_file = form.cleaned_data["package_file"]
            existing.file_size = 0
            existing.file_md5 = ""
            existing.save()
            self.object = existing
            msg = f"源码包 {existing.name} (nginx-{existing.version}) 已覆盖更新"
        else:
            form.instance.uploaded_by = user
            self.object = form.save()
            msg = f"源码包 {self.object.name} (nginx-{self.object.version}) 上传成功"

        if self._wants_json():
            return JsonResponse({
                "success": True,
                "message": msg,
                "redirect": self.get_success_url(),
            })
        messages.success(self.request, msg)
        return redirect(self.get_success_url())

    def form_invalid(self, form):
        """校验失败：AJAX 返回 JSON，含 need_overwrite 标记"""
        if self._wants_json():
            need_overwrite = self._has_version_exists_error(form)
            message = "版本已存在，是否覆盖？" if need_overwrite else "上传校验失败"
            if not need_overwrite and form.errors:
                # 取首条可读错误
                for field, errs in form.errors.items():
                    if errs:
                        message = errs[0]
                        break
            return JsonResponse({
                "success": False,
                "need_overwrite": need_overwrite,
                "message": message,
                "errors": form.errors,
            }, status=400)
        return super().form_invalid(form)

    def get_success_url(self):
        """上传成功回列表，保留侧栏 nav 透传"""
        return append_nav_query(reverse("upgrade:package_list"), get_sidebar_nav(self.request))


class PackageVersionCheckView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """检查当前用户是否已上传同版本源码包（轻量预检，避免大文件重复上传）"""
    permission_resource = "upgrade"
    permission_action = "create"

    def get(self, request):
        """按 version 查询是否已存在"""
        version = (request.GET.get("version") or "").strip()
        if not version:
            return JsonResponse({"exists": False, "version": version})
        exists = NginxSourcePackage.objects.filter(
            version=version,
            uploaded_by=request.user,
        ).exists()
        return JsonResponse({"exists": exists, "version": version})


class PackageDeleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """删除源码包"""
    permission_resource = "upgrade"
    permission_action = "delete"

    def post(self, request, pk):
        package = get_object_or_404(NginxSourcePackage, pk=pk)
        name = str(package)
        package.package_file.delete(save=False)
        package.delete()
        messages.success(request, f"源码包 {name} 已删除")
        return redirect(
            append_nav_query(reverse("upgrade:package_list"), get_sidebar_nav(request))
        )

class PackageDownloadView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """下载源码包"""
    permission_resource = "upgrade"
    permission_action = "read"

    def get(self, request, pk):
        from django.http import FileResponse
        package = get_object_or_404(NginxSourcePackage, pk=pk)
        response = FileResponse(
            package.package_file.open("rb"),
            as_attachment=True,
            filename=package.package_file.name.split("/")[-1],
        )
        return response


# ==================== 第三方模块包管理 ====================

class ModulePackageListView(LoginRequiredMixin, PermissionRequiredMixin, PerPagePaginationMixin, ListView):
    """第三方模块离线包列表"""
    model = NginxThirdPartyModulePackage
    template_name = "upgrade/module_package_list.html"
    context_object_name = "packages"
    paginate_by = None
    ordering = ["-created_at"]
    permission_resource = "upgrade"
    permission_action = "read"

    def get_queryset(self):
        """预加载上传人"""
        return super().get_queryset().select_related("uploaded_by")

    def get_context_data(self, **kwargs):
        """分页上下文"""
        context = super().get_context_data(**kwargs)
        packages = self.get_queryset()
        per_page = self.get_paginate_by(None)
        paginator = Paginator(list(packages), per_page)
        page_num = self.request.GET.get("page", 1)
        page_obj = paginator.get_page(page_num)
        context["packages"] = page_obj.object_list
        context["page_obj"] = page_obj
        context["is_paginated"] = page_obj.has_other_pages()
        context["per_page"] = per_page
        context["per_page_options"] = self.per_page_options
        context.update(nav_context(self.request))
        return context


class ModulePackageUploadView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    """上传第三方模块离线包（支持 AJAX、同名同版本覆盖）"""
    model = NginxThirdPartyModulePackage
    form_class = NginxThirdPartyModulePackageForm
    template_name = "upgrade/module_package_upload.html"
    permission_resource = "upgrade"
    permission_action = "create"

    def get_form_kwargs(self):
        """传入当前用户供唯一性校验"""
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        """传入包大小限制"""
        context = super().get_context_data(**kwargs)
        try:
            context["package_max_size_mb"] = max(
                1, int(get_setting("upgrade.package_max_size_mb", "20") or 20)
            )
        except (TypeError, ValueError):
            context["package_max_size_mb"] = 20
        context.update(nav_context(self.request))
        return context
    def _wants_json(self):
        """是否返回 JSON（XHR 上传）"""
        return (
            self.request.headers.get("X-Requested-With") == "XMLHttpRequest"
            or "application/json" in self.request.headers.get("Accept", "")
        )

    def _has_version_exists_error(self, form):
        """判断是否为同名同版本冲突"""
        for err in form.non_field_errors().as_data():
            if getattr(err, "code", None) == "version_exists":
                return True
        return False

    def form_valid(self, form):
        """新建或覆盖已有同名同版本模块包"""
        user = self.request.user
        name = form.cleaned_data["name"]
        version = form.cleaned_data.get("version") or ""
        overwrite = form.cleaned_data.get("overwrite")
        existing = NginxThirdPartyModulePackage.objects.filter(
            name=name,
            version=version,
            uploaded_by=user,
        ).first()

        if existing and overwrite:
            if existing.package_file:
                existing.package_file.delete(save=False)
            existing.description = form.cleaned_data.get("description") or ""
            existing.package_file = form.cleaned_data["package_file"]
            existing.file_size = 0
            existing.file_md5 = ""
            existing.save()
            self.object = existing
            msg = f"模块包 {existing} 已覆盖更新"
        else:
            form.instance.uploaded_by = user
            self.object = form.save()
            msg = f"模块包 {self.object} 上传成功"

        if self._wants_json():
            return JsonResponse({
                "success": True,
                "message": msg,
                "redirect": self.get_success_url(),
            })
        messages.success(self.request, msg)
        return redirect(self.get_success_url())

    def form_invalid(self, form):
        """校验失败：AJAX 返回 JSON"""
        if self._wants_json():
            need_overwrite = self._has_version_exists_error(form)
            message = "模块包已存在，是否覆盖？" if need_overwrite else "上传校验失败"
            if not need_overwrite and form.errors:
                for field, errs in form.errors.items():
                    if errs:
                        message = errs[0]
                        break
            return JsonResponse({
                "success": False,
                "need_overwrite": need_overwrite,
                "message": message,
                "errors": form.errors,
            }, status=400)
        return super().form_invalid(form)

    def get_success_url(self):
        """上传成功回列表，保留侧栏 nav 透传"""
        return append_nav_query(
            reverse("upgrade:module_package_list"), get_sidebar_nav(self.request)
        )


class ModulePackageCheckView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """检查当前用户是否已上传同名同版本模块包"""
    permission_resource = "upgrade"
    permission_action = "create"

    def get(self, request):
        """按 name+version 查询是否已存在"""
        name = (request.GET.get("name") or "").strip()
        version = (request.GET.get("version") or "").strip()
        if not name:
            return JsonResponse({"exists": False, "name": name, "version": version})
        exists = NginxThirdPartyModulePackage.objects.filter(
            name=name,
            version=version,
            uploaded_by=request.user,
        ).exists()
        return JsonResponse({"exists": exists, "name": name, "version": version})


class ModulePackageDeleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """删除第三方模块离线包"""
    permission_resource = "upgrade"
    permission_action = "delete"

    def post(self, request, pk):
        """删除文件与记录"""
        package = get_object_or_404(NginxThirdPartyModulePackage, pk=pk)
        label = str(package)
        package.package_file.delete(save=False)
        package.delete()
        messages.success(request, f"模块包 {label} 已删除")
        return redirect(
            append_nav_query(
                reverse("upgrade:module_package_list"), get_sidebar_nav(request)
            )
        )

class ModulePackageDownloadView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """下载第三方模块离线包"""
    permission_resource = "upgrade"
    permission_action = "read"

    def get(self, request, pk):
        """附件下载"""
        from django.http import FileResponse
        package = get_object_or_404(NginxThirdPartyModulePackage, pk=pk)
        response = FileResponse(
            package.package_file.open("rb"),
            as_attachment=True,
            filename=package.package_file.name.split("/")[-1],
        )
        return response


# ==================== 升级中心 ====================

class UpgradeCenterView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    """升级中心主页面"""
    template_name = "upgrade/center.html"
    permission_resource = "upgrade"
    permission_action = "read"

    def get_context_data(self, **kwargs):
        """注入节点、源码包及系统设置默认编译参数"""
        from utils.setting_service import get_setting

        context = super().get_context_data(**kwargs)
        context["nodes"] = Node.objects.filter(is_locked=False).order_by("hostname")
        context["packages"] = (
            NginxSourcePackage.objects.select_related("uploaded_by").order_by("-created_at")
        )
        context["module_packages"] = (
            NginxThirdPartyModulePackage.objects.select_related("uploaded_by").order_by("-created_at")
        )
        context["latest_tasks"] = (
            NginxUpgradeTask.objects.select_related("node", "operator")
            .order_by("-created_at")[: get_recent_tasks_limit()]
        )
        context["default_work_dir"] = get_setting("upgrade.default_work_dir", "/tmp/nginx-upgrade")
        context["default_make_jobs"] = int(get_setting("upgrade.make_jobs_default", "4") or 4)
        return context


# ==================== API 接口 ====================

class NginxVApiView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """获取目标节点 nginx -V 输出 (Ajax)"""
    permission_resource = "upgrade"
    permission_action = "create"

    def post(self, request, node_id):
        node = get_object_or_404(Node, pk=node_id)
        if node.is_locked:
            return JsonResponse({"success": False, "message": "节点已锁定"}, status=400)

        success, result = fetch_nginx_v_from_node(node)
        if not success:
            return JsonResponse({"success": False, "message": result}, status=400)

        return JsonResponse({"success": True, "data": result})


class ParseConfigApiView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """解析 nginx -V 输出为结构化参数 (Ajax)"""
    permission_resource = "upgrade"
    permission_action = "create"

    def post(self, request):
        raw_output = request.POST.get("raw_output", "")
        if not raw_output:
            return JsonResponse({"success": False, "message": "缺少原始输出"}, status=400)

        parsed = parse_nginx_v_output(raw_output)
        return JsonResponse({"success": True, "data": parsed})


class ComputeConfigApiView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """计算调整后的编译参数预览 (Ajax)"""
    permission_resource = "upgrade"
    permission_action = "create"

    def post(self, request):
        try:
            current_params = json.loads(request.POST.get("current_params", "[]"))
            added_modules = json.loads(request.POST.get("added_modules", "[]"))
            removed_modules = json.loads(request.POST.get("removed_modules", "[]"))
            added_third_party = json.loads(request.POST.get("added_third_party", "[]"))
            remote_work_dir = (
                request.POST.get("remote_work_dir")
                or get_setting("upgrade.default_work_dir", "/tmp/nginx-upgrade")
                or "/tmp/nginx-upgrade"
            ).strip()
        except json.JSONDecodeError:
            return JsonResponse({"success": False, "message": "JSON 解析失败"}, status=400)

        added_third_party = enrich_third_party_module_paths(added_third_party, remote_work_dir)
        target_opts = compute_target_configure_opts(
            current_params, added_modules, removed_modules, added_third_party,
            remote_work_dir=remote_work_dir,
        )
        return JsonResponse({"success": True, "target_opts": target_opts})


# ==================== 升级任务 CRUD ====================

def _parse_json_body(request):
    """解析 JSON 请求体，失败返回 None"""
    try:
        return json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _task_progress_dict(task):
    """序列化单条升级任务进度"""
    return {
        "task_id": task.id,
        "node_id": task.node_id,
        "hostname": task.node.hostname if task.node_id else "",
        "ip": task.node.ip if task.node_id else "",
        "status": task.status,
        "status_display": task.get_status_display(),
        "progress": task.progress,
        "current_step": task.current_step,
        "error_message": task.error_message,
        "current_version": task.current_version,
        "target_version": task.target_version,
        "batch_number": task.batch_number or "",
        "log_output": task.log_output[-20000:] if task.log_output else "",
        "log_url": reverse("upgrade:task_log", kwargs={"pk": task.id}),
    }


def _start_upgrade_tasks_parallel(task_ids):
    """在后台线程池中并行执行多个升级任务"""
    max_workers = max(1, int(get_setting("node.batch_max_count", "3") or 3))

    def _runner():
        workers = min(max_workers, len(task_ids)) or 1
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(run_upgrade_task, task_ids))

    threading.Thread(target=_runner, daemon=True).start()


class UpgradeTaskCreateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """创建升级任务：支持批量 JSON（多节点）与旧版单节点 FormData"""
    permission_resource = "upgrade"
    permission_action = "create"

    def post(self, request):
        """按 Content-Type 分发到批量或单节点创建"""
        content_type = (request.content_type or "").lower()
        if "application/json" in content_type:
            return self._create_batch(request)
        # 兼容旧前端 FormData 单节点
        return self._create_single_form(request)

    def _create_single_form(self, request):
        """旧版单节点 FormData 创建（保持兼容）"""
        form = NginxUpgradeTaskForm(request.POST)
        if not form.is_valid():
            errors = {k: [str(e) for e in v] for k, v in form.errors.items()}
            return JsonResponse({"success": False, "message": "表单验证失败", "errors": errors}, status=400)

        batch_number = generate_upgrade_batch_number()
        task = form.save(commit=False)
        task.operator = request.user
        task.status = "pending"
        task.current_step = "任务已创建，等待执行"
        task.batch_number = batch_number
        if not task.current_version and task.node_id:
            task.current_version = task.node.nginx_version or ""
        cleaned = form.cleaned_data
        task.added_modules = cleaned.get("added_modules", "[]")
        task.removed_modules = cleaned.get("removed_modules", "[]")
        task.added_third_party = cleaned.get("added_third_party", "[]")
        task.save()

        from apps.releases.models import TaskCenterTask
        from apps.releases.task_result import upgrade_detail_short
        task_center = TaskCenterTask.objects.create(
            operation_type="nginx_upgrade",
            status="pending",
            detail=upgrade_detail_short(task.current_version, task.target_version),
            target_hostnames=task.node.hostname,
            target_ips=task.node.ip,
            trigger_user=request.user,
            source_batch=batch_number,
        )
        task.task_center = task_center
        task.save(update_fields=["task_center"])

        _start_upgrade_tasks_parallel([task.id])
        return JsonResponse({
            "success": True,
            "task_id": task.id,
            "task_ids": [task.id],
            "batch_number": batch_number,
            "progress_url": reverse("upgrade:task_progress", kwargs={"pk": task.id}),
        })

    def _create_batch(self, request):
        """批量创建：每节点一条任务，同 batch_number，并行执行"""
        data = _parse_json_body(request)
        if data is None:
            return JsonResponse({"success": False, "message": "JSON 解析失败"}, status=400)

        try:
            node_ids = [int(x) for x in (data.get("node_ids") or [])]
            package_id = int(data.get("source_package") or 0)
            upgrade_mode = (data.get("upgrade_mode") or "upgrade").strip()
            remote_work_dir = (
                data.get("remote_work_dir")
                or get_setting("upgrade.default_work_dir", "/tmp/nginx-upgrade")
                or "/tmp/nginx-upgrade"
            ).strip()
            make_jobs = int(
                data.get("make_jobs")
                or get_setting("upgrade.make_jobs_default", "4")
                or 4
            )
            target_version = (data.get("target_version") or "").strip()
            shared_prefix = (data.get("target_prefix") or "").strip()
            added_modules = data.get("added_modules") or []
            removed_modules = data.get("removed_modules") or []
            added_third_party = data.get("added_third_party") or []
            nodes_payload = data.get("nodes_payload") or []
        except (TypeError, ValueError):
            return JsonResponse({"success": False, "message": "请求参数格式错误"}, status=400)

        if not isinstance(added_third_party, list):
            added_third_party = []
        added_third_party = enrich_third_party_module_paths(added_third_party, remote_work_dir)

        if not node_ids:
            return JsonResponse({"success": False, "message": "请至少选择一个节点"}, status=400)
        if upgrade_mode not in ("upgrade", "install", "switch_path"):
            return JsonResponse({"success": False, "message": "升级模式无效"}, status=400)
        if upgrade_mode == "switch_path" and not shared_prefix:
            return JsonResponse(
                {"success": False, "message": "切换路径模式请填写目标 --prefix"},
                status=400,
            )

        package = NginxSourcePackage.objects.filter(pk=package_id).first()
        if not package:
            return JsonResponse({"success": False, "message": "源码包不存在"}, status=400)
        if not target_version:
            target_version = package.version

        payload_map = {}
        for item in nodes_payload:
            try:
                nid = int(item.get("node_id"))
            except (TypeError, ValueError, AttributeError):
                continue
            payload_map[nid] = item

        nodes = list(
            Node.objects.filter(pk__in=node_ids)
            .select_related("credential")
            .prefetch_related("groups")
        )
        node_map = {n.id: n for n in nodes}
        if len(node_map) != len(set(node_ids)):
            return JsonResponse({"success": False, "message": "部分节点不存在"}, status=400)

        for nid in node_ids:
            node = node_map[nid]
            if node.is_locked:
                return JsonResponse(
                    {"success": False, "message": f"节点 {node.hostname} 已锁定"},
                    status=400,
                )
            if node.status != "online":
                return JsonResponse(
                    {"success": False, "message": f"节点 {node.hostname} 非在线状态"},
                    status=400,
                )
            if not _get_node_credential(node):
                return JsonResponse(
                    {"success": False, "message": f"节点 {node.hostname} 未配置有效凭证"},
                    status=400,
                )
            if nid not in payload_map and upgrade_mode != "install":
                return JsonResponse(
                    {"success": False, "message": f"缺少节点 {node.hostname} 的编译参数基线"},
                    status=400,
                )

        batch_number = generate_upgrade_batch_number()
        from apps.releases.models import TaskCenterTask

        created_tasks = []
        for nid in node_ids:
            node = node_map[nid]
            item = payload_map.get(nid) or {}
            params = item.get("params") or []
            if not isinstance(params, list):
                params = []
            current_version = (item.get("current_version") or "").strip()
            current_opts = (item.get("current_configure_opts") or "").strip()
            prefix = (item.get("prefix") or "").strip()
            binary_path = (item.get("binary_path") or "").strip()

            # 平滑/全新：各节点自身 prefix；切换路径：统一使用共享 prefix
            if upgrade_mode == "switch_path":
                target_prefix = shared_prefix
            else:
                target_prefix = prefix or "/usr/local/nginx"

            target_opts = compute_target_configure_opts(
                params, added_modules, removed_modules, added_third_party,
                remote_work_dir=remote_work_dir,
            )
            # 仅切换路径模式才重写 configure 中的 --prefix=
            if upgrade_mode == "switch_path" and target_prefix:
                from .services import _tokenize_configure_args, _join_configure_opts
                tokens = _tokenize_configure_args(target_opts)
                if not tokens:
                    tokens = [p for p in params if p not in removed_modules]
                    for mod in added_modules:
                        if mod not in tokens:
                            tokens.append(mod)
                new_tokens = []
                has_prefix = False
                for t in tokens:
                    if t.startswith("--prefix="):
                        new_tokens.append(f"--prefix={target_prefix}")
                        has_prefix = True
                    else:
                        new_tokens.append(t)
                if not has_prefix:
                    new_tokens.insert(0, f"--prefix={target_prefix}")
                target_opts = _join_configure_opts(new_tokens, multiline=True)

            task = NginxUpgradeTask(
                batch_number=batch_number,
                node=node,
                source_package=package,
                upgrade_mode=upgrade_mode,
                remote_work_dir=remote_work_dir,
                make_jobs=make_jobs,
                current_version=current_version or (node.nginx_version or ""),
                current_configure_opts=current_opts,
                current_configure_path=prefix,
                current_binary_path=binary_path,
                target_version=target_version,
                target_configure_opts=target_opts,
                target_prefix=target_prefix,
                added_modules=json.dumps(added_modules, ensure_ascii=False),
                removed_modules=json.dumps(removed_modules, ensure_ascii=False),
                added_third_party=json.dumps(added_third_party, ensure_ascii=False),
                operator=request.user,
                status="pending",
                current_step="任务已创建，等待执行",
            )
            task.save()

            from apps.releases.task_result import upgrade_detail_short
            task_center = TaskCenterTask.objects.create(
                operation_type="nginx_upgrade",
                status="pending",
                detail=upgrade_detail_short(
                    current_version or (node.nginx_version or ""),
                    target_version,
                ),
                target_hostnames=node.hostname,
                target_ips=node.ip,
                trigger_user=request.user,
                source_batch=batch_number,
            )
            task.task_center = task_center
            task.save(update_fields=["task_center"])
            created_tasks.append(task)

        task_ids = [t.id for t in created_tasks]
        _start_upgrade_tasks_parallel(task_ids)

        return JsonResponse({
            "success": True,
            "batch_number": batch_number,
            "task_id": task_ids[0],
            "task_ids": task_ids,
            "message": f"已创建 {len(task_ids)} 个升级任务，批次 {batch_number}",
        })


class UpgradeTaskProgressView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """获取升级进度 (Ajax 轮询)"""
    permission_resource = "upgrade"
    permission_action = "read"

    def get(self, request, pk):
        task = get_object_or_404(
            NginxUpgradeTask.objects.select_related("node"), pk=pk
        )
        data = _task_progress_dict(task)
        data["success"] = True
        # 任务详情轮询返回完整日志，不做截断
        data["log_output"] = task.log_output or ""
        return JsonResponse(data)


class UpgradeBatchProgressView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """批量查询多条升级任务进度"""
    permission_resource = "upgrade"
    permission_action = "read"

    def get(self, request):
        """按 ids=1,2,3 返回各任务进度与汇总百分比"""
        raw = (request.GET.get("ids") or "").strip()
        if not raw:
            return JsonResponse({"success": False, "message": "缺少 ids"}, status=400)
        try:
            ids = [int(x) for x in raw.split(",") if x.strip()]
        except ValueError:
            return JsonResponse({"success": False, "message": "ids 格式错误"}, status=400)
        if not ids:
            return JsonResponse({"success": False, "message": "缺少 ids"}, status=400)

        tasks = list(
            NginxUpgradeTask.objects.filter(pk__in=ids)
            .select_related("node")
            .order_by("id")
        )
        items = [_task_progress_dict(t) for t in tasks]
        terminal = ("success", "failed", "rollback", "cancelled")
        avg = int(sum(t.progress for t in tasks) / len(tasks)) if tasks else 0
        all_done = bool(tasks) and all(t.status in terminal for t in tasks)
        any_failed = any(t.status == "failed" for t in tasks)
        all_success = bool(tasks) and all(t.status == "success" for t in tasks)

        return JsonResponse({
            "success": True,
            "tasks": items,
            "progress": avg,
            "all_done": all_done,
            "any_failed": any_failed,
            "all_success": all_success,
            "batch_number": tasks[0].batch_number if tasks else "",
        })


class UpgradeTaskLogView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    """查看完整升级日志"""
    model = NginxUpgradeTask
    template_name = "upgrade/task_log.html"
    context_object_name = "task"
    permission_resource = "upgrade"
    permission_action = "read"

    def get_context_data(self, **kwargs):
        """注入分词后的编译参数与增减对比"""
        context = super().get_context_data(**kwargs)
        task = self.object
        current_raw = (task.current_configure_opts or "").strip()
        target_raw = (task.target_configure_opts or "").strip()
        current_params = _tokenize_configure_args(current_raw) if current_raw else []
        target_params = _tokenize_configure_args(target_raw) if target_raw else []
        target_set = set(target_params)
        current_set = set(current_params)
        removed = [p for p in current_params if p not in target_set]
        added = [p for p in target_params if p not in current_set]
        context["current_params"] = current_params
        context["target_params"] = target_params
        context["param_removed"] = removed
        context["param_added"] = added
        context["has_param_diff"] = bool(removed or added)
        context["log_output_display"] = (task.log_output or "").strip()
        return context


class UpgradeTaskCancelView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """取消升级任务"""
    permission_resource = "upgrade"
    permission_action = "update"

    def post(self, request, pk):
        task = get_object_or_404(NginxUpgradeTask, pk=pk)
        if task.status not in ("pending", "fetching_config", "uploading_package"):
            return JsonResponse({"success": False, "message": "当前状态不允许取消"}, status=400)

        task.status = "cancelled"
        task.error_message = "用户手动取消"
        task.finished_at = timezone.now()
        task.save()

        if task.task_center:
            from apps.releases.task_result import (
                build_tree_result,
                item_failed,
                node_header,
                upgrade_detail_short,
            )
            ver_label = upgrade_detail_short(task.current_version, task.target_version)
            task.task_center.status = "cancelled"
            task.task_center.progress = 100
            task.task_center.detail = ver_label
            task.task_center.result = build_tree_result(
                0, 1, 1,
                [
                    node_header(task.node.ip, task.node.hostname),
                    item_failed(ver_label, "用户手动取消"),
                ],
            )
            task.task_center.finished_at = timezone.now()
            task.task_center.save(
                update_fields=["status", "progress", "detail", "result", "finished_at"]
            )

        messages.success(request, "升级任务已取消")
        return JsonResponse({"success": True})


class UpgradeTaskRollbackView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """回滚升级任务"""
    permission_resource = "upgrade"
    permission_action = "update"

    def post(self, request, pk):
        task = get_object_or_404(NginxUpgradeTask, pk=pk)
        if task.status not in ("success", "failed"):
            return JsonResponse({"success": False, "message": "当前状态不允许回滚"}, status=400)

        # 执行回滚操作
        node = task.node
        credential = _get_node_credential(node)
        if not credential:
            return JsonResponse({"success": False, "message": "节点未配置有效的 SSH 凭证"}, status=400)

        if not task.old_binary_backup:
            return JsonResponse({"success": False, "message": "没有可用的备份文件"}, status=400)

        from utils.ssh import SSHClient
        auth_kwargs = {}
        if credential.auth_type == "password":
            auth_kwargs["password"] = credential.get_password()
        else:
            auth_kwargs["private_key"] = credential.get_private_key()

        binary_path = task.current_binary_path or task.current_configure_path.rstrip("/") + "/sbin/nginx"

        try:
            from utils.nginx_ops import reload_nginx

            with SSHClient(node.ip, node.port, credential.username, **auth_kwargs) as ssh:
                # 恢复旧二进制
                success, output = ssh.execute_command(
                    f"cp {task.old_binary_backup} {binary_path} 2>&1"
                )
                if not success:
                    return JsonResponse({"success": False, "message": f"回滚失败: {output}"}, status=500)

            # 按启动方式 reload（未运行则 start），使进程回到旧二进制生效路径
            ok, msg = reload_nginx(
                node.ip, node.port, credential.username,
                nginx_path=binary_path, start_if_stopped=True, **auth_kwargs,
            )
            if not ok:
                return JsonResponse(
                    {"success": False, "message": f"二进制已回滚，但 reload/start 失败: {msg}"},
                    status=500,
                )
        except Exception as e:
            return JsonResponse({"success": False, "message": f"回滚异常: {str(e)}"}, status=500)

        task.status = "rollback"
        task.finished_at = timezone.now()
        task.error_message = "已手动回滚到旧版本"
        task.save()

        if task.task_center:
            from apps.releases.task_result import (
                build_tree_result,
                item_success,
                node_header,
                upgrade_detail_short,
            )
            ver_label = upgrade_detail_short(task.current_version, task.target_version)
            task.task_center.status = "cancelled"
            task.task_center.progress = 100
            task.task_center.detail = f"已回滚：{ver_label}"
            task.task_center.result = build_tree_result(
                1, 0, 1,
                [
                    node_header(task.node.ip, task.node.hostname),
                    item_success(f"已回滚到旧版本 ({ver_label})"),
                ],
            )
            task.task_center.finished_at = timezone.now()
            task.task_center.save(
                update_fields=["status", "progress", "detail", "result", "finished_at"]
            )

        messages.success(request, f"Nginx 已回滚到备份版本 ({task.old_binary_backup})")
        return JsonResponse({"success": True})


# ==================== 升级历史 ====================

class UpgradeHistoryView(LoginRequiredMixin, PermissionRequiredMixin, PerPagePaginationMixin, ListView):
    """升级历史列表"""
    model = NginxUpgradeTask
    template_name = "upgrade/history.html"
    context_object_name = "tasks"
    paginate_by = None
    ordering = ["-created_at"]
    permission_resource = "upgrade"
    permission_action = "read"

    def get_queryset(self):
        queryset = super().get_queryset().select_related("node", "operator", "source_package")
        search = self.request.GET.get("search", "")
        if search:
            queryset = queryset.filter(
                Q(node__hostname__icontains=search)
                | Q(node__ip__icontains=search)
                | Q(target_version__icontains=search)
                | Q(current_version__icontains=search)
                | Q(batch_number__icontains=search)
            )
        status_filter = self.request.GET.get("status", "")
        if status_filter == "running":
            # 进行中 = 非终态（与首页统计一致）
            queryset = queryset.exclude(
                status__in=("success", "failed", "rollback", "cancelled")
            )
        elif status_filter:
            queryset = queryset.filter(status=status_filter)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tasks = self.get_queryset()
        per_page = self.get_paginate_by(None)
        paginator = Paginator(list(tasks), per_page)
        page_num = self.request.GET.get("page", 1)
        page_obj = paginator.get_page(page_num)

        context["tasks"] = page_obj.object_list
        context["page_obj"] = page_obj
        context["is_paginated"] = page_obj.has_other_pages()
        context["search"] = self.request.GET.get("search", "")
        context["status_filter"] = self.request.GET.get("status", "")
        # 下拉增加「进行中」虚拟选项
        context["status_choices"] = [("running", "进行中")] + list(
            NginxUpgradeTask.STATUS_CHOICES
        )
        context["per_page"] = per_page
        context["per_page_options"] = self.per_page_options
        return context


class UpgradeTaskListView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    """Nginx 升级主页：运维操作台（统计 + 最近任务）"""
    template_name = "upgrade/index.html"
    permission_resource = "upgrade"
    permission_action = "read"

    # 进行中：非终态
    _TERMINAL_STATUSES = ("success", "failed", "rollback", "cancelled")

    def get_context_data(self, **kwargs):
        """组装首页统计与最近任务列表"""
        context = super().get_context_data(**kwargs)
        since_7d = timezone.now() - timedelta(days=7)
        context["package_count"] = NginxSourcePackage.objects.count()
        context["module_package_count"] = NginxThirdPartyModulePackage.objects.count()
        context["running_count"] = NginxUpgradeTask.objects.exclude(
            status__in=self._TERMINAL_STATUSES
        ).count()
        context["failed_7d_count"] = NginxUpgradeTask.objects.filter(
            status="failed",
            created_at__gte=since_7d,
        ).count()
        context["recent_tasks"] = (
            NginxUpgradeTask.objects
            .select_related("node", "operator", "source_package")
            .order_by("-created_at")[: get_recent_tasks_limit()]
        )
        return context