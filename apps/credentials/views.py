from datetime import datetime

from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.http import JsonResponse, HttpResponse
from django.views.generic import ListView, CreateView, UpdateView, DeleteView, View
from django.urls import reverse_lazy, reverse
from django.shortcuts import get_object_or_404, redirect
from django.db.models import Count, Q
from django.core.paginator import Paginator

import threading

from .forms import CredentialForm
from .models import Credential, CredentialEnableTask
from .services import (
    _run_credential_enable_task,
    apply_credential_import,
    build_credential_export_bytes,
    build_credential_import_template_bytes,
    parse_credential_import_workbook,
    validate_credential_import_rows,
)
from apps.audit.models import AuditLog
from apps.audit.utils import _resolve_client_ip
from apps.releases.models import TaskCenterTask
from apps.users.permissions import PermissionRequiredMixin
from apps.nodes.models import Node
from utils.pagination import PerPagePaginationMixin


def filter_credential_list_queryset(queryset, request):
    """按凭证列表页相同条件筛选 queryset（搜索/认证方式/启用状态）。"""
    search = request.GET.get("search", "").strip()
    auth_type = request.GET.get("auth_type", "").strip()
    status = request.GET.get("status", "").strip()

    if search:
        terms = [t.strip() for t in search.replace("，", ",").split(",") if t.strip()]
        if terms:
            for term in terms:
                queryset = queryset.filter(
                    Q(name__icontains=term) | Q(username__icontains=term)
                )

    if auth_type in ("password", "key"):
        queryset = queryset.filter(auth_type=auth_type)

    if status == "enabled":
        queryset = queryset.filter(is_enabled=True)
    elif status == "disabled":
        queryset = queryset.filter(is_enabled=False)

    return queryset


def _parse_export_ids(request):
    """解析导出勾选 ID（ids=1,2,3 或重复 id=）；无效项忽略。"""
    raw = (request.GET.get("ids") or "").strip()
    if not raw:
        raw = ",".join(request.GET.getlist("id") or [])
    ids = []
    seen = set()
    for part in raw.replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            cid = int(part)
        except (TypeError, ValueError):
            continue
        if cid in seen:
            continue
        seen.add(cid)
        ids.append(cid)
    return ids


class CredentialListView(
    LoginRequiredMixin, PermissionRequiredMixin, PerPagePaginationMixin, ListView
):
    """凭证列表页，支持搜索和认证方式/状态筛选"""
    model = Credential
    template_name = "credentials/list.html"
    context_object_name = "credentials"
    paginate_by = 10
    ordering = ["-created_at"]
    permission_resource = "credentials"
    permission_action = "read"

    def get_queryset(self):
        """根据搜索词和筛选条件过滤凭证列表"""
        queryset = super().get_queryset().annotate(node_count=Count("node", distinct=True))
        return filter_credential_list_queryset(queryset, self.request)

    def get_context_data(self, **kwargs):
        """向模板传递搜索和筛选状态"""
        context = super().get_context_data(**kwargs)
        context["search"] = self.request.GET.get("search", "")
        context["auth_type"] = self.request.GET.get("auth_type", "")
        context["status"] = self.request.GET.get("status", "")
        params = self.request.GET.copy()
        params.pop("page", None)
        params.pop("per_page", None)
        context["export_query"] = params.urlencode()
        return context


