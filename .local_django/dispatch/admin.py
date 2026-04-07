from django.contrib import admin

from .models import AssignmentRun, CustomerMemo, DispatchUpload, Driver, Route, RouteAssignment, RouteGroupSuggestion, ShareSnapshot, Stop


@admin.register(Driver)
class DriverAdmin(admin.ModelAdmin):
    list_display = ("name", "status", "vehicle_type", "sort_order", "updated_at")
    list_filter = ("status",)
    search_fields = ("name", "phone")
    ordering = ("sort_order", "name")


@admin.register(DispatchUpload)
class DispatchUploadAdmin(admin.ModelAdmin):
    list_display = ("source_date", "source_filename", "parse_status", "camp_scope", "created_at")
    list_filter = ("parse_status", "source_date")
    search_fields = ("source_filename", "camp_scope")


@admin.register(Route)
class RouteAdmin(admin.ModelAdmin):
    list_display = ("route_code", "route_prefix", "truck_request_id", "camp_name", "total_qty", "work_minutes")
    list_filter = ("camp_name",)
    search_fields = ("route_code", "truck_request_id", "camp_name")


@admin.register(Stop)
class StopAdmin(admin.ModelAdmin):
    list_display = ("route", "stop_order", "company_name", "address", "is_center")
    list_filter = ("is_center", "spu_center")
    search_fields = ("company_name", "address", "address_norm")


@admin.register(AssignmentRun)
class AssignmentRunAdmin(admin.ModelAdmin):
    list_display = ("name", "upload", "status", "is_latest", "updated_at")
    list_filter = ("status", "is_latest")
    search_fields = ("name", "notes")


@admin.register(RouteGroupSuggestion)
class RouteGroupSuggestionAdmin(admin.ModelAdmin):
    list_display = ("assignment_run", "route", "group_name", "group_order")
    list_filter = ("group_name",)


@admin.register(RouteAssignment)
class RouteAssignmentAdmin(admin.ModelAdmin):
    list_display = ("assignment_run", "route", "driver", "assignment_source", "saved_at")
    list_filter = ("assignment_source",)
    search_fields = ("route__route_code", "driver__name")


@admin.register(ShareSnapshot)
class ShareSnapshotAdmin(admin.ModelAdmin):
    list_display = ("share_key", "assignment_run", "snapshot_kind", "created_at", "expires_at")
    list_filter = ("snapshot_kind",)


@admin.register(CustomerMemo)
class CustomerMemoAdmin(admin.ModelAdmin):
    list_display = ("match_type", "match_key", "company_name", "company_id", "is_active", "updated_at")
    list_filter = ("match_type", "is_active")
    search_fields = ("match_key", "company_name", "company_id", "address", "address_norm", "note")
