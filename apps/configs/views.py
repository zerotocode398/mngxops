"""配置管理视图 - 适配 ConfigNodeBinding 模型"""

import difflib
import json
import threading

from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import redirect, get_object_or_404, render
from django.urls import reverse_lazy, reverse
from django.views.generic import ListView, DetailView, UpdateView, CreateView, View

from .forms import ConfigForm, BindingForm
from .models import Config, ConfigNodeBinding, BindingVersion
from .services import (
    get_or_create_sync_setting,
    default_nginx_conf_path,
    preview_glob_configs,
    run_batch_config_sync_task,
    run_single_config_sync_task,
)
from apps.users.permissions import PermissionRequiredMixin
from apps.releases.models import TaskCenterTask
from apps.nodes.models import Node
from utils.pagination import PerPagePaginationMixin
from utils.setting_service import get_setting


def _build_node_stats(node):
    """构建单个节点的绑定状态统计"""
    stats = {
        "total": 0,
        "pending": 0,
        "conflict": 0,
        "orphaned": 0,
        "failed": 0,
        "syncing": 0,
        "marked_deleted": 0,
    }
    for b in node.config_bindings.all():
        stats["total"] += 1
        s = b.sync_status
        if s in ("not_synced", "modified"):
            stats["pending"] += 1
        elif s == "conflict":
            stats["conflict"] += 1
        elif s == "orphaned":
            stats["orphaned"] += 1
        elif s == "failed":
            stats["failed"] += 1
        elif s == "syncing":
            stats["syncing"] += 1
        elif s == "marked_deleted":
            stats["marked_deleted"] += 1
    return stats


def _build_global_status_counts():
    """构建全局绑定状态计数（排除已逻辑删除节点）"""
    base = ConfigNodeBinding.objects.filter(node__is_deleted=False)
    total = base.count()
    pending = base.filter(sync_status__in=["not_synced", "modified"]).count()
    synced = base.filter(sync_status="synced").count()
    orphaned = base.filter(sync_status="orphaned").count()
    failed = base.filter(sync_status="failed").count()
    marked_deleted = base.filter(sync_status="marked_deleted").count()
    return {
        "total": total,
        "pending": pending,
        "synced": synced,
        "orphaned": orphaned,
        "failed": failed,
        "marked_deleted": marked_deleted,
    }


# ==================== 配置标签 CRUD ====================


class ConfigListView(
    LoginRequiredMixin, PermissionRequiredMixin, PerPagePaginationMixin, ListView
):
    """配置列表 - 以节点为基准展示绑定（每个节点展开显示其所有配置绑定）"""

    template_name = "configs/list.html"
    context_object_name = "nodes"
    paginate_by = None
    permission_resource = "configs"
    permission_action = "read"
    default_paginate_by = 10

    def get_queryset(self):
        from apps.nodes.models import Node

        queryset = (
            Node.objects.filter(is_locked=False)
            .prefetch_related("config_bindings__config", "groups")
            .order_by("hostname")
        )
        search = self.request.GET.get("search", "").strip()
        group_id = self.request.GET.get("group_id", "")
        sync_status = self.request.GET.get("sync_status", "").strip()

        if search:
            terms = [
                t.strip() for t in search.replace("，", ",").split(",") if t.strip()
            ]
            for term in terms:
                queryset = queryset.filter(
                    Q(hostname__icontains=term)
                    | Q(ip__icontains=term)
                    | Q(config_bindings__config__name__icontains=term)
                    | Q(config_bindings__remote_path__icontains=term)
                ).distinct()
        if group_id:
            queryset = queryset.filter(groups__id=group_id).distinct()

        if sync_status:
            if sync_status == "pending":
                queryset = queryset.filter(
                    config_bindings__sync_status__in=["not_synced", "modified"]
                ).distinct()
            else:
                queryset = queryset.filter(
                    config_bindings__sync_status=sync_status
                ).distinct()

        nginx_available = self.request.GET.get("nginx_available", "true").strip()
        if nginx_available == "true":
            queryset = queryset.filter(nginx_available=True)

        return queryset

    def get_context_data(self, **kwargs):
        from apps.nodes.models import Node, NodeGroup

        context = super().get_context_data(**kwargs)
        all_nodes = list(self.get_queryset())
        sync_status = self.request.GET.get("sync_status", "").strip()
        search = self.request.GET.get("search", "").strip()
        group_id = self.request.GET.get("group_id", "").strip()
        nginx_available = self.request.GET.get("nginx_available", "true").strip()

        node_stats_map = {node.id: _build_node_stats(node) for node in all_nodes}

        per_page = self.get_paginate_by(None)
        paginator = Paginator(all_nodes, per_page)
        page_num = self.request.GET.get("page", 1)
        page_obj = paginator.get_page(page_num)

        list_query_params = self.request.GET.copy()
        list_query_params.pop("page", None)

        context["nodes"] = page_obj.object_list
        context["node_stats_map"] = node_stats_map
        context["page_obj"] = page_obj
        context["is_paginated"] = page_obj.has_other_pages()
        context["per_page"] = per_page
        context["per_page_options"] = self.per_page_options
        context["search"] = search
        context["group_id"] = group_id
        context["sync_status"] = sync_status
        context["nginx_available"] = nginx_available
        context["has_any_filter"] = bool(
            search or group_id or sync_status or nginx_available == "all"
        )
        context["groups"] = NodeGroup.objects.all().order_by("name")
        context["status_counts"] = _build_global_status_counts()
        context["list_query_string"] = list_query_params.urlencode()

        all_unlocked = Node.objects.filter(is_locked=False)
        context["nginx_available_count"] = all_unlocked.filter(
            nginx_available=True
        ).count()
        context["total_nodes_count"] = all_unlocked.count()

        if not (search or group_id or sync_status):
            context["unbound_configs"] = (
                Config.objects.filter(bindings__isnull=True)
                .select_related("created_by")
                .order_by("-created_at")
            )
        else:
            context["unbound_configs"] = []
        return context


class ConfigNodeDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    """节点配置详情页 — 展示单个节点的全部配置绑定"""

    model = Node
    template_name = "configs/node_detail.html"
    context_object_name = "node"
    pk_url_kwarg = "node_id"
    permission_resource = "configs"
    permission_action = "read"

    def get_queryset(self):
        return super().get_queryset().filter(is_locked=False).prefetch_related("groups")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        node = self.object

        search = self.request.GET.get("search", "").strip()
        sync_status = self.request.GET.get("sync_status", "").strip()

        bindings_qs = (
            ConfigNodeBinding.objects.filter(node=node)
            .select_related("config")
            .order_by("config__name")
        )

        if sync_status and sync_status != "all":
            if sync_status == "pending":
                bindings_qs = bindings_qs.filter(
                    sync_status__in=["not_synced", "modified"]
                )
            else:
                bindings_qs = bindings_qs.filter(sync_status=sync_status)

        if search:
            terms = [
                t.strip() for t in search.replace("，", ",").split(",") if t.strip()
            ]
            for term in terms:
                bindings_qs = bindings_qs.filter(
                    Q(config__name__icontains=term) | Q(remote_path__icontains=term)
                )

        per_page = self.request.GET.get("per_page", "10")
        try:
            per_page = int(per_page)
        except (ValueError, TypeError):
            per_page = 10
        per_page = max(1, min(per_page, 100))

        paginator = Paginator(bindings_qs, per_page)
        page = self.request.GET.get("page", "1")
        try:
            page_obj = paginator.page(page)
        except Exception:
            page_obj = paginator.page(1)

        context["bindings"] = page_obj.object_list
        context["page_obj"] = page_obj
        context["per_page"] = str(per_page)
        context["per_page_options"] = [10, 20, 50]
        context["stats"] = _build_node_stats(node)
        context["search"] = search
        context["sync_status"] = sync_status
        context["list_query_string"] = self.request.GET.get("list_query", "")
        return context


class ConfigCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    """创建配置标签：无 node_id 跳绑定创建页，有 node_id 自动绑定到该节点"""

    model = Config
    form_class = ConfigForm
    template_name = "configs/create.html"
    permission_resource = "configs"
    permission_action = "create"

    def get_success_url(self):
        node_id = self.request.GET.get("node_id")
        if node_id:
            return reverse("configs:list")
        return reverse("configs:binding_create") + "?config_id=" + str(self.object.id)

    def form_valid(self, form):
        from apps.nodes.models import Node
        from apps.nodes.services import nginx_ops_gate_message

        form.instance.created_by = self.request.user
        form.instance.source = "manual"

        node_id = self.request.GET.get("node_id")
        node = None
        if node_id:
            node = get_object_or_404(Node, pk=node_id)
            gate_msg = nginx_ops_gate_message(node)
            if gate_msg:
                messages.error(self.request, gate_msg)
                return redirect("configs:list")

        response = super().form_valid(form)

        if node is not None:
            binding = ConfigNodeBinding.objects.create(
                config=self.object,
                node=node,
                remote_path=self.object.default_remote_path,
                content=self.object.template_content or "",
                current_version=1,
                sync_status="not_synced",
                source="manual",
                created_by=self.request.user,
            )
            BindingVersion.objects.create(
                binding=binding,
                version=1,
                content=binding.content,
                remark="手动创建绑定",
                created_by=self.request.user,
            )
            messages.success(
                self.request,
                f"配置标签 {self.object.name} 已创建并绑定到节点 {node.hostname}",
            )
        else:
            messages.success(
                self.request, f"配置标签 {self.object.name} 创建成功，请绑定节点"
            )
        return response


class ConfigEditView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    """编辑配置标签"""

    model = Config
    form_class = ConfigForm
    template_name = "configs/edit.html"
    permission_resource = "configs"
    permission_action = "update"

    def get_success_url(self):
        return reverse("configs:list")

    def form_valid(self, form):
        messages.success(self.request, f"配置标签 {form.instance.name} 更新成功")
        return super().form_valid(form)


class ConfigDeleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """删除配置标签（级联删除所有绑定）"""

    permission_resource = "configs"
    permission_action = "delete"

    def get(self, request, pk):
        config = get_object_or_404(Config, pk=pk)
        return render(request, "configs/delete.html", {"config": config})

    def post(self, request, pk):
        config = get_object_or_404(Config, pk=pk)
        name = config.name
        config.delete()
        messages.success(request, f"配置标签 {name} 及所有绑定已删除")
        return redirect("configs:list")


class ConfigBatchDeleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """批量删除未绑定配置标签"""

    permission_resource = "configs"
    permission_action = "delete"

    def post(self, request):
        ids = request.POST.get("ids", "").strip()
        fallback = reverse("configs:list")
        if not ids:
            messages.error(request, "未选择要删除的配置标签")
            return redirect(fallback)

        id_list = [i.strip() for i in ids.split(",") if i.strip().isdigit()]
        if not id_list:
            messages.error(request, "无效的配置标签 ID")
            return redirect(fallback)

        configs = Config.objects.filter(pk__in=id_list, bindings__isnull=True)
        count = configs.count()
        for cfg in configs:
            cfg.delete()

        messages.success(request, f"已批量删除 {count} 个未绑定配置标签")
        return redirect(fallback)


class ConfigDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    """配置标签详情"""

    model = Config
    template_name = "configs/detail.html"
    context_object_name = "config"
    permission_resource = "configs"
    permission_action = "read"

    def get_queryset(self):
        """预加载绑定与节点关联"""
        return (
            super()
            .get_queryset()
            .prefetch_related("bindings__node", "bindings__versions")
        )

    def get_context_data(self, **kwargs):
        """注入绑定的最新版本信息"""
        context = super().get_context_data(**kwargs)
        config = self.object
        bindings = (
            config.bindings.filter(node__is_deleted=False)
            .select_related("node")
            .order_by("node__hostname")
        )
        context["bindings"] = bindings

        latest_version = None
        for binding in bindings:
            bv = binding.versions.order_by("-version").first()
            if bv and (
                latest_version is None or bv.created_at > latest_version.created_at
            ):
                latest_version = bv
        context["latest_version"] = latest_version
        return context


# ==================== 配置节点绑定 CRUD ====================


class BindingCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    """创建配置-节点绑定，支持批量绑定多个节点"""

    model = ConfigNodeBinding
    form_class = BindingForm
    template_name = "configs/binding_create.html"
    permission_resource = "configs"
    permission_action = "create"

    def get_initial(self):
        initial = super().get_initial()
        config_id = self.request.GET.get("config_id")
        if config_id:
            config = get_object_or_404(Config, pk=config_id)
            initial["config"] = config
            initial["remote_path"] = config.default_remote_path
            content = config.template_content
            if not content:
                last_binding = (
                    ConfigNodeBinding.objects.filter(config=config)
                    .order_by("-updated_at")
                    .first()
                )
                if last_binding:
                    content = last_binding.content
            initial["content"] = content
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from apps.nodes.models import Node

        # 仅展示可依赖 Nginx 的节点（在线且已检测到）
        context["all_nodes"] = Node.objects.filter(
            is_locked=False, status="online", nginx_available=True
        ).order_by("hostname")
        return context

    def post(self, request, *args, **kwargs):
        from apps.nodes.models import Node
        from apps.nodes.services import nginx_ops_gate_message

        node_ids_raw = request.POST.get("node_ids", "")
        node_ids = [int(nid) for nid in node_ids_raw.split(",") if nid.strip()]

        form = self.get_form()
        if not form.is_valid():
            return self.form_invalid(form)

        nodes = []
        if node_ids:
            nodes = list(
                Node.objects.filter(id__in=node_ids, is_locked=False).order_by(
                    "hostname"
                )
            )
        elif form.cleaned_data.get("node"):
            nodes = [form.cleaned_data["node"]]

        if not nodes:
            form.add_error(None, "请至少选择一个目标节点")
            return self.form_invalid(form)

        for node in nodes:
            gate_msg = nginx_ops_gate_message(node)
            if gate_msg:
                form.add_error(None, gate_msg)
                return self.form_invalid(form)

        created_count = 0
        for node in nodes:
            binding = ConfigNodeBinding.objects.create(
                config=form.cleaned_data["config"],
                node=node,
                remote_path=form.cleaned_data.get("remote_path", ""),
                content=form.cleaned_data.get("content", ""),
                current_version=1,
                sync_status="not_synced",
                source="manual",
                created_by=request.user,
            )
            BindingVersion.objects.create(
                binding=binding,
                version=1,
                content=binding.content,
                remark="手动创建绑定",
                created_by=request.user,
            )
            created_count += 1

        if created_count == 1:
            msg = f"绑定 {form.cleaned_data['config'].name} @ {nodes[0].hostname} 创建成功"
        else:
            msg = f"已为配置 {form.cleaned_data['config'].name} 创建 {created_count} 个节点绑定"
        messages.success(request, msg)
        return redirect(self.get_success_url())

    def get_success_url(self):
        return reverse("configs:list")


