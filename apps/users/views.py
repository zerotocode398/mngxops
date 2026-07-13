from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import User
from django.contrib import messages
from django.shortcuts import redirect, get_object_or_404
from django.urls import reverse_lazy
from django.views.generic import ListView, CreateView, UpdateView, DeleteView, View

from .forms import UserCreateForm, UserUpdateForm, UserGroupForm, UserTeamForm
from .models import UserProfile, UserGroup, UserTeam, PermissionItem
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
    """Build resource → (resource_label, permission_item_ids) mapping for the template.
    Returns list of (resource_key, resource_label, [permission_id_str, ...]).
    The ID strings are used to match against choice.data.value in templates.
    """
    perm_map = {}
    for perm in PermissionItem.objects.all():
        rk = perm.resource
        if rk not in perm_map:
            perm_map[rk] = []
        perm_map[rk].append(str(perm.id))

    result = []
    for rk, rl in RESOURCE_CHOICES:
        if rk in perm_map:
            result.append((rk, rl, perm_map[rk]))
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


# ========== 用户组视图（UserTeam）==========


from django.db.models import Q
from django.http import JsonResponse
from django.core.paginator import Paginator


def _search_users(search_tags):
    """根据标签列表搜索用户，返回 QuerySet。支持用户名和邮箱，且关系。"""
    if not search_tags:
        return User.objects.all()
    queryset = User.objects.all()
    for tag in search_tags:
        queryset = queryset.filter(
            Q(username__icontains=tag) | Q(email__icontains=tag)
        )
    return queryset


class UserTeamListView(
    LoginRequiredMixin,
    PermissionRequiredMixin,
    PerPagePaginationMixin,
    ListView,
):
    model = UserTeam
    template_name = "users/team_list.html"
    context_object_name = "teams"
    paginate_by = 10
    ordering = ["-created_at"]
    permission_resource = "teams"
    permission_action = "read"

    def get_queryset(self):
        queryset = super().get_queryset()
        search = self.request.GET.get("search", "")
        if search:
            terms = [
                t.strip() for t in search.replace("，", ",").split(",") if t.strip()
            ]
            for term in terms:
                queryset = queryset.filter(name__icontains=term)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["search"] = self.request.GET.get("search", "")
        return context


class UserTeamCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    model = UserTeam
    form_class = UserTeamForm
    template_name = "users/team_create.html"
    success_url = reverse_lazy("users:team_list")
    permission_resource = "teams"
    permission_action = "create"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["available_roles"] = UserGroup.objects.all()
        return context

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        response = super().form_valid(form)
        form.instance.roles.set(form.cleaned_data.get("roles", []))
        messages.success(self.request, f"用户组 {form.instance.name} 创建成功")
        return response

    def form_invalid(self, form):
        messages.error(self.request, "用户组创建失败，请检查输入")
        return super().form_invalid(form)


class UserTeamUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = UserTeam
    form_class = UserTeamForm
    template_name = "users/team_edit.html"
    success_url = reverse_lazy("users:team_list")
    permission_resource = "teams"
    permission_action = "update"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["available_roles"] = UserGroup.objects.all()
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        form.instance.roles.set(form.cleaned_data.get("roles", []))
        messages.success(self.request, f"用户组 {form.instance.name} 更新成功")
        return response

    def form_invalid(self, form):
        messages.error(self.request, "用户组更新失败，请检查输入")
        return super().form_invalid(form)


class UserTeamDeleteView(LoginRequiredMixin, PermissionRequiredMixin, DeleteView):
    model = UserTeam
    template_name = "users/team_delete.html"
    success_url = reverse_lazy("users:team_list")
    permission_resource = "teams"
    permission_action = "delete"

    def post(self, request, *args, **kwargs):
        team = self.get_object()
        messages.success(request, f"用户组 {team.name} 删除成功")
        return super().post(request, *args, **kwargs)


class UserTeamMemberListView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """返回用户组的成员列表（JSON），供弹窗滚动加载。"""
    permission_resource = "teams"
    permission_action = "read"

    def get(self, request, pk):
        team = get_object_or_404(UserTeam, pk=pk)
        member_ids = set(team.members.values_list("id", flat=True))
        page = int(request.GET.get("page", 1))
        search_raw = request.GET.get("search", "")
        search_tags = [
            t.strip() for t in search_raw.replace("，", ",").split(",") if t.strip()
        ]
        queryset = _search_users(search_tags).order_by("username")
        paginator = Paginator(queryset, 10)
        page_obj = paginator.get_page(page)

        users_data = []
        for u in page_obj:
            users_data.append({
                "id": u.id,
                "username": u.username,
                "first_name": u.first_name or "-",
                "email": u.email or "-",
                "is_active": u.is_active,
                "is_superuser": u.is_superuser,
                "is_member": u.id in member_ids,
                "role_count": u.profile.groups.count() if hasattr(u, "profile") else 0,
            })

        return JsonResponse({
            "users": users_data,
            "page": page_obj.number,
            "total_pages": paginator.num_pages,
            "has_next": page_obj.has_next(),
            "has_previous": page_obj.has_previous(),
            "total_count": paginator.count,
        })


class UserTeamManageMembersView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """管理用户组成员 — 添加/移除成员。"""
    permission_resource = "teams"
    permission_action = "update"

    def post(self, request, pk):
        team = get_object_or_404(UserTeam, pk=pk)
        action = request.POST.get("action")

        if action == "add":
            user_ids = [int(uid) for uid in request.POST.getlist("user_ids", [])]
            added = 0
            for uid in user_ids:
                user = get_object_or_404(User, pk=uid)
                if team.members.filter(pk=uid).exists():
                    continue
                team.members.add(user)
                added += 1
            if added:
                messages.success(request, f"已向 {team.name} 添加 {added} 个成员")
            else:
                messages.info(request, "未添加新成员")

        elif action == "remove":
            user_ids = [int(uid) for uid in request.POST.getlist("user_ids", [])]
            removed = 0
            for uid in user_ids:
                user = get_object_or_404(User, pk=uid)
                if not team.members.filter(pk=uid).exists():
                    continue
                team.members.remove(user)
                removed += 1
            if removed:
                messages.success(request, f"已从 {team.name} 移除 {removed} 个成员")
            else:
                messages.info(request, "未移除任何成员")

        return redirect("users:team_list")
