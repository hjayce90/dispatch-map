from datetime import date

from django.core.management.base import BaseCommand

from dispatch.models import AssignmentRun, AssignmentRunStatus, DispatchUpload, Driver, Route, RouteAssignment


class Command(BaseCommand):
    help = "Seed a tiny demo dispatch upload and assignment run for local API testing."

    def handle(self, *args, **options):
        upload, _ = DispatchUpload.objects.get_or_create(
            source_date=date.today(),
            source_filename="demo_dispatch.xlsx",
            defaults={
                "parse_status": "parsed",
                "parse_message": "Local demo seed data",
                "raw_meta": {"seeded": True},
            },
        )

        route1, _ = Route.objects.get_or_create(
            upload=upload,
            route_code="R-100",
            truck_request_id="TRK-100",
            defaults={
                "route_prefix": "A",
                "camp_code": "SPU_ILSAN1",
                "camp_name": "일산1캠프",
                "stop_count": 4,
                "small_qty": 10,
                "medium_qty": 8,
                "large_qty": 3,
                "total_qty": 21,
                "work_minutes": 180,
            },
        )

        route2, _ = Route.objects.get_or_create(
            upload=upload,
            route_code="R-200",
            truck_request_id="TRK-200",
            defaults={
                "route_prefix": "B",
                "camp_code": "SPU_ILSAN7",
                "camp_name": "일산7캠프",
                "stop_count": 5,
                "small_qty": 7,
                "medium_qty": 6,
                "large_qty": 5,
                "total_qty": 18,
                "work_minutes": 165,
            },
        )

        run, _ = AssignmentRun.objects.get_or_create(
            upload=upload,
            name=f"{upload.source_date} Demo Run",
            defaults={
                "status": AssignmentRunStatus.ACTIVE,
                "is_latest": True,
                "ui_state": {
                    "selected_driver_filter": "all",
                    "selected_group_filter": "all",
                },
                "notes": "Local seed data for early API testing",
            },
        )

        AssignmentRun.objects.exclude(pk=run.pk).update(is_latest=False)
        if not run.is_latest:
            run.is_latest = True
            run.save(update_fields=["is_latest", "updated_at"])

        first_driver = Driver.objects.order_by("sort_order", "name").first()
        if first_driver:
            RouteAssignment.objects.get_or_create(
                assignment_run=run,
                route=route1,
                defaults={
                    "driver": first_driver,
                },
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded demo upload={upload.id}, assignment_run={run.id}"
            )
        )
