import os
import re
import json
import requests
import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import folium
from folium.features import DivIcon
from streamlit_folium import st_folium

st.set_page_config(page_title="배차 지도", layout="wide")
st.title("배차 지도")

# =========================
# 설정값
# =========================
KAKAO_API_KEY = "080375c5f09bbb8c7db9f368bc752d33"
TIME_COL_INDEX = 21   # V열
CACHE_FILE = "geocode_cache.csv"
DRIVER_FILE = "drivers.csv"
MAP_DIR = "saved_maps"
SHARE_DIR = "shared_payloads"
ASSIGNMENT_FILE = "route_assignments.json"
APP_URL = "https://dispatch-map.streamlit.app"  # 본인 배포 주소로 수정

os.makedirs(MAP_DIR, exist_ok=True)
os.makedirs(SHARE_DIR, exist_ok=True)

uploaded_file = st.file_uploader("엑셀 파일 업로드", type=["xlsx"])

ROUTE_COLORS = [
    "#e53935",
    "#1e88e5",
    "#43a047",
    "#8e24aa",
    "#fb8c00",
    "#3949ab",
    "#00897b",
    "#6d4c41",
    "#546e7a",
    "#d81b60",
    "#5e35b1",
    "#039be5",
    "#7cb342",
    "#f4511e",
    "#00acc1",
    "#c0ca33",
]

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

def extract_date_from_filename(filename: str) -> str:
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", str(filename))
    if m:
        return f"{m.group(1)}{m.group(2)}{m.group(3)}"
    return "dispatch_map"

def extract_base_name(filename: str) -> str:
    name = os.path.splitext(str(filename))[0]
    name = re.sub(r"[^\w\-가-힣]+", "_", name)
    return name.strip("_") or "dispatch_map"

def load_drivers():
    if os.path.exists(DRIVER_FILE):
        df = pd.read_csv(DRIVER_FILE)
        if "driver_name" in df.columns:
            return df["driver_name"].dropna().astype(str).tolist()
    return []

def load_geocode_cache():
    if os.path.exists(CACHE_FILE):
        df = pd.read_csv(CACHE_FILE, dtype=str)
        cache = {}
        for _, row in df.iterrows():
            addr = str(row["address"]).strip()
            lat = row["lat"]
            lon = row["lon"]
            if addr and lat and lon and lat != "nan" and lon != "nan":
                cache[addr] = (float(lat), float(lon))
        return cache
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
        save_geocode_cache(cache)
        return coords
    except Exception:
        return None

def extract_camp_number(name: str) -> str:
    text = str(name)
    m = re.search(r"(\d+)$", text)
    if m:
        return m.group(1)
    m = re.search(r"(\d+)", text)
    if m:
        return m.group(1)
    return "C"

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

def make_camp_div_icon(number_text: str):
    html = f"""
    <div style="
        width:24px;
        height:24px;
        border-radius:4px;
        background:#111111;
        border:2px solid #ffffff;
        color:#ffffff;
        text-align:center;
        line-height:20px;
        font-size:12px;
        font-weight:700;
        box-shadow:0 0 3px rgba(0,0,0,0.45);
    ">{number_text}</div>
    """
    return DivIcon(html=html, icon_size=(24, 24), icon_anchor=(12, 12))

def make_stop_div_icon(route_color: str, stop_text: str):
    font_size = "11px" if len(str(stop_text)) <= 2 else "9px"
    html = f"""
    <div style="
        width:28px;
        height:28px;
        border-radius:50%;
        background:{route_color};
        border:2px solid #ffffff;
        color:#ffffff;
        text-align:center;
        line-height:24px;
        font-size:{font_size};
        font-weight:700;
        box-shadow:0 0 3px rgba(0,0,0,0.45);
    ">{stop_text}</div>
    """
    return DivIcon(html=html, icon_size=(28, 28), icon_anchor=(14, 14))

def make_assigned_square_icon(route_color: str, stop_text: str):
    font_size = "11px" if len(str(stop_text)) <= 2 else "9px"
    html = f"""
    <div style="
        width:30px;
        height:30px;
        background:{route_color};
        border:2px solid #ffffff;
        border-radius:6px;
        color:#ffffff;
        text-align:center;
        line-height:26px;
        font-size:{font_size};
        font-weight:700;
        box-shadow:0 0 3px rgba(0,0,0,0.45);
    ">{stop_text}</div>
    """
    return DivIcon(html=html, icon_size=(30, 30), icon_anchor=(15, 15))

