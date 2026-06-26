from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.db.models import Q
from django.shortcuts import redirect, get_object_or_404, render
from django.urls import reverse_lazy, reverse
from django.views.generic import ListView, DetailView, UpdateView, CreateView, View
from django.http import JsonResponse
import difflib
import hashlib
import json
import uuid
from urllib.parse import quote
from django.core.cache import cache

from .forms import ConfigForm
from .models import Config, ConfigVersion
from .services import (
    get_or_create_sync_setting,
    save_sync_path,
    sync_discovered_configs,
    sync_selected_configs,
)
from apps.users.permissions import PermissionRequiredMixin
from utils.pagination import PerPagePaginationMixin


class ConfigListView(
    LoginRequiredMixin, PermissionRequiredMixin, PerPagePaginationMixin, ListView
):
    model = Config
    template_name = "configs/list.html"
    context_object_name = "configs"
    paginate_by = 10
    ordering = ["-updated_at"]
    permission_resource = "configs"
    permission_action = "read"

    SKIP_FILES = {"mime.types"}

    def get_queryset(self):
        queryset = super().get_queryset().exclude(name__in=self.SKIP_FILES)
        search = self.request.GET.get("search", "")
        env_filter = self.request.GET.get("environment", "")
        status_filter = self.request.GET.get("status", "")
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) | Q(node__hostname__icontains=search)
            )
        if env_filter:
            queryset = queryset.filter(node__environment=env_filter)
        if status_filter:
            queryset = queryset.filter(node__status=status_filter)
        return queryset.select_related("node", "created_by")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        from collections import OrderedDict

        search = self.request.GET.get("search", "")
        env_filter = self.request.GET.get("environment", "")
        status_filter = self.request.GET.get("status", "")

        context["search"] = search
        context["env_filter"] = env_filter
        context["status_filter"] = status_filter

        node_configs = OrderedDict()
        for config in context["configs"]:
            node_configs.setdefault(config.node, []).append(config)

        context["node_configs"] = node_configs
        context["has_any_filter"] = bool(search or env_filter or status_filter)
        return context


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
            Node.objects.all()
            .select_related("created_by")
            .prefetch_related("config_set", "groups")
            .order_by("-created_at")
        )

        search = self.request.GET.get("search", "")
        if search:
            from django.db.models import Q

            queryset = queryset.filter(
                Q(hostname__icontains=search) | Q(ip__icontains=search)
            )

        group_search = self.request.GET.get("group_search", "")
        if group_search:
            group_names = [
                name.strip() for name in group_search.split(",") if name.strip()
            ]
            if group_names:
                from functools import reduce
                import operator
                from django.db.models import Q

                q_objects = [Q(groups__name__icontains=name) for name in group_names]
                queryset = queryset.filter(reduce(operator.or_, q_objects)).distinct()

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context["search"] = self.request.GET.get("search", "")
        context["group_search"] = self.request.GET.get("group_search", "")

        node_sync_paths = {}
        node_sync_summary = {}
        node_groups = {}
        for node in context["nodes"]:
            setting = get_or_create_sync_setting(node)
            node_sync_paths[node.id] = setting.main_conf_path
            node_groups[node.id] = list(node.groups.all())

            configs = node.config_set.all()
            total = len(configs)
            if total > 0:
                success_count = sum(1 for c in configs if c.sync_status == "success")
                failed_count = sum(1 for c in configs if c.sync_status == "failed")
                syncing_count = sum(1 for c in configs if c.sync_status == "syncing")
                last_sync = max(
                    (c.last_sync_time for c in configs if c.last_sync_time),
                    default=None,
                )
                node_sync_summary[node.id] = {
                    "total": total,
                    "success": success_count,
                    "failed": failed_count,
                    "syncing": syncing_count,
                    "last_sync": last_sync,
                    "fallback_time": max(
                        (c.updated_at for c in configs),
                        default=None,
                    ),
                }
            else:
                node_sync_summary[node.id] = None

        context["node_sync_paths"] = node_sync_paths
        context["node_sync_summary"] = node_sync_summary
        context["node_groups"] = node_groups
        return context


class ConfigSyncRemoteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_resource = "configs"
    permission_action = "update"

    def post(self, request, node_id):
        from apps.nodes.models import Node
        from apps.nodes.views import _get_node_credential
        from utils.ssh import discover_nginx_configs

        node = get_object_or_404(Node, id=node_id)
        credential = _get_node_credential(node)

        if not credential:
            messages.error(request, f"节点 {node.hostname} 未配置 SSH 凭证，请先配置")
            return redirect("configs:sync_wizard")

        nginx_conf_path = request.POST.get("main_conf_path", "").strip()
        if not nginx_conf_path:
            messages.error(request, "请输入 nginx.conf 主配置文件路径")
            return redirect("configs:sync_wizard")

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

        created, updated, skipped = sync_discovered_configs(
            node, discovered, request.user, remark="从远程节点全量同步"
        )

        if discovered:
            save_sync_path(node, nginx_conf_path, request.user)

        if created or updated:
            parts = []
            if created:
                parts.append(f"新增 {len(created)} 个")
            if updated:
                parts.append(f"更新 {len(updated)} 个")
            msg = f"节点 {node.hostname} 同步完成：{'，'.join(parts)}"
            if skipped:
                msg += f"，{len(skipped)} 个未变化"
            if errors:
                msg += f"，失败 {len(errors)} 个"
            messages.success(request, msg)
        else:
            if errors:
                error_msg = f"节点 {node.hostname} 同步失败：{'; '.join(errors[:5])}"
                messages.warning(request, error_msg)
                url = reverse("configs:sync_wizard")
                return redirect(f"{url}?sync_error={quote(error_msg)}")
            else:
                messages.info(request, f"节点 {node.hostname} 未发现新的配置文件")

        return redirect("configs:sync_wizard")


class ConfigSyncPartialView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_resource = "configs"
    permission_action = "update"

    def post(self, request, node_id):
        from apps.nodes.models import Node
        from apps.nodes.views import _get_node_credential
        from utils.ssh import discover_nginx_configs

        node = get_object_or_404(Node, id=node_id)
        credential = _get_node_credential(node)
        if not credential:
            messages.error(request, f"节点 {node.hostname} 未配置 SSH 凭证，请先配置")
            return redirect("configs:sync_wizard")

        nginx_conf_path = request.POST.get("main_conf_path", "").strip()
        if not nginx_conf_path:
            messages.error(request, "请输入 nginx.conf 主配置文件路径")
            return redirect("configs:sync_wizard")
        selected_paths = request.POST.getlist("selected_paths")
        if not selected_paths:
            messages.error(request, "请至少选择一个配置文件")
            return redirect("configs:sync_wizard")

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

        created, updated, skipped = sync_selected_configs(
            node,
            selected_paths,
            discovered,
            request.user,
            remark="从远程节点部分同步",
        )

        if discovered:
            save_sync_path(node, nginx_conf_path, request.user)

        if created or updated:
            parts = []
            if created:
                parts.append(f"新增 {len(created)} 个")
            if updated:
                parts.append(f"更新 {len(updated)} 个")
            msg = f"节点 {node.hostname} 部分同步完成：{'，'.join(parts)}"
            if skipped:
                msg += f"，{len(skipped)} 个未变化"
            if errors:
                msg += f"，失败 {len(errors)} 个"
            messages.success(request, msg)
        else:
            if errors:
                error_msg = (
                    f"节点 {node.hostname} 部分同步失败：{'; '.join(errors[:5])}"
                )
                messages.warning(request, error_msg)
                url = reverse("configs:sync_wizard")
                return redirect(f"{url}?sync_error={quote(error_msg)}")
            else:
                messages.info(request, f"节点 {node.hostname} 所选配置均未变化")

        return redirect("configs:sync_wizard")


