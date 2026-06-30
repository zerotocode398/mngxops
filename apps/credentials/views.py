from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.http import JsonResponse
from django.views.generic import ListView, CreateView, UpdateView, DeleteView, View
from django.urls import reverse_lazy, reverse
from django.shortcuts import get_object_or_404, redirect
from django.db.models import Count, Q
from django.db import close_old_connections
from django.core.paginator import Paginator
from django.utils import timezone

from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

from .forms import CredentialForm
from .models import Credential, CredentialEnableTask
from apps.releases.models import TaskCenterTask
from apps.users.permissions import PermissionRequiredMixin
from apps.nodes.models import Node
from utils.ssh import test_ssh_connection
from utils.pagination import PerPagePaginationMixin


def _run_credential_enable_task(task_id, credential_id):
    # Ensure thread owns a clean DB connection.
    close_old_connections()

    try:
        task = CredentialEnableTask.objects.get(pk=task_id)
        credential = Credential.objects.get(pk=credential_id)
        center_task_id = task.task_center_id

        task.status = "running"
        task.started_at = timezone.now()
        task.save(update_fields=["status", "started_at", "updated_at"])

        if center_task_id:
            TaskCenterTask.objects.filter(pk=center_task_id).update(
                status="running",
                started_at=timezone.now(),
                progress=0,
                detail="凭证启用后后台测试开始",
            )

        nodes = list(Node.objects.filter(credential=credential, is_locked=False).order_by("id"))
        task.total_count = len(nodes)
        task.skipped_count = Node.objects.filter(
            credential=credential, is_locked=True
        ).count()
        task.save(update_fields=["total_count", "skipped_count", "updated_at"])

        if not nodes:
            task.status = "completed"
            task.finished_at = timezone.now()
            task.message = "无可测试节点"
            task.save(update_fields=["status", "finished_at", "message", "updated_at"])
            if center_task_id:
                TaskCenterTask.objects.filter(pk=center_task_id).update(
                    status="success",
                    progress=100,
                    finished_at=timezone.now(),
                    result="无可测试节点",
                )
            return

        max_workers = min(10, len(nodes))

        def _test_node(node):
            try:
                if credential.auth_type == "password":
                    success, _ = test_ssh_connection(
                        node.ip,
                        node.port,
                        credential.username,
                        password=credential.get_password(),
                    )
                else:
                    success, _ = test_ssh_connection(
                        node.ip,
                        node.port,
                        credential.username,
                        private_key=credential.get_private_key(),
                    )
                node.status = "online" if success else "offline"
                node.save(update_fields=["status", "updated_at"])
                return success
            except Exception:
                node.status = "offline"
                node.save(update_fields=["status", "updated_at"])
                return False

        success_count = 0
        fail_count = 0
        completed_count = 0

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(_test_node, n) for n in nodes]
            for future in as_completed(futures):
                completed_count += 1
                if future.result():
                    success_count += 1
                else:
                    fail_count += 1

                CredentialEnableTask.objects.filter(pk=task.pk).update(
                    completed_count=completed_count,
                    success_count=success_count,
                    failed_count=fail_count,
                    updated_at=timezone.now(),
                )
                if center_task_id:
                    TaskCenterTask.objects.filter(pk=center_task_id).update(
                        progress=int((completed_count / len(nodes)) * 100),
                        detail=f"执行中：成功 {success_count}，失败 {fail_count}，共 {len(nodes)}",
                        updated_at=timezone.now(),
                    )

        task.refresh_from_db()
        task.status = "completed"
        task.finished_at = timezone.now()
        task.message = (
            f"自动测试完成：成功 {task.success_count}，失败 {task.failed_count}"
            + (f"，锁定跳过 {task.skipped_count}" if task.skipped_count else "")
        )
        task.save(
            update_fields=["status", "finished_at", "message", "updated_at"]
        )
        if center_task_id:
            TaskCenterTask.objects.filter(pk=center_task_id).update(
                status="success" if task.failed_count == 0 else "failed",
                progress=100,
                finished_at=timezone.now(),
                result=task.message,
                detail=task.message,
            )
    except Exception as exc:
        CredentialEnableTask.objects.filter(pk=task_id).update(
            status="failed",
            finished_at=timezone.now(),
            message=f"任务失败: {exc}",
            updated_at=timezone.now(),
        )
        try:
            failed_task = CredentialEnableTask.objects.get(pk=task_id)
            if failed_task.task_center_id:
                TaskCenterTask.objects.filter(pk=failed_task.task_center_id).update(
                    status="failed",
                    finished_at=timezone.now(),
                    progress=100,
                    result=f"任务失败: {exc}",
                    detail=f"任务失败: {exc}",
                )
        except CredentialEnableTask.DoesNotExist:
            pass
    finally:
        close_old_connections()


