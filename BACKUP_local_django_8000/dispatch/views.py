from django.db import transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    AssignmentRun,
    AssignmentRunStatus,
    DispatchUpload,
    Driver,
    CustomerMemo,
    MemoMatchType,
    ParseStatus,
    Route,
    RouteAssignment,
    ShareSnapshot,
    SnapshotKind,
)
from .serializers import (
    AssignmentRunDetailSerializer,
    AssignmentRunSerializer,
    DispatchUploadSerializer,
    DriverSerializer,
)


class HealthCheckView(APIView):
    def get(self, request):
        return Response(
            {
                "status": "ok",
                "service": "dispatch-backend",
                "timestamp": timezone.now(),
            }
        )


class DriverListView(generics.ListAPIView):
    queryset = Driver.objects.all()
    serializer_class = DriverSerializer


class DispatchUploadListCreateView(generics.ListCreateAPIView):
    serializer_class = DispatchUploadSerializer

    def get_queryset(self):
        return DispatchUpload.objects.annotate(route_count=Count("routes")).order_by("-source_date", "-created_at")

    def create(self, request, *args, **kwargs):
        source_date = request.data.get("source_date")
        source_filename = (request.data.get("source_filename") or "").strip()
        if not source_date or not source_filename:
            return Response(
                {"detail": "source_date and source_filename are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        upload, created = DispatchUpload.objects.get_or_create(
            source_date=source_date,
            source_filename=source_filename,
            defaults={
                "camp_scope": (request.data.get("camp_scope") or "").strip(),
                "parse_status": ParseStatus.PARSED,
                "parse_message": (request.data.get("parse_message") or "").strip(),
                "raw_meta": request.data.get("raw_meta") or {},
            },
        )

        if not created:
            upload.camp_scope = (request.data.get("camp_scope") or upload.camp_scope or "").strip()
            upload.parse_status = ParseStatus.PARSED
            upload.parse_message = (request.data.get("parse_message") or upload.parse_message or "").strip()
            upload.raw_meta = request.data.get("raw_meta") or upload.raw_meta or {}
            upload.save()

        serializer = self.get_serializer(upload)
        return Response(serializer.data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class SyncAssignmentRunView(APIView):
    @transaction.atomic
    def post(self, request):
        source_date = request.data.get("source_date")
        source_filename = (request.data.get("source_filename") or "").strip()
        route_rows = request.data.get("routes") or []
        run_name = (request.data.get("run_name") or "").strip()
        ui_state = request.data.get("ui_state") or {}
        raw_meta = request.data.get("raw_meta") or {}

        if not source_date or not source_filename:
            return Response(
                {"detail": "source_date and source_filename are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        upload, _ = DispatchUpload.objects.get_or_create(
            source_date=source_date,
            source_filename=source_filename,
            defaults={
                "parse_status": ParseStatus.PARSED,
                "raw_meta": raw_meta,
            },
        )
        upload.parse_status = ParseStatus.PARSED
        upload.raw_meta = raw_meta or upload.raw_meta or {}
        upload.save()

        run_defaults = {
            "status": AssignmentRunStatus.ACTIVE,
            "is_latest": True,
            "ui_state": ui_state,
            "notes": "Created from Streamlit sync",
        }
        run, created = AssignmentRun.objects.get_or_create(
            upload=upload,
            name=run_name or f"{source_date} {source_filename}",
            defaults=run_defaults,
        )
        if not created:
            run.status = AssignmentRunStatus.ACTIVE
            run.is_latest = True
            run.ui_state = ui_state or run.ui_state or {}
            run.save()

        AssignmentRun.objects.exclude(pk=run.pk).update(is_latest=False)

        incoming_keys = set()
        for row in route_rows:
            route_code = str(row.get("route") or row.get("route_code") or "").strip()
            truck_request_id = str(row.get("truck_request_id") or "").strip()
            if not route_code:
                continue

            incoming_keys.add((route_code, truck_request_id))
            route, _ = Route.objects.get_or_create(
                upload=upload,
                route_code=route_code,
                truck_request_id=truck_request_id,
                defaults={
                    "route_prefix": str(row.get("route_prefix") or "").strip(),
                    "camp_name": str(row.get("camp_name") or "").strip(),
                    "camp_code": str(row.get("camp_code") or "").strip(),
                },
            )

            route.route_prefix = str(row.get("route_prefix") or route.route_prefix or "").strip()
            route.camp_name = str(row.get("camp_name") or route.camp_name or "").strip()
            route.camp_code = str(row.get("camp_code") or route.camp_code or "").strip()
            route.start_min = row.get("start_min")
            route.end_min = row.get("end_min")
            route.stop_count = int(row.get("stop_count") or 0)
            route.small_qty = int(row.get("small_qty") or 0)
            route.medium_qty = int(row.get("medium_qty") or 0)
            route.large_qty = int(row.get("large_qty") or 0)
            route.total_qty = int(row.get("total_qty") or 0)
            route.work_minutes = int(row.get("work_minutes") or 0)
            route.route_meta = row.get("route_meta") or route.route_meta or {}
            route.save()

        stale_routes = [
            route.id
            for route in upload.routes.all()
            if (route.route_code, route.truck_request_id) not in incoming_keys
        ]
        if stale_routes:
            Route.objects.filter(id__in=stale_routes).delete()

        detail = AssignmentRunDetailSerializer(run)
        return Response(
            {
                "run": AssignmentRunSerializer(run).data,
                "detail": detail.data,
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class SyncAssignmentsView(APIView):
    @transaction.atomic
    def post(self, request, pk):
        try:
            run = AssignmentRun.objects.select_related("upload").get(pk=pk)
        except AssignmentRun.DoesNotExist:
            return Response({"detail": "AssignmentRun not found."}, status=status.HTTP_404_NOT_FOUND)

        assignments = request.data.get("assignments") or []
        saved_count = 0
        for item in assignments:
            route_code = str(item.get("route") or item.get("route_code") or "").strip()
            truck_request_id = str(item.get("truck_request_id") or "").strip()
            driver_name = str(item.get("driver_name") or "").strip()
            assignment_source = str(item.get("assignment_source") or "manual").strip() or "manual"

            if not route_code:
                continue

            try:
                route = Route.objects.get(
                    upload=run.upload,
                    route_code=route_code,
                    truck_request_id=truck_request_id,
                )
            except Route.DoesNotExist:
                continue

            driver = None
            if driver_name:
                driver = Driver.objects.filter(name=driver_name).first()

            route_assignment, _ = RouteAssignment.objects.get_or_create(
                assignment_run=run,
                route=route,
                defaults={
                    "driver": driver,
                    "assignment_source": assignment_source,
                },
            )
            route_assignment.driver = driver
            route_assignment.assignment_source = assignment_source
            route_assignment.assignment_meta = item.get("assignment_meta") or {}
            route_assignment.save()
            saved_count += 1

        run.last_opened_at = timezone.now()
        run.save(update_fields=["last_opened_at", "updated_at"])

        return Response(
            {
                "saved_count": saved_count,
                "run": AssignmentRunSerializer(run).data,
            },
            status=status.HTTP_200_OK,
        )


class SaveRunSnapshotView(APIView):
    @transaction.atomic
    def post(self, request, pk):
        try:
            run = AssignmentRun.objects.get(pk=pk)
        except AssignmentRun.DoesNotExist:
            return Response({"detail": "AssignmentRun not found."}, status=status.HTTP_404_NOT_FOUND)

        payload = request.data.get("payload") or {}
        if not isinstance(payload, dict):
            return Response({"detail": "payload must be an object."}, status=status.HTTP_400_BAD_REQUEST)

        snapshot_kind = str(request.data.get("snapshot_kind") or SnapshotKind.RECENT).strip() or SnapshotKind.RECENT
        share_key = str(request.data.get("share_key") or f"run-{run.pk}-{snapshot_kind}").strip()

        snapshot, _ = ShareSnapshot.objects.get_or_create(
            assignment_run=run,
            share_key=share_key,
            defaults={
                "snapshot_kind": snapshot_kind,
                "payload": payload,
            },
        )
        snapshot.snapshot_kind = snapshot_kind
        snapshot.payload = payload
        snapshot.save()

        run.last_opened_at = timezone.now()
        run.save(update_fields=["last_opened_at", "updated_at"])

        return Response(
            {
                "snapshot_id": snapshot.id,
                "share_key": snapshot.share_key,
                "snapshot_kind": snapshot.snapshot_kind,
            },
            status=status.HTTP_200_OK,
        )


class LatestAssignmentRunView(APIView):
    def get(self, request):
        run = (
            AssignmentRun.objects.select_related("upload")
            .order_by("-is_latest", "-updated_at", "-created_at")
            .first()
        )
        if not run:
            return Response({"run": None})

        serializer = AssignmentRunSerializer(run)
        return Response({"run": serializer.data})


class AssignmentRunListView(generics.ListAPIView):
    serializer_class = AssignmentRunSerializer

    def get_queryset(self):
        queryset = AssignmentRun.objects.select_related("upload").order_by("-updated_at", "-created_at")
        source_date = str(self.request.query_params.get("source_date", "")).strip()
        if source_date:
            queryset = queryset.filter(upload__source_date=source_date)
        try:
            limit = int(self.request.query_params.get("limit", 10))
        except Exception:
            limit = 10
        limit = min(max(limit, 1), 50)
        return queryset[:limit]


class AssignmentRunDetailView(generics.RetrieveAPIView):
    queryset = AssignmentRun.objects.select_related("upload").prefetch_related(
        "route_assignments__route",
        "route_assignments__driver",
        "group_suggestions__route",
        "upload__routes",
    )
    serializer_class = AssignmentRunDetailSerializer


class DriverStatsStubView(APIView):
    def get(self, request):
        source_date = request.query_params.get("source_date", "")
        return Response(
            {
                "base_date": source_date,
                "recent_work_date": None,
                "rows": [],
                "message": "Stats query stub. Replace with DB aggregation after real assignment data is connected.",
            },
            status=status.HTTP_200_OK,
        )


class CustomerMemoBulkLookupView(APIView):
    def post(self, request):
        rows = request.data.get("rows") or []
        out = {}
        for row in rows:
            company_id = str(row.get("company_id") or "").strip()
            address_norm = str(row.get("address_norm") or "").strip()
            memo = None
            if company_id:
                memo = CustomerMemo.objects.filter(
                    match_type=MemoMatchType.COMPANY_ID,
                    match_key=company_id,
                    is_active=True,
                ).first()
            if memo is None and address_norm:
                memo = CustomerMemo.objects.filter(
                    match_type=MemoMatchType.ADDRESS_NORM,
                    match_key=address_norm,
                    is_active=True,
                ).first()
            if memo is not None:
                out[f"{company_id}|{address_norm}"] = {
                    "id": memo.id,
                    "match_type": memo.match_type,
                    "match_key": memo.match_key,
                    "company_id": memo.company_id,
                    "company_name": memo.company_name,
                    "address": memo.address,
                    "address_norm": memo.address_norm,
                    "note": memo.note,
                    "updated_at": memo.updated_at,
                }
        return Response({"memos": out}, status=status.HTTP_200_OK)


class CustomerMemoUpsertView(APIView):
    @transaction.atomic
    def post(self, request):
        company_id = str(request.data.get("company_id") or "").strip()
        address = str(request.data.get("address") or "").strip()
        address_norm = str(request.data.get("address_norm") or "").strip()
        company_name = str(request.data.get("company_name") or "").strip()
        note = str(request.data.get("note") or "").strip()

        if company_id:
            match_type = MemoMatchType.COMPANY_ID
            match_key = company_id
        elif address_norm:
            match_type = MemoMatchType.ADDRESS_NORM
            match_key = address_norm
        else:
            return Response({"detail": "company_id or address_norm is required."}, status=status.HTTP_400_BAD_REQUEST)

        memo, _ = CustomerMemo.objects.get_or_create(
            match_type=match_type,
            match_key=match_key,
            defaults={
                "company_id": company_id,
                "company_name": company_name,
                "address": address,
                "address_norm": address_norm,
                "note": note,
                "is_active": True,
            },
        )
        memo.company_id = company_id or memo.company_id
        memo.company_name = company_name or memo.company_name
        memo.address = address or memo.address
        memo.address_norm = address_norm or memo.address_norm
        memo.note = note
        memo.is_active = True
        memo.save()

        return Response(
            {
                "id": memo.id,
                "match_type": memo.match_type,
                "match_key": memo.match_key,
                "note": memo.note,
            },
            status=status.HTTP_200_OK,
        )


class ShareSnapshotPageView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request, share_key):
        snapshot = get_object_or_404(
            ShareSnapshot.objects.select_related("assignment_run", "assignment_run__upload"),
            share_key=share_key,
            snapshot_kind=SnapshotKind.SHARE,
        )
        payload = snapshot.payload or {}
        assignment_rows = payload.get("assignment_rows", [])
        assigned_summary_rows = payload.get("assigned_summary_rows", [])
        total_count = len(assignment_rows)
        driver_count = len({
            str(row.get("assigned_driver", "")).strip()
            for row in assignment_rows
            if str(row.get("assigned_driver", "")).strip()
        })

        assignment_columns = ["route_prefix", "camp_name", "truck_request_id", "assigned_driver", "총합"]
        assignment_headers = [c for c in assignment_columns if any(c in row for row in assignment_rows)]
        assignment_display_rows = [
            {key: row.get(key, "") for key in assignment_headers}
            for row in assignment_rows
        ]

        summary_columns = ["assigned_driver", "할당루트수", "총박스합계", "총걸린시간"]
        summary_headers = [c for c in summary_columns if any(c in row for row in assigned_summary_rows)]
        summary_display_rows = [
            {key: row.get(key, "") for key in summary_headers}
            for row in assigned_summary_rows
        ]

        context = {
            "share_key": share_key,
            "run_name": snapshot.assignment_run.name,
            "source_date": snapshot.assignment_run.upload.source_date,
            "source_filename": snapshot.assignment_run.upload.source_filename,
            "map_html": payload.get("map_html", ""),
            "assignment_rows": assignment_display_rows,
            "assigned_summary_rows": summary_display_rows,
            "total_count": total_count,
            "driver_count": driver_count,
        }
        return render(request, "dispatch/share_snapshot.html", context)
