from django.contrib import admin

from .models import Block, Inquiry, World


class BlockInline(admin.TabularInline):
    model = Block
    extra = 0


@admin.register(World)
class WorldAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "owner", "updated_at")
    search_fields = ("name", "slug", "owner__username")
    inlines = [BlockInline]


@admin.register(Inquiry)
class InquiryAdmin(admin.ModelAdmin):
    list_display = ("world", "name", "contact", "created_at")
    list_filter = ("world",)
