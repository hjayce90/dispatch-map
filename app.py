import os
import re
import json
import math
import hashlib
import logging
import html
import subprocess
from io import BytesIO
from pathlib import Path

import requests
import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import folium
from folium.features import DivIcon
from folium.plugins import OverlappingMarkerSpiderfier
from streamlit_folium import st_folium

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

from auto_grouping import (
    DRIVER_PREFERENCE_COLUMNS,
    apply_group_edit_map,
    build_group_assignment_df,
    build_group_map_data,
    build_group_summary_df,
    build_group_detail_stats_df,
    build_driver_preference_df,
    build_route_feature_df,
    choose_auto_group_count,
    default_group_edit_map,
    filter_group_map_for_routes,
    has_complete_group_map,
    recommend_route_groups,
    resolve_group_count,
)
from assign_input_builder import build_assign_input_df, default_order_date
from services.report_xlsx import (
    TEMPLATE_REPORT_XLSX,
    build_report_export_df,
    build_report_export_filename,
    build_report_export_payload,
    make_report_xlsx_bytes,
)

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
logger = logging.getLogger(__name__)
RECOMMENDATION_STATE_KEYS = (
    "recommended_groups_result",
    "recommended_groups_meta",
    "recommended_groups_error",
    "recommended_groups_inputs_hash",
    "recommended_groups_status",
    "recommended_groups_assignment_df",
    "recommended_groups_selected_filter",
    "recommended_groups_dataset_key",
)
DRIVER_PREFERENCE_DISPLAY_COLUMNS = [
    "그룹평균퍼짐km" if column == "route_spread_km" else column
    for column in DRIVER_PREFERENCE_COLUMNS
]


