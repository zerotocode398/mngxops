from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib import messages
from django.shortcuts import render, redirect
from django.urls import reverse_lazy
from django.views import View

from apps.audit.models import LoginLog, AuditLog
from .forms import LoginForm, CustomPasswordChangeForm


def _get_client_ip(request):
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        ip = x_forwarded_for.split(",")[0].strip()
    else:
        ip = request.META.get("REMOTE_ADDR", "0.0.0.0")
    return ip


def _get_user_agent(request):
    return request.META.get("HTTP_USER_AGENT", "")


class LoginView(View):
    template_name = "accounts/login.html"

    def get(self, request):
        if request.user.is_authenticated:
            return redirect("dashboard:index")
        form = LoginForm()
        return render(request, self.template_name, {"form": form})

    def post(self, request):
        form = LoginForm(request, data=request.POST)
        username = request.POST.get("username", "")
        ip = _get_client_ip(request)
        user_agent = _get_user_agent(request)

        if form.is_valid():
            username = form.cleaned_data.get("username")
            password = form.cleaned_data.get("password")
            user = authenticate(request, username=username, password=password)
            if user is not None:
                login(request, user)

                LoginLog.objects.create(
                    username=username,
                    ip=ip,
                    user_agent=user_agent,
                    status="success",
                )

                AuditLog.objects.create(
                    user=user,
                    module="登录管理",
                    action="登录系统",
                    ip=ip,
                    result="success",
                    detail=f"用户 {username} 登录成功",
                )

                messages.success(request, "登录成功")
                next_url = request.GET.get("next", "dashboard:index")
                return redirect(next_url)
            else:
                LoginLog.objects.create(
                    username=username,
                    ip=ip,
                    user_agent=user_agent,
                    status="failed",
                )
                messages.error(request, "用户名或密码错误")
        else:
            LoginLog.objects.create(
                username=username,
                ip=ip,
                user_agent=user_agent,
                status="failed",
            )
            messages.error(request, "登录失败，请检查用户名或密码")
        return render(request, self.template_name, {"form": form})


class LogoutView(View):
    def get(self, request):
        if request.user.is_authenticated:
            ip = _get_client_ip(request)
            AuditLog.objects.create(
                user=request.user,
                module="登录管理",
                action="登出系统",
                ip=ip,
                result="success",
                detail=f"用户 {request.user.username} 登出成功",
            )

        logout(request)
        messages.success(request, "登出成功")
        return redirect("accounts:login")


class ProfileView(LoginRequiredMixin, View):
    template_name = "accounts/profile.html"

    def get(self, request):
        return render(request, self.template_name, {"user": request.user})


class PasswordChangeView(LoginRequiredMixin, View):
    template_name = "accounts/password_change.html"
    success_url = reverse_lazy("accounts:profile")

    def get(self, request):
        form = CustomPasswordChangeForm(request.user)
        return render(request, self.template_name, {"form": form})

    def post(self, request):
        form = CustomPasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)

            ip = _get_client_ip(request)
            AuditLog.objects.create(
                user=request.user,
                module="用户管理",
                action="修改密码",
                ip=ip,
                result="success",
                detail=f"用户 {request.user.username} 修改密码成功",
            )

            messages.success(request, "密码修改成功")
            return redirect(self.success_url)
        else:
            messages.error(request, "密码修改失败，请检查输入")
        return render(request, self.template_name, {"form": form})
