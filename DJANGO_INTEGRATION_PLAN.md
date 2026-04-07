# Dispatch Map Django Integration Plan

## Goal

`dispatch-map` should evolve from a file-driven Streamlit tool into a dispatch workbench backed by the same Django data layer as `Nasil-Sale`.

Recommended target structure:

- Django (`Nasil-Sale`) owns source-of-truth data, history, session restore, and APIs
- Streamlit (`dispatch-map`) keeps the map UX, grouping experiments, and assignment workflows
- PostgreSQL is the long-term database
- Company PC can be used for early development or internal staging, but should not be the long-term single point of production

## Product Boundary

Keep in `dispatch-map`:

- map visualization
- route grouping and recommendation logic
- driver assignment UX
- assignment experiment workflow
- CSV / map export UX

Move to Django:

- Excel upload intake
- parsed route/stop persistence
- driver master data
- assignment run persistence
- assignment history and stats
- recent work/session restore
- shared snapshot metadata and payload persistence

Do not treat `dispatch-map` as:

- the permanent source-of-truth for operational raw data
- the long-term storage location for business history
- the only place where dispatch state exists

## Recommended Deployment Shape

### Phase 1

- Develop with Django on the company computer or current available environment
- Allow remote access through a private access path such as VPN or Tailscale
- Use PostgreSQL if possible from the start

### Phase 2

- Move the canonical deployment next to `Nasil-Sale`
- Make Django the shared backend for both `Nasil-Sale` and `dispatch-map`
- Keep Streamlit as a client UI that reads and writes through Django APIs

### Why this is the best fit

- You use it both at home and at the office, so central persistence matters
- External access is mostly for viewing, so private network access is enough
- You already intend to connect it to `Nasil-Sale`, so designing around a shared data model avoids rework

## Django App Proposal

Create a Django app inside `Nasil-Sale`, for example `dispatch`.

Suggested model set:

### Driver

Purpose:
- dispatch-facing driver master

Fields:
- `id`
- `name`
- `status` (`active`, `inactive`)
- `vehicle_type` nullable
- `phone` nullable
- `notes` nullable
- `sort_order` default `0`
- `created_at`
- `updated_at`

Notes:
- If `Nasil-Sale` already has a driver or employee master, use a one-to-one or foreign key bridge instead of duplicating identity

### DispatchUpload

Purpose:
- one uploaded workbook / source batch

Fields:
- `id`
- `source_date` date
- `source_filename`
- `uploaded_by` nullable FK to user
- `uploaded_at`
- `source_file` file field or storage path
- `camp_scope` nullable
- `parse_status` (`pending`, `parsed`, `failed`)
- `parse_message` nullable text
- `raw_meta` JSONField default `{}` for workbook-level metadata

### Route

Purpose:
- parsed route-level summary per upload

Fields:
- `id`
- `upload` FK to `DispatchUpload`
- `route_code`
- `route_prefix`
- `truck_request_id`
- `camp_code` nullable
- `camp_name` nullable
- `start_time` nullable time
- `end_time` nullable time
- `start_min` nullable integer
- `end_min` nullable integer
- `stop_count` default `0`
- `small_qty` default `0`
- `medium_qty` default `0`
- `large_qty` default `0`
- `total_qty` default `0`
- `work_minutes` default `0`
- `route_meta` JSONField default `{}`

Constraints:
- unique together: `upload`, `route_code`, `truck_request_id`

### Stop

Purpose:
- parsed delivery stops under a route

Fields:
- `id`
- `route` FK to `Route`
- `stop_order`
- `house_order` nullable
- `company_id` nullable
- `company_name` nullable
- `address`
- `address_norm`
- `lat` nullable decimal
- `lon` nullable decimal
- `time_str` nullable
- `time_minutes` nullable integer
- `small_qty` default `0`
- `medium_qty` default `0`
- `large_qty` default `0`
- `is_center` default `False`
- `center_type` nullable
- `spu_center` nullable
- `stop_meta` JSONField default `{}`

### AssignmentRun

Purpose:
- one work session or assignment scenario

Fields:
- `id`
- `upload` FK to `DispatchUpload`
- `name`
- `status` (`draft`, `active`, `archived`)
- `created_by` nullable FK to user
- `created_at`
- `updated_at`
- `last_opened_at` nullable
- `is_latest` default `False`
- `ui_state` JSONField default `{}`
- `notes` nullable