class BindingEditView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    """编辑绑定内容 → version+1"""

    model = ConfigNodeBinding
    form_class = BindingForm
    template_name = "configs/binding_edit.html"
    context_object_name = "binding"
    permission_resource = "configs"
    permission_action = "update"

    def dispatch(self, request, *args, **kwargs):
        obj = self.get_object()
        if obj.sync_status == "marked_deleted":
            messages.error(request, "已标记删除的绑定无法编辑")
            return redirect("configs:list")
        from apps.nodes.services import nginx_ops_gate_message

        gate_msg = nginx_ops_gate_message(obj.node)
        if gate_msg:
            messages.error(request, gate_msg)
            return redirect("configs:list")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["next_version"] = self.object.current_version + 1
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        original_content = self.object.content
        form = self.get_form()
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

        if not form.is_valid():
            if is_ajax:
                errors = []
                for field, errs in form.errors.items():
                    for e in errs:
                        errors.append(f"{form.fields[field].label}: {e}")
                return JsonResponse(
                    {
                        "success": False,
                        "message": (
                            "请检查输入：" + "; ".join(errors)
                            if errors
                            else "表单验证失败"
                        ),
                    }
                )
            return self.form_invalid(form)

        if request.POST.get("confirm_save") == "yes":
            return self._save_after_review(form, is_ajax=is_ajax)
        return self._render_review(form, original_content)

    def _render_review(self, form, current_content):
        new_content = form.cleaned_data.get("content", "")
        context = {
            "binding": self.object,
            "next_version": self.object.current_version + 1,
            "split_diff_rows": _build_split_diff_rows(current_content, new_content),
            "new_content": new_content,
            "remark": form.cleaned_data.get("remark", ""),
        }
        return render(self.request, "configs/binding_edit_review.html", context)

    def _save_after_review(self, form, is_ajax=False):
        binding = form.save(commit=False)
        remark = form.cleaned_data.get("remark", "")
        new_content = form.cleaned_data["content"]
        new_version = self.object.current_version + 1

        # 创建版本记录
        BindingVersion.objects.create(
            binding=self.object,
            version=new_version,
            content=new_content,
            remark=remark,
            created_by=self.request.user,
        )

        binding.current_version = new_version
        binding.sync_status = "modified"
        binding.save()

        success_msg = f"绑定 {self.object.config.name} @ {self.object.node.hostname} 保存成功（v{new_version}）"
        if is_ajax:
            return JsonResponse(
                {
                    "success": True,
                    "message": success_msg,
                    "redirect": reverse("configs:list"),
                }
            )
        messages.success(self.request, success_msg)
        return redirect("configs:list")

    def form_valid(self, form):
        return self._save_after_review(form)

    def form_invalid(self, form):
        messages.error(self.request, "保存失败，请检查输入")
        return super().form_invalid(form)


class BindingDeleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """解除绑定：not_synced/orphaned 直接物理删除，其他标记为 marked_deleted"""

    permission_resource = "configs"
    permission_action = "delete"

    def get(self, request, pk):
        binding = get_object_or_404(ConfigNodeBinding, pk=pk)
        return render(request, "configs/binding_delete.html", {"binding": binding})

    def post(self, request, pk):
        binding = get_object_or_404(ConfigNodeBinding, pk=pk)
        label = f"{binding.config.name} @ {binding.node.hostname}"
        next_url = request.POST.get("next", "").strip()
        default_redirect = reverse(
            "configs:node_detail", kwargs={"node_id": binding.node.id}
        )

        if binding.node.nginx_available is not True:
            binding.delete()
            messages.success(
                request, f"绑定 {label} 已删除（节点无可用 Nginx，未清理远程）"
            )
            return redirect(next_url or default_redirect)

        if binding.sync_status in ("not_synced", "orphaned", "marked_deleted"):
            binding.delete()
            messages.success(request, f"绑定 {label} 已删除")
        else:
            binding.sync_status = "marked_deleted"
            binding.save(update_fields=["sync_status", "updated_at"])
            messages.success(
                request, f"绑定 {label} 已标记删除，下次同步时将清理远程文件"
            )
        return redirect(next_url or default_redirect)


class BindingRestoreView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """恢复已标记删除的绑定"""

    permission_resource = "configs"
    permission_action = "update"

    def post(self, request, pk):
        binding = get_object_or_404(ConfigNodeBinding, pk=pk)
        next_url = request.POST.get("next", "").strip()
        default_redirect = (
            f"{reverse('configs:node_detail', kwargs={'node_id': binding.node.id})}"
        )

        if binding.sync_status != "marked_deleted":
            messages.error(request, "该绑定未处于标记删除状态")
            return redirect(next_url or default_redirect)
        from apps.nodes.services import nginx_ops_gate_message

        gate_msg = nginx_ops_gate_message(binding.node)
        if gate_msg:
            messages.error(request, gate_msg)
            return redirect(next_url or default_redirect)
        binding.sync_status = "not_synced"
        binding.save(update_fields=["sync_status", "updated_at"])
        messages.success(
            request, f"绑定 {binding.config.name} @ {binding.node.hostname} 已恢复"
        )
        return redirect(next_url or default_redirect)


class BindingBatchDeleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """批量解除绑定：与单个解除绑定逻辑一致"""

    permission_resource = "configs"
    permission_action = "delete"

    def post(self, request):
        ids = request.POST.get("ids", "").strip()
        next_url = request.POST.get("next", "").strip()
        fallback = next_url or request.META.get("HTTP_REFERER", reverse("configs:list"))
        if not ids:
            messages.error(request, "未选择要删除的绑定")
            return redirect(fallback)

        id_list = [i.strip() for i in ids.split(",") if i.strip().isdigit()]
        if not id_list:
            messages.error(request, "无效的绑定 ID")
            return redirect(fallback)

        bindings = list(ConfigNodeBinding.objects.filter(pk__in=id_list))
        hard_deleted = 0
        soft_deleted = 0

        for b in bindings:
            if b.node.nginx_available is not True:
                b.delete()
                hard_deleted += 1
            elif b.sync_status in ("not_synced", "orphaned", "marked_deleted"):
                b.delete()
                hard_deleted += 1
            else:
                b.sync_status = "marked_deleted"
                b.save(update_fields=["sync_status", "updated_at"])
                soft_deleted += 1

        parts = []
        if soft_deleted:
            parts.append(f"{soft_deleted} 个已标记删除（下次同步时清理远程文件）")
        if hard_deleted:
            parts.append(f"{hard_deleted} 个已直接移除")
        messages.success(request, "批量解除完成：" + "，".join(parts))
        return redirect(fallback)


class BindingDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    """绑定详情"""

    model = ConfigNodeBinding
    template_name = "configs/binding_detail.html"
    context_object_name = "binding"
    permission_resource = "configs"
    permission_action = "read"


# ==================== 版本历史 ====================


