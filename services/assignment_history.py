"""CSV-backed assignment history helpers."""

from __future__ import annotations

import os
import re

import pandas as pd


ASSIGNMENT_HISTORY_FILE = "assignment_history.csv"
ASSIGNMENT_HISTORY_COLUMNS = [
    "date",
    "driver",
    "route",
    "truck_request_id",
    "small",
    "medium",
    "large",
    "total_qty",
    "route_count",
    "saved_at",
]


def empty_assignment_history_df() -> pd.DataFrame:
    return pd.DataFrame(columns=ASSIGNMENT_HISTORY_COLUMNS)


def ensure_assignment_history_file(history_file: str = ASSIGNMENT_HISTORY_FILE) -> None:
    required_cols = ASSIGNMENT_HISTORY_COLUMNS
    if not os.path.exists(history_file):
        pd.DataFrame(columns=required_cols).to_csv(history_file, index=False, encoding="utf-8-sig")
        return

    try:
        df = pd.read_csv(history_file)
        if len(df.columns) == 0:
            pd.DataFrame(columns=required_cols).to_csv(history_file, index=False, encoding="utf-8-sig")
    except Exception:
        pd.DataFrame(columns=required_cols).to_csv(history_file, index=False, encoding="utf-8-sig")


def infer_assignment_base_date(uploaded_filename: str, route_summary: pd.DataFrame) -> str:
    try:
        date_like_cols = [c for c in route_summary.columns if "date" in str(c).lower() or "날짜" in str(c)]
        for col in date_like_cols:
            parsed = pd.to_datetime(route_summary[col], errors="coerce").dropna()
            if len(parsed) > 0:
                return parsed.iloc[0].strftime("%Y-%m-%d")
    except Exception:
        pass

    file_text = str(uploaded_filename or "")
    match = re.search(r"(20\d{2})[-_./]?(0[1-9]|1[0-2])[-_./]?(0[1-9]|[12]\d|3[01])", file_text)
    if match:
        yyyy, mm, dd = match.group(1), match.group(2), match.group(3)
        return f"{yyyy}-{mm}-{dd}"

    return pd.Timestamp.now().strftime("%Y-%m-%d")


def load_assignment_history(history_file: str = ASSIGNMENT_HISTORY_FILE) -> pd.DataFrame:
    ensure_assignment_history_file(history_file=history_file)
    required_cols = empty_assignment_history_df().columns.tolist()

    try:
        hist = pd.read_csv(history_file)
    except Exception:
        return empty_assignment_history_df()

    if len(hist) == 0:
        return empty_assignment_history_df()

    for col in required_cols:
        if col not in hist.columns:
            hist[col] = 0 if col in {"small", "medium", "large", "total_qty", "route_count"} else ""

    hist["date"] = pd.to_datetime(hist["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    hist = hist[hist["date"].notna()].copy()
    hist["driver"] = hist["driver"].fillna("").astype(str).str.strip()
    hist = hist[hist["driver"] != ""].copy()
    return hist[required_cols].copy()


def save_assignment_history_for_date(
    assignment_df: pd.DataFrame,
    base_date: str,
    history_file: str = ASSIGNMENT_HISTORY_FILE,
) -> int:
    ensure_assignment_history_file(history_file=history_file)
    hist = load_assignment_history(history_file=history_file)

    work_df = assignment_df.copy()
    for col in ["assigned_driver", "route", "truck_request_id", "소형합", "중형합", "대형합", "총합"]:
        if col not in work_df.columns:
            work_df[col] = ""

    work_df["assigned_driver"] = work_df["assigned_driver"].fillna("").astype(str).str.strip()
    work_df = work_df[
        (work_df["assigned_driver"] != "")
        & (work_df["assigned_driver"] != "미배정")
    ].copy()

    hist = hist[hist["date"] != base_date].copy()

    if len(work_df) == 0:
        hist.to_csv(history_file, index=False, encoding="utf-8-sig")
        return 0

    now_ts = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
    save_rows = pd.DataFrame(
        {
            "date": base_date,
            "driver": work_df["assigned_driver"].astype(str),
            "route": work_df["route"].astype(str),
            "truck_request_id": work_df["truck_request_id"].astype(str),
            "small": pd.to_numeric(work_df["소형합"], errors="coerce").fillna(0).astype(int),
            "medium": pd.to_numeric(work_df["중형합"], errors="coerce").fillna(0).astype(int),
            "large": pd.to_numeric(work_df["대형합"], errors="coerce").fillna(0).astype(int),
            "total_qty": pd.to_numeric(work_df["총합"], errors="coerce").fillna(0).astype(int),
            "route_count": 1,
            "saved_at": now_ts,
        }
    )

    merged = pd.concat([hist, save_rows], ignore_index=True)
    merged.to_csv(history_file, index=False, encoding="utf-8-sig")
    return len(save_rows)
