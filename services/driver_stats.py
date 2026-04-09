"""Driver assignment statistics helpers."""

from __future__ import annotations

import pandas as pd

from .assignment_history import empty_assignment_history_df


DRIVER_STATS_COLUMNS = [
    "기사명",
    "오늘물량",
    "최근근무일(1일) 물량",
    "7일 근무일평균",
    "30일 근무일평균",
]


def safe_int(value) -> int:
    try:
        if pd.isna(value):
            return 0
        return int(float(value))
    except Exception:
        return 0


def _build_workday_avg_qty(df: pd.DataFrame, start_dt: pd.Timestamp, end_dt: pd.Timestamp) -> tuple[dict, dict]:
    period = df[(df["date_dt"] >= start_dt) & (df["date_dt"] <= end_dt)].copy()
    if len(period) == 0:
        return {}, {}

    qty_sum = period.groupby("driver")["total_qty"].sum().to_dict()
    work_days = period.groupby("driver")["date_dt"].nunique().to_dict()
    return qty_sum, work_days


def build_driver_assignment_stats_df(
    assignment_df: pd.DataFrame,
    history_df: pd.DataFrame,
    driver_candidates,
    base_date: str,
):
    driver_set = {str(driver).strip() for driver in (driver_candidates or []) if str(driver).strip()}

    if len(assignment_df) > 0 and "assigned_driver" in assignment_df.columns:
        current_drivers = assignment_df["assigned_driver"].fillna("").astype(str).str.strip().tolist()
        driver_set.update([driver for driver in current_drivers if driver and driver != "미배정"])

    if len(history_df) > 0 and "driver" in history_df.columns:
        hist_drivers = history_df["driver"].fillna("").astype(str).str.strip().tolist()
        driver_set.update([driver for driver in hist_drivers if driver])

    drivers = sorted(driver_set)
    if len(drivers) == 0:
        return pd.DataFrame(columns=DRIVER_STATS_COLUMNS), None

    hist = history_df.copy()
    if len(hist) == 0:
        hist = empty_assignment_history_df()

    hist["date_dt"] = pd.to_datetime(hist["date"], errors="coerce").dt.normalize()
    hist = hist[hist["date_dt"].notna()].copy()
    hist["driver"] = hist["driver"].fillna("").astype(str).str.strip()
    hist = hist[hist["driver"] != ""].copy()
    hist["total_qty"] = pd.to_numeric(hist["total_qty"], errors="coerce").fillna(0)

    base_dt = pd.to_datetime(base_date, errors="coerce")
    if pd.isna(base_dt):
        base_dt = pd.Timestamp.now().normalize()
    else:
        base_dt = base_dt.normalize()

    today_hist = hist[hist["date_dt"] == base_dt].copy()
    today_qty_map = today_hist.groupby("driver")["total_qty"].sum().to_dict() if len(today_hist) > 0 else {}

    hist_before_today = hist[hist["date_dt"] < base_dt].copy()
    common_recent_date = hist_before_today["date_dt"].max() if len(hist_before_today) > 0 else pd.NaT
    recent_qty_map = {}
    if not pd.isna(common_recent_date):
        recent_day_hist = hist_before_today[hist_before_today["date_dt"] == common_recent_date].copy()
        recent_qty_map = recent_day_hist.groupby("driver")["total_qty"].sum().to_dict()

    w7_start = base_dt - pd.Timedelta(days=6)
    w30_start = base_dt - pd.Timedelta(days=29)
    q7, d7 = _build_workday_avg_qty(hist, w7_start, base_dt)
    q30, d30 = _build_workday_avg_qty(hist, w30_start, base_dt)

    rows = []
    for driver in drivers:
        v_today = safe_int(today_qty_map.get(driver, 0))
        v_recent = safe_int(recent_qty_map.get(driver, 0))
        v_q7 = safe_int(q7.get(driver, 0))
        v_d7 = safe_int(d7.get(driver, 0))
        v_q30 = safe_int(q30.get(driver, 0))
        v_d30 = safe_int(d30.get(driver, 0))
        avg_q7 = (v_q7 / v_d7) if v_d7 > 0 else 0
        avg_q30 = (v_q30 / v_d30) if v_d30 > 0 else 0
        rows.append(
            {
                "기사명": driver,
                "오늘물량": v_today,
                "최근근무일(1일) 물량": v_recent,
                "7일 근무일평균": round(avg_q7, 1),
                "30일 근무일평균": round(avg_q30, 1),
            }
        )

    recent_date_str = common_recent_date.strftime("%Y-%m-%d") if not pd.isna(common_recent_date) else None
    return (
        pd.DataFrame(rows)
        .sort_values(["오늘물량", "기사명"], ascending=[False, True])
        .reset_index(drop=True),
        recent_date_str,
    )
