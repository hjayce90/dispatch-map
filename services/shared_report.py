"""Shared report data preparation helpers."""

from __future__ import annotations

import pandas as pd


def safe_int(value) -> int:
    try:
        if pd.isna(value):
            return 0
        return int(float(value))
    except Exception:
        return 0


def _normalize_coords(coords):
    if isinstance(coords, str):
        text = coords.strip()
        if text == "":
            return None
        text = text.strip("()[]")
        parts = [part.strip() for part in text.split(",")]
        if len(parts) == 2:
            coords = parts

    if not (isinstance(coords, (list, tuple)) and len(coords) == 2):
        return None

    try:
        lat = float(coords[0])
        lon = float(coords[1])
    except (TypeError, ValueError):
        return None

    if any(pd.isna(value) for value in [lat, lon]):
        return None

    return (lat, lon)


def _sanitize_coords_column(df: pd.DataFrame, log_label: str = ""):
    if len(df) == 0 or "coords" not in df.columns:
        return df.copy(), 0

    out = df.copy()
    out["coords"] = out["coords"].apply(_normalize_coords)
    invalid_count = safe_int(out["coords"].isna().sum())
    return out, invalid_count


def filter_shared_view(
    result_df: pd.DataFrame,
    grouped_df: pd.DataFrame,
    mode: str,
    selected_driver: str,
    selected_group: str,
):
    result2 = result_df.copy()
    grouped2 = grouped_df.copy()

    if mode == "기사별 보기" and str(selected_driver).strip() != "":
        result2 = result2[result2["assigned_driver"].fillna("").astype(str) == str(selected_driver)].copy()
        grouped2 = grouped2[grouped2["assigned_driver"].fillna("").astype(str) == str(selected_driver)].copy()
    elif mode == "추천그룹별 보기" and str(selected_group).strip() != "":
        result2 = result2[result2["추천그룹"].fillna("").astype(str) == str(selected_group)].copy()
        grouped2 = grouped2[grouped2["추천그룹"].fillna("").astype(str) == str(selected_group)].copy()

    result2, _ = _sanitize_coords_column(result2, log_label="shared_result_filter")
    grouped2, _ = _sanitize_coords_column(grouped2, log_label="shared_grouped_filter")

    result2 = result2[result2["coords"].notna()].copy() if "coords" in result2.columns else pd.DataFrame()
    grouped2 = grouped2[grouped2["coords"].notna()].copy() if "coords" in grouped2.columns else pd.DataFrame()
    return result2, grouped2


def build_shared_summary(result_df: pd.DataFrame, grouped_df: pd.DataFrame):
    route_count = safe_int(result_df["route"].nunique()) if len(result_df) > 0 and "route" in result_df.columns else 0
    driver_count = safe_int(
        result_df["assigned_driver"].fillna("").astype(str).str.strip().replace("", pd.NA).dropna().nunique()
    ) if len(result_df) > 0 and "assigned_driver" in result_df.columns else 0
    group_count = safe_int(
        result_df["추천그룹"].fillna("").astype(str).str.strip().replace("", pd.NA).dropna().nunique()
    ) if len(result_df) > 0 and "추천그룹" in result_df.columns else 0
    small_box_total = 0
    medium_box_total = 0
    large_box_total = 0
    box_total = 0
    if len(grouped_df) > 0:
        small_box_total = safe_int(grouped_df.get("ae_sum", pd.Series(dtype=float)).sum())
        medium_box_total = safe_int(grouped_df.get("af_sum", pd.Series(dtype=float)).sum())
        large_box_total = safe_int(grouped_df.get("ag_sum", pd.Series(dtype=float)).sum())
        box_total = small_box_total + medium_box_total + large_box_total

    overlap_count = 0
    if len(grouped_df) > 0 and "coords" in grouped_df.columns:
        key_series = grouped_df["coords"].apply(
            lambda coords: f"{round(float(coords[0]), 6)}_{round(float(coords[1]), 6)}"
            if isinstance(coords, (tuple, list)) and len(coords) == 2
            else ""
        )
        overlap_count = safe_int((key_series.value_counts() > 1).sum())

    return route_count, driver_count, group_count, small_box_total, medium_box_total, large_box_total, box_total, overlap_count


