"""Export and share payload helpers."""

from __future__ import annotations

import json
import os
import re

import pandas as pd


SHARE_DIR = "shared_payloads"


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


def _coords_to_jsonable(coords):
    normalized = _normalize_coords(coords)
    if normalized is None:
        return ""
    return [normalized[0], normalized[1]]


def df_to_records_for_json(df: pd.DataFrame):
    if df is None or len(df) == 0:
        return []

    out = []
    for row in df.fillna("").to_dict(orient="records"):
        row_copy = row.copy()
        if "coords" in row_copy:
            row_copy["coords"] = _coords_to_jsonable(row_copy.get("coords"))
        out.append(row_copy)
    return out


def records_to_df_with_coords(records):
    df = pd.DataFrame(records or [])
    if len(df) == 0:
        return df

    if "coords" in df.columns:
        df["coords"] = df["coords"].apply(_normalize_coords)
    return df


def build_shared_delivery_frames(
    result_delivery: pd.DataFrame,
    grouped_delivery: pd.DataFrame,
    route_driver_map: dict,
    group_route_map: dict,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    shared_result_delivery = result_delivery.copy()
    shared_grouped_delivery = grouped_delivery.copy()

    shared_result_delivery["assigned_driver"] = shared_result_delivery["route"].map(route_driver_map).fillna("")
    shared_grouped_delivery["assigned_driver"] = shared_grouped_delivery["route"].map(route_driver_map).fillna("")
    shared_result_delivery["추천그룹"] = shared_result_delivery["route"].map(group_route_map).fillna("")
    shared_grouped_delivery["추천그룹"] = shared_grouped_delivery["route"].map(group_route_map).fillna("")
    return shared_result_delivery, shared_grouped_delivery


def build_share_payload_data(
    map_html: str,
    assignment_df: pd.DataFrame,
    assigned_summary: pd.DataFrame,
    result_delivery_df: pd.DataFrame = None,
    grouped_delivery_df: pd.DataFrame = None,
    route_prefix_map: dict = None,
    truck_request_map: dict = None,
    route_line_label: dict = None,
    route_camp_map: dict = None,
    camp_coords: dict = None,
    group_assignment_df: pd.DataFrame = None,
) -> dict:
    return {
        "map_html": map_html,
        "assignment_rows": assignment_df.fillna("").to_dict(orient="records"),
        "assigned_summary_rows": assigned_summary.fillna("").to_dict(orient="records"),
        "result_delivery_rows": df_to_records_for_json(result_delivery_df) if result_delivery_df is not None else [],
        "grouped_delivery_rows": df_to_records_for_json(grouped_delivery_df) if grouped_delivery_df is not None else [],
        "route_prefix_map": route_prefix_map or {},
        "truck_request_map": truck_request_map or {},
        "route_line_label": route_line_label or {},
        "route_camp_map": route_camp_map or {},
        "camp_coords": camp_coords or {},
        "group_assignment_rows": group_assignment_df.fillna("").to_dict(orient="records") if group_assignment_df is not None else [],
    }


def save_share_payload(
    share_name: str,
    map_html: str,
    assignment_df: pd.DataFrame,
    assigned_summary: pd.DataFrame,
    result_delivery_df: pd.DataFrame = None,
    grouped_delivery_df: pd.DataFrame = None,
    route_prefix_map: dict = None,
    truck_request_map: dict = None,
    route_line_label: dict = None,
    route_camp_map: dict = None,
    camp_coords: dict = None,
    group_assignment_df: pd.DataFrame = None,
    share_dir: str = SHARE_DIR,
) -> None:
    os.makedirs(share_dir, exist_ok=True)
    payload_path = os.path.join(share_dir, f"{share_name}.json")
    payload = build_share_payload_data(
        map_html=map_html,
        assignment_df=assignment_df,
        assigned_summary=assigned_summary,
        result_delivery_df=result_delivery_df,
        grouped_delivery_df=grouped_delivery_df,
        route_prefix_map=route_prefix_map,
        truck_request_map=truck_request_map,
        route_line_label=route_line_label,
        route_camp_map=route_camp_map,
        camp_coords=camp_coords,
        group_assignment_df=group_assignment_df,
    )
    with open(payload_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)


def load_share_payload(share_name: str, share_dir: str = SHARE_DIR):
    payload_path = os.path.join(share_dir, f"{share_name}.json")
    if not os.path.exists(payload_path):
        return None

    try:
        with open(payload_path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return None


def build_snapshot_payload(
    map_html: str,
    assignment_df: pd.DataFrame,
    assigned_summary: pd.DataFrame,
    result_delivery_df: pd.DataFrame = None,
    grouped_delivery_df: pd.DataFrame = None,
    route_prefix_map: dict = None,
    truck_request_map: dict = None,
    route_line_label: dict = None,
    route_camp_map: dict = None,
    camp_coords: dict = None,
    group_assignment_df: pd.DataFrame = None,
    selected_filter: str = "전체",
    recommended_group_map: dict = None,
    recommended_group_count: int = 0,
    selected_group_filter: str = "전체",
) -> dict:
    payload = build_share_payload_data(
        map_html=map_html,
        assignment_df=assignment_df,
        assigned_summary=assigned_summary,
        result_delivery_df=result_delivery_df,
        grouped_delivery_df=grouped_delivery_df,
        route_prefix_map=route_prefix_map,
        truck_request_map=truck_request_map,
        route_line_label=route_line_label,
        route_camp_map=route_camp_map,
        camp_coords=camp_coords,
        group_assignment_df=group_assignment_df,
    )
    payload.update(
        {
            "selected_filter": selected_filter,
            "recommended_group_map": recommended_group_map or {},
            "recommended_group_count": recommended_group_count,
            "selected_group_filter": selected_group_filter,
        }
    )
    return payload


def _natural_desc_sort_key(value):
    text = str(value).strip()
    return [int(token) if token.isdigit() else token.lower() for token in re.split(r"(\d+)", text)]


def build_assignment_export_df(assignment_df: pd.DataFrame) -> pd.DataFrame:
    export_df = assignment_df.copy()
    if "truck_request_id" not in export_df.columns:
        export_df["truck_request_id"] = ""
    if "assigned_driver" not in export_df.columns:
        export_df["assigned_driver"] = ""

    export_df["truck_request_id"] = export_df["truck_request_id"].fillna("").astype(str).str.strip()
    export_df["assigned_driver"] = export_df["assigned_driver"].fillna("").astype(str).str.strip()
    export_df = export_df[["truck_request_id", "assigned_driver"]].copy()
    export_df = export_df.sort_values(
        by="truck_request_id",
        key=lambda series: series.map(_natural_desc_sort_key),
        ascending=False,
    )
    return export_df.rename(columns={"truck_request_id": "트럭요청ID", "assigned_driver": "이름"})


def build_assignment_export_csv_data(assignment_df: pd.DataFrame) -> bytes:
    export_df = build_assignment_export_df(assignment_df)
    return export_df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
