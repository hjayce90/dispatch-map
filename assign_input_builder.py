import argparse
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd


REQUEST_ID_CANDIDATES = ["truck_request_id", "트럭요청ID", "request_id", "Request ID"]
DRIVER_NAME_CANDIDATES = ["assigned_driver", "이름", "driver_name", "기사명"]
ASSIGN_INPUT_COLUMNS = [
    "registration_mode",
    "order_date",
    "request_id",
    "worker_login_id",
    "plate_number",
]
ASSIGN_ERROR_COLUMNS = [
    "request_id",
    "driver_name",
    "registration_mode",
    "order_date",
    "error_reason",
    "source_row",
]
DRIVER_REQUIRED_COLUMNS = ["driver_name", "worker_login_id", "plate_number"]


def default_order_date(now=None) -> str:
    current = now or datetime.now(ZoneInfo("Asia/Seoul"))
    if current.tzinfo is None:
        current = current.replace(tzinfo=ZoneInfo("Asia/Seoul"))
    current_kst = current.astimezone(ZoneInfo("Asia/Seoul"))
    return (current_kst + timedelta(days=1)).strftime("%Y-%m-%d")


def _clean_header(value) -> str:
    return str(value or "").replace("\ufeff", "").strip()


def _header_key(value) -> str:
    return _clean_header(value).casefold().replace(" ", "").replace("_", "")


def _resolve_column(columns, candidates, target_name: str) -> str:
    exact_lookup = {_clean_header(column): column for column in columns}
    normalized_lookup = {_header_key(column): column for column in columns}

    for candidate in candidates:
        clean_candidate = _clean_header(candidate)
        if clean_candidate in exact_lookup:
            return exact_lookup[clean_candidate]

    for candidate in candidates:
        normalized_candidate = _header_key(candidate)
        if normalized_candidate in normalized_lookup:
            return normalized_lookup[normalized_candidate]

    raise ValueError(
        f"Missing {target_name} column. Expected one of: {', '.join(candidates)}"
    )


