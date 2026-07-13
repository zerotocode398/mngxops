from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.db.models import Q
from django.utils import timezone
from django.http import JsonResponse
from django.shortcuts import redirect, get_object_or_404
from django.views.generic import ListView, CreateView, UpdateView, DeleteView, View
from django.urls import reverse_lazy
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from .forms import NodeForm, NodeGroupForm
from .models import Node, NodeGroup
from apps.credentials.models import Credential
from apps.releases.models import TaskCenterTask
from apps.users.permissions import PermissionRequiredMixin, user_has_permission
from utils.ssh import test_ssh_connection, get_nginx_version, get_system_info
from utils.pagination import PerPagePaginationMixin


def _get_node_credential(node):
    return node.credential


# ========== 节点组视图（仅 Admin）==========


class NodeGroupListView(
    LoginRequiredMixin,
    PermissionRequiredMixin,
    PerPagePaginationMixin,
    ListView,
):
    model = NodeGroup
    template_name = "nodes/group_list.html"
    context_object_name = "node_groups"
    paginate_by = 10
    ordering = ["-created_at"]
    permission_resource = "nodes"
    permission_action = "read"

    def get_queryset(self):
        queryset = super().get_queryset()
        search = self.request.GET.get("search", "")
        if search:
            terms = [
                t.strip() for t in search.replace("，", ",").split(",") if t.strip()
            ]
            if terms:
                for term in terms:
                    queryset = queryset.filter(Q(name__icontains=term))
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["search"] = self.request.GET.get("search", "")
        context["all_nodes"] = Node.objects.all()
        return context


def _assign_nodes_to_group(node_group, node_ids):
    desired_ids = set(int(nid) for nid in node_ids)
    current_ids = set(node_group.nodes.values_list("id", flat=True))

    to_add_ids = desired_ids - current_ids
    to_remove_ids = current_ids - desired_ids

    for node_id in to_add_ids:
        try:
            node = Node.objects.get(pk=node_id)
            if node.groups.count() < 3:
                node.groups.add(node_group)
        except Node.DoesNotExist:
            pass

    for node_id in to_remove_ids:
        try:
            node = Node.objects.get(pk=node_id)
            node.groups.remove(node_group)
        except Node.DoesNotExist:
            pass


class NodeSearchAPIView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_resource = "nodes"
    permission_action = "read"

    def get(self, request):
        search = request.GET.get("search", "").strip()
        group_search = request.GET.get("group_search", "").strip()

        queryset = Node.objects.filter(is_locked=False).prefetch_related("groups")

        if search:
            terms = [
                t.strip() for t in search.replace("，", ",").split(",") if t.strip()
            ]
            for term in terms:
                queryset = queryset.filter(
                    Q(hostname__icontains=term) | Q(ip__icontains=term)
                )

        if group_search:
            terms = [
                t.strip()
                for t in group_search.replace("，", ",").split(",")
                if t.strip()
            ]
            for term in terms:
                queryset = queryset.filter(groups__name__icontains=term)
            queryset = queryset.distinct()

        nodes = queryset.order_by("hostname")[:50]
        data = []
        for node in nodes:
            data.append(
                {
                    "id": node.id,
                    "hostname": node.hostname,
                    "ip": node.ip,
                    "status": node.status,
                    "groups": [{"id": g.id, "name": g.name} for g in node.groups.all()],
                    "groups_count": node.groups.count(),
                }
            )

        return JsonResponse({"success": True, "nodes": data})


class NodeGroupCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    model = NodeGroup
    form_class = NodeGroupForm
    template_name = "nodes/group_create.html"
    success_url = reverse_lazy("nodes:group_list")
    permission_resource = "nodes"
    permission_action = "create"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["all_nodes"] = (
            Node.objects.filter(is_locked=False)
            .prefetch_related("groups")
            .order_by("hostname")
        )
        return context

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        response = super().form_valid(form)
        node_ids = self.request.POST.getlist("node_ids", [])
        if node_ids:
            _assign_nodes_to_group(self.object, node_ids)
        messages.success(self.request, f"节点组 {form.instance.name} 创建成功")
        return response

    def form_invalid(self, form):
        messages.error(self.request, "节点组创建失败，请检查输入")
        return super().form_invalid(form)


class NodeGroupUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = NodeGroup
    form_class = NodeGroupForm
    template_name = "nodes/group_edit.html"
    success_url = reverse_lazy("nodes:group_list")
    permission_resource = "nodes"
    permission_action = "update"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["all_nodes"] = (
            Node.objects.filter(is_locked=False)
            .prefetch_related("groups")
            .order_by("hostname")
        )
        context["current_node_ids"] = list(
            self.object.nodes.values_list("id", flat=True)
        )
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        node_ids = self.request.POST.getlist("node_ids", [])
        _assign_nodes_to_group(self.object, node_ids)
        messages.success(self.request, f"节点组 {form.instance.name} 更新成功")
        return response

    def form_invalid(self, form):
        messages.error(self.request, "节点组更新失败，请检查输入")
        return super().form_invalid(form)


class NodeGroupDeleteView(LoginRequiredMixin, PermissionRequiredMixin, DeleteView):
    model = NodeGroup
    template_name = "nodes/group_delete.html"
    success_url = reverse_lazy("nodes:group_list")
    permission_resource = "nodes"
    permission_action = "delete"

    def post(self, request, *args, **kwargs):
        node_group = self.get_object()
        messages.success(request, f"节点组 {node_group.name} 删除成功")
        return super().post(request, *args, **kwargs)


class NodeGroupManageNodesView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_resource = "nodes"
    permission_action = "update"

    def post(self, request, pk):
        node_group = get_object_or_404(NodeGroup, pk=pk)
        desired_ids = set(int(nid) for nid in request.POST.getlist("node_ids", []))
        current_ids = set(node_group.nodes.values_list("id", flat=True))

        to_add_ids = desired_ids - current_ids
        to_remove_ids = current_ids - desired_ids

        added = 0
        skipped = 0
        for node_id in to_add_ids:
            node = get_object_or_404(Node, pk=node_id)
            if node.groups.count() >= 3:
                skipped += 1
                continue
            node.groups.add(node_group)
            added += 1

        removed = 0
        for node_id in to_remove_ids:
            node = get_object_or_404(Node, pk=node_id)
            node.groups.remove(node_group)
            removed += 1

        parts = []
        if added:
            parts.append(f"添加 {added} 个")
        if removed:
            parts.append(f"移除 {removed} 个")
        if skipped:
            parts.append(f"跳过 {skipped} 个（已达上限）")
        if parts:
            messages.success(request, f"节点组 {node_group.name}：{', '.join(parts)}")
        else:
            messages.info(request, "节点组成员未发生变化")
        return redirect("nodes:group_list")


# ========== 节点视图 ==========


class NodeListView(
    LoginRequiredMixin, PermissionRequiredMixin, PerPagePaginationMixin, ListView
):
    model = Node
    template_name = "nodes/list.html"
    context_object_name = "nodes"
    paginate_by = 10
    ordering = ["-created_at"]
    permission_resource = "nodes"
    permission_action = "read"

    def get_queryset(self):
        """根据搜索、节点组、环境、状态参数筛选节点"""
        queryset = (
            super().get_queryset()
            .select_related("credential")
            .prefetch_related("groups")
        )
        search = self.request.GET.get("search", "").strip()
        group_search = self.request.GET.get("group_search", "").strip()
        env_filter = self.request.GET.get("environment", "").strip()
        status_filter = self.request.GET.get("status", "").strip()

        if search:
            queryset = queryset.filter(
                Q(hostname__icontains=search) | Q(ip__icontains=search)
            )

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

        if env_filter:
            queryset = queryset.filter(environment=env_filter)
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        return queryset

    def get_context_data(self, **kwargs):
        """添加搜索条件、节点组、环境选项到模板上下文"""
        context = super().get_context_data(**kwargs)
        context["search"] = self.request.GET.get("search", "")
        context["group_search"] = self.request.GET.get("group_search", "")
        context["env_filter"] = self.request.GET.get("environment", "")
        context["status_filter"] = self.request.GET.get("status", "")
        context["env_choices"] = dict(Node.ENV_CHOICES)
        context["status_choices"] = dict(Node.STATUS_CHOICES)
        context["node_groups_list"] = NodeGroup.objects.all().order_by("name")
        context["node_groups"] = {
            node.id: list(node.groups.all()) for node in context["nodes"]
        }
        return context


class NodeCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    model = Node
    form_class = NodeForm
    template_name = "nodes/create.html"
    success_url = reverse_lazy("nodes:list")
    permission_resource = "nodes"
    permission_action = "create"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["query_string"] = self.request.GET.urlencode()
        return context

    def get_success_url(self):
        url = reverse_lazy("nodes:list")
        qs = self.request.GET.urlencode()
        if qs:
            url = f"{url}?{qs}"
        return url

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        form.instance.status = "unknown"
        messages.success(self.request, f"节点 {form.instance.hostname} 创建成功")
        return super().form_valid(form)

    def form_invalid(self, form):
        messages.error(self.request, "节点创建失败，请检查输入")
        return super().form_invalid(form)


class NodeUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = Node
    form_class = NodeForm
    template_name = "nodes/edit.html"
    permission_resource = "nodes"
    permission_action = "update"

    # Locked nodes can still be edited - only remote operations are blocked

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["query_string"] = self.request.GET.urlencode()
        return context

    def get_success_url(self):
        url = reverse_lazy("nodes:list")
        qs = self.request.GET.urlencode()
        if qs:
            url = f"{url}?{qs}"
        return url

    def form_valid(self, form):
        messages.success(self.request, f"节点 {form.instance.hostname} 更新成功")
        return super().form_valid(form)

    def form_invalid(self, form):
        messages.error(self.request, "节点更新失败，请检查输入")
        return super().form_invalid(form)


class NodeDeleteView(LoginRequiredMixin, PermissionRequiredMixin, DeleteView):
    model = Node
    template_name = "nodes/delete.html"
    success_url = reverse_lazy("nodes:list")
    permission_resource = "nodes"
    permission_action = "delete"

    # Locked nodes can still be deleted - only remote operations are blocked

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["query_string"] = self.request.GET.urlencode()
        return context

    def get_success_url(self):
        url = reverse_lazy("nodes:list")
        qs = self.request.GET.urlencode()
        if qs:
            url = f"{url}?{qs}"
        return url

    def post(self, request, *args, **kwargs):
        node = self.get_object()
        messages.success(request, f"节点 {node.hostname} 删除成功")
        return super().post(request, *args, **kwargs)


def node_lock(request):
    if not request.user.is_authenticated:
        return JsonResponse({"success": False, "message": "请先登录"})
    if not user_has_permission(request.user, "nodes", "update"):
        return JsonResponse({"success": False, "message": "无权限执行该操作"})

    if request.method == "POST":
        import json
        from concurrent.futures import ThreadPoolExecutor, as_completed

        data = json.loads(request.body)
        action = data.get("action", "lock")
        node_ids = data.get("node_ids", [])
        if not node_ids and data.get("node_id"):
            node_ids = [data.get("node_id")]

        if not node_ids:
            return JsonResponse({"success": False, "message": "未指定节点"})

        MAX_BATCH = 3
        if len(node_ids) > MAX_BATCH:
            return JsonResponse(
                {"success": False, "message": f"最多只能操作 {MAX_BATCH} 个节点"}
            )

        nodes = Node.objects.filter(id__in=node_ids).order_by("id")
        if not nodes:
            return JsonResponse({"success": False, "message": "节点不存在"})

        if action == "lock":
            updated_count = nodes.update(is_locked=True, status="offline")
            hostnames = list(nodes.values_list("hostname", flat=True))
            return JsonResponse(
                {
                    "success": True,
                    "message": f"已锁定 {updated_count} 个节点",
                    "hostnames": hostnames,
                    "action": "lock",
                }
            )
        elif action == "unlock":
            nodes.update(is_locked=False)

            def _unlock_test(node):
                try:
                    credential = _get_node_credential(node)
                    if not credential:
                        node.status = "unknown"
                        node.save()
                        return {
                            "node_id": node.id,
                            "hostname": node.hostname,
                            "ip": node.ip,
                            "success": False,
                            "message": "未配置凭证",
                        }
                    if not credential.is_enabled:
                        node.status = "offline"
                        node.save()
                        return {
                            "node_id": node.id,
                            "hostname": node.hostname,
                            "ip": node.ip,
                            "success": False,
                            "message": "关联凭证已禁用",
                        }

                    if credential.auth_type == "password":
                        success, message = test_ssh_connection(
                            node.ip,
                            node.port,
                            credential.username,
                            password=credential.get_password(),
                        )
                    else:
                        success, message = test_ssh_connection(
                            node.ip,
                            node.port,
                            credential.username,
                            private_key=credential.get_private_key(),
                        )

                    if success:
                        node.status = "online"
                        nginx_path = node.nginx_path if node.nginx_path else None
                        version_success, version_info = get_nginx_version(
                            node.ip,
                            node.port,
                            credential.username,
                            password=(
                                credential.get_password()
                                if credential.auth_type == "password"
                                else None
                            ),
                            private_key=(
                                credential.get_private_key()
                                if credential.auth_type == "key"
                                else None
                            ),
                            nginx_path=nginx_path,
                        )
                        if version_success:
                            node.nginx_version = version_info
                    else:
                        node.status = "offline"

                    node.save()
                    return {
                        "node_id": node.id,
                        "hostname": node.hostname,
                        "ip": node.ip,
                        "success": success,
                        "message": message,
                    }
                except Exception as e:
                    return {
                        "node_id": node.id,
                        "hostname": node.hostname,
                        "ip": node.ip,
                        "success": False,
                        "message": str(e),
                    }

            results = []
            with ThreadPoolExecutor(max_workers=MAX_BATCH) as executor:
                future_to_node = {
                    executor.submit(_unlock_test, node): node for node in nodes
                }
                for future in as_completed(future_to_node):
                    result = future.result()
                    results.append(result)

            return JsonResponse(
                {
                    "success": True,
                    "message": "已解锁并完成连接测试",
                    "action": "unlock",
                    "results": results,
                }
            )
        else:
            return JsonResponse({"success": False, "message": "无效的操作"})

    return JsonResponse({"success": False, "message": "仅支持POST请求"})