def make_assigned_square_camp_icon(route_color: str, text_value: str):
    html = f"""
    <div style="
        width:28px;
        height:28px;
        background:{route_color};
        border:2px solid #ffffff;
        border-radius:6px;
        color:#ffffff;
        text-align:center;
        line-height:24px;
        font-size:10px;
        font-weight:700;
        box-shadow:0 0 3px rgba(0,0,0,0.45);
    ">{text_value}</div>
    """
    return DivIcon(html=html, icon_size=(28, 28), icon_anchor=(14, 14))

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

def get_camp_icon_by_name(camp_name: str, color: str):
    camp_text = str(camp_name).strip()

    if "일산1캠" in camp_text:
        return make_square_div_icon(color, "1")
    elif "일산7캠" in camp_text:
        return make_diamond_div_icon(color, "7")
    else:
        camp_no = extract_camp_number(camp_text)
        return make_camp_div_icon(camp_no)

def get_assigned_camp_icon_by_name(camp_name: str, color: str):
    camp_text = str(camp_name).strip()

    if "일산1캠" in camp_text:
        return make_square_div_icon(color, "1")
    elif "일산7캠" in camp_text:
        return make_diamond_div_icon(color, "7")
    else:
        camp_no = extract_camp_number(camp_text)
        return make_assigned_square_camp_icon(color, camp_no)

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