class ConfigSyncBatchView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_resource = "configs"
    permission_action = "update"

    def post(self, request):
        from apps.nodes.models import Node
        from apps.nodes.views import _get_node_credential
        from utils.ssh import discover_nginx_configs

        node_ids = request.POST.getlist("node_ids")
        if not node_ids:
            messages.error(request, "请至少选择一个节点")
            return redirect("configs:sync_wizard")

        if len(node_ids) > 3:
            messages.error(request, "最多只能选择 3 个节点进行批量同步")
            return redirect("configs:sync_wizard")

        total_created = 0
        total_updated = 0
        total_errors = []
        skip_nodes = []

        for node_id in node_ids:
            node = get_object_or_404(Node, id=node_id)
            credential = _get_node_credential(node)

            if not credential:
                skip_nodes.append(node.hostname)
                continue

            setting = get_or_create_sync_setting(node)
            nginx_conf_path = setting.main_conf_path
            if not nginx_conf_path:
                skip_nodes.append(f"{node.hostname}(未配置路径)")
                continue

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

            total_errors.extend(errors)

            created, updated, _ = sync_discovered_configs(
                node, discovered, request.user, remark="批量节点全量同步"
            )
            total_created += len(created)
            total_updated += len(updated)

            if discovered:
                save_sync_path(node, nginx_conf_path, request.user)

        parts = [f"{len(node_ids)} 个节点"]
        if total_created:
            parts.append(f"新增 {total_created} 个配置")
        if total_updated:
            parts.append(f"更新 {total_updated} 个配置")
        msg = f"批量同步完成：{'，'.join(parts)}"
        if skip_nodes:
            msg += (
                f"，跳过 {len(skip_nodes)} 个节点（{', '.join(skip_nodes[:5])} 无凭证）"
            )
        if total_errors:
            msg += f"，{len(total_errors)} 个错误"
        messages.success(request, msg)

        if total_errors and not total_created and not total_updated:
            error_msg = f"批量同步失败：{'; '.join(total_errors[:5])}"
            url = reverse("configs:sync_wizard")
            return redirect(f"{url}?sync_error={quote(error_msg)}")

        return redirect("configs:sync_wizard")


class ConfigUpdateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_resource = "configs"
    permission_action = "update"

    def post(self, request, pk):
        from apps.nodes.views import _get_node_credential
        from utils.ssh import read_remote_file

        config = get_object_or_404(Config, pk=pk)
        node = config.node
        credential = _get_node_credential(node)

        if not credential:
            messages.error(request, f"节点 {node.hostname} 未配置 SSH 凭证，无法更新")
            return redirect("configs:list")

        if credential.auth_type == "password":
            success, content = read_remote_file(
                node.ip,
                node.port,
                credential.username,
                password=credential.get_password(),
                file_path=config.file_path,
            )
        else:
            success, content = read_remote_file(
                node.ip,
                node.port,
                credential.username,
                private_key=credential.get_private_key(),
                file_path=config.file_path,
            )

        if not success:
            messages.error(request, f"读取远程文件失败：{config.file_path} — {content}")
            return redirect("configs:list")

        if config.content == content:
            messages.info(
                request, f"配置 {config.name} ({node.hostname}) 内容未变化，无需更新"
            )
            return redirect("configs:list")

        new_version = config.current_version + 1
        config.content = content
        config.current_version = new_version
        config.save()

        ConfigVersion.objects.create(
            config=config,
            version=new_version,
            content=content,
            remark="从远程节点更新",
            created_by=request.user,
        )

        config.prune_old_versions()

        messages.success(
            request,
            f"配置 {config.name} ({node.hostname}) 更新成功 → v{new_version}",
        )
        return redirect("configs:list")


class ConfigGlobPreviewView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_resource = "configs"
    permission_action = "create"

    def post(self, request):
        from apps.nodes.models import Node
        from apps.nodes.views import _get_node_credential
        from utils.ssh import expand_remote_glob

        node_id = request.POST.get("node_id")
        pattern = request.POST.get("pattern", "").strip()

        if not node_id or not pattern:
            return JsonResponse({"success": False, "message": "参数不完整"}, status=400)

        node = get_object_or_404(Node, id=node_id)
        credential = _get_node_credential(node)

        if not credential:
            return JsonResponse(
                {"success": False, "message": "节点未配置SSH凭证"}, status=400
            )

        if credential.auth_type == "password":
            files = expand_remote_glob(
                node.ip,
                node.port,
                credential.username,
                password=credential.get_password(),
                pattern=pattern,
            )
        else:
            files = expand_remote_glob(
                node.ip,
                node.port,
                credential.username,
                private_key=credential.get_private_key(),
                pattern=pattern,
            )

        return JsonResponse({"success": True, "files": files, "count": len(files)})


class ConfigCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    model = Config
    form_class = ConfigForm
    template_name = "configs/create.html"
    success_url = reverse_lazy("configs:list")
    permission_resource = "configs"
    permission_action = "create"

    def get_form_kwargs(self):
        from apps.nodes.models import Node

        kwargs = super().get_form_kwargs()
        self.node_id = self.request.GET.get("node_id")
        if self.node_id:
            self.node = get_object_or_404(Node, id=self.node_id)
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from apps.nodes.models import Node

        context["all_nodes"] = (
            Node.objects.all().select_related("created_by").order_by("hostname")
        )
        context["selected_node_id"] = self.request.GET.get("node_id", "")
        return context

    def form_valid(self, form):
        request = self.request
        use_glob = request.POST.get("use_glob") == "1"
        glob_pattern = request.POST.get("glob_pattern", "").strip()
        batch_remark = request.POST.get("batch_remark", "").strip()

        if use_glob and glob_pattern:
            return self._handle_glob_creation(form, glob_pattern, batch_remark)

        if not form.cleaned_data.get("name") or not form.cleaned_data.get("file_path"):
            form.add_error("name", "配置名称和文件路径为必填项")
            return self.form_invalid(form)

        node_id = request.POST.get("node")
        if node_id:
            form.instance.node_id = node_id

        form.instance.created_by = request.user
        form.instance.current_version = 1
        remark = form.cleaned_data.get("remark", "")

        response = super().form_valid(form)

        ConfigVersion.objects.create(
            config=self.object,
            version=1,
            content=self.object.content,
            remark=remark or "手动创建",
            created_by=request.user,
        )

        messages.success(
            request,
            f"配置 {self.object.name} 创建成功（v1）",
        )
        return response

    def _handle_glob_creation(self, form, glob_pattern, batch_remark):
        from apps.nodes.models import Node
        from apps.nodes.views import _get_node_credential
        from utils.ssh import expand_remote_glob, read_remote_file

        node_id = self.request.POST.get("node")
        selected_files = self.request.POST.getlist("selected_files")

        if not node_id:
            form.add_error(None, "请选择关联节点")
            return self.form_invalid(form)

        node = get_object_or_404(Node, id=node_id)
        credential = _get_node_credential(node)

        if not credential:
            form.add_error(None, f"节点 {node.hostname} 未配置 SSH 凭证")
            return self.form_invalid(form)

        if not selected_files:
            if credential.auth_type == "password":
                matched = expand_remote_glob(
                    node.ip,
                    node.port,
                    credential.username,
                    password=credential.get_password(),
                    pattern=glob_pattern,
                )
            else:
                matched = expand_remote_glob(
                    node.ip,
                    node.port,
                    credential.username,
                    private_key=credential.get_private_key(),
                    pattern=glob_pattern,
                )
            if not matched:
                form.add_error(None, f"未匹配到任何文件：{glob_pattern}")
                return self.form_invalid(form)
            selected_files = matched

        created = []
        failed = []
        for file_path in selected_files:
            file_path = file_path.strip()
            if not file_path:
                continue
            if credential.auth_type == "password":
                success, content = read_remote_file(
                    node.ip,
                    node.port,
                    credential.username,
                    password=credential.get_password(),
                    file_path=file_path,
                )
            else:
                success, content = read_remote_file(
                    node.ip,
                    node.port,
                    credential.username,
                    private_key=credential.get_private_key(),
                    file_path=file_path,
                )
            if not success:
                failed.append((file_path, content))
                continue

            config_name = file_path.split("/")[-1]
            config = Config(
                node=node,
                name=config_name,
                file_path=file_path,
                content=content,
                current_version=1,
                created_by=self.request.user,
            )
            config.save()

            ConfigVersion.objects.create(
                config=config,
                version=1,
                content=content,
                remark=batch_remark or "批量导入",
                created_by=self.request.user,
            )
            created.append(config_name)

        if created:
            messages.success(
                self.request,
                f"批量导入完成：{len(created)} 个文件（{', '.join(created[:5])}"
                + (f" 等" if len(created) > 5 else "")
                + "）",
            )
        if failed:
            messages.warning(
                self.request,
                f"{len(failed)} 个文件读取失败："
                + "; ".join(f"{f[0]}: {f[1][:50]}" for f in failed[:3]),
            )
        if not created and not failed:
            form.add_error(None, "没有成功创建任何配置")
            return self.form_invalid(form)

        return redirect(self.success_url)

    def form_invalid(self, form):
        messages.error(self.request, "配置创建失败，请检查输入")
        return super().form_invalid(form)


