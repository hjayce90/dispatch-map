import os
import re
import math
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
APP_URL = "https://dispatch-map.streamlit.app"  # 본인 배포 주소

os.makedirs(MAP_DIR, exist_ok=True)

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
# 공유 링크로 직접 지도 열기
# =========================
query_params = st.query_params
shared_map = query_params.get("map")

if shared_map:
    shared_map_path = os.path.join(MAP_DIR, shared_map)

    if os.path.exists(shared_map_path):
        with open(shared_map_path, "r", encoding="utf-8") as f:
            shared_html = f.read()
        components.html(shared_html, height=1000, scrolling=True)
    else:
        st.error("저장된 공유 지도를 찾을 수 없습니다. 서버 재시작 등으로 파일이 사라졌을 수 있습니다.")

    st.stop()

# =========================
# 공통 함수
# =========================
def extract_date_from_filename(filename: str) -> str:
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", str(filename))
    if m:
        return f"{m.group(1)}{m.group(2)}{m.group(3)}"
    return "dispatch_map"

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
    html = f"""
    <div style="
        width:24px;
        height:24px;
        border-radius:50%;
        background:{route_color};
        border:2px solid #ffffff;
        color:#ffffff;
        text-align:center;
        line-height:20px;
        font-size:11px;
        font-weight:700;
        box-shadow:0 0 3px rgba(0,0,0,0.45);
    ">{stop_text}</div>
    """
    return DivIcon(html=html, icon_size=(24, 24), icon_anchor=(12, 12))

def make_assigned_triangle_icon(route_color: str, stop_text: str):
    html = f"""
    <div style="
        position: relative;
        width: 0;
        height: 0;
        border-left: 14px solid transparent;
        border-right: 14px solid transparent;
        border-bottom: 26px solid {route_color};
        filter: drop-shadow(0 0 3px rgba(0,0,0,0.45));
    ">
        <div style="
            position:absolute;
            top:7px;
            left:-8px;
            width:16px;
            text-align:center;
            color:#ffffff;
            font-size:10px;
            font-weight:700;
            line-height:1;
        ">{stop_text}</div>
    </div>
    """
    return DivIcon(html=html, icon_size=(28, 26), icon_anchor=(14, 22))

def make_assigned_triangle_camp_icon(route_color: str, number_text: str):
    html = f"""
    <div style="
        position: relative;
        width: 0;
        height: 0;
        border-left: 14px solid transparent;
        border-right: 14px solid transparent;
        border-bottom: 26px solid {route_color};
        filter: drop-shadow(0 0 3px rgba(0,0,0,0.45));
    ">
        <div style="
            position:absolute;
            top:7px;
            left:-8px;
            width:16px;
            text-align:center;
            color:#ffffff;
            font-size:10px;
            font-weight:700;
            line-height:1;
        ">{number_text}</div>
    </div>
    """
    return DivIcon(html=html, icon_size=(28, 26), icon_anchor=(14, 22))

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

def safe_int(v):
    try:
        if pd.isna(v):
            return 0
        return int(float(v))
    except Exception:
        return 0

# =========================
# 시작
# =========================
drivers = load_drivers()

if not uploaded_file:
    st.info("엑셀 파일을 업로드하세요.")

