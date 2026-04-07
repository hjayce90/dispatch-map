# Local Django Test Backend

This is a local-only Django test backend for validating the future `dispatch-map` -> `Nasil-Sale` integration.

## What is included

- SQLite-backed local Django project
- `dispatch` app with draft models
- DRF endpoints for:
  - health check
  - driver list
  - upload list/create
  - latest assignment run
  - assignment run detail
  - driver stats stub

## Project path

- [manage.py](/C:/Users/niceh/Desktop/dispatch-map-github/.local_django/manage.py)

## Run locally

From `C:\Users\niceh\Desktop\dispatch-map-github\.local_django`

```powershell
python manage.py migrate
python manage.py import_drivers
python manage.py runserver
```

Open:

- API health: [http://127.0.0.1:8000/api/dispatch/health/](http://127.0.0.1:8000/api/dispatch/health/)
- Drivers: [http://127.0.0.1:8000/api/dispatch/drivers/](http://127.0.0.1:8000/api/dispatch/drivers/)
- Latest run: [http://127.0.0.1:8000/api/dispatch/assignment-runs/latest/](http://127.0.0.1:8000/api/dispatch/assignment-runs/latest/)

## Notes

- This backend is intentionally local and temporary.
- It is meant to prove the data model and API flow before moving the same concepts into `Nasil-Sale`.
- The current endpoints are a scaffold, not the final production backend.