def test_node_connection(request):
    if not request.user.is_authenticated:
        return JsonResponse({"success": False, "message": "请先登录"})
    if not user_has_permission(request.user, "nodes", "update"):
        return JsonResponse({"success": False, "message": "无权限执行该操作"})

    if request.method == "POST":
        import json

        data = json.loads(request.body)

        try:
            node_id = data.get("node_id")
            ip = data.get("ip")
            port = data.get("port")
            credential_id = data.get("credential_id")

            credential = None
            host = None
            ssh_port = 22

            if node_id:
                node = Node.objects.get(id=node_id)
                if node.is_locked:
                    return JsonResponse(
                        {"success": False, "message": "该节点已锁定，无法测试连接"}
                    )

                if credential_id:
                    credential = Credential.objects.get(id=credential_id)
                else:
                    credential = _get_node_credential(node)

                if not credential:
                    return JsonResponse(
                        {
                            "success": False,
                            "message": "节点未配置凭证，请先关联SSH凭证",
                        }
                    )
                if not credential.is_enabled:
                    return JsonResponse(
                        {
                            "success": False,
                            "message": "节点关联凭证已禁用，请先在凭证列表启用",
                        }
                    )
                host = ip if ip else node.ip
                ssh_port = int(port) if port else node.port
            elif ip and port and credential_id:
                credential = Credential.objects.get(id=credential_id)
                if not credential.is_enabled:
                    return JsonResponse(
                        {
                            "success": False,
                            "message": "凭证已禁用，请先在凭证列表启用",
                        }
                    )
                host = ip
                ssh_port = int(port)
            else:
                return JsonResponse({"success": False, "message": "缺少必要参数"})

            if credential.auth_type == "password":
                success, message = test_ssh_connection(
                    host,
                    ssh_port,
                    credential.username,
                    password=credential.get_password(),
                )
            else:
                success, message = test_ssh_connection(
                    host,
                    ssh_port,
                    credential.username,
                    private_key=credential.get_private_key(),
                )

            if success and node_id:
                node = Node.objects.get(id=node_id)
                node.status = "online"

                nginx_path = node.nginx_path if node.nginx_path else None
                version_success, version_info = get_nginx_version(
                    host,
                    ssh_port,
                    credential.username,
                    password=(
                        credential.get_password()
                        if credential.auth_type == "password"
                        else None
                    ),
                    private_key=(
                        credential.get_private_key()
                        if credential.auth_type == "key"
                        else None
                    ),
                    nginx_path=nginx_path,
                )
                if version_success:
                    node.nginx_version = version_info

                node.save()
            elif not success and node_id:
                node = Node.objects.get(id=node_id)
                node.status = "offline"
                node.save()

            return JsonResponse(
                {
                    "success": success,
                    "message": message,
                    "status_updated": bool(node_id),
                }
            )
        except Node.DoesNotExist:
            return JsonResponse({"success": False, "message": "节点不存在"})
        except Credential.DoesNotExist:
            return JsonResponse({"success": False, "message": "凭证不存在"})
        except Exception as e:
            return JsonResponse({"success": False, "message": str(e)})

    return JsonResponse({"success": False, "message": "仅支持POST请求"})