if uploaded_file:
    uploaded_filename = uploaded_file.name
    file_date = extract_date_from_filename(uploaded_filename)
    html_filename = f"{file_date}.html"

    cache = load_geocode_cache()

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
                "company_id": company_id_str,
                "company_name": company_name_str,
                "address": str(address).strip(),
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

    route_total_map = result.groupby("route")["stop_order"].max().to_dict()
    truck_request_map = result.groupby("route")["truck_request_id"].first().to_dict()

    route_qty_map = (
        result.groupby("route", as_index=True)
        .agg(
            ae_sum=("ae", "sum"),
            af_sum=("af", "sum"),
            ag_sum=("ag", "sum"),
        )
    )

    route_line_label = {
        route: f"{truck_request_map.get(route, '')} {safe_int(row['ae_sum'])}/{safe_int(row['af_sum'])}/{safe_int(row['ag_sum'])}"
        for route, row in route_qty_map.iterrows()
    }

    normal_result = result[result["is_camp"] == False].copy()
    camp_result = result[result["is_camp"] == True].copy()

    grouped_normal = (
        normal_result.groupby(["route", "company_id"], as_index=False)
        .agg(
            truck_request_id=("truck_request_id", "first"),
            company_name=("company_name", "first"),
            address=("address", "first"),
            stop_count=("stop_order", "count"),
            first_stop=("stop_order", "min"),
            first_time=("time_str", "first"),
            ae_sum=("ae", "sum"),
            af_sum=("af", "sum"),
            ag_sum=("ag", "sum"),
            is_camp=("is_camp", "first"),
        )
        .sort_values(["route", "first_stop"])
        .reset_index(drop=True)
    )

    if len(grouped_normal) > 0:
        grouped_normal["route_total"] = grouped_normal["route"].map(route_total_map)
        grouped_normal["label"] = grouped_normal.apply(
            lambda r: f"{safe_int(r['first_stop'])}/{safe_int(r['route_total'])} - {safe_int(r['ae_sum'])}.{safe_int(r['af_sum'])}.{safe_int(r['ag_sum'])}",
            axis=1
        )
        grouped_normal["hover_text"] = grouped_normal.apply(
            lambda r: f"{r['first_time']} {safe_int(r['ae_sum'])}/{safe_int(r['af_sum'])}/{safe_int(r['ag_sum'])}".strip(),
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
        camp_markers["label"] = camp_markers.apply(
            lambda r: f"{safe_int(r['first_stop'])}/{safe_int(r['route_total'])} - CAMP",
            axis=1
        )
        camp_markers["hover_text"] = camp_markers.apply(
            lambda r: f"{r['first_time']} CAMP".strip(),
            axis=1
        )
    else:
        camp_markers["route_total"] = 0
        camp_markers["label"] = ""
        camp_markers["hover_text"] = ""

    grouped = pd.concat([grouped_normal, camp_markers], ignore_index=True, sort=False)
    grouped = grouped.sort_values(["route", "first_stop"]).reset_index(drop=True)

    # 좌표 변환
    result["coords"] = result["address"].apply(lambda x: geocode_kakao(x, KAKAO_API_KEY, cache))
    grouped["coords"] = grouped["address"].apply(lambda x: geocode_kakao(x, KAKAO_API_KEY, cache))

    route_summary = (
        result.groupby(["route", "truck_request_id"], as_index=False)
        .agg(
            총정차수=("address", "count"),
            소형합=("ae", "sum"),
            중형합=("af", "sum"),
            대형합=("ag", "sum"),
        )
        .sort_values("route")
        .reset_index(drop=True)
    )
    route_summary["총합"] = route_summary["소형합"] + route_summary["중형합"] + route_summary["대형합"]

    # =========================
    # 기사 배정
    # =========================
    st.subheader("기사 배정")
    st.caption("route / truck_request_id / 총정차수 / 소형 / 중형 / 대형 / 총합 / 기사")

    assignment_rows = []

    for _, row in route_summary.iterrows():
        route = row["route"]
        truck_request_id = row["truck_request_id"]

        c1, c2, c3, c4, c5, c6, c7, c8 = st.columns([1.0, 1.3, 0.9, 0.8, 0.8, 0.8, 0.9, 1.4])
        c1.write(str(route))
        c2.write(str(truck_request_id))
        c3.write(safe_int(row["총정차수"]))
        c4.write(safe_int(row["소형합"]))
        c5.write(safe_int(row["중형합"]))
        c6.write(safe_int(row["대형합"]))
        c7.write(safe_int(row["총합"]))

        selected_driver = c8.selectbox(
            f"기사선택_{route}",
            options=[""] + drivers,
            index=0,
            label_visibility="collapsed"
        )

        assignment_rows.append({
            "route": route,
            "truck_request_id": truck_request_id,
            "총정차수": safe_int(row["총정차수"]),
            "소형합": safe_int(row["소형합"]),
            "중형합": safe_int(row["중형합"]),
            "대형합": safe_int(row["대형합"]),
            "총합": safe_int(row["총합"]),
            "assigned_driver": selected_driver
        })

    assignment_df = pd.DataFrame(assignment_rows)
    route_driver_map = dict(zip(assignment_df["route"], assignment_df["assigned_driver"]))

    result["assigned_driver"] = result["route"].map(route_driver_map)
    grouped["assigned_driver"] = grouped["route"].map(route_driver_map)
    route_summary["assigned_driver"] = route_summary["route"].map(route_driver_map)

    # =========================
    # 기사별 필터
    # =========================
    st.subheader("기사별 필터")
    driver_filter_options = ["전체", "미배정"] + drivers
    selected_filter = st.selectbox("지도 표시 대상", driver_filter_options)

    if selected_filter == "전체":
        map_result = result.copy()
        map_grouped = grouped.copy()
    elif selected_filter == "미배정":
        map_result = result[result["assigned_driver"].fillna("") == ""].copy()
        map_grouped = grouped[grouped["assigned_driver"].fillna("") == ""].copy()
    else:
        map_result = result[result["assigned_driver"] == selected_filter].copy()
        map_grouped = grouped[grouped["assigned_driver"] == selected_filter].copy()

    valid_result = map_result[map_result["coords"].notna()].copy()
    valid_grouped = map_grouped[map_grouped["coords"].notna()].copy()

    # =========================
    # 지도 (최우선)
    # =========================
    st.subheader("지도")
    st.write(f"캐시 주소 수: {len(cache)}")
    st.write(f"현재 필터: {selected_filter}")
    st.caption("기사 미배정은 루트별 색상+원형+실선, 기사 배정 후에는 같은 기사면 같은 색상+삼각형+점선으로 표시됩니다.")

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
        ].sort_values("stop_order")

        line_points = []
        for _, row in route_df_line.iterrows():
            lat, lon = row["coords"]
            line_points.append([lat, lon])

        route_driver = route_driver_map.get(route, "")
        is_assigned_route = str(route_driver).strip() != ""

        if is_assigned_route:
            line_color = driver_color_map.get(route_driver, "#1e88e5")
        else:
            line_color = route_color_map.get(route, "#1e88e5")

        if len(line_points) >= 2:
            folium.PolyLine(
                line_points,
                color="#111111",
                weight=8,
                opacity=0.55,
                tooltip=route_line_label.get(route, truck_request_id),
                dash_array="10, 8" if is_assigned_route else None
            ).add_to(route_group)

            folium.PolyLine(
                line_points,
                color=line_color,
                weight=5,
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
                camp_no = extract_camp_number(row["company_name"])
                popup_html = f"""
                <b>루트:</b> {row['route']}<br>
                <b>트럭요청ID:</b> {row.get('truck_request_id', '')}<br>
                <b>기사:</b> {row.get('assigned_driver', '')}<br>
                <b>캠프명:</b> {row['company_name']}<br>
                <b>주소:</b> {row['address']}<br>
                <b>순서:</b> {safe_int(row['first_stop'])}/{safe_int(row['route_total'])}
                """

                if is_assigned_pin:
                    icon_obj = make_assigned_triangle_camp_icon(pin_color, camp_no)
                else:
                    icon_obj = make_camp_div_icon(camp_no)

                folium.Marker(
                    [lat, lon],
                    popup=popup_html,
                    tooltip=row["hover_text"],
                    icon=icon_obj
                ).add_to(route_group)

            else:
                popup_html = f"""
                <b>루트:</b> {row['route']}<br>
                <b>트럭요청ID:</b> {row.get('truck_request_id', '')}<br>
                <b>기사:</b> {row.get('assigned_driver', '')}<br>
                <b>업체ID:</b> {row['company_id']}<br>
                <b>업체명:</b> {row['company_name']}<br>
                <b>주소:</b> {row['address']}<br>
                <b>순서:</b> {safe_int(row['first_stop'])}/{safe_int(row['route_total'])}<br>
                <b>건수:</b> {safe_int(row['stop_count'])}<br>
                <b>물량:</b> {safe_int(row['ae_sum'])}.{safe_int(row['af_sum'])}.{safe_int(row['ag_sum'])}
                """

                if is_assigned_pin:
                    icon_obj = make_assigned_triangle_icon(pin_color, str(safe_int(row["first_stop"])))
                else:
                    icon_obj = make_stop_div_icon(pin_color, str(safe_int(row["first_stop"])))

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

    # HTML 저장 + 다운로드
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

    share_url = f"{APP_URL}?map={html_filename}"

    st.subheader("지도 공유 링크")
    st.success("아래 링크를 복사해서 바로 공유하시면 됩니다.")
    st.markdown(f"### [🔗 지도 바로 열기]({share_url})")
    st.text_input("공유 URL", value=share_url, key="share_url_box")

    # =========================
    # 기사별 할당표
    # =========================
    st.subheader("기사 할당표")

    assigned_only = assignment_df.copy()
    assigned_only["assigned_driver"] = assigned_only["assigned_driver"].fillna("").astype(str)

    assigned_summary = (
        assigned_only[assigned_only["assigned_driver"].str.strip() != ""]
        .groupby("assigned_driver", as_index=False)
        .agg(
            담당루트수=("route", "count"),
            소형합=("소형합", "sum"),
            중형합=("중형합", "sum"),
            대형합=("대형합", "sum"),
            총박스합계=("총합", "sum")
        )
        .sort_values(["assigned_driver"])
        .reset_index(drop=True)
    )

    if len(assigned_summary) == 0:
        st.info("아직 기사 배정이 없습니다.")
    else:
        st.dataframe(assigned_summary, use_container_width=True)

    csv_data = assignment_df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
    st.download_button(
        "기사 배정표 CSV 다운로드",
        data=csv_data,
        file_name="route_assignment.csv",
        mime="text/csv"
    )