def build_driver_overview_df(result_df: pd.DataFrame, grouped_df: pd.DataFrame):
    if len(result_df) == 0:
        return pd.DataFrame()

    assigned = result_df.copy()
    assigned["assigned_driver"] = assigned.get("assigned_driver", "").fillna("").astype(str).str.strip()
    assigned = assigned[assigned["assigned_driver"] != ""].copy()
    if len(assigned) == 0:
        return pd.DataFrame()

    route_driver_df = (
        assigned[["route", "assigned_driver"]]
        .dropna(subset=["route"])
        .drop_duplicates(subset=["route"])
    )

    grouped2 = grouped_df.copy()
    if len(grouped2) == 0:
        grouped2 = pd.DataFrame(columns=["route", "assigned_driver", "ae_sum", "af_sum", "ag_sum"])
    elif "assigned_driver" not in grouped2.columns:
        grouped2 = grouped2.merge(route_driver_df, on="route", how="left")

    grouped2["assigned_driver"] = grouped2.get("assigned_driver", "").fillna("").astype(str).str.strip()
    grouped2 = grouped2[grouped2["assigned_driver"] != ""].copy()

    box_summary = (
        grouped2.groupby("assigned_driver", as_index=False)
        .agg(
            소형합=("ae_sum", "sum"),
            중형합=("af_sum", "sum"),
            대형합=("ag_sum", "sum"),
        )
    ) if len(grouped2) > 0 else pd.DataFrame(columns=["assigned_driver", "소형합", "중형합", "대형합"])

    time_summary = (
        assigned.groupby("assigned_driver", as_index=False)
        .agg(
            총걸린분=("time_minutes", "max"),
            route_count=("route", "nunique"),
        )
    )

    out = time_summary.merge(box_summary, on="assigned_driver", how="left")
    for col in ["소형합", "중형합", "대형합", "총걸린분", "route_count"]:
        out[col] = pd.to_numeric(out.get(col, 0), errors="coerce").fillna(0).astype(int)

    out["총박스"] = out["소형합"] + out["중형합"] + out["대형합"]
    return out.sort_values(["총박스", "총걸린분", "assigned_driver"], ascending=[False, False, True]).reset_index(drop=True)


def build_camp_driver_summary_df(
    result_df: pd.DataFrame,
    grouped_df: pd.DataFrame,
    route_camp_map: dict,
    camp_info: dict,
):
    empty_df = pd.DataFrame(columns=["camp_name", "assigned_driver", "회전수", "총박스"])
    if len(result_df) == 0:
        return empty_df

    assigned = result_df.copy()
    assigned["assigned_driver"] = assigned.get("assigned_driver", "").fillna("").astype(str).str.strip()
    assigned = assigned[assigned["assigned_driver"] != ""].copy()
    if len(assigned) == 0:
        return empty_df

    route_driver_df = (
        assigned[["route", "assigned_driver"]]
        .dropna(subset=["route"])
        .drop_duplicates(subset=["route"])
    )

    grouped2 = grouped_df.copy()
    if len(grouped2) == 0:
        return empty_df

    grouped2 = grouped2.merge(route_driver_df, on="route", how="left", suffixes=("", "_route"))
    if "assigned_driver_route" in grouped2.columns:
        grouped2["assigned_driver"] = grouped2.get("assigned_driver_route", "").fillna(grouped2.get("assigned_driver", ""))
        grouped2 = grouped2.drop(columns=["assigned_driver_route"])

    grouped2["assigned_driver"] = grouped2.get("assigned_driver", "").fillna("").astype(str).str.strip()
    grouped2 = grouped2[grouped2["assigned_driver"] != ""].copy()
    if len(grouped2) == 0:
        return empty_df

    grouped2["camp_code"] = grouped2["route"].map(route_camp_map).fillna(grouped2.get("camp_code", ""))
    grouped2["camp_name"] = grouped2["camp_code"].map(lambda code: camp_info.get(code, {}).get("camp_name", ""))
    grouped2["camp_name"] = grouped2["camp_name"].fillna(grouped2.get("camp_name", "")).astype(str).str.strip()
    grouped2 = grouped2[grouped2["camp_name"] != ""].copy()
    if len(grouped2) == 0:
        return empty_df

    summary_df = (
        grouped2.groupby(["camp_name", "assigned_driver"], as_index=False)
        .agg(
            회전수=("route", "nunique"),
            소형합=("ae_sum", "sum"),
            중형합=("af_sum", "sum"),
            대형합=("ag_sum", "sum"),
        )
    )
    for col in ["회전수", "소형합", "중형합", "대형합"]:
        summary_df[col] = pd.to_numeric(summary_df[col], errors="coerce").fillna(0).astype(int)
    summary_df["총박스"] = summary_df["소형합"] + summary_df["중형합"] + summary_df["대형합"]

    camp_order = [value["camp_name"] for value in camp_info.values()]
    summary_df["camp_order"] = summary_df["camp_name"].apply(
        lambda name: camp_order.index(name) if name in camp_order else len(camp_order)
    )
    summary_df = summary_df.sort_values(
        ["camp_order", "camp_name", "총박스", "회전수", "assigned_driver"],
        ascending=[True, True, False, False, True],
    ).reset_index(drop=True)
    return summary_df[["camp_name", "assigned_driver", "회전수", "총박스"]]
