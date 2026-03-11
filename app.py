import os
import re
import json
import math
from io import BytesIO

import requests
import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import folium
from folium.features import DivIcon
from folium.plugins import OverlappingMarkerSpiderfier
from streamlit_folium import st_folium

from auto_grouping import (
    apply_group_edit_map,
    build_group_assignment_df,
    build_group_map_data,
    build_group_summary_df,
    build_group_detail_stats_df,
    build_driver_preference_df,
    build_route_feature_df,
    choose_auto_group_count,
    default_group_edit_map,
    recommend_route_groups,
    resolve_group_count,
)

st.set_page_config(page_title="배차 지도", layout="wide")
st.title("배차 지도")

# =========================
# 설정값
# =========================
KAKAO_API_KEY = os.getenv("KAKAO_API_KEY", "080375c5f09bbb8c7db9f368bc752d33")

TIME_COL_INDEX = 21   # V열
CACHE_FILE = "geocode_cache.csv"
DRIVER_FILE = "drivers.csv"
MAP_DIR = "saved_maps"
SHARE_DIR = "shared_payloads"
ASSIGNMENT_FILE = "route_assignments.json"
APP_URL = "https://dispatch-map.streamlit.app"  # 본인 배포 주소로 수정

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

uploaded_file = st.file_uploader("엑셀 파일 업로드", type=["xlsx"])

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


def normalize_address(addr: str) -> str:
    if pd.isna(addr):
        return ""
    text = str(addr).strip()
    text = re.sub(r"\s+", " ", text)
    return text


def extract_base_name(filename: str) -> str:
    name = os.path.splitext(str(filename))[0]
    name = re.sub(r"[^\w\-가-힣]+", "_", name)
    return name.strip("_") or "dispatch_map"


def load_drivers():
    if os.path.exists(DRIVER_FILE):
        try:
            df = pd.read_csv(DRIVER_FILE)
            if "driver_name" in df.columns:
                return df["driver_name"].dropna().astype(str).tolist()
        except Exception:
            return []
    return []


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
    except Exception:
        pass


def make_assignment_key(route: str, truck_request_id: str) -> str:
    return f"{str(route).strip()}__{str(truck_request_id).strip()}"


def is_header_like_route_values(route_value, truck_request_id_value) -> bool:
    route_text = str(route_value).strip().lower()
    truck_text = str(truck_request_id_value).strip().lower()
    header_routes = {"루트 번호", "루트번호", "route", "route no", "route_no"}
    header_trucks = {"트럭 요청 id", "트럭요청id", "truck request id", "truck_request_id"}
    return route_text in header_routes or truck_text in header_trucks


def _df_to_records_for_json(df: pd.DataFrame):
    out = []
    for row in df.fillna("").to_dict(orient="records"):
        r = row.copy()
        coords = r.get("coords")
        if isinstance(coords, tuple):
            r["coords"] = [coords[0], coords[1]]
        out.append(r)
    return out


