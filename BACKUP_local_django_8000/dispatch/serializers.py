from rest_framework import serializers

from .models import AssignmentRun, DispatchUpload, Driver, Route, RouteAssignment, RouteGroupSuggestion, ShareSnapshot, Stop


class DriverSerializer(serializers.ModelSerializer):
    class Meta:
        model = Driver
        fields = [
            "id",
            "name",
            "status",
            "vehicle_type",
            "phone",
            "notes",
            "sort_order",
        ]


class DispatchUploadSerializer(serializers.ModelSerializer):
    route_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = DispatchUpload
        fields = [
            "id",
            "source_date",
            "source_filename",
            "camp_scope",
            "parse_status",
            "parse_message",
            "raw_meta",
            "created_at",
            "updated_at",
            "route_count",
        ]


class RouteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Route
        fields = [
            "id",
            "route_code",
            "route_prefix",
            "truck_request_id",
            "camp_code",
            "camp_name",
            "start_min",
            "end_min",
            "stop_count",
            "small_qty",
            "medium_qty",
            "large_qty",
            "total_qty",
            "work_minutes",
            "route_meta",
        ]


class StopSerializer(serializers.ModelSerializer):
    route_id = serializers.IntegerField(source="route.id", read_only=True)

    class Meta:
        model = Stop
        fields = [
            "id",
            "route_id",
            "stop_order",
            "house_order",
            "company_id",
            "company_name",
            "address",
            "address_norm",
            "lat",
            "lon",
            "time_str",
            "time_minutes",
            "small_qty",
            "medium_qty",
            "large_qty",
            "is_center",
            "center_type",
            "spu_center",
            "stop_meta",
        ]


class RouteAssignmentSerializer(serializers.ModelSerializer):
    route_id = serializers.IntegerField(source="route.id", read_only=True)
    route_code = serializers.CharField(source="route.route_code", read_only=True)
    driver_name = serializers.CharField(source="driver.name", read_only=True)

    class Meta:
        model = RouteAssignment
        fields = [
            "id",
            "route_id",
            "route_code",
            "driver",
            "driver_name",
            "assignment_source",
            "saved_at",
            "assignment_meta",
        ]


class RouteGroupSuggestionSerializer(serializers.ModelSerializer):
    route_id = serializers.IntegerField(source="route.id", read_only=True)
    route_code = serializers.CharField(source="route.route_code", read_only=True)

    class Meta:
        model = RouteGroupSuggestion
        fields = [
            "id",
            "route_id",
            "route_code",
            "group_name",
            "group_order",
            "metrics",
        ]


class AssignmentRunSerializer(serializers.ModelSerializer):
    upload_id = serializers.IntegerField(source="upload.id", read_only=True)
    source_date = serializers.DateField(source="upload.source_date", read_only=True)
    source_filename = serializers.CharField(source="upload.source_filename", read_only=True)

    class Meta:
        model = AssignmentRun
        fields = [
            "id",
            "name",
            "status",
            "upload_id",
            "source_date",
            "source_filename",
            "last_opened_at",
            "is_latest",
            "ui_state",
            "notes",
            "created_at",
            "updated_at",
        ]


class AssignmentRunDetailSerializer(serializers.ModelSerializer):
    upload = DispatchUploadSerializer(read_only=True)
    routes = RouteSerializer(source="upload.routes", many=True, read_only=True)
    route_assignments = RouteAssignmentSerializer(many=True, read_only=True)
    group_suggestions = RouteGroupSuggestionSerializer(many=True, read_only=True)
    stops = serializers.SerializerMethodField()
    latest_snapshot = serializers.SerializerMethodField()

    class Meta:
        model = AssignmentRun
        fields = [
            "id",
            "name",
            "status",
            "upload",
            "ui_state",
            "notes",
            "created_at",
            "updated_at",
            "routes",
            "stops",
            "route_assignments",
            "group_suggestions",
            "latest_snapshot",
        ]

    def get_stops(self, obj):
        stops = Stop.objects.filter(route__upload=obj.upload).select_related("route").order_by("route_id", "stop_order")
        return StopSerializer(stops, many=True).data

    def get_latest_snapshot(self, obj):
        snapshot = obj.share_snapshots.order_by("-created_at").first()
        if not snapshot:
            return None
        return {
            "id": snapshot.id,
            "share_key": snapshot.share_key,
            "snapshot_kind": snapshot.snapshot_kind,
            "payload": snapshot.payload,
            "created_at": snapshot.created_at,
        }