class ConfigByNodesAPIView(LoginRequiredMixin, View):
    def get(self, request):
        node_ids_str = request.GET.get("node_ids", "")
        if not node_ids_str:
            return JsonResponse({"success": False, "error": "缺少 node_ids 参数"})

        try:
            node_ids = [int(x.strip()) for x in node_ids_str.split(",") if x.strip()]
        except (ValueError, TypeError):
            return JsonResponse({"success": False, "error": "node_ids 格式错误"})

        if not node_ids:
            return JsonResponse({"success": False, "error": "node_ids 为空"})

        configs = (
            Config.objects.filter(node_id__in=node_ids)
            .select_related("node")
            .prefetch_related("versions")
            .order_by("node__hostname", "name")
        )

        data = []
        for cfg in configs:
            versions = cfg.versions.order_by("-version")[:10]
            data.append(
                {
                    "id": cfg.id,
                    "node_id": cfg.node_id,
                    "node_name": cfg.node.hostname,
                    "name": cfg.name,
                    "file_path": cfg.file_path,
                    "current_version": cfg.current_version,
                    "versions": [
                        {
                            "id": v.id,
                            "version": v.version,
                            "created_at": v.created_at.strftime("%Y-%m-%d %H:%M"),
                        }
                        for v in versions
                    ],
                }
            )

        return JsonResponse({"success": True, "data": data})


class ConfigVersionContentAPIView(LoginRequiredMixin, View):
    def get(self, request, version_id):
        version = get_object_or_404(
            ConfigVersion.objects.select_related("config", "config__node"),
            pk=version_id,
        )

        return JsonResponse(
            {
                "success": True,
                "data": {
                    "id": version.id,
                    "version": version.version,
                    "config_name": version.config.name,
                    "file_path": version.config.file_path,
                    "node_name": version.config.node.hostname,
                    "content": version.content,
                    "created_at": version.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                },
            }
        )


class ConfigSyncBatchAPIView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_resource = "configs"
    permission_action = "update"

    def post(self, request):
        from apps.nodes.models import Node
        from apps.nodes.views import _get_node_credential
        from utils.ssh import discover_nginx_configs
        from django.utils import timezone

        data = json.loads(request.body)
        node_ids = data.get("node_ids", [])
        task_id = data.get("task_id", str(uuid.uuid4()))

        if not node_ids:
            return JsonResponse({"success": False, "message": "请至少选择一个节点"})

        if len(node_ids) > 3:
            return JsonResponse({"success": False, "message": "最多只能选择 3 个节点"})

        progress = {
            "task_id": task_id,
            "total": len(node_ids),
            "completed": 0,
            "nodes": {},
        }

        for node_id in node_ids:
            progress["nodes"][str(node_id)] = {
                "status": "waiting",
                "hostname": "",
                "created": 0,
                "updated": 0,
                "error": "",
                "time": 0,
            }

        cache.set(f"batch_sync:{task_id}", progress, timeout=300)

        total_created = 0
        total_updated = 0
        total_errors = []

        for node_id in node_ids:
            node = get_object_or_404(Node, id=node_id)
            progress["nodes"][str(node_id)]["hostname"] = node.hostname
            progress["nodes"][str(node_id)]["status"] = "syncing"
            cache.set(f"batch_sync:{task_id}", progress, timeout=300)

            start_time = timezone.now()
            credential = _get_node_credential(node)

            if not credential:
                progress["nodes"][str(node_id)]["status"] = "failed"
                progress["nodes"][str(node_id)]["error"] = "未配置SSH凭证"
                progress["completed"] += 1
                cache.set(f"batch_sync:{task_id}", progress, timeout=300)
                total_errors.append(f"{node.hostname}: 未配置SSH凭证")
                continue

            setting = get_or_create_sync_setting(node)
            nginx_conf_path = setting.main_conf_path
            if not nginx_conf_path:
                progress["nodes"][str(node_id)]["status"] = "failed"
                progress["nodes"][str(node_id)]["error"] = "未配置nginx路径"
                progress["completed"] += 1
                cache.set(f"batch_sync:{task_id}", progress, timeout=300)
                total_errors.append(f"{node.hostname}: 未配置nginx路径")
                continue

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
                progress["nodes"][str(node_id)]["error"] = "; ".join(errors[:3])

            if discovered:
                created, updated, _ = sync_discovered_configs(
                    node, discovered, request.user, remark="批量节点全量同步"
                )
                save_sync_path(node, nginx_conf_path, request.user)
                files_detail = _build_files_detail(discovered, errors, created, updated)
                progress["nodes"][str(node_id)]["created"] = len(created)
                progress["nodes"][str(node_id)]["updated"] = len(updated)
                progress["nodes"][str(node_id)]["files"] = files_detail
                total_created += len(created)
                total_updated += len(updated)
            else:
                progress["nodes"][str(node_id)]["error"] = (
                    progress["nodes"][str(node_id)]["error"]
                    or f"未发现配置文件（路径: {nginx_conf_path}）"
                )

            elapsed = (timezone.now() - start_time).total_seconds()
            progress["nodes"][str(node_id)]["time"] = round(elapsed, 1)

            if progress["nodes"][str(node_id)]["error"] and not discovered:
                progress["nodes"][str(node_id)]["status"] = "failed"
                total_errors.append(
                    f"{node.hostname}: {progress['nodes'][str(node_id)]['error']}"
                )
            else:
                progress["nodes"][str(node_id)]["status"] = "success"

            progress["completed"] += 1
            cache.set(f"batch_sync:{task_id}", progress, timeout=300)

        return JsonResponse(
            {
                "success": True,
                "task_id": task_id,
                "total_created": total_created,
                "total_updated": total_updated,
                "total_errors": len(total_errors),
                "summary": {
                    "created": total_created,
                    "updated": total_updated,
                    "errors": total_errors[:5],
                },
            }
        )


