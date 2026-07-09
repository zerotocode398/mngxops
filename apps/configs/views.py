from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import redirect, get_object_or_404, render
from django.urls import reverse_lazy, reverse
from django.views.generic import ListView, DetailView, UpdateView, CreateView, View
from django.http import JsonResponse
import difflib
import hashlib
import json
import uuid
from collections import OrderedDict
from urllib.parse import quote
from django.core.cache import cache
import threading

from .forms import ConfigForm
from .models import Config, ConfigVersion
from .services import (
    get_or_create_sync_setting,
    save_sync_path,
    sync_discovered_configs,
    sync_selected_configs,
    mark_discovery_failed_configs,
)
from apps.users.permissions import PermissionRequiredMixin
from utils.pagination import PerPagePaginationMixin


class ConfigListView(
    LoginRequiredMixin, PermissionRequiredMixin, PerPagePaginationMixin, ListView
):
    model = Config
    template_name = "configs/list.html"
    context_object_name = "configs"
    paginate_by = None
    ordering = ["-updated_at"]
    permission_resource = "configs"
    permission_action = "read"

    SKIP_FILES = {"mime.types"}

    def _parse_search_terms(self, search):
        env_map = {"开发": "dev", "测试": "test", "生产": "prod"}
        sync_map = {
            "同步成功": "success",
            "同步失败": "failed",
            "等待同步": "pending",
            "远程已删除": "orphaned",
        }
        status_map = {"在线": "online", "离线": "offline", "未知": "unknown"}

        env_filter = self.request.GET.get("environment", "")
        status_filter = self.request.GET.get("status", "")
        sync_status_filter = self.request.GET.get("sync_status", "")

        if not search:
            return [], env_filter, status_filter, sync_status_filter

        terms = [t.strip() for t in search.replace("，", ",").split(",") if t.strip()]
        config_terms = []

        for term in terms:
            if term in env_map and not env_filter:
                env_filter = env_map[term]
            elif term in sync_map and not sync_status_filter:
                sync_status_filter = sync_map[term]
            elif term in status_map and not status_filter:
                status_filter = status_map[term]
            else:
                config_terms.append(term)

        return config_terms, env_filter, status_filter, sync_status_filter

    def get_queryset(self):
        queryset = super().get_queryset().exclude(name__in=self.SKIP_FILES)
        search = self.request.GET.get("search", "")

        config_terms, env_filter, status_filter, sync_status_filter = (
            self._parse_search_terms(search)
        )

        if config_terms:
            for term in config_terms:
                queryset = queryset.filter(
                    Q(name__icontains=term)
                    | Q(nodes__hostname__icontains=term)
                    | Q(nodes__ip__icontains=term)
                )
        if env_filter:
            queryset = queryset.filter(nodes__environment=env_filter)
        if status_filter:
            queryset = queryset.filter(nodes__status=status_filter)
        if sync_status_filter:
            queryset = queryset.filter(sync_status=sync_status_filter)
        return queryset.prefetch_related("nodes", "created_by")

    def get_context_data(self, **kwargs):
        search = self.request.GET.get("search", "")
        _, env_filter, status_filter, sync_status_filter = self._parse_search_terms(
            search
        )

        configs = self.get_queryset()

        node_configs = OrderedDict()
        for config in configs:
            for node in config.nodes.all():
                node_configs.setdefault(node, []).append(config)

        nodes = sorted(node_configs.keys(), key=lambda n: n.hostname)

        node_ids = [n.id for n in nodes]
        orphaned_info = Config.objects.filter(
            nodes__in=node_ids, sync_status="orphaned"
        ).values_list("nodes__id", "name")
        orphaned_by_node = {}
        orphaned_counts = {}
        for node_id, name in orphaned_info:
            orphaned_by_node.setdefault(node_id, []).append(name)
            orphaned_counts[node_id] = len(orphaned_by_node[node_id])
        for node in nodes:
            node.orphaned_count = orphaned_counts.get(node.id, 0)
            node.orphaned_names = orphaned_by_node.get(node.id, [])

        pending_info = Config.objects.filter(
            nodes__in=node_ids, sync_status="pending"
        ).values_list("nodes__id", "name")
        pending_by_node = {}
        pending_counts = {}
        for node_id, name in pending_info:
            pending_by_node.setdefault(node_id, []).append(name)
            pending_counts[node_id] = len(pending_by_node[node_id])
        for node in nodes:
            node.pending_count = pending_counts.get(node.id, 0)
            node.pending_names = pending_by_node.get(node.id, [])

        failed_info = Config.objects.filter(
            nodes__in=node_ids, sync_status="failed"
        ).values_list("nodes__id", "name")
        failed_by_node = {}
        failed_counts = {}
        for node_id, name in failed_info:
            failed_by_node.setdefault(node_id, []).append(name)
            failed_counts[node_id] = len(failed_by_node[node_id])
        for node in nodes:
            node.failed_count = failed_counts.get(node.id, 0)
            node.failed_names = failed_by_node.get(node.id, [])

        per_page = self.get_paginate_by(None)
        paginator = Paginator(nodes, per_page)
        page_num = self.request.GET.get("page", 1)
        page_obj = paginator.get_page(page_num)

        paginated_node_configs = OrderedDict()
        for node in page_obj.object_list:
            paginated_node_configs[node] = node_configs[node]

        context = {
            "node_configs": paginated_node_configs,
            "page_obj": page_obj,
            "is_paginated": page_obj.has_other_pages(),
            "search": search,
            "env_filter": env_filter,
            "status_filter": status_filter,
            "sync_status_filter": sync_status_filter,
            "has_any_filter": bool(
                search or env_filter or status_filter or sync_status_filter
            ),
            "per_page": per_page,
            "per_page_options": self.per_page_options,
        }
        return context


class ConfigCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    model = Config
    form_class = ConfigForm
    template_name = "configs/create.html"
    permission_resource = "configs"
    permission_action = "create"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from apps.nodes.models import Node, NodeGroup

        context["all_nodes"] = (
            Node.objects.filter(is_locked=False)
            .prefetch_related("groups")
            .order_by("hostname")
        )
        context["node_groups"] = NodeGroup.objects.all()
        context["selected_node_id"] = self.request.GET.get("node_id", "")
        context["selected_node_ids"] = self.request.GET.get("node_id", "")
        return context

    def form_valid(self, form):
        config = form.save(commit=False)
        config.created_by = self.request.user
        config.current_version = 1
        config.sync_status = "pending"
        config.save()

        node_ids_str = self.request.POST.get("selected_node_ids", "")
        if node_ids_str:
            from apps.nodes.models import Node

            node_ids = [int(nid) for nid in node_ids_str.split(",") if nid.strip()]
            if node_ids:
                nodes = Node.objects.filter(id__in=node_ids)
                config.nodes.add(*nodes)

        ConfigVersion.objects.create(
            config=config,
            version=1,
            content=config.content,
            remark="手动创建",
            created_by=self.request.user,
        )

        messages.success(self.request, f"配置 {config.name} 创建成功")
        return redirect("configs:detail", pk=config.pk)

    def post(self, request, *args, **kwargs):
        use_glob = request.POST.get("use_glob") == "1"

        if use_glob:
            return self._handle_glob_import(request)

        return super().post(request, *args, **kwargs)

    def _handle_glob_import(self, request):
        from apps.nodes.models import Node
        from apps.nodes.views import _get_node_credential
        from utils.ssh import read_remote_file
        from django.utils import timezone

        node_ids_str = request.POST.get("selected_node_ids", "")
        selected_files = request.POST.getlist("selected_files")
        batch_remark = request.POST.get("batch_remark", "批量导入")

        if not node_ids_str:
            messages.error(request, "请选择关联节点")
            return redirect("configs:create")

        if not selected_files:
            messages.error(request, "请至少选择一个文件")
            return redirect("configs:create")

        node_ids = [int(nid) for nid in node_ids_str.split(",") if nid.strip()]
        nodes = Node.objects.filter(id__in=node_ids)

        if not nodes.exists():
            messages.error(request, "未找到有效的节点")
            return redirect("configs:create")

        locked_nodes = [n for n in nodes if n.is_locked]
        if locked_nodes:
            names = ",".join([n.hostname for n in locked_nodes])
            messages.error(request, f"节点 {names} 已锁定，无法导入配置")
            return redirect("configs:create")

        try:
            created_count = 0
            failed_files = []

            for node in nodes:
                credential = _get_node_credential(node)
                if not credential:
                    failed_files.append(f"节点 {node.hostname}: 未配置SSH凭证")
                    continue

                auth_kwargs = {}
                if credential.auth_type == "password":
                    auth_kwargs["password"] = credential.get_password()
                else:
                    auth_kwargs["private_key"] = credential.get_private_key()

                for file_path in selected_files:
                    success, content = read_remote_file(
                        host=node.ip,
                        port=node.port,
                        username=credential.username,
                        file_path=file_path,
                        **auth_kwargs,
                    )

                    if not success:
                        failed_files.append(f"{node.hostname}:{file_path}: {content}")
                        continue

                    config_name = file_path.split("/")[-1]

                    existing_config = Config.objects.filter(
                        nodes=node, file_path=file_path
                    ).first()

                    if existing_config:
                        config = existing_config
                        config.content = content
                        config.current_version = config.current_version + 1
                        config.sync_status = "success"
                        config.last_sync_time = timezone.now()
                        config.save()

                        ConfigVersion.objects.create(
                            config=config,
                            version=config.current_version,
                            content=content,
                            remark=batch_remark or "批量导入",
                            created_by=request.user,
                        )
                        created_count += 1
                    else:
                        config = Config.objects.create(
                            name=config_name,
                            file_path=file_path,
                            content=content,
                            current_version=1,
                            sync_status="success",
                            last_sync_time=timezone.now(),
                            created_by=request.user,
                        )
                        config.nodes.add(node)

                        ConfigVersion.objects.create(
                            config=config,
                            version=1,
                            content=content,
                            remark=batch_remark or "批量导入",
                            created_by=request.user,
                        )
                        created_count += 1

            if created_count > 0:
                messages.success(request, f"成功导入 {created_count} 个配置文件")

            if failed_files:
                messages.warning(
                    request, f"以下文件导入失败：{'; '.join(failed_files[:5])}"
                )

            return redirect("configs:list")

        except Exception as e:
            messages.error(request, f"批量导入失败：{str(e)}")
            return redirect("configs:create")


class ConfigDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    model = Config
    template_name = "configs/detail.html"
    context_object_name = "config"
    permission_resource = "configs"
    permission_action = "read"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        config = self.object
        latest_version = config.versions.order_by("-version").first()
        context["latest_version"] = latest_version
        return context


