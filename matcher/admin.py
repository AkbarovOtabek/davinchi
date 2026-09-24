from django.contrib import admin
from django.utils.html import format_html

from .models import ActionLog, BotConfig, GeneratedMessage, Profile


@admin.register(BotConfig)
class BotConfigAdmin(admin.ModelAdmin):
    list_display = ["__str__", "enabled", "auto_send", "tone", "daily_limit", "updated_at"]

    def has_add_permission(self, request):
        return not BotConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


class GeneratedMessageInline(admin.TabularInline):
    model = GeneratedMessage
    extra = 0
    readonly_fields = ["text", "language", "strategy", "model", "status", "sent_at", "created_at"]
    fields = readonly_fields
    can_delete = False


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ["created_at", "name", "age", "city", "short_about", "status"]
    list_filter = ["status", "language", "created_at"]
    search_fields = ["name", "city", "about", "raw_caption"]
    readonly_fields = ["fingerprint", "created_at", "raw_caption", "photos"]
    inlines = [GeneratedMessageInline]
    date_hierarchy = "created_at"

    @admin.display(description="О себе")
    def short_about(self, obj):
        return format_html("<span title='{}'>{}</span>", obj.about, (obj.about or "—")[:60])


@admin.register(GeneratedMessage)
class GeneratedMessageAdmin(admin.ModelAdmin):
    list_display = ["created_at", "profile", "strategy", "language", "status", "sent_at"]
    list_filter = ["status", "strategy", "language", "model"]
    search_fields = ["text"]
    readonly_fields = [f.name for f in GeneratedMessage._meta.fields]


@admin.register(ActionLog)
class ActionLogAdmin(admin.ModelAdmin):
    list_display = ["created_at", "level", "event", "short_detail"]
    list_filter = ["level", "event"]
    search_fields = ["detail"]
    readonly_fields = [f.name for f in ActionLog._meta.fields]

    @admin.display(description="Детали")
    def short_detail(self, obj):
        return (obj.detail or "")[:90]