class BindingVersionListView(
    LoginRequiredMixin, PermissionRequiredMixin, PerPagePaginationMixin, ListView
):
    """绑定版本历史"""

    model = BindingVersion
    template_name = "configs/versions.html"
    context_object_name = "versions"
    paginate_by = 10
    permission_resource = "configs"
    permission_action = "read"

    def get_queryset(self):
        self.binding = get_object_or_404(ConfigNodeBinding, pk=self.kwargs["pk"])
        return (
            BindingVersion.objects.filter(binding=self.binding)
            .select_related("created_by")
            .order_by("-version")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["binding"] = self.binding
        context["config"] = self.binding.config
        context["next_version"] = self.binding.current_version + 1
        return context


class BindingVersionDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    """版本详情"""

    model = BindingVersion
    template_name = "configs/version_detail.html"
    context_object_name = "version"
    permission_resource = "configs"
    permission_action = "read"

    def get_object(self, queryset=None):
        binding_pk = self.kwargs.get("pk")
        version_id = self.kwargs.get("version_id")
        return get_object_or_404(BindingVersion, pk=version_id, binding_id=binding_pk)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["binding"] = self.object.binding
        context["config"] = self.object.binding.config
        return context


class BindingVersionRestoreView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """恢复到指定版本"""

    permission_resource = "configs"
    permission_action = "update"

    def post(self, request, pk, version_id):
        binding = get_object_or_404(ConfigNodeBinding, pk=pk)
        if binding.sync_status == "marked_deleted":
            messages.error(request, "已标记删除的绑定无法恢复版本")
            return redirect("configs:list")
        if binding.node.is_locked:
            messages.error(request, f"节点 {binding.node.hostname} 已锁定，无法恢复")
            return redirect("configs:binding_versions", pk=binding.pk)
        from apps.nodes.services import nginx_ops_gate_message

        gate_msg = nginx_ops_gate_message(binding.node)
        if gate_msg:
            messages.error(request, gate_msg)
            return redirect("configs:list")

        old_version = get_object_or_404(BindingVersion, pk=version_id, binding=binding)
        new_version_num = binding.current_version + 1

        BindingVersion.objects.create(
            binding=binding,
            version=new_version_num,
            content=old_version.content,
            remark=f"恢复自 v{old_version.version}",
            created_by=request.user,
        )

        binding.content = old_version.content
        binding.current_version = new_version_num
        # 恢复仅改本地内容，需再次发布后才与远程一致
        binding.sync_status = "modified"
        binding.save()

        messages.success(
            request,
            f"已恢复到 v{old_version.version}（生成新版本 v{new_version_num}，状态为本地已修改，需重新发布）",
        )
        return redirect("configs:binding_versions", pk=binding.pk)


# ==================== 差异对比 ====================


def _build_split_diff_rows(base_content, target_content):
    base_lines = base_content.splitlines()
    target_lines = target_content.splitlines()
    matcher = difflib.SequenceMatcher(a=base_lines, b=target_lines)

    rows = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset, line in enumerate(base_lines[i1:i2]):
                rows.append(
                    {
                        "type": "equal",
                        "left_no": i1 + offset + 1,
                        "left": line,
                        "right_no": j1 + offset + 1,
                        "right": line,
                    }
                )
            continue
        left_block = base_lines[i1:i2]
        right_block = target_lines[j1:j2]
        max_len = max(len(left_block), len(right_block))
        for idx in range(max_len):
            left_line = left_block[idx] if idx < len(left_block) else ""
            right_line = right_block[idx] if idx < len(right_block) else ""
            rows.append(
                {
                    "type": tag,
                    "left_no": (i1 + idx + 1) if idx < len(left_block) else "",
                    "left": left_line,
                    "right_no": (j1 + idx + 1) if idx < len(right_block) else "",
                    "right": right_line,
                }
            )
    return rows


class BindingVersionCompareView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """绑定版本差异对比"""

    permission_resource = "configs"
    permission_action = "read"

    def get(self, request, pk):
        binding = get_object_or_404(ConfigNodeBinding, pk=pk)
        versions = binding.versions.order_by("-version")
        selected_base = request.GET.get("base_version")
        selected_target = request.GET.get("target_version")

        base_obj = None
        target_obj = None
        split_diff_rows = []
        has_diff = False
        draft_content = ""

        if selected_base and selected_target and selected_base != selected_target:
            base_obj = get_object_or_404(versions, id=selected_base)
            target_obj = get_object_or_404(versions, id=selected_target)
            split_diff_rows = _build_split_diff_rows(
                base_obj.content, target_obj.content
            )
            has_diff = base_obj.content != target_obj.content
            draft_content = target_obj.content

        context = {
            "binding": binding,
            "config": binding.config,
            "versions": versions,
            "selected_base": selected_base,
            "selected_target": selected_target,
            "base_obj": base_obj,
            "target_obj": target_obj,
            "has_diff": has_diff,
            "split_diff_rows": split_diff_rows,
            "draft_content": draft_content,
        }
        return render(request, "configs/version_compare.html", context)


class BindingVersionCompareApplyView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """应用版本差异"""

    permission_resource = "configs"
    permission_action = "update"

    def post(self, request, pk):
        binding = get_object_or_404(ConfigNodeBinding, pk=pk)
        if binding.sync_status == "marked_deleted":
            messages.error(request, "已标记删除的绑定无法应用差异变更")
            return redirect("configs:list")
        if binding.node.is_locked:
            messages.error(request, f"节点 {binding.node.hostname} 已锁定")
            return redirect("configs:binding_versions", pk=binding.pk)
        from apps.nodes.services import nginx_ops_gate_message

        gate_msg = nginx_ops_gate_message(binding.node)
        if gate_msg:
            messages.error(request, gate_msg)
            return redirect("configs:list")

        confirmed_content = request.POST.get("confirmed_content", "")
        is_confirmed = request.POST.get("confirm_change") == "yes"

        if not is_confirmed or not confirmed_content.strip():
            messages.error(request, "请确认变更后再提交")
            return redirect("configs:binding_compare", pk=binding.pk)

        if binding.content == confirmed_content:
            messages.info(request, "当前内容与目标版本一致，无需更新")
            return redirect("configs:binding_versions", pk=binding.pk)

        new_version_num = binding.current_version + 1
        binding.content = confirmed_content
        binding.current_version = new_version_num
        binding.sync_status = "modified"
        binding.save()

        BindingVersion.objects.create(
            binding=binding,
            version=new_version_num,
            content=confirmed_content,
            remark="差异确认更新",
            created_by=request.user,
        )

        messages.success(request, f"差异确认成功，已生成新版本 V{new_version_num}")
        return redirect("configs:binding_versions", pk=binding.pk)


# ==================== API 视图 ====================


class ConfigByNodesAPIView(LoginRequiredMixin, View):
    """根据节点列表获取配置"""

    def get(self, request):
        from apps.nodes.models import Node

        node_ids = request.GET.getlist("node_ids")
        if not node_ids:
            return JsonResponse({"configs": []})

        nodes = Node.objects.filter(id__in=node_ids)
        bindings = (
            ConfigNodeBinding.objects.filter(node__in=nodes)
            .select_related("config", "node")
            .order_by("config__name", "node__hostname")
        )

        data = []
        for b in bindings:
            data.append(
                {
                    "id": b.id,
                    "config_id": b.config_id,
                    "config_name": b.config.name,
                    "node_id": b.node_id,
                    "node_hostname": b.node.hostname,
                    "version": b.current_version,
                    "sync_status": b.sync_status,
                    "remote_path": b.remote_path,
                }
            )
        return JsonResponse({"configs": data})


class ConfigGlobPreviewView(LoginRequiredMixin, View):
    """预览 glob 匹配文件（仅支持单节点）"""

    def post(self, request):
        from apps.nodes.models import Node
        from apps.nodes.services import _get_node_credential

        node_ids_str = request.POST.get("node_ids", "")
        if not node_ids_str:
            return JsonResponse({"success": False, "message": "请选择节点"}, status=400)

        node_ids = [int(nid) for nid in node_ids_str.split(",") if nid.strip()]
        if len(node_ids) > 1:
            return JsonResponse(
                {"success": False, "message": "Glob 预览仅支持单个节点，请勿多选"},
                status=400,
            )
        node = Node.objects.filter(id__in=node_ids).first()
        if not node:
            return JsonResponse({"success": False, "message": "节点不存在"}, status=404)

        if node.is_locked:
            return JsonResponse({"success": False, "message": "节点已锁定"}, status=400)
        from apps.nodes.services import nginx_ops_gate_message

        gate_msg = nginx_ops_gate_message(node)
        if gate_msg:
            return JsonResponse({"success": False, "message": gate_msg}, status=400)

        credential = _get_node_credential(node)
        if not credential:
            return JsonResponse(
                {"success": False, "message": "未配置SSH凭证"}, status=400
            )

        setting = get_or_create_sync_setting(node)
        main_conf_path = (
            request.POST.get("main_conf_path")
            or setting.main_conf_path
            or default_nginx_conf_path()
        )
        if main_conf_path and main_conf_path != setting.main_conf_path:
            setting.main_conf_path = main_conf_path
            setting.save(update_fields=["main_conf_path"])
        nginx_conf_path = main_conf_path
        if not nginx_conf_path:
            return JsonResponse({"success": False, "message": "未配置nginx路径"})

        files, errors = preview_glob_configs(node, credential, nginx_conf_path)
        return JsonResponse({"success": True, "files": files, "errors": errors})


# ==================== 同步向导（保留兼容） ====================


class ConfigSyncWizardView(
    LoginRequiredMixin, PermissionRequiredMixin, PerPagePaginationMixin, ListView
):
    template_name = "configs/sync_wizard.html"
    context_object_name = "nodes"
    paginate_by = 10
    permission_resource = "configs"
    permission_action = "read"

    def get_queryset(self):
        from apps.nodes.models import Node

        queryset = (
            Node.objects.filter(is_locked=False)
            .select_related("created_by")
            .prefetch_related("groups")
            .order_by("hostname")
        )
        search = self.request.GET.get("search", "").strip()
        group_search = self.request.GET.get("group_search", "").strip()

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

        nginx_available = self.request.GET.get("nginx_available", "true").strip()
        if nginx_available == "true":
            queryset = queryset.filter(nginx_available=True)

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        nodes = context["nodes"]
        search = self.request.GET.get("search", "")
        group_search = self.request.GET.get("group_search", "")
        nginx_available = self.request.GET.get("nginx_available", "true").strip()

        node_stats = {}
        node_sync_paths = {}
        node_groups = {}

        for node in nodes:
            bindings = ConfigNodeBinding.objects.filter(node=node)
            node_stats[node.id] = {
                "synced": bindings.filter(sync_status="synced").count(),
                "failed": bindings.filter(sync_status="failed").count(),
                "syncing": bindings.filter(sync_status="syncing").count(),
                "not_synced": bindings.filter(sync_status="not_synced").count(),
                "orphaned": bindings.filter(sync_status="orphaned").count(),
                "modified": bindings.filter(sync_status="modified").count(),
                "total": bindings.count(),
                "last_sync": bindings.exclude(last_sync_time__isnull=True)
                .order_by("-last_sync_time")
                .first(),
            }
            setting = get_or_create_sync_setting(node)
            node_sync_paths[node.id] = (
                setting.main_conf_path if setting.main_conf_path else ""
            )
            node_groups[node.id] = list(node.groups.all())

        from apps.nodes.models import Node

        all_unlocked = Node.objects.filter(is_locked=False)
        context["nginx_available"] = nginx_available
        context["nginx_available_count"] = all_unlocked.filter(
            nginx_available=True
        ).count()
        context["total_nodes_count"] = all_unlocked.count()
        context["node_stats"] = node_stats
        context["node_sync_paths"] = node_sync_paths
        context["node_groups"] = node_groups
        context["search"] = search
        context["group_search"] = group_search
        context["batch_max_count"] = int(get_setting("node.batch_max_count", "3"))
        pre_select_node_id = self.request.GET.get("node_id", "").strip()
        if pre_select_node_id and pre_select_node_id.isdigit():
            context["pre_select_node_id"] = int(pre_select_node_id)
        return context


class ConfigSyncBatchAPIView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_resource = "configs"
    permission_action = "update"

    def post(self, request):
        from apps.nodes.models import Node

        data = json.loads(request.body)
        node_ids = data.get("node_ids", [])

        if not node_ids:
            return JsonResponse({"success": False, "message": "请至少选择一个节点"})

        MAX_BATCH = int(get_setting("node.batch_max_count", "3"))
        if len(node_ids) > MAX_BATCH:
            return JsonResponse(
                {"success": False, "message": f"最多只能勾选 {MAX_BATCH} 个节点"}
            )

        max_workers = MAX_BATCH
        nodes = list(Node.objects.filter(id__in=node_ids).order_by("id"))

        # 任选节点非在线或无 Nginx 则整批拒绝
        from apps.nodes.services import nginx_ops_gate_message

        for node in nodes:
            gate_msg = nginx_ops_gate_message(node)
            if gate_msg:
                return JsonResponse(
                    {"success": False, "message": gate_msg},
                    status=400,
                )

        # 配置发现与批量同步统一记为 config_batch_sync（未再单独创建 config_discover）
        task_center = TaskCenterTask.objects.create(
            operation_type="config_batch_sync",
            status="pending",
            detail="任务已创建，等待执行",
            target_hostnames=",".join(node.hostname for node in nodes),
            target_ips=",".join(node.ip for node in nodes),
            trigger_user=request.user,
        )

        thread = threading.Thread(
            target=run_batch_config_sync_task,
            args=(task_center.id, nodes, request.user, max_workers),
            daemon=True,
        )
        thread.start()

        return JsonResponse(
            {
                "success": True,
                "async": True,
                "task_center_id": task_center.id,
                "task_center_detail_url": reverse(
                    "releases:task_center_detail", kwargs={"pk": task_center.id}
                ),
            }
        )


class ConfigSyncSingleAPIView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_resource = "configs"
    permission_action = "update"

    def post(self, request):
        from apps.nodes.models import Node
        from apps.nodes.services import _get_node_credential

        data = json.loads(request.body)
        node_id = data.get("node_id")
        if not node_id:
            return JsonResponse({"success": False, "message": "缺少节点ID"})

        node = get_object_or_404(Node, pk=node_id)
        if node.is_locked:
            return JsonResponse({"success": False, "message": "节点已锁定"})
        from apps.nodes.services import nginx_ops_gate_message

        gate_msg = nginx_ops_gate_message(node)
        if gate_msg:
            return JsonResponse({"success": False, "message": gate_msg}, status=400)

        credential = _get_node_credential(node)
        if not credential:
            return JsonResponse({"success": False, "message": "未配置SSH凭证"})

        setting = get_or_create_sync_setting(node)
        main_conf_path = (
            data.get("main_conf_path")
            or setting.main_conf_path
            or default_nginx_conf_path()
        )
        if main_conf_path and main_conf_path != setting.main_conf_path:
            setting.main_conf_path = main_conf_path
            setting.save(update_fields=["main_conf_path"])
        nginx_conf_path = main_conf_path
        if not nginx_conf_path:
            return JsonResponse({"success": False, "message": "未配置nginx路径"})

        selected_paths = data.get("selected_paths", [])
        is_partial = bool(selected_paths)

        task_center = TaskCenterTask.objects.create(
            operation_type="config_batch_sync",
            status="pending",
            detail=f"单节点{'部分' if is_partial else '全量'}同步：{node.hostname}",
            target_hostnames=node.hostname,
            target_ips=node.ip,
            trigger_user=request.user,
        )

        auth_kwargs = {}
        if credential.auth_type == "password":
            auth_kwargs["password"] = credential.get_password()
        else:
            auth_kwargs["private_key"] = credential.get_private_key()

        thread = threading.Thread(
            target=run_single_config_sync_task,
            args=(
                task_center.id,
                node,
                request.user,
                credential.username,
                nginx_conf_path,
                auth_kwargs,
                selected_paths,
                is_partial,
            ),
            daemon=True,
        )
        thread.start()

        return JsonResponse(
            {
                "success": True,
                "async": True,
                "task_center_id": task_center.id,
                "task_center_detail_url": reverse(
                    "releases:task_center_detail", kwargs={"pk": task_center.id}
                ),
            }
        )


class ConfigSyncProgressView(LoginRequiredMixin, View):
    """同步进度查询接口，从 TaskCenterTask 读取真实进度"""

    def get(self, request):
        from apps.releases.models import TaskCenterTask

        task_id = request.GET.get("task_id", "")
        try:
            task = TaskCenterTask.objects.get(pk=int(task_id))
        except (ValueError, TaskCenterTask.DoesNotExist):
            return JsonResponse(
                {
                    "success": True,
                    "progress": {"completed": 0, "total": 100, "nodes": {}},
                }
            )

        return JsonResponse(
            {
                "success": True,
                "progress": {
                    "completed": task.progress or 0,
                    "total": 100,
                    "detail": task.detail or "",
                    "status": task.status,
                    "nodes": {},
                },
            }
        )


class ConfigUpdateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """更新配置（兼容旧URL）"""

    permission_resource = "configs"
    permission_action = "update"

    def get(self, request, pk):
        config = get_object_or_404(Config, pk=pk)
        return redirect("configs:edit", pk=config.pk)


class ConfigNodeDeleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """删除节点下配置（兼容旧URL）"""

    permission_resource = "configs"
    permission_action = "delete"

    def post(self, request, pk):
        # 尝试按 binding 处理
        binding = get_object_or_404(ConfigNodeBinding, pk=pk)
        label = f"{binding.config.name} @ {binding.node.hostname}"
        binding.delete()
        messages.success(request, f"绑定 {label} 已删除")
        return redirect("configs:list")


class ConfigUpdatePreviewView(LoginRequiredMixin, View):
    """预览更新差异（兼容旧接口）"""

    def post(self, request):
        binding_id = request.POST.get("binding_id")
        content = request.POST.get("content", "")
        if not binding_id:
            return JsonResponse({"success": False, "message": "缺少绑定ID"})

        binding = get_object_or_404(ConfigNodeBinding, pk=binding_id)
        rows = _build_split_diff_rows(binding.content, content)
        return JsonResponse({"success": True, "rows": rows})