def get_telegram_config():
    token = str(os.getenv("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN)).strip()
    chat_id = str(os.getenv("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID)).strip()
    return token, chat_id


def is_telegram_configured():
    token, chat_id = get_telegram_config()
    return bool(token and chat_id)


def load_local_env(env_path: Path = ENV_FILE):
    if load_dotenv is not None:
        load_dotenv(dotenv_path=env_path, override=False)
        return
    if not env_path.exists():
        return

    try:
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                logger.warning("Skipping malformed .env line: %s", raw_line)
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key:
                logger.warning("Skipping malformed .env line with empty key.")
                continue
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            os.environ.setdefault(key, value)
    except OSError:
        logger.exception("Failed to read local .env file: %s", env_path)


load_local_env()

st.set_page_config(page_title="배차 지도", layout="wide")
st.title("배차 지도")

# =========================
# 설정값
# =========================
KAKAO_API_KEY = os.getenv("KAKAO_API_KEY", "080375c5f09bbb8c7db9f368bc752d33")

TIME_COL_INDEX = 21   # V열
CACHE_FILE = "geocode_cache.csv"
DRIVER_FILE = "drivers.csv"
ASSIGNMENT_HISTORY_FILE = "assignment_history.csv"
MAP_DIR = "saved_maps"
SHARE_DIR = "shared_payloads"
ASSIGNMENT_FILE = "route_assignments.json"
ASSIGNMENT_PROGRESS_FILE = "assignment_progress.json"
ASSIGNMENT_PROGRESS_NOTIFY_EVERY = 5
MANUAL_LOCATION_MAPPING_CSV = "manual_location_mappings.csv"
MANUAL_LOCATION_MAPPING_JSON = "manual_location_mappings.json"
CANCEL_FILE = "cancel_management.json"
APP_URL = "https://dispatch-map.streamlit.app"  # 본인 배포 주소로 수정
DJANGO_API_BASE_URL = os.getenv("DJANGO_API_BASE_URL", "http://127.0.0.1:8010/api/dispatch")
DJANGO_API_READ_TIMEOUT = 5
DJANGO_API_TIMEOUT = 5
DJANGO_PUBLIC_BASE_URL = os.getenv("DJANGO_PUBLIC_BASE_URL", "https://sales.nasilfamily.com")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
REQUESTS_SESSION = requests.Session()
REQUESTS_SESSION.trust_env = False

REPORT_CANCEL_VISIBLE_COLUMNS = [
    "assigned_driver",
    "company_name",
    "origin_center",
    "milkrun_no",
    "총수량",
    "취소수량",
    "취소사유",
]
REPORT_CANCEL_DISPLAY_RENAME = {"assigned_driver": "이름"}
REPORT_CANCEL_EDITABLE_COLUMNS = {"취소수량", "취소사유"}
DEFAULT_MAP_CENTER = [37.55, 126.98]
DEFAULT_MAP_ZOOM = 10

# 고정 캠프 정보
CAMP_INFO = {
    "SPU일산1": {
        "camp_name": "일산1캠",
        "address": "경기도 파주시 능안로231번길 87 지하 1층",
        "icon_type": "square",
        "icon_text": "1",
    },
    "SPU일산7": {
        "camp_name": "일산7캠",
        "address": "경기 파주시 조리읍 대원리 296-8, 4층 일산7캠프",
        "icon_type": "diamond",
        "icon_text": "7",
    }
}

CENTER_CODES = set(CAMP_INFO.keys())

ROUTE_COLORS = [
    "#e53935", "#1e88e5", "#43a047", "#8e24aa",
    "#fb8c00", "#3949ab", "#00897b", "#6d4c41",
    "#546e7a", "#d81b60", "#5e35b1", "#039be5",
    "#7cb342", "#f4511e", "#00acc1", "#c0ca33",
]

PIN_NORMAL_SCALE = 0.8
PIN_EDGE_SCALE = 1.2
PIN_NORMAL_BORDER_COLOR = "#ffffff"
PIN_EDGE_BORDER_COLOR = "#111111"

os.makedirs(MAP_DIR, exist_ok=True)
os.makedirs(SHARE_DIR, exist_ok=True)

uploaded_file = None

def _normalize_coords(coords):
    if isinstance(coords, str):
        text = coords.strip()
        if text == "":
            return None
        text = text.strip("()[]")
        parts = [p.strip() for p in text.split(",")]
        if len(parts) == 2:
            coords = parts

    if not (isinstance(coords, (list, tuple)) and len(coords) == 2):
        return None

    try:
        lat = float(coords[0])
        lon = float(coords[1])
    except (TypeError, ValueError):
        return None

    if any(pd.isna(v) for v in [lat, lon]):
        return None

    return (lat, lon)


def _sanitize_coords_column(df: pd.DataFrame, log_label: str = ""):
    if len(df) == 0 or "coords" not in df.columns:
        return df.copy(), 0

    out = df.copy()
    out["coords"] = out["coords"].apply(_normalize_coords)
    invalid_count = safe_int(out["coords"].isna().sum())
    if invalid_count > 0 and log_label:
        logger.debug("[%s] invalid coords rows skipped: %s", log_label, invalid_count)
    return out, invalid_count


def _coords_to_jsonable(coords):
    normalized = _normalize_coords(coords)
    if normalized is None:
        return ""
    return [normalized[0], normalized[1]]

# =========================
# 공통 함수
# =========================
def safe_int(v):
    try:
        if pd.isna(v):
            return 0
        return int(float(v))
    except Exception:
        return 0


def safe_bool(v):
    try:
        if pd.isna(v):
            return False
    except Exception:
        pass

    if isinstance(v, str):
        return v.strip().lower() not in {"", "0", "false", "n", "no", "off"}
    return bool(v)


def coerce_map_center(center):
    if isinstance(center, dict):
        lat = center.get("lat")
        lon = center.get("lng", center.get("lon"))
    elif isinstance(center, (list, tuple)) and len(center) >= 2:
        lat, lon = center[0], center[1]
    else:
        return None

    try:
        if pd.isna(lat) or pd.isna(lon):
            return None
        return [float(lat), float(lon)]
    except Exception:
        return None


def resolve_map_center(valid_result: pd.DataFrame = None, camp_coords: dict = None):
    center = DEFAULT_MAP_CENTER.copy()

    for camp_code in CAMP_INFO.keys():
        coords = _normalize_coords((camp_coords or {}).get(camp_code))
        if coords:
            return [float(coords[0]), float(coords[1])]

    if isinstance(valid_result, pd.DataFrame) and len(valid_result) > 0 and "coords" in valid_result.columns:
        coords = _normalize_coords(valid_result.iloc[0].get("coords"))
        if coords:
            center = [float(coords[0]), float(coords[1])]

    return center


def build_base_map(center=None, zoom=DEFAULT_MAP_ZOOM, use_spiderfier: bool = False):
    map_center = coerce_map_center(center) or DEFAULT_MAP_CENTER.copy()
    map_zoom = safe_int(zoom) or DEFAULT_MAP_ZOOM
    base_map = folium.Map(
        location=map_center,
        tiles="CartoDB positron",
        zoom_start=map_zoom,
        prefer_canvas=True,
    )
    if use_spiderfier:
        OverlappingMarkerSpiderfier(nearby_distance=24, keep_spiderfied=True).add_to(base_map)
    return base_map


def ensure_map_view_state(dataset_key: str, center_key: str, zoom_key: str, dataset_state_key: str, default_center):
    resolved_center = coerce_map_center(default_center) or DEFAULT_MAP_CENTER.copy()
    if st.session_state.get(dataset_state_key) != dataset_key:
        st.session_state[center_key] = resolved_center
        st.session_state[zoom_key] = DEFAULT_MAP_ZOOM
        st.session_state[dataset_state_key] = dataset_key
        return

    if coerce_map_center(st.session_state.get(center_key)) is None:
        st.session_state[center_key] = resolved_center
    if safe_int(st.session_state.get(zoom_key, 0)) <= 0:
        st.session_state[zoom_key] = DEFAULT_MAP_ZOOM


def sync_map_view_state_from_return(map_state, center_key: str, zoom_key: str):
    if not isinstance(map_state, dict):
        return

    center = coerce_map_center(map_state.get("center"))
    if center is not None:
        st.session_state[center_key] = center

    zoom = safe_int(map_state.get("zoom", 0))
    if zoom > 0:
        st.session_state[zoom_key] = zoom


def render_stable_folium_map(
    overlay_layers,
    key: str,
    center_key: str,
    zoom_key: str,
    height: int,
    fallback_flag_key: str,
    use_spiderfier: bool = False,
):
    center = coerce_map_center(st.session_state.get(center_key)) or DEFAULT_MAP_CENTER.copy()
    zoom = safe_int(st.session_state.get(zoom_key, DEFAULT_MAP_ZOOM)) or DEFAULT_MAP_ZOOM
    layers = overlay_layers or []
    dynamic_disabled = bool(st.session_state.get(fallback_flag_key, False))

    if not dynamic_disabled:
        base_map = build_base_map(center=center, zoom=zoom, use_spiderfier=use_spiderfier)
        try:
            map_state = st_folium(
                base_map,
                key=key,
                width=None,
                height=height,
                center=center,
                zoom=zoom,
                feature_group_to_add=layers,
                returned_objects=["center", "zoom"],
            )
            sync_map_view_state_from_return(map_state, center_key, zoom_key)
            return map_state
        except Exception as exc:
            logger.exception("Dynamic st_folium render failed for %s; switching to fallback.", key)
            st.session_state[fallback_flag_key] = True
            st.warning(f"지도 동적 갱신이 불안정해 fallback 렌더로 전환했습니다: {exc}")

    fallback_map = build_base_map(center=center, zoom=zoom, use_spiderfier=use_spiderfier)
    for layer in layers:
        layer.add_to(fallback_map)
    map_state = st_folium(
        fallback_map,
        key=key,
        width=None,
        height=height,
        center=center,
        zoom=zoom,
        returned_objects=["center", "zoom"],
    )
    sync_map_view_state_from_return(map_state, center_key, zoom_key)
    return map_state


def render_full_folium_map(
    overlay_layers,
    key: str,
    center_key: str,
    zoom_key: str,
    height: int,
    use_spiderfier: bool = False,
):
    center = coerce_map_center(st.session_state.get(center_key)) or DEFAULT_MAP_CENTER.copy()
    zoom = safe_int(st.session_state.get(zoom_key, DEFAULT_MAP_ZOOM)) or DEFAULT_MAP_ZOOM
    layers = overlay_layers or []

    full_map = build_base_map(center=center, zoom=zoom, use_spiderfier=use_spiderfier)
    for layer in layers:
        layer.add_to(full_map)

    map_state = st_folium(
        full_map,
        key=key,
        width=None,
        height=height,
        center=center,
        zoom=zoom,
        returned_objects=["center", "zoom"],
    )
    sync_map_view_state_from_return(map_state, center_key, zoom_key)
    return map_state


def build_main_overlay_cache_key(active_dataset_key: str, selected_filter: str, route_summary: pd.DataFrame, assignment_store: dict) -> str:
    assignment_store = assignment_store if isinstance(assignment_store, dict) else {}
    assignment_rows = []
    for _, row in route_summary.iterrows():
        route = str(row.get("route", "")).strip()
        truck_request_id = str(row.get("truck_request_id", "")).strip()
        if not route:
            continue
        assignment_key = make_assignment_key(route, truck_request_id)
        assignment_rows.append({
            "route": route,
            "truck_request_id": truck_request_id,
            "assigned_driver": str(assignment_store.get(assignment_key, "")).strip(),
        })
    payload = json.dumps(
        {
            "cache_version": "main_overlay_pickup_v1",
            "dataset": str(active_dataset_key or ""),
            "filter": str(selected_filter or ""),
            "manual_location_mapping": manual_location_mapping_fingerprint(),
            "assignment": sorted(assignment_rows, key=lambda r: (r["route"], r["truck_request_id"])),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def get_cached_main_map_data(cache_key: str):
    if st.session_state.get("main_map_data_cache_key") != cache_key:
        return None
    return {
        "valid_result": st.session_state.get("main_overlay_valid_result", pd.DataFrame()),
        "valid_grouped": st.session_state.get("main_overlay_valid_grouped", pd.DataFrame()),
        "route_driver_map": st.session_state.get("main_overlay_route_driver_map", {}),
        "unmapped_df": st.session_state.get("main_overlay_unmapped_df", pd.DataFrame()),
    }


def set_cached_main_map_data(cache_key: str, valid_result, valid_grouped, route_driver_map, unmapped_df=None):
    st.session_state["main_map_data_cache_key"] = cache_key
    st.session_state["main_overlay_valid_result"] = valid_result
    st.session_state["main_overlay_valid_grouped"] = valid_grouped
    st.session_state["main_overlay_route_driver_map"] = route_driver_map
    st.session_state["main_overlay_unmapped_df"] = unmapped_df if isinstance(unmapped_df, pd.DataFrame) else pd.DataFrame()


def normalize_address(addr: str) -> str:
    if pd.isna(addr):
        return ""
    text = str(addr).strip()
    text = re.sub(r"\s+", " ", text)
    return text


def clean_map_text(value) -> str:
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    text = str(value or "").strip()
    if text.lower() in {"nan", "none", "null"}:
        return ""
    return text


def safe_float_or_none(value):
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except Exception:
        return None


def manual_location_mapping_paths():
    return [
        BASE_DIR / MANUAL_LOCATION_MAPPING_CSV,
        BASE_DIR / MANUAL_LOCATION_MAPPING_JSON,
    ]


def manual_location_mapping_fingerprint() -> str:
    rows = []
    for path in manual_location_mapping_paths():
        if path.exists():
            try:
                stat = path.stat()
                rows.append({"path": path.name, "size": stat.st_size, "mtime": stat.st_mtime})
            except OSError:
                rows.append({"path": path.name, "size": -1, "mtime": -1})
    payload = json.dumps(rows, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _manual_mapping_enabled(value) -> bool:
    text = clean_map_text(value).lower()
    if text == "":
        return True
    return text not in {"0", "false", "n", "no", "off", "disabled", "사용안함", "비활성"}


def _normalize_manual_mapping_row(row: dict) -> dict:
    raw_address = clean_map_text(
        row.get("raw_address", "")
        or row.get("address", "")
        or row.get("주소", "")
    )
    normalized_address = clean_map_text(
        row.get("normalized_address", "")
        or row.get("address_norm", "")
        or row.get("주소정규화", "")
    )
    if not normalized_address and raw_address:
        normalized_address = normalize_address(raw_address)

    lat = safe_float_or_none(row.get("lat", row.get("latitude", row.get("위도", ""))))
    lng = safe_float_or_none(row.get("lng", row.get("lon", row.get("longitude", row.get("경도", "")))))
    if lat is None or lng is None:
        return {}
    if not _manual_mapping_enabled(row.get("enabled", row.get("사용", ""))):
        return {}

    return {
        "company_id": clean_map_text(row.get("company_id", row.get("업체ID", ""))),
        "company_name": clean_map_text(row.get("company_name", row.get("업체명", ""))),
        "raw_address": raw_address,
        "normalized_address": normalized_address,
        "coords": (lat, lng),
    }


def load_manual_location_mappings() -> list:
    mappings = []
    csv_path = BASE_DIR / MANUAL_LOCATION_MAPPING_CSV
    json_path = BASE_DIR / MANUAL_LOCATION_MAPPING_JSON

    if csv_path.exists():
        try:
            csv_df = pd.read_csv(csv_path, dtype=str).fillna("")
            for row in csv_df.to_dict(orient="records"):
                normalized = _normalize_manual_mapping_row(row)
                if normalized:
                    mappings.append(normalized)
        except Exception as exc:
            logger.warning("Failed to load manual location mapping CSV: %s", exc)

    if json_path.exists():
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            if isinstance(payload, dict):
                rows = payload.get("rows") or payload.get("mappings") or list(payload.values())
            elif isinstance(payload, list):
                rows = payload
            else:
                rows = []
            for row in rows:
                if isinstance(row, dict):
                    normalized = _normalize_manual_mapping_row(row)
                    if normalized:
                        mappings.append(normalized)
        except Exception as exc:
            logger.warning("Failed to load manual location mapping JSON: %s", exc)

    return mappings


def find_manual_location_mapping(row, mappings: list):
    if not mappings:
        return None

    company_id = clean_map_text(row.get("company_id", ""))
    company_name = clean_map_text(row.get("company_name", ""))
    raw_address = clean_map_text(row.get("address", ""))
    address_norm = clean_map_text(row.get("address_norm", "")) or normalize_address(raw_address)
    raw_address_norm = normalize_address(raw_address)

    for mapping in mappings:
        mapping_company_id = clean_map_text(mapping.get("company_id", ""))
        if not company_id or mapping_company_id != company_id:
            continue
        mapping_norm = clean_map_text(mapping.get("normalized_address", ""))
        mapping_raw_norm = normalize_address(mapping.get("raw_address", ""))
        if not mapping_norm and not mapping_raw_norm:
            return mapping
        if address_norm and mapping_norm == address_norm:
            return mapping
        if raw_address_norm and mapping_raw_norm == raw_address_norm:
            return mapping

    for mapping in mappings:
        mapping_company_name = clean_map_text(mapping.get("company_name", ""))
        if not company_name or mapping_company_name != company_name:
            continue
        mapping_norm = clean_map_text(mapping.get("normalized_address", ""))
        mapping_raw_norm = normalize_address(mapping.get("raw_address", ""))
        if address_norm and mapping_norm == address_norm:
            return mapping
        if raw_address_norm and mapping_raw_norm == raw_address_norm:
            return mapping

    return None


def map_unmapped_reason(row) -> str:
    address_norm = clean_map_text(row.get("address_norm", ""))
    address = clean_map_text(row.get("address", ""))
    if not address_norm:
        return "address_norm missing"
    if not address:
        return "coords missing"
    return "geocode failed; manual mapping not found"


def apply_manual_location_mappings(df: pd.DataFrame, mappings: list, collect_unmapped: bool = False):
    if not isinstance(df, pd.DataFrame) or len(df) == 0:
        return pd.DataFrame() if not isinstance(df, pd.DataFrame) else df.copy(), pd.DataFrame()

    out = df.copy()
    if "coords" not in out.columns:
        out["coords"] = None
    out["coords"] = out["coords"].apply(_normalize_coords)
    out["manual_coords_applied"] = False

    missing_rows = []
    for idx, row in out.iterrows():
        if _normalize_coords(row.get("coords")) is not None:
            continue

        mapping = find_manual_location_mapping(row, mappings)
        if mapping:
            out.at[idx, "coords"] = mapping["coords"]
            out.at[idx, "manual_coords_applied"] = True
            continue

        if collect_unmapped:
            missing_rows.append({
                "route": clean_map_text(row.get("route", "")),
                "truck_request_id": clean_map_text(row.get("truck_request_id", "")),
                "company_id": clean_map_text(row.get("company_id", "")),
                "company_name": clean_map_text(row.get("company_name", "")),
                "address": clean_map_text(row.get("address", "")),
                "address_norm": clean_map_text(row.get("address_norm", "")),
                "missing_reason": map_unmapped_reason(row),
            })

    missing_df = pd.DataFrame(missing_rows)
    if len(missing_df) > 0:
        missing_df = missing_df.drop_duplicates(
            subset=[c for c in ["route", "company_id", "company_name", "address_norm", "address"] if c in missing_df.columns]
        ).reset_index(drop=True)
    return out, missing_df


def shorten_company_name_for_tooltip(company_name: str, max_len: int = 10) -> str:
    text = clean_map_text(company_name) or "-"
    for token in ["주식회사", "(주)", "㈜", "유한회사"]:
        text = text.replace(token, "").strip()
    for suffix in ["물류센터", "물류"]:
        if text.endswith(suffix) and len(text) > len(suffix):
            text = text[: -len(suffix)].strip()
    if len(text) > max_len:
        return f"{text[:max_len]}..."
    return text


def extract_base_name(filename: str) -> str:
    name = os.path.splitext(str(filename))[0]
    name = re.sub(r"[^\w\-가-힣]+", "_", name)
    return name.strip("_") or "dispatch_map"


def build_route_dataset_key(uploaded_filename: str, base_date: str, route_summary: pd.DataFrame) -> str:
    normalized_rows = []
    if isinstance(route_summary, pd.DataFrame) and len(route_summary) > 0:
        normalized_route_summary = route_summary.copy()
        sort_columns = [column for column in ["route", "truck_request_id", "route_prefix"] if column in normalized_route_summary.columns]
        if sort_columns:
            normalized_route_summary = normalized_route_summary.sort_values(sort_columns).reset_index(drop=True)
        normalized_rows = (
            normalized_route_summary.fillna("").astype(str).to_dict(orient="records")
        )

    payload = json.dumps(
        {
            "uploaded_filename": str(uploaded_filename or ""),
            "base_date": str(base_date or ""),
            "route_summary_rows": normalized_rows,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _copy_recommended_group_map(group_map) -> dict:
    if not isinstance(group_map, dict):
        return {}
    return {
        str(route).strip(): str(group_name).strip()
        for route, group_name in group_map.items()
        if str(route).strip() and str(group_name).strip()
    }


def _copy_recommended_groups_assignment_df(assignment_df) -> pd.DataFrame:
    if isinstance(assignment_df, pd.DataFrame):
        return assignment_df.copy(deep=True)
    if assignment_df:
        return pd.DataFrame(assignment_df).copy(deep=True)
    return pd.DataFrame()


def _infer_recommended_group_count(group_map: dict, fallback: int = 0) -> int:
    count = safe_int(fallback)
    if count > 0:
        return count

    max_group_number = 0
    for group_name in _copy_recommended_group_map(group_map).values():
        match = re.search(r"(\d+)$", str(group_name).strip())
        if match:
            max_group_number = max(max_group_number, safe_int(match.group(1)))
    return max_group_number


def build_recommended_groups_inputs_hash(
    route_feature_df: pd.DataFrame,
    group_count_mode: str,
    manual_group_count=None,
) -> str:
    normalized_rows = []
    if isinstance(route_feature_df, pd.DataFrame) and len(route_feature_df) > 0:
        work_df = route_feature_df.copy(deep=True)
        sort_columns = [column for column in ["route_prefix", "route", "truck_request_id"] if column in work_df.columns]
        if sort_columns:
            work_df = work_df.sort_values(sort_columns).reset_index(drop=True)
        normalized_rows = work_df.fillna("").astype(str).to_dict(orient="records")

    resolved_mode = str(group_count_mode or "자동").strip() or "자동"
    resolved_manual_count = safe_int(manual_group_count) if resolved_mode == "직접입력" else 0
    payload = json.dumps(
        {
            "route_feature_rows": normalized_rows,
            "group_count_mode": resolved_mode,
            "manual_group_count": resolved_manual_count,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def set_recommended_groups_state(
    group_map: dict,
    group_count: int,
    assignment_df: pd.DataFrame,
    inputs_hash: str,
    selected_filter: str = None,
    meta: dict = None,
):
    recommended_group_map = _copy_recommended_group_map(group_map)
    inferred_group_count = _infer_recommended_group_count(recommended_group_map, fallback=group_count)
    st.session_state["recommended_groups_result"] = recommended_group_map
    st.session_state["recommended_groups_meta"] = {
        **(meta or {}),
        "group_count": inferred_group_count,
        "generated_at": pd.Timestamp.now().isoformat(),
    }
    st.session_state["recommended_groups_assignment_df"] = _copy_recommended_groups_assignment_df(assignment_df)
    st.session_state["recommended_groups_inputs_hash"] = str(inputs_hash or "")
    st.session_state["recommended_groups_status"] = "active" if recommended_group_map else "inactive"
    st.session_state["recommended_groups_error"] = ""

    normalized_filter = str(selected_filter or st.session_state.get("recommended_groups_selected_filter", "전체")).strip() or "전체"
    st.session_state["recommended_groups_selected_filter"] = normalized_filter


def mark_recommended_groups_error(message: str):
    st.session_state["recommended_groups_error"] = str(message or "").strip()
    if not _copy_recommended_group_map(st.session_state.get("recommended_groups_result", {})):
        st.session_state["recommended_groups_status"] = "inactive"


def mark_recommended_groups_stale(message: str = "", status: str = "stale"):
    if status not in {"stale", "inactive"}:
        status = "stale"
    if st.session_state.get("recommended_groups_status") != status:
        st.session_state["recommended_groups_status"] = status
    if message:
        st.session_state["recommended_groups_meta"] = {
            **(st.session_state.get("recommended_groups_meta", {}) or {}),
            "status_message": str(message),
        }


def clear_recommendation_state():
    st.session_state["recommended_groups_error"] = ""
    if not _copy_recommended_group_map(st.session_state.get("recommended_groups_result", {})):
        st.session_state["recommended_groups_status"] = "inactive"


def sync_recommended_groups_status_for_inputs(inputs_hash: str):
    stored_hash = str(st.session_state.get("recommended_groups_inputs_hash", "") or "")
    if not stored_hash:
        if _copy_recommended_group_map(st.session_state.get("recommended_groups_result", {})):
            mark_recommended_groups_stale("추천 입력 조건을 확인할 수 없어 재계산이 필요합니다.")
        return
    if stored_hash != str(inputs_hash or ""):
        mark_recommended_groups_stale("추천 입력 조건이 바뀌어 재계산이 필요합니다.")


def get_recommended_groups_projection(
    route_feature_df: pd.DataFrame = None,
    inputs_hash: str = "",
) -> dict:
    empty_assignment_df = pd.DataFrame()
    group_map = _copy_recommended_group_map(st.session_state.get("recommended_groups_result", {}))
    meta = st.session_state.get("recommended_groups_meta", {}) or {}
    status = str(st.session_state.get("recommended_groups_status", "inactive") or "inactive")
    stored_hash = str(st.session_state.get("recommended_groups_inputs_hash", "") or "")
    is_current = bool(stored_hash and stored_hash == str(inputs_hash or ""))

    if status != "active" or not is_current or not group_map:
        return {
            "group_map": {},
            "group_count": 0,
            "assignment_df": empty_assignment_df,
            "selected_filter": "전체",
            "status": status,
            "error": str(st.session_state.get("recommended_groups_error", "") or ""),
            "is_current": is_current,
            "payload": {
                "recommended_group_map": {},
                "recommended_group_count": 0,
                "selected_group_filter": "전체",
                "group_assignment_rows": [],
            },
        }

    if isinstance(route_feature_df, pd.DataFrame):
        feature_df = route_feature_df.copy(deep=True)
        group_map = filter_group_map_for_routes(feature_df, group_map)
        if not has_complete_group_map(feature_df, group_map):
            return {
                "group_map": {},
                "group_count": 0,
                "assignment_df": empty_assignment_df,
                "selected_filter": "전체",
                "status": "stale",
                "error": str(st.session_state.get("recommended_groups_error", "") or ""),
                "is_current": False,
                "payload": {
                    "recommended_group_map": {},
                    "recommended_group_count": 0,
                    "selected_group_filter": "전체",
                    "group_assignment_rows": [],
                },
            }
    else:
        feature_df = None

    assignment_df = _copy_recommended_groups_assignment_df(
        st.session_state.get("recommended_groups_assignment_df", pd.DataFrame())
    )
    if isinstance(feature_df, pd.DataFrame) and (len(assignment_df) == 0 or "추천그룹" not in assignment_df.columns):
        assignment_df = build_group_assignment_df(feature_df.copy(deep=True), group_map)

    selected_filter = str(st.session_state.get("recommended_groups_selected_filter", "전체") or "전체").strip() or "전체"
    group_count = _infer_recommended_group_count(group_map, fallback=meta.get("group_count", 0))
    group_assignment_rows = assignment_df.fillna("").astype(str).to_dict(orient="records") if len(assignment_df) > 0 else []
    return {
        "group_map": group_map,
        "group_count": group_count,
        "assignment_df": assignment_df.copy(deep=True),
        "selected_filter": selected_filter,
        "status": status,
        "error": str(st.session_state.get("recommended_groups_error", "") or ""),
        "is_current": True,
        "payload": {
            "recommended_group_map": group_map.copy(),
            "recommended_group_count": group_count,
            "selected_group_filter": selected_filter,
            "group_assignment_rows": group_assignment_rows,
        },
    }


def sync_recommendation_state_with_dataset(uploaded_filename: str, base_date: str, route_summary: pd.DataFrame) -> str:
    dataset_key = build_route_dataset_key(uploaded_filename, base_date, route_summary)
    if st.session_state.get("recommended_groups_dataset_key") != dataset_key:
        mark_recommended_groups_stale("추천 기준 데이터가 바뀌어 재계산이 필요합니다.", status="inactive")
    st.session_state["recommended_groups_dataset_key"] = dataset_key
    return dataset_key


def current_assignment_keys(route_summary: pd.DataFrame) -> set:
    if not isinstance(route_summary, pd.DataFrame) or len(route_summary) == 0:
        return set()

    keys = set()
    for _, row in route_summary.iterrows():
        route = str(row.get("route", "")).strip()
        if not route:
            continue
        truck_request_id = str(row.get("truck_request_id", "")).strip()
        keys.add(make_assignment_key(route, truck_request_id))
    return keys


def is_fresh_local_assignment_state(route_summary: pd.DataFrame, assignment_store: dict) -> bool:
    if not isinstance(assignment_store, dict) or not assignment_store:
        return False

    current_keys = current_assignment_keys(route_summary)
    return any(key in assignment_store for key in current_keys)


def record_backend_merge_debug(dataset_key: str, route_summary: pd.DataFrame, assignment_store: dict, should_merge: bool, reason: str):
    current_keys = current_assignment_keys(route_summary)
    st.session_state["backend_merge_debug"] = {
        "dataset_key": str(dataset_key or ""),
        "should_merge_backend_assignments": bool(should_merge),
        "reason": str(reason or ""),
        "disable_backend_merge": bool(st.session_state.get("disable_backend_merge", False)),
        "backend_dataset_key": str(st.session_state.get("backend_dataset_key", "")),
        "backend_sync_done": str(st.session_state.get("backend_sync_done", "")),
        "assignment_local_source_dataset_key": str(st.session_state.get("assignment_local_source_dataset_key", "")),
        "backend_assignment_merge_dataset_key": str(st.session_state.get("backend_assignment_merge_dataset_key", "")),
        "current_route_key_count": len(current_keys),
        "assignment_store_current_key_count": sum(1 for key in current_keys if isinstance(assignment_store, dict) and key in assignment_store),
    }


def should_merge_backend_assignments(dataset_key: str, route_summary: pd.DataFrame, assignment_store: dict) -> bool:
    if st.session_state.get("disable_backend_merge"):
        record_backend_merge_debug(dataset_key, route_summary, assignment_store, False, "disable_backend_merge")
        return False
    if st.session_state.get("assignment_local_source_dataset_key") == dataset_key:
        record_backend_merge_debug(dataset_key, route_summary, assignment_store, False, "local_source_dataset_key match")
        return False
    if st.session_state.get("backend_assignment_merge_dataset_key") == dataset_key:
        record_backend_merge_debug(dataset_key, route_summary, assignment_store, False, "backend_assignment_merge_dataset_key match")
        return False
    if is_fresh_local_assignment_state(route_summary, assignment_store):
        st.session_state["assignment_local_source_dataset_key"] = dataset_key
        record_backend_merge_debug(dataset_key, route_summary, assignment_store, False, "fresh_local_assignment_state")
        return False
    record_backend_merge_debug(dataset_key, route_summary, assignment_store, True, "no local assignment state for current dataset")
    return True


def prepare_backend_run_state_for_dataset(dataset_key: str):
    if st.session_state.get("backend_dataset_key") == dataset_key:
        return

    for key in [
        "backend_run_id",
        "backend_run_summary",
        "backend_sync_done",
        "backend_sync_error",
        "backend_assignment_merge_dataset_key",
        "disable_backend_merge",
    ]:
        st.session_state.pop(key, None)
    st.session_state["backend_dataset_key"] = dataset_key


def should_sync_backend_run_for_dataset(dataset_key: str) -> bool:
    if st.session_state.get("backend_sync_done") == dataset_key:
        return False
    if safe_int(st.session_state.get("backend_run_id", 0)) > 0 and st.session_state.get("backend_dataset_key") == dataset_key:
        st.session_state["backend_sync_done"] = dataset_key
        return False
    return True


def mark_backend_run_sync_done(dataset_key: str, sync_error: str = None):
    st.session_state["backend_dataset_key"] = dataset_key
    st.session_state["backend_sync_done"] = dataset_key
    if sync_error:
        st.session_state["backend_sync_error"] = str(sync_error)
    else:
        st.session_state.pop("backend_sync_error", None)


def build_driver_preference_display_df(preference_df: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(preference_df, pd.DataFrame):
        preference_df = pd.DataFrame()

    display_df = preference_df.copy().reindex(columns=DRIVER_PREFERENCE_COLUMNS)
    if "선호예상점수" in display_df.columns:
        display_df["선호예상점수"] = pd.to_numeric(display_df["선호예상점수"], errors="coerce").round(1)
    if "route_spread_km" in display_df.columns:
        display_df["route_spread_km"] = pd.to_numeric(display_df["route_spread_km"], errors="coerce").round(1)

    display_df = display_df.rename(columns={"route_spread_km": "그룹평균퍼짐km"})
    return display_df.reindex(columns=DRIVER_PREFERENCE_DISPLAY_COLUMNS)


DRIVER_RECORD_COLUMNS = ["driver_name", "worker_login_id", "plate_number"]


def _normalize_driver_records_df(df: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(df, pd.DataFrame):
        return pd.DataFrame(columns=DRIVER_RECORD_COLUMNS)

    work_df = df.copy()
    for column in DRIVER_RECORD_COLUMNS:
        if column not in work_df.columns:
            work_df[column] = ""
        work_df[column] = work_df[column].fillna("").astype(str).str.strip()

    work_df = work_df[DRIVER_RECORD_COLUMNS].copy()
    work_df = work_df[work_df["driver_name"] != ""].drop_duplicates(subset=["driver_name", "worker_login_id"], keep="first")
    return work_df.reset_index(drop=True)


def _driver_names_from_df(df: pd.DataFrame) -> list[str]:
    if not isinstance(df, pd.DataFrame) or "driver_name" not in df.columns:
        return []
    return df["driver_name"].dropna().astype(str).str.strip().loc[lambda s: s != ""].tolist()


def load_driver_records_local() -> pd.DataFrame:
    drivers_csv_path = BASE_DIR / DRIVER_FILE
    if not drivers_csv_path.exists():
        return pd.DataFrame(columns=DRIVER_RECORD_COLUMNS)
    try:
        df = pd.read_csv(drivers_csv_path, dtype=str).fillna("")
    except Exception:
        return pd.DataFrame(columns=DRIVER_RECORD_COLUMNS)
    return _normalize_driver_records_df(df)


def _driver_records_df_from_backend_payload(payload) -> pd.DataFrame:
    if not isinstance(payload, list):
        return pd.DataFrame(columns=DRIVER_RECORD_COLUMNS)

    rows = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        rows.append({
            "driver_name": str(row.get("name", "") or row.get("driver_name", "")).strip(),
            "worker_login_id": str(row.get("worker_login_id", "")).strip(),
            "plate_number": str(row.get("plate_number", "")).strip(),
        })
    return _normalize_driver_records_df(pd.DataFrame(rows))


def load_driver_names_from_local():
    return _driver_names_from_df(load_driver_records_local())


def _dispatch_api_url(path: str) -> str:
    base = str(DJANGO_API_BASE_URL).rstrip("/")
    tail = str(path).lstrip("/")
    return f"{base}/{tail}"


def _dispatch_api_get(path: str):
    try:
        response = REQUESTS_SESSION.get(_dispatch_api_url(path), timeout=DJANGO_API_READ_TIMEOUT)
        response.raise_for_status()
        return response.json(), None
    except Exception as exc:
        return None, str(exc)


def _dispatch_api_post(path: str, payload: dict):
    try:
        response = REQUESTS_SESSION.post(
            _dispatch_api_url(path),
            json=payload,
            timeout=DJANGO_API_TIMEOUT * 2,
        )
        response.raise_for_status()
        return response.json(), None
    except Exception as exc:
        return None, str(exc)


def fetch_driver_records_from_backend():
    payload, error = _dispatch_api_get("drivers/")
    if error:
        return pd.DataFrame(columns=DRIVER_RECORD_COLUMNS), str(error)
    return _driver_records_df_from_backend_payload(payload), None


def load_driver_records_from_backend():
    driver_records_df, _ = fetch_driver_records_from_backend()
    return driver_records_df


def load_driver_names_from_backend():
    return _driver_names_from_df(load_driver_records_from_backend())


def load_latest_assignment_run_summary():
    payload, error = _dispatch_api_get("assignment-runs/latest/")
    if error:
        return None, error
    if not isinstance(payload, dict):
        return None, "invalid latest run payload"
    return payload.get("run"), None


def load_assignment_run_detail(run_id):
    payload, error = _dispatch_api_get(f"assignment-runs/{safe_int(run_id)}/")
    if error:
        return None, error
    if not isinstance(payload, dict):
        return None, "invalid run detail payload"
    return payload, None


def load_assignment_runs(source_date: str = "", limit: int = 10):
    query = f"assignment-runs/?limit={safe_int(limit) if safe_int(limit) > 0 else 10}"
    if str(source_date).strip():
        query += f"&source_date={str(source_date).strip()}"
    payload, error = _dispatch_api_get(query)
    if error:
        return [], error
    if not isinstance(payload, list):
        return [], "invalid assignment run list payload"
    return payload, None


@st.cache_data(show_spinner=False, ttl=15)
def cached_load_latest_assignment_run_summary():
    return load_latest_assignment_run_summary()


@st.cache_data(show_spinner=False, ttl=15)
def cached_load_assignment_run_detail(run_id):
    return load_assignment_run_detail(run_id)


@st.cache_data(show_spinner=False, ttl=15)
def cached_load_assignment_runs(source_date: str = "", limit: int = 10):
    return load_assignment_runs(source_date, limit)


def _coerce_run_date(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return pd.to_datetime(text).date()
    except Exception:
        return None


def _format_run_option_label(row: dict) -> str:
    source_date = str(row.get("source_date", "")).strip()
    name = str(row.get("name", "")).strip()
    filename = str(row.get("source_filename", "")).strip()
    route_count = safe_int(row.get("route_count", 0))
    suffix = f" / {route_count}개" if route_count else ""
    return f"{source_date} | {name} | {filename}{suffix}"


def load_driver_records_prefer_backend():
    backend_driver_records, backend_error = fetch_driver_records_from_backend()
    if backend_error is None:
        return backend_driver_records, "django"
    return load_driver_records_local(), "local"


def load_drivers_prefer_backend():
    driver_records, source = load_driver_records_prefer_backend()
    return _driver_names_from_df(driver_records), source


def sync_driver_records_to_backend(driver_records_df: pd.DataFrame):
    work_df = _normalize_driver_records_df(driver_records_df)
    if len(work_df) == 0:
        return None, "no driver records to sync"

    payload = {
        "drivers": [
            {
                "name": row["driver_name"],
                "worker_login_id": row["worker_login_id"],
                "plate_number": row["plate_number"],
            }
            for _, row in work_df.iterrows()
        ]
    }
    return _dispatch_api_post("drivers/upsert/", payload)


def load_driver_records_for_assignment_input():
    driver_records_df, driver_source = load_driver_records_prefer_backend()
    if len(driver_records_df) > 0:
        return driver_records_df, driver_source, None

    drivers_csv_path = BASE_DIR / DRIVER_FILE
    return (
        pd.DataFrame(columns=DRIVER_RECORD_COLUMNS),
        driver_source,
        f"기사 목록을 불러오지 못했습니다. Django drivers API와 로컬 drivers.csv를 모두 확인해 주세요: {drivers_csv_path}",
    )


def _build_backend_route_rows(route_summary: pd.DataFrame):
    rows = []
    if len(route_summary) == 0:
        return rows

    for _, row in route_summary.iterrows():
        row_values = row.tolist()
        rows.append({
            "route": str(row.get("route", "")).strip(),
            "route_prefix": str(row.get("route_prefix", "")).strip(),
            "truck_request_id": str(row.get("truck_request_id", "")).strip(),
            "camp_name": str(row.get("camp_name", "")).strip(),
            "camp_code": str(row.get("camp_code", "")).strip(),
            "start_min": safe_int(row.get("start_min", 0)),
            "end_min": safe_int(row.get("end_max", 0)),
            "stop_count": safe_int(row.get("stop_count", row_values[6] if len(row_values) > 6 else 0)),
            "small_qty": safe_int(row.get("small_qty", row_values[2] if len(row_values) > 2 else 0)),
            "medium_qty": safe_int(row.get("medium_qty", row_values[3] if len(row_values) > 3 else 0)),
            "large_qty": safe_int(row.get("large_qty", row_values[4] if len(row_values) > 4 else 0)),
            "total_qty": safe_int(row.get("total_qty", row_values[5] if len(row_values) > 5 else 0)),
            "work_minutes": safe_int(row.get("work_minutes", row_values[14] if len(row_values) > 14 else 0)),
        })
    return rows


def sync_assignment_run_to_backend(uploaded_filename: str, base_date: str, route_summary: pd.DataFrame):
    payload = {
        "source_date": str(base_date),
        "source_filename": str(uploaded_filename),
        "run_name": f"{base_date} {extract_base_name(uploaded_filename)}",
        "raw_meta": {
            "route_count": safe_int(len(route_summary)),
        },
        "routes": _build_backend_route_rows(route_summary),
    }
    return _dispatch_api_post("assignment-runs/sync/", payload)


def sync_assignments_to_backend(run_id, assignment_df: pd.DataFrame):
    if safe_int(run_id) <= 0:
        return None, "missing backend run id"

    assignments = []
    for _, row in assignment_df.iterrows():
        assignments.append({
            "route": str(row.get("route", "")).strip(),
            "truck_request_id": str(row.get("truck_request_id", "")).strip(),
            "driver_name": str(row.get("assigned_driver", "")).strip(),
            "assignment_source": "manual",
        })

    return _dispatch_api_post(
        f"assignment-runs/{safe_int(run_id)}/assignments/sync/",
        {"assignments": assignments},
    )


def ensure_backend_run_for_session(
    uploaded_filename: str,
    base_date: str,
    route_summary: pd.DataFrame,
    merge_backend_assignments: bool = True,
):
    dataset_key = build_route_dataset_key(uploaded_filename, base_date, route_summary)
    existing_run_id = safe_int(st.session_state.get("backend_run_id", 0))
    existing_dataset_key = st.session_state.get("backend_dataset_key")
    if existing_run_id > 0 and (existing_dataset_key in (None, "", dataset_key)):
        st.session_state["backend_dataset_key"] = dataset_key
        st.session_state["backend_sync_done"] = dataset_key
        return existing_run_id, None
    if not str(uploaded_filename or "").strip():
        return 0, "missing uploaded filename"
    if st.session_state.get("backend_sync_done") == dataset_key:
        return 0, st.session_state.get("backend_sync_error") or "backend run sync already attempted for this dataset"

    sync_payload, sync_error = sync_assignment_run_to_backend(uploaded_filename, base_date, route_summary)
    if sync_error:
        return 0, sync_error

    backend_run_summary = (sync_payload or {}).get("run") if isinstance(sync_payload, dict) else None
    backend_run_detail = (sync_payload or {}).get("detail") if isinstance(sync_payload, dict) else None
    backend_run_id = safe_int((backend_run_summary or {}).get("id"))
    if backend_run_id <= 0:
        return 0, "backend run sync returned no run id"

    st.session_state["backend_run_id"] = backend_run_id
    st.session_state["backend_dataset_key"] = dataset_key
    st.session_state["backend_sync_done"] = dataset_key
    if backend_run_summary:
        st.session_state["backend_run_summary"] = backend_run_summary
    if backend_run_detail and merge_backend_assignments and not st.session_state.get("disable_backend_merge"):
        st.session_state["assignment_store"] = apply_backend_assignments_to_store(
            backend_run_detail,
            st.session_state.get("assignment_store", {}),
        )
    cached_load_latest_assignment_run_summary.clear()
    cached_load_assignment_runs.clear()
    cached_load_assignment_run_detail.clear()
    return backend_run_id, None


def persist_assignment_store(
    route_summary: pd.DataFrame,
    assignment_store: dict,
    uploaded_filename: str,
    base_date: str,
):
    dataset_key = build_route_dataset_key(uploaded_filename, base_date, route_summary)
    local_save_error = save_assignment_store(assignment_store)
    if local_save_error:
        st.session_state["assignment_store"] = assignment_store
        st.session_state["assignment_store_source"] = "submitted_local_save_failed"
        st.session_state["assignment_local_source_dataset_key"] = dataset_key
        st.session_state["disable_backend_merge"] = True
        st.session_state["map_force_refresh"] = True
        return {
            "ok": False,
            "local_ok": False,
            "backend_ok": False,
            "saved_count": 0,
            "message": f"Local assignment save failed: {local_save_error}",
        }

    reloaded_store = load_assignment_store()
    if not isinstance(reloaded_store, dict):
        reloaded_store = {}
    st.session_state["assignment_store"] = reloaded_store
    st.session_state["assignment_store_source"] = "file_reloaded_after_save"
    st.session_state["assignment_local_source_dataset_key"] = dataset_key
    st.session_state["disable_backend_merge"] = True
    st.session_state["map_force_refresh"] = True

    backend_run_id, backend_run_error = ensure_backend_run_for_session(
        uploaded_filename=uploaded_filename,
        base_date=base_date,
        route_summary=route_summary,
        merge_backend_assignments=False,
    )
    if backend_run_error:
        return {
            "ok": False,
            "local_ok": True,
            "backend_ok": False,
            "saved_count": 0,
            "message": f"Local assignment file saved/reloaded; Django assignment sync failed: {backend_run_error}",
        }

    assignment_df_for_backend = build_assignment_df(route_summary, reloaded_store)
    backend_save_payload, backend_save_error = sync_assignments_to_backend(backend_run_id, assignment_df_for_backend)
    if backend_save_error:
        return {
            "ok": False,
            "local_ok": True,
            "backend_ok": False,
            "saved_count": 0,
            "message": f"Local assignment file saved/reloaded; Django assignment sync failed: {backend_save_error}",
        }

    saved_count = safe_int((backend_save_payload or {}).get("saved_count", 0))
    if saved_count <= 0:
        return {
            "ok": False,
            "local_ok": True,
            "backend_ok": False,
            "saved_count": saved_count,
            "message": "Local assignment file saved/reloaded; Django assignment sync saved 0 rows.",
        }

    cached_load_latest_assignment_run_summary.clear()
    cached_load_assignment_runs.clear()
    cached_load_assignment_run_detail.clear()
    return {
        "ok": True,
        "local_ok": True,
        "backend_ok": True,
        "saved_count": saved_count,
        "message": f"Local assignment file saved/reloaded; Django assignment sync saved {saved_count} rows.",
    }


def queue_assignment_feedback(level: str, message: str):
    st.session_state["assignment_save_feedback"] = {
        "level": str(level or "info"),
        "message": str(message or "").strip(),
    }
    st.session_state["assignment_pending_rerun"] = True


def rerun_after_assignment_submit():
    if st.session_state.pop("assignment_pending_rerun", False):
        st.rerun()


def sync_run_snapshot_to_backend(run_id, payload: dict, snapshot_kind: str = "recent"):
    if safe_int(run_id) <= 0:
        return None, "missing backend run id"

    return _dispatch_api_post(
        f"assignment-runs/{safe_int(run_id)}/snapshot/",
        {
            "snapshot_kind": snapshot_kind,
            "share_key": f"run-{safe_int(run_id)}-{snapshot_kind}",
            "payload": payload,
        },
    )


def build_backend_share_url(share_key: str) -> str:
    base = str(DJANGO_PUBLIC_BASE_URL).rstrip("/")
    return f"{base}/api/dispatch/share/{share_key}/"


def _memo_lookup_key(company_id: str, address_norm: str) -> str:
    return f"{str(company_id).strip()}|{str(address_norm).strip()}"


def load_customer_memos_for_df(df: pd.DataFrame):
    if len(df) == 0:
        return {}, None

    rows = []
    unique_df = df[["company_id", "address_norm"]].drop_duplicates().fillna("")
    for _, row in unique_df.iterrows():
        rows.append({
            "company_id": str(row.get("company_id", "")).strip(),
            "address_norm": str(row.get("address_norm", "")).strip(),
        })

    payload, error = _dispatch_api_post("customer-memos/bulk-lookup/", {"rows": rows})
    if error:
        return {}, error
    if not isinstance(payload, dict):
        return {}, "invalid customer memo payload"
    return payload.get("memos", {}) or {}, None


@st.cache_data(show_spinner=False, ttl=30)
def cached_load_customer_memos(rows_json: str):
    try:
        rows = json.loads(rows_json)
    except Exception:
        rows = []

    payload, error = _dispatch_api_post("customer-memos/bulk-lookup/", {"rows": rows})
    if error:
        return {}, error
    if not isinstance(payload, dict):
        return {}, "invalid customer memo payload"
    return payload.get("memos", {}) or {}, None


def load_customer_memos_cached_for_df(df: pd.DataFrame):
    if len(df) == 0:
        return {}, None

    rows = []
    unique_df = df[["company_id", "address_norm"]].drop_duplicates().fillna("")
    for _, row in unique_df.iterrows():
        rows.append({
            "company_id": str(row.get("company_id", "")).strip(),
            "address_norm": str(row.get("address_norm", "")).strip(),
        })
    rows = sorted(rows, key=lambda r: (r["company_id"], r["address_norm"]))
    return cached_load_customer_memos(json.dumps(rows, ensure_ascii=False))


def save_customer_memo_to_backend(company_id: str, address: str, address_norm: str, company_name: str, note: str):
    payload = {
        "company_id": str(company_id).strip(),
        "address": str(address).strip(),
        "address_norm": str(address_norm).strip(),
        "company_name": str(company_name).strip(),
        "note": str(note),
    }
    return _dispatch_api_post("customer-memos/upsert/", payload)


def attach_customer_memos(df: pd.DataFrame, memo_map: dict):
    out = df.copy()
    if len(out) == 0:
        out["customer_memo"] = ""
        return out

    def _lookup(row):
        key = _memo_lookup_key(row.get("company_id", ""), row.get("address_norm", ""))
        memo = memo_map.get(key, {}) if isinstance(memo_map, dict) else {}
        return str(memo.get("note", "")).strip()

    out["customer_memo"] = out.apply(_lookup, axis=1)
    return out


def send_telegram_message(text: str):
    token, chat_id = get_telegram_config()
    if not token or not chat_id:
        logger.info(
            "Skipping Telegram send because TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is not configured."
        )
        return None, None

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        response = REQUESTS_SESSION.post(
            url,
            json={
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": False,
            },
            timeout=15,
        )
        response.raise_for_status()
        return response.json(), None
    except Exception as exc:
        logger.warning("Telegram send failed: %s", exc)
        return None, str(exc)


def send_assignment_completion_notifications(summary_text: str, memo_text: str):
    if not is_telegram_configured():
        logger.info(
            "Skipping assignment completion Telegram notification because configuration is missing."
        )
        return "skipped", None

    tg_resp_1, tg_err_1 = send_telegram_message(summary_text)
    tg_resp_2, tg_err_2 = send_telegram_message(memo_text)
    error_message = tg_err_1 or tg_err_2
    if error_message:
        logger.warning("Telegram assignment completion notification failed: %s", error_message)
        return "failed", error_message
    return "sent", {
        "summary": tg_resp_1,
        "memo": tg_resp_2,
    }


def load_assignment_progress_state():
    if not os.path.exists(ASSIGNMENT_PROGRESS_FILE):
        return {}
    try:
        with open(ASSIGNMENT_PROGRESS_FILE, "r", encoding="utf-8") as f:
            payload = json.load(f)
        return payload if isinstance(payload, dict) else {}
    except Exception as exc:
        logger.warning("Failed to load assignment progress state: %s", exc)
        return {}


def save_assignment_progress_state(state: dict):
    try:
        with open(ASSIGNMENT_PROGRESS_FILE, "w", encoding="utf-8") as f:
            json.dump(state or {}, f, ensure_ascii=False, indent=2)
        return None
    except Exception as exc:
        logger.warning("Failed to save assignment progress state: %s", exc)
        return str(exc)


def format_assignment_progress_lines(state: dict) -> str:
    state = state if isinstance(state, dict) else {}
    total = safe_int(state.get("total", 0))
    completed = safe_int(state.get("completed", 0))
    success_count = safe_int(state.get("success_count", 0))
    failure_count = safe_int(state.get("failure_count", 0))
    base_date = str(state.get("base_date", "") or "").strip()
    last_attempt = str(state.get("last_attempt_request_id", "") or "").strip()
    last_success = str(state.get("last_success_request_id", "") or "").strip()
    last_stage = str(state.get("last_stage", "") or "").strip()
    retry_count = safe_int(state.get("retry_count", 0))
    last_error = str(state.get("last_exception_message", "") or "").strip()
    updated_at = str(state.get("updated_at", "") or "").strip()

    lines = []
    if base_date:
        lines.append(f"기준일: {base_date}")
    lines.append(f"전체 {total}건 중 {completed}건 완료")
    lines.append(f"성공 {success_count}건 / 실패 {failure_count}건")
    if last_attempt:
        lines.append(f"마지막 처리 request_id: {last_attempt}")
    if last_success:
        lines.append(f"마지막 성공 request_id: {last_success}")
    if last_stage:
        lines.append(f"마지막 단계: {last_stage}")
    lines.append(f"재시도 횟수: {retry_count}")
    if last_error:
        lines.append(f"오류: {last_error[:300]}")
    if updated_at:
        lines.append(f"갱신: {updated_at}")
    return "\n".join(lines)


def build_assignment_progress_message(title: str, state: dict) -> str:
    state = state if isinstance(state, dict) else {}
    total = safe_int(state.get("total", 0))
    completed = safe_int(state.get("completed", 0))
    success_count = safe_int(state.get("success_count", 0))
    failure_count = safe_int(state.get("failure_count", 0))
    last_request_id = clean_map_text(state.get("last_attempt_request_id", ""))
    last_stage = clean_map_text(state.get("last_stage", ""))
    last_error = clean_map_text(state.get("last_exception_message", ""))

    if title == "자동할당 완료":
        return f"[자동할당 완료]\n전체 {total}건 중 성공 {success_count}건 / 실패 {failure_count}건"

    lines = [
        "[자동할당 실패]",
        f"전체 {total}건 중 {completed}건 처리",
        f"성공 {success_count}건 / 실패 {failure_count}건",
    ]
    if last_request_id:
        lines.append(f"마지막 request_id: {last_request_id}")
    if last_stage:
        lines.append(f"마지막 단계: {last_stage}")
    if last_error:
        lines.append(f"오류: {last_error[:300]}")
    return "\n".join(lines)


def send_assignment_progress_notification(title: str, state: dict):
    try:
        if not is_telegram_configured():
            reason = "TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing"
            logger.warning("Skipping assignment progress Telegram notification: %s", reason)
            return "skipped", reason

        _, error = send_telegram_message(build_assignment_progress_message(title, state))
        if error:
            logger.warning("Telegram assignment progress notification failed: %s", error)
            return "failed", error
        return "sent", None
    except Exception as exc:
        logger.warning("Telegram assignment progress notification failed: %s", exc, exc_info=True)
        return "failed", str(exc)


def render_assignment_progress_state(state: dict = None, placeholder=None):
    state = state if isinstance(state, dict) else load_assignment_progress_state()
    if not state:
        return

    title = str(state.get("status", "") or "unknown").strip()
    body = format_assignment_progress_lines(state)
    target = placeholder if placeholder is not None else st
    try:
        target.info(f"자동할당 진행 상태: {title}\n\n{body}")
    except Exception:
        logger.debug("Failed to render assignment progress state.", exc_info=True)


def make_assignment_progress_callback(
    total_count: int,
    base_date: str,
    placeholder=None,
    progress_bar=None,
    notify_every: int = ASSIGNMENT_PROGRESS_NOTIFY_EVERY,
):
    started_at = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
    state = {
        "status": "running",
        "base_date": str(base_date or "").strip(),
        "total": safe_int(total_count),
        "completed": 0,
        "success_count": 0,
        "failure_count": 0,
        "last_success_request_id": "",
        "last_attempt_request_id": "",
        "last_stage": "queued",
        "retry_count": 0,
        "last_exception_message": "",
        "started_at": started_at,
        "updated_at": started_at,
    }
    def _update_ui():
        st.session_state["assignment_progress_state"] = state.copy()
        save_assignment_progress_state(state)
        render_assignment_progress_state(state, placeholder=placeholder)
        if progress_bar is not None:
            total = max(1, safe_int(state.get("total", 0)))
            completed = min(total, safe_int(state.get("completed", 0)))
            try:
                progress_bar.progress(completed / total)
            except Exception:
                logger.debug("Failed to update assignment progress bar.", exc_info=True)

    def _notify(title: str):
        try:
            status, error = send_assignment_progress_notification(title, state.copy())
            state["telegram_status"] = status
            state["telegram_error"] = str(error or "")
            state["telegram_notified_at"] = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
            save_assignment_progress_state(state)
            logger.info("Assignment progress Telegram notification status: %s", status)
        except Exception as exc:
            state["telegram_status"] = "failed"
            state["telegram_error"] = str(exc)
            state["telegram_notified_at"] = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
            save_assignment_progress_state(state)
            logger.warning("Telegram assignment progress notification failed: %s", exc, exc_info=True)

    def callback(event_payload: dict):
        payload = event_payload if isinstance(event_payload, dict) else {}
        event = str(payload.get("event", "") or "").strip()
        stage = str(payload.get("stage", "") or "").strip()
        request_id = str(payload.get("request_id", "") or "").strip()
        reason = str(payload.get("reason", "") or "").strip()
        error = str(payload.get("error", "") or "").strip()

        if payload.get("total") is not None:
            state["total"] = safe_int(payload.get("total", state.get("total", 0)))
        if payload.get("completed") is not None:
            state["completed"] = safe_int(payload.get("completed", state.get("completed", 0)))
        if payload.get("success_count") is not None:
            state["success_count"] = safe_int(payload.get("success_count", state.get("success_count", 0)))
        if payload.get("failure_count") is not None:
            state["failure_count"] = safe_int(payload.get("failure_count", state.get("failure_count", 0)))
        if stage:
            state["last_stage"] = stage
        if request_id:
            state["last_attempt_request_id"] = request_id
        if event == "row_saved" and request_id:
            state["last_success_request_id"] = request_id
        if event == "row_done" and stage == "saved" and request_id:
            state["last_success_request_id"] = request_id
        state["retry_count"] = max(safe_int(state.get("retry_count", 0)), safe_int(payload.get("retry_count", 0)))
        if error or reason:
            state["last_exception_message"] = (error or reason)[:500]
        state["updated_at"] = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")

        if event == "start":
            state["status"] = "running"
            _update_ui()
            return
        if event == "progress":
            state["status"] = "running"
            _update_ui()
            return
        if event == "completed":
            has_failures = safe_int(state.get("failure_count", 0)) > 0
            state["status"] = "failed" if has_failures else "completed"
            state["completed"] = state["total"]
            state["finished_at"] = state["updated_at"]
            _update_ui()
            if has_failures:
                _notify("자동할당 실패")
            else:
                _notify("자동할당 완료")
            return
        if event == "aborted":
            state["status"] = "aborted"
            state["last_stage"] = "failed"
            state["finished_at"] = state["updated_at"]
            _update_ui()
            _notify("자동할당 실패")
            return

        _update_ui()

    return callback


def render_route_dashboard(route_summary: pd.DataFrame):
    st.subheader("라우트 대시보드")
    if not isinstance(route_summary, pd.DataFrame) or len(route_summary) == 0:
        st.info("표시할 라우트 데이터가 없습니다.")
        return

    route_count = (
        safe_int(route_summary["route"].dropna().astype(str).str.strip().replace("", pd.NA).dropna().nunique())
        if "route" in route_summary.columns
        else safe_int(len(route_summary))
    )
    small_total = safe_int(pd.to_numeric(route_summary.get("소형합", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
    medium_total = safe_int(pd.to_numeric(route_summary.get("중형합", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
    large_total = safe_int(pd.to_numeric(route_summary.get("대형합", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
    if "총합" in route_summary.columns:
        box_total = safe_int(pd.to_numeric(route_summary["총합"], errors="coerce").fillna(0).sum())
    else:
        box_total = small_total + medium_total + large_total

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("라우트개수", route_count)
    m2.metric("박스총개수", box_total)
    m3.metric("소형", small_total)
    m4.metric("중형", medium_total)
    m5.metric("대형", large_total)


def render_coupang_assignment_panel(export_df: pd.DataFrame):
    st.subheader("쿠팡 자동반영용 CSV 생성")
    col1, col2 = st.columns(2)
    with col1:
        order_date = st.date_input(
            "Order Date",
            value=pd.Timestamp(default_order_date()).date(),
            key="coupang_assign_order_date",
        )
    with col2:
        registration_mode = st.selectbox(
            "registration_mode",
            ["new", "modify"],
            index=0,
            key="coupang_assign_registration_mode",
        )

    def render_downloads(source_df: pd.DataFrame, key_prefix: str):
        driver_df, driver_source, driver_error = load_driver_records_for_assignment_input()
        if driver_error:
            st.error(driver_error)
            return
        try:
            success_df, error_df = build_assign_input_df(
                source_df,
                driver_df,
                order_date=pd.Timestamp(order_date).strftime("%Y-%m-%d"),
                registration_mode=registration_mode,
            )
        except Exception as exc:
            st.error(f"쿠팡 자동반영용 CSV 생성 실패: {exc}")
            return

        source_label = "Django Driver" if driver_source == "django" else "local drivers.csv fallback"
        st.caption(f"기사 기준값: {source_label}")
        st.caption(f"실행 대상 {len(success_df)}건 / 확인 필요 {len(error_df)}건")
        if len(success_df) > 0:
            st.download_button(
                "coupang_assign_input.csv 다운로드",
                data=success_df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"),
                file_name="coupang_assign_input.csv",
                mime="text/csv",
                key=f"{key_prefix}_success_download",
            )
        else:
            st.warning("assign_bot 실행 대상 행이 없습니다.")
        if len(error_df) > 0:
            st.dataframe(error_df, use_container_width=True)
            st.download_button(
                "coupang_assign_errors.csv 다운로드",
                data=error_df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"),
                file_name="coupang_assign_errors.csv",
                mime="text/csv",
                key=f"{key_prefix}_error_download",
            )

    if st.button("현재 배정 결과로 쿠팡 CSV 생성", key="coupang_assign_build_from_current"):
        render_downloads(export_df.copy(), "coupang_assign_current")

    status_placeholder = st.empty()
    progress_bar_placeholder = st.empty()
    render_assignment_progress_state(placeholder=status_placeholder)

    def run_now(source_df: pd.DataFrame, key_prefix: str):
        driver_df, driver_source, driver_error = load_driver_records_for_assignment_input()
        if driver_error:
            st.error(driver_error)
            return
        try:
            success_df, error_df = build_assign_input_df(
                source_df,
                driver_df,
                order_date=pd.Timestamp(order_date).strftime("%Y-%m-%d"),
                registration_mode=registration_mode,
            )
        except Exception as exc:
            st.error(f"Failed to prepare Coupang assignments: {exc}")
            return

        st.caption(f"Driver source: {'Django Driver' if driver_source == 'django' else 'local drivers.csv fallback'}")
        if len(error_df) > 0:
            st.error(f"Cannot run Coupang assignment because {len(error_df)} rows need review.")
            st.dataframe(error_df, use_container_width=True)
            return
        if len(success_df) == 0:
            st.warning("No Coupang assignment rows to run.")
            return

        try:
            import importlib
            import assign_bot as assign_bot_module
            assign_bot_module = importlib.reload(assign_bot_module)
            run_assignments_df = assign_bot_module.run_assignments_df
        except Exception as exc:
            st.error(f"Failed to load assign_bot: {exc}")
            return

        progress_bar = progress_bar_placeholder.progress(0)
        progress_callback = make_assignment_progress_callback(
            total_count=len(success_df),
            base_date=pd.Timestamp(order_date).strftime("%Y-%m-%d"),
            placeholder=status_placeholder,
            progress_bar=progress_bar,
        )
        result_file_path = BASE_DIR / "assign_results.csv"
        with st.spinner(f"Running Coupang assignment for {len(success_df)} rows..."):
            try:
                results_df = run_assignments_df(
                    success_df,
                    result_file=str(result_file_path),
                    progress_callback=progress_callback,
                    progress_interval=ASSIGNMENT_PROGRESS_NOTIFY_EVERY,
                    raise_on_abort=False,
                )
            except Exception as exc:
                progress_callback({
                    "event": "aborted",
                    "stage": "failed",
                    "total": len(success_df),
                    "completed": 0,
                    "success_count": 0,
                    "failure_count": 0,
                    "error": str(exc),
                })
                st.error(f"Coupang assignment aborted: {exc}")
                return

        st.session_state[f"{key_prefix}_results"] = results_df
        latest_progress = st.session_state.get("assignment_progress_state", {})
        failed_df = results_df[results_df["status"].astype(str) != "success"].copy()
        if isinstance(latest_progress, dict) and latest_progress.get("status") == "aborted":
            st.error(
                "Coupang assignment aborted. "
                f"Last stage: {latest_progress.get('last_stage', '')}, "
                f"error: {latest_progress.get('last_exception_message', '')}"
            )
        elif len(failed_df) > 0:
            st.error(f"Coupang assignment finished with {len(failed_df)} failed rows.")
        else:
            st.success(f"Coupang assignment completed: {len(results_df)} rows.")
        st.caption(f"Intermediate results saved to {result_file_path}")
        st.dataframe(results_df, use_container_width=True)

    if st.button("쿠팡 자동반영 바로 실행", key="coupang_assign_run_current"):
        run_now(export_df.copy(), "coupang_assign_current_run")

    uploaded_assignment_csv = st.file_uploader(
        "이미 내려받은 기사 배정표 CSV 업로드",
        type=["csv"],
        key="coupang_assign_uploaded_csv",
    )
    if uploaded_assignment_csv is not None and st.button(
        "업로드 CSV로 쿠팡 CSV 생성",
        key="coupang_assign_build_from_upload",
    ):
        try:
            uploaded_assignment_df = pd.read_csv(uploaded_assignment_csv, dtype=str).fillna("")
        except Exception as exc:
            st.error(f"업로드 CSV 읽기 실패: {exc}")
        else:
            render_downloads(uploaded_assignment_df, "coupang_assign_upload")


def build_driver_memo_report(grouped_delivery: pd.DataFrame) -> str:
    if len(grouped_delivery) == 0:
        return "[기사별 업체 메모]\n표시할 메모가 없습니다."

    work_df = grouped_delivery.copy()
    for col in ["assigned_driver", "customer_memo", "company_id", "company_name", "address_norm", "first_time"]:
        if col not in work_df.columns:
            work_df[col] = ""

    work_df["assigned_driver"] = work_df["assigned_driver"].fillna("").astype(str).str.strip()
    work_df["customer_memo"] = work_df["customer_memo"].fillna("").astype(str).str.strip()
    work_df["company_id"] = work_df["company_id"].fillna("").astype(str).str.strip()
    work_df["company_name"] = work_df["company_name"].fillna("").astype(str).str.strip()
    work_df["address_norm"] = work_df["address_norm"].fillna("").astype(str).str.strip()
    work_df["first_time"] = work_df["first_time"].fillna("").astype(str).str.strip()
    work_df = work_df[(work_df["assigned_driver"] != "") & (work_df["customer_memo"] != "")].copy()
    if len(work_df) == 0:
        return "[기사별 업체 메모]\n표시할 메모가 없습니다."

    def _memo_company_key(row):
        company_id = str(row["company_id"]).strip()
        if company_id:
            return f"id:{company_id}"
        company_name = str(row["company_name"]).strip()
        address_norm = str(row["address_norm"]).strip()
        if company_name or address_norm:
            return f"name:{company_name}|addr:{address_norm}"
        return f"row:{row.name}"

    def _memo_time_sort_value(value):
        text = str(value or "").strip()
        match = re.search(r"(\d{1,2}):(\d{1,2})", text)
        if match:
            hour = safe_int(match.group(1))
            minute = safe_int(match.group(2))
            return f"{hour:02d}:{minute:02d}"
        return text if text else "99:99"

    before_dedupe_count = len(work_df)
    work_df["_memo_company_key"] = work_df.apply(_memo_company_key, axis=1)
    work_df["_first_time_sort"] = work_df["first_time"].apply(_memo_time_sort_value)

    sort_cols = [c for c in ["assigned_driver", "_first_time_sort", "route", "house_order"] if c in work_df.columns]
    if sort_cols:
        work_df = work_df.sort_values(sort_cols)

    work_df = (
        work_df.groupby(["assigned_driver", "_memo_company_key"], as_index=False, sort=False)
        .agg(
            first_time=("first_time", "first"),
            company_name=("company_name", "first"),
            customer_memo=("customer_memo", "first"),
            _first_time_sort=("_first_time_sort", "first"),
        )
        .sort_values(["assigned_driver", "_first_time_sort", "company_name"], kind="stable")
        .reset_index(drop=True)
    )
    logger.info("Driver memo report deduped from %s rows to %s rows.", before_dedupe_count, len(work_df))

    lines = ["[기사별 업체 메모]"]
    for driver_name, part in work_df.groupby("assigned_driver", sort=True):
        lines.append("")
        lines.append(driver_name)
        for _, row in part.iterrows():
            time_text = str(row.get("first_time", "")).strip()
            company_name = str(row.get("company_name", "")).strip() or "업체명없음"
            memo_text = str(row.get("customer_memo", "")).strip()
            if time_text:
                lines.append(f"{time_text} {company_name}")
            else:
                lines.append(company_name)
            for memo_line in [line.strip() for line in memo_text.splitlines() if line.strip()]:
                lines.append(f"- {memo_line}")
    return "\n".join(lines)


def apply_backend_assignments_to_store(run_detail: dict, assignment_store: dict):
    if not isinstance(run_detail, dict):
        return assignment_store

    route_rows = run_detail.get("routes", []) or []
    route_lookup = {
        safe_int(row.get("id")): row
        for row in route_rows
        if safe_int(row.get("id")) > 0
    }

    updated_store = assignment_store.copy()
    for item in run_detail.get("route_assignments", []) or []:
        route_id = safe_int(item.get("route_id"))
        route_row = route_lookup.get(route_id, {})
        route = str(route_row.get("route_code", "")).strip()
        truck_request_id = str(route_row.get("truck_request_id", "")).strip()
        driver_name = str(item.get("driver_name", "")).strip()
        if route:
            updated_store[make_assignment_key(route, truck_request_id)] = driver_name

    return updated_store


def build_route_summary_from_backend_routes(route_rows):
    rows = []
    for row in route_rows or []:
        start_min = safe_int(row.get("start_min", 0))
        end_min = safe_int(row.get("end_min", 0))
        work_minutes = safe_int(row.get("work_minutes", 0))
        rows.append({
            "route": str(row.get("route_code", "")).strip(),
            "route_prefix": str(row.get("route_prefix", "")).strip(),
            "camp_name": str(row.get("camp_name", "")).strip(),
            "camp_code": str(row.get("camp_code", "")).strip(),
            "truck_request_id": str(row.get("truck_request_id", "")).strip(),
            "스톱수": safe_int(row.get("stop_count", 0)),
            "시작시간": min_to_hhmm(start_min),
            "종료시간": min_to_hhmm(end_min),
            "총걸린분": work_minutes,
            "총걸린시간": minutes_to_korean_text(work_minutes),
            "소형합": safe_int(row.get("small_qty", 0)),
            "중형합": safe_int(row.get("medium_qty", 0)),
            "대형합": safe_int(row.get("large_qty", 0)),
            "총합": safe_int(row.get("total_qty", 0)),
            "start_min": start_min,
            "end_max": end_min,
        })
    return pd.DataFrame(rows)


def apply_assignment_rows_to_store(assignment_rows, assignment_store: dict):
    updated_store = assignment_store.copy()
    for row in assignment_rows or []:
        route = str(row.get("route", "")).strip()
        truck_request_id = str(row.get("truck_request_id", "")).strip()
        assigned_driver = str(row.get("assigned_driver", "")).strip()
        if route:
            updated_store[make_assignment_key(route, truck_request_id)] = assigned_driver
    return updated_store


def ensure_assignment_history_file():
    # 이력 파일이 없거나 비어있으면 기본 헤더로 생성
    required_cols = [
        "date", "driver", "route", "truck_request_id",
        "small", "medium", "large", "total_qty",
        "route_count", "saved_at",
    ]
    if not os.path.exists(ASSIGNMENT_HISTORY_FILE):
        pd.DataFrame(columns=required_cols).to_csv(ASSIGNMENT_HISTORY_FILE, index=False, encoding="utf-8-sig")
        return

    try:
        df = pd.read_csv(ASSIGNMENT_HISTORY_FILE)
        if len(df.columns) == 0:
            pd.DataFrame(columns=required_cols).to_csv(ASSIGNMENT_HISTORY_FILE, index=False, encoding="utf-8-sig")
    except Exception:
        pd.DataFrame(columns=required_cols).to_csv(ASSIGNMENT_HISTORY_FILE, index=False, encoding="utf-8-sig")


def infer_assignment_base_date(uploaded_filename: str, route_summary: pd.DataFrame) -> str:
    # 1순위: route_summary에 날짜성 컬럼이 있으면 사용
    try:
        date_like_cols = [c for c in route_summary.columns if "date" in str(c).lower() or "날짜" in str(c)]
        for col in date_like_cols:
            parsed = pd.to_datetime(route_summary[col], errors="coerce")
            parsed = parsed.dropna()
            if len(parsed) > 0:
                return parsed.iloc[0].strftime("%Y-%m-%d")
    except Exception:
        pass

    # 2순위: 업로드 파일명에서 YYYY-MM-DD / YYYYMMDD 추출
    file_text = str(uploaded_filename or "")
    m1 = re.search(r"(20\d{2})[-_./]?(0[1-9]|1[0-2])[-_./]?(0[1-9]|[12]\d|3[01])", file_text)
    if m1:
        yyyy, mm, dd = m1.group(1), m1.group(2), m1.group(3)
        return f"{yyyy}-{mm}-{dd}"

    # 3순위: 오늘
    return pd.Timestamp.now().strftime("%Y-%m-%d")


def _empty_assignment_history_df():
    return pd.DataFrame(columns=[
        "date", "driver", "route", "truck_request_id",
        "small", "medium", "large", "total_qty",
        "route_count", "saved_at",
    ])


def load_assignment_history():
    ensure_assignment_history_file()
    required_cols = _empty_assignment_history_df().columns.tolist()
    try:
        hist = pd.read_csv(ASSIGNMENT_HISTORY_FILE)
    except Exception:
        return _empty_assignment_history_df()

    if len(hist) == 0:
        return _empty_assignment_history_df()

    for col in required_cols:
        if col not in hist.columns:
            hist[col] = 0 if col in {"small", "medium", "large", "total_qty", "route_count"} else ""

    hist["date"] = pd.to_datetime(hist["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    hist = hist[hist["date"].notna()].copy()
    hist["driver"] = hist["driver"].fillna("").astype(str).str.strip()
    hist = hist[hist["driver"] != ""].copy()
    return hist[required_cols].copy()


def save_assignment_history_for_date(assignment_df: pd.DataFrame, base_date: str):
    ensure_assignment_history_file()
    hist = load_assignment_history()

    work_df = assignment_df.copy()
    for col in ["assigned_driver", "route", "truck_request_id", "소형합", "중형합", "대형합", "총합"]:
        if col not in work_df.columns:
            work_df[col] = ""

    work_df["assigned_driver"] = work_df["assigned_driver"].fillna("").astype(str).str.strip()
    # 미배정/공백 기사 제외
    work_df = work_df[
        (work_df["assigned_driver"] != "")
        & (work_df["assigned_driver"] != "미배정")
    ].copy()

    # 같은 날짜는 덮어쓰기 저장
    hist = hist[hist["date"] != base_date].copy()

    if len(work_df) == 0:
        hist.to_csv(ASSIGNMENT_HISTORY_FILE, index=False, encoding="utf-8-sig")
        return 0

    now_ts = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
    save_rows = pd.DataFrame({
        "date": base_date,
        "driver": work_df["assigned_driver"].astype(str),
        "route": work_df["route"].astype(str),
        "truck_request_id": work_df["truck_request_id"].astype(str),
        "small": pd.to_numeric(work_df["소형합"], errors="coerce").fillna(0).astype(int),
        "medium": pd.to_numeric(work_df["중형합"], errors="coerce").fillna(0).astype(int),
        "large": pd.to_numeric(work_df["대형합"], errors="coerce").fillna(0).astype(int),
        # 기존 총합 로직 재사용
        "total_qty": pd.to_numeric(work_df["총합"], errors="coerce").fillna(0).astype(int),
        "route_count": 1,
        "saved_at": now_ts,
    })

    merged = pd.concat([hist, save_rows], ignore_index=True)
    merged.to_csv(ASSIGNMENT_HISTORY_FILE, index=False, encoding="utf-8-sig")
    return len(save_rows)


def resolve_assignment_total_qty_col(df: pd.DataFrame):
    if not isinstance(df, pd.DataFrame) or len(df) == 0:
        return None
    for column in ["총합", "珥앺빀", "total_qty"]:
        if column in df.columns:
            return column
    return None


def build_live_assignment_today_qty_map(assignment_df: pd.DataFrame):
    if not isinstance(assignment_df, pd.DataFrame) or len(assignment_df) == 0 or "assigned_driver" not in assignment_df.columns:
        return {}

    live_df = assignment_df.copy()
    live_df["assigned_driver"] = live_df["assigned_driver"].fillna("").astype(str).str.strip()
    live_df = live_df[live_df["assigned_driver"] != ""].copy()
    if len(live_df) == 0:
        return {}

    total_qty_col = resolve_assignment_total_qty_col(live_df)
    if total_qty_col:
        live_df["_today_qty"] = pd.to_numeric(live_df[total_qty_col], errors="coerce").fillna(0)
    else:
        qty_cols = [c for c in ["소형", "중형", "대형", "小型", "中型", "大型", "小", "中", "大", "ae_sum", "af_sum", "ag_sum", "?뚰삎??", "以묓삎??", "??뺥빀"] if c in live_df.columns]
        if not qty_cols:
            return {}
        live_df["_today_qty"] = 0
        for col in qty_cols:
            live_df["_today_qty"] += pd.to_numeric(live_df[col], errors="coerce").fillna(0)

    return live_df.groupby("assigned_driver")["_today_qty"].sum().to_dict()


def check_live_today_stats_consistency(assignment_df: pd.DataFrame, stats_df: pd.DataFrame):
    if not isinstance(stats_df, pd.DataFrame) or len(stats_df) == 0:
        return

    driver_col = next((c for c in ["기사명", "湲곗궗紐?"] if c in stats_df.columns), None)
    today_col = next((c for c in ["오늘물량", "?ㅻ뒛臾쇰웾"] if c in stats_df.columns), None)
    if not driver_col or not today_col:
        return

    live_map = {str(k).strip(): safe_int(v) for k, v in build_live_assignment_today_qty_map(assignment_df).items()}
    stats_map = {
        str(row.get(driver_col, "")).strip(): safe_int(row.get(today_col, 0))
        for _, row in stats_df.iterrows()
        if str(row.get(driver_col, "")).strip()
    }
    mismatch = {
        driver: {"live": live_map.get(driver, 0), "stats": stats_map.get(driver, 0)}
        for driver in set(live_map) | set(stats_map)
        if safe_int(live_map.get(driver, 0)) != safe_int(stats_map.get(driver, 0))
    }
    if mismatch:
        logger.warning("Live assignment today qty differs from stats today qty: %s", mismatch)


def build_driver_assignment_stats_df(
    assignment_df: pd.DataFrame,
    history_df: pd.DataFrame,
    driver_candidates,
    base_date: str,
):
    driver_set = {str(d).strip() for d in (driver_candidates or []) if str(d).strip()}

    if len(assignment_df) > 0 and "assigned_driver" in assignment_df.columns:
        current_drivers = assignment_df["assigned_driver"].fillna("").astype(str).str.strip().tolist()
        driver_set.update([d for d in current_drivers if d and d != "미배정"])

    if len(history_df) > 0 and "driver" in history_df.columns:
        hist_drivers = history_df["driver"].fillna("").astype(str).str.strip().tolist()
        driver_set.update([d for d in hist_drivers if d])

    drivers = sorted(driver_set)
    if len(drivers) == 0:
        return pd.DataFrame(columns=[
            "기사명", "오늘물량", "최근근무일(1일) 물량", "7일 근무일평균", "30일 근무일평균",
        ]), None

    hist = history_df.copy()
    if len(hist) == 0:
        hist = _empty_assignment_history_df()

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

    today_qty_map = build_live_assignment_today_qty_map(assignment_df)

    hist_before_today = hist[hist["date_dt"] < base_dt].copy()
    common_recent_date = hist_before_today["date_dt"].max() if len(hist_before_today) > 0 else pd.NaT
    recent_qty_map = {}
    if not pd.isna(common_recent_date):
        recent_day_hist = hist_before_today[hist_before_today["date_dt"] == common_recent_date].copy()
        recent_qty_map = recent_day_hist.groupby("driver")["total_qty"].sum().to_dict()

    def workday_avg_qty(df, start_dt, end_dt):
        period = df[(df["date_dt"] >= start_dt) & (df["date_dt"] <= end_dt)].copy()
        if len(period) == 0:
            return {}, {}
        qty_sum = period.groupby("driver")["total_qty"].sum().to_dict()
        work_days = period.groupby("driver")["date_dt"].nunique().to_dict()
        return qty_sum, work_days

    w7_start = base_dt - pd.Timedelta(days=6)
    w30_start = base_dt - pd.Timedelta(days=29)
    q7, d7 = workday_avg_qty(hist, w7_start, base_dt)
    q30, d30 = workday_avg_qty(hist, w30_start, base_dt)

    rows = []
    for d in drivers:
        v_today = safe_int(today_qty_map.get(d, 0))
        v_recent = safe_int(recent_qty_map.get(d, 0))
        v_q7 = safe_int(q7.get(d, 0))
        v_d7 = safe_int(d7.get(d, 0))
        v_q30 = safe_int(q30.get(d, 0))
        v_d30 = safe_int(d30.get(d, 0))
        avg_q7 = (v_q7 / v_d7) if v_d7 > 0 else 0
        avg_q30 = (v_q30 / v_d30) if v_d30 > 0 else 0
        rows.append({
            "기사명": d,
            "오늘물량": v_today,
            "최근근무일(1일) 물량": v_recent,
            "7일 근무일평균": round(avg_q7, 1),
            "30일 근무일평균": round(avg_q30, 1),
        })

    recent_date_str = common_recent_date.strftime("%Y-%m-%d") if not pd.isna(common_recent_date) else None
    return (
        pd.DataFrame(rows)
        .sort_values(["오늘물량", "기사명"], ascending=[False, True])
        .reset_index(drop=True),
        recent_date_str,
    )


def load_geocode_cache():
    if os.path.exists(CACHE_FILE):
        try:
            df = pd.read_csv(CACHE_FILE, dtype=str)
            cache = {}
            for _, row in df.iterrows():
                addr = str(row.get("address", "")).strip()
                lat = row.get("lat")
                lon = row.get("lon")
                if addr and lat and lon and lat != "nan" and lon != "nan":
                    cache[addr] = (float(lat), float(lon))
            return cache
        except Exception:
            return {}
    return {}


def save_geocode_cache(cache):
    rows = []
    for addr, coords in cache.items():
        rows.append({
            "address": addr,
            "lat": coords[0],
            "lon": coords[1],
        })
    pd.DataFrame(rows).to_csv(CACHE_FILE, index=False, encoding="utf-8-sig")


def geocode_kakao(address, api_key, cache):
    address = str(address).strip()

    if not address or address == "nan":
        return None

    if address in cache:
        return cache[address]

    url = "https://dapi.kakao.com/v2/local/search/address.json"
    headers = {"Authorization": f"KakaoAK {api_key}"}
    params = {"query": address}

    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        if response.status_code != 200:
            return None

        data = response.json()
        documents = data.get("documents", [])
        if not documents:
            return None

        first = documents[0]
        coords = (float(first["y"]), float(first["x"]))
        cache[address] = coords
        return coords
    except Exception:
        return None


def route_index_to_label(n: int) -> str:
    label = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        label = chr(65 + rem) + label
    return label


def short_driver_name(name: str) -> str:
    text = str(name).strip()
    if not text:
        return ""
    return text[-2:]


def minutes_to_korean_text(x):
    try:
        if pd.isna(x):
            return "0시간 00분"
        total = int(x)
        hh = total // 60
        mm = total % 60
        return f"{hh}시간 {mm:02d}분"
    except Exception:
        return "0시간 00분"


def format_time_value(value):
    if pd.isna(value):
        return ""

    if isinstance(value, pd.Timestamp):
        return value.strftime("%H:%M")

    text = str(value).strip()

    m = re.match(r"^(\d{1,2}):(\d{2})", text)
    if m:
        hh = int(m.group(1))
        mm = m.group(2)
        return f"{hh:02d}:{mm}"

    try:
        num = float(value)
        if 0 <= num < 1:
            total_minutes = int(round(num * 24 * 60))
            hh = total_minutes // 60
            mm = total_minutes % 60
            return f"{hh:02d}:{mm:02d}"
    except Exception:
        pass

    return text


def time_to_minutes(t: str):
    t = str(t).strip()
    m = re.match(r"^(\d{1,2}):(\d{2})$", t)
    if not m:
        return None
    return int(m.group(1)) * 60 + int(m.group(2))


def min_to_hhmm(x):
    if pd.isna(x):
        return ""
    try:
        x = int(x)
        hh = x // 60
        mm = x % 60
        return f"{hh:02d}:{mm:02d}"
    except Exception:
        return ""


def load_assignment_store():
    if os.path.exists(ASSIGNMENT_FILE):
        try:
            with open(ASSIGNMENT_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_assignment_store(store):
    try:
        with open(ASSIGNMENT_FILE, "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False, indent=2)
        return None
    except Exception as exc:
        logger.warning("Failed to save assignment store: %s", exc)
        return str(exc)


def build_assignment_change_rows(route_summary: pd.DataFrame, old_store: dict, new_store: dict):
    changes = []
    if not isinstance(route_summary, pd.DataFrame) or len(route_summary) == 0:
        return changes

    old_store = old_store if isinstance(old_store, dict) else {}
    new_store = new_store if isinstance(new_store, dict) else {}
    sort_cols = [c for c in ["route_prefix", "route", "truck_request_id"] if c in route_summary.columns]
    work_df = route_summary.sort_values(sort_cols).reset_index(drop=True) if sort_cols else route_summary.reset_index(drop=True)
    for _, row in work_df.iterrows():
        route = str(row.get("route", "")).strip()
        truck_request_id = str(row.get("truck_request_id", "")).strip()
        if not route:
            continue
        assignment_key = make_assignment_key(route, truck_request_id)
        old_driver = str(old_store.get(assignment_key, "")).strip()
        new_driver = str(new_store.get(assignment_key, "")).strip()
        if old_driver == new_driver:
            continue
        changes.append({
            "route": route,
            "truck_request_id": truck_request_id,
            "assignment_key": assignment_key,
            "old_driver": old_driver,
            "new_driver": new_driver,
        })
    return changes


def build_debug_assignment_sample_df(route_summary: pd.DataFrame, assignment_store: dict, assignment_df: pd.DataFrame, limit: int = 10):
    if not isinstance(route_summary, pd.DataFrame) or len(route_summary) == 0:
        return pd.DataFrame(columns=["route", "truck_request_id", "assignment_key", "session_store_driver", "assignment_df_driver"])

    assignment_store = assignment_store if isinstance(assignment_store, dict) else {}
    assignment_df_driver_map = {}
    if isinstance(assignment_df, pd.DataFrame) and len(assignment_df) > 0:
        for _, row in assignment_df.iterrows():
            route = str(row.get("route", "")).strip()
            truck_request_id = str(row.get("truck_request_id", "")).strip()
            if route:
                assignment_df_driver_map[make_assignment_key(route, truck_request_id)] = str(row.get("assigned_driver", "")).strip()

    sort_cols = [c for c in ["route_prefix", "route", "truck_request_id"] if c in route_summary.columns]
    sample_df = route_summary.sort_values(sort_cols).head(limit).reset_index(drop=True) if sort_cols else route_summary.head(limit).reset_index(drop=True)
    rows = []
    for _, row in sample_df.iterrows():
        route = str(row.get("route", "")).strip()
        truck_request_id = str(row.get("truck_request_id", "")).strip()
        assignment_key = make_assignment_key(route, truck_request_id)
        rows.append({
            "route": route,
            "truck_request_id": truck_request_id,
            "assignment_key": assignment_key,
            "session_store_driver": str(assignment_store.get(assignment_key, "")).strip(),
            "assignment_df_driver": assignment_df_driver_map.get(assignment_key, ""),
        })
    return pd.DataFrame(rows)


def build_assignment_file_debug(route_summary: pd.DataFrame, last_changes):
    assignment_path = Path(ASSIGNMENT_FILE).resolve()
    exists = assignment_path.exists()
    file_store = load_assignment_store() if exists else {}
    file_store = file_store if isinstance(file_store, dict) else {}
    current_keys = current_assignment_keys(route_summary)

    file_info = {
        "exists": exists,
        "path": str(assignment_path),
        "size_bytes": assignment_path.stat().st_size if exists else 0,
        "modified_at": pd.Timestamp.fromtimestamp(assignment_path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S") if exists else "",
        "json_total_key_count": len(file_store),
        "current_dataset_route_key_count": len(current_keys),
        "json_current_dataset_key_count": sum(1 for key in current_keys if key in file_store),
    }

    rows = []
    for change in last_changes or []:
        assignment_key = str(change.get("assignment_key", "")).strip()
        if not assignment_key:
            assignment_key = make_assignment_key(change.get("route", ""), change.get("truck_request_id", ""))
        rows.append({
            "route": str(change.get("route", "")).strip(),
            "truck_request_id": str(change.get("truck_request_id", "")).strip(),
            "assignment_key": assignment_key,
            "new_driver": str(change.get("new_driver", "")).strip(),
            "file_contains_key": assignment_key in file_store,
            "file_driver": str(file_store.get(assignment_key, "")).strip(),
        })
    return file_info, pd.DataFrame(rows)


def resync_assignment_store_from_file_if_changed(route_summary: pd.DataFrame, source: str):
    file_store = load_assignment_store()
    session_store = st.session_state.get("assignment_store", {})
    file_store = file_store if isinstance(file_store, dict) else {}
    session_store = session_store if isinstance(session_store, dict) else {}
    current_keys = current_assignment_keys(route_summary)
    file_current_count = sum(1 for key in current_keys if key in file_store)
    session_current_count = sum(1 for key in current_keys if key in session_store)

    should_resync = bool(file_store and file_current_count > 0 and file_store != session_store)
    st.session_state["assignment_file_resync_debug"] = {
        "source": str(source or ""),
        "file_current_count": file_current_count,
        "session_current_count": session_current_count,
        "file_total_key_count": len(file_store),
        "session_total_key_count": len(session_store),
        "changed": should_resync,
    }

    if should_resync:
        st.session_state["assignment_store"] = file_store
        st.session_state["assignment_store_source"] = str(source or "file_resynced")
        return file_store

    if "assignment_store_source" not in st.session_state:
        st.session_state["assignment_store_source"] = "session_existing"
    return session_store


def render_debug_status_panel(
    route_summary: pd.DataFrame,
    assignment_store: dict,
    assignment_df: pd.DataFrame,
    selected_filter: str,
    main_map_key: str,
    marker_count: int,
    valid_result: pd.DataFrame,
    valid_grouped: pd.DataFrame,
):
    if not st.checkbox("디버그 표시", value=False, key="debug_status_visible"):
        return

    last_changes = st.session_state.get("last_assignment_changes", [])
    file_info, last_file_df = build_assignment_file_debug(route_summary, last_changes)

    with st.expander("디버그 상태", expanded=True):
        st.write("실행 경로 / backend 상태")
        st.json({
            "current_file": str(Path(__file__).resolve()),
            "cwd": str(Path.cwd()),
            "assignment_file": str(Path(ASSIGNMENT_FILE).resolve()),
            "backend_run_id": safe_int(st.session_state.get("backend_run_id", 0)),
            "backend_dataset_key": str(st.session_state.get("backend_dataset_key", "")),
            "backend_sync_done": str(st.session_state.get("backend_sync_done", "")),
            "disable_backend_merge": bool(st.session_state.get("disable_backend_merge", False)),
            "assignment_store_source": str(st.session_state.get("assignment_store_source", "")),
            "assignment_local_source_dataset_key": str(st.session_state.get("assignment_local_source_dataset_key", "")),
            "main_map_key_nonce": safe_int(st.session_state.get("main_map_key_nonce", 0)),
            "main_map_key": str(main_map_key or ""),
        })

        st.write("assignment file/session resync")
        st.json(st.session_state.get("assignment_file_resync_debug", {}))

        st.write("backend merge 판단")
        st.json(st.session_state.get("backend_merge_debug", {}))

        st.write("메인 지도 렌더 직전 상태")
        st.json({
            "main_map_key": str(main_map_key or ""),
            "overlay_cache_key": str(st.session_state.get("main_map_data_cache_key", "")),
            "overlay_cache_status": str(st.session_state.get("main_overlay_cache_status", "")),
            "marker_count": safe_int(marker_count),
            "selected_filter": str(selected_filter or ""),
            "assignment_df_rows": safe_int(len(assignment_df)) if isinstance(assignment_df, pd.DataFrame) else 0,
            "valid_result_rows": safe_int(len(valid_result)) if isinstance(valid_result, pd.DataFrame) else 0,
            "valid_grouped_rows": safe_int(len(valid_grouped)) if isinstance(valid_grouped, pd.DataFrame) else 0,
        })

        st.write("route_assignments.json 상태")
        st.json(file_info)
        if len(last_file_df) > 0:
            st.write("마지막 변경 route가 JSON에 저장됐는지")
            st.dataframe(last_file_df, use_container_width=True)
        else:
            st.caption("마지막 변경 route 기록이 아직 없습니다.")

        st.write("마지막 저장 변경 route")
        last_changes_df = pd.DataFrame(last_changes or [])
        if len(last_changes_df) > 0:
            st.dataframe(last_changes_df, use_container_width=True)
        else:
            st.caption("이번 세션에서 추적된 저장 변경 route가 없습니다.")

        st.write("현재 route assignment 샘플")
        st.dataframe(
            build_debug_assignment_sample_df(route_summary, assignment_store, assignment_df, limit=10),
            use_container_width=True,
        )


def load_cancel_store():
    if os.path.exists(CANCEL_FILE):
        try:
            with open(CANCEL_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_cancel_store(store):
    try:
        with open(CANCEL_FILE, "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def make_cancel_key(base_date: str, milkrun_no: str, origin_center: str, address_norm: str) -> str:
    return (
        f"{str(base_date).strip()}__"
        f"{str(milkrun_no).strip()}__"
        f"{str(origin_center).strip()}__"
        f"{str(address_norm).strip()}"
    )


def build_cancel_management_df(grouped_delivery: pd.DataFrame, route_driver_map: dict, base_date: str, cancel_store: dict):
    if len(grouped_delivery) == 0:
        return pd.DataFrame()

    work_df = grouped_delivery.copy()
    work_df["assigned_driver"] = work_df["route"].map(route_driver_map).fillna("")
    work_df["cancel_key"] = work_df.apply(
        lambda row: make_cancel_key(
            base_date,
            row.get("milkrun_no", ""),
            row.get("origin_center", ""),
            row.get("address_norm", ""),
        ),
        axis=1,
    )
    work_df["총수량"] = work_df.apply(
        lambda row: safe_int(row.get("ae_sum", 0)) + safe_int(row.get("af_sum", 0)) + safe_int(row.get("ag_sum", 0)),
        axis=1,
    )
    work_df["취소건수"] = work_df["cancel_key"].map(lambda key: safe_int((cancel_store.get(key) or {}).get("cancel_count", 0)))
    work_df["취소수량"] = work_df["cancel_key"].map(lambda key: safe_int((cancel_store.get(key) or {}).get("cancel_qty", 0)))
    work_df["취소사유"] = work_df["cancel_key"].map(lambda key: str((cancel_store.get(key) or {}).get("reason", "")).strip())
    work_df["정산제외"] = work_df["취소건수"].apply(lambda v: bool(safe_int(v) > 0))
    cols = [
        "milkrun_no", "origin_center", "route_prefix", "route", "truck_request_id", "assigned_driver",
        "house_order", "company_name", "address", "stop_count", "총수량",
        "취소건수", "취소수량", "정산제외", "취소사유", "address_norm", "cancel_key",
    ]
    existing_cols = [c for c in cols if c in work_df.columns]
    sort_cols = [c for c in ["assigned_driver", "company_name", "milkrun_no", "route_prefix", "route", "house_order"] if c in work_df.columns]
    return work_df[existing_cols].sort_values(by=sort_cols).reset_index(drop=True)


def make_assignment_key(route: str, truck_request_id: str) -> str:
    return f"{str(route).strip()}__{str(truck_request_id).strip()}"


def is_header_like_route_values(route_value, truck_request_id_value) -> bool:
    route_text = str(route_value).strip().lower()
    truck_text = str(truck_request_id_value).strip().lower()
    header_routes = {"루트 번호", "루트번호", "route", "route no", "route_no"}
    header_trucks = {"트럭 요청 id", "트럭요청id", "truck request id", "truck_request_id"}
    return route_text in header_routes or truck_text in header_trucks


def _df_to_records_for_json(df: pd.DataFrame):
    if df is None or len(df) == 0:
        return []

    out = []
    for row in df.fillna("").to_dict(orient="records"):
        r = row.copy()
        if "coords" in r:
            r["coords"] = _coords_to_jsonable(r.get("coords"))
        out.append(r)
    return out


def _records_to_df_with_coords(records):
    df = pd.DataFrame(records or [])
    if len(df) == 0:
        return df

    if "coords" in df.columns:
        df["coords"] = df["coords"].apply(_normalize_coords)
    return df


def save_share_payload(
    share_name: str,
    map_html: str,
    assignment_df: pd.DataFrame,
    assigned_summary: pd.DataFrame,
    result_delivery_df: pd.DataFrame = None,
    grouped_delivery_df: pd.DataFrame = None,
    pickup_grouped_delivery_df: pd.DataFrame = None,
    route_prefix_map: dict = None,
    truck_request_map: dict = None,
    route_line_label: dict = None,
    route_camp_map: dict = None,
    camp_coords: dict = None,
    group_assignment_df: pd.DataFrame = None,
):
    payload_path = os.path.join(SHARE_DIR, f"{share_name}.json")
    payload = {
        "map_html": map_html,
        "assignment_rows": assignment_df.fillna("").to_dict(orient="records"),
        "assigned_summary_rows": assigned_summary.fillna("").to_dict(orient="records"),
        "result_delivery_rows": _df_to_records_for_json(result_delivery_df) if result_delivery_df is not None else [],
        "grouped_delivery_rows": _df_to_records_for_json(grouped_delivery_df) if grouped_delivery_df is not None else [],
        "pickup_grouped_delivery_rows": _df_to_records_for_json(pickup_grouped_delivery_df) if pickup_grouped_delivery_df is not None else [],
        "route_prefix_map": route_prefix_map or {},
        "truck_request_map": truck_request_map or {},
        "route_line_label": route_line_label or {},
        "route_camp_map": route_camp_map or {},
        "camp_coords": camp_coords or {},
        "group_assignment_rows": group_assignment_df.fillna("").to_dict(orient="records") if group_assignment_df is not None else [],
    }
    with open(payload_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)


def load_share_payload(share_name: str):
    payload_path = os.path.join(SHARE_DIR, f"{share_name}.json")
    if not os.path.exists(payload_path):
        return None
    try:
        with open(payload_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _filter_shared_view(result_df: pd.DataFrame, grouped_df: pd.DataFrame, mode: str, selected_driver: str, selected_group: str):
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


def _build_shared_summary(result_df: pd.DataFrame, grouped_df: pd.DataFrame):
    route_count = safe_int(result_df["route"].nunique()) if len(result_df) > 0 and "route" in result_df.columns else 0
    driver_count = safe_int(result_df["assigned_driver"].fillna("").astype(str).str.strip().replace("", pd.NA).dropna().nunique()) if len(result_df) > 0 and "assigned_driver" in result_df.columns else 0
    group_count = safe_int(result_df["추천그룹"].fillna("").astype(str).str.strip().replace("", pd.NA).dropna().nunique()) if len(result_df) > 0 and "추천그룹" in result_df.columns else 0
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
        key_series = grouped_df["coords"].apply(lambda c: f"{round(float(c[0]), 6)}_{round(float(c[1]), 6)}" if isinstance(c, (tuple, list)) and len(c) == 2 else "")
        overlap_count = safe_int((key_series.value_counts() > 1).sum())

    return route_count, driver_count, group_count, small_box_total, medium_box_total, large_box_total, box_total, overlap_count


def _natural_desc_sort_key(value):
    text = str(value).strip()
    return [int(token) if token.isdigit() else token.lower() for token in re.split(r"(\d+)", text)]




def _build_driver_overview_df(result_df: pd.DataFrame, grouped_df: pd.DataFrame):
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
    out = out.sort_values(["총박스", "총걸린분", "assigned_driver"], ascending=[False, False, True]).reset_index(drop=True)
    return out


def _build_camp_driver_summary_df(result_df: pd.DataFrame, grouped_df: pd.DataFrame, route_camp_map: dict):
    if len(result_df) == 0:
        return pd.DataFrame(columns=["camp_name", "assigned_driver", "회전수", "총박스"])

    assigned = result_df.copy()
    assigned["assigned_driver"] = assigned.get("assigned_driver", "").fillna("").astype(str).str.strip()
    assigned = assigned[assigned["assigned_driver"] != ""].copy()
    if len(assigned) == 0:
        return pd.DataFrame(columns=["camp_name", "assigned_driver", "회전수", "총박스"])

    route_driver_df = (
        assigned[["route", "assigned_driver"]]
        .dropna(subset=["route"])
        .drop_duplicates(subset=["route"])
    )

    grouped2 = grouped_df.copy()
    if len(grouped2) == 0:
        return pd.DataFrame(columns=["camp_name", "assigned_driver", "회전수", "총박스"])

    grouped2 = grouped2.merge(route_driver_df, on="route", how="left", suffixes=("", "_route"))
    if "assigned_driver_route" in grouped2.columns:
        grouped2["assigned_driver"] = grouped2.get("assigned_driver_route", "").fillna(grouped2.get("assigned_driver", ""))
        grouped2 = grouped2.drop(columns=["assigned_driver_route"])

    grouped2["assigned_driver"] = grouped2.get("assigned_driver", "").fillna("").astype(str).str.strip()
    grouped2 = grouped2[grouped2["assigned_driver"] != ""].copy()
    if len(grouped2) == 0:
        return pd.DataFrame(columns=["camp_name", "assigned_driver", "회전수", "총박스"])

    grouped2["camp_code"] = grouped2["route"].map(route_camp_map).fillna(grouped2.get("camp_code", ""))
    grouped2["camp_name"] = grouped2["camp_code"].map(lambda x: CAMP_INFO.get(x, {}).get("camp_name", ""))
    grouped2["camp_name"] = grouped2["camp_name"].fillna(grouped2.get("camp_name", "")).astype(str).str.strip()
    grouped2 = grouped2[grouped2["camp_name"] != ""].copy()
    if len(grouped2) == 0:
        return pd.DataFrame(columns=["camp_name", "assigned_driver", "회전수", "총박스"])

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

    camp_order = [v["camp_name"] for v in CAMP_INFO.values()]
    summary_df["camp_order"] = summary_df["camp_name"].apply(lambda x: camp_order.index(x) if x in camp_order else len(camp_order))
    summary_df = summary_df.sort_values(
        ["camp_order", "camp_name", "총박스", "회전수", "assigned_driver"],
        ascending=[True, True, False, False, True],
    ).reset_index(drop=True)
    return summary_df[["camp_name", "assigned_driver", "회전수", "총박스"]]


def make_stop_div_icon(route_color: str, stop_text: str, size_scale: float = 1.0, border_color: str = "#ffffff"):
    base_size = 28
    icon_size = max(16, int(round(base_size * size_scale)))
    border_size = max(1, int(round(2 * size_scale)))
    line_height = max(14, icon_size - border_size * 2)
    anchor = icon_size // 2
    font_size = "11px" if len(str(stop_text)) <= 2 else "9px"
    html = f"""
    <div style="
        width:{icon_size}px;
        height:{icon_size}px;
        border-radius:50%;
        background:{route_color};
        border:{border_size}px solid {border_color};
        color:#ffffff;
        text-align:center;
        line-height:{line_height}px;
        font-size:{font_size};
        font-weight:700;
        box-shadow:0 0 3px rgba(0,0,0,0.45);
    ">{stop_text}</div>
    """
    return DivIcon(html=html, icon_size=(icon_size, icon_size), icon_anchor=(anchor, anchor))


def make_assigned_square_icon(route_color: str, stop_text: str, size_scale: float = 1.0, border_color: str = "#ffffff"):
    base_size = 30
    icon_size = max(16, int(round(base_size * size_scale)))
    border_size = max(1, int(round(2 * size_scale)))
    border_radius = max(3, int(round(6 * size_scale)))
    line_height = max(14, icon_size - border_size * 2)
    anchor = icon_size // 2
    font_size = "11px" if len(str(stop_text)) <= 2 else "9px"
    html = f"""
    <div style="
        width:{icon_size}px;
        height:{icon_size}px;
        background:{route_color};
        border:{border_size}px solid {border_color};
        border-radius:{border_radius}px;
        color:#ffffff;
        text-align:center;
        line-height:{line_height}px;
        font-size:{font_size};
        font-weight:700;
        box-shadow:0 0 3px rgba(0,0,0,0.45);
    ">{stop_text}</div>
    """
    return DivIcon(html=html, icon_size=(icon_size, icon_size), icon_anchor=(anchor, anchor))


def make_lightweight_stop_label_icon(route_color: str, stop_text: str, assigned: bool = False):
    label = re.sub(r"[<>'\"\s]", "", str(stop_text or "").strip())[:4] or "?"
    width = 26 if len(label) <= 2 else 34
    radius = 5 if assigned else 13
    font_size = "10px" if len(label) <= 3 else "9px"
    html = (
        f"<div style=\"width:{width}px;height:22px;border-radius:{radius}px;"
        f"background:{route_color};border:1px solid #fff;color:#fff;"
        f"text-align:center;line-height:20px;font-size:{font_size};"
        "font-weight:700;box-shadow:0 1px 3px rgba(0,0,0,.35);\">"
        f"{label}</div>"
    )
    return DivIcon(html=html, icon_size=(width, 22), icon_anchor=(width // 2, 11))


def make_diamond_div_icon(bg_color: str, text_value: str):
    html = f"""
    <div style="
        width:24px;
        height:24px;
        background:{bg_color};
        border:2px solid #ffffff;
        transform:rotate(45deg);
        box-shadow:0 0 3px rgba(0,0,0,0.45);
        position:relative;
    ">
        <div style="
            position:absolute;
            inset:0;
            display:flex;
            align-items:center;
            justify-content:center;
            color:#ffffff;
            font-size:10px;
            font-weight:700;
            transform:rotate(-45deg);
        ">{text_value}</div>
    </div>
    """
    return DivIcon(html=html, icon_size=(24, 24), icon_anchor=(12, 12))


def make_square_div_icon(bg_color: str, text_value: str):
    html = f"""
    <div style="
        width:24px;
        height:24px;
        background:{bg_color};
        border:2px solid #ffffff;
        border-radius:4px;
        color:#ffffff;
        text-align:center;
        line-height:20px;
        font-size:10px;
        font-weight:700;
        box-shadow:0 0 3px rgba(0,0,0,0.45);
    ">{text_value}</div>
    """
    return DivIcon(html=html, icon_size=(24, 24), icon_anchor=(12, 12))


def make_camp_icon(camp_code: str):
    info = CAMP_INFO.get(camp_code, {})
    icon_type = info.get("icon_type", "square")
    icon_text = info.get("icon_text", "C")

    if icon_type == "diamond":
        return make_diamond_div_icon("#111111", icon_text)
    return make_square_div_icon("#111111", icon_text)


# =========================
# 캐시 함수
# =========================
@st.cache_data(show_spinner=False)
def read_excel_general_sheet(file_bytes: bytes):
    return pd.read_excel(BytesIO(file_bytes), sheet_name="일반차량")


@st.cache_data(show_spinner=False)
def build_base_data(file_bytes: bytes):
    df = read_excel_general_sheet(file_bytes)

    current_route = None
    current_truck_request_id = None
    stop_order = 0
    parsed = []

    for _, row in df.iterrows():
        route_val = row.iloc[0]           # A열
        truck_request_id = row.iloc[2]    # C열
        milkrun_no = row.iloc[8]          # I열
        spu_center = row.iloc[12]         # M열
        origin_center = row.iloc[13]      # N열
        time_val = row.iloc[TIME_COL_INDEX]
        company_id = row.iloc[22]         # W열
        company_name = row.iloc[23]       # X열
        address = row.iloc[24]            # Y열
        ae = row.iloc[30]                 # AE열
        af = row.iloc[31]                 # AF열
        ag = row.iloc[32]                 # AG열

        if pd.notna(route_val):
            current_route = str(route_val).strip()
            current_truck_request_id = str(truck_request_id).strip() if pd.notna(truck_request_id) else ""
            stop_order = 1
        else:
            stop_order += 1

        if pd.notna(address) and str(address).strip():
            company_id_str = str(company_id).strip() if pd.notna(company_id) else ""
            company_name_str = str(company_name).strip() if pd.notna(company_name) else ""
            milkrun_no_str = str(milkrun_no).strip() if pd.notna(milkrun_no) else ""
            spu_center_str = str(spu_center).strip() if pd.notna(spu_center) else ""
            origin_center_str = str(origin_center).strip() if pd.notna(origin_center) else ""
            time_str = format_time_value(time_val)

            is_center_row = spu_center_str in CENTER_CODES

            parsed.append({
                "route": current_route,
                "truck_request_id": current_truck_request_id,
                "stop_order": stop_order,
                "time_str": time_str,
                "time_minutes": time_to_minutes(time_str),
                "company_id": company_id_str,
                "company_name": company_name_str,
                "address": str(address).strip(),
                "address_norm": normalize_address(address),
                "ae": pd.to_numeric(ae, errors="coerce") if pd.notna(ae) else 0,
                "af": pd.to_numeric(af, errors="coerce") if pd.notna(af) else 0,
                "ag": pd.to_numeric(ag, errors="coerce") if pd.notna(ag) else 0,
                "milkrun_no": milkrun_no_str,
                "spu_center": spu_center_str,
                "origin_center": origin_center_str,
                "is_center": is_center_row,
            })

    result = pd.DataFrame(parsed)

    if len(result) == 0:
        return {
            "result_all": pd.DataFrame(),
            "result_delivery": pd.DataFrame(),
            "grouped_delivery": pd.DataFrame(),
            "route_summary": pd.DataFrame(),
            "route_prefix_map": {},
            "route_total_map": {},
            "truck_request_map": {},
            "route_line_label": {},
            "route_camp_map": {},
        }

    result["ae"] = pd.to_numeric(result["ae"], errors="coerce").fillna(0)
    result["af"] = pd.to_numeric(result["af"], errors="coerce").fillna(0)
    result["ag"] = pd.to_numeric(result["ag"], errors="coerce").fillna(0)

    # 라우트별 캠프 매핑
    route_camp_map = (
        result[result["spu_center"].isin(list(CENTER_CODES))]
        .groupby("route")["spu_center"]
        .first()
        .to_dict()
    )

    # 배송지 데이터만 별도 사용 (센터 행 제외)
    result_delivery = result[result["is_center"] == False].copy()

    if len(result_delivery) == 0:
        return {
            "result_all": result,
            "result_delivery": pd.DataFrame(),
            "grouped_delivery": pd.DataFrame(),
            "route_summary": pd.DataFrame(),
            "route_prefix_map": {},
            "route_total_map": {},
            "truck_request_map": {},
            "route_line_label": {},
            "route_camp_map": route_camp_map,
        }

    # 같은 업체/주소라도 센터명이 다르면 별도 행으로 취급해야 한다.
    house_order_df = (
        result_delivery.groupby(["route", "milkrun_no", "origin_center", "address_norm"], as_index=False)
        .agg(first_stop=("stop_order", "min"))
        .sort_values(["route", "first_stop", "milkrun_no", "origin_center", "address_norm"])
        .reset_index(drop=True)
    )
    house_order_df["house_order"] = house_order_df.groupby("route").cumcount() + 1

    result_delivery = result_delivery.merge(
        house_order_df[["route", "milkrun_no", "origin_center", "address_norm", "house_order"]],
        on=["route", "milkrun_no", "origin_center", "address_norm"],
        how="left"
    )

    all_routes_sorted = sorted(result_delivery["route"].dropna().astype(str).unique().tolist())
    route_prefix_map = {
        route: route_index_to_label(i + 1)
        for i, route in enumerate(all_routes_sorted)
    }

    result_delivery["route_prefix"] = result_delivery["route"].map(route_prefix_map)
    result_delivery["pin_label"] = result_delivery.apply(
        lambda r: f"{r['route_prefix']}{safe_int(r['house_order'])}",
        axis=1
    )

    route_total_map = result_delivery.groupby("route")["house_order"].max().to_dict()
    truck_request_map = result_delivery.groupby("route")["truck_request_id"].first().to_dict()

    route_qty_map = (
        result_delivery.groupby("route", as_index=True)
        .agg(
            ae_sum=("ae", "sum"),
            af_sum=("af", "sum"),
            ag_sum=("ag", "sum"),
        )
    )

    route_line_label = {}
    for route, row in route_qty_map.iterrows():
        small = safe_int(row["ae_sum"])
        mid = safe_int(row["af_sum"])
        large = safe_int(row["ag_sum"])
        total = small + mid + large
        prefix = route_prefix_map.get(route, "")
        camp_code = route_camp_map.get(route, "")
        camp_text = ""
        if camp_code in CAMP_INFO:
            camp_text = f" | {CAMP_INFO[camp_code]['camp_name']}"
        route_line_label[route] = f"{prefix} | {total}박스 | {small}/{mid}/{large}{camp_text}"

    grouped_delivery = (
        result_delivery.groupby(["route", "milkrun_no", "origin_center", "address_norm"], as_index=False)
        .agg(
            truck_request_id=("truck_request_id", "first"),
            company_id=("company_id", "first"),
            company_name=("company_name", "first"),
            address=("address", "first"),
            origin_center=("origin_center", "first"),
            stop_count=("stop_order", "count"),
            first_stop=("stop_order", "min"),
            house_order=("house_order", "first"),
            route_prefix=("route_prefix", "first"),
            pin_label=("pin_label", "first"),
            first_time=("time_str", "first"),
            ae_sum=("ae", "sum"),
            af_sum=("af", "sum"),
            ag_sum=("ag", "sum"),
        )
        .sort_values(["route", "house_order"])
        .reset_index(drop=True)
    )

    if len(grouped_delivery) > 0:
        grouped_delivery["route_total"] = grouped_delivery["route"].map(route_total_map)
        grouped_delivery["hover_text"] = grouped_delivery.apply(
            lambda r: f"{r['pin_label']} / {r['first_time']} / {safe_int(r['ae_sum'])}/{safe_int(r['af_sum'])}/{safe_int(r['ag_sum'])}".strip(),
            axis=1
        )
    else:
        grouped_delivery["route_total"] = 0
        grouped_delivery["hover_text"] = ""

    route_time_summary = (
        result_delivery.groupby(["route"], as_index=False)
        .agg(
            start_min=("time_minutes", "min"),
            end_max=("time_minutes", "max")
        )
    )

    route_stop_count = (
        result_delivery.groupby("route", as_index=False)["address_norm"]
        .nunique()
        .rename(columns={"address_norm": "스톱수"})
    )

    route_summary = (
        result_delivery.groupby(["route", "truck_request_id"], as_index=False)
        .agg(
            소형합=("ae", "sum"),
            중형합=("af", "sum"),
            대형합=("ag", "sum"),
        )
        .sort_values("route")
        .reset_index(drop=True)
    )

    route_summary["총합"] = route_summary["소형합"] + route_summary["중형합"] + route_summary["대형합"]
    route_summary = route_summary.merge(route_stop_count, on="route", how="left")
    route_summary = route_summary.merge(route_time_summary, on="route", how="left")
    route_summary["route_prefix"] = route_summary["route"].map(route_prefix_map)
    route_summary["camp_code"] = route_summary["route"].map(route_camp_map)
    route_summary["camp_name"] = route_summary["camp_code"].map(
        lambda x: CAMP_INFO.get(x, {}).get("camp_name", "")
    )
    route_summary["시작시간"] = route_summary["start_min"].apply(min_to_hhmm)
    route_summary["종료시간"] = route_summary["end_max"].apply(min_to_hhmm)
    route_summary["총걸린분"] = (
        route_summary["end_max"].fillna(0) - route_summary["start_min"].fillna(0)
    ).clip(lower=0)
    route_summary["총걸린시간"] = route_summary["총걸린분"].apply(minutes_to_korean_text)

    header_like_routes = route_summary[
        route_summary.apply(
            lambda r: is_header_like_route_values(r.get("route", ""), r.get("truck_request_id", "")),
            axis=1,
        )
    ]["route"].astype(str).tolist()

    if header_like_routes:
        route_summary = route_summary[~route_summary["route"].astype(str).isin(header_like_routes)].copy()
        result = result[~result["route"].astype(str).isin(header_like_routes)].copy()
        result_delivery = result_delivery[~result_delivery["route"].astype(str).isin(header_like_routes)].copy()
        grouped_delivery = grouped_delivery[~grouped_delivery["route"].astype(str).isin(header_like_routes)].copy()
        route_prefix_map = {k: v for k, v in route_prefix_map.items() if str(k) not in set(map(str, header_like_routes))}
        route_total_map = {k: v for k, v in route_total_map.items() if str(k) not in set(map(str, header_like_routes))}
        truck_request_map = {k: v for k, v in truck_request_map.items() if str(k) not in set(map(str, header_like_routes))}
        route_line_label = {k: v for k, v in route_line_label.items() if str(k) not in set(map(str, header_like_routes))}
        route_camp_map = {k: v for k, v in route_camp_map.items() if str(k) not in set(map(str, header_like_routes))}

    return {
        "result_all": result,
        "result_delivery": result_delivery,
        "grouped_delivery": grouped_delivery,
        "route_summary": route_summary,
        "route_prefix_map": route_prefix_map,
        "route_total_map": route_total_map,
        "truck_request_map": truck_request_map,
        "route_line_label": route_line_label,
        "route_camp_map": route_camp_map,
    }


def attach_coords_by_unique_address(df: pd.DataFrame, cache: dict):
    if len(df) == 0:
        df = df.copy()
        df["coords"] = None
        return df, cache

    out = df.copy()
    unique_addresses = out["address"].dropna().astype(str).str.strip().unique().tolist()

    coord_map = {}
    for addr in unique_addresses:
        if not addr:
            coord_map[addr] = None
            continue
        coords = geocode_kakao(addr, KAKAO_API_KEY, cache)
        coord_map[addr] = coords

    out["coords"] = out["address"].map(coord_map)
    return out, cache


def resolve_camp_coords(cache: dict):
    camp_coords = {}
    changed = False

    for camp_code, info in CAMP_INFO.items():
        addr = info["address"]
        coords = geocode_kakao(addr, KAKAO_API_KEY, cache)
        camp_coords[camp_code] = coords
        if coords is not None and addr not in cache:
            cache[addr] = coords
            changed = True

    if changed:
        save_geocode_cache(cache)

    return camp_coords, cache


def build_assignment_df(route_summary: pd.DataFrame, assignment_store: dict):
    rows = []
    for _, row in route_summary.iterrows():
        route = row["route"]
        truck_request_id = row["truck_request_id"]
        assignment_key = make_assignment_key(route, truck_request_id)
        selected_driver = assignment_store.get(assignment_key, "")

        rows.append({
            "route": route,
            "route_prefix": row["route_prefix"],
            "camp_name": row.get("camp_name", ""),
            "truck_request_id": truck_request_id,
            "스톱수": safe_int(row["스톱수"]),
            "시작시간": str(row["시작시간"]),
            "종료시간": str(row["종료시간"]),
            "총걸린분": safe_int(row["총걸린분"]),
            "총걸린시간": str(row["총걸린시간"]),
            "소형합": safe_int(row["소형합"]),
            "중형합": safe_int(row["중형합"]),
            "대형합": safe_int(row["대형합"]),
            "총합": safe_int(row["총합"]),
            "assigned_driver": selected_driver
        })
    return pd.DataFrame(rows)


def build_assigned_summary(assignment_df: pd.DataFrame):
    if len(assignment_df) == 0:
        return pd.DataFrame()

    assigned_only = assignment_df.copy()
    assigned_only["assigned_driver"] = assigned_only["assigned_driver"].fillna("").astype(str)

    assigned_summary = (
        assigned_only[assigned_only["assigned_driver"].str.strip() != ""]
        .groupby("assigned_driver", as_index=False)
        .agg(
            담당루트수=("route", "count"),
            총스톱수=("스톱수", "sum"),
            총걸린분=("총걸린분", "sum"),
            소형합=("소형합", "sum"),
            중형합=("중형합", "sum"),
            대형합=("대형합", "sum"),
            총박스합계=("총합", "sum")
        )
        .sort_values(["assigned_driver"])
        .reset_index(drop=True)
    )

    if len(assigned_summary) > 0:
        assigned_summary["총걸린시간"] = assigned_summary["총걸린분"].apply(minutes_to_korean_text)

    return assigned_summary


def apply_group_driver_assignments(
    route_summary: pd.DataFrame,
    assignment_store: dict,
    group_assignment_df: pd.DataFrame,
    group_driver_selection: dict,
):
    if len(route_summary) == 0 or len(group_assignment_df) == 0:
        return assignment_store.copy(), 0

    route_to_group = {
        str(route).strip(): str(group_name).strip()
        for route, group_name in zip(group_assignment_df["route"], group_assignment_df["추천그룹"])
        if str(route).strip()
    }
    updated_store = assignment_store.copy()
    updated_count = 0

    for _, row in route_summary.iterrows():
        route = str(row["route"]).strip()
        truck_request_id = str(row["truck_request_id"]).strip()
        group_name = route_to_group.get(route, "")
        selected_driver = str(group_driver_selection.get(group_name, "")).strip()
        if not selected_driver:
            continue

        assignment_key = make_assignment_key(route, truck_request_id)
        if str(updated_store.get(assignment_key, "")).strip() == selected_driver:
            continue
        updated_store[assignment_key] = selected_driver
        updated_count += 1

    return updated_store, updated_count


def render_group_driver_assignment_form(
    route_summary: pd.DataFrame,
    drivers,
    assignment_store: dict,
    group_assignment_df: pd.DataFrame,
    uploaded_filename: str,
    base_date_str: str,
):
    if len(group_assignment_df) == 0:
        return assignment_store

    st.subheader("추천그룹별 기사 배정")
    st.caption("추천그룹 단위로 기사명을 먼저 선택하고, 적용 후 아래 기사 배정 표에서 미세 조정할 수 있습니다.")

    driver_options = [""] + drivers
    group_options = sorted(
        group_assignment_df["추천그룹"].dropna().astype(str).unique().tolist(),
        key=lambda x: int(str(x).replace("추천그룹 ", "")) if str(x).replace("추천그룹 ", "").isdigit() else 999,
    )

    with st.form("group_driver_assignment_form", clear_on_submit=False):
        group_driver_selection = {}
        for group_name in group_options:
            c1, c2 = st.columns([1.1, 1.7])
            c1.write(group_name)
            selected_driver = c2.selectbox(
                f"추천그룹기사선택_{group_name}",
                options=driver_options,
                index=0,
                key=f"group_driver_select_{group_name}",
                label_visibility="collapsed",
            )
            group_driver_selection[group_name] = selected_driver

        group_apply_submitted = st.form_submit_button("추천그룹 배정 적용")

    if group_apply_submitted:
        updated_store, updated_count = apply_group_driver_assignments(
            route_summary=route_summary,
            assignment_store=assignment_store,
            group_assignment_df=group_assignment_df,
            group_driver_selection=group_driver_selection,
        )
        if updated_count <= 0:
            st.warning("추천그룹 배정으로 실제 변경된 route가 없습니다.")
            return assignment_store

        st.session_state["last_assignment_changes"] = build_assignment_change_rows(
            route_summary,
            assignment_store,
            updated_store,
        )
        st.session_state["assignment_store"] = updated_store
        persist_result = persist_assignment_store(
            route_summary=route_summary,
            assignment_store=updated_store,
            uploaded_filename=uploaded_filename,
            base_date=base_date_str,
        )
        if persist_result["ok"]:
            queue_assignment_feedback(
                "success",
                f"추천그룹 배정을 적용했습니다. ({updated_count}개 route 로컬 반영, 서버 저장 {persist_result['saved_count']}건)",
            )
        elif not persist_result.get("local_ok", True):
            queue_assignment_feedback(
                "error",
                f"추천그룹 배정은 현재 화면에는 반영했지만 로컬 파일 저장에 실패했습니다. 새로고침하면 유지되지 않을 수 있습니다. {persist_result['message']}",
            )
        else:
            queue_assignment_feedback(
                "warning",
                f"추천그룹 배정은 로컬 화면에 즉시 반영했고 서버 저장은 실패했습니다. {persist_result['message']}",
            )
        rerun_after_assignment_submit()
        return updated_store

    return assignment_store


def styled_qty_value_html(value: int, kind: str) -> str:
    palette = {
        "small": {"bg": "rgba(148, 163, 184, 0.10)", "text": "#e2e8f0"},
        "medium": {"bg": "rgba(148, 163, 184, 0.14)", "text": "#e2e8f0"},
        "large": {"bg": "rgba(148, 163, 184, 0.18)", "text": "#f1f5f9"},
        "total": {"bg": "rgba(226, 232, 240, 0.18)", "text": "#f8fafc"},
    }
    style = palette.get(kind, palette["small"])
    weight = "700" if kind == "total" else "600"
    return (
        "<span style='display:inline-flex; align-items:center; justify-content:flex-end;"
        " min-width:44px; padding:2px 8px; border-radius:6px;"
        f" background:{style['bg']}; color:{style['text']};"
        f" font-weight:{weight}; letter-spacing:0.01em; line-height:1.35;'>"
        f"{safe_int(value)}"
        "</span>"
    )


def marker_overlap_identity(row) -> str:
    try:
        pickup_key = str(row.get("pickup_map_key", "") or "").strip()
    except Exception:
        pickup_key = ""
    if pickup_key:
        return pickup_key

    try:
        route = str(row.get("route", "") or "").strip()
        address_norm = str(row.get("address_norm", "") or "").strip()
        company_key = str(row.get("company_id", "") or "").strip() or str(row.get("company_name", "") or "").strip()
        milkrun_no = str(row.get("milkrun_no", "") or "").strip()
        origin_center = str(row.get("origin_center", "") or "").strip()
        return f"{route}|{company_key}|{address_norm}|{milkrun_no}|{origin_center}"
    except Exception:
        return ""


def build_overlap_info_map(valid_grouped: pd.DataFrame) -> dict:
    overlap_info_map = {}
    if len(valid_grouped) == 0:
        return overlap_info_map

    overlap_df = valid_grouped.copy()
    overlap_df["coord_key"] = overlap_df["coords"].apply(
        lambda c: f"{round(float(c[0]), 6)}_{round(float(c[1]), 6)}" if isinstance(c, (tuple, list)) and len(c) == 2 else ""
    )
    overlap_df["coord_rank"] = overlap_df.groupby("coord_key").cumcount()

    for key, part in overlap_df.groupby("coord_key"):
        if key == "":
            continue
        overlap_info_map[key] = {
            "count": safe_int(len(part)),
            "rank_map": {marker_overlap_identity(r): safe_int(r.get("coord_rank", 0)) for _, r in part.iterrows()},
        }

    return overlap_info_map


def spread_overlapping_marker(lat: float, lon: float, overlap_info: dict, row: pd.Series):
    base_lat, base_lon = lat, lon
    overlap_count = safe_int(overlap_info.get("count", 1))
    overlap_rank = safe_int(overlap_info.get("rank_map", {}).get(marker_overlap_identity(row), 0))

    if overlap_count > 1:
        angle = (2 * math.pi * overlap_rank) / overlap_count
        radius = 0.00018
        lat = base_lat + (radius * math.sin(angle))
        lon = base_lon + (radius * math.cos(angle))

    return lat, lon, overlap_count


def build_lightweight_stop_rows(valid_grouped: pd.DataFrame, route_prefix_map: dict) -> pd.DataFrame:
    if not isinstance(valid_grouped, pd.DataFrame) or len(valid_grouped) == 0:
        return pd.DataFrame()

    work = valid_grouped.copy()
    if "route" not in work.columns:
        return work

    if "address_norm" not in work.columns:
        work["address_norm"] = ""

    work["_lightweight_row_order"] = range(len(work))
    work["_lightweight_address_norm"] = work["address_norm"].fillna("").astype(str).str.strip()
    if "house_order" in work.columns:
        work["_lightweight_house_order"] = pd.to_numeric(work["house_order"], errors="coerce").fillna(0)
    else:
        work["_lightweight_house_order"] = 0

    work = work.sort_values(
        ["route", "_lightweight_house_order", "_lightweight_row_order"],
        kind="stable",
    )

    merged_rows = []
    for route, route_part in work.groupby("route", sort=False):
        route_part = route_part.copy()
        route_part["_lightweight_stop_key"] = route_part.apply(
            lambda r: r["_lightweight_address_norm"] or f"__row_{safe_int(r['_lightweight_row_order'])}",
            axis=1,
        )

        route_rows = []
        for _, part in route_part.groupby("_lightweight_stop_key", sort=False):
            part = part.sort_values(["_lightweight_house_order", "_lightweight_row_order"], kind="stable")
            row = part.iloc[0].to_dict()

            for qty_col in ["ae_sum", "af_sum", "ag_sum"]:
                if qty_col in part.columns:
                    row[qty_col] = safe_int(pd.to_numeric(part[qty_col], errors="coerce").fillna(0).sum())

            company_names = [
                str(v).strip()
                for v in part.get("company_name", pd.Series(dtype=object)).fillna("").tolist()
                if str(v).strip()
            ]
            unique_company_names = list(dict.fromkeys(company_names))
            if len(unique_company_names) > 1:
                row["company_name"] = f"{unique_company_names[0]} 외 {len(unique_company_names) - 1}곳"
            elif len(unique_company_names) == 1:
                row["company_name"] = unique_company_names[0]

            for text_col in ["address", "customer_memo", "first_time"]:
                if text_col in part.columns:
                    values = [str(v).strip() for v in part[text_col].fillna("").tolist() if str(v).strip()]
                    if values:
                        row[text_col] = values[0]

            merged_count = safe_int(len(part))
            row["lightweight_merged_count"] = merged_count
            row["stop_count"] = 1
            if not str(row.get("address_norm", "")).strip():
                row["address_norm"] = row["_lightweight_stop_key"]
            route_rows.append(row)

        route_rows = sorted(
            route_rows,
            key=lambda r: (safe_int(r.get("_lightweight_house_order", r.get("house_order", 0))), safe_int(r.get("_lightweight_row_order", 0))),
        )
        route_total = len(route_rows)
        route_prefix = str(route_prefix_map.get(route, "") or "").strip()
        for stop_index, row in enumerate(route_rows, start=1):
            stop_label = f"{route_prefix}{stop_index}" if route_prefix else str(stop_index)
            row["house_order"] = stop_index
            row["route_total"] = route_total
            row["pin_label"] = stop_label
            row["lightweight_stop_label"] = stop_label
            merged_rows.append(row)

    if not merged_rows:
        return pd.DataFrame()

    return pd.DataFrame(merged_rows).drop(
        columns=[
            "_lightweight_row_order",
            "_lightweight_address_norm",
            "_lightweight_house_order",
            "_lightweight_stop_key",
        ],
        errors="ignore",
    )


def render_assignment_form(
    route_summary: pd.DataFrame,
    drivers,
    assignment_store: dict,
    uploaded_filename: str,
    base_date_str: str,
):
    st.subheader("기사 배정")
    st.caption("구분 / 캠프 / truck_request_id / 스톱 / 시작시간 / 종료시간 / 소형 / 중형 / 대형 / 총합 / 기사")

    driver_options = [""] + drivers

    with st.form("assignment_form", clear_on_submit=False):
        new_assignment_store = assignment_store.copy()

        for _, row in route_summary.sort_values(["route_prefix", "route", "truck_request_id"]).iterrows():
            route = row["route"]
            truck_request_id = row["truck_request_id"]
            assignment_key = make_assignment_key(route, truck_request_id)
            saved_driver = new_assignment_store.get(assignment_key, "")

            default_index = 0
            if saved_driver in driver_options:
                default_index = driver_options.index(saved_driver)

            c1, c2, c3, c4, c5, c6, c7, c8, c9, c10, c11 = st.columns(
                [0.6, 0.9, 1.2, 0.7, 0.9, 0.9, 0.7, 0.7, 0.7, 0.8, 1.4]
            )
            c1.write(str(row["route_prefix"]))
            c2.write(str(row.get("camp_name", "")))
            c3.write(str(truck_request_id))
            c4.write(safe_int(row["스톱수"]))
            c5.write(str(row["시작시간"]))
            c6.write(str(row["종료시간"]))
            c7.markdown(styled_qty_value_html(row["소형합"], "small"), unsafe_allow_html=True)
            c8.markdown(styled_qty_value_html(row["중형합"], "medium"), unsafe_allow_html=True)
            c9.markdown(styled_qty_value_html(row["대형합"], "large"), unsafe_allow_html=True)
            c10.markdown(styled_qty_value_html(row["총합"], "total"), unsafe_allow_html=True)

            selected_driver = c11.selectbox(
                f"기사선택_{route}_{truck_request_id}",
                options=driver_options,
                index=default_index,
                label_visibility="collapsed",
                key=f"driver_select_{route}_{truck_request_id}"
            )

            new_assignment_store[assignment_key] = selected_driver

        submitted = st.form_submit_button("기사 배정 적용")

    if submitted:
        st.session_state["last_assignment_changes"] = build_assignment_change_rows(
            route_summary,
            assignment_store,
            new_assignment_store,
        )
        st.session_state["assignment_store"] = new_assignment_store
        persist_result = persist_assignment_store(
            route_summary=route_summary,
            assignment_store=new_assignment_store,
            uploaded_filename=uploaded_filename,
            base_date=base_date_str,
        )
        if persist_result["ok"]:
            queue_assignment_feedback(
                "success",
                f"기사 배정을 저장했습니다. (로컬 반영 완료, 서버 저장 {persist_result['saved_count']}건)",
            )
        elif not persist_result.get("local_ok", True):
            queue_assignment_feedback(
                "error",
                f"기사 배정은 현재 화면에는 반영했지만 로컬 파일 저장에 실패했습니다. 새로고침하면 유지되지 않을 수 있습니다. {persist_result['message']}",
            )
        else:
            queue_assignment_feedback(
                "warning",
                f"기사 배정은 로컬 화면에 즉시 반영했고 서버 저장은 실패했습니다. {persist_result['message']}",
            )
        rerun_after_assignment_submit()
        return new_assignment_store

    return assignment_store


def resolve_group_count(route_feature_df: pd.DataFrame, manual_group_count=None) -> int:
    if len(route_feature_df) == 0:
        return 1

    k = safe_int(manual_group_count)
    if k <= 0:
        k = choose_auto_group_count(route_feature_df)

    return max(1, min(k, len(route_feature_df)))


def _first_nonempty_value(values):
    for value in values:
        text = str(value).strip()
        if text and text.lower() != "nan":
            return text
    return ""


def _first_valid_coords_value(values):
    for value in values:
        coords = _normalize_coords(value)
        if coords is not None:
            return coords
    return None


def build_pickup_map_grouped_df(
    grouped_delivery: pd.DataFrame,
    route_prefix_map: dict = None,
    route_camp_map: dict = None,
    route_driver_map: dict = None,
):
    if not isinstance(grouped_delivery, pd.DataFrame) or len(grouped_delivery) == 0:
        return pd.DataFrame()

    work = grouped_delivery.copy()
    route_prefix_map = route_prefix_map if isinstance(route_prefix_map, dict) else {}
    route_camp_map = route_camp_map if isinstance(route_camp_map, dict) else {}
    route_driver_map = route_driver_map if isinstance(route_driver_map, dict) else {}

    for col in [
        "route",
        "company_id",
        "company_name",
        "address",
        "address_norm",
        "truck_request_id",
        "route_prefix",
        "assigned_driver",
        "customer_memo",
        "first_time",
    ]:
        if col not in work.columns:
            work[col] = ""
        work[col] = work[col].fillna("").astype(str)

    for qty_col in ["ae_sum", "af_sum", "ag_sum"]:
        if qty_col not in work.columns:
            work[qty_col] = 0
        work[qty_col] = pd.to_numeric(work[qty_col], errors="coerce").fillna(0)

    if "stop_count" not in work.columns:
        work["stop_count"] = 1
    work["stop_count"] = pd.to_numeric(work["stop_count"], errors="coerce").fillna(1)

    if "coords" not in work.columns:
        work["coords"] = None
    work["coords"] = work["coords"].apply(_normalize_coords)

    work["_pickup_row_order"] = range(len(work))
    if "house_order" in work.columns:
        work["_pickup_sort_order"] = pd.to_numeric(work["house_order"], errors="coerce")
    else:
        work["_pickup_sort_order"] = pd.NA
    work["_pickup_sort_order"] = work["_pickup_sort_order"].fillna(work["_pickup_row_order"])
    work["_pickup_company_key"] = work.apply(
        lambda row: str(row.get("company_id", "")).strip() or str(row.get("company_name", "")).strip(),
        axis=1,
    )
    work["_pickup_address_key"] = work.apply(
        lambda row: str(row.get("address_norm", "")).strip() or normalize_address(row.get("address", "")),
        axis=1,
    )
    work["_pickup_company_key"] = work["_pickup_company_key"].replace("", "__unknown_company__")
    work["_pickup_address_key"] = work["_pickup_address_key"].replace("", "__unknown_address__")

    pickup = (
        work.groupby(["route", "_pickup_company_key", "_pickup_address_key"], as_index=False, sort=False)
        .agg(
            truck_request_id=("truck_request_id", _first_nonempty_value),
            company_id=("company_id", _first_nonempty_value),
            company_name=("company_name", _first_nonempty_value),
            address=("address", _first_nonempty_value),
            address_norm=("address_norm", _first_nonempty_value),
            coords=("coords", _first_valid_coords_value),
            assigned_driver=("assigned_driver", _first_nonempty_value),
            ae_sum=("ae_sum", "sum"),
            af_sum=("af_sum", "sum"),
            ag_sum=("ag_sum", "sum"),
            stop_count=("stop_count", "sum"),
            route_prefix=("route_prefix", _first_nonempty_value),
            customer_memo=("customer_memo", _first_nonempty_value),
            first_time=("first_time", _first_nonempty_value),
            _pickup_sort_order=("_pickup_sort_order", "min"),
            _pickup_row_order=("_pickup_row_order", "min"),
        )
        .reset_index(drop=True)
    )

    if len(pickup) == 0:
        return pickup

    if route_prefix_map:
        pickup["route_prefix"] = pickup["route"].map(route_prefix_map).fillna(pickup["route_prefix"]).astype(str)
    if route_driver_map:
        pickup["assigned_driver"] = pickup["route"].map(route_driver_map).fillna(pickup["assigned_driver"]).astype(str)

    pickup["camp_code"] = pickup["route"].map(route_camp_map).fillna("")
    pickup["camp_name"] = pickup["camp_code"].map(lambda code: CAMP_INFO.get(code, {}).get("camp_name", "")).fillna("")
    pickup["address_norm"] = pickup["address_norm"].where(
        pickup["address_norm"].astype(str).str.strip() != "",
        pickup["_pickup_address_key"],
    ).astype(str)
    pickup["box_total"] = pickup["ae_sum"] + pickup["af_sum"] + pickup["ag_sum"]
    pickup["total_qty"] = pickup["box_total"]
    pickup["pickup_map_grouped"] = True
    pickup["pickup_map_key"] = pickup.apply(
        lambda row: f"{row.get('route', '')}|{row.get('_pickup_company_key', '')}|{row.get('_pickup_address_key', '')}",
        axis=1,
    )

    pickup = pickup.sort_values(
        ["route", "_pickup_sort_order", "_pickup_row_order"],
        kind="stable",
    ).reset_index(drop=True)
    pickup["house_order"] = pickup.groupby("route").cumcount() + 1
    pickup["route_total"] = pickup.groupby("route")["route"].transform("count")
    pickup["pin_label"] = pickup.apply(
        lambda row: f"{row.get('route_prefix', '')}{safe_int(row.get('house_order', 0))}".strip()
        or str(safe_int(row.get("house_order", 0))),
        axis=1,
    )

    def _tooltip(row):
        time_text = clean_map_text(row.get("first_time", ""))
        company_name = shorten_company_name_for_tooltip(row.get("company_name", ""))
        total = safe_int(row.get("box_total", 0))
        small = safe_int(row.get("ae_sum", 0))
        medium = safe_int(row.get("af_sum", 0))
        large = safe_int(row.get("ag_sum", 0))
        prefix = f"{time_text} " if time_text else ""
        return f"{prefix}{company_name} {total}({small}/{medium}/{large})".strip()

    def _popup(row):
        memo = str(row.get("customer_memo", "") or "").strip()
        memo_html = f"<br><b>메모:</b> {html.escape(memo)}" if memo else ""
        return f"""
        <div style="min-width:280px;max-width:400px;line-height:1.5;">
            <b>루트:</b> {html.escape(str(row.get('route', '')))}<br>
            <b>기사:</b> {html.escape(str(row.get('assigned_driver', '') or '미배정'))}<br>
            <b>방문예정시간:</b> {html.escape(clean_map_text(row.get('first_time', '')) or '-')}<br>
            <b>업체명:</b> {html.escape(str(row.get('company_name', '') or '-'))}<br>
            <b>주소:</b> {html.escape(str(row.get('address', '') or '-'))}<br>
            <b>총박스:</b> {safe_int(row.get('box_total', 0))}<br>
            <b>소:</b> {safe_int(row.get('ae_sum', 0))}<br>
            <b>중:</b> {safe_int(row.get('af_sum', 0))}<br>
            <b>대:</b> {safe_int(row.get('ag_sum', 0))}<br>
            <b>총건수:</b> {safe_int(row.get('stop_count', 0))}<br>
            <b>캠프:</b> {html.escape(str(row.get('camp_name', '') or '-'))}
            {memo_html}
        </div>
        """

    pickup["pickup_tooltip"] = pickup.apply(_tooltip, axis=1)
    pickup["pickup_popup_html"] = pickup.apply(_popup, axis=1)
    pickup["hover_text"] = pickup["pickup_tooltip"]

    return pickup.drop(
        columns=["_pickup_company_key", "_pickup_address_key", "_pickup_sort_order", "_pickup_row_order"],
        errors="ignore",
    )


def build_map_data(
    result_delivery: pd.DataFrame,
    grouped_delivery: pd.DataFrame,
    assignment_df: pd.DataFrame,
    selected_filter: str,
    route_prefix_map: dict = None,
    route_camp_map: dict = None,
    pickup_grouped: bool = False,
):
    route_driver_map = {}
    if len(assignment_df) > 0:
        route_driver_map = dict(zip(assignment_df["route"], assignment_df["assigned_driver"]))

    result2 = result_delivery.copy()
    grouped2 = grouped_delivery.copy()

    result2["assigned_driver"] = result2["route"].map(route_driver_map)
    grouped2["assigned_driver"] = grouped2["route"].map(route_driver_map)

    if selected_filter == "전체":
        map_result = result2.copy()
        map_grouped = grouped2.copy()
    elif selected_filter == "미배정":
        map_result = result2[result2["assigned_driver"].fillna("").astype(str).str.strip() == ""].copy()
        map_grouped = grouped2[grouped2["assigned_driver"].fillna("").astype(str).str.strip() == ""].copy()
    else:
        map_result = result2[result2["assigned_driver"] == selected_filter].copy()
        map_grouped = grouped2[grouped2["assigned_driver"] == selected_filter].copy()

    if pickup_grouped:
        map_grouped = build_pickup_map_grouped_df(
            map_grouped,
            route_prefix_map=route_prefix_map,
            route_camp_map=route_camp_map,
            route_driver_map=route_driver_map,
        )

    manual_mappings = load_manual_location_mappings()
    map_result, _ = apply_manual_location_mappings(map_result, manual_mappings, collect_unmapped=False)
    map_grouped, unmapped_df = apply_manual_location_mappings(map_grouped, manual_mappings, collect_unmapped=True)

    map_result, _ = _sanitize_coords_column(map_result, log_label="build_map_data_result")
    map_grouped, _ = _sanitize_coords_column(map_grouped, log_label="build_map_data_grouped")

    valid_result = map_result[map_result["coords"].notna()].copy()
    valid_grouped = map_grouped[map_grouped["coords"].notna()].copy()

    return valid_result, valid_grouped, route_driver_map, unmapped_df


def build_dispatch_overlay_layers(
    valid_result: pd.DataFrame,
    valid_grouped: pd.DataFrame,
    route_prefix_map: dict,
    truck_request_map: dict,
    route_line_label: dict,
    route_driver_map: dict,
    route_camp_map: dict,
    camp_coords: dict,
    highlighted_driver: str = "",
    coords_already_sanitized: bool = False,
    lightweight: bool = False,
):
    if coords_already_sanitized:
        valid_result_safe = valid_result.copy() if isinstance(valid_result, pd.DataFrame) else pd.DataFrame()
        valid_grouped_safe = valid_grouped.copy() if isinstance(valid_grouped, pd.DataFrame) else pd.DataFrame()
        invalid_result_count = 0
        invalid_grouped_count = 0
    else:
        valid_result_safe, invalid_result_count = _sanitize_coords_column(valid_result, log_label="render_map_result")
        valid_grouped_safe, invalid_grouped_count = _sanitize_coords_column(valid_grouped, log_label="render_map_grouped")
        valid_result_safe = valid_result_safe[valid_result_safe["coords"].notna()].copy() if "coords" in valid_result_safe.columns else pd.DataFrame()
        valid_grouped_safe = valid_grouped_safe[valid_grouped_safe["coords"].notna()].copy() if "coords" in valid_grouped_safe.columns else pd.DataFrame()

    if invalid_result_count > 0 or invalid_grouped_count > 0:
        logger.debug(
            "[render_map] skipped invalid coords rows result=%s grouped=%s",
            invalid_result_count,
            invalid_grouped_count,
        )

    map_center = resolve_map_center(valid_result_safe, camp_coords)
    overlay_layers = []

    # 캠프 레이어 (항상 고정)
    camp_group = folium.FeatureGroup(name="캠프", show=True)

    for camp_code, info in CAMP_INFO.items():
        coords = _normalize_coords(camp_coords.get(camp_code))
        if not coords:
            continue

        lat, lon = coords
        icon_obj = make_camp_icon(camp_code)

        popup_html = f"""
        <div style="min-width:260px;max-width:340px;line-height:1.45;">
            <b>캠프:</b> {info['camp_name']}<br>
            <b>코드:</b> {camp_code}<br>
            <b>주소:</b> {info['address']}
        </div>
        """

        folium.Marker(
            [lat, lon],
            popup=folium.Popup(popup_html, max_width=380),
            tooltip=info["camp_name"],
            icon=icon_obj
        ).add_to(camp_group)

    overlay_layers.append(camp_group)

    # 라우트별 색상
    route_list = sorted(
        valid_result_safe["route"].dropna().unique().tolist(),
        key=lambda x: route_prefix_map.get(x, "")
    )

    route_color_map = {
        route: ROUTE_COLORS[i % len(ROUTE_COLORS)]
        for i, route in enumerate(route_list)
    }

    driver_list = [
        d for d in valid_result_safe["assigned_driver"].fillna("").unique().tolist()
        if str(d).strip() != ""
    ]
    driver_color_map = {
        driver: ROUTE_COLORS[(i + len(route_list)) % len(ROUTE_COLORS)]
        for i, driver in enumerate(driver_list)
    }

    lightweight_stop_rows = build_lightweight_stop_rows(valid_grouped_safe, route_prefix_map) if lightweight else pd.DataFrame()
    overlap_info_source = lightweight_stop_rows if lightweight else valid_grouped_safe
    overlap_info_map = build_overlap_info_map(overlap_info_source)

    if not lightweight:
        for key, part in valid_grouped_safe.assign(
            coord_key=valid_grouped_safe["coords"].apply(
                lambda c: f"{round(float(c[0]), 6)}_{round(float(c[1]), 6)}" if isinstance(c, (tuple, list)) and len(c) == 2 else ""
            )
        ).groupby("coord_key"):
            if key == "":
                continue
            route_names = sorted(part["route"].dropna().astype(str).unique().tolist())
            driver_names = sorted([d for d in part["assigned_driver"].fillna("").astype(str).unique().tolist() if d.strip() != ""])
            overlap_info_map[key]["routes"] = route_names
            overlap_info_map[key]["drivers"] = driver_names

    active_driver = str(highlighted_driver or "").strip()

    if lightweight:
        line_group = folium.FeatureGroup(name="routes", show=True)
        marker_group = folium.FeatureGroup(name="stops", show=True)

        for route in route_list:
            truck_request_id = truck_request_map.get(route, "")
            camp_code = route_camp_map.get(route, "")
            camp_name = CAMP_INFO.get(camp_code, {}).get("camp_name", "")
            route_driver = route_driver_map.get(route, "")
            is_assigned_route = str(route_driver).strip() != ""

            route_df_line = valid_result_safe[
                (valid_result_safe["route"] == route)
            ].sort_values("house_order")
            line_points = []
            route_df_line_house = route_df_line.drop_duplicates(subset=["address_norm"], keep="first")
            for _, row in route_df_line_house.iterrows():
                coords = _normalize_coords(row.get("coords"))
                if coords is None:
                    continue
                line_points.append([coords[0], coords[1]])

            if is_assigned_route:
                line_color = driver_color_map.get(route_driver, "#1e88e5")
                dash_value = "8, 6"
            else:
                line_color = route_color_map.get(route, "#1e88e5")
                dash_value = None

            if len(line_points) >= 2:
                folium.PolyLine(
                    line_points,
                    color=line_color,
                    weight=3,
                    opacity=0.8,
                    tooltip=str(route_prefix_map.get(route, "") or truck_request_id or route),
                    dash_array=dash_value,
                ).add_to(line_group)

            route_grouped = (
                lightweight_stop_rows[lightweight_stop_rows["route"] == route].copy()
                if "route" in lightweight_stop_rows.columns
                else pd.DataFrame()
            )
            for _, row in route_grouped.iterrows():
                coords = _normalize_coords(row.get("coords"))
                if coords is None:
                    continue
                lat, lon = coords
                coord_key = f"{round(float(lat), 6)}_{round(float(lon), 6)}"
                overlap_info = overlap_info_map.get(coord_key, {})
                lat, lon, overlap_count = spread_overlapping_marker(lat, lon, overlap_info, row)

                driver_name = str(row.get("assigned_driver", "")).strip()
                is_assigned_pin = driver_name != ""
                pin_color = driver_color_map.get(driver_name, "#1e88e5") if is_assigned_pin else route_color_map.get(route, "#1e88e5")
                stop_label = str(row.get("lightweight_stop_label", row.get("pin_label", ""))).strip()
                marker_label = f"{short_driver_name(driver_name)}{safe_int(row.get('house_order', 0))}" if is_assigned_pin else stop_label
                marker_label = marker_label or stop_label
                memo = str(row.get("customer_memo", "")).strip()
                memo_html = f"<br>메모: {memo}" if memo else ""
                bundle_count = safe_int(row.get("lightweight_merged_count", 1))
                bundle_html = f"<br>동일 route 동일 주소 {bundle_count}건 묶음" if bundle_count > 1 else ""
                overlap_html = f"<br>동일위치 {overlap_count}건" if overlap_count > 1 else ""
                popup_html = (
                    f"<b>{row.get('route', '')}</b> / {driver_name or '미배정'}<br>"
                    f"{row.get('company_name', '')}<br>"
                    f"{row.get('address', '')}<br>"
                    f"물량 {safe_int(row.get('ae_sum', 0))}/{safe_int(row.get('af_sum', 0))}/{safe_int(row.get('ag_sum', 0))}"
                    f"{memo_html}{bundle_html}{overlap_html}"
                )
                tooltip_text = (
                    f"{stop_label} / {row.get('first_time', '')} / "
                    f"{safe_int(row.get('ae_sum', 0))}/{safe_int(row.get('af_sum', 0))}/{safe_int(row.get('ag_sum', 0))}"
                )
                folium.Marker(
                    location=[lat, lon],
                    opacity=0.45 if (active_driver != "" and driver_name != active_driver) else 1.0,
                    popup=folium.Popup(popup_html, max_width=260),
                    tooltip=tooltip_text,
                    icon=make_lightweight_stop_label_icon(pin_color, marker_label, assigned=is_assigned_pin),
                ).add_to(marker_group)

        overlay_layers.append(line_group)
        overlay_layers.append(marker_group)
        return overlay_layers, map_center

    for route in route_list:
        truck_request_id = truck_request_map.get(route, "")
        camp_code = route_camp_map.get(route, "")
        camp_name = CAMP_INFO.get(camp_code, {}).get("camp_name", "")

        route_group = folium.FeatureGroup(
            name=f"{route_prefix_map.get(route, '')}",
            show=True
        )

        route_df_line = valid_result_safe[
            (valid_result_safe["route"] == route)
        ].sort_values("house_order")

        line_points = []
        route_df_line_house = route_df_line.drop_duplicates(subset=["address_norm"], keep="first")

        for _, row in route_df_line_house.iterrows():
            coords = _normalize_coords(row.get("coords"))
            if coords is None:
                continue
            lat, lon = coords
            line_points.append([lat, lon])

        route_driver = route_driver_map.get(route, "")
        is_assigned_route = str(route_driver).strip() != ""
        is_highlight_target = active_driver != "" and str(route_driver).strip() == active_driver
        is_dimmed_route = active_driver != "" and not is_highlight_target

        if is_assigned_route:
            line_color = driver_color_map.get(route_driver, "#1e88e5")
            under_weight = 5 if is_dimmed_route else 7
            main_weight = 3 if is_dimmed_route else 5
            dash_value = "10, 8"
        else:
            line_color = route_color_map.get(route, "#1e88e5")
            under_weight = 6 if is_dimmed_route else 10
            main_weight = 4 if is_dimmed_route else 7
            dash_value = None

        line_opacity_main = 0.2 if is_dimmed_route else 0.95
        line_opacity_under = 0.15 if is_dimmed_route else 0.55
        connector_opacity = 0.2 if is_dimmed_route else 0.7

        # 캠프 -> 마지막 배송지 연결선
        camp_coord = camp_coords.get(camp_code)
        camp_coord = _normalize_coords(camp_coord)
        if camp_coord and len(line_points) >= 1:
            folium.PolyLine(
                [[camp_coord[0], camp_coord[1]], line_points[-1]],
                color="#444444",
                weight=2,
                opacity=connector_opacity,
                dash_array="4, 6",
                tooltip=f"{route_prefix_map.get(route, '')} 도착센터: {camp_name}"
            ).add_to(route_group)

        # 배송 동선
        if len(line_points) >= 2:
            folium.PolyLine(
                line_points,
                color="#111111",
                weight=under_weight,
                opacity=line_opacity_under,
                tooltip=route_line_label.get(route, truck_request_id),
                dash_array=dash_value
            ).add_to(route_group)

            folium.PolyLine(
                line_points,
                color=line_color,
                weight=main_weight,
                opacity=line_opacity_main,
                tooltip=route_line_label.get(route, truck_request_id),
                dash_array=dash_value
            ).add_to(route_group)

        route_grouped = valid_grouped_safe[valid_grouped_safe["route"] == route].copy()

        for _, row in route_grouped.iterrows():
            coords = _normalize_coords(row.get("coords"))
            if coords is None:
                continue
            lat, lon = coords
            coord_key = f"{round(float(lat), 6)}_{round(float(lon), 6)}"
            overlap_info = overlap_info_map.get(coord_key, {})
            lat, lon, overlap_count = spread_overlapping_marker(lat, lon, overlap_info, row)

            driver_name = row.get("assigned_driver", "")
            is_assigned_pin = str(driver_name).strip() != ""

            if is_assigned_pin:
                pin_color = driver_color_map.get(driver_name, "#1e88e5")
            else:
                pin_color = route_color_map.get(route, "#1e88e5")

            overlap_label = ""
            overlap_detail = ""
            if overlap_count > 1:
                overlap_label = f"동일 위치 {overlap_count}건"
                overlap_routes = ", ".join(overlap_info.get("routes", []))
                overlap_drivers = ", ".join(overlap_info.get("drivers", []))
                overlap_detail = f"<b>중복배정:</b> 동일 위치 {overlap_count}건<br><b>겹치는 route:</b> {overlap_routes}<br><b>겹치는 기사:</b> {overlap_drivers}<br>"

            popup_html = f"""
            <div style="min-width:300px;max-width:420px;line-height:1.45;">
                <b>루트:</b> {row['route']}<br>
                <b>구분:</b> {row.get('route_prefix', '')}<br>
                <b>캠프:</b> {camp_name}<br>
                <b>핀번호:</b> {row.get('pin_label', '')}<br>
                <b>트럭요청ID:</b> {row.get('truck_request_id', '')}<br>
                <b>기사:</b> {row.get('assigned_driver', '')}<br>
                <b>업체ID:</b> {row['company_id']}<br>
                <b>업체명:</b> {row['company_name']}<br>
                <b>주소:</b> {row['address']}<br>
                <b>업체메모:</b> {str(row.get('customer_memo', '')).strip() or '-'}<br>
                <b>집순서:</b> {safe_int(row['house_order'])}/{safe_int(row['route_total'])}<br>
                <b>건수:</b> {safe_int(row['stop_count'])}<br>
                <b>물량:</b> {safe_int(row['ae_sum'])}.{safe_int(row['af_sum'])}.{safe_int(row['ag_sum'])}<br>
                {overlap_detail}
            </div>
            """
            pickup_popup_html = str(row.get("pickup_popup_html", "") or "").strip()
            if pickup_popup_html:
                popup_html = pickup_popup_html

            house_order = safe_int(row.get("house_order", 0))
            route_total = safe_int(row.get("route_total", 0))
            is_start = route_total > 0 and house_order == 1
            is_end = route_total > 0 and house_order == route_total
            size_scale = PIN_EDGE_SCALE if is_start else PIN_NORMAL_SCALE
            border_color = PIN_EDGE_BORDER_COLOR if (is_start or is_end) else PIN_NORMAL_BORDER_COLOR

            if is_assigned_pin:
                pin_text = short_driver_name(driver_name)
                icon_obj = make_assigned_square_icon(pin_color, pin_text, size_scale=size_scale, border_color=border_color)
            else:
                pin_text = str(row.get("pin_label", ""))
                icon_obj = make_stop_div_icon(pin_color, pin_text, size_scale=size_scale, border_color=border_color)

            tooltip_text = str(row.get("pickup_tooltip", "") or "").strip() or row["hover_text"]
            if overlap_label:
                tooltip_text = f"{tooltip_text} / {overlap_label}"

            folium.Marker(
                [lat, lon],
                popup=folium.Popup(popup_html, max_width=440),
                tooltip=tooltip_text,
                opacity=0.35 if (active_driver != "" and str(driver_name).strip() != active_driver) else 1.0,
                icon=icon_obj
            ).add_to(route_group)

        overlay_layers.append(route_group)

    return overlay_layers, map_center


def render_map(
    valid_result: pd.DataFrame,
    valid_grouped: pd.DataFrame,
    route_prefix_map: dict,
    truck_request_map: dict,
    route_line_label: dict,
    route_driver_map: dict,
    route_camp_map: dict,
    camp_coords: dict,
    highlighted_driver: str = "",
):
    overlay_layers, map_center = build_dispatch_overlay_layers(
        valid_result=valid_result,
        valid_grouped=valid_grouped,
        route_prefix_map=route_prefix_map,
        truck_request_map=truck_request_map,
        route_line_label=route_line_label,
        route_driver_map=route_driver_map,
        route_camp_map=route_camp_map,
        camp_coords=camp_coords,
        highlighted_driver=highlighted_driver,
    )
    m = build_base_map(center=map_center, zoom=DEFAULT_MAP_ZOOM)
    for layer in overlay_layers:
        layer.add_to(m)
    return m


def build_group_overlay_layers(
    valid_result: pd.DataFrame,
    valid_grouped: pd.DataFrame,
    route_prefix_map: dict,
    route_camp_map: dict,
    camp_coords: dict,
    selected_group: str = "전체",
):
    map_center = resolve_map_center(valid_result, camp_coords)
    overlay_layers = []

    camp_group = folium.FeatureGroup(name="캠프", show=True)
    for camp_code, info in CAMP_INFO.items():
        coords = camp_coords.get(camp_code)
        if not coords:
            continue

        folium.Marker(
            [coords[0], coords[1]],
            popup=folium.Popup(
                f"""
                <div style="min-width:260px;max-width:340px;line-height:1.45;">
                    <b>캠프:</b> {info['camp_name']}<br>
                    <b>코드:</b> {camp_code}<br>
                    <b>주소:</b> {info['address']}
                </div>
                """,
                max_width=380,
            ),
            tooltip=info["camp_name"],
            icon=make_camp_icon(camp_code)
        ).add_to(camp_group)
    overlay_layers.append(camp_group)

    route_list = sorted(
        valid_result["route"].dropna().unique().tolist(),
        key=lambda x: route_prefix_map.get(x, "")
    )

    if selected_group != "전체":
        selected_routes = valid_result[valid_result["추천그룹"] == selected_group]["route"].dropna().unique().tolist()
        route_list = [r for r in route_list if r in selected_routes]

    group_list = sorted([g for g in valid_result["추천그룹"].dropna().unique().tolist() if str(g).strip() != ""])
    group_color_map = {group_name: ROUTE_COLORS[i % len(ROUTE_COLORS)] for i, group_name in enumerate(group_list)}
    overlap_info_map = build_overlap_info_map(valid_grouped)

    for route in route_list:
        route_df_line = valid_result[valid_result["route"] == route].sort_values("house_order")
        if len(route_df_line) == 0:
            continue

        route_grouped = valid_grouped[valid_grouped["route"] == route].copy()
        group_name = str(route_df_line.iloc[0].get("추천그룹", "")).strip()
        route_color = group_color_map.get(group_name, "#1e88e5")
        camp_code = route_camp_map.get(route, "")
        camp_name = CAMP_INFO.get(camp_code, {}).get("camp_name", "")

        small_sum = safe_int(route_grouped["ae_sum"].sum()) if "ae_sum" in route_grouped.columns else 0
        medium_sum = safe_int(route_grouped["af_sum"].sum()) if "af_sum" in route_grouped.columns else 0
        large_sum = safe_int(route_grouped["ag_sum"].sum()) if "ag_sum" in route_grouped.columns else 0
        group_label = str(group_name).replace("추천그룹 ", "추천그룹")
        route_tooltip = f"{group_label} / {small_sum + medium_sum + large_sum}개({small_sum}/{medium_sum}/{large_sum})"

        route_group = folium.FeatureGroup(name=f"{route_prefix_map.get(route, '')}", show=True)

        line_points = []
        route_df_line_house = route_df_line.drop_duplicates(subset=["address_norm"], keep="first")
        for _, row in route_df_line_house.iterrows():
            lat, lon = row["coords"]
            line_points.append([lat, lon])

        camp_coord = camp_coords.get(camp_code)
        if camp_coord and len(line_points) >= 1:
            folium.PolyLine(
                [[camp_coord[0], camp_coord[1]], line_points[-1]],
                color="#444444",
                weight=2,
                opacity=0.7,
                dash_array="4, 6",
                tooltip=route_tooltip
            ).add_to(route_group)

        if len(line_points) >= 2:
            folium.PolyLine(line_points, color="#111111", weight=8, opacity=0.55, tooltip=route_tooltip).add_to(route_group)
            folium.PolyLine(line_points, color=route_color, weight=5, opacity=0.95, tooltip=route_tooltip).add_to(route_group)

        for _, row in route_grouped.iterrows():
            lat, lon = row["coords"]
            coord_key = f"{round(float(lat), 6)}_{round(float(lon), 6)}"
            overlap_info = overlap_info_map.get(coord_key, {})
            lat, lon, overlap_count = spread_overlapping_marker(lat, lon, overlap_info, row)
            overlap_label = f" / 동일 위치 {overlap_count}건" if overlap_count > 1 else ""
            popup_html = f"""
            <div style="min-width:280px;max-width:380px;line-height:1.45;">
                <b>추천그룹:</b> {str(row.get('추천그룹', '')).replace('추천그룹 ', '추천그룹')}<br>
                <b>주소:</b> {row.get('address', '')}<br>
                <b>업체명:</b> {row.get('company_name', '')}<br>
                <b>물량(소/중/대):</b> {safe_int(row.get('ae_sum', 0))}/{safe_int(row.get('af_sum', 0))}/{safe_int(row.get('ag_sum', 0))}
            </div>
            """

            house_order = safe_int(row.get("house_order", 0))
            route_total = safe_int(row.get("route_total", 0))
            is_start = route_total > 0 and house_order == 1
            is_end = route_total > 0 and house_order == route_total
            size_scale = PIN_EDGE_SCALE if is_start else PIN_NORMAL_SCALE
            border_color = PIN_EDGE_BORDER_COLOR if (is_start or is_end) else PIN_NORMAL_BORDER_COLOR

            company_name = str(row.get("company_name", "")).strip() or "업체명없음"
            item_small = safe_int(row.get("ae_sum", 0))
            item_medium = safe_int(row.get("af_sum", 0))
            item_large = safe_int(row.get("ag_sum", 0))
            total_items = item_small + item_medium + item_large
            marker_tooltip = f"{company_name} / 총수량 {total_items}개({item_small}/{item_medium}/{item_large}){overlap_label}"

            folium.Marker(
                [lat, lon],
                popup=folium.Popup(popup_html, max_width=400),
                tooltip=marker_tooltip,
                icon=make_stop_div_icon(route_color, str(row.get("pin_label", "")), size_scale=size_scale, border_color=border_color)
            ).add_to(route_group)

        overlay_layers.append(route_group)

    return overlay_layers, map_center


def render_group_map(
    valid_result: pd.DataFrame,
    valid_grouped: pd.DataFrame,
    route_prefix_map: dict,
    route_camp_map: dict,
    camp_coords: dict,
    selected_group: str = "전체",
):
    overlay_layers, map_center = build_group_overlay_layers(
        valid_result=valid_result,
        valid_grouped=valid_grouped,
        route_prefix_map=route_prefix_map,
        route_camp_map=route_camp_map,
        camp_coords=camp_coords,
        selected_group=selected_group,
    )
    m = build_base_map(center=map_center, zoom=DEFAULT_MAP_ZOOM, use_spiderfier=True)
    for layer in overlay_layers:
        layer.add_to(m)
    return m


def build_static_map_cache_key(active_dataset_key: str, selected_filter: str, assignment_df: pd.DataFrame) -> str:
    assignment_rows = []
    if isinstance(assignment_df, pd.DataFrame) and len(assignment_df) > 0:
        cols = [c for c in ["route", "truck_request_id", "assigned_driver"] if c in assignment_df.columns]
        if cols:
            assignment_rows = (
                assignment_df[cols]
                .fillna("")
                .astype(str)
                .sort_values(cols)
                .to_dict(orient="records")
            )
    payload = json.dumps(
        {
            "cache_version": "static_pickup_v1",
            "dataset_key": str(active_dataset_key or ""),
            "selected_filter": str(selected_filter or ""),
            "manual_location_mapping": manual_location_mapping_fingerprint(),
            "assignment_rows": assignment_rows,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def get_cached_static_map_html(cache_key: str):
    if st.session_state.get("static_map_html_cache_key") == cache_key:
        return st.session_state.get("static_map_html", "")
    return ""


def get_static_map_html(
    cache_key: str,
    html_filename: str,
    valid_result: pd.DataFrame,
    valid_grouped: pd.DataFrame,
    route_prefix_map: dict,
    truck_request_map: dict,
    route_line_label: dict,
    route_driver_map: dict,
    route_camp_map: dict,
    camp_coords: dict,
):
    cached_html = get_cached_static_map_html(cache_key)
    if cached_html:
        return cached_html

    static_map = render_map(
        valid_result=valid_result,
        valid_grouped=valid_grouped,
        route_prefix_map=route_prefix_map,
        truck_request_map=truck_request_map,
        route_line_label=route_line_label,
        route_driver_map=route_driver_map,
        route_camp_map=route_camp_map,
        camp_coords=camp_coords,
    )
    map_html = static_map.get_root().render()
    map_path = os.path.join(MAP_DIR, html_filename)
    with open(map_path, "w", encoding="utf-8") as f:
        f.write(map_html)

    st.session_state["static_map_html_cache_key"] = cache_key
    st.session_state["static_map_html"] = map_html
    st.session_state["static_map_html_filename"] = html_filename
    return map_html


def milkrun_project_dir() -> Path:
    return BASE_DIR.parent / "Nasil-sale-main"


def milkrun_python_executable(project_dir: Path) -> Path:
    venv_python = project_dir / ".venv" / "Scripts" / "python.exe"
    return venv_python if venv_python.exists() else Path("python")


def _extract_last_json_object(output_text: str) -> dict:
    for line in reversed(str(output_text or "").splitlines()):
        line = line.strip()
        if not (line.startswith("{") and line.endswith("}")):
            continue
        try:
            payload = json.loads(line)
            return payload if isinstance(payload, dict) else {}
        except Exception:
            continue
    return {}


def load_latest_milkrun_attempt_summary(project_dir: Path, python_exe: Path) -> dict:
    shell_code = (
        "import json; "
        "from milkrun.services.collection import latest_collection_attempt; "
        "a=latest_collection_attempt(); "
        "meta=(a.attempt_meta or {}) if a else {}; "
        "sync=meta.get('dispatch_sync') or {}; "
        "scrape=meta.get('scrape_save') or {}; "
        "payload={"
        "'status': getattr(a, 'status', '') if a else '', "
        "'status_display': a.get_status_display() if a else '', "
        "'target_date': a.target_date.isoformat() if a and a.target_date else '', "
        "'message': (a.message or '') if a else '', "
        "'item_count': int(scrape.get('item_count') or 0), "
        "'created': bool(scrape.get('created', False)), "
        "'route_count': int(sync.get('route_count') or 0), "
        "'stop_count': int(sync.get('stop_count') or 0), "
        "'source_date': sync.get('source_date') or '', "
        "'run_id': int(sync.get('run_id') or 0), "
        "'error_stage': meta.get('error_stage') or '', "
        "}; "
        "print(json.dumps(payload, ensure_ascii=False))"
    )
    result = subprocess.run(
        [str(python_exe), "manage.py", "shell", "-c", shell_code],
        cwd=str(project_dir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    if result.returncode != 0:
        logger.warning("Failed to inspect latest milkrun attempt: %s", result.stderr or result.stdout)
        return {}
    return _extract_last_json_object(result.stdout)


def run_manual_milkrun_collection(target_date: str | None = None) -> dict:
    project_dir = milkrun_project_dir()
    if not project_dir.exists():
        return {
            "ok": False,
            "stage": "prepare",
            "error": f"Milkrun project not found: {project_dir}",
        }

    python_exe = milkrun_python_executable(project_dir)
    command = [str(python_exe), "manage.py", "collect_milkrun", "--manual"]
    target_date_text = str(target_date or "").strip()
    if target_date_text:
        command.extend(["--target-date", target_date_text])
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    started_at = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        result = subprocess.run(
            command,
            cwd=str(project_dir),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=900,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "stage": "collect",
            "started_at": started_at,
            "finished_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
            "error": f"milkrun collection timed out: {exc}",
        }
    except Exception as exc:
        return {
            "ok": False,
            "stage": "collect",
            "started_at": started_at,
            "finished_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
            "error": str(exc),
        }

    latest_attempt = load_latest_milkrun_attempt_summary(project_dir, python_exe)
    ok = result.returncode == 0
    return {
        "ok": ok,
        "stage": "completed" if ok else "collect",
        "started_at": started_at,
        "finished_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "returncode": result.returncode,
        "stdout": (result.stdout or "").strip()[-2000:],
        "stderr": (result.stderr or "").strip()[-2000:],
        "error": "" if ok else ((result.stderr or result.stdout or "milkrun collection failed").strip()[-1000:]),
        "attempt": latest_attempt,
        "requested_target_date": target_date_text,
    }


def render_manual_milkrun_result(result: dict):
    if not isinstance(result, dict) or not result:
        return

    attempt = result.get("attempt") if isinstance(result.get("attempt"), dict) else {}
    target_date = attempt.get("source_date") or attempt.get("target_date") or ""
    item_count = safe_int(attempt.get("item_count", 0))
    route_count = safe_int(attempt.get("route_count", 0))
    stop_count = safe_int(attempt.get("stop_count", 0))
    status_display = clean_map_text(attempt.get("status_display", ""))
    message = clean_map_text(attempt.get("message", ""))
    status = clean_map_text(attempt.get("status", ""))

    if result.get("ok"):
        summary_text = (
            f"기준일 {target_date or '-'} / "
            f"가져온 {item_count}건 / 저장 route {route_count}건 / stop {stop_count}건"
        )
        if status == "skipped":
            st.warning(f"밀크런 가져오기 건너뜀: {summary_text}")
        elif status == "no_data":
            st.warning(f"밀크런 데이터 없음: {summary_text}")
        else:
            st.success(f"밀크런 가져오기 완료: {summary_text}")
        if status_display or message:
            st.caption(f"{status_display} {message}".strip())
    else:
        stage = clean_map_text(result.get("stage", ""))
        error = clean_map_text(result.get("error", ""))
        st.error(f"밀크런 가져오기 실패: 단계 {stage or '-'} / 오류 {error or '-'}")
        if result.get("stderr"):
            with st.expander("실패 로그", expanded=False):
                st.code(result.get("stderr", ""), language="text")


# =========================
# 공유 링크로 직접 열기
# =========================
query_params = st.query_params
shared_map = query_params.get("map")

if shared_map:
    payload = load_share_payload(shared_map)

    if payload and payload.get("map_html"):
        result_rows = payload.get("result_delivery_rows", [])
        grouped_rows = payload.get("pickup_grouped_delivery_rows", []) or payload.get("grouped_delivery_rows", [])

        if result_rows and grouped_rows:
            shared_result_df = _records_to_df_with_coords(result_rows)
            shared_grouped_df = _records_to_df_with_coords(grouped_rows)
            shared_result_df, _ = _sanitize_coords_column(shared_result_df, log_label="shared_payload_result")
            shared_grouped_df, _ = _sanitize_coords_column(shared_grouped_df, log_label="shared_payload_grouped")

            route_prefix_map_payload = payload.get("route_prefix_map", {})
            truck_request_map_payload = payload.get("truck_request_map", {})
            route_line_label_payload = payload.get("route_line_label", {})
            route_camp_map_payload = payload.get("route_camp_map", {})
            camp_coords_payload = payload.get("camp_coords", {})
            if isinstance(camp_coords_payload, dict):
                camp_coords_payload = {
                    k: _normalize_coords(v)
                    for k, v in camp_coords_payload.items()
                }

            st.subheader("공유 지도")

            filtered_result = shared_result_df.copy()
            filtered_grouped = shared_grouped_df.copy()
            driver_overview_df = _build_driver_overview_df(shared_result_df, shared_grouped_df)
            camp_driver_summary_df = _build_camp_driver_summary_df(
                shared_result_df,
                shared_grouped_df,
                route_camp_map_payload,
            )

            route_list_shared = sorted(
                shared_result_df["route"].dropna().unique().tolist(),
                key=lambda x: route_prefix_map_payload.get(x, "")
            ) if "route" in shared_result_df.columns else []
            driver_list_shared = [
                d for d in shared_result_df.get("assigned_driver", pd.Series(dtype=str)).fillna("").unique().tolist()
                if str(d).strip() != ""
            ]
            driver_color_map_shared = {
                driver: ROUTE_COLORS[(i + len(route_list_shared)) % len(ROUTE_COLORS)]
                for i, driver in enumerate(driver_list_shared)
            }

            st.markdown("### 기사 선택")
            driver_options = ["전체 기사"] + driver_overview_df["assigned_driver"].tolist()
            selected_driver = st.radio(
                "기사 라우트 보기",
                driver_options,
                label_visibility="collapsed",
            )
            highlighted_driver = "" if selected_driver == "전체 기사" else selected_driver

            route_driver_map = {}
            if len(filtered_result) > 0 and "route" in filtered_result.columns and "assigned_driver" in filtered_result.columns:
                route_driver_map = dict(zip(filtered_result["route"], filtered_result["assigned_driver"]))

            with st.spinner("공유 지도 레이어 준비 중..."):
                shared_overlay_layers, shared_default_center = build_dispatch_overlay_layers(
                    valid_result=filtered_result,
                    valid_grouped=filtered_grouped,
                    route_prefix_map=route_prefix_map_payload,
                    truck_request_map=truck_request_map_payload,
                    route_line_label=route_line_label_payload,
                    route_driver_map=route_driver_map,
                    route_camp_map=route_camp_map_payload,
                    camp_coords=camp_coords_payload,
                    highlighted_driver=highlighted_driver,
                )
            ensure_map_view_state(
                dataset_key=f"shared:{shared_map}",
                center_key="shared_map_center",
                zoom_key="shared_map_zoom",
                dataset_state_key="shared_map_dataset_key",
                default_center=shared_default_center,
            )
            render_stable_folium_map(
                overlay_layers=shared_overlay_layers,
                key="dispatch_shared_map",
                center_key="shared_map_center",
                zoom_key="shared_map_zoom",
                height=760,
                fallback_flag_key="shared_map_dynamic_fallback",
            )

            if len(driver_overview_df) == 0:
                st.info("배정된 기사가 없습니다.")
            else:
                st.markdown("### 기사 배정 현황")
                for _, drow in driver_overview_df.iterrows():
                    driver_name = str(drow["assigned_driver"])
                    is_selected = highlighted_driver != "" and driver_name == highlighted_driver
                    line_prefix = "➡️ " if is_selected else ""
                    color_chip = driver_color_map_shared.get(driver_name, "#1e88e5")
                    st.markdown(
                        f"<span style='display:inline-block;width:8px;height:8px;border-radius:50%;background:{color_chip};margin-right:6px;'></span>"
                        f"**{line_prefix}{driver_name}**",
                        unsafe_allow_html=True,
                    )
                    st.caption(f"소 {safe_int(drow['소형합'])} / 중 {safe_int(drow['중형합'])} / 대 {safe_int(drow['대형합'])}")
                    st.caption(f"총합 {safe_int(drow['총박스'])}")

            st.markdown("### 캠프별 기사 요약")
            if len(camp_driver_summary_df) == 0:
                st.caption("표시할 캠프 배정 정보가 없습니다.")
            else:
                for camp_name, part in camp_driver_summary_df.groupby("camp_name", sort=False):
                    st.markdown(f"**{camp_name}**")
                    for _, crow in part.iterrows():
                        st.markdown(
                            f"- {crow['assigned_driver']} {safe_int(crow['회전수'])}회전 {safe_int(crow['총박스'])}개"
                        )

            st.markdown("### 전체 요약")
            total_drivers = safe_int(driver_overview_df["assigned_driver"].nunique()) if len(driver_overview_df) > 0 else 0
            total_boxes = safe_int(driver_overview_df["총박스"].sum()) if len(driver_overview_df) > 0 else 0
            st.caption(f"기사 {total_drivers}명")
            st.caption(f"총 박스 {total_boxes}개")

            st.subheader("기사 할당표")
            assignment_rows = payload.get("assignment_rows", [])
            if assignment_rows:
                assignment_df_payload = pd.DataFrame(assignment_rows)
                if highlighted_driver and "assigned_driver" in assignment_df_payload.columns:
                    assignment_df_payload = assignment_df_payload[assignment_df_payload["assigned_driver"].fillna("").astype(str) == highlighted_driver].copy()
                preferred_cols = [c for c in ["route_prefix", "route", "camp_name", "assigned_driver", "총합"] if c in assignment_df_payload.columns]
                if preferred_cols:
                    st.dataframe(assignment_df_payload[preferred_cols], use_container_width=True)
                else:
                    st.dataframe(assignment_df_payload, use_container_width=True)

        else:
            st.subheader("공유 지도")
            components.html(payload["map_html"], height=950, scrolling=True)

            assigned_summary_rows = payload.get("assigned_summary_rows", [])
            if assigned_summary_rows:
                st.subheader("기사별 요약")
                shared_summary_df = pd.DataFrame(assigned_summary_rows)
                if "총걸린분" in shared_summary_df.columns:
                    shared_summary_df = shared_summary_df.drop(columns=["총걸린분"])
                st.dataframe(shared_summary_df, use_container_width=True)
    else:
        st.error("저장된 공유 데이터를 찾을 수 없습니다. 서버 재시작 등으로 파일이 사라졌을 수 있습니다.")

    st.stop()

# =========================
# 시작
# =========================
driver_records_df, driver_source = load_driver_records_prefer_backend()
drivers = _driver_names_from_df(driver_records_df)
latest_run_summary, latest_run_error = cached_load_latest_assignment_run_summary()
today_run_options, today_run_error = [], None
recent_run_options, recent_run_error = [], None

with st.sidebar:
    st.subheader("백엔드 상태")
    with st.expander("파일 업로드 / 관리", expanded=False):
        uploaded_file = st.file_uploader("엑셀 파일 업로드", type=["xlsx"], key="dispatch_upload_file")
        if uploaded_file is not None:
            st.caption(f"업로드 파일: {uploaded_file.name}")

    st.caption(f"Django API: {DJANGO_API_BASE_URL}")
    if driver_source == "django":
        st.success(f"기사 목록 연동됨 ({len(drivers)}명)")
    else:
        st.warning("Django 기사 목록 연결 실패, 로컬 drivers.csv 사용 중")

    with st.expander("기사 목록 관리", expanded=False):
        st.caption("기사 정보의 기준값은 Django Driver입니다. drivers.csv는 import/export 및 fallback 용도로만 사용합니다.")
        if len(driver_records_df) > 0:
            driver_export_df = driver_records_df.copy()
            st.download_button(
                "현재 기사목록 CSV 다운로드",
                data=driver_export_df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"),
                file_name="drivers_export.csv",
                mime="text/csv",
                key="driver_records_export_download",
            )
        else:
            st.caption("내보낼 기사 목록이 없습니다.")

        local_drivers_csv_path = BASE_DIR / DRIVER_FILE
        if local_drivers_csv_path.exists():
            st.caption(f"로컬 import 원본: {local_drivers_csv_path}")
            if st.button("로컬 drivers.csv를 Django에 반영", key="sync_local_drivers_to_backend_button"):
                local_driver_records_df = load_driver_records_local()
                backend_sync_payload, backend_sync_error = sync_driver_records_to_backend(local_driver_records_df)
                if backend_sync_error:
                    st.error(f"기사 목록 동기화 실패: {backend_sync_error}")
                else:
                    driver_records_df, driver_source = load_driver_records_prefer_backend()
                    drivers = _driver_names_from_df(driver_records_df)
                    created_count = safe_int((backend_sync_payload or {}).get("created_count", 0))
                    updated_count = safe_int((backend_sync_payload or {}).get("updated_count", 0))
                    synced_count = safe_int((backend_sync_payload or {}).get("synced_count", created_count + updated_count))
                    st.success(
                        f"기사 목록 동기화 완료 ({synced_count}건, 생성 {created_count}건 / 갱신 {updated_count}건)"
                    )
        else:
            st.caption(f"로컬 import 원본 없음: {local_drivers_csv_path}")

    if latest_run_summary:
        st.caption(
            f"최근 작업: {latest_run_summary.get('source_date', '')} / "
            f"{latest_run_summary.get('name', '')}"
        )
    elif latest_run_error:
        st.caption(f"최근 작업 조회 실패: {latest_run_error}")

    with st.expander("밀크런 수동 수집", expanded=False):
        st.caption("기존 Django 밀크런 수집/dispatch 동기화 로직을 수동 실행합니다.")
        last_manual_milkrun_result = st.session_state.get("manual_milkrun_result", {})
        if isinstance(last_manual_milkrun_result, dict) and last_manual_milkrun_result.get("finished_at"):
            st.caption(f"최근 실행: {last_manual_milkrun_result.get('finished_at')}")
        default_milkrun_collect_date = pd.Timestamp.now().date()
        if latest_run_summary and latest_run_summary.get("source_date"):
            try:
                default_milkrun_collect_date = pd.Timestamp(latest_run_summary.get("source_date")).date()
            except Exception:
                default_milkrun_collect_date = pd.Timestamp.now().date()
        manual_milkrun_target_date = st.date_input(
            "수집 날짜",
            value=default_milkrun_collect_date,
            key="manual_milkrun_target_date",
        )
        if st.button("밀크런 가져오기", key="manual_milkrun_collect_button"):
            manual_milkrun_target_date_text = pd.Timestamp(manual_milkrun_target_date).strftime("%Y-%m-%d")
            st.session_state["manual_milkrun_result"] = {
                "ok": False,
                "stage": "running",
                "started_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
                "message": "수집 중",
                "requested_target_date": manual_milkrun_target_date_text,
            }
            with st.spinner(f"{manual_milkrun_target_date_text} 밀크런 수집 중..."):
                manual_milkrun_result = run_manual_milkrun_collection(manual_milkrun_target_date_text)
            st.session_state["manual_milkrun_result"] = manual_milkrun_result
            cached_load_latest_assignment_run_summary.clear()
            cached_load_assignment_runs.clear()
            cached_load_assignment_run_detail.clear()
            render_manual_milkrun_result(manual_milkrun_result)
        elif isinstance(last_manual_milkrun_result, dict) and last_manual_milkrun_result:
            render_manual_milkrun_result(last_manual_milkrun_result)

if "assignment_store" not in st.session_state:
    st.session_state["assignment_store"] = load_assignment_store()
    st.session_state["assignment_store_source"] = "initial_load"
elif "assignment_store_source" not in st.session_state:
    st.session_state["assignment_store_source"] = "session_existing"
if "cancel_store" not in st.session_state:
    st.session_state["cancel_store"] = load_cancel_store()
st.session_state.pop("assignment_pending_rerun", None)
assignment_feedback = st.session_state.pop("assignment_save_feedback", None)
if isinstance(assignment_feedback, dict) and assignment_feedback.get("message"):
    feedback_level = assignment_feedback.get("level", "info")
    if feedback_level == "success":
        st.success(assignment_feedback["message"])
    elif feedback_level == "warning":
        st.warning(assignment_feedback["message"])
    elif feedback_level == "error":
        st.error(assignment_feedback["message"])
    else:
        st.info(assignment_feedback["message"])

restored_mode = False
restored_selected_filter = "전체"
restored_selected_group_filter = "전체"

if not uploaded_file:
    today_run_options, today_run_error = cached_load_assignment_runs(pd.Timestamp.now().strftime("%Y-%m-%d"), limit=10)
    recent_run_options, recent_run_error = cached_load_assignment_runs(limit=10)
    st.subheader("저장된 지도 열기")

    latest_run_date = _coerce_run_date(latest_run_summary.get("source_date")) if isinstance(latest_run_summary, dict) else None
    default_browse_date = latest_run_date or pd.Timestamp.now().date()
    browse_col1, browse_col2 = st.columns([1.1, 1.9])
    with browse_col1:
        browse_date = st.date_input(
            "조회 날짜",
            value=default_browse_date,
            key="saved_run_browse_date",
            help="원하는 날짜를 고르면 그날 저장된 배차지도를 바로 불러올 수 있습니다.",
        )

    selected_date_str = pd.Timestamp(browse_date).strftime("%Y-%m-%d")
    selected_date_runs, selected_date_error = cached_load_assignment_runs(selected_date_str, limit=30)
    chosen_run_summary = None
    latest_run_id = safe_int(latest_run_summary.get("id")) if isinstance(latest_run_summary, dict) else 0

    with browse_col2:
        if selected_date_runs:
            run_labels = [_format_run_option_label(row) for row in selected_date_runs]
            default_run_index = 0
            for idx, row in enumerate(selected_date_runs):
                if safe_int(row.get("id")) == latest_run_id:
                    default_run_index = idx
                    break
            selected_run_label = st.selectbox(
                "저장된 작업",
                run_labels,
                index=default_run_index,
                key="saved_run_selector",
                help="같은 날짜에 여러 번 저장된 작업이 있으면 여기서 선택합니다.",
            )
            chosen_run_summary = selected_date_runs[run_labels.index(selected_run_label)]
        elif selected_date_error:
            st.caption(f"저장된 작업 조회 실패: {selected_date_error}")
        else:
            st.caption(f"{selected_date_str} 날짜에 저장된 작업이 아직 없습니다.")

    with st.expander("최근 작업 빠르게 보기", expanded=False):
        quick_candidates = []
        seen_run_ids = set()
        for row in (today_run_options or []) + (recent_run_options or []):
            run_id = safe_int(row.get("id", 0))
            if run_id > 0 and run_id not in seen_run_ids:
                seen_run_ids.add(run_id)
                quick_candidates.append(row)
        if quick_candidates:
            quick_df = pd.DataFrame(
                [
                    {
                        "기준일": row.get("source_date", ""),
                        "작업명": row.get("name", ""),
                        "파일명": row.get("source_filename", ""),
                        "route수": safe_int(row.get("route_count", 0)),
                    }
                    for row in quick_candidates[:20]
                ]
            )
            st.dataframe(quick_df, use_container_width=True)
        elif today_run_error or recent_run_error:
            st.caption(f"최근 작업 목록 조회 실패: {today_run_error or recent_run_error}")
        else:
            st.caption("최근 작업 이력이 아직 없습니다.")

    if chosen_run_summary:
        latest_run_detail, latest_run_detail_error = cached_load_assignment_run_detail(chosen_run_summary.get("id"))
        latest_snapshot = latest_run_detail.get("latest_snapshot") if latest_run_detail else None
        latest_snapshot_payload = latest_snapshot.get("payload", {}) if isinstance(latest_snapshot, dict) else {}
        latest_result_delivery_rows = latest_snapshot_payload.get("result_delivery_rows", [])
        latest_grouped_delivery_rows = latest_snapshot_payload.get("grouped_delivery_rows", [])
        latest_assignment_rows = latest_snapshot_payload.get("assignment_rows", [])
        latest_assigned_summary_rows = latest_snapshot_payload.get("assigned_summary_rows", [])
        latest_route_prefix_map = latest_snapshot_payload.get("route_prefix_map", {}) or {}
        latest_truck_request_map = latest_snapshot_payload.get("truck_request_map", {}) or {}
        latest_route_line_label = latest_snapshot_payload.get("route_line_label", {}) or {}
        latest_route_camp_map = latest_snapshot_payload.get("route_camp_map", {}) or {}
        latest_camp_coords = latest_snapshot_payload.get("camp_coords", {}) or {}
        latest_selected_filter = str(latest_snapshot_payload.get("selected_filter", "전체")).strip() or "전체"
        latest_recommended_group_map = latest_snapshot_payload.get("recommended_group_map", {}) or {}
        latest_recommended_group_count = safe_int(latest_snapshot_payload.get("recommended_group_count", 0))
        latest_selected_group_filter = str(latest_snapshot_payload.get("selected_group_filter", "전체")).strip() or "전체"
        latest_group_assignment_rows = latest_snapshot_payload.get("group_assignment_rows", []) or []

        if latest_result_delivery_rows and latest_grouped_delivery_rows:
            restored_mode = True
            uploaded_filename = str(chosen_run_summary.get("source_filename", "")) or "restored_dispatch.xlsx"
            base_name = extract_base_name(uploaded_filename)
            share_name = base_name
            html_filename = f"{share_name}.html"
            base_date_str = str(chosen_run_summary.get("source_date", "")) or pd.Timestamp.now().strftime("%Y-%m-%d")
            result_all = _records_to_df_with_coords(latest_result_delivery_rows)
            result_delivery = _records_to_df_with_coords(latest_result_delivery_rows)
            grouped_delivery = _records_to_df_with_coords(latest_grouped_delivery_rows)
            route_summary = build_route_summary_from_backend_routes(latest_run_detail.get("routes", []))
            restored_dataset_key = sync_recommendation_state_with_dataset(uploaded_filename, base_date_str, route_summary)
            prepare_backend_run_state_for_dataset(restored_dataset_key)
            route_prefix_map = latest_route_prefix_map
            truck_request_map = latest_truck_request_map
            route_line_label = latest_route_line_label
            route_camp_map = latest_route_camp_map
            route_total_map = grouped_delivery.groupby("route")["house_order"].max().to_dict() if len(grouped_delivery) > 0 and "house_order" in grouped_delivery.columns else {}
            camp_coords = latest_camp_coords
            restored_selected_filter = latest_selected_filter
            restored_selected_group_filter = latest_selected_group_filter
            st.session_state["backend_run_id"] = safe_int(chosen_run_summary.get("id"))
            st.session_state["backend_dataset_key"] = restored_dataset_key
            st.session_state["backend_sync_done"] = restored_dataset_key
            st.session_state["backend_run_summary"] = chosen_run_summary
            st.session_state["assignment_store"] = apply_assignment_rows_to_store(
                latest_assignment_rows,
                st.session_state.get("assignment_store", {}),
            )
            if latest_recommended_group_map:
                restored_route_feature_df = build_route_feature_df(
                    route_summary.copy(deep=True),
                    grouped_delivery.copy(deep=True),
                    camp_coords=dict(camp_coords or {}),
                )
                restored_auto_group_count = choose_auto_group_count(restored_route_feature_df.copy(deep=True))
                restored_group_count = _infer_recommended_group_count(
                    latest_recommended_group_map,
                    fallback=latest_recommended_group_count,
                )
                restored_group_count_mode = "자동" if restored_group_count == restored_auto_group_count else "직접입력"
                restored_manual_group_count = None if restored_group_count_mode == "자동" else restored_group_count
                restored_inputs_hash = build_recommended_groups_inputs_hash(
                    restored_route_feature_df.copy(deep=True),
                    restored_group_count_mode,
                    restored_manual_group_count,
                )
                restored_group_assignment_df = _copy_recommended_groups_assignment_df(latest_group_assignment_rows)
                if len(restored_group_assignment_df) == 0:
                    restored_group_assignment_df = build_group_assignment_df(
                        restored_route_feature_df.copy(deep=True),
                        _copy_recommended_group_map(latest_recommended_group_map),
                    )
                set_recommended_groups_state(
                    group_map=latest_recommended_group_map,
                    group_count=restored_group_count,
                    assignment_df=restored_group_assignment_df,
                    inputs_hash=restored_inputs_hash,
                    selected_filter=restored_selected_group_filter,
                    meta={"source": "restored"},
                )
            else:
                mark_recommended_groups_stale("저장된 추천그룹 데이터가 없습니다.", status="inactive")
            st.caption(f"저장된 작업을 열었습니다: {chosen_run_summary.get('name', '')}")
        elif latest_run_detail and latest_run_detail.get("routes"):
            route_preview_df = pd.DataFrame(latest_run_detail.get("routes", []))
            assignment_preview_df = pd.DataFrame(latest_run_detail.get("route_assignments", []))
            st.caption("Django 최근 작업 미리보기")
            if len(route_preview_df) > 0:
                preview_cols = [c for c in ["route_prefix", "route_code", "truck_request_id", "camp_name", "total_qty"] if c in route_preview_df.columns]
                st.dataframe(route_preview_df[preview_cols] if preview_cols else route_preview_df, use_container_width=True)
            if len(assignment_preview_df) > 0:
                preview_assign_cols = [c for c in ["route_code", "driver_name", "assignment_source", "saved_at"] if c in assignment_preview_df.columns]
                st.dataframe(assignment_preview_df[preview_assign_cols] if preview_assign_cols else assignment_preview_df, use_container_width=True)
        elif latest_run_detail_error:
            st.caption(f"최근 작업 상세 조회 실패: {latest_run_detail_error}")

    if not restored_mode:
        st.info("엑셀 파일을 업로드하세요.")
        st.stop()

if not restored_mode:
    uploaded_filename = uploaded_file.name
    base_name = extract_base_name(uploaded_filename)
    share_name = base_name
    html_filename = f"{share_name}.html"

    file_bytes = uploaded_file.getvalue()

    with st.spinner("엑셀 데이터 정리 중..."):
        built = build_base_data(file_bytes)

    result_all = built["result_all"].copy()
    result_delivery = built["result_delivery"].copy()
    grouped_delivery = built["grouped_delivery"].copy()
    route_summary = built["route_summary"].copy()
    route_prefix_map = built["route_prefix_map"]
    route_total_map = built["route_total_map"]
    truck_request_map = built["truck_request_map"]
    route_line_label = built["route_line_label"]
    route_camp_map = built["route_camp_map"]
    base_date_str = infer_assignment_base_date(uploaded_filename, route_summary)
    upload_dataset_key = sync_recommendation_state_with_dataset(uploaded_filename, base_date_str, route_summary)
    prepare_backend_run_state_for_dataset(upload_dataset_key)

    backend_run_summary = None
    backend_run_detail = None
    backend_sync_error = None
    backend_sync_payload = None
    if should_sync_backend_run_for_dataset(upload_dataset_key):
        backend_sync_payload, backend_sync_error = sync_assignment_run_to_backend(uploaded_filename, base_date_str, route_summary)
        mark_backend_run_sync_done(upload_dataset_key, backend_sync_error)
        if backend_sync_payload and isinstance(backend_sync_payload, dict):
            backend_run_summary = backend_sync_payload.get("run")
            backend_run_detail = backend_sync_payload.get("detail")
            if backend_run_summary:
                st.session_state["backend_run_id"] = safe_int(backend_run_summary.get("id"))
                st.session_state["backend_run_summary"] = backend_run_summary
                if backend_run_detail and should_merge_backend_assignments(
                    upload_dataset_key,
                    route_summary,
                    st.session_state.get("assignment_store", {}),
                ):
                    st.session_state["assignment_store"] = apply_backend_assignments_to_store(
                        backend_run_detail,
                        st.session_state.get("assignment_store", {}),
                    )
                    st.session_state["backend_assignment_merge_dataset_key"] = upload_dataset_key
                st.caption(f"Django run synced: #{safe_int(backend_run_summary.get('id'))}")
        elif backend_sync_error:
            st.warning(f"Django run sync failed: {backend_sync_error}")

if len(result_all) == 0:
    st.warning("유효한 주소 데이터가 없습니다.")
    st.stop()

if len(result_delivery) == 0:
    st.warning("센터를 제외한 배송지 데이터가 없습니다.")
    st.stop()

cache = load_geocode_cache()

with st.spinner("주소 좌표 확인 중..."):
    result_delivery, cache = attach_coords_by_unique_address(result_delivery, cache)
    grouped_delivery, cache = attach_coords_by_unique_address(grouped_delivery, cache)
    camp_coords, cache = resolve_camp_coords(cache)
    save_geocode_cache(cache)

customer_memo_map, customer_memo_error = load_customer_memos_cached_for_df(grouped_delivery)
if customer_memo_error:
    st.caption(f"업체 메모 조회 실패: {customer_memo_error}")
result_delivery = attach_customer_memos(result_delivery, customer_memo_map)
grouped_delivery = attach_customer_memos(grouped_delivery, customer_memo_map)
base_dataset_key = build_route_dataset_key(uploaded_filename, base_date_str, route_summary)
active_dataset_key = base_dataset_key
if st.session_state.get("share_payload_dataset_key") != active_dataset_key:
    for share_state_key in ["share_payload_cache_key", "share_key", "share_url", "share_snapshot_error"]:
        st.session_state.pop(share_state_key, None)
    st.session_state["share_payload_dataset_key"] = active_dataset_key

recommended_groups_route_feature_df = build_route_feature_df(
    route_summary.copy(deep=True),
    grouped_delivery.copy(deep=True),
    camp_coords=dict(camp_coords or {}),
)
recommended_groups_count_mode_state = str(st.session_state.get("recommended_groups_count_mode", "자동") or "자동")
recommended_groups_manual_count_state = (
    st.session_state.get("recommended_groups_manual_count", None)
    if recommended_groups_count_mode_state == "직접입력"
    else None
)
recommended_groups_inputs_hash = build_recommended_groups_inputs_hash(
    recommended_groups_route_feature_df.copy(deep=True),
    recommended_groups_count_mode_state,
    recommended_groups_manual_count_state,
)
sync_recommended_groups_status_for_inputs(recommended_groups_inputs_hash)

render_route_dashboard(route_summary=route_summary)

tab_basic, tab_recommend, tab_stats, tab_assign, tab_memo, tab_report = st.tabs(
    ["기본", "추천", "통계", "할당", "메모", "보고"]
)

with tab_memo:
    st.subheader("업체 메모 관리")
    with st.expander("업체 메모 관리", expanded=False):
        memo_source_df = grouped_delivery.copy()
        memo_source_df["_memo_match_key"] = memo_source_df.apply(
            lambda row: (
                str(row.get("company_id", "")).strip()
            if str(row.get("company_id", "")).strip()
            else str(row.get("address_norm", "")).strip()
        ),
        axis=1,
    )
    memo_source_df = (
        memo_source_df.sort_values(["company_name", "address"])
        .drop_duplicates(subset=["_memo_match_key"], keep="first")
        .drop(columns=["_memo_match_key"])
        .reset_index(drop=True)
    )
    if len(memo_source_df) == 0:
        st.info("메모를 등록할 업체가 없습니다.")
    else:
        memo_source_df["_memo_company_name"] = memo_source_df["company_name"].fillna("").astype(str)
        memo_source_df["_memo_address"] = memo_source_df["address"].fillna("").astype(str)
        memo_search = st.text_input(
            "업체 검색",
            key="customer_memo_search",
            placeholder="업체명 또는 주소로 검색",
        ).strip()
        filtered_memo_df = memo_source_df
        if memo_search:
            filtered_memo_df = memo_source_df[
                memo_source_df["_memo_company_name"].str.contains(memo_search, case=False, na=False)
                | memo_source_df["_memo_address"].str.contains(memo_search, case=False, na=False)
            ].reset_index(drop=True)
        st.caption(f"검색 결과 {len(filtered_memo_df)}건 / 전체 {len(memo_source_df)}건")
        if len(filtered_memo_df) == 0:
            st.info("검색 결과가 없습니다.")
            preview_cols = [c for c in ["company_name", "address", "customer_memo"] if c in memo_source_df.columns]
            st.dataframe(memo_source_df[preview_cols], use_container_width=True)
            filtered_memo_df = pd.DataFrame()

    if len(memo_source_df) > 0 and len(filtered_memo_df) > 0:
        memo_labels = filtered_memo_df.apply(
            lambda row: f"{str(row.get('company_name', '')).strip() or '업체명없음'} | {str(row.get('address', '')).strip()}",
            axis=1,
        ).tolist()
        selected_memo_label = st.selectbox("메모 등록 업체", memo_labels, key="customer_memo_selector")
        selected_memo_row = filtered_memo_df.iloc[memo_labels.index(selected_memo_label)]
        current_note = str(selected_memo_row.get("customer_memo", "")).strip()
        note_value = st.text_area(
            "업체 메모",
            value=current_note,
            height=140,
            key=f"customer_memo_text_{safe_int(selected_memo_row.name)}",
            help="같은 업체 ID 또는 정규화 주소가 다시 나오면 이 메모가 함께 표시됩니다.",
        )
        if st.button("업체 메모 저장", key="customer_memo_save_button"):
            save_payload, save_error = save_customer_memo_to_backend(
                company_id=str(selected_memo_row.get("company_id", "")).strip(),
                address=str(selected_memo_row.get("address", "")).strip(),
                address_norm=str(selected_memo_row.get("address_norm", "")).strip(),
                company_name=str(selected_memo_row.get("company_name", "")).strip(),
                note=note_value,
            )
            if save_error:
                st.warning(f"업체 메모 저장 실패: {save_error}")
            else:
                st.success("업체 메모를 저장했습니다.")
                cached_load_customer_memos.clear()
                customer_memo_map, customer_memo_error = load_customer_memos_cached_for_df(grouped_delivery)
                result_delivery = attach_customer_memos(result_delivery, customer_memo_map)
                grouped_delivery = attach_customer_memos(grouped_delivery, customer_memo_map)
        preview_cols = [c for c in ["company_name", "address", "customer_memo"] if c in filtered_memo_df.columns]
        st.dataframe(filtered_memo_df[preview_cols], use_container_width=True)

with tab_recommend:
    # 추천배정 엔진 (기존 기사배정과 분리)
    st.subheader("추천배정 엔진")
    route_feature_df = recommended_groups_route_feature_df.copy(deep=True)
    group_count_mode = st.radio(
        "추천그룹 수 설정",
        ["자동", "직접입력"],
        horizontal=True,
        key="recommended_groups_count_mode",
    )
    manual_group_count = None

    if group_count_mode == "직접입력":
        manual_group_count = st.number_input(
            "추천그룹 수",
            min_value=1,
            max_value=max(1, len(route_summary)),
            value=2,
            step=1,
            key="recommended_groups_manual_count",
        )
    else:
        auto_group_count = choose_auto_group_count(route_feature_df.copy(deep=True))
        st.caption(f"자동 추천그룹 수: {auto_group_count}")

    recommended_groups_inputs_hash = build_recommended_groups_inputs_hash(
        route_feature_df.copy(deep=True),
        group_count_mode,
        manual_group_count,
    )
    sync_recommended_groups_status_for_inputs(recommended_groups_inputs_hash)

    if st.button("추천그룹 자동 추천 실행"):
        try:
            feature_df_for_calc = route_feature_df.copy(deep=True)
            recommended_group_count = resolve_group_count(
                feature_df_for_calc.copy(deep=True),
                manual_group_count=manual_group_count,
            )
            recommended_group_map = recommend_route_groups(
                feature_df_for_calc.copy(deep=True),
                manual_group_count=manual_group_count,
            )
            recommended_group_map = filter_group_map_for_routes(
                feature_df_for_calc.copy(deep=True),
                recommended_group_map,
            )
            if not recommended_group_map or not has_complete_group_map(feature_df_for_calc.copy(deep=True), recommended_group_map):
                mark_recommended_groups_error("추천 계산 결과가 현재 데이터와 맞지 않습니다. 추천그룹을 다시 계산해 주세요.")
                st.error("추천 계산 결과가 현재 데이터와 맞지 않습니다. 기존 추천 결과는 유지했습니다.")
            else:
                latest_assignment_df = build_group_assignment_df(feature_df_for_calc.copy(deep=True), recommended_group_map)
                if len(latest_assignment_df) == 0:
                    mark_recommended_groups_error("추천 계산 결과로 표시할 그룹 배정 데이터가 없습니다.")
                    st.error("추천 계산 결과가 비어 있습니다. 기존 추천 결과는 유지했습니다.")
                else:
                    set_recommended_groups_state(
                        group_map=recommended_group_map,
                        group_count=recommended_group_count,
                        assignment_df=latest_assignment_df.copy(deep=True),
                        inputs_hash=recommended_groups_inputs_hash,
                        selected_filter="전체",
                        meta={
                            "group_count_mode": group_count_mode,
                            "manual_group_count": safe_int(manual_group_count) if manual_group_count is not None else 0,
                        },
                    )
                    st.success("추천그룹 생성을 완료했습니다.")
        except Exception as exc:
            logger.exception("Recommendation group calculation failed.")
            mark_recommended_groups_error(f"{type(exc).__name__}: {exc}")
            st.error("추천그룹 계산에 실패했습니다. 기존 추천 결과는 유지했습니다.")

    recommended_groups_projection = get_recommended_groups_projection(
        route_feature_df.copy(deep=True),
        recommended_groups_inputs_hash,
    )
    recommendation_error = recommended_groups_projection.get("error", "")
    if recommendation_error:
        st.error(f"추천그룹 오류: {recommendation_error}")

    recommendation_status = str(recommended_groups_projection.get("status", "inactive"))
    has_stored_recommendation = bool(_copy_recommended_group_map(st.session_state.get("recommended_groups_result", {})))
    if has_stored_recommendation and recommendation_status in {"stale", "inactive"}:
        st.warning("추천 입력 조건이 바뀌었거나 현재 데이터와 맞지 않아 추천그룹을 다시 계산해야 합니다. 직전 성공 결과는 보존되어 있습니다.")

    recommended_group_map = recommended_groups_projection["group_map"]

    if recommended_group_map:
        group_assignment_df = recommended_groups_projection["assignment_df"].copy(deep=True)
        if len(group_assignment_df) == 0:
            group_assignment_df = build_group_assignment_df(route_feature_df.copy(deep=True), recommended_group_map)
        with st.expander("추천그룹 수정", expanded=False):
            edit_map = default_group_edit_map(group_assignment_df)
            recommended_group_count = safe_int(recommended_groups_projection.get("group_count", 0))
            if recommended_group_count <= 0:
                recommended_group_count = max(1, safe_int(group_assignment_df["추천그룹"].nunique()))
            group_options = [f"추천그룹 {i}" for i in range(1, recommended_group_count + 1)]
            with st.form("group_edit_form", clear_on_submit=False):
                new_group_map = edit_map.copy()
                edit_rows = group_assignment_df.sort_values(["route_prefix", "route"]).reset_index(drop=True)

                for _, row in edit_rows.iterrows():
                    route = row["route"]
                    current_group = recommended_group_map.get(route, row["추천그룹"])
                    default_idx = group_options.index(current_group) if current_group in group_options else 0

                    c1, c2, c3, c4 = st.columns([1.0, 0.8, 1.2, 1.2])
                    c1.write(str(route))
                    c2.write(str(row.get("route_prefix", "")))
                    c3.write(str(row.get("truck_request_id", "")))
                    selected_group = c4.selectbox(
                        f"추천그룹선택_{route}",
                        options=group_options,
                        index=default_idx,
                        label_visibility="collapsed",
                        key=f"group_select_{route}"
                    )
                    new_group_map[route] = selected_group

                group_submitted = st.form_submit_button("추천그룹 수정 적용")

            if group_submitted:
                updated_assignment_df = apply_group_edit_map(route_feature_df.copy(deep=True), new_group_map)
                updated_group_map = default_group_edit_map(updated_assignment_df)
                if not updated_group_map or not has_complete_group_map(route_feature_df.copy(deep=True), updated_group_map):
                    mark_recommended_groups_error("추천그룹 수동 수정 결과가 현재 데이터와 맞지 않습니다.")
                    st.error("추천그룹 수동 수정 결과가 현재 데이터와 맞지 않아 기존 결과를 유지했습니다.")
                else:
                    set_recommended_groups_state(
                        group_map=updated_group_map,
                        group_count=recommended_group_count,
                        assignment_df=updated_assignment_df.copy(deep=True),
                        inputs_hash=recommended_groups_inputs_hash,
                        selected_filter=st.session_state.get("recommended_groups_selected_filter", "전체"),
                        meta={
                            "group_count_mode": group_count_mode,
                            "manual_group_count": safe_int(manual_group_count) if manual_group_count is not None else 0,
                            "source": "manual_edit",
                        },
                    )
                    recommended_groups_projection = get_recommended_groups_projection(
                        route_feature_df.copy(deep=True),
                        recommended_groups_inputs_hash,
                    )
                    recommended_group_map = recommended_groups_projection["group_map"]
                    group_assignment_df = recommended_groups_projection["assignment_df"].copy(deep=True)
                    st.success("추천그룹 수동 수정을 반영했습니다.")

        final_group_map = filter_group_map_for_routes(
            route_feature_df.copy(deep=True),
            recommended_group_map,
        )
        if not final_group_map or not has_complete_group_map(route_feature_df, final_group_map):
            mark_recommended_groups_error("표시할 추천그룹 데이터가 현재 입력과 맞지 않습니다.")
            st.info("표시할 추천그룹 데이터가 없습니다. 추천그룹을 다시 계산해 주세요.")
        else:
            if final_group_map != recommended_group_map:
                set_recommended_groups_state(
                    group_map=final_group_map,
                    group_count=recommended_groups_projection.get("group_count", 0),
                    assignment_df=build_group_assignment_df(route_feature_df.copy(deep=True), final_group_map),
                    inputs_hash=recommended_groups_inputs_hash,
                    selected_filter=st.session_state.get("recommended_groups_selected_filter", "전체"),
                    meta={"source": "route_filter"},
                )
            latest_assignment_df = build_group_assignment_df(route_feature_df.copy(deep=True), final_group_map)
            assignment_store = st.session_state.get("assignment_store", {})
            assignment_store = render_group_driver_assignment_form(
                route_summary=route_summary,
                drivers=drivers,
                assignment_store=assignment_store,
                group_assignment_df=latest_assignment_df,
                uploaded_filename=uploaded_filename,
                base_date_str=base_date_str,
            )
            st.session_state["assignment_store"] = assignment_store

            with st.expander("기사 선호 예상 순위 (참고용)", expanded=False):
                st.caption("추천그룹별 물량·스톱·예상시간·퍼짐 기준의 휴리스틱 참고 순위입니다.")
                preference_df = build_driver_preference_df(route_feature_df.copy(deep=True), final_group_map)
                preference_display_df = build_driver_preference_display_df(preference_df)
                if len(preference_display_df) == 0:
                    st.info("표시할 선호예상 데이터가 없습니다.")
                else:
                    st.dataframe(preference_display_df, use_container_width=True)
    else:
        pass

with tab_basic:
    summary_container = st.container()
    map_container = st.container()
    form_container = st.container()

    assignment_store = st.session_state["assignment_store"]
    recommended_groups_projection_for_view = get_recommended_groups_projection(
        recommended_groups_route_feature_df.copy(deep=True),
        recommended_groups_inputs_hash,
    )
    latest_group_assignment_df = recommended_groups_projection_for_view["assignment_df"].copy(deep=True)
    with form_container:
        assignment_store = render_assignment_form(
            route_summary,
            drivers,
            assignment_store,
            uploaded_filename,
            base_date_str,
        )
    st.session_state["assignment_store"] = assignment_store

    assignment_store = resync_assignment_store_from_file_if_changed(
        route_summary,
        "file_resynced_before_assignment_df",
    )
    st.session_state["assignment_store"] = assignment_store
    assignment_df = build_assignment_df(route_summary, st.session_state["assignment_store"])

    group_route_map = {}
    if len(latest_group_assignment_df) > 0 and "route" in latest_group_assignment_df.columns and "추천그룹" in latest_group_assignment_df.columns:
        group_route_map = dict(zip(latest_group_assignment_df["route"], latest_group_assignment_df["추천그룹"]))

    assigned_summary = build_assigned_summary(assignment_df)
    if len(assigned_summary) == 0:
        view_assigned_summary = pd.DataFrame()
    else:
        view_assigned_summary = assigned_summary.copy()
        if "총걸린분" in view_assigned_summary.columns:
            view_assigned_summary = view_assigned_summary.drop(columns=["총걸린분"])

    with summary_container:
        st.subheader("기사별 요약")
        if len(view_assigned_summary) == 0:
            st.info("아직 기사 배정이 없습니다.")
        else:
            st.dataframe(view_assigned_summary, use_container_width=True)

    with map_container:
        st.subheader("지도")
        st.subheader("기사별 필터")
        driver_filter_options = ["전체", "미배정"] + drivers
        if st.session_state.get("selected_filter_dataset_key") != active_dataset_key:
            st.session_state["selected_filter_value"] = "전체"
            st.session_state["selected_filter_dataset_key"] = active_dataset_key
        if st.session_state.get("selected_filter_value") not in driver_filter_options:
            st.session_state["selected_filter_value"] = "전체"
        selected_filter = st.selectbox("지도 표시 대상", driver_filter_options, key="selected_filter_value")

        main_overlay_cache_key = build_main_overlay_cache_key(
            active_dataset_key,
            selected_filter,
            route_summary,
            st.session_state["assignment_store"],
        )
        cached_main_map_data = get_cached_main_map_data(main_overlay_cache_key)
        if cached_main_map_data:
            valid_result = cached_main_map_data["valid_result"]
            valid_grouped = cached_main_map_data["valid_grouped"]
            route_driver_map = cached_main_map_data["route_driver_map"]
            map_unmapped_df = cached_main_map_data.get("unmapped_df", pd.DataFrame())
            st.session_state["main_overlay_cache_status"] = "data_hit"
        else:
            valid_result, valid_grouped, route_driver_map, map_unmapped_df = build_map_data(
                result_delivery=result_delivery,
                grouped_delivery=grouped_delivery,
                assignment_df=assignment_df,
                selected_filter=selected_filter,
                route_prefix_map=route_prefix_map,
                route_camp_map=route_camp_map,
                pickup_grouped=True,
            )
            set_cached_main_map_data(main_overlay_cache_key, valid_result, valid_grouped, route_driver_map, map_unmapped_df)
            st.session_state["main_overlay_cache_status"] = "data_miss"

        st.write(f"캐시 주소 수: {len(cache)}")
        st.write(f"현재 필터: {selected_filter}")
        st.caption("캠프는 고정핀(검정색), 배송지는 라우트/기사 상태에 따라 표시됩니다.")

        marker_count = len(valid_grouped)
        st.write(f"지도에 찍힌 배송 핀 수: {marker_count}")
        unmapped_count = safe_int(len(map_unmapped_df)) if isinstance(map_unmapped_df, pd.DataFrame) else 0
        if unmapped_count > 0:
            st.warning(f"지도 미표시 업체 {unmapped_count}건")
            with st.expander("지도 미표시 업체 목록", expanded=False):
                display_cols = [
                    c for c in ["route", "company_name", "address", "missing_reason"]
                    if c in map_unmapped_df.columns
                ]
                st.dataframe(map_unmapped_df[display_cols] if display_cols else map_unmapped_df, use_container_width=True)
        else:
            st.caption("지도 미표시 업체 0건")

        static_map_cache_key = build_static_map_cache_key(active_dataset_key, selected_filter, assignment_df)
        if st.session_state.pop("map_force_refresh", False):
            st.session_state["main_map_key_nonce"] = safe_int(st.session_state.get("main_map_key_nonce", 0)) + 1
            if st.session_state.get("static_map_html_cache_key") == static_map_cache_key:
                st.session_state.pop("static_map_html_cache_key", None)
                st.session_state.pop("static_map_html", None)
                st.session_state.pop("static_map_html_filename", None)
        main_map_key_nonce = safe_int(st.session_state.get("main_map_key_nonce", 0))
        main_map_key = "dispatch_main_static_map" if main_map_key_nonce <= 0 else f"dispatch_main_static_map_{main_map_key_nonce}"
        st.session_state["last_main_map_key"] = main_map_key
        render_debug_status_panel(
            route_summary=route_summary,
            assignment_store=st.session_state["assignment_store"],
            assignment_df=assignment_df,
            selected_filter=selected_filter,
            main_map_key=main_map_key,
            marker_count=marker_count,
            valid_result=valid_result,
            valid_grouped=valid_grouped,
        )

        main_map_html = get_cached_static_map_html(static_map_cache_key)
        if main_map_html:
            st.session_state["main_static_map_cache_status"] = "html_hit"
        else:
            st.session_state["main_static_map_cache_status"] = "html_miss"
            with st.spinner("지도 HTML 준비 중..."):
                main_map_html = get_static_map_html(
                    cache_key=static_map_cache_key,
                    html_filename=html_filename,
                    valid_result=valid_result,
                    valid_grouped=valid_grouped,
                    route_prefix_map=route_prefix_map,
                    truck_request_map=truck_request_map,
                    route_line_label=route_line_label,
                    route_driver_map=route_driver_map,
                    route_camp_map=route_camp_map,
                    camp_coords=camp_coords,
                )
        components.html(main_map_html, height=900, scrolling=True)

with tab_stats:
    recommended_groups_projection_for_view = get_recommended_groups_projection(
        recommended_groups_route_feature_df.copy(deep=True),
        recommended_groups_inputs_hash,
    )
    latest_group_assignment_df = recommended_groups_projection_for_view["assignment_df"].copy(deep=True)
    group_route_map = {}
    if len(latest_group_assignment_df) > 0 and "route" in latest_group_assignment_df.columns and "추천그룹" in latest_group_assignment_df.columns:
        group_route_map = dict(zip(latest_group_assignment_df["route"], latest_group_assignment_df["추천그룹"]))

    view_assignment_df = assignment_df.copy()
    if group_route_map:
        view_assignment_df["추천그룹"] = view_assignment_df["route"].map(group_route_map).fillna("")
    if "총걸린분" in view_assignment_df.columns:
        view_assignment_df = view_assignment_df.drop(columns=["총걸린분"])

    history_df = load_assignment_history()
    stats_df, recent_work_date_str = build_driver_assignment_stats_df(
        assignment_df=assignment_df,
        history_df=history_df,
        driver_candidates=drivers,
        base_date=base_date_str,
    )
    check_live_today_stats_consistency(assignment_df, stats_df)

    st.subheader("기사별 배정 통계")
    st.caption("오늘물량은 현재 화면의 최신 배정 기준이고, 최근근무일/7일/30일 평균은 assignment_history.csv 이력 기준입니다.")
    if recent_work_date_str:
        st.caption(f"최근근무일(1일) 기준일: {recent_work_date_str}")
    else:
        st.caption("최근근무일(1일) 기준일: 없음")
    if len(stats_df) == 0:
        st.info("표시할 기사 통계가 없습니다.")
    else:
        st.dataframe(stats_df, use_container_width=True)

    st.caption(f"배정 이력 저장 기준일: {base_date_str} (동일 날짜 재저장 시 덮어쓰기)")
    if st.button("배정 이력 저장"):
        saved_count = save_assignment_history_for_date(assignment_df, base_date_str)
        if saved_count > 0:
            st.session_state["send_assignment_completion_push"] = True
            st.session_state["refresh_share_payload_once"] = True
            st.success(f"배정 이력을 저장했습니다. 기준일 {base_date_str}, {saved_count}건 저장 완료")
        else:
            st.warning(f"저장할 배정 데이터가 없어 {base_date_str} 이력은 0건으로 덮어쓰기되었습니다.")

    recommended_groups_payload = recommended_groups_projection_for_view["payload"]
    shared_result_delivery = result_delivery.copy(deep=True)
    shared_grouped_delivery = grouped_delivery.copy(deep=True)
    shared_result_delivery["assigned_driver"] = shared_result_delivery["route"].map(route_driver_map).fillna("")
    shared_grouped_delivery["assigned_driver"] = shared_grouped_delivery["route"].map(route_driver_map).fillna("")
    shared_result_delivery["추천그룹"] = shared_result_delivery["route"].map(group_route_map).fillna("")
    shared_grouped_delivery["추천그룹"] = shared_grouped_delivery["route"].map(group_route_map).fillna("")

    shared_pickup_grouped_delivery = build_pickup_map_grouped_df(
        shared_grouped_delivery,
        route_prefix_map=route_prefix_map,
        route_camp_map=route_camp_map,
        route_driver_map=route_driver_map,
    )

    backend_run_id = safe_int(st.session_state.get("backend_run_id", 0))
    share_group_key = hashlib.sha1(
        json.dumps(
            recommended_groups_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    share_payload_cache_key = f"{static_map_cache_key}:share:{backend_run_id}:{share_group_key}"
    share_url = st.session_state.get("share_url", f"{APP_URL}?map={share_name}")
    share_key = st.session_state.get("share_key", "")
    share_snapshot_error = st.session_state.get("share_snapshot_error", "")
    share_payload_ready = st.session_state.get("share_payload_cache_key") == share_payload_cache_key
    refresh_share_payload = st.session_state.pop("refresh_share_payload_once", False)
    if st.button("공유 지도/스냅샷 생성 또는 갱신", key="refresh_share_payload_button"):
        refresh_share_payload = True
    if not st.session_state.get("share_url"):
        refresh_share_payload = True

    if refresh_share_payload:
        with st.spinner("공유/보고용 HTML 지도 갱신 중..."):
            map_html = get_static_map_html(
                cache_key=static_map_cache_key,
                html_filename=html_filename,
                valid_result=valid_result,
                valid_grouped=valid_grouped,
                route_prefix_map=route_prefix_map,
                truck_request_map=truck_request_map,
                route_line_label=route_line_label,
                route_driver_map=route_driver_map,
                route_camp_map=route_camp_map,
                camp_coords=camp_coords,
            )

            save_share_payload(
                share_name,
                map_html,
                view_assignment_df,
                view_assigned_summary,
                result_delivery_df=shared_result_delivery,
                grouped_delivery_df=shared_grouped_delivery,
                pickup_grouped_delivery_df=shared_pickup_grouped_delivery,
                route_prefix_map=route_prefix_map,
                truck_request_map=truck_request_map,
                route_line_label=route_line_label,
                route_camp_map=route_camp_map,
                camp_coords=camp_coords,
                group_assignment_df=latest_group_assignment_df.copy(deep=True),
            )

            snapshot_payload = {
                "map_html": map_html,
                "assignment_rows": view_assignment_df.fillna("").to_dict(orient="records"),
                "assigned_summary_rows": view_assigned_summary.fillna("").to_dict(orient="records") if len(view_assigned_summary) > 0 else [],
                "result_delivery_rows": _df_to_records_for_json(shared_result_delivery),
                "grouped_delivery_rows": _df_to_records_for_json(shared_grouped_delivery),
                "pickup_grouped_delivery_rows": _df_to_records_for_json(shared_pickup_grouped_delivery),
                "route_prefix_map": route_prefix_map,
                "truck_request_map": truck_request_map,
                "route_line_label": route_line_label,
                "route_camp_map": route_camp_map,
                "camp_coords": camp_coords,
                "selected_filter": selected_filter,
                "recommended_group_map": recommended_groups_payload["recommended_group_map"],
                "recommended_group_count": recommended_groups_payload["recommended_group_count"],
                "selected_group_filter": recommended_groups_payload["selected_group_filter"],
                "group_assignment_rows": recommended_groups_payload["group_assignment_rows"],
            }
            snapshot_save_payload, snapshot_save_error = sync_run_snapshot_to_backend(backend_run_id, snapshot_payload, snapshot_kind="recent")
            if snapshot_save_error:
                st.caption(f"Django snapshot sync failed: {snapshot_save_error}")
            elif snapshot_save_payload is not None:
                st.caption(f"Django snapshot synced: {snapshot_save_payload.get('share_key', '')}")

            share_snapshot_payload, share_snapshot_error = sync_run_snapshot_to_backend(backend_run_id, snapshot_payload, snapshot_kind="share")
            share_key = f"run-{backend_run_id}-share" if backend_run_id > 0 else ""
            if share_snapshot_payload is not None:
                share_key = str(share_snapshot_payload.get("share_key", "")).strip() or share_key
            share_url = build_backend_share_url(share_key) if share_key else f"{APP_URL}?map={share_name}"

            st.session_state["share_payload_cache_key"] = share_payload_cache_key
            st.session_state["share_key"] = share_key
            st.session_state["share_url"] = share_url
            st.session_state["share_snapshot_error"] = share_snapshot_error
            share_payload_ready = True
    elif not share_payload_ready:
        st.caption("공유 지도는 이전 생성본입니다. 최신 배정으로 공유하려면 '공유 지도/스냅샷 생성 또는 갱신'을 눌러 주세요.")

    st.subheader("지도 공유 링크")
    if share_snapshot_error:
        if share_key:
            st.info(f"Django share page sync skipped existing share snapshot: {share_snapshot_error}")
        else:
            st.warning(f"Django share page sync failed: {share_snapshot_error}")
    else:
        st.success("아래 링크를 복사해서 바로 공유하시면 됩니다.")
    st.markdown(f"### [🔗 지도 + 기사할당표 바로 열기]({share_url})")
    st.text_input("공유 URL", value=share_url, key="share_url_box")

    total_box_total = 0
    nasil_box_total = 0
    external_box_total = 0
    if len(shared_grouped_delivery) > 0:
        assigned_box_rows = shared_grouped_delivery[
            shared_grouped_delivery["assigned_driver"].fillna("").astype(str).str.strip() != ""
        ].copy()
        assigned_box_rows["assigned_driver"] = assigned_box_rows["assigned_driver"].fillna("").astype(str).str.strip()
        assigned_box_rows["box_total"] = (
            assigned_box_rows.get("ae_sum", pd.Series(dtype=float)).fillna(0)
            + assigned_box_rows.get("af_sum", pd.Series(dtype=float)).fillna(0)
            + assigned_box_rows.get("ag_sum", pd.Series(dtype=float)).fillna(0)
        )
        total_box_total = safe_int(assigned_box_rows["box_total"].sum())
        nasil_driver_mask = (
            assigned_box_rows["assigned_driver"].isin(["김태경", "김태균"])
            | assigned_box_rows["assigned_driver"].str.contains("나실", na=False)
        )
        nasil_box_total = safe_int(assigned_box_rows.loc[nasil_driver_mask, "box_total"].sum())
        external_box_total = safe_int(assigned_box_rows.loc[~nasil_driver_mask, "box_total"].sum())

    telegram_summary_text = (
        f"[배차 완료]\n"
        f"기준📅: {base_date_str}\n"
        f"전체📦: {total_box_total}\n"
        f"나실🚚: {nasil_box_total}\n"
        f"기사🚛: {external_box_total}\n"
        f"근무👥: {safe_int(view_assignment_df['assigned_driver'].fillna('').astype(str).str.strip().replace('', pd.NA).dropna().nunique()) if len(view_assignment_df) > 0 and 'assigned_driver' in view_assignment_df.columns else 0}\n"
        f"지도🧭: {share_url}"
    )
    telegram_memo_text = build_driver_memo_report(shared_grouped_delivery)

    if st.session_state.get("send_assignment_completion_push"):
        st.session_state.pop("send_assignment_completion_push", None)
        notification_status, notification_result = send_assignment_completion_notifications(
            telegram_summary_text,
            telegram_memo_text,
        )
        if notification_status == "sent":
            st.success("Assignment history was saved and Telegram notification was sent.")
        elif notification_status == "failed":
            st.warning(f"Assignment history was saved, but Telegram notification failed: {notification_result}")

    with st.expander("Telegram group send", expanded=False):
        telegram_configured = is_telegram_configured()
        st.caption("Send the summary and driver memo report to the Telegram group.")
        st.text_area("Message 1", value=telegram_summary_text, height=140, key="telegram_summary_preview")
        st.text_area("Message 2", value=telegram_memo_text, height=260, key="telegram_memo_preview")
        if not telegram_configured:
            st.caption("Telegram is disabled until TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set in .env.")
        if st.button("Send to Telegram group", key="telegram_send_button", disabled=not telegram_configured):
            notification_status, notification_result = send_assignment_completion_notifications(
                telegram_summary_text,
                telegram_memo_text,
            )
            if notification_status == "sent":
                st.success("Sent to the Telegram group.")
            elif notification_status == "failed":
                st.warning(f"Telegram send failed: {notification_result}")

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
        key=lambda s: s.map(_natural_desc_sort_key),
        ascending=False,
    )
    export_df = export_df.rename(columns={"truck_request_id": "트럭요청ID", "assigned_driver": "이름"})
    csv_data = export_df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
    st.download_button(
        "기사 배정표 CSV 다운로드",
        data=csv_data,
        file_name="route_assignment.csv",
        mime="text/csv"
    )

with tab_assign:
    render_coupang_assignment_panel(export_df)

with tab_report:
    st.divider()
    st.subheader("취소건 관리")
    cancel_store = st.session_state.get("cancel_store", {})
    cancel_df = build_cancel_management_df(
        grouped_delivery=grouped_delivery,
        route_driver_map=route_driver_map,
        base_date=base_date_str,
        cancel_store=cancel_store,
    )

    if len(cancel_df) == 0:
        st.info("관리할 취소 대상이 없습니다.")
    else:
        cancel_only = st.checkbox("취소 입력된 행만 보기", value=False, key="cancel_only_filter")
        visible_cancel_df = cancel_df.copy()
        visible_settlement_excluded = visible_cancel_df["정산제외"].apply(safe_bool) if "정산제외" in visible_cancel_df.columns else pd.Series(False, index=visible_cancel_df.index)
        if cancel_only:
            visible_cancel_df = visible_cancel_df[
                (visible_cancel_df["취소수량"] > 0)
                | visible_settlement_excluded
                | (visible_cancel_df["취소사유"].astype(str).str.strip() != "")
            ].copy()

        with st.form("report_search_form", clear_on_submit=False):
            search_col1, search_col2, search_col3 = st.columns([1.4, 1.4, 0.4])
            with search_col1:
                report_milkrun_search = st.text_input(
                    "밀크런번호 검색",
                    key="report_milkrun_search",
                    placeholder="밀크런번호",
                ).strip()
            with search_col2:
                report_company_search = st.text_input(
                    "업체명 검색",
                    key="report_company_search",
                    placeholder="업체명",
                ).strip()
            with search_col3:
                st.write("")
                st.form_submit_button("검색 적용")

        def _normalized_report_search_series(df: pd.DataFrame, column: str) -> pd.Series:
            if column not in df.columns:
                return pd.Series("", index=df.index, dtype="object")
            return (
                df[column]
                .fillna("")
                .astype(str)
                .str.replace(r"\s+", "", regex=True)
                .str.casefold()
            )

        if report_milkrun_search and "milkrun_no" in visible_cancel_df.columns:
            report_milkrun_query = re.sub(r"\s+", "", report_milkrun_search).casefold()
            visible_cancel_df = visible_cancel_df[
                _normalized_report_search_series(visible_cancel_df, "milkrun_no").str.contains(
                    re.escape(report_milkrun_query),
                    na=False,
                    regex=True,
                )
            ].copy()
        if report_company_search and "company_name" in visible_cancel_df.columns:
            report_company_query = re.sub(r"\s+", "", report_company_search).casefold()
            visible_cancel_df = visible_cancel_df[
                _normalized_report_search_series(visible_cancel_df, "company_name").str.contains(
                    re.escape(report_company_query),
                    na=False,
                    regex=True,
                )
            ].copy()

        report_export_df = build_report_export_df(cancel_df)
        visible_cancel_qty_sum = (
            safe_int(visible_cancel_df["취소수량"].sum())
            if len(visible_cancel_df) > 0 and "취소수량" in visible_cancel_df.columns
            else 0
        )
        c1, c2, c3 = st.columns(3)
        c1.metric("보고 대상 행", len(report_export_df))
        c2.metric("취소수량 합계", visible_cancel_qty_sum)
        c3.metric(
            "보고 route 수",
            safe_int(report_export_df["route"].nunique()) if len(report_export_df) > 0 and "route" in report_export_df.columns else 0,
        )

        if not TEMPLATE_REPORT_XLSX.exists():
            st.info("템플릿 파일이 필요합니다.")
        else:
            try:
                report_export_payload = build_report_export_payload(
                    grouped_delivery=grouped_delivery,
                    report_export_df=report_export_df,
                    base_date_str=base_date_str,
                )
                report_xlsx_bytes = make_report_xlsx_bytes(report_export_payload)
            except Exception as exc:
                st.error(f"엑셀 보고서 생성 중 오류가 발생했습니다: {exc}")
            else:
                st.download_button(
                    label="엑셀 보고서 다운로드",
                    data=report_xlsx_bytes,
                    file_name=build_report_export_filename(base_date_str),
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key=f"report_xlsx_download_{base_date_str}",
                )

        visible_columns = [c for c in REPORT_CANCEL_VISIBLE_COLUMNS if c in visible_cancel_df.columns]
        editor_source_df = visible_cancel_df[visible_columns].copy()
        editor_df = editor_source_df.rename(columns=REPORT_CANCEL_DISPLAY_RENAME)

        with st.form(f"cancel_management_form_{base_date_str}", clear_on_submit=False):
            edited_cancel_df = st.data_editor(
                editor_df,
                hide_index=True,
                num_rows="fixed",
                use_container_width=True,
                disabled=[
                    c for c in editor_df.columns
                    if c not in REPORT_CANCEL_EDITABLE_COLUMNS
                ],
                key=f"cancel_editor_{base_date_str}",
            )
            save_cancel_clicked = st.form_submit_button("취소건 관리 저장")

        if save_cancel_clicked:
            updated_store = cancel_store.copy()
            source_df = editor_source_df.reset_index(drop=True)
            edited_df = edited_cancel_df.reset_index(drop=True)
            for idx, row in edited_df.iterrows():
                source_row = source_df.iloc[idx]
                cancel_key = source_row.get("cancel_key", "")
                cancel_count = 0
                cancel_qty = max(0, safe_int(row.get("취소수량", 0)))
                cancel_reason = str(row.get("취소사유", "")).strip()
                settlement_excluded = safe_bool(source_row.get("정산제외", False))
                if settlement_excluded:
                    cancel_count = max(1, safe_int(source_row.get("stop_count", 0)))
                if cancel_key:
                    if cancel_count > 0 or cancel_qty > 0 or cancel_reason:
                        updated_store[cancel_key] = {
                            "cancel_count": cancel_count,
                            "cancel_qty": cancel_qty,
                            "reason": cancel_reason,
                        }
                    else:
                        updated_store.pop(cancel_key, None)
            save_cancel_store(updated_store)
            st.session_state["cancel_store"] = updated_store
            st.success("취소건 관리 내용을 저장했습니다.")

        with st.expander("취소 입력 요약", expanded=True):
            saved_cancel_df = build_cancel_management_df(
                grouped_delivery=grouped_delivery,
                route_driver_map=route_driver_map,
                base_date=base_date_str,
                cancel_store=st.session_state.get("cancel_store", {}),
            )
            saved_settlement_excluded = saved_cancel_df["정산제외"].apply(safe_bool) if "정산제외" in saved_cancel_df.columns else pd.Series(False, index=saved_cancel_df.index)
            saved_cancel_df = saved_cancel_df[
                (saved_cancel_df["취소수량"] > 0)
                | saved_settlement_excluded
                | (saved_cancel_df["취소사유"].astype(str).str.strip() != "")
            ].copy()
            if len(saved_cancel_df) == 0:
                st.info("저장된 취소건이 없습니다.")
            else:
                summary_cols = [
                    "assigned_driver", "house_order", "milkrun_no", "route_prefix",
                    "총수량", "취소수량", "정산제외", "취소사유",
                ]
                st.dataframe(saved_cancel_df[[c for c in summary_cols if c in saved_cancel_df.columns]], use_container_width=True)