Examples of `ui_state`:
- selected driver filter
- selected group filter
- last viewed map mode
- panel open/close state

### RouteGroupSuggestion

Purpose:
- stores current or saved recommended group placement per route

Fields:
- `id`
- `assignment_run` FK to `AssignmentRun`
- `route` FK to `Route`
- `group_name`
- `group_order` default `1`
- `metrics` JSONField default `{}`

Constraints:
- unique together: `assignment_run`, `route`

### RouteAssignment

Purpose:
- actual driver assignment per route inside a run

Fields:
- `id`
- `assignment_run` FK to `AssignmentRun`
- `route` FK to `Route`
- `driver` nullable FK to `Driver`
- `assignment_source` (`manual`, `recommended`, `imported`)
- `saved_at`
- `assignment_meta` JSONField default `{}`

Constraints:
- unique together: `assignment_run`, `route`

### ShareSnapshot

Purpose:
- payload or reference for share links and recent restore

Fields:
- `id`
- `assignment_run` FK to `AssignmentRun`
- `share_key` unique
- `snapshot_kind` (`share`, `autosave`, `recent`)
- `payload` JSONField default `{}`
- `created_at`
- `expires_at` nullable

## Data Ownership Mapping From Current Streamlit Code

Current file-based pieces in [app.py](C:\Users\niceh\Desktop\dispatch-map-github\app.py):

- `load_drivers()` at line 148 reads `drivers.csv`
- `load_assignment_history()` at line 209 reads `assignment_history.csv`
- `save_assignment_history_for_date()` at line 231 writes `assignment_history.csv`
- `load_assignment_store()` at line 491 reads `route_assignments.json`
- `save_assignment_store()` at line 501 writes `route_assignments.json`
- `save_share_payload()` at line 542 writes shared JSON payloads
- `load_share_payload()` at line 574 reads shared JSON payloads
- `build_base_data()` at line 859 parses Excel directly in Streamlit

Recommended ownership after migration:

- `drivers.csv` -> Django `Driver`
- `assignment_history.csv` -> derived query over `RouteAssignment`, `Route`, `DispatchUpload`, `AssignmentRun`
- `route_assignments.json` -> Django `RouteAssignment`
- `shared_payloads/*.json` -> Django `ShareSnapshot`
- Excel parsing entrypoint -> Django service layer

## Service Layer Proposal In Django

Use service functions or a `services/dispatch/` module.

Suggested modules:

- `dispatch/services/upload_parser.py`
- `dispatch/services/geocoding.py`
- `dispatch/services/assignment_runs.py`
- `dispatch/services/stats.py`
- `dispatch/services/share_snapshots.py`

Suggested responsibilities:

### `upload_parser.py`

- accept uploaded workbook
- parse general sheet
- create `DispatchUpload`
- create `Route`
- create `Stop`
- normalize addresses
- derive route summary fields

This is where logic from `build_base_data()` should move first.

### `geocoding.py`

- resolve coordinates for stop addresses
- maintain reusable geocode cache in DB table or durable cache store
- populate `lat` / `lon`

### `assignment_runs.py`

- create assignment runs
- mark latest run for an upload or work date
- save route assignments
- save recommended groups
- restore latest run

### `stats.py`

- compute yesterday stats
- compute 7-day and 30-day averages
- aggregate by driver, route, camp, or date

### `share_snapshots.py`

- create and resolve shared map payloads
- create autosave snapshots for recent work restore

## API Contract Proposal

Keep the API thin and practical.

### Upload and load data

- `POST /api/dispatch/uploads/`
  - upload workbook and create parsed upload
- `GET /api/dispatch/uploads/`
  - list recent uploads
- `GET /api/dispatch/uploads/{id}/`
  - upload detail
- `GET /api/dispatch/uploads/{id}/routes/`
  - route summaries
- `GET /api/dispatch/uploads/{id}/stops/`
  - stop detail payload for map

### Drivers

- `GET /api/dispatch/drivers/`
- `POST /api/dispatch/drivers/`
- `PATCH /api/dispatch/drivers/{id}/`

### Assignment runs

- `POST /api/dispatch/assignment-runs/`
  - create run from upload
- `GET /api/dispatch/assignment-runs/{id}/`
  - return run detail, routes, assignments, group suggestions, and UI restore state
- `PATCH /api/dispatch/assignment-runs/{id}/ui-state/`
  - save current screen state
- `POST /api/dispatch/assignment-runs/{id}/assignments/`
  - save route-to-driver assignments
