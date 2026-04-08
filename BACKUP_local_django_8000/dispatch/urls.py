from django.urls import path

from .views import (
    AssignmentRunDetailView,
    AssignmentRunListView,
    CustomerMemoBulkLookupView,
    CustomerMemoUpsertView,
    DispatchUploadListCreateView,
    DriverListView,
    DriverStatsStubView,
    HealthCheckView,
    LatestAssignmentRunView,
    SaveRunSnapshotView,
    ShareSnapshotPageView,
    SyncAssignmentRunView,
    SyncAssignmentsView,
)


urlpatterns = [
    path("health/", HealthCheckView.as_view(), name="dispatch-health"),
    path("drivers/", DriverListView.as_view(), name="dispatch-driver-list"),
    path("customer-memos/bulk-lookup/", CustomerMemoBulkLookupView.as_view(), name="dispatch-customer-memo-bulk-lookup"),
    path("customer-memos/upsert/", CustomerMemoUpsertView.as_view(), name="dispatch-customer-memo-upsert"),
    path("uploads/", DispatchUploadListCreateView.as_view(), name="dispatch-upload-list-create"),
    path("assignment-runs/sync/", SyncAssignmentRunView.as_view(), name="dispatch-assignment-run-sync"),
    path("assignment-runs/", AssignmentRunListView.as_view(), name="dispatch-assignment-run-list"),
    path("assignment-runs/latest/", LatestAssignmentRunView.as_view(), name="dispatch-assignment-run-latest"),
    path("assignment-runs/<int:pk>/", AssignmentRunDetailView.as_view(), name="dispatch-assignment-run-detail"),
    path("assignment-runs/<int:pk>/assignments/sync/", SyncAssignmentsView.as_view(), name="dispatch-assignment-sync"),
    path("assignment-runs/<int:pk>/snapshot/", SaveRunSnapshotView.as_view(), name="dispatch-run-snapshot-save"),
    path("stats/drivers/", DriverStatsStubView.as_view(), name="dispatch-driver-stats"),
    path("share/<str:share_key>/", ShareSnapshotPageView.as_view(), name="dispatch-share-page"),
]
