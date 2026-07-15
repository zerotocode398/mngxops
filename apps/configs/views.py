"""配置管理视图 - 适配 ConfigNodeBinding 模型"""
import difflib
import hashlib
import json
from collections import OrderedDict

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
    save_sync_path,
    sync_discovered_configs,
    sync_selected_configs,
    mark_discovery_failed_configs,
)
from apps.users.permissions import PermissionRequiredMixin
from utils.pagination import PerPagePaginationMixin


def _build_node_stats(node):
    """构建单个节点的绑定状态统计"""
    stats = {
        "total": 0, "pending": 0, "conflict": 0, "orphaned": 0,
        "failed": 0, "syncing": 0, "marked_deleted": 0,
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
    """构建全局绑定状态计数"""
    from django.db.models import Q
    total = ConfigNodeBinding.objects.count()
    pending = ConfigNodeBinding.objects.filter(
        sync_status__in=["not_synced", "modified"]
    ).count()
    conflict = ConfigNodeBinding.objects.filter(sync_status="conflict").count()
    orphaned = ConfigNodeBinding.objects.filter(sync_status="orphaned").count()
    failed = ConfigNodeBinding.objects.filter(sync_status="failed").count()
    syncing = ConfigNodeBinding.objects.filter(sync_status="syncing").count()
    marked_deleted = ConfigNodeBinding.objects.filter(sync_status="marked_deleted").count()
    return {
        "total": total, "pending": pending, "conflict": conflict,
        "orphaned": orphaned, "failed": failed, "syncing": syncing,
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

        if search:
            queryset = queryset.filter(
                Q(hostname__icontains=search)
                | Q(ip__icontains=search)
                | Q(config_bindings__config__name__icontains=search)
                | Q(config_bindings__remote_path__icontains=search)
            ).distinct()
        if group_id:
            queryset = queryset.filter(groups__id=group_id).distinct()
        return queryset

    def get_context_data(self, **kwargs):
        from apps.nodes.models import Node, NodeGroup

        context = super().get_context_data(**kwargs)
        all_nodes = list(self.get_queryset())
        sync_status = self.request.GET.get("sync_status", "").strip()
        search = self.request.GET.get("search", "").strip()
        group_id = self.request.GET.get("group_id", "").strip()

        node_bindings_map = {}
        node_stats_map = {}

        for node in all_nodes:
            bindings_qs = ConfigNodeBinding.objects.filter(
                node=node
            ).select_related("config").order_by("config__name")

            if sync_status:
                if sync_status == "pending":
                    bindings_qs = bindings_qs.filter(
                        sync_status__in=["not_synced", "modified"]
                    )
                else:
                    bindings_qs = bindings_qs.filter(sync_status=sync_status)

            bindings = list(bindings_qs)
            if not bindings and sync_status:
                continue

            node_bindings_map[node.id] = bindings
            node_stats_map[node.id] = _build_node_stats(node)

        if sync_status:
            filtered_nodes = [n for n in all_nodes if n.id in node_bindings_map]
        else:
            filtered_nodes = all_nodes

        per_page = self.get_paginate_by(None)
        paginator = Paginator(filtered_nodes, per_page)
        page_num = self.request.GET.get("page", 1)
        page_obj = paginator.get_page(page_num)

        context["nodes"] = page_obj.object_list
        context["node_bindings_map"] = node_bindings_map
        context["node_stats_map"] = node_stats_map
        context["page_obj"] = page_obj
        context["is_paginated"] = page_obj.has_other_pages()
        context["per_page"] = per_page
        context["per_page_options"] = self.per_page_options
        context["search"] = search
        context["group_id"] = group_id
        context["sync_status"] = sync_status
        context["has_any_filter"] = bool(search or group_id or sync_status)
        context["groups"] = NodeGroup.objects.all().order_by("name")
        context["status_counts"] = _build_global_status_counts()
        return context


class ConfigCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    """创建配置标签"""
    model = Config
    form_class = ConfigForm
    template_name = "configs/create.html"
    success_url = reverse_lazy("configs:list")
    permission_resource = "configs"
    permission_action = "create"

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        form.instance.source = "manual"
        messages.success(self.request, f"配置标签 {form.instance.name} 创建成功")
        return super().form_valid(form)


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


class ConfigDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    """配置标签详情"""
    model = Config
    template_name = "configs/detail.html"
    context_object_name = "config"
    permission_resource = "configs"
    permission_action = "read"

    def get_queryset(self):
        """预加载绑定与节点关联"""
        return super().get_queryset().prefetch_related(
            "bindings__node", "bindings__versions"
        )

    def get_context_data(self, **kwargs):
        """注入绑定的最新版本信息"""
        context = super().get_context_data(**kwargs)
        config = self.object
        bindings = config.bindings.select_related("node").order_by("node__hostname")
        context["bindings"] = bindings

        latest_version = None
        for binding in bindings:
            bv = binding.versions.order_by("-version").first()
            if bv and (latest_version is None or bv.created_at > latest_version.created_at):
                latest_version = bv
        context["latest_version"] = latest_version
        return context


# ==================== 配置节点绑定 CRUD ====================

class BindingCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    """创建配置-节点绑定"""
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
            initial["content"] = config.template_content
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from apps.nodes.models import Node
        context["all_nodes"] = Node.objects.filter(is_locked=False).order_by("hostname")
        return context

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        form.instance.current_version = 1
        form.instance.sync_status = "not_synced"
        response = super().form_valid(form)

        # 创建初始版本
        BindingVersion.objects.create(
            binding=self.object,
            version=1,
            content=self.object.content,
            remark="手动创建绑定",
            created_by=self.request.user,
        )

        messages.success(
            self.request,
            f"绑定 {self.object.config.name} @ {self.object.node.hostname} 创建成功",
        )
        return response

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
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["next_version"] = self.object.current_version + 1
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        original_content = self.object.content
        form = self.get_form()
        if not form.is_valid():
            return self.form_invalid(form)

        if request.POST.get("confirm_save") == "yes":
            return self._save_after_review(form)
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

    def _save_after_review(self, form):
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

        messages.success(
            self.request,
            f"绑定 {self.object.config.name} @ {self.object.node.hostname} 保存成功（v{new_version}）",
        )
        return redirect("configs:list")

    def form_valid(self, form):
        return self._save_after_review(form)

    def form_invalid(self, form):
        messages.error(self.request, "保存失败，请检查输入")
        return super().form_invalid(form)


class BindingDeleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """解除绑定（逻辑删除：标记为 marked_deleted）"""
    permission_resource = "configs"
    permission_action = "delete"

    def get(self, request, pk):
        binding = get_object_or_404(ConfigNodeBinding, pk=pk)
        return render(request, "configs/binding_delete.html", {"binding": binding})

    def post(self, request, pk):
        binding = get_object_or_404(ConfigNodeBinding, pk=pk)
        label = f"{binding.config.name} @ {binding.node.hostname}"
        binding.sync_status = "marked_deleted"
        binding.save(update_fields=["sync_status", "updated_at"])
        messages.success(request, f"绑定 {label} 已标记删除，下次同步时将清理远程文件")
        return redirect("configs:list")


class BindingRestoreView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """恢复已标记删除的绑定"""
    permission_resource = "configs"
    permission_action = "update"

    def post(self, request, pk):
        binding = get_object_or_404(ConfigNodeBinding, pk=pk)
        if binding.sync_status != "marked_deleted":
            messages.error(request, "该绑定未处于标记删除状态")
            return redirect("configs:list")
        binding.sync_status = "not_synced"
        binding.save(update_fields=["sync_status", "updated_at"])
        messages.success(request, f"绑定 {binding.config.name} @ {binding.node.hostname} 已恢复")
        return redirect("configs:list")


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
        binding.sync_status = "modified"
        binding.save()

        messages.success(
            request,
            f"已恢复到 v{old_version.version}（生成新版本 v{new_version_num}）",
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
                rows.append({
                    "type": "equal",
                    "left_no": i1 + offset + 1,
                    "left": line,
                    "right_no": j1 + offset + 1,
                    "right": line,
                })
            continue
        left_block = base_lines[i1:i2]
        right_block = target_lines[j1:j2]
        max_len = max(len(left_block), len(right_block))
        for idx in range(max_len):
            left_line = left_block[idx] if idx < len(left_block) else ""
            right_line = right_block[idx] if idx < len(right_block) else ""
            rows.append({
                "type": tag,
                "left_no": (i1 + idx + 1) if idx < len(left_block) else "",
                "left": left_line,
                "right_no": (j1 + idx + 1) if idx < len(right_block) else "",
                "right": right_line,
            })
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
            split_diff_rows = _build_split_diff_rows(base_obj.content, target_obj.content)
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
        bindings = ConfigNodeBinding.objects.filter(
            node__in=nodes
        ).select_related("config", "node").order_by("config__name", "node__hostname")

        data = []
        for b in bindings:
            data.append({
                "id": b.id,
                "config_id": b.config_id,
                "config_name": b.config.name,
                "node_id": b.node_id,
                "node_hostname": b.node.hostname,
                "version": b.current_version,
                "sync_status": b.sync_status,
                "remote_path": b.remote_path,
            })
        return JsonResponse({"configs": data})


class ConfigGlobPreviewView(LoginRequiredMixin, View):
    """预览 glob 匹配文件（兼容旧接口）"""

    def post(self, request):
        from apps.nodes.models import Node
        from apps.nodes.views import _get_node_credential
        from utils.ssh import discover_nginx_configs

        node_ids_str = request.POST.get("node_ids", "")
        if not node_ids_str:
            return JsonResponse({"success": False, "message": "请选择节点"}, status=400)

        node_ids = [int(nid) for nid in node_ids_str.split(",") if nid.strip()]
        node = Node.objects.filter(id__in=node_ids).first()
        if not node:
            return JsonResponse({"success": False, "message": "节点不存在"}, status=404)

        if node.is_locked:
            return JsonResponse({"success": False, "message": "节点已锁定"}, status=400)

        credential = _get_node_credential(node)
        if not credential:
            return JsonResponse({"success": False, "message": "未配置SSH凭证"}, status=400)

        setting = get_or_create_sync_setting(node)
        main_conf_path = data.get("main_conf_path") or setting.main_conf_path
        if main_conf_path and main_conf_path != setting.main_conf_path:
            setting.main_conf_path = main_conf_path
            setting.save(update_fields=["main_conf_path"])
        nginx_conf_path = main_conf_path
        if not nginx_conf_path:
            return JsonResponse({"success": False, "message": "未配置nginx路径"})

        auth_kwargs = {}
        if credential.auth_type == "password":
            auth_kwargs["password"] = credential.get_password()
        else:
            auth_kwargs["private_key"] = credential.get_private_key()

        discovered, errors = discover_nginx_configs(
            node.ip, node.port, credential.username,
            nginx_conf_path=nginx_conf_path, **auth_kwargs,
        )

        files = [{"path": item["path"], "name": item["name"]} for item in discovered]
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
            tags = [name.strip() for name in group_search.replace("，", ",").split(",") if name.strip()]
            if tags:
                for tag in tags:
                    queryset = queryset.filter(
                        Q(groups__name__icontains=tag)
                        | Q(hostname__icontains=tag)
                        | Q(ip__icontains=tag)
                    )
                queryset = queryset.distinct()
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        nodes = context["nodes"]
        search = self.request.GET.get("search", "")
        group_search = self.request.GET.get("group_search", "")

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
                "last_sync": bindings.exclude(last_sync_time__isnull=True).order_by("-last_sync_time").first(),
            }
            setting = get_or_create_sync_setting(node)
            node_sync_paths[node.id] = setting.main_conf_path if setting.main_conf_path else ""
            node_groups[node.id] = list(node.groups.all())

        context["node_stats"] = node_stats
        context["node_sync_paths"] = node_sync_paths
        context["node_groups"] = node_groups
        context["search"] = search
        context["group_search"] = group_search
        return context


class ConfigSyncBatchAPIView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_resource = "configs"
    permission_action = "update"

    def post(self, request):
        from apps.nodes.models import Node
        from apps.nodes.views import _get_node_credential
        from utils.ssh import discover_nginx_configs
        from apps.releases.models import TaskCenterTask
        from django.utils import timezone
        from django.db import close_old_connections

        data = json.loads(request.body)
        node_ids = data.get("node_ids", [])

        if not node_ids:
            return JsonResponse({"success": False, "message": "请至少选择一个节点"})

        MAX_BATCH = 3
        if len(node_ids) > MAX_BATCH:
            return JsonResponse({"success": False, "message": f"最多只能选择 {MAX_BATCH} 个节点"})

        nodes = list(Node.objects.filter(id__in=node_ids).order_by("id"))
        total = len(nodes)

        task_center = TaskCenterTask.objects.create(
            operation_type="config_batch_sync",
            status="pending",
            detail="任务已创建，等待执行",
            target_hostnames=",".join(node.hostname for node in nodes),
            target_ips=",".join(node.ip for node in nodes),
            trigger_user=request.user,
        )

        def _sync_one(node):
            close_old_connections()
            result = {
                "node_id": node.id, "hostname": node.hostname, "ip": node.ip,
                "success": False, "message": "", "created": 0, "updated": 0,
                "orphaned": 0, "errors": [],
            }
            if node.is_locked:
                result["message"] = "节点已锁定"
                return result
            credential = _get_node_credential(node)
            if not credential:
                result["message"] = "未配置SSH凭证"
                return result
            setting = get_or_create_sync_setting(node)
            nginx_conf_path = setting.main_conf_path or "/etc/nginx/nginx.conf"
            if not nginx_conf_path:
                result["message"] = "未配置nginx路径"
                return result

            if credential.auth_type == "password":
                discovered, errors = discover_nginx_configs(
                    node.ip, node.port, credential.username,
                    password=credential.get_password(), nginx_conf_path=nginx_conf_path,
                )
            else:
                discovered, errors = discover_nginx_configs(
                    node.ip, node.port, credential.username,
                    private_key=credential.get_private_key(), nginx_conf_path=nginx_conf_path,
                )

            if errors:
                mark_discovery_failed_configs(node, errors, request.user)
                result["errors"].extend(errors)

            if discovered:
                created, updated, skipped, orphaned = sync_discovered_configs(
                    node, discovered, request.user, remark="批量节点全量同步",
                )
                save_sync_path(node, nginx_conf_path, request.user)
                result["created"] = len(created)
                result["updated"] = len(updated)
                result["orphaned"] = len(orphaned)
                result["created_names"] = created
                result["updated_names"] = updated
                result["orphaned_names"] = orphaned

            if result["errors"]:
                result["message"] = "; ".join(result["errors"][:3])
                result["success"] = False
            elif not discovered:
                result["message"] = "未发现配置文件"
                result["success"] = False
            else:
                result["success"] = True
                result["message"] = f"已同步 {len(discovered)} 个配置文件"
            return result

        def _run_batch_sync_task(task_id, sync_nodes):
            TaskCenterTask.objects.filter(pk=task_id).update(
                status="running", started_at=timezone.now(), progress=0,
                detail=f"执行中：0/{len(sync_nodes)}",
            )
            success_count = 0
            fail_count = 0
            done = 0
            detail_lines = []

            from concurrent.futures import ThreadPoolExecutor, as_completed
            with ThreadPoolExecutor(max_workers=MAX_BATCH) as executor:
                future_to_node = {executor.submit(_sync_one, node): node for node in sync_nodes}
                for future in as_completed(future_to_node):
                    result = future.result()
                    done += 1
                    node = future_to_node[future]
                    if result["success"]:
                        success_count += 1
                    else:
                        fail_count += 1
                    detail_lines.append(
                        f"[节点] {node.ip} ({node.hostname}) - "
                        f"{'成功' if result['success'] else '失败'}: {result['message']}"
                    )
                    TaskCenterTask.objects.filter(pk=task_id).update(
                        progress=int(done * 100 / total) if total else 100,
                        detail=f"执行中：成功 {success_count}，失败 {fail_count}，已完成 {done}/{total}",
                        updated_at=timezone.now(),
                    )

            status = "success" if fail_count == 0 else "failed"
            TaskCenterTask.objects.filter(pk=task_id).update(
                status=status, progress=100, finished_at=timezone.now(),
                result="\n".join([f"执行完成：成功 {success_count}，失败 {fail_count}，共 {total}"] + detail_lines),
                detail=f"执行完成：成功 {success_count}，失败 {fail_count}，共 {total}",
            )

        import threading
        thread = threading.Thread(target=_run_batch_sync_task, args=(task_center.id, nodes), daemon=True)
        thread.start()

        return JsonResponse({
            "success": True,
            "async": True,
            "task_center_id": task_center.id,
            "task_center_detail_url": reverse("releases:task_center_detail", kwargs={"pk": task_center.id}),
        })


class ConfigSyncSingleAPIView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_resource = "configs"
    permission_action = "update"

    def post(self, request):
        from apps.nodes.models import Node
        from apps.nodes.views import _get_node_credential
        from utils.ssh import discover_nginx_configs

        data = json.loads(request.body)
        node_id = data.get("node_id")
        if not node_id:
            return JsonResponse({"success": False, "message": "缺少节点ID"})

        node = get_object_or_404(Node, pk=node_id)
        if node.is_locked:
            return JsonResponse({"success": False, "message": "节点已锁定"})

        credential = _get_node_credential(node)
        if not credential:
            return JsonResponse({"success": False, "message": "未配置SSH凭证"})

        setting = get_or_create_sync_setting(node)
        main_conf_path = data.get("main_conf_path") or setting.main_conf_path
        if main_conf_path and main_conf_path != setting.main_conf_path:
            setting.main_conf_path = main_conf_path
            setting.save(update_fields=["main_conf_path"])
        nginx_conf_path = main_conf_path
        if not nginx_conf_path:
            return JsonResponse({"success": False, "message": "未配置nginx路径"})

        auth_kwargs = {}
        if credential.auth_type == "password":
            auth_kwargs["password"] = credential.get_password()
        else:
            auth_kwargs["private_key"] = credential.get_private_key()

        discovered, errors = discover_nginx_configs(
            node.ip, node.port, credential.username,
            nginx_conf_path=nginx_conf_path, **auth_kwargs,
        )

        if errors:
            mark_discovery_failed_configs(node, errors, request.user)

        if not discovered:
            return JsonResponse({"success": False, "message": "未发现配置文件", "errors": errors})

        created, updated, skipped, orphaned = sync_discovered_configs(
            node, discovered, request.user, remark="单节点手动同步",
        )
        save_sync_path(node, nginx_conf_path, request.user)

        return JsonResponse({
            "success": True,
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "orphaned": orphaned,
            "errors": errors,
        })


class ConfigSyncProgressView(LoginRequiredMixin, View):
    """同步进度查询接口，返回前端轮询所需的进度数据结构"""

    def get(self, request):
        return JsonResponse({
            "success": True,
            "progress": {
                "completed": 0,
                "total": 1,
                "nodes": {},
            },
        })


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