def _normalize_assignment_df(assignment_df: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(assignment_df, pd.DataFrame):
        raise TypeError("assignment_df must be a pandas DataFrame")

    request_col = _resolve_column(
        assignment_df.columns,
        REQUEST_ID_CANDIDATES,
        "request_id",
    )
    driver_col = _resolve_column(
        assignment_df.columns,
        DRIVER_NAME_CANDIDATES,
        "driver_name",
    )

    out = pd.DataFrame(
        {
            "request_id": assignment_df[request_col].fillna("").astype(str).str.strip(),
            "driver_name": assignment_df[driver_col].fillna("").astype(str).str.strip(),
            "source_row": range(2, len(assignment_df) + 2),
        }
    )
    return out


def _validate_drivers_df(drivers_df: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(drivers_df, pd.DataFrame):
        raise TypeError("drivers_df must be a pandas DataFrame")

    missing = [column for column in DRIVER_REQUIRED_COLUMNS if column not in drivers_df.columns]
    if missing:
        raise ValueError(f"drivers.csv missing required columns: {', '.join(missing)}")

    work_df = drivers_df[DRIVER_REQUIRED_COLUMNS].copy()
    for column in DRIVER_REQUIRED_COLUMNS:
        work_df[column] = work_df[column].fillna("").astype(str).str.strip()

    work_df = work_df[work_df["driver_name"] != ""].copy()
    duplicated = work_df.loc[work_df["driver_name"].duplicated(), "driver_name"].unique().tolist()
    if duplicated:
        raise ValueError(
            "drivers.csv has duplicate driver_name values: " + ", ".join(duplicated)
        )

    return work_df


def build_assign_input_df(
    assignment_df: pd.DataFrame,
    drivers_df: pd.DataFrame,
    order_date=None,
    registration_mode: str = "new",
    *,
    now=None,
):
    resolved_order_date = (
        pd.Timestamp(order_date).strftime("%Y-%m-%d")
        if order_date is not None and str(order_date).strip()
        else default_order_date(now=now)
    )
    resolved_mode = str(registration_mode or "new").strip().lower() or "new"
    if resolved_mode not in {"new", "modify"}:
        raise ValueError("registration_mode must be 'new' or 'modify'")

    normalized_assignments = _normalize_assignment_df(assignment_df)
    drivers = _validate_drivers_df(drivers_df)
    merged = normalized_assignments.merge(drivers, on="driver_name", how="left")
    merged["registration_mode"] = resolved_mode
    merged["order_date"] = resolved_order_date

    error_reasons = []
    for _, row in merged.iterrows():
        reasons = []
        if not str(row.get("request_id", "")).strip():
            reasons.append("missing request_id")
        driver_name = str(row.get("driver_name", "")).strip()
        if not driver_name:
            reasons.append("missing driver_name")
        elif pd.isna(row.get("worker_login_id")):
            reasons.append("driver_name not found in drivers.csv")
        if not str(row.get("worker_login_id", "") if not pd.isna(row.get("worker_login_id")) else "").strip():
            reasons.append("missing worker_login_id")
        if not str(row.get("plate_number", "") if not pd.isna(row.get("plate_number")) else "").strip():
            reasons.append("missing plate_number")
        error_reasons.append("; ".join(dict.fromkeys(reasons)))

    merged["error_reason"] = error_reasons
    errors_df = merged[merged["error_reason"] != ""].copy()
    success_df = merged[merged["error_reason"] == ""].copy()

    for column in ASSIGN_INPUT_COLUMNS:
        success_df[column] = success_df[column].fillna("").astype(str).str.strip()

    error_out = pd.DataFrame(columns=ASSIGN_ERROR_COLUMNS)
    if len(errors_df) > 0:
        error_out = errors_df.copy()
        for column in ["request_id", "driver_name", "registration_mode", "order_date", "error_reason", "source_row"]:
            error_out[column] = error_out[column].fillna("").astype(str).str.strip()
        error_out = error_out[ASSIGN_ERROR_COLUMNS].sort_values(
            ["request_id", "source_row"],
            kind="stable",
        )

    success_out = success_df[ASSIGN_INPUT_COLUMNS].sort_values(
        ["request_id"],
        kind="stable",
    )
    return success_out.reset_index(drop=True), error_out.reset_index(drop=True)


def build_assign_input_from_csv(
    assignment_csv_path,
    drivers_csv_path,
    order_date=None,
    registration_mode: str = "new",
):
    assignment_path = Path(assignment_csv_path)
    drivers_path = Path(drivers_csv_path)
    if not assignment_path.exists():
        raise FileNotFoundError(f"Assignment CSV not found: {assignment_path}")
    if not drivers_path.exists():
        raise FileNotFoundError(f"drivers.csv not found: {drivers_path}")

    assignment_df = pd.read_csv(assignment_path, dtype=str).fillna("")
    drivers_df = pd.read_csv(drivers_path, dtype=str).fillna("")
    return build_assign_input_df(
        assignment_df,
        drivers_df,
        order_date=order_date,
        registration_mode=registration_mode,
    )


def _main():
    parser = argparse.ArgumentParser(
        description="Build assign_bot input CSV from a dispatch route assignment CSV."
    )
    parser.add_argument("assignment_csv", help="Dispatch route assignment CSV path")
    parser.add_argument("--drivers", default="drivers.csv", help="drivers.csv path")
    parser.add_argument("--out", default="coupang_assign_input.csv", help="Output CSV path")
    parser.add_argument("--errors", default="coupang_assign_errors.csv", help="Error CSV path")
    parser.add_argument("--order-date", default=None, help="Order date YYYY-MM-DD")
    parser.add_argument("--mode", default="new", choices=["new", "modify"], help="Registration mode")
    args = parser.parse_args()

    success_df, error_df = build_assign_input_from_csv(
        args.assignment_csv,
        args.drivers,
        order_date=args.order_date,
        registration_mode=args.mode,
    )
    success_df.to_csv(args.out, index=False, encoding="utf-8-sig")
    error_df.to_csv(args.errors, index=False, encoding="utf-8-sig")
    print(f"Wrote {len(success_df)} runnable rows to {args.out}")
    print(f"Wrote {len(error_df)} error rows to {args.errors}")


if __name__ == "__main__":
    _main()
