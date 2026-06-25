from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.http import JsonResponse
from django.views.generic import ListView, CreateView, UpdateView, DeleteView, View
from django.urls import reverse_lazy
from django.shortcuts import get_object_or_404

from .forms import CredentialForm
from .models import Credential
from apps.users.permissions import PermissionRequiredMixin
from utils.pagination import PerPagePaginationMixin


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
        queryset = super().get_queryset()
        search = self.request.GET.get("search", "")
        if search:
            queryset = queryset.filter(name__icontains=search) | queryset.filter(
                username__icontains=search
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
