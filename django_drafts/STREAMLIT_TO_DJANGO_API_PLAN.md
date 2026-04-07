# Streamlit To Django API Plan

## Purpose

This file turns the current `dispatch-map` Streamlit app into a staged migration plan toward a Django-backed workflow that can later live inside `Nasil-Sale`.

It is written to be practical:

- keep the current GitHub project untouched as the main working app for now
- prepare a local integration shape that can be tested before full backend merge
- allow gradual replacement of local CSV/JSON file storage with Django APIs

## Can We Run This Locally First?

Yes.

Recommended local development path:

1. Keep the current Streamlit app as-is
2. Build a Django backend separately or as a sibling project
3. Point Streamlit to local Django API endpoints
4. Test the new save/load flow locally before touching production GitHub workflows

You do not need to change the current GitHub-hosted app first.

## Current Startup Problem

Today `app.py` stops at the uploader unless a file is uploaded.

Current behavior in [app.py](C:\Users\niceh\Desktop\dispatch-map-github\app.py):

- `uploaded_file = st.file_uploader(...)`
- if no file is uploaded, the app shows only the upload prompt and stops

This creates three problems:

- no recent work restore
- no persistent latest assignment session
- stats depend on file-based writes that may be skipped or lost

## Target Startup Flow

Replace file-first startup with session-first startup.

### New home screen

- `Continue recent work`
- `Open recent upload`
- `Start new upload`

### Desired load order

1. ask Django for latest active assignment run
2. if found, restore that run
3. if not found, show recent uploads
4. uploader becomes a secondary action

## API-Backed State Model

The Streamlit app should stop owning persistence.

Instead, it should ask Django for:

- recent uploads
- run detail
- driver master
- historical stats
- share snapshots

And it should send Django:

- assignment changes
- group edits
- UI restore state
- explicit share snapshot requests

## Suggested API Payloads

### `GET /api/dispatch/assignment-runs/latest/`

Response shape:

```json
{
  "run": {
    "id": 41,
    "name": "2026-04-07 Morning Dispatch",
    "status": "active",
    "upload_id": 11,
    "source_date": "2026-04-07",
    "source_filename": "20260407.xlsx",
    "ui_state": {
      "selected_driver_filter": "all",
      "selected_group_filter": "all"
    }
  },
  "drivers": [],
  "routes": [],
  "stops": [],
  "group_suggestions": [],
  "route_assignments": []
}
```

### `POST /api/dispatch/uploads/`

Purpose:
- upload workbook
- parse workbook in Django
- create `DispatchUpload`, `Route`, and `Stop`
- optionally create a new `AssignmentRun`

### `POST /api/dispatch/assignment-runs/{id}/assignments/`

Body shape:

```json
{
  "assignments": [
    {
      "route_id": 1001,
      "driver_id": 12,
      "assignment_source": "manual"
    }
  ]
}
```

### `POST /api/dispatch/assignment-runs/{id}/groups/`

Body shape:

```json
{
  "groups": [
    {
      "route_id": 1001,
      "group_name": "추천그룹 1",
      "group_order": 1,
      "metrics": {}
    }
  ]
}
```

### `PATCH /api/dispatch/assignment-runs/{id}/ui-state/`

Body shape:

```json
{
  "selected_driver_filter": "all",
  "selected_group_filter": "추천그룹 2",
  "expanded_sections": {
    "group_edit": true,
    "driver_stats": false
  }
}
```

### `GET /api/dispatch/stats/drivers/?source_date=2026-04-07`

Response shape:

```json
{
  "base_date": "2026-04-07",
  "recent_work_date": "2026-04-06",
  "rows": [
    {
      "driver_name": "홍길동",
      "today_qty": 42,
      "recent_workday_qty": 38,
      "avg_7d_qty": 35.4,
      "avg_30d_qty": 33.1
    }
  ]
}
```

## Current Function To Future API Mapping

### Data loading and persistence

- `load_drivers()` -> `GET /api/dispatch/drivers/`
- `load_assignment_history()` -> `GET /api/dispatch/stats/drivers/`
- `save_assignment_history_for_date()` -> remove direct file save, persist `RouteAssignment`
- `load_assignment_store()` -> `GET /api/dispatch/assignment-runs/{id}/`
- `save_assignment_store()` -> `POST /api/dispatch/assignment-runs/{id}/assignments/`
- `save_share_payload()` -> `POST /api/dispatch/share-snapshots/`
- `load_share_payload()` -> `GET /api/dispatch/share-snapshots/{share_key}/`

### Excel parsing

- `build_base_data()` -> Django upload parser service and upload detail API

### Keep local for now

- `render_map()`
- `render_assignment_form()`
- route grouping display functions
- recommendation engine in `auto_grouping.py`

## Streamlit Refactor Steps

### Step A

Add a backend base URL config:

```python
DJANGO_API_BASE_URL = os.getenv("DJANGO_API_BASE_URL", "http://127.0.0.1:8000/api")
```

### Step B

Introduce API helpers:

- `api_get_drivers()`
- `api_get_latest_run()`
- `api_get_run_detail(run_id)`
- `api_create_upload(file_bytes, filename)`
- `api_save_assignments(run_id, assignments)`
- `api_save_groups(run_id, groups)`
- `api_get_driver_stats(source_date)`

### Step C

Change startup logic:

- try latest run first
- fallback to recent uploads
- uploader only if user starts a new upload

### Step D

Replace local state persistence:

- stop reading `drivers.csv`
- stop reading `route_assignments.json`
- stop reading `assignment_history.csv`
- stop reading/writing `shared_payloads/*.json`

### Step E

Keep map and recommendation behavior the same while changing only the data source

This reduces rewrite risk.

## What Should Be Implemented First

First backend milestone:

- models
- upload parse service
- drivers API
- assignment run detail API
- assignment save API
- driver stats API

First Streamlit milestone:

- backend URL config
- drivers from API
- latest run restore
- assignment save to API
- recent stats from API

## Minimal Local Test Stack

Local setup for early testing:

- Django on `127.0.0.1:8000`
- PostgreSQL locally if available, or SQLite only for temporary development
- Streamlit on `127.0.0.1:8501`
- `.env` or system environment variable for `DJANGO_API_BASE_URL`

Example:

```text
Streamlit -> http://127.0.0.1:8000/api/dispatch/...
```

## Recommended Transition Strategy

### Stage 1

Build the Django backend locally and prove:

- upload persists
- latest run restore works
- assignment save works
- stats query works

### Stage 2

Update Streamlit locally to use the new APIs.

### Stage 3

Once local behavior is stable, decide whether to:

- merge into `Nasil-Sale`
- or deploy a temporary internal dispatch backend first

### Stage 4

After backend confidence is high, update the production-facing workflow.

## Why This Fits Your Environment

Because you mostly use it from home and then from the office:

- central saved state matters more than file upload convenience
- recent session restore matters more than blank startup
- a backend tied to `Nasil-Sale` avoids duplicate databases later

Because external access is mostly view-only:

- private remote access is enough
- you do not need a fully public open server just to start

## Recommended Immediate Next Step

Build the Django app skeleton first, then create the first two APIs:

1. `POST /uploads/`
2. `GET /assignment-runs/latest/`

Those two endpoints unlock the biggest user-facing change:

- no more blank upload-only homepage
- recent work can reopen automatically