class ConfigEditView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = Config
    form_class = ConfigForm
    template_name = "configs/edit.html"
    context_object_name = "config"
    permission_resource = "configs"
    permission_action = "update"

    def dispatch(self, request, *args, **kwargs):
        config = self.get_object()
        locked_nodes = [n for n in config.nodes.all() if n.is_locked]
        if locked_nodes:
            names = ",".join([n.hostname for n in locked_nodes])
            messages.error(request, f"节点 {names} 已锁定，无法编辑配置")
            return redirect("configs:list")
        return super().dispatch(request, *args, **kwargs)

    def get_success_url(self):
        return reverse("configs:detail", kwargs={"pk": self.object.pk})

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        original_content = Config.objects.only("content").get(pk=self.object.pk).content
        form = self.get_form()
        if not form.is_valid():
            return self.form_invalid(form)

        if request.POST.get("confirm_save") == "yes":
            return self._save_after_review(form)
        return self._render_review(form, original_content)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["next_version"] = self.object.current_version + 1
        return context

    def _render_review(self, form, current_content):
        new_content = form.cleaned_data.get("content", "")
        context = {
            "config": self.object,
            "next_version": self.object.current_version + 1,
            "split_diff_rows": _build_split_diff_rows(current_content, new_content),
            "new_name": form.cleaned_data.get("name", self.object.name),
            "new_file_path": form.cleaned_data.get("file_path", self.object.file_path),
            "new_content": new_content,
            "remark": form.cleaned_data.get("remark", ""),
        }
        return render(self.request, "configs/edit_review.html", context)

    def _save_after_review(self, form):
        config = form.save(commit=False)
        remark = form.cleaned_data.get("remark", "")
        new_content = form.cleaned_data["content"]
        new_version = self.object.current_version + 1

        ConfigVersion.objects.create(
            config=self.object,
            version=new_version,
            content=new_content,
            remark=remark,
            created_by=self.request.user,
        )

        config.current_version = new_version
        config.save()
        self.object.prune_old_versions()

        messages.success(
            self.request,
            f"配置 {self.object.name} 保存成功（v{new_version}）",
        )
        return redirect(self.get_success_url())

    def form_valid(self, form):
        return self._save_after_review(form)

    def form_invalid(self, form):
        messages.error(self.request, "配置保存失败，请检查输入")
        return super().form_invalid(form)


