from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.models import User
from django.contrib import messages
from django.contrib.sessions.models import Session
from django.shortcuts import render, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views import View

from apps.audit.models import LoginLog, AuditLog
from .forms import LoginForm, CustomPasswordChangeForm
from .login_lock import (
    clear_login_fail_lock,
    get_fail_lock_minutes,
    is_temp_login_locked,
    record_login_failure,
)


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
        # 多点登录冲突确认：用户点击了"确认踢下线并登录"
        if request.POST.get("confirm_kick") == "1":
            return self._handle_confirm_kick(request)

        form = LoginForm(request, data=request.POST)
        username = request.POST.get("username", "")
        ip = _get_client_ip(request)
        user_agent = _get_user_agent(request)

        # is_valid 会因 is_active=False 失败，须先拦截给出锁定提示
        target_user = None
        if username:
            try:
                target_user = User.objects.get(username=username)
            except User.DoesNotExist:
                target_user = None

        if target_user is not None and not target_user.is_active:
            LoginLog.objects.create(
                username=username,
                ip=ip,
                user_agent=user_agent,
                status="failed",
            )
            return render(
                request,
                self.template_name,
                {
                    "form": form,
                    "error_type": "user_disabled",
                    "error_message": "用户已锁定，请联系管理员",
                },
            )

        if target_user is not None and is_temp_login_locked(target_user):
            LoginLog.objects.create(
                username=username,
                ip=ip,
                user_agent=user_agent,
                status="failed",
            )
            minutes = get_fail_lock_minutes()
            return render(
                request,
                self.template_name,
                {
                    "form": form,
                    "error_type": "account_locked",
                    "error_message": f"登录失败次数过多，请 {minutes} 分钟后再试或联系管理员解锁。",
                },
            )

        if form.is_valid():
            username = form.cleaned_data.get("username")
            password = form.cleaned_data.get("password")
            user = authenticate(request, username=username, password=password)
            if user is not None:
                clear_login_fail_lock(user)
                return self._after_auth_success(request, user, ip, user_agent)

        if target_user is not None:
            record_login_failure(target_user)
            if is_temp_login_locked(target_user):
                LoginLog.objects.create(
                    username=username,
                    ip=ip,
                    user_agent=user_agent,
                    status="failed",
                )
                minutes = get_fail_lock_minutes()
                return render(
                    request,
                    self.template_name,
                    {
                        "form": form,
                        "error_type": "account_locked",
                        "error_message": f"登录失败次数过多，请 {minutes} 分钟后再试或联系管理员解锁。",
                    },
                )
        LoginLog.objects.create(
            username=username,
            ip=ip,
            user_agent=user_agent,
            status="failed",
        )
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "error_type": "auth_failed",
                "error_message": "用户名或密码错误",
            },
        )

    def _after_auth_success(self, request, user, ip, user_agent):
        """认证成功后的处理：检查多点登录冲突，无冲突则直接登录。"""
        conflict = self._detect_session_conflict(user)

        if conflict:
            # 暂存待登录用户 ID 到匿名 session，等待用户确认
            request.session["pending_login_user_id"] = user.id
            request.session["pending_login_ip"] = ip
            request.session["pending_login_agent"] = user_agent

            return render(
                request,
                self.template_name,
                {
                    "form": LoginForm(),
                    "show_conflict": True,
                    "conflict_ip": conflict["ip"],
                    "conflict_agent": conflict["agent"],
                },
            )

        # 无冲突，直接登录
        return self._complete_login(request, user, ip, user_agent)

    def _detect_session_conflict(self, user):
        """检测用户是否已在其他浏览器登录。

        Returns:
            None 表示无冲突；dict 包含旧会话的 IP 和 UA 信息。
        """
        try:
            profile = user.profile
        except Exception:
            return None

        old_key = profile.current_session_key
        if not old_key:
            return None

        # 检查旧 session 是否仍然有效（未过期）
        session_exists = Session.objects.filter(
            session_key=old_key,
            expire_date__gt=timezone.now(),
        ).exists()

        if not session_exists:
            return None

        return {
            "ip": profile.last_login_ip or "未知",
            "agent": self._format_agent(profile.last_login_agent),
        }

    def _handle_confirm_kick(self, request):
        """处理用户确认踢下线旧会话。"""
        user_id = request.session.get("pending_login_user_id")
        ip = request.session.get("pending_login_ip", "")
        user_agent = request.session.get("pending_login_agent", "")

        if not user_id:
            messages.error(request, "确认已过期，请重新登录")
            return redirect("accounts:login")

        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            messages.error(request, "用户不存在")
            return redirect("accounts:login")

        # 删除旧 session
        self._kick_old_session(user)

        # 清理 pending 标记
        request.session.pop("pending_login_user_id", None)
        request.session.pop("pending_login_ip", None)
        request.session.pop("pending_login_agent", None)

        return self._complete_login(request, user, ip, user_agent)

    def _kick_old_session(self, user):
        """删除用户之前的活跃 session。"""
        try:
            old_key = user.profile.current_session_key
        except Exception:
            return

        if old_key:
            Session.objects.filter(session_key=old_key).delete()

    def _complete_login(self, request, user, ip, user_agent):
        """完成登录：创建 session、记录日志、更新 profile。"""
        login(request, user)

        # 更新 profile 中的会话追踪字段
        try:
            profile = user.profile
            profile.current_session_key = request.session.session_key
            profile.last_login_ip = ip
            profile.last_login_agent = user_agent
            profile.save(
                update_fields=[
                    "current_session_key",
                    "last_login_ip",
                    "last_login_agent",
                    "updated_at",
                ]
            )
        except Exception:
            pass

        LoginLog.objects.create(
            username=user.username,
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
            detail=f"用户 {user.username} 登录成功",
        )

        messages.success(request, "登录成功")
        next_url = request.GET.get("next", "dashboard:index")
        return redirect(next_url)

    @staticmethod
    def _format_agent(agent):
        """从 User-Agent 中提取可读的浏览器/系统信息。"""
        if not agent:
            return "未知"

        browser = ""
        os_name = ""

        # 解析浏览器
        if "Edg/" in agent:
            browser = "Edge"
        elif "Firefox/" in agent:
            browser = "Firefox"
        elif "Chrome/" in agent:
            browser = "Chrome"
        elif "Safari/" in agent and "Chrome" not in agent:
            browser = "Safari"
        else:
            browser = "未知浏览器"

        # 解析操作系统
        if "Windows NT 10.0" in agent:
            os_name = "Windows 10"
        elif "Windows NT 6.3" in agent:
            os_name = "Windows 8.1"
        elif "Windows NT 6.1" in agent:
            os_name = "Windows 7"
        elif "Windows" in agent:
            os_name = "Windows"
        elif "Mac OS X" in agent or "macOS" in agent:
            os_name = "macOS"
        elif "Linux" in agent and "Android" not in agent:
            os_name = "Linux"
        elif "Android" in agent:
            os_name = "Android"
        elif "iPhone" in agent or "iPad" in agent:
            os_name = "iOS"
        else:
            os_name = ""

        if os_name:
            return f"{browser} ({os_name})"
        return browser


class CheckSessionView(View):
    """供前端轮询：检查当前 session 是否仍然有效。"""

    def get(self, request):
        from django.http import JsonResponse

        if not request.user.is_authenticated:
            return JsonResponse({"valid": False})

        try:
            profile = request.user.profile
            if profile.current_session_key != request.session.session_key:
                return JsonResponse({"valid": False})
        except Exception:
            pass

        return JsonResponse({"valid": True})


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
            # 清理当前会话追踪，避免下次登录误报冲突
            try:
                profile = request.user.profile
                profile.current_session_key = ""
                profile.save(update_fields=["current_session_key", "updated_at"])
            except Exception:
                pass

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
