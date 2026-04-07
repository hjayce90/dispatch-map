from django.conf import settings
from django.db import models


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class DriverStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    INACTIVE = "inactive", "Inactive"


class ParseStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PARSED = "parsed", "Parsed"
    FAILED = "failed", "Failed"


class AssignmentRunStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    ACTIVE = "active", "Active"
    ARCHIVED = "archived", "Archived"


class AssignmentSource(models.TextChoices):
    MANUAL = "manual", "Manual"
    RECOMMENDED = "recommended", "Recommended"
    IMPORTED = "imported", "Imported"


class SnapshotKind(models.TextChoices):
    SHARE = "share", "Share"
    AUTOSAVE = "autosave", "Autosave"
    RECENT = "recent", "Recent"


class Driver(TimeStampedModel):
    """
    Dispatch-facing driver master.

    If Nasil-Sale already has a staff or driver master table, replace this
    model with a bridge to that table or add a OneToOneField / ForeignKey.
    """

    name = models.CharField(max_length=100, db_index=True)
    status = models.CharField(
        max_length=20,
        choices=DriverStatus.choices,
        default=DriverStatus.ACTIVE,
    )
    vehicle_type = models.CharField(max_length=50, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    notes = models.TextField(blank=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class DispatchUpload(TimeStampedModel):
    """
    One uploaded workbook / source batch.
    """

    source_date = models.DateField(db_index=True)
    source_filename = models.CharField(max_length=255)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="dispatch_uploads",
    )
    source_file = models.FileField(upload_to="dispatch/uploads/%Y/%m/", blank=True)
    camp_scope = models.CharField(max_length=50, blank=True)
    parse_status = models.CharField(
        max_length=20,
        choices=ParseStatus.choices,
        default=ParseStatus.PENDING,
    )
    parse_message = models.TextField(blank=True)
    raw_meta = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-source_date", "-created_at"]

    def __str__(self):
        return f"{self.source_date} - {self.source_filename}"


class Route(TimeStampedModel):
    """
    Route-level summary generated from one upload.
    """

    upload = models.ForeignKey(
        DispatchUpload,
        on_delete=models.CASCADE,
        related_name="routes",
    )
    route_code = models.CharField(max_length=100, db_index=True)
    route_prefix = models.CharField(max_length=20, blank=True)
    truck_request_id = models.CharField(max_length=100, blank=True, db_index=True)
    camp_code = models.CharField(max_length=50, blank=True)
    camp_name = models.CharField(max_length=100, blank=True)
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    start_min = models.IntegerField(null=True, blank=True)
    end_min = models.IntegerField(null=True, blank=True)
    stop_count = models.PositiveIntegerField(default=0)
    small_qty = models.PositiveIntegerField(default=0)
    medium_qty = models.PositiveIntegerField(default=0)
    large_qty = models.PositiveIntegerField(default=0)
    total_qty = models.PositiveIntegerField(default=0)
    work_minutes = models.PositiveIntegerField(default=0)
    route_meta = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["route_prefix", "route_code", "truck_request_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["upload", "route_code", "truck_request_id"],
                name="dispatch_route_unique_per_upload",
            )
        ]

    def __str__(self):
        return f"{self.route_code} ({self.truck_request_id})"


class Stop(TimeStampedModel):
    """
    Delivery stop under a route.
    """

    route = models.ForeignKey(Route, on_delete=models.CASCADE, related_name="stops")
    stop_order = models.PositiveIntegerField()
    house_order = models.PositiveIntegerField(null=True, blank=True)
    company_id = models.CharField(max_length=100, blank=True)
    company_name = models.CharField(max_length=255, blank=True)
    address = models.CharField(max_length=500)
    address_norm = models.CharField(max_length=500, db_index=True)
    lat = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    lon = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    time_str = models.CharField(max_length=20, blank=True)
    time_minutes = models.IntegerField(null=True, blank=True)
    small_qty = models.PositiveIntegerField(default=0)
    medium_qty = models.PositiveIntegerField(default=0)
    large_qty = models.PositiveIntegerField(default=0)
    is_center = models.BooleanField(default=False)
    center_type = models.CharField(max_length=50, blank=True)
    spu_center = models.CharField(max_length=50, blank=True)
    stop_meta = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["route", "stop_order", "id"]

    def __str__(self):
        return f"{self.route.route_code} #{self.stop_order}"


class AssignmentRun(TimeStampedModel):
    """
    One assignment work session or scenario.
    """

    upload = models.ForeignKey(
        DispatchUpload,
        on_delete=models.CASCADE,
        related_name="assignment_runs",
    )
    name = models.CharField(max_length=150)
    status = models.CharField(
        max_length=20,
        choices=AssignmentRunStatus.choices,
        default=AssignmentRunStatus.DRAFT,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="dispatch_assignment_runs",
    )
    last_opened_at = models.DateTimeField(null=True, blank=True)
    is_latest = models.BooleanField(default=False)
    ui_state = models.JSONField(default=dict, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-updated_at", "-created_at"]

    def __str__(self):
        return self.name


class RouteGroupSuggestion(TimeStampedModel):
    """
    Saved recommended or edited group placement per route.
    """

    assignment_run = models.ForeignKey(
        AssignmentRun,
        on_delete=models.CASCADE,
        related_name="group_suggestions",
    )
    route = models.ForeignKey(Route, on_delete=models.CASCADE, related_name="group_suggestions")
    group_name = models.CharField(max_length=100)
    group_order = models.PositiveIntegerField(default=1)
    metrics = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["group_order", "group_name", "route__route_prefix", "route__route_code"]
        constraints = [
            models.UniqueConstraint(
                fields=["assignment_run", "route"],
                name="dispatch_group_suggestion_unique_per_run_route",
            )
        ]

    def __str__(self):
        return f"{self.assignment_run_id} - {self.group_name} - {self.route_id}"


class RouteAssignment(TimeStampedModel):
    """
    Actual driver assignment per route inside a run.
    """

    assignment_run = models.ForeignKey(
        AssignmentRun,
        on_delete=models.CASCADE,
        related_name="route_assignments",
    )
    route = models.ForeignKey(Route, on_delete=models.CASCADE, related_name="route_assignments")
    driver = models.ForeignKey(
        Driver,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="route_assignments",
    )
    assignment_source = models.CharField(
        max_length=20,
        choices=AssignmentSource.choices,
        default=AssignmentSource.MANUAL,
    )
    saved_at = models.DateTimeField(auto_now=True)
    assignment_meta = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["route__route_prefix", "route__route_code", "route__truck_request_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["assignment_run", "route"],
                name="dispatch_route_assignment_unique_per_run_route",
            )
        ]

    def __str__(self):
        driver_name = self.driver.name if self.driver_id else "unassigned"
        return f"{self.route_id} -> {driver_name}"


class ShareSnapshot(TimeStampedModel):
    """
    Payload or reference for share links and recent restore.
    """

    assignment_run = models.ForeignKey(
        AssignmentRun,
        on_delete=models.CASCADE,
        related_name="share_snapshots",
    )
    share_key = models.CharField(max_length=120, unique=True, db_index=True)
    snapshot_kind = models.CharField(
        max_length=20,
        choices=SnapshotKind.choices,
        default=SnapshotKind.SHARE,
    )
    payload = models.JSONField(default=dict, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.share_key