- `POST /api/dispatch/assignment-runs/{id}/groups/`
  - save recommended or edited groups
- `GET /api/dispatch/assignment-runs/latest/?source_date=YYYY-MM-DD`
  - restore most recent run

### Stats and reporting

- `GET /api/dispatch/stats/drivers/?source_date=YYYY-MM-DD`
  - yesterday / 7-day / 30-day stats
- `GET /api/dispatch/exports/assignment-run/{id}.csv`

### Sharing

- `POST /api/dispatch/share-snapshots/`
- `GET /api/dispatch/share-snapshots/{share_key}/`

## Streamlit Refactor Map

What stays in Streamlit:

- `render_map()`
- `render_assignment_form()`
- the route grouping UX
- recommendation visualization
- filters and exports

What should become API-backed:

- initial data load instead of `st.file_uploader`
- driver list load
- assignment save/load
- history stats
- recent work restore
- shared snapshot save/load

## Concrete Function Migration Map

### Move first

- `build_base_data()` -> Django upload parsing service
- `load_drivers()` -> Django drivers API
- `load_assignment_history()` -> Django stats API
- `save_assignment_history_for_date()` -> no direct replacement; persist route assignments and derive stats from DB
- `load_assignment_store()` -> run detail API
- `save_assignment_store()` -> assignment save API
- `save_share_payload()` -> share snapshot API
- `load_share_payload()` -> share snapshot read API

### Keep in Streamlit for now

- `render_map()`
- `build_map_data()`
- `render_group_map()` and related display logic
- `render_assignment_form()`
- recommendation logic in `auto_grouping.py`

### Consider moving later

- recommendation engine from `auto_grouping.py`
- export generation
- share rendering

## Home Screen UX Proposal

Instead of opening on a blank uploader, make the first screen show:

1. `Continue recent work`
2. `Open by date/upload`
3. `Start new upload`

Recommended startup logic:

- call latest assignment run API
- if a recent active run exists, show it first
- if no run exists, show recent uploads list
- uploader becomes a secondary action, not the only first action

## How Recent Work Restore Should Work

When a user opens the site:

- Django returns the most recent active `AssignmentRun`
- the payload includes:
  - upload metadata
  - route summaries
  - stop map data
  - saved assignments
  - saved group suggestions
  - UI state
- Streamlit rebuilds the current screen from that payload

This directly addresses the current issue where the app stops at the uploader screen and does not restore the previous working session.

## How Driver History Stats Should Work

Do not persist a separate CSV history ledger long-term.

Instead:

- each saved `RouteAssignment` belongs to an `AssignmentRun`
- each `AssignmentRun` belongs to a `DispatchUpload`
- each `DispatchUpload` has `source_date`

Then compute:

- yesterday total by driver
- latest prior workday total by driver
- 7-day average by driver
- 30-day average by driver

from relational queries.

This is more reliable than the current CSV button-based save flow, which can be skipped or lost.

## Storage Recommendation

Preferred:

- PostgreSQL for production / long-term use with `Nasil-Sale`

Acceptable only for short early development:

- SQLite on the company machine

Why PostgreSQL:

- better concurrent access
- safer long-term persistence
- better aggregation support
- cleaner fit for the eventual `Nasil-Sale` integration

## Rollout Plan

### Step 1

Add the Django `dispatch` app and models.

### Step 2

Move Excel parsing into Django and persist uploads/routes/stops.

### Step 3

Expose APIs for:
- recent uploads
- run detail
- drivers
- assignment save
- stats

### Step 4

Refactor Streamlit:
- replace file-first startup with recent-run startup
- load data from Django APIs
- save assignments and UI state to Django

### Step 5

Replace file-based history and payload storage completely.

### Step 6

Optionally move grouping/recommendation computation into Django later.

## Practical Recommendation For Your Environment

Best fit based on your usage:

- Build against a Django backend designed to live with `Nasil-Sale`
- Use a central PostgreSQL database
- Use the company computer only as an early development or staging machine if needed
- Access from home and office through a private remote path, not broad public exposure

This gives you:

- one shared dataset
- session restore
- durable assignment history
- easier later integration with `Nasil-Sale`

## Next Implementation Deliverables

Recommended next coding steps:

1. create Django `models.py` draft for the `dispatch` app
2. define serializers and API payload shape for upload detail and assignment run detail
3. refactor Streamlit startup from `file_uploader` to `recent run / upload select`
4. replace local files with API calls one area at a time
