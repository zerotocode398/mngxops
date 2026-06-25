from django.contrib import admin
from .models import AuditLog, LoginLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "user",
        "module",
        "action",
        "ip",
        "result",
        "created_at",
    ]
    list_filter = ["module", "result", "created_at"]
    search_fields = ["user__username", "action", "ip"]
    readonly_fields = ["created_at"]


@admin.register(LoginLog)
class LoginLogAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "username",
        "ip",
        "status",
        "created_at",
    ]
    list_filter = ["status", "created_at"]
    search_fields = ["username", "ip"]
    readonly_fields = ["created_at"]
