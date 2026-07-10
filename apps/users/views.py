from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import User
from django.contrib import messages
from django.shortcuts import redirect, get_object_or_404
from django.urls import reverse_lazy
from django.views.generic import ListView, CreateView, UpdateView, DeleteView, View

from .forms import UserCreateForm, UserUpdateForm, UserGroupForm
from .models import UserProfile, UserGroup
from utils.pagination import PerPagePaginationMixin
from .permissions import PermissionRequiredMixin


class UserListView(
    LoginRequiredMixin,
    PermissionRequiredMixin,
    PerPagePaginationMixin,
    ListView,
):
    model = User
    template_name = "users/list.html"
    context_object_name = "users"
    paginate_by = 10
    ordering = ["-date_joined"]
    permission_resource = "users"
    permission_action = "read"

    def get_queryset(self):
        queryset = super().get_queryset()
        search = self.request.GET.get("search", "")
        if search:
            queryset = (
                queryset.filter(username__icontains=search)
                | queryset.filter(email__icontains=search)
                | queryset.filter(first_name__icontains=search)
            )
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["search"] = self.request.GET.get("search", "")
        return context


class UserCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    model = User
    form_class = UserCreateForm
    template_name = "users/create.html"
    success_url = reverse_lazy("users:list")
    permission_resource = "users"
    permission_action = "create"

    def form_valid(self, form):
        messages.success(self.request, f"用户 {form.instance.username} 创建成功")
        return super().form_valid(form)

    def form_invalid(self, form):
        messages.error(self.request, "用户创建失败，请检查输入")
        return super().form_invalid(form)


class UserUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = User
    form_class = UserUpdateForm
    template_name = "users/edit.html"
    success_url = reverse_lazy("users:list")
    slug_field = "username"
    slug_url_kwarg = "username"
    permission_resource = "users"
    permission_action = "update"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["permission_groups"] = _build_permission_groups()
        return context

    def form_valid(self, form):
        messages.success(self.request, f"用户 {form.instance.username} 更新成功")
        return super().form_valid(form)

    def form_invalid(self, form):
        messages.error(self.request, "用户更新失败，请检查输入")
        return super().form_invalid(form)


class UserDeleteView(LoginRequiredMixin, PermissionRequiredMixin, DeleteView):
    model = User
    template_name = "users/delete.html"
    success_url = reverse_lazy("users:list")
    slug_field = "username"
    slug_url_kwarg = "username"
    permission_resource = "users"
    permission_action = "delete"

    def post(self, request, *args, **kwargs):
        user = self.get_object()
        if user == request.user:
            messages.error(request, "不能删除当前登录用户")
            return redirect("users:list")
        messages.success(request, f"用户 {user.username} 删除成功")
        return super().post(request, *args, **kwargs)


class UserLockToggleView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_resource = "users"
    permission_action = "update"

    def post(self, request, username):
        user = get_object_or_404(User, username=username)
        if user == request.user:
            messages.error(request, "不能锁定当前登录用户")
            return redirect("users:list")
        if user.is_superuser and not request.user.is_superuser:
            messages.error(request, "无权操作超级管理员")
            return redirect("users:list")
        user.is_active = not user.is_active
        user.save()
        if user.is_active:
            messages.success(request, f"用户 {user.username} 已解锁")
        else:
            messages.success(request, f"用户 {user.username} 已锁定")
        return redirect("users:list")


# ========== 角色视图（仅 Admin）==========


from .perm_defs import RESOURCE_CHOICES, all_permission_items


def _build_permission_groups():
    """Build resource → permission_code mapping for the template."""
    from .perm_defs import all_permission_items

    groups = {}
    for item in all_permission_items():
        rk = item["resource"]
        if rk not in groups:
            groups[rk] = []
        groups[rk].append(item["code"])

    result = []
    for rk, rl in RESOURCE_CHOICES:
        if rk in groups:
            result.append((rk, rl, groups[rk]))
    return result


class UserGroupListView(
    LoginRequiredMixin,
    PermissionRequiredMixin,
    PerPagePaginationMixin,
    ListView,
):
    model = UserGroup
    template_name = "users/group_list.html"
    context_object_name = "user_groups"
    paginate_by = 10
    ordering = ["-created_at"]
    permission_resource = "roles"
    permission_action = "read"

    def get_queryset(self):
        queryset = super().get_queryset()
        search = self.request.GET.get("search", "")
        if search:
            queryset = queryset.filter(name__icontains=search)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["search"] = self.request.GET.get("search", "")
        all_users = list(User.objects.all())
        for u in all_users:
            UserProfile.objects.get_or_create(user=u)
        context["all_users"] = all_users
        return context


class UserGroupCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    model = UserGroup
    form_class = UserGroupForm
    template_name = "users/group_create.html"
    success_url = reverse_lazy("users:role_list")
    permission_resource = "roles"
    permission_action = "create"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["permission_groups"] = _build_permission_groups()
        return context

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        messages.success(self.request, f"角色 {form.instance.name} 创建成功")
        return super().form_valid(form)

    def form_invalid(self, form):
        messages.error(self.request, "角色创建失败，请检查输入")
        return super().form_invalid(form)


class UserGroupUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = UserGroup
    form_class = UserGroupForm
    template_name = "users/group_edit.html"
    success_url = reverse_lazy("users:role_list")
    permission_resource = "roles"
    permission_action = "update"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["permission_groups"] = _build_permission_groups()
        return context

    def form_valid(self, form):
        messages.success(self.request, f"角色 {form.instance.name} 更新成功")
        return super().form_valid(form)

    def form_invalid(self, form):
        messages.error(self.request, "角色更新失败，请检查输入")
        return super().form_invalid(form)


class UserGroupDeleteView(LoginRequiredMixin, PermissionRequiredMixin, DeleteView):
    model = UserGroup
    template_name = "users/group_delete.html"
    success_url = reverse_lazy("users:role_list")
    permission_resource = "roles"
    permission_action = "delete"

    def post(self, request, *args, **kwargs):
        user_group = self.get_object()
        messages.success(request, f"角色 {user_group.name} 删除成功")
        return super().post(request, *args, **kwargs)


class UserGroupManageUsersView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_resource = "roles"
    permission_action = "update"

    def post(self, request, pk):
        user_group = get_object_or_404(UserGroup, pk=pk)
        desired_ids = set(int(uid) for uid in request.POST.getlist("user_ids", []))
        current_ids = set(user_group.members.values_list("user_id", flat=True))

        to_add_ids = desired_ids - current_ids
        to_remove_ids = current_ids - desired_ids

        added = 0
        skipped = 0
        for user_id in to_add_ids:
            user = get_object_or_404(User, pk=user_id)
            profile, _ = UserProfile.objects.get_or_create(user=user)
            if profile.groups.count() >= 3:
                skipped += 1
                continue
            profile.groups.add(user_group)
            added += 1

        removed = 0
        for user_id in to_remove_ids:
            user = get_object_or_404(User, pk=user_id)
            profile = get_object_or_404(UserProfile, user=user)
            profile.groups.remove(user_group)
            removed += 1

        parts = []
        if added:
            parts.append(f"添加 {added} 个")
        if removed:
            parts.append(f"移除 {removed} 个")
        if skipped:
            parts.append(f"跳过 {skipped} 个（已达上限）")
        if parts:
            messages.success(request, f"角色 {user_group.name}：{', '.join(parts)}")
        else:
            messages.info(request, "角色成员未发生变化")
        return redirect("users:role_list")