class CredentialListView(
    LoginRequiredMixin, PermissionRequiredMixin, PerPagePaginationMixin, ListView
):
    model = Credential
    template_name = "credentials/list.html"
    context_object_name = "credentials"
    paginate_by = 10
    ordering = ["-created_at"]
    permission_resource = "credentials"
    permission_action = "read"

    def get_queryset(self):
        queryset = super().get_queryset().annotate(node_count=Count("node", distinct=True))
        search = self.request.GET.get("search", "").strip()

        if search:
            terms = [t.strip() for t in search.replace("，", ",").split(",") if t.strip()]
            if terms:
                for term in terms:
                    queryset = queryset.filter(
                        Q(name__icontains=term) | Q(username__icontains=term)
                    )

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["search"] = self.request.GET.get("search", "")

        return context


class CredentialCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    model = Credential
    form_class = CredentialForm
    template_name = "credentials/create.html"
    success_url = reverse_lazy("credentials:list")
    permission_resource = "credentials"
    permission_action = "create"

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        messages.success(self.request, f"凭证 {form.instance.name} 创建成功")
        return super().form_valid(form)

    def form_invalid(self, form):
        messages.error(self.request, "凭证创建失败，请检查输入")
        return super().form_invalid(form)


class CredentialUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = Credential
    form_class = CredentialForm
    template_name = "credentials/edit.html"
    success_url = reverse_lazy("credentials:list")
    permission_resource = "credentials"
    permission_action = "update"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        credential = self.get_object()
        context["has_password"] = bool(credential.password)
        context["has_private_key"] = bool(credential.private_key)
        return context

    def form_valid(self, form):
        messages.success(self.request, f"凭证 {form.instance.name} 更新成功")
        return super().form_valid(form)

    def form_invalid(self, form):
        messages.error(self.request, "凭证更新失败，请检查输入")
        return super().form_invalid(form)


class CredentialDeleteView(LoginRequiredMixin, PermissionRequiredMixin, DeleteView):
    model = Credential
    template_name = "credentials/delete.html"
    success_url = reverse_lazy("credentials:list")
    permission_resource = "credentials"
    permission_action = "delete"

    def post(self, request, *args, **kwargs):
        credential = self.get_object()
        messages.success(request, f"凭证 {credential.name} 删除成功")
        return super().post(request, *args, **kwargs)


class CredentialToggleEnableView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_resource = "credentials"
    permission_action = "update"

    def post(self, request, pk):
        credential = get_object_or_404(Credential, pk=pk)
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

        if credential.is_enabled:
            credential.is_enabled = False
            credential.save(update_fields=["is_enabled"])
            affected = Node.objects.filter(credential=credential).update(status="offline")
            messages.success(
                request,
                f"凭证 {credential.name} 已禁用，{affected} 个关联节点状态已更新为离线",
            )
        else:
            credential.is_enabled = True
            credential.save(update_fields=["is_enabled"])

            Node.objects.filter(credential=credential, is_locked=False).update(status="unknown")

            center_task = TaskCenterTask.objects.create(
                operation_type="credential_enable_test",
                status="pending",
                detail="凭证已启用，后台测试任务已创建",
                target_configs=credential.name,
                trigger_user=request.user,
            )

            task = CredentialEnableTask.objects.create(
                credential=credential,
                status="pending",
                message="任务已创建，等待执行",
                task_center_id=center_task.id,
            )
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
    permission_resource = "credentials"
    permission_action = "read"

    def get(self, request, pk):
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
    permission_resource = "credentials"
    permission_action = "read"

    def get(self, request, pk):
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
    permission_resource = "credentials"
    permission_action = "read"

    def get(self, request, pk):
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