def save_share_payload(share_name: str, map_html: str, assignment_df: pd.DataFrame, assigned_summary: pd.DataFrame):
    payload_path = os.path.join(SHARE_DIR, f"{share_name}.json")
    payload = {
        "map_html": map_html,
        "assignment_rows": assignment_df.fillna("").to_dict(orient="records"),
        "assigned_summary_rows": assigned_summary.fillna("").to_dict(orient="records"),
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

# =========================
# 공유 링크로 직접 열기
# =========================
query_params = st.query_params
shared_map = query_params.get("map")

if shared_map:
    payload = load_share_payload(shared_map)

    if payload and payload.get("map_html"):
        st.subheader("공유 지도")
        components.html(payload["map_html"], height=950, scrolling=True)

        assignment_rows = payload.get("assignment_rows", [])
        if assignment_rows:
            st.subheader("기사 할당표")
            st.dataframe(pd.DataFrame(assignment_rows), use_container_width=True)

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

if not uploaded_file:
    st.info("엑셀 파일을 업로드하세요.")

if uploaded_file:
    uploaded_filename = uploaded_file.name
    base_name = extract_base_name(uploaded_filename)
    share_name = base_name
    html_filename = f"{share_name}.html"

    cache = load_geocode_cache()
    assignment_store = load_assignment_store()

    df = pd.read_excel(uploaded_file, sheet_name="일반차량")

    current_route = None
    current_truck_request_id = None
    stop_order = 0
    parsed = []

    for _, row in df.iterrows():
        route_val = row.iloc[0]          # A열
        time_val = row.iloc[TIME_COL_INDEX]
        truck_request_id = row.iloc[2]   # C열
        company_id = row.iloc[22]        # W열
        company_name = row.iloc[23]      # X열
        address = row.iloc[24]           # Y열
        ae = row.iloc[30]                # AE열
        af = row.iloc[31]                # AF열
        ag = row.iloc[32]                # AG열

        if pd.notna(route_val):
            current_route = str(route_val).strip()
            current_truck_request_id = str(truck_request_id).strip() if pd.notna(truck_request_id) else ""
            stop_order = 1
        else:
            stop_order += 1

        if pd.notna(address) and str(address).strip():
            company_id_str = str(company_id).strip() if pd.notna(company_id) else ""
            company_name_str = str(company_name).strip() if pd.notna(company_name) else ""
            time_str = format_time_value(time_val)

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
                "is_camp": company_id_str == ""
            })

    result = pd.DataFrame(parsed)

    if len(result) == 0:
        st.warning("유효한 주소 데이터가 없습니다.")
        st.stop()

    result["ae"] = pd.to_numeric(result["ae"], errors="coerce").fillna(0)
    result["af"] = pd.to_numeric(result["af"], errors="coerce").fillna(0)
    result["ag"] = pd.to_numeric(result["ag"], errors="coerce").fillna(0)

    # 같은 route 안에서 같은 주소는 같은 집으로 처리
    house_order_df = (
        result.groupby(["route", "address_norm"], as_index=False)
        .agg(first_stop=("stop_order", "min"))
        .sort_values(["route", "first_stop", "address_norm"])
        .reset_index(drop=True)
    )
    house_order_df["house_order"] = house_order_df.groupby("route").cumcount() + 1

    result = result.merge(
        house_order_df[["route", "address_norm", "house_order"]],
        on=["route", "address_norm"],
        how="left"
    )

    all_routes_sorted = sorted(result["route"].dropna().astype(str).unique().tolist())
    route_prefix_map = {
        route: route_index_to_label(i + 1)
        for i, route in enumerate(all_routes_sorted)
    }

    result["route_prefix"] = result["route"].map(route_prefix_map)
    result["pin_label"] = result.apply(
        lambda r: f"{r['route_prefix']}{safe_int(r['house_order'])}",
        axis=1
    )

    route_total_map = result.groupby("route")["house_order"].max().to_dict()
    truck_request_map = result.groupby("route")["truck_request_id"].first().to_dict()

    route_qty_map = (
        result.groupby("route", as_index=True)
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
        route_line_label[route] = f"{prefix} | {total}박스 | {small}/{mid}/{large}"

    normal_result = result[result["is_camp"] == False].copy()
    camp_result = result[result["is_camp"] == True].copy()

    grouped_normal = (
        normal_result.groupby(["route", "address_norm"], as_index=False)
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
            is_camp=("is_camp", "first"),
        )
        .sort_values(["route", "house_order"])
        .reset_index(drop=True)
    )

    if len(grouped_normal) > 0:
        grouped_normal["route_total"] = grouped_normal["route"].map(route_total_map)
        grouped_normal["label"] = grouped_normal.apply(
            lambda r: f"{r['pin_label']} - {safe_int(r['ae_sum'])}.{safe_int(r['af_sum'])}.{safe_int(r['ag_sum'])}",
            axis=1
        )
        grouped_normal["hover_text"] = grouped_normal.apply(
            lambda r: f"{r['pin_label']} / {r['first_time']} / {safe_int(r['ae_sum'])}/{safe_int(r['af_sum'])}/{safe_int(r['ag_sum'])}".strip(),
            axis=1
        )
    else:
        grouped_normal["route_total"] = 0
        grouped_normal["label"] = ""
        grouped_normal["hover_text"] = ""

    camp_markers = camp_result.copy()
    if len(camp_markers) > 0:
        camp_markers["stop_count"] = 1
        camp_markers["first_stop"] = camp_markers["stop_order"]
        camp_markers["first_time"] = camp_markers["time_str"]
        camp_markers["ae_sum"] = camp_markers["ae"]
        camp_markers["af_sum"] = camp_markers["af"]
        camp_markers["ag_sum"] = camp_markers["ag"]
        camp_markers["route_total"] = camp_markers["route"].map(route_total_map)
        camp_markers["route_prefix"] = camp_markers["route"].map(route_prefix_map)
        camp_markers["pin_label"] = camp_markers.apply(
            lambda r: f"{r['route_prefix']}{safe_int(r['house_order'])}",
            axis=1
        )
        camp_markers["label"] = camp_markers.apply(
            lambda r: f"{r['pin_label']} - CAMP",
            axis=1
        )
        camp_markers["hover_text"] = camp_markers.apply(
            lambda r: f"{r['pin_label']} / {r['first_time']} / CAMP".strip(),
            axis=1
        )
    else:
        camp_markers["route_total"] = 0
        camp_markers["route_prefix"] = ""
        camp_markers["pin_label"] = ""
        camp_markers["label"] = ""
        camp_markers["hover_text"] = ""

    grouped = pd.concat([grouped_normal, camp_markers], ignore_index=True, sort=False)
    grouped = grouped.sort_values(["route", "house_order"]).reset_index(drop=True)

    result["coords"] = result["address"].apply(lambda x: geocode_kakao(x, KAKAO_API_KEY, cache))
    grouped["coords"] = grouped["address"].apply(lambda x: geocode_kakao(x, KAKAO_API_KEY, cache))

    route_time_summary = (
        result.groupby(["route"], as_index=False)
        .agg(
            start_min=("time_minutes", "min"),
            end_max=("time_minutes", "max")
        )
    )

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

    route_stop_count = (
        result.groupby("route", as_index=False)["address_norm"]
        .nunique()
        .rename(columns={"address_norm": "스톱수"})
    )

    route_summary = (
        result.groupby(["route", "truck_request_id"], as_index=False)
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

    route_summary["시작시간"] = route_summary["start_min"].apply(min_to_hhmm)
    route_summary["종료시간"] = route_summary["end_max"].apply(min_to_hhmm)
    route_summary["총걸린분"] = (
        route_summary["end_max"].fillna(0) - route_summary["start_min"].fillna(0)
    ).clip(lower=0)
    route_summary["총걸린시간"] = route_summary["총걸린분"].apply(minutes_to_korean_text)

    st.subheader("지도")

    st.subheader("기사 배정")
    st.caption("route / 구분 / truck_request_id / 스톱 / 시작시간 / 종료시간 / 소형 / 중형 / 대형 / 총합 / 기사")

    assignment_rows = []

    for _, row in route_summary.iterrows():
        route = row["route"]
        truck_request_id = row["truck_request_id"]

        c1, c2, c3, c4, c5, c6, c7, c8, c9, c10, c11 = st.columns([0.8, 0.7, 1.2, 0.7, 0.9, 0.9, 0.7, 0.7, 0.7, 0.8, 1.4])
        c1.write(str(route))
        c2.write(str(row["route_prefix"]))
        c3.write(str(truck_request_id))
        c4.write(safe_int(row["스톱수"]))
        c5.write(str(row["시작시간"]))
        c6.write(str(row["종료시간"]))
        c7.write(safe_int(row["소형합"]))
        c8.write(safe_int(row["중형합"]))
        c9.write(safe_int(row["대형합"]))
        c10.write(safe_int(row["총합"]))

        driver_options = [""] + drivers
        assignment_key = make_assignment_key(route, truck_request_id)
        saved_driver = assignment_store.get(assignment_key, "")

        default_index = 0
        if saved_driver in driver_options:
            default_index = driver_options.index(saved_driver)

        selected_driver = c11.selectbox(
            f"기사선택_{route}_{truck_request_id}",
            options=driver_options,
            index=default_index,
            label_visibility="collapsed",
            key=f"driver_select_{route}_{truck_request_id}"
        )

        assignment_store[assignment_key] = selected_driver

        assignment_rows.append({
            "route": route,
            "route_prefix": row["route_prefix"],
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

    save_assignment_store(assignment_store)

    assignment_df = pd.DataFrame(assignment_rows)
    route_driver_map = dict(zip(assignment_df["route"], assignment_df["assigned_driver"]))

    result["assigned_driver"] = result["route"].map(route_driver_map)
    grouped["assigned_driver"] = grouped["route"].map(route_driver_map)

    st.subheader("기사별 필터")
    driver_filter_options = ["전체", "미배정"] + drivers
    selected_filter = st.selectbox("지도 표시 대상", driver_filter_options)

    if selected_filter == "전체":
        map_result = result.copy()
        map_grouped = grouped.copy()
    elif selected_filter == "미배정":
        map_result = result[result["assigned_driver"].fillna("").astype(str).str.strip() == ""].copy()
        map_grouped = grouped[grouped["assigned_driver"].fillna("").astype(str).str.strip() == ""].copy()
    else:
        map_result = result[result["assigned_driver"] == selected_filter].copy()
        map_grouped = grouped[grouped["assigned_driver"] == selected_filter].copy()

    valid_result = map_result[map_result["coords"].notna()].copy()
    valid_grouped = map_grouped[map_grouped["coords"].notna()].copy()

    st.write(f"캐시 주소 수: {len(cache)}")
    st.write(f"현재 필터: {selected_filter}")
    st.caption("미할당: 루트별 색 + 원형핀 + 굵은 실선 / 할당됨: 기사별 같은 색 + 네모핀 + 얇은 점선 / 핀번호: 미할당은 A1, 할당은 기사명 뒤 2글자")

    if len(valid_result) > 0:
        center_lat = valid_result.iloc[0]["coords"][0]
        center_lon = valid_result.iloc[0]["coords"][1]
    else:
        center_lat, center_lon = 37.55, 126.98

    m = folium.Map(location=[center_lat, center_lon], zoom_start=10)

    route_list = list(valid_result["route"].dropna().unique())
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

    for route in route_list:
        truck_request_id = truck_request_map.get(route, "")

        route_group = folium.FeatureGroup(
            name=f"{route} | {truck_request_id}",
            show=True
        )

        route_df_line = map_result[
            (map_result["route"] == route) &
            (map_result["is_camp"] == False) &
            (map_result["coords"].notna())
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
        else:
            line_color = route_color_map.get(route, "#1e88e5")
            under_weight = 10
            main_weight = 7

        if len(line_points) >= 2:
            folium.PolyLine(
                line_points,
                color="#111111",
                weight=under_weight,
                opacity=0.55,
                tooltip=route_line_label.get(route, truck_request_id),
                dash_array="10, 8" if is_assigned_route else None
            ).add_to(route_group)

            folium.PolyLine(
                line_points,
                color=line_color,
                weight=main_weight,
                opacity=0.95,
                tooltip=route_line_label.get(route, truck_request_id),
                dash_array="10, 8" if is_assigned_route else None
            ).add_to(route_group)

        route_grouped = valid_grouped[valid_grouped["route"] == route].copy()

        for _, row in route_grouped.iterrows():
            lat, lon = row["coords"]
            driver_name = row.get("assigned_driver", "")
            is_assigned_pin = str(driver_name).strip() != ""

            if is_assigned_pin:
                pin_color = driver_color_map.get(driver_name, "#1e88e5")
            else:
                pin_color = route_color_map.get(route, "#1e88e5")

            is_camp = bool(row["is_camp"])

            if is_camp:
                popup_html = f"""
                <b>루트:</b> {row['route']}<br>
                <b>구분:</b> {row.get('route_prefix', '')}<br>
                <b>핀번호:</b> {row.get('pin_label', '')}<br>
                <b>트럭요청ID:</b> {row.get('truck_request_id', '')}<br>
                <b>기사:</b> {row.get('assigned_driver', '')}<br>
                <b>캠프명:</b> {row['company_name']}<br>
                <b>주소:</b> {row['address']}<br>
                <b>집순서:</b> {safe_int(row['house_order'])}/{safe_int(row['route_total'])}
                """

                if is_assigned_pin:
                    icon_obj = get_assigned_camp_icon_by_name(row["company_name"], pin_color)
                else:
                    icon_obj = get_camp_icon_by_name(row["company_name"], pin_color)

                folium.Marker(
                    [lat, lon],
                    popup=popup_html,
                    tooltip=row["hover_text"],
                    icon=icon_obj
                ).add_to(route_group)

            else:
                popup_html = f"""
                <b>루트:</b> {row['route']}<br>
                <b>구분:</b> {row.get('route_prefix', '')}<br>
                <b>핀번호:</b> {row.get('pin_label', '')}<br>
                <b>트럭요청ID:</b> {row.get('truck_request_id', '')}<br>
                <b>기사:</b> {row.get('assigned_driver', '')}<br>
                <b>업체ID:</b> {row['company_id']}<br>
                <b>업체명:</b> {row['company_name']}<br>
                <b>주소:</b> {row['address']}<br>
                <b>집순서:</b> {safe_int(row['house_order'])}/{safe_int(row['route_total'])}<br>
                <b>건수:</b> {safe_int(row['stop_count'])}<br>
                <b>물량:</b> {safe_int(row['ae_sum'])}.{safe_int(row['af_sum'])}.{safe_int(row['ag_sum'])}
                """

                if is_assigned_pin:
                    pin_text = short_driver_name(driver_name)
                    icon_obj = make_assigned_square_icon(pin_color, pin_text)
                else:
                    pin_text = str(row.get("pin_label", ""))
                    icon_obj = make_stop_div_icon(pin_color, pin_text)

                folium.Marker(
                    [lat, lon],
                    popup=popup_html,
                    tooltip=row["hover_text"],
                    icon=icon_obj
                ).add_to(route_group)

        route_group.add_to(m)

    folium.LayerControl(collapsed=False, position="topright").add_to(m)

    marker_count = len(valid_grouped)
    st.write(f"지도에 찍힌 핀 수: {marker_count}")

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
    if "총걸린분" in view_assignment_df.columns:
        view_assignment_df = view_assignment_df.drop(columns=["총걸린분"])
    st.dataframe(view_assignment_df, use_container_width=True)

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

    assigned_summary["총걸린시간"] = assigned_summary["총걸린분"].apply(minutes_to_korean_text)

    if len(assigned_summary) == 0:
        st.info("아직 기사 배정이 없습니다.")
    else:
        st.subheader("기사별 요약")
        view_assigned_summary = assigned_summary.copy()
        if "총걸린분" in view_assigned_summary.columns:
            view_assigned_summary = view_assigned_summary.drop(columns=["총걸린분"])
        st.dataframe(view_assigned_summary, use_container_width=True)

    save_share_payload(share_name, map_html, view_assignment_df, view_assigned_summary if len(assigned_summary) > 0 else pd.DataFrame())

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
