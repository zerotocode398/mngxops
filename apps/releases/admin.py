from django.contrib import admin
from .models import ReleaseTask, ReleaseHistory


@admin.register(ReleaseTask)
class ReleaseTaskAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "node",
        "config",
        "version",
        "operator",
        "status",
        "created_at",
    ]
    list_filter = ["status", "created_at"]
    search_fields = ["config__name", "node__hostname", "operator__username"]
    readonly_fields = ["result", "started_at", "finished_at", "created_at"]


@admin.register(ReleaseHistory)
class ReleaseHistoryAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "release_task",
        "config",
        "version",
        "operator",
        "action",
        "created_at",
    ]
    list_filter = ["action", "created_at"]
    search_fields = ["config__name", "operator__username"]
    readonly_fields = ["created_at"]
