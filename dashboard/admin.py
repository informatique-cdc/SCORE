from django.contrib import admin

from .models import Feedback


@admin.register(Feedback)
class FeedbackAdmin(admin.ModelAdmin):
    list_display = ["feedback_type", "area", "subject", "user", "created_at"]
    list_filter = ["feedback_type", "area"]
    readonly_fields = ["user", "tenant", "created_at"]