def batch_test_node_connection(request):
    if not request.user.is_authenticated:
        return JsonResponse({"success": False, "message": "请先登录"})
    if not user_has_permission(request.user, "nodes", "update"):
        return JsonResponse({"success": False, "message": "无权限执行该操作"})

    if request.method == "POST":
        import json

        data = json.loads(request.body)
        node_ids = data.get("node_ids", [])

        if not node_ids:
            return JsonResponse({"success": False, "message": "未选择任何节点"})

        MAX_BATCH = 3
        if len(node_ids) > MAX_BATCH:
            return JsonResponse(
                {"success": False, "message": f"最多只能测试 {MAX_BATCH} 个节点"}
            )

        nodes = list(Node.objects.filter(id__in=node_ids).order_by("id"))
        total = len(nodes)

        task_center = TaskCenterTask.objects.create(
            operation_type="node_batch_test",
            status="pending",
            detail="任务已创建，等待执行",
            target_hostnames=",".join(node.hostname for node in nodes),
            target_ips=",".join(node.ip for node in nodes),
            trigger_user=request.user,
        )

        def _test_one(node):
            try:
                if node.is_locked:
                    return {
                        "node_id": node.id,
                        "hostname": node.hostname,
                        "ip": node.ip,
                        "success": False,
                        "message": "节点已锁定",
                    }
                credential = _get_node_credential(node)
                if not credential:
                    return {
                        "node_id": node.id,
                        "hostname": node.hostname,
                        "ip": node.ip,
                        "success": False,
                        "message": "未配置凭证",
                    }
                if not credential.is_enabled:
                    return {
                        "node_id": node.id,
                        "hostname": node.hostname,
                        "ip": node.ip,
                        "success": False,
                        "message": "关联凭证已禁用",
                    }

                if credential.auth_type == "password":
                    success, message = test_ssh_connection(
                        node.ip,
                        node.port,
                        credential.username,
                        password=credential.get_password(),
                    )
                else:
                    success, message = test_ssh_connection(
                        node.ip,
                        node.port,
                        credential.username,
                        private_key=credential.get_private_key(),
                    )

                if success:
                    node.status = "online"
                    nginx_path = node.nginx_path if node.nginx_path else None
                    version_success, version_info = get_nginx_version(
                        node.ip,
                        node.port,
                        credential.username,
                        password=(
                            credential.get_password()
                            if credential.auth_type == "password"
                            else None
                        ),
                        private_key=(
                            credential.get_private_key()
                            if credential.auth_type == "key"
                            else None
                        ),
                        nginx_path=nginx_path,
                    )
                    if version_success:
                        node.nginx_version = version_info
                else:
                    node.status = "offline"

                node.save()

                return {
                    "node_id": node.id,
                    "hostname": node.hostname,
                    "ip": node.ip,
                    "success": success,
                    "message": message,
                }
            except Exception as e:
                return {
                    "node_id": node.id,
                    "hostname": node.hostname,
                    "ip": node.ip,
                    "success": False,
                    "message": str(e),
                }

        def _run_batch_test_task(task_id, test_nodes):
            TaskCenterTask.objects.filter(pk=task_id).update(
                status="running",
                started_at=timezone.now(),
                progress=0,
                detail=f"执行中：0/{len(test_nodes)}",
            )

            success_count = 0
            fail_count = 0
            done = 0
            detail_lines = []

            with ThreadPoolExecutor(max_workers=MAX_BATCH) as executor:
                future_to_node = {
                    executor.submit(_test_one, node): node for node in test_nodes
                }
                for future in as_completed(future_to_node):
                    result = future.result()
                    done += 1
                    if result.get("success"):
                        success_count += 1
                        detail_lines.append(
                            f"[成功] {result['hostname']}({result['ip']}) - {result.get('message','')}"
                        )
                    else:
                        fail_count += 1
                        detail_lines.append(
                            f"[失败] {result['hostname']}({result['ip']}) - {result.get('message','')}"
                        )

                    TaskCenterTask.objects.filter(pk=task_id).update(
                        progress=(
                            int(done * 100 / len(test_nodes)) if test_nodes else 100
                        ),
                        detail=f"执行中：成功 {success_count}，失败 {fail_count}，已完成 {done}/{len(test_nodes)}",
                        updated_at=timezone.now(),
                    )

            status = "success" if fail_count == 0 else "failed"
            TaskCenterTask.objects.filter(pk=task_id).update(
                status=status,
                progress=100,
                finished_at=timezone.now(),
                detail=f"执行完成：成功 {success_count}，失败 {fail_count}，共 {len(test_nodes)}",
                result="\n".join(detail_lines),
                updated_at=timezone.now(),
            )

        thread = threading.Thread(
            target=_run_batch_test_task,
            args=(task_center.id, nodes),
            daemon=True,
        )
        thread.start()

        return JsonResponse(
            {
                "success": True,
                "async": True,
                "message": f"已创建后台测试任务（{total} 台）",
                "task_center_id": task_center.id,
                "task_center_detail_url": str(
                    reverse_lazy("releases:task_center_detail", args=[task_center.id])
                ),
                "task_center_home_url": str(reverse_lazy("releases:history")),
            }
        )

    return JsonResponse({"success": False, "message": "仅支持POST请求"})