class CredentialExportView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """按勾选 ID 或当前筛选导出凭证 xlsx（含明文密码/私钥）"""

    permission_resource = "credentials"
    permission_action = "read"

    def get(self, request):
        """生成并下载凭证明文导出；有 ids 仅导勾选，否则按筛选全量"""
        ids = _parse_export_ids(request)
        queryset = Credential.objects.all()
        if ids:
            id_order = {cid: idx for idx, cid in enumerate(ids)}
            credentials = list(queryset.filter(id__in=ids))
            credentials.sort(key=lambda c: id_order.get(c.id, 0))
            scope_label = "勾选"
        else:
            queryset = filter_credential_list_queryset(
                queryset.order_by("-created_at"), request
            )
            credentials = list(queryset)
            scope_label = "全量"

        content = build_credential_export_bytes(credentials)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"credentials_export_{stamp}.xlsx"

        names = [c.name for c in credentials if c.name]
        name_preview = "、".join(names[:20])
        if len(names) > 20:
            name_preview = f"{name_preview} 等{len(names)}个"
        detail = f"导出 {len(credentials)} 条（{scope_label}）"
        if name_preview:
            detail = f"{detail}：{name_preview}"

        AuditLog.objects.create(
            user=request.user,
            module="凭证管理",
            action="导出凭证",
            ip=_resolve_client_ip(),
            result="success",
            detail=detail,
        )

        response = HttpResponse(
            content,
            content_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class CredentialImportTemplateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """下载凭证批量导入 Excel 模板"""

    permission_resource = "credentials"
    permission_action = "create"

    def get(self, request):
        """返回 xlsx 模板文件流"""
        content = build_credential_import_template_bytes()
        response = HttpResponse(
            content,
            content_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
        )
        response["Content-Disposition"] = (
            'attachment; filename="credential_import_template.xlsx"'
        )
        return response


class CredentialImportAPIView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """上传 Excel 批量创建/更新凭证（整文件校验，失败不写入）"""

    permission_resource = "credentials"
    permission_action = "create"

    def post(self, request):
        """解析并导入凭证 Excel"""
        upload = request.FILES.get("file")
        if not upload:
            return JsonResponse(
                {"success": False, "message": "请选择要上传的 Excel 文件", "errors": []}
            )
        name = (upload.name or "").lower()
        if not name.endswith(".xlsx"):
            return JsonResponse(
                {
                    "success": False,
                    "message": "仅支持 .xlsx 格式",
                    "errors": [{"row": 0, "message": "仅支持 .xlsx 格式"}],
                }
            )

        rows, parse_errors = parse_credential_import_workbook(upload)
        if parse_errors:
            return JsonResponse(
                {
                    "success": False,
                    "message": "Excel 解析失败",
                    "errors": parse_errors,
                }
            )

        cleaned, errors = validate_credential_import_rows(rows, request.user)
        if errors:
            return JsonResponse(
                {
                    "success": False,
                    "message": f"校验未通过，共 {len(errors)} 条错误，未导入任何凭证",
                    "errors": errors,
                }
            )

        result = apply_credential_import(cleaned, request.user)
        parts = []
        if result["created"]:
            parts.append(f"新建 {result['created']} 条")
        if result["updated"]:
            parts.append(f"更新 {result['updated']} 条")
        message = "批量导入成功：" + "，".join(parts) if parts else "批量导入完成"

        AuditLog.objects.create(
            user=request.user,
            module="凭证管理",
            action="导入凭证",
            ip=_resolve_client_ip(),
            result="success",
            detail=message,
        )

        return JsonResponse(
            {
                "success": True,
                "message": message,
                "created": result["created"],
                "updated": result["updated"],
                "total": result["total"],
                "errors": [],
            }
        )


class CredentialCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    """创建凭证视图"""
    model = Credential
    form_class = CredentialForm
    template_name = "credentials/create.html"
    success_url = reverse_lazy("credentials:list")
    permission_resource = "credentials"
    permission_action = "create"

    def get_form_kwargs(self):
        """向表单传入当前用户，用于名称唯一校验"""
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        """保存凭证并关联创建人"""
        form.instance.created_by = self.request.user
        messages.success(self.request, f"凭证 {form.instance.name} 创建成功")
        return super().form_valid(form)

    def form_invalid(self, form):
        """表单验证失败时显示错误消息"""
        messages.error(self.request, "凭证创建失败，请检查输入")
        return super().form_invalid(form)


class CredentialUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    """编辑凭证视图，编辑时敏感字段留空不回填"""
    model = Credential
    form_class = CredentialForm
    template_name = "credentials/edit.html"
    success_url = reverse_lazy("credentials:list")
    permission_resource = "credentials"
    permission_action = "update"

    def get_form_kwargs(self):
        """向表单传入当前用户，用于名称唯一校验"""
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        """传递编辑模式下密码/私钥是否存在的信息"""
        context = super().get_context_data(**kwargs)
        credential = self.get_object()
        context["has_password"] = bool(credential.password)
        context["has_private_key"] = bool(credential.private_key)
        return context

    def form_valid(self, form):
        """更新成功后显示消息"""
        messages.success(self.request, f"凭证 {form.instance.name} 更新成功")
        return super().form_valid(form)

    def form_invalid(self, form):
        """表单验证失败时显示错误消息"""
        messages.error(self.request, "凭证更新失败，请检查输入")
        return super().form_invalid(form)


class CredentialDeleteView(LoginRequiredMixin, PermissionRequiredMixin, DeleteView):
    """删除凭证视图，包含关联节点确认（R5 删除保护）"""
    model = Credential
    template_name = "credentials/delete.html"
    success_url = reverse_lazy("credentials:list")
    permission_resource = "credentials"
    permission_action = "delete"

    def get_context_data(self, **kwargs):
        """传递关联节点列表，用于删除确认提示"""
        context = super().get_context_data(**kwargs)
        credential = self.get_object()
        context["related_nodes"] = Node.objects.filter(credential=credential)
        context["related_node_count"] = context["related_nodes"].count()
        return context

    def post(self, request, *args, **kwargs):
        """执行删除并显示成功消息"""
        credential = self.get_object()
        name = credential.name
        response = super().post(request, *args, **kwargs)
        messages.success(request, f"凭证 {name} 删除成功")
        return response


class CredentialToggleEnableView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """凭证启用/禁用切换视图，禁用到启用时自动触发关联节点批量测试"""
    permission_resource = "credentials"
    permission_action = "update"

    def post(self, request, pk):
        """切换凭证启用状态，支持Ajax和普通请求"""
        credential = get_object_or_404(Credential, pk=pk)
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

        if credential.is_enabled:
            # 禁用凭证：设为禁用，所有关联节点标记为离线
            credential.is_enabled = False
            credential.save(update_fields=["is_enabled", "updated_at"])
            affected = Node.objects.filter(credential=credential).update(status="offline")
            messages.success(
                request,
                f"凭证 {credential.name} 已禁用，{affected} 个关联节点状态已更新为离线",
            )
        else:
            # 启用凭证：设为启用；有关联节点时再触发后台测试
            credential.is_enabled = True
            credential.save(update_fields=["is_enabled", "updated_at"])

            has_related_nodes = Node.objects.filter(credential=credential).exists()
            if not has_related_nodes:
                messages.success(request, f"凭证 {credential.name} 已启用")
                if is_ajax:
                    return JsonResponse(
                        {
                            "success": True,
                            "message": f"凭证 {credential.name} 已启用",
                        }
                    )
            else:
                Node.objects.filter(credential=credential, is_locked=False).update(status="unknown")

                # 关联可测节点，供任务中心摘要/详情展示
                related_nodes = list(
                    Node.objects.filter(credential=credential, is_locked=False)
                    .order_by("id")
                    .values_list("hostname", "ip")
                )
                center_task = TaskCenterTask.objects.create(
                    operation_type="credential_enable_test",
                    status="pending",
                    detail="后台测试已创建",
                    target_configs=credential.name,
                    target_hostnames=",".join(hn for hn, _ in related_nodes if hn),
                    target_ips=",".join(ip for _, ip in related_nodes if ip),
                    trigger_user=request.user,
                )

                # 创建凭证启用测试任务记录
                task = CredentialEnableTask.objects.create(
                    credential=credential,
                    status="pending",
                    message="任务已创建，等待执行",
                    task_center_id=center_task.id,
                )

                # 启动后台线程执行测试
                worker = threading.Thread(
                    target=_run_credential_enable_task,
                    args=(task.id, credential.id),
                    daemon=True,
                )
                worker.start()

                messages.info(
                    request,
                    f"凭证 {credential.name} 已启用，后台测试已创建，可在任务中心查看详情",
                )

                if is_ajax:
                    return JsonResponse(
                        {
                            "success": True,
                            "message": f"凭证 {credential.name} 已启用，后台测试任务已创建",
                            "task_center_id": center_task.id,
                            "task_center_detail_url": reverse(
                                "releases:task_center_detail", args=[center_task.id]
                            ),
                            "task_center_home_url": reverse("releases:history"),
                        }
                    )

        if is_ajax:
            return JsonResponse(
                {
                    "success": True,
                    "message": f"凭证 {credential.name} 已禁用",
                }
            )

        query = request.GET.urlencode()
        url = reverse("credentials:list")
        if query:
            url = f"{url}?{query}"
        return redirect(url)


class CredentialEnableProgressView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """查询凭证启用测试进度（轮询接口）"""
    permission_resource = "credentials"
    permission_action = "read"

    def get(self, request, pk):
        """返回最新测试任务的进度信息"""
        credential = get_object_or_404(Credential, pk=pk)
        task = credential.enable_tasks.order_by("-created_at").first()
        if not task:
            return JsonResponse({"success": True, "has_task": False})

        percent = 0
        if task.total_count > 0:
            percent = int((task.completed_count / task.total_count) * 100)

        return JsonResponse(
            {
                "success": True,
                "has_task": True,
                "task": {
                    "id": task.id,
                    "status": task.status,
                    "total_count": task.total_count,
                    "completed_count": task.completed_count,
                    "success_count": task.success_count,
                    "failed_count": task.failed_count,
                    "skipped_count": task.skipped_count,
                    "message": task.message,
                    "percent": percent,
                    "task_center_detail_url": reverse(
                        "releases:task_center_detail", args=[task.task_center_id]
                    )
                    if task.task_center_id
                    else "",
                },
            }
        )


class CredentialDecryptView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """解密凭证敏感字段（密码/私钥）的Ajax接口"""
    permission_resource = "credentials"
    permission_action = "read"

    def get(self, request, pk):
        """返回解密后的密码或私钥明文"""
        credential = get_object_or_404(Credential, pk=pk)
        field = request.GET.get("field", "password")
        if field == "password":
            value = credential.get_password()
        elif field == "private_key":
            value = credential.get_private_key()
        else:
            return JsonResponse({"success": False, "message": "无效字段"})
        return JsonResponse({"success": True, "value": value})


class CredentialRelatedNodesView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """查询凭证关联的节点列表（支持搜索和分页）"""
    permission_resource = "credentials"
    permission_action = "read"

    def get(self, request, pk):
        """分页返回关联节点列表"""
        credential = get_object_or_404(Credential, pk=pk)

        search = request.GET.get("search", "").strip()
        group_search = request.GET.get("group_search", "").strip()

        try:
            page = max(1, int(request.GET.get("page", 1) or 1))
        except (TypeError, ValueError):
            page = 1

        try:
            per_page = max(1, min(int(request.GET.get("per_page", 5) or 5), 50))
        except (TypeError, ValueError):
            per_page = 5

        queryset = Node.objects.filter(credential=credential).prefetch_related("groups")

        if search:
            queryset = queryset.filter(Q(hostname__icontains=search) | Q(ip__icontains=search))

        if group_search:
            tags = [
                name.strip()
                for name in group_search.replace("，", ",").split(",")
                if name.strip()
            ]
            if tags:
                for tag in tags:
                    queryset = queryset.filter(
                        Q(groups__name__icontains=tag)
                        | Q(hostname__icontains=tag)
                        | Q(ip__icontains=tag)
                    )
                queryset = queryset.distinct()

        queryset = queryset.order_by("hostname")
        paginator = Paginator(queryset, per_page)
        page_obj = paginator.get_page(page)

        data = [
            {
                "id": node.id,
                "hostname": node.hostname,
                "ip": node.ip,
                "status": node.status,
                "status_display": node.get_status_display(),
            }
            for node in page_obj.object_list
        ]

        return JsonResponse(
            {
                "success": True,
                "credential": {"id": credential.id, "name": credential.name},
                "data": data,
                "search": search,
                "group_search": group_search,
                "pagination": {
                    "page": page_obj.number,
                    "per_page": per_page,
                    "total": paginator.count,
                    "total_pages": paginator.num_pages,
                    "has_previous": page_obj.has_previous(),
                    "has_next": page_obj.has_next(),
                    "previous_page": page_obj.previous_page_number()
                    if page_obj.has_previous()
                    else None,
                    "next_page": page_obj.next_page_number()
                    if page_obj.has_next()
                    else None,
                },
            }
        )


class CredentialApiListView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """凭证列表API接口，返回JSON格式"""
    permission_resource = "credentials"
    permission_action = "read"

    def get(self, request):
        """返回所有凭证的基本信息列表"""
        credentials = Credential.objects.filter(is_enabled=True).order_by("name")
        data = [
            {
                "id": c.id,
                "name": c.name,
                "username": c.username,
                "auth_type": c.auth_type,
                "auth_type_display": c.get_auth_type_display(),
                "is_enabled": c.is_enabled,
            }
            for c in credentials
        ]
        return JsonResponse({"success": True, "data": data})