def _records_to_df_with_coords(records):
    df = pd.DataFrame(records or [])
    if len(df) == 0:
        return df

    if "coords" in df.columns:
        def to_tuple(v):
            if isinstance(v, (list, tuple)) and len(v) == 2:
                return (float(v[0]), float(v[1]))
            return None
        df["coords"] = df["coords"].apply(to_tuple)
    return df


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
):
    payload_path = os.path.join(SHARE_DIR, f"{share_name}.json")
    payload = {
        "map_html": map_html,
        "assignment_rows": assignment_df.fillna("").to_dict(orient="records"),
        "assigned_summary_rows": assigned_summary.fillna("").to_dict(orient="records"),
        "result_delivery_rows": _df_to_records_for_json(result_delivery_df) if result_delivery_df is not None else [],
        "grouped_delivery_rows": _df_to_records_for_json(grouped_delivery_df) if grouped_delivery_df is not None else [],
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
        center_type = row.iloc[8]         # I열
        spu_center = row.iloc[12]         # M열
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
            center_type_str = str(center_type).strip() if pd.notna(center_type) else ""
            spu_center_str = str(spu_center).strip() if pd.notna(spu_center) else ""
            time_str = format_time_value(time_val)

            is_center_row = (
                center_type_str == "센터"
                or spu_center_str in CENTER_CODES
            )

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
                "center_type": center_type_str,
                "spu_center": spu_center_str,
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

    # 같은 route 안에서 같은 주소는 같은 집으로 처리
    house_order_df = (
        result_delivery.groupby(["route", "address_norm"], as_index=False)
        .agg(first_stop=("stop_order", "min"))
        .sort_values(["route", "first_stop", "address_norm"])
        .reset_index(drop=True)
    )
    house_order_df["house_order"] = house_order_df.groupby("route").cumcount() + 1

    result_delivery = result_delivery.merge(
        house_order_df[["route", "address_norm", "house_order"]],
        on=["route", "address_norm"],
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
        result_delivery.groupby(["route", "address_norm"], as_index=False)
        .agg(
            truck_request_id=("truck_request_id", "first"),
            company_id=("company_id", "first"),
            company_name=("company_name", "first"),
            address=("address", "first"),
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

    route_to_group = dict(zip(group_assignment_df["route"], group_assignment_df["추천그룹"]))
    updated_store = assignment_store.copy()
    updated_count = 0

    for _, row in route_summary.iterrows():
        route = row["route"]
        truck_request_id = row["truck_request_id"]
        group_name = route_to_group.get(route, "")
        selected_driver = str(group_driver_selection.get(group_name, "")).strip()
        if not selected_driver:
            continue

        assignment_key = make_assignment_key(route, truck_request_id)
        updated_store[assignment_key] = selected_driver
        updated_count += 1

    return updated_store, updated_count


def render_group_driver_assignment_form(
    route_summary: pd.DataFrame,
    drivers,
    assignment_store: dict,
    group_assignment_df: pd.DataFrame,
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
        save_assignment_store(updated_store)
        st.session_state["assignment_store"] = updated_store
        st.success(f"추천그룹 배정을 적용했습니다. ({updated_count}개 route 반영)")
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
            "rank_map": {(str(r.get("route", "")), str(r.get("address_norm", ""))): safe_int(r.get("coord_rank", 0)) for _, r in part.iterrows()},
        }

    return overlap_info_map


def spread_overlapping_marker(lat: float, lon: float, overlap_info: dict, row: pd.Series):
    base_lat, base_lon = lat, lon
    overlap_count = safe_int(overlap_info.get("count", 1))
    overlap_rank = safe_int(overlap_info.get("rank_map", {}).get((str(row.get("route", "")), str(row.get("address_norm", ""))), 0))

    if overlap_count > 1:
        angle = (2 * math.pi * overlap_rank) / overlap_count
        radius = 0.00018
        lat = base_lat + (radius * math.sin(angle))
        lon = base_lon + (radius * math.cos(angle))

    return lat, lon, overlap_count


def render_assignment_form(route_summary: pd.DataFrame, drivers, assignment_store: dict):
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
        save_assignment_store(new_assignment_store)
        st.session_state["assignment_store"] = new_assignment_store
        st.success("기사 배정을 저장했습니다.")
        return new_assignment_store

    return assignment_store


def resolve_group_count(route_feature_df: pd.DataFrame, manual_group_count=None) -> int:
    if len(route_feature_df) == 0:
        return 1

    k = safe_int(manual_group_count)
    if k <= 0:
        k = choose_auto_group_count(route_feature_df)

    return max(1, min(k, len(route_feature_df)))


def build_map_data(result_delivery: pd.DataFrame, grouped_delivery: pd.DataFrame, assignment_df: pd.DataFrame, selected_filter: str):
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

    valid_result = map_result[map_result["coords"].notna()].copy()
    valid_grouped = map_grouped[map_grouped["coords"].notna()].copy()

    return valid_result, valid_grouped, route_driver_map


def render_map(
    valid_result: pd.DataFrame,
    valid_grouped: pd.DataFrame,
    route_prefix_map: dict,
    truck_request_map: dict,
    route_line_label: dict,
    route_driver_map: dict,
    route_camp_map: dict,
    camp_coords: dict,
):
    # 지도 중심
    center_lat, center_lon = 37.55, 126.98

    for camp_code in CAMP_INFO.keys():
        coords = camp_coords.get(camp_code)
        if coords:
            center_lat, center_lon = coords
            break

    if len(valid_result) > 0 and valid_result.iloc[0]["coords"] is not None:
        center_lat = valid_result.iloc[0]["coords"][0]
        center_lon = valid_result.iloc[0]["coords"][1]

    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=10,
        prefer_canvas=True
    )

    # 캠프 레이어 (항상 고정)
    camp_group = folium.FeatureGroup(name="캠프", show=True)

    for camp_code, info in CAMP_INFO.items():
        coords = camp_coords.get(camp_code)
        if not coords:
            continue

        lat, lon = coords
        icon_obj = make_camp_icon(camp_code)

        popup_html = f"""
        <b>캠프:</b> {info['camp_name']}<br>
        <b>코드:</b> {camp_code}<br>
        <b>주소:</b> {info['address']}
        """

        folium.Marker(
            [lat, lon],
            popup=popup_html,
            tooltip=info["camp_name"],
            icon=icon_obj
        ).add_to(camp_group)

    camp_group.add_to(m)

    # 라우트별 색상
    route_list = sorted(
        valid_result["route"].dropna().unique().tolist(),
        key=lambda x: route_prefix_map.get(x, "")
    )

    route_color_map = {
        route: ROUTE_COLORS[i % len(ROUTE_COLORS)]
        for i, route in enumerate(route_list)
    }

    driver_list = [
        d for d in valid_result["assigned_driver"].fillna("").unique().tolist()
        if str(d).strip() != ""
    ]
    driver_color_map = {
        driver: ROUTE_COLORS[(i + len(route_list)) % len(ROUTE_COLORS)]
        for i, driver in enumerate(driver_list)
    }

    overlap_info_map = build_overlap_info_map(valid_grouped)

    for key, part in valid_grouped.assign(
        coord_key=valid_grouped["coords"].apply(
            lambda c: f"{round(float(c[0]), 6)}_{round(float(c[1]), 6)}" if isinstance(c, (tuple, list)) and len(c) == 2 else ""
        )
    ).groupby("coord_key"):
        if key == "":
            continue
        route_names = sorted(part["route"].dropna().astype(str).unique().tolist())
        driver_names = sorted([d for d in part["assigned_driver"].fillna("").astype(str).unique().tolist() if d.strip() != ""])
        overlap_info_map[key]["routes"] = route_names
        overlap_info_map[key]["drivers"] = driver_names

    for route in route_list:
        truck_request_id = truck_request_map.get(route, "")
        camp_code = route_camp_map.get(route, "")
        camp_name = CAMP_INFO.get(camp_code, {}).get("camp_name", "")

        route_group = folium.FeatureGroup(
            name=f"{route_prefix_map.get(route, '')}",
            show=True
        )

        route_df_line = valid_result[
            (valid_result["route"] == route)
        ].sort_values("house_order")

        line_points = []
        route_df_line_house = route_df_line.drop_duplicates(subset=["address_norm"], keep="first")

        for _, row in route_df_line_house.iterrows():
            lat, lon = row["coords"]
            line_points.append([lat, lon])

        route_driver = route_driver_map.get(route, "")
        is_assigned_route = str(route_driver).strip() != ""

        if is_assigned_route:
            line_color = driver_color_map.get(route_driver, "#1e88e5")
            under_weight = 6
            main_weight = 4
            dash_value = "10, 8"
        else:
            line_color = route_color_map.get(route, "#1e88e5")
            under_weight = 10
            main_weight = 7
            dash_value = None

        # 캠프 -> 마지막 배송지 연결선
        camp_coord = camp_coords.get(camp_code)
        if camp_coord and len(line_points) >= 1:
            folium.PolyLine(
                [[camp_coord[0], camp_coord[1]], line_points[-1]],
                color="#444444",
                weight=2,
                opacity=0.7,
                dash_array="4, 6",
                tooltip=f"{route_prefix_map.get(route, '')} 도착센터: {camp_name}"
            ).add_to(route_group)

        # 배송 동선
        if len(line_points) >= 2:
            folium.PolyLine(
                line_points,
                color="#111111",
                weight=under_weight,
                opacity=0.55,
                tooltip=route_line_label.get(route, truck_request_id),
                dash_array=dash_value
            ).add_to(route_group)

            folium.PolyLine(
                line_points,
                color=line_color,
                weight=main_weight,
                opacity=0.95,
                tooltip=route_line_label.get(route, truck_request_id),
                dash_array=dash_value
            ).add_to(route_group)

        route_grouped = valid_grouped[valid_grouped["route"] == route].copy()

        for _, row in route_grouped.iterrows():
            lat, lon = row["coords"]
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
            <b>루트:</b> {row['route']}<br>
            <b>구분:</b> {row.get('route_prefix', '')}<br>
            <b>캠프:</b> {camp_name}<br>
            <b>핀번호:</b> {row.get('pin_label', '')}<br>
            <b>트럭요청ID:</b> {row.get('truck_request_id', '')}<br>
            <b>기사:</b> {row.get('assigned_driver', '')}<br>
            <b>업체ID:</b> {row['company_id']}<br>
            <b>업체명:</b> {row['company_name']}<br>
            <b>주소:</b> {row['address']}<br>
            <b>집순서:</b> {safe_int(row['house_order'])}/{safe_int(row['route_total'])}<br>
            <b>건수:</b> {safe_int(row['stop_count'])}<br>
            <b>물량:</b> {safe_int(row['ae_sum'])}.{safe_int(row['af_sum'])}.{safe_int(row['ag_sum'])}<br>
            {overlap_detail}
            """

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

            tooltip_text = row["hover_text"]
            if overlap_label:
                tooltip_text = f"{tooltip_text} / {overlap_label}"

            folium.Marker(
                [lat, lon],
                popup=popup_html,
                tooltip=tooltip_text,
                icon=icon_obj
            ).add_to(route_group)

        route_group.add_to(m)

    folium.LayerControl(collapsed=False, position="topright").add_to(m)
    return m


def render_group_map(
    valid_result: pd.DataFrame,
    valid_grouped: pd.DataFrame,
    route_prefix_map: dict,
    route_camp_map: dict,
    camp_coords: dict,
    selected_group: str = "전체",
):
    # 추천그룹 확인용 지도
    center_lat, center_lon = 37.55, 126.98

    for camp_code in CAMP_INFO.keys():
        coords = camp_coords.get(camp_code)
        if coords:
            center_lat, center_lon = coords
            break

    if len(valid_result) > 0 and valid_result.iloc[0]["coords"] is not None:
        center_lat = valid_result.iloc[0]["coords"][0]
        center_lon = valid_result.iloc[0]["coords"][1]

    m = folium.Map(location=[center_lat, center_lon], zoom_start=10, prefer_canvas=True)
    OverlappingMarkerSpiderfier(nearby_distance=24, keep_spiderfied=True).add_to(m)

    camp_group = folium.FeatureGroup(name="캠프", show=True)
    for camp_code, info in CAMP_INFO.items():
        coords = camp_coords.get(camp_code)
        if not coords:
            continue

        folium.Marker(
            [coords[0], coords[1]],
            popup=f"<b>캠프:</b> {info['camp_name']}<br><b>코드:</b> {camp_code}<br><b>주소:</b> {info['address']}",
            tooltip=info["camp_name"],
            icon=make_camp_icon(camp_code)
        ).add_to(camp_group)
    camp_group.add_to(m)

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
            <b>추천그룹:</b> {str(row.get('추천그룹', '')).replace('추천그룹 ', '추천그룹')}<br>
            <b>주소:</b> {row.get('address', '')}<br>
            <b>업체명:</b> {row.get('company_name', '')}<br>
            <b>물량(소/중/대):</b> {safe_int(row.get('ae_sum', 0))}/{safe_int(row.get('af_sum', 0))}/{safe_int(row.get('ag_sum', 0))}
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
                popup=popup_html,
                tooltip=marker_tooltip,
                icon=make_stop_div_icon(route_color, str(row.get("pin_label", "")), size_scale=size_scale, border_color=border_color)
            ).add_to(route_group)

        route_group.add_to(m)

    folium.LayerControl(collapsed=False, position="topright").add_to(m)
    return m


# =========================
# 공유 링크로 직접 열기
# =========================
query_params = st.query_params
shared_map = query_params.get("map")

if shared_map:
    payload = load_share_payload(shared_map)

    if payload and payload.get("map_html"):
        result_rows = payload.get("result_delivery_rows", [])
        grouped_rows = payload.get("grouped_delivery_rows", [])

        if result_rows and grouped_rows:
            shared_result_df = _records_to_df_with_coords(result_rows)
            shared_grouped_df = _records_to_df_with_coords(grouped_rows)

            route_prefix_map_payload = payload.get("route_prefix_map", {})
            truck_request_map_payload = payload.get("truck_request_map", {})
            route_line_label_payload = payload.get("route_line_label", {})
            route_camp_map_payload = payload.get("route_camp_map", {})
            camp_coords_payload = payload.get("camp_coords", {})

            st.subheader("대표님 보고용 공유 지도")

            left_col, right_col = st.columns([1.3, 8.7], gap="medium")

            with left_col:
                st.markdown("### 추천그룹 선택")
                group_options = sorted([g for g in shared_result_df["추천그룹"].fillna("").astype(str).unique().tolist() if g.strip() != ""])
                selectable_groups = ["전체"] + group_options if group_options else ["전체"]
                selected_group = st.selectbox("추천그룹", selectable_groups, label_visibility="collapsed")
                shared_view_mode = "추천그룹별 보기" if selected_group != "전체" else "전체 보기"

                filtered_result, filtered_grouped = _filter_shared_view(
                    shared_result_df,
                    shared_grouped_df,
                    mode=shared_view_mode,
                    selected_driver="",
                    selected_group=selected_group if selected_group != "전체" else "",
                )

                route_count, driver_count, group_count, small_box_total, medium_box_total, large_box_total, box_total, overlap_count = _build_shared_summary(filtered_result, filtered_grouped)
                st.markdown("### 핵심 요약")
                st.metric("라우트", route_count)
                st.metric("기사", driver_count)
                st.metric("추천그룹", group_count)
                st.metric("박스 총합", f"{box_total}개")
                st.caption(f"소 {small_box_total} / 중 {medium_box_total} / 대 {large_box_total}")
                st.caption(f"동일위치 겹침 {overlap_count}건")

            route_driver_map = {}
            if len(filtered_result) > 0 and "route" in filtered_result.columns and "assigned_driver" in filtered_result.columns:
                route_driver_map = dict(zip(filtered_result["route"], filtered_result["assigned_driver"]))

            with right_col:
                with st.spinner("공유 지도 생성 중..."):
                    shared_map_obj = render_map(
                        valid_result=filtered_result,
                        valid_grouped=filtered_grouped,
                        route_prefix_map=route_prefix_map_payload,
                        truck_request_map=truck_request_map_payload,
                        route_line_label=route_line_label_payload,
                        route_driver_map=route_driver_map,
                        route_camp_map=route_camp_map_payload,
                        camp_coords=camp_coords_payload,
                    )
                st_folium(shared_map_obj, width=None, height=980)

            st.subheader("기사 할당표")
            assignment_rows = payload.get("assignment_rows", [])
            if assignment_rows:
                assignment_df_payload = pd.DataFrame(assignment_rows)
                if shared_view_mode == "추천그룹별 보기" and selected_group and selected_group != "전체" and "추천그룹" in assignment_df_payload.columns:
                    assignment_df_payload = assignment_df_payload[assignment_df_payload["추천그룹"].fillna("").astype(str) == selected_group].copy()
                st.dataframe(assignment_df_payload, use_container_width=True)

        else:
            st.subheader("공유 지도")
            components.html(payload["map_html"], height=950, scrolling=True)

            assigned_summary_rows = payload.get("assigned_summary_rows", [])
            if assigned_summary_rows:
                st.subheader("기사별 요약")
                st.dataframe(pd.DataFrame(assigned_summary_rows), use_container_width=True)
    else:
        st.error("저장된 공유 데이터를 찾을 수 없습니다. 서버 재시작 등으로 파일이 사라졌을 수 있습니다.")

    st.stop()

# =========================
# 시작
# =========================
drivers = load_drivers()

if "assignment_store" not in st.session_state:
    st.session_state["assignment_store"] = load_assignment_store()

if not uploaded_file:
    st.info("엑셀 파일을 업로드하세요.")
    st.stop()

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

# 추천배정 엔진 (기존 기사배정과 분리)
st.subheader("추천배정 엔진")
group_count_mode = st.radio("추천그룹 수 설정", ["자동", "직접입력"], horizontal=True)
manual_group_count = None

if group_count_mode == "직접입력":
    manual_group_count = st.number_input("추천그룹 수", min_value=1, max_value=max(1, len(route_summary)), value=2, step=1)
else:
    route_feature_df_for_count = build_route_feature_df(route_summary, grouped_delivery)
    auto_group_count = choose_auto_group_count(route_feature_df_for_count)
    st.caption(f"자동 추천그룹 수: {auto_group_count}")

if st.button("추천그룹 자동 추천 실행"):
    route_feature_df = build_route_feature_df(route_summary, grouped_delivery)
    recommended_group_count = resolve_group_count(route_feature_df, manual_group_count=manual_group_count)
    recommended_group_map = recommend_route_groups(route_feature_df, manual_group_count=manual_group_count)
    st.session_state["recommended_group_map"] = recommended_group_map
    st.session_state["recommended_group_count"] = recommended_group_count
    st.success("추천그룹 생성을 완료했습니다.")

if "recommended_group_map" in st.session_state:
    route_feature_df = build_route_feature_df(route_summary, grouped_delivery)
    recommended_group_map = st.session_state["recommended_group_map"]
    group_assignment_df = build_group_assignment_df(route_feature_df, recommended_group_map)
    group_summary_df = build_group_summary_df(group_assignment_df)

    st.subheader("추천그룹 요약")
    st.dataframe(group_summary_df, use_container_width=True)

    with st.expander("추천그룹 수정", expanded=False):
        edit_map = default_group_edit_map(group_assignment_df)
        recommended_group_count = safe_int(st.session_state.get("recommended_group_count", 0))
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
            updated_assignment_df = apply_group_edit_map(route_feature_df, new_group_map)
            st.session_state["recommended_group_map"] = default_group_edit_map(updated_assignment_df)
            st.success("추천그룹 수동 수정을 반영했습니다.")

    final_group_map = st.session_state["recommended_group_map"]
    group_map_result, group_map_grouped = build_group_map_data(result_delivery, grouped_delivery, final_group_map)

    latest_assignment_df = build_group_assignment_df(route_feature_df, final_group_map)
    st.session_state["latest_group_assignment_df"] = latest_assignment_df.copy()
    group_detail_df = build_group_detail_stats_df(latest_assignment_df)

    st.subheader("추천그룹 지도")
    selectable_groups = ["전체"] + group_detail_df["추천그룹"].astype(str).tolist()
    selected_group_filter = st.selectbox("추천그룹 선택", selectable_groups, key="selected_group_filter")

    if selected_group_filter == "전체":
        selected_info_df = group_detail_df.copy()
        route_count = safe_int(selected_info_df["라우트개수"].sum())
        box_total = safe_int(selected_info_df["박스총개수"].sum())
        small_total = safe_int(selected_info_df["소형합"].sum())
        medium_total = safe_int(selected_info_df["중형합"].sum())
        large_total = safe_int(selected_info_df["대형합"].sum())
    else:
        selected_info_df = group_detail_df[group_detail_df["추천그룹"] == selected_group_filter]
        if len(selected_info_df) == 0:
            route_count = box_total = small_total = medium_total = large_total = 0
        else:
            row = selected_info_df.iloc[0]
            route_count = safe_int(row["라우트개수"])
            box_total = safe_int(row["박스총개수"])
            small_total = safe_int(row["소형합"])
            medium_total = safe_int(row["중형합"])
            large_total = safe_int(row["대형합"])

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("라우트개수", route_count)
    m2.metric("박스총개수", box_total)
    m3.metric("소형", small_total)
    m4.metric("중형", medium_total)
    m5.metric("대형", large_total)

    st.caption("추천그룹 지도와 지표는 선택한 그룹 기준으로 표시됩니다.")

    group_map_view = render_group_map(
        valid_result=group_map_result,
        valid_grouped=group_map_grouped,
        route_prefix_map=route_prefix_map,
        route_camp_map=route_camp_map,
        camp_coords=camp_coords,
        selected_group=selected_group_filter,
    )
    st_folium(group_map_view, width=None, height=700)

    with st.expander("기사 선호 예상 순위 (참고용)", expanded=False):
        st.caption("추천그룹별 물량·스톱·예상시간·퍼짐 기준의 휴리스틱 참고 순위입니다.")
        preference_df = build_driver_preference_df(route_feature_df, final_group_map)
        st.dataframe(
            preference_df.assign(
                선호예상점수=lambda df: df["선호예상점수"].round(1),
                route_spread_km=lambda df: df["route_spread_km"].round(1),
            ).rename(columns={"route_spread_km": "그룹평균퍼짐km"}),
            use_container_width=True,
        )

st.subheader("지도")

assignment_store = st.session_state["assignment_store"]
latest_group_assignment_df = st.session_state.get("latest_group_assignment_df", pd.DataFrame())
if len(latest_group_assignment_df) > 0:
    assignment_store = render_group_driver_assignment_form(
        route_summary=route_summary,
        drivers=drivers,
        assignment_store=assignment_store,
        group_assignment_df=latest_group_assignment_df,
    )

assignment_store = render_assignment_form(route_summary, drivers, assignment_store)
st.session_state["assignment_store"] = assignment_store

assignment_df = build_assignment_df(route_summary, assignment_store)

group_route_map = {}
if len(latest_group_assignment_df) > 0 and "route" in latest_group_assignment_df.columns and "추천그룹" in latest_group_assignment_df.columns:
    group_route_map = dict(zip(latest_group_assignment_df["route"], latest_group_assignment_df["추천그룹"]))

st.subheader("기사별 필터")
driver_filter_options = ["전체", "미배정"] + drivers
selected_filter = st.selectbox("지도 표시 대상", driver_filter_options)

valid_result, valid_grouped, route_driver_map = build_map_data(
    result_delivery=result_delivery,
    grouped_delivery=grouped_delivery,
    assignment_df=assignment_df,
    selected_filter=selected_filter
)

st.write(f"캐시 주소 수: {len(cache)}")
st.write(f"현재 필터: {selected_filter}")
st.caption("캠프는 고정핀(검정색), 배송지는 라우트/기사 상태에 따라 표시됩니다.")

with st.spinner("지도 생성 중..."):
    m = render_map(
        valid_result=valid_result,
        valid_grouped=valid_grouped,
        route_prefix_map=route_prefix_map,
        truck_request_map=truck_request_map,
        route_line_label=route_line_label,
        route_driver_map=route_driver_map,
        route_camp_map=route_camp_map,
        camp_coords=camp_coords,
    )

marker_count = len(valid_grouped)
st.write(f"지도에 찍힌 배송 핀 수: {marker_count}")

st_folium(m, width=None, height=900)

map_html = m.get_root().render()

map_path = os.path.join(MAP_DIR, html_filename)
with open(map_path, "w", encoding="utf-8") as f:
    f.write(map_html)

st.download_button(
    label=f"지도 다운로드 (HTML) - {html_filename}",
    data=map_html,
    file_name=html_filename,
    mime="text/html"
)

st.subheader("기사 할당표")
view_assignment_df = assignment_df.copy()
if group_route_map:
    view_assignment_df["추천그룹"] = view_assignment_df["route"].map(group_route_map).fillna("")
if "총걸린분" in view_assignment_df.columns:
    view_assignment_df = view_assignment_df.drop(columns=["총걸린분"])
st.dataframe(view_assignment_df, use_container_width=True)

assigned_summary = build_assigned_summary(assignment_df)

if len(assigned_summary) == 0:
    st.info("아직 기사 배정이 없습니다.")
    view_assigned_summary = pd.DataFrame()
else:
    st.subheader("기사별 요약")
    view_assigned_summary = assigned_summary.copy()
    if "총걸린분" in view_assigned_summary.columns:
        view_assigned_summary = view_assigned_summary.drop(columns=["총걸린분"])
    st.dataframe(view_assigned_summary, use_container_width=True)

shared_result_delivery = result_delivery.copy()
shared_grouped_delivery = grouped_delivery.copy()
shared_result_delivery["assigned_driver"] = shared_result_delivery["route"].map(route_driver_map).fillna("")
shared_grouped_delivery["assigned_driver"] = shared_grouped_delivery["route"].map(route_driver_map).fillna("")
shared_result_delivery["추천그룹"] = shared_result_delivery["route"].map(group_route_map).fillna("")
shared_grouped_delivery["추천그룹"] = shared_grouped_delivery["route"].map(group_route_map).fillna("")

save_share_payload(
    share_name,
    map_html,
    view_assignment_df,
    view_assigned_summary,
    result_delivery_df=shared_result_delivery,
    grouped_delivery_df=shared_grouped_delivery,
    route_prefix_map=route_prefix_map,
    truck_request_map=truck_request_map,
    route_line_label=route_line_label,
    route_camp_map=route_camp_map,
    camp_coords=camp_coords,
    group_assignment_df=latest_group_assignment_df,
)

share_url = f"{APP_URL}?map={share_name}"

st.subheader("지도 공유 링크")
st.success("아래 링크를 복사해서 바로 공유하시면 됩니다.")
st.markdown(f"### [🔗 지도 + 기사할당표 바로 열기]({share_url})")
st.text_input("공유 URL", value=share_url, key="share_url_box")

csv_data = assignment_df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
st.download_button(
    "기사 배정표 CSV 다운로드",
    data=csv_data,
    file_name="route_assignment.csv",
    mime="text/csv"
)