def _build_files_detail(discovered, errors, created, updated):
    import re

    files_detail = []
    created_set = set(created)
    updated_set = set(updated)

    for item in discovered:
        file_status = "skipped"
        if item["name"] in created_set:
            file_status = "created"
        elif item["name"] in updated_set:
            file_status = "updated"
        files_detail.append(
            {
                "path": item["path"],
                "name": item["name"],
                "status": file_status,
                "error": "",
            }
        )

    for err in errors:
        m = re.match(r"读取\s+(.+?)\s+失败", err)
        if m:
            fail_path = m.group(1)
            files_detail.append(
                {
                    "path": fail_path,
                    "name": fail_path.split("/")[-1],
                    "status": "failed",
                    "error": err,
                }
            )

    return files_detail


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
        task_id = data.get("task_id", str(uuid.uuid4()))

        if not node_id:
            return JsonResponse({"success": False, "message": "缺少节点ID"})
        if not main_conf_path:
            return JsonResponse(
                {"success": False, "message": "请输入 nginx.conf 主配置文件路径"}
            )

        node = get_object_or_404(Node, id=node_id)

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
                    "error": "",
                    "time": 0,
                }
            },
        }
        cache.set(f"batch_sync:{task_id}", progress, timeout=300)

        start_time = timezone.now()
        progress["nodes"][str(node_id)]["status"] = "syncing"
        cache.set(f"batch_sync:{task_id}", progress, timeout=300)

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

        created, updated, skipped = sync_discovered_configs(
            node, discovered, request.user, remark="从远程节点全量同步"
        )

        if discovered:
            save_sync_path(node, main_conf_path, request.user)

        files_detail = _build_files_detail(discovered, errors, created, updated)

        progress["nodes"][str(node_id)]["created"] = len(created)
        progress["nodes"][str(node_id)]["updated"] = len(updated)
        progress["nodes"][str(node_id)]["skipped"] = len(skipped)
        progress["nodes"][str(node_id)]["files"] = files_detail

        if errors:
            progress["nodes"][str(node_id)]["error"] = "; ".join(errors[:5])

        elapsed = (timezone.now() - start_time).total_seconds()
        progress["nodes"][str(node_id)]["time"] = round(elapsed, 1)

        if errors and not discovered:
            progress["nodes"][str(node_id)]["status"] = "failed"
        else:
            progress["nodes"][str(node_id)]["status"] = "success"

        progress["completed"] = 1
        cache.set(f"batch_sync:{task_id}", progress, timeout=300)

        return JsonResponse(
            {
                "success": True,
                "task_id": task_id,
                "summary": {
                    "created": len(created),
                    "updated": len(updated),
                    "skipped": len(skipped),
                    "errors": errors[:5],
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