class NodeListAPIView(LoginRequiredMixin, View):
    def get(self, request):
        page = int(request.GET.get("page", 1))
        page_size = int(request.GET.get("page_size", 10))
        search = request.GET.get("search", "")
        group_search = request.GET.get("group_search", "")
        env_filter = request.GET.get("environment", "")
        status_filter = request.GET.get("status", "")

        queryset = (
            Node.objects.all()
            .select_related("credential")
            .prefetch_related("groups")
            .order_by("hostname")
        )

        if search:
            terms = [
                t.strip() for t in search.replace("，", ",").split(",") if t.strip()
            ]
            if terms:
                for term in terms:
                    queryset = queryset.filter(
                        Q(hostname__icontains=term) | Q(ip__icontains=term)
                    )
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
        if env_filter:
            queryset = queryset.filter(environment=env_filter)
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        total = queryset.count()
        total_pages = max(1, (total + page_size - 1) // page_size)
        offset = (page - 1) * page_size
        nodes = queryset[offset : offset + page_size]

        data = []
        for node in nodes:
            data.append(
                {
                    "id": node.id,
                    "hostname": node.hostname,
                    "ip": node.ip,
                    "environment": node.environment,
                    "status": node.status,
                    "has_credential": node.credential is not None,
                    "groups": [{"id": g.id, "name": g.name} for g in node.groups.all()],
                }
            )

        return JsonResponse(
            {
                "success": True,
                "data": data,
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages,
            }
        )


class NodeGroupListAPIView(LoginRequiredMixin, View):
    def get(self, request):
        groups = NodeGroup.objects.all().prefetch_related("nodes").order_by("name")

        data = []
        for g in groups:
            nodes_data = []
            for n in g.nodes.all().order_by("hostname"):
                nodes_data.append(
                    {
                        "id": n.id,
                        "hostname": n.hostname,
                        "ip": n.ip,
                        "environment": n.environment,
                        "status": n.status,
                        "has_credential": n.credential is not None,
                    }
                )
            data.append(
                {
                    "id": g.id,
                    "name": g.name,
                    "description": g.description,
                    "node_count": len(nodes_data),
                    "nodes": nodes_data,
                }
            )

        return JsonResponse({"success": True, "data": data})


def get_node_detail(request):
    if not request.user.is_authenticated:
        return JsonResponse({"success": False, "message": "请先登录"})
    if not user_has_permission(request.user, "nodes", "read"):
        return JsonResponse({"success": False, "message": "无权限执行该操作"})

    if request.method == "POST":
        import json

        data = json.loads(request.body)
        node_id = data.get("node_id")

        try:
            node = Node.objects.get(id=node_id)
            credential = _get_node_credential(node)

            node_info = {
                "id": node.id,
                "hostname": node.hostname,
                "ip": node.ip,
                "port": node.port,
                "environment": node.get_environment_display(),
                "nginx_version": node.nginx_version or "未获取",
                "nginx_path": node.nginx_path or "默认",
                "status": node.get_status_display(),
                "is_locked": node.is_locked,
                "description": node.description or "无描述",
                "credential_name": credential.name if credential else "未配置",
                "credential_username": credential.username if credential else "-",
                "credential_auth_type": (
                    credential.get_auth_type_display() if credential else "-"
                ),
                "groups": ", ".join(
                    g.name for g in node.groups.all()
                ) or "-",
                "created_by": node.created_by.username,
                "created_at": node.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                "updated_at": node.updated_at.strftime("%Y-%m-%d %H:%M:%S"),
            }

            try:
                if credential:
                    if not credential.is_enabled:
                        node.status = "offline"
                        node.save()
                        node_info["system_info"] = None
                        return JsonResponse({"success": True, "node_info": node_info})

                    if credential.auth_type == "password":
                        success, system_info = get_system_info(
                            node.ip,
                            node.port,
                            credential.username,
                            password=credential.get_password(),
                        )
                    else:
                        success, system_info = get_system_info(
                            node.ip,
                            node.port,
                            credential.username,
                            private_key=credential.get_private_key(),
                        )

                    if success:
                        node.status = "online"
                        node.save()
                        node_info["system_info"] = system_info
                    else:
                        node.status = "offline"
                        node.save()
                        node_info["system_info"] = None
                else:
                    node_info["system_info"] = None
            except:
                node_info["system_info"] = None

            return JsonResponse({"success": True, "node_info": node_info})
        except Node.DoesNotExist:
            return JsonResponse({"success": False, "message": "节点不存在"})
        except Exception as e:
            return JsonResponse({"success": False, "message": str(e)})

    return JsonResponse({"success": False, "message": "仅支持POST请求"})


def get_node_system_info(request):
    if not request.user.is_authenticated:
        return JsonResponse({"success": False, "message": "请先登录"})
    if not user_has_permission(request.user, "nodes", "read"):
        return JsonResponse({"success": False, "message": "无权限执行该操作"})

    if request.method == "POST":
        import json

        data = json.loads(request.body)
        node_id = data.get("node_id")

        try:
            node = Node.objects.get(id=node_id)
            credential = _get_node_credential(node)
            if not credential:
                return JsonResponse({"success": False, "message": "节点未配置SSH凭证"})
            if not credential.is_enabled:
                return JsonResponse({"success": False, "message": "节点关联凭证已禁用"})

            if credential.auth_type == "password":
                success, system_info = get_system_info(
                    node.ip,
                    node.port,
                    credential.username,
                    password=credential.get_password(),
                )
            else:
                success, system_info = get_system_info(
                    node.ip,
                    node.port,
                    credential.username,
                    private_key=credential.get_private_key(),
                )

            if success:
                node.status = "online"
                node.save()
                return JsonResponse({"success": True, "system_info": system_info})
            else:
                node.status = "offline"
                node.save()
                return JsonResponse({"success": False, "message": system_info})
        except Node.DoesNotExist:
            return JsonResponse({"success": False, "message": "节点不存在"})
        except Exception as e:
            return JsonResponse({"success": False, "message": str(e)})

    return JsonResponse({"success": False, "message": "仅支持POST请求"})


def get_node_nginx_version(request):
    if not request.user.is_authenticated:
        return JsonResponse({"success": False, "message": "请先登录"})
    if not user_has_permission(request.user, "nodes", "read"):
        return JsonResponse({"success": False, "message": "无权限执行该操作"})

    if request.method == "POST":
        import json

        data = json.loads(request.body)
        node_id = data.get("node_id")
        try:
            node = Node.objects.get(id=node_id)
            credential = _get_node_credential(node)
            if not credential:
                return JsonResponse({"success": False, "message": "节点未配置SSH凭证"})
            if not credential.is_enabled:
                return JsonResponse({"success": False, "message": "节点关联凭证已禁用"})
            nginx_path = node.nginx_path if node.nginx_path else None

            if credential.auth_type == "password":
                success, output = get_nginx_version(
                    node.ip,
                    node.port,
                    credential.username,
                    password=credential.get_password(),
                    nginx_path=nginx_path,
                )
            else:
                success, output = get_nginx_version(
                    node.ip,
                    node.port,
                    credential.username,
                    private_key=credential.get_private_key(),
                    nginx_path=nginx_path,
                )

            if success:
                node.nginx_version = output
                node.status = "online"
                node.save()
                return JsonResponse({"success": True, "version": output})
            else:
                node.status = "offline"
                node.save()
                return JsonResponse({"success": False, "message": output})
        except Node.DoesNotExist:
            return JsonResponse({"success": False, "message": "节点不存在"})
        except Exception as e:
            return JsonResponse({"success": False, "message": str(e)})

    return JsonResponse({"success": False, "message": "仅支持POST请求"})