class ConfigVersionListView(
    LoginRequiredMixin, PermissionRequiredMixin, PerPagePaginationMixin, ListView
):
    model = ConfigVersion
    template_name = "configs/versions.html"
    context_object_name = "versions"
    paginate_by = 10
    permission_resource = "configs"
    permission_action = "read"

    def get_queryset(self):
        self.config = get_object_or_404(Config, pk=self.kwargs["pk"])
        return (
            ConfigVersion.objects.filter(config=self.config)
            .select_related("created_by")
            .order_by("-version")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["config"] = self.config
        context["next_version"] = self.config.current_version + 1
        context["retention_days"] = 180
        return context


class ConfigVersionDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    model = ConfigVersion
    template_name = "configs/version_detail.html"
    context_object_name = "version"
    permission_resource = "configs"
    permission_action = "read"

    def get_object(self, queryset=None):
        config_pk = self.kwargs.get("pk")
        version_id = self.kwargs.get("version_id")
        return get_object_or_404(
            ConfigVersion,
            pk=version_id,
            config_id=config_pk,
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["config"] = self.object.config
        context["next_version"] = self.object.config.current_version + 1
        return context


class ConfigVersionRestoreView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_resource = "configs"
    permission_action = "update"

    def post(self, request, pk, version_id):
        config = get_object_or_404(Config, pk=pk)
        locked_nodes = [n for n in config.nodes.all() if n.is_locked]
        if locked_nodes:
            names = ",".join([n.hostname for n in locked_nodes])
            messages.error(request, f"节点 {names} 已锁定，无法恢复版本")
            return redirect("configs:versions", pk=config.pk)
        old_version = get_object_or_404(ConfigVersion, pk=version_id, config=config)

        new_version_num = config.current_version + 1

        ConfigVersion.objects.create(
            config=config,
            version=new_version_num,
            content=old_version.content,
            remark=f"恢复自 v{old_version.version}",
            created_by=request.user,
        )

        config.content = old_version.content
        config.current_version = new_version_num
        config.save()

        config.prune_old_versions()

        messages.success(
            request,
            f"配置 {config.name} 已恢复到 v{old_version.version}（生成新版本 v{new_version_num}）",
        )
        return redirect(reverse("configs:versions", kwargs={"pk": config.pk}))


def _build_change_list(base_content, target_content):
    base_lines = base_content.splitlines()
    target_lines = target_content.splitlines()
    matcher = difflib.SequenceMatcher(a=base_lines, b=target_lines)

    changes = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag in ("replace", "delete"):
            for idx, line in enumerate(base_lines[i1:i2], start=i1 + 1):
                changes.append(
                    {
                        "type": "removed",
                        "line_no": idx,
                        "content": line,
                    }
                )
        if tag in ("replace", "insert"):
            for idx, line in enumerate(target_lines[j1:j2], start=j1 + 1):
                changes.append(
                    {
                        "type": "added",
                        "line_no": idx,
                        "content": line,
                    }
                )
    return changes


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


class ConfigVersionCompareView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_resource = "configs"
    permission_action = "update"

    def get(self, request, pk):
        config = get_object_or_404(Config, pk=pk)
        versions = config.versions.order_by("-version")
        selected_base = request.GET.get("base_version")
        selected_target = request.GET.get("target_version")

        base_obj = None
        target_obj = None
        change_list = []
        split_diff_rows = []
        diff_text = ""
        draft_content = ""
        has_diff = False
        payload = ""
        payload_digest = ""

        if selected_base and selected_target and selected_base != selected_target:
            base_obj = get_object_or_404(versions, id=selected_base)
            target_obj = get_object_or_404(versions, id=selected_target)

            base_content = base_obj.content
            target_content = target_obj.content
            change_list = _build_change_list(base_content, target_content)
            split_diff_rows = _build_split_diff_rows(base_content, target_content)
            has_diff = len(change_list) > 0

            diff_lines = difflib.unified_diff(
                base_content.splitlines(),
                target_content.splitlines(),
                fromfile=f"{base_obj.version_label}",
                tofile=f"{target_obj.version_label}",
                lineterm="",
            )
            diff_text = "\n".join(diff_lines)
            draft_content = target_content

            payload_dict = {
                "config_id": config.id,
                "base_version_id": base_obj.id,
                "target_version_id": target_obj.id,
                "target_content": target_content,
                "target_label": target_obj.version_label,
            }
            payload = json.dumps(payload_dict, ensure_ascii=False)
            payload_digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()

        context = {
            "config": config,
            "versions": versions,
            "selected_base": selected_base,
            "selected_target": selected_target,
            "base_obj": base_obj,
            "target_obj": target_obj,
            "has_diff": has_diff,
            "split_diff_rows": split_diff_rows,
            "diff_text": diff_text,
            "draft_content": draft_content,
            "payload": payload,
            "payload_digest": payload_digest,
        }
        return render(request, "configs/version_compare.html", context)


class ConfigVersionCompareApplyView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_resource = "configs"
    permission_action = "update"

    def post(self, request, pk):
        config = get_object_or_404(Config, pk=pk)
        locked_nodes = [n for n in config.nodes.all() if n.is_locked]
        if locked_nodes:
            names = ",".join([n.hostname for n in locked_nodes])
            messages.error(request, f"节点 {names} 已锁定，无法应用差异")
            return redirect("configs:versions", pk=config.pk)
        confirmed_content = request.POST.get("confirmed_content", "")
        is_confirmed = request.POST.get("confirm_change") == "yes"
        payload = request.POST.get("payload", "")
        payload_digest = request.POST.get("payload_digest", "")

        if not is_confirmed:
            messages.error(request, "请先确认差异内容后再更新数据库")
            return redirect(
                reverse("configs:version_compare", kwargs={"pk": config.pk})
            )

        if not payload or not payload_digest:
            messages.error(request, "缺少确认参数，无法应用差异")
            return redirect(
                reverse("configs:version_compare", kwargs={"pk": config.pk})
            )

        calc_digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        if calc_digest != payload_digest:
            messages.error(request, "差异确认数据校验失败，请重新比较")
            return redirect(
                reverse("configs:version_compare", kwargs={"pk": config.pk})
            )

        try:
            data = json.loads(payload)
        except Exception:
            messages.error(request, "差异确认数据格式错误")
            return redirect(
                reverse("configs:version_compare", kwargs={"pk": config.pk})
            )

        if data.get("config_id") != config.id:
            messages.error(request, "差异确认数据与当前配置不匹配")
            return redirect(
                reverse("configs:version_compare", kwargs={"pk": config.pk})
            )

        target_version_id = data.get("target_version_id")
        target_obj = get_object_or_404(config.versions, id=target_version_id)
        target_content = data.get("target_content", "")

        if target_obj.content != target_content:
            messages.error(request, "目标版本内容已变化，请重新比较")
            return redirect(
                reverse("configs:version_compare", kwargs={"pk": config.pk})
            )

        if confirmed_content != target_content:
            messages.error(request, "提交内容与目标版本不一致，请重新比较后确认")
            return redirect(
                reverse("configs:version_compare", kwargs={"pk": config.pk})
            )

        if not confirmed_content.strip():
            messages.error(request, "修改后的文件内容不能为空")
            return redirect(
                reverse("configs:version_compare", kwargs={"pk": config.pk})
            )

        if config.content == confirmed_content:
            messages.info(request, "当前配置与目标版本一致，无需更新")
            return redirect(reverse("configs:versions", kwargs={"pk": config.pk}))

        previous_content = config.content
        new_version_num = config.current_version + 1
        config.content = confirmed_content
        config.current_version = new_version_num
        config.save()

        confirmed_changes = _build_change_list(previous_content, confirmed_content)

        ConfigVersion.objects.create(
            config=config,
            version=new_version_num,
            content=confirmed_content,
            remark=(
                f"差异确认更新：{target_obj.version_label}"
                f"（确认 {len(confirmed_changes)} 项变更）"
            ),
            created_by=request.user,
        )
        config.prune_old_versions()

        messages.success(
            request,
            f"差异确认成功，已更新配置并生成新版本 V{new_version_num}",
        )
        return redirect(reverse("configs:versions", kwargs={"pk": config.pk}))


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

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        nodes = context["nodes"]

        search = self.request.GET.get("search", "")
        group_search = self.request.GET.get("group_search", "")

        node_stats = {}
        node_sync_paths = {}
        node_pending_configs = {}
        node_groups = {}

        for node in nodes:
            configs = Config.objects.filter(nodes=node).exclude(name__in=["mime.types"])
            last_synced = (
                configs.exclude(sync_status__in=["pending", "syncing"])
                .order_by("-last_sync_time")
                .first()
            )
            node_stats[node.id] = {
                "success": configs.filter(sync_status="success").count(),
                "failed": configs.filter(sync_status="failed").count(),
                "syncing": configs.filter(sync_status="syncing").count(),
                "pending": configs.filter(sync_status="pending").count(),
                "orphaned": configs.filter(sync_status="orphaned").count(),
                "total": configs.count(),
                "last_sync": last_synced.last_sync_time if last_synced else None,
                "fallback_time": last_synced.updated_at if last_synced else None,
            }

            setting = get_or_create_sync_setting(node)
            node_sync_paths[node.id] = (
                setting.main_conf_path if setting.main_conf_path else ""
            )

            pending_configs = list(
                configs.filter(sync_status="pending").values_list("name", flat=True)
            )
            node_pending_configs[node.id] = pending_configs

            node_groups[node.id] = list(node.groups.all())

        context["node_stats"] = node_stats
        context["node_sync_paths"] = node_sync_paths
        context["node_pending_configs"] = node_pending_configs
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
            return JsonResponse(
                {"success": False, "message": f"最多只能选择 {MAX_BATCH} 个节点"}
            )

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
                "node_id": node.id,
                "hostname": node.hostname,
                "ip": node.ip,
                "success": False,
                "message": "",
                "created": 0,
                "updated": 0,
                "orphaned": 0,
                "errors": [],
            }

            if node.is_locked:
                result["message"] = "节点已锁定"
                return result

            credential = _get_node_credential(node)
            if not credential:
                result["message"] = "未配置SSH凭证"
                return result

            setting = get_or_create_sync_setting(node)
            nginx_conf_path = setting.main_conf_path
            if not nginx_conf_path:
                result["message"] = "未配置nginx路径"
                return result

            if credential.auth_type == "password":
                discovered, errors = discover_nginx_configs(
                    node.ip,
                    node.port,
                    credential.username,
                    password=credential.get_password(),
                    nginx_conf_path=nginx_conf_path,
                )
            else:
                discovered, errors = discover_nginx_configs(
                    node.ip,
                    node.port,
                    credential.username,
                    private_key=credential.get_private_key(),
                    nginx_conf_path=nginx_conf_path,
                )

            if errors:
                mark_discovery_failed_configs(node, errors, request.user)
                result["errors"].extend(errors)

            if discovered:
                created, updated, skipped, orphaned = sync_discovered_configs(
                    node,
                    discovered,
                    request.user,
                    remark="批量节点全量同步",
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
                status="running",
                started_at=timezone.now(),
                progress=0,
                detail=f"执行中：0/{len(sync_nodes)}",
            )

            success_count = 0
            fail_count = 0
            done = 0
            total_created = 0
            total_updated = 0
            total_orphaned = 0
            detail_lines = []

            from concurrent.futures import ThreadPoolExecutor, as_completed

            with ThreadPoolExecutor(max_workers=MAX_BATCH) as executor:
                future_to_node = {
                    executor.submit(_sync_one, node): node for node in sync_nodes
                }
                for future in as_completed(future_to_node):
                    result = future.result()
                    done += 1
                    if result.get("success"):
                        success_count += 1
                        total_created += result["created"]
                        total_updated += result["updated"]
                        total_orphaned += result["orphaned"]

                        parts = [
                            f"[成功] {result['hostname']}({result['ip']}) - {result.get('message','')}"
                        ]
                        if result.get("created_names"):
                            parts.append(
                                f"  [新增] {', '.join(result['created_names'])}"
                            )
                        if result.get("updated_names"):
                            parts.append(
                                f"  [更新] {', '.join(result['updated_names'])}"
                            )
                        if result.get("orphaned_names"):
                            parts.append(
                                f"  [删除] {', '.join(result['orphaned_names'])}"
                            )
                        detail_lines.append("\n".join(parts))
                    else:
                        fail_count += 1
                        detail_lines.append(
                            f"[失败] {result['hostname']}({result['ip']}) - {result.get('message','')}"
                        )

                    TaskCenterTask.objects.filter(pk=task_id).update(
                        progress=(
                            int(done * 100 / len(sync_nodes)) if sync_nodes else 100
                        ),
                        detail=f"执行中：成功 {success_count}，失败 {fail_count}，已完成 {done}/{len(sync_nodes)}",
                        updated_at=timezone.now(),
                    )

            status = "success" if fail_count == 0 else "failed"
            TaskCenterTask.objects.filter(pk=task_id).update(
                status=status,
                progress=100,
                finished_at=timezone.now(),
                detail=f"执行完成：成功 {success_count}，失败 {fail_count}，共 {len(sync_nodes)}，新增 {total_created}，更新 {total_updated}",
                result="\n".join(detail_lines),
                updated_at=timezone.now(),
            )

        thread = threading.Thread(
            target=_run_batch_sync_task,
            args=(task_center.id, nodes),
            daemon=True,
        )
        thread.start()

        return JsonResponse(
            {
                "success": True,
                "async": True,
                "message": f"已创建后台批量同步任务（{total} 台节点）",
                "task_center_id": task_center.id,
                "task_center_detail_url": str(
                    reverse_lazy("releases:task_center_detail", args=[task_center.id])
                ),
                "task_center_home_url": str(reverse_lazy("releases:history")),
            }
        )


class ConfigSyncSingleAPIView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_resource = "configs"
    permission_action = "update"

    def post(self, request):
        from apps.nodes.models import Node
        from apps.nodes.views import _get_node_credential
        from utils.ssh import discover_nginx_configs
        from django.utils import timezone

        data = json.loads(request.body)
        node_id = data.get("node_id")
        main_conf_path = data.get("main_conf_path", "").strip()
        selected_paths = data.get("selected_paths", [])
        task_id = data.get("task_id", str(uuid.uuid4()))

        if not node_id:
            return JsonResponse({"success": False, "message": "缺少节点ID"})
        if not main_conf_path:
            return JsonResponse(
                {"success": False, "message": "请输入 nginx.conf 主配置文件路径"}
            )

        node = get_object_or_404(Node, id=node_id)

        if node.is_locked:
            return JsonResponse(
                {"success": False, "message": f"节点 {node.hostname} 已锁定，无法同步"}
            )

        progress = {
            "task_id": task_id,
            "total": 1,
            "completed": 0,
            "nodes": {
                str(node_id): {
                    "status": "waiting",
                    "hostname": node.hostname,
                    "created": 0,
                    "updated": 0,
                    "skipped": 0,
                    "orphaned": 0,
                    "total_files": 0,
                    "completed_files": 0,
                    "error": "",
                    "time": 0,
                }
            },
        }
        cache.set(f"batch_sync:{task_id}", progress, timeout=300)

        start_time = timezone.now()
        progress["nodes"][str(node_id)]["status"] = "syncing"
        cache.set(f"batch_sync:{task_id}", progress, timeout=300)

        try:
            credential = _get_node_credential(node)
            if not credential:
                progress["nodes"][str(node_id)]["status"] = "failed"
                progress["nodes"][str(node_id)]["error"] = "未配置SSH凭证"
                progress["completed"] = 1
                cache.set(f"batch_sync:{task_id}", progress, timeout=300)
                return JsonResponse({"success": True, "task_id": task_id})

            if credential.auth_type == "password":
                discovered, errors = discover_nginx_configs(
                    node.ip,
                    node.port,
                    credential.username,
                    password=credential.get_password(),
                    nginx_conf_path=main_conf_path,
                )
            else:
                discovered, errors = discover_nginx_configs(
                    node.ip,
                    node.port,
                    credential.username,
                    private_key=credential.get_private_key(),
                    nginx_conf_path=main_conf_path,
                )

            if errors:
                progress["nodes"][str(node_id)]["error"] = "; ".join(errors[:5])
                mark_discovery_failed_configs(node, errors, request.user)

            if not discovered:
                error_msg = (
                    progress["nodes"][str(node_id)]["error"]
                    or f"未发现配置文件（路径: {main_conf_path}）"
                )
                progress["nodes"][str(node_id)]["error"] = error_msg
                progress["nodes"][str(node_id)]["status"] = "failed"
                progress["completed"] = 1
                cache.set(f"batch_sync:{task_id}", progress, timeout=300)
                return JsonResponse(
                    {
                        "success": True,
                        "task_id": task_id,
                        "summary": {
                            "created": 0,
                            "updated": 0,
                            "skipped": 0,
                            "orphaned": 0,
                            "errors": [error_msg],
                        },
                    }
                )

            progress["total"] = len(discovered)
            progress["nodes"][str(node_id)]["total_files"] = len(discovered)
            cache.set(f"batch_sync:{task_id}", progress, timeout=300)

            def _on_file_progress(action, name):
                node_data = progress["nodes"][str(node_id)]
                node_data["completed_files"] += 1
                if action == "created":
                    node_data["created"] += 1
                elif action == "updated":
                    node_data["updated"] += 1
                elif action == "skipped":
                    node_data["skipped"] += 1
                elif action == "orphaned":
                    node_data["orphaned"] += 1
                progress["completed"] = node_data["completed_files"]
                cache.set(f"batch_sync:{task_id}", progress, timeout=300)

            if selected_paths:
                created, updated, skipped, orphaned = sync_selected_configs(
                    node,
                    selected_paths,
                    discovered,
                    request.user,
                    remark="从远程节点部分同步",
                    progress_callback=_on_file_progress,
                )
            else:
                created, updated, skipped, orphaned = sync_discovered_configs(
                    node,
                    discovered,
                    request.user,
                    remark="从远程节点全量同步",
                    progress_callback=_on_file_progress,
                )

            if discovered:
                save_sync_path(node, main_conf_path, request.user)

            files_detail = _build_files_detail(
                discovered, errors, created, updated, orphaned
            )

            progress["nodes"][str(node_id)]["created"] = len(created)
            progress["nodes"][str(node_id)]["updated"] = len(updated)
            progress["nodes"][str(node_id)]["skipped"] = len(skipped)
            progress["nodes"][str(node_id)]["orphaned"] = len(orphaned)
            progress["nodes"][str(node_id)]["files"] = files_detail

            if errors:
                progress["nodes"][str(node_id)]["error"] = "; ".join(errors[:5])

            elapsed = (timezone.now() - start_time).total_seconds()
            progress["nodes"][str(node_id)]["time"] = round(elapsed, 1)

            if errors:
                progress["nodes"][str(node_id)]["status"] = "failed"
            else:
                progress["nodes"][str(node_id)]["status"] = "success"

            progress["completed"] = progress["total"]
            cache.set(f"batch_sync:{task_id}", progress, timeout=300)

            return JsonResponse(
                {
                    "success": True,
                    "task_id": task_id,
                    "summary": {
                        "created": len(created),
                        "updated": len(updated),
                        "skipped": len(skipped),
                        "orphaned": len(orphaned),
                        "errors": errors[:5],
                    },
                }
            )

        except Exception as e:
            error_msg = f"同步异常: {str(e)}"
            progress["nodes"][str(node_id)]["status"] = "failed"
            progress["nodes"][str(node_id)]["error"] = error_msg
            progress["completed"] = progress["total"]
            cache.set(f"batch_sync:{task_id}", progress, timeout=300)
            return JsonResponse(
                {
                    "success": True,
                    "task_id": task_id,
                    "summary": {
                        "created": 0,
                        "updated": 0,
                        "skipped": 0,
                        "orphaned": 0,
                        "errors": [error_msg],
                    },
                }
            )


class ConfigSyncProgressView(LoginRequiredMixin, View):
    def get(self, request):
        task_id = request.GET.get("task_id")
        if not task_id:
            return JsonResponse({"success": False, "message": "缺少task_id"})

        progress = cache.get(f"batch_sync:{task_id}")
        if not progress:
            return JsonResponse({"success": False, "message": "任务不存在或已过期"})

        return JsonResponse({"success": True, "progress": progress})


class ConfigDeleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_resource = "configs"
    permission_action = "delete"

    def post(self, request, pk):
        config = get_object_or_404(Config, pk=pk)
        hosts = ",".join(config.nodes.values_list("hostname", flat=True))
        config_name = config.name

        if config.sync_status not in ("orphaned", "pending"):
            messages.error(
                request,
                f"只能删除「远程已删除」或「等待同步」的配置，"
                f"{config_name} 当前状态为「{config.get_sync_status_display()}」",
            )
            return redirect("configs:list")

        config.delete()
        messages.success(request, f"已删除配置 {config_name}（节点 {hosts}）")
        return redirect("configs:list")


class ConfigNodeDeleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_resource = "configs"
    permission_action = "delete"

    def post(self, request, pk):
        from apps.nodes.models import Node

        node = get_object_or_404(Node, pk=pk)
        configs = Config.objects.filter(nodes=node)
        count = configs.count()

        if count == 0:
            messages.info(request, f"节点 {node.hostname} 没有配置文件")
            return redirect("configs:list")

        configs.delete()
        messages.success(request, f"已删除节点 {node.hostname} 的 {count} 个配置文件")
        return redirect("configs:list")


class ConfigByNodesAPIView(LoginRequiredMixin, View):
    def get(self, request):
        from apps.nodes.models import Node

        node_ids_str = request.GET.get("node_ids", "")
        if not node_ids_str:
            return JsonResponse({"success": False, "message": "缺少node_ids参数"})

        try:
            node_ids = [int(nid) for nid in node_ids_str.split(",")]
        except ValueError:
            return JsonResponse({"success": False, "message": "node_ids格式错误"})

        nodes = Node.objects.filter(id__in=node_ids)
        result = []

        for node in nodes:
            configs = Config.objects.filter(nodes=node).exclude(name__in=["mime.types"])
            for config in configs:
                versions = config.versions.order_by("-version").values(
                    "id", "version", "created_at"
                )
                result.append(
                    {
                        "id": config.id,
                        "name": config.name,
                        "file_path": config.file_path,
                        "node_id": node.id,
                        "node_name": node.hostname,
                        "current_version": config.current_version,
                        "sync_status": config.sync_status,
                        "versions": [
                            {
                                "id": v["id"],
                                "version": v["version"],
                                "created_at": (
                                    v["created_at"].strftime("%Y-%m-%d %H:%M")
                                    if v["created_at"]
                                    else ""
                                ),
                            }
                            for v in versions
                        ],
                    }
                )

        return JsonResponse({"success": True, "data": result})


class ConfigGlobPreviewView(LoginRequiredMixin, View):
    def get(self, request):
        from apps.nodes.models import Node
        from apps.nodes.views import _get_node_credential
        from utils.ssh import discover_nginx_configs

        node_id = request.GET.get("node_id")
        main_conf_path = request.GET.get("main_conf_path", "").strip()

        if not node_id:
            return JsonResponse({"success": False, "message": "缺少node_id参数"})
        if not main_conf_path:
            return JsonResponse(
                {"success": False, "message": "请输入 nginx.conf 主配置文件路径"}
            )

        node = get_object_or_404(Node, id=node_id)

        if node.is_locked:
            return JsonResponse(
                {"success": False, "message": f"节点 {node.hostname} 已锁定"}
            )

        credential = _get_node_credential(node)
        if not credential:
            return JsonResponse(
                {"success": False, "message": f"节点 {node.hostname} 未配置SSH凭证"}
            )

        try:
            if credential.auth_type == "password":
                discovered, errors = discover_nginx_configs(
                    node.ip,
                    node.port,
                    credential.username,
                    password=credential.get_password(),
                    nginx_conf_path=main_conf_path,
                )
            else:
                discovered, errors = discover_nginx_configs(
                    node.ip,
                    node.port,
                    credential.username,
                    private_key=credential.get_private_key(),
                    nginx_conf_path=main_conf_path,
                )

            result = []
            for item in discovered:
                result.append(
                    {
                        "name": item["name"],
                        "path": item["path"],
                    }
                )

            return JsonResponse(
                {
                    "success": True,
                    "data": result,
                    "errors": errors[:5],
                }
            )

        except Exception as e:
            return JsonResponse({"success": False, "message": f"预览失败: {str(e)}"})


class ConfigUpdatePreviewView(LoginRequiredMixin, View):
    def get(self, request):
        from apps.nodes.views import _get_node_credential
        from utils.ssh import discover_nginx_configs

        config_id = request.GET.get("config_id")
        if not config_id:
            return JsonResponse({"success": False, "message": "缺少config_id参数"})

        config = get_object_or_404(Config, pk=config_id)
        node = config.nodes.first()

        if not node:
            return JsonResponse({"success": False, "message": "配置未关联节点"})

        if node.is_locked:
            return JsonResponse(
                {"success": False, "message": f"节点 {node.hostname} 已锁定"}
            )

        credential = _get_node_credential(node)
        if not credential:
            return JsonResponse(
                {"success": False, "message": f"节点 {node.hostname} 未配置SSH凭证"}
            )

        if not credential.is_enabled:
            return JsonResponse(
                {"success": False, "message": f"节点 {node.hostname} 关联凭证已禁用"}
            )

        try:
            auth_kwargs = {}
            if credential.auth_type == "password":
                auth_kwargs["password"] = credential.get_password()
            else:
                auth_kwargs["private_key"] = credential.get_private_key()

            discovered, errors = discover_nginx_configs(
                host=node.ip,
                port=node.port,
                username=credential.username,
                nginx_conf_path=config.file_path,
                **auth_kwargs,
            )

            result = []
            for item in discovered:
                result.append(
                    {
                        "name": item["name"],
                        "path": item["path"],
                    }
                )

            return JsonResponse(
                {
                    "success": True,
                    "data": result,
                    "total": len(result),
                    "errors": errors[:5],
                    "config_name": config.name,
                    "node_hostname": node.hostname,
                }
            )

        except Exception as e:
            return JsonResponse({"success": False, "message": f"预览失败: {str(e)}"})


class ConfigUpdateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_resource = "configs"
    permission_action = "update"

    def post(self, request, pk):
        from apps.nodes.views import _get_node_credential
        from utils.ssh import discover_nginx_configs

        config = get_object_or_404(Config, pk=pk)
        node = config.nodes.first()

        if not node:
            messages.error(request, "配置未关联节点，无法更新")
            return redirect("configs:list")

        if node.is_locked:
            messages.error(request, f"节点 {node.hostname} 已锁定，无法更新配置")
            return redirect("configs:list")

        if config.sync_status == "orphaned":
            messages.error(request, f"配置 {config.name} 已从远程删除，无法更新")
            return redirect("configs:list")

        try:
            credential = _get_node_credential(node)
            if not credential:
                messages.error(request, f"节点 {node.hostname} 未配置凭证")
                return redirect("configs:list")

            if not credential.is_enabled:
                messages.error(request, f"节点 {node.hostname} 关联凭证已禁用")
                return redirect("configs:list")

            host = node.ip
            ssh_port = node.port
            username = credential.username

            auth_kwargs = {}
            if credential.auth_type == "password":
                auth_kwargs["password"] = credential.get_password()
            else:
                auth_kwargs["private_key"] = credential.get_private_key()

            discovered, errors = discover_nginx_configs(
                host=host,
                port=ssh_port,
                username=username,
                nginx_conf_path=config.file_path,
                **auth_kwargs,
            )

            if errors:
                node = config.nodes.first()
                if node:
                    mark_discovery_failed_configs(node, errors, request.user)

            if not discovered:
                error_msg = "; ".join(errors) if errors else "未发现配置文件"
                config.sync_status = "failed"
                config.last_sync_error = error_msg
                config.save(
                    update_fields=["sync_status", "last_sync_error", "updated_at"]
                )
                messages.error(request, f"读取远程文件失败：{error_msg}")
                return redirect("configs:list")

            created, updated, skipped, orphaned = sync_discovered_configs(
                node,
                discovered,
                request.user,
                remark="从远程节点更新",
                mark_orphaned=False,
            )

            total_affected = len(created) + len(updated)
            if total_affected > 0:
                if total_affected > 1:
                    messages.success(
                        request,
                        f"配置 {config.name} 已从远程节点更新，共更新 {total_affected} 个文件"
                        f"（新增 {len(created)}，更新 {len(updated)}"
                        + (f"，未变化 {len(skipped)}" if skipped else "")
                        + "）",
                    )
                else:
                    messages.success(request, f"配置 {config.name} 已从远程节点更新")
            elif skipped:
                messages.info(
                    request,
                    f"配置 {config.name} 无变化"
                    + (f"（共 {len(skipped)} 个文件）" if len(skipped) > 1 else ""),
                )
            else:
                messages.info(request, f"配置 {config.name} 无变化")

            if errors:
                messages.warning(request, f"部分文件读取失败：{'; '.join(errors[:3])}")

            return redirect("configs:detail", pk=config.pk)

        except Exception as e:
            config.sync_status = "failed"
            config.last_sync_error = str(e)
            config.save(update_fields=["sync_status", "last_sync_error", "updated_at"])
            messages.error(request, f"更新配置失败：{str(e)}")
            return redirect("configs:list")


class ConfigBatchDeleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_resource = "configs"
    permission_action = "delete"

    def post(self, request):
        config_ids = request.POST.getlist("config_ids")

        if not config_ids:
            messages.error(request, "请选择要删除的配置")
            return redirect("configs:list")

        configs = Config.objects.filter(id__in=config_ids)
        deleted_count = 0
        failed_configs = []

        for config in configs:
            if config.sync_status in ("orphaned", "pending"):
                config.delete()
                deleted_count += 1
            else:
                failed_configs.append(
                    f"{config.name}（{','.join(config.nodes.values_list('hostname', flat=True))}）- {config.get_sync_status_display()}"
                )

        if deleted_count > 0:
            messages.success(request, f"已删除 {deleted_count} 个配置")
        if failed_configs:
            messages.warning(
                request,
                f"以下配置无法删除：{'; '.join(failed_configs[:5])}",
            )

        return redirect("configs:list")


def _build_files_detail(discovered, errors, created, updated, orphaned):
    import re

    files_detail = []
    for item in discovered:
        file_name = item["name"]
        file_path = item["path"]
        status = "skipped"
        if file_name in created:
            status = "created"
        elif file_name in updated:
            status = "updated"
        elif file_name in orphaned:
            status = "orphaned"
        files_detail.append(
            {
                "name": file_name,
                "path": file_path,
                "status": status,
            }
        )
    if errors:
        pattern = re.compile(r"^读取 (.+?) 失败: (.+)$")
        for error in errors:
            match = pattern.match(error)
            if match:
                failed_path = match.group(1)
                failed_name = failed_path.split("/")[-1]
                failed_detail = match.group(2)
                files_detail.append(
                    {
                        "name": failed_name,
                        "path": failed_path,
                        "status": "failed",
                        "error": failed_detail,
                    }
                )
    return files_detail
