import os
import re
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

# 본인 Streamlit 배포 주소로 바꿔주세요
APP_URL = "https://dispatch-map.streamlit.app"

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
# 예: https://dispatch-map.streamlit.app/?map=20260309.html
# =========================
query_params = st.query_params
shared_map = query_params.get("map")

if shared_map:
    shared_map_path = os.path.join(MAP_DIR, shared_map)

    if os.path.exists(shared_map_path):
        st.subheader(f"공유 지도: {shared_map}")
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

def add_layer_toggle_buttons(m):
    map_name = m.get_name()

    button_html = f"""
    <style>
      .layer-toggle-box {{
        position: absolute;
        top: 10px;
        left: 10px;
        z-index: 9999;
        background: rgba(255,255,255,0.95);
        border: 1px solid #bbb;
        border-radius: 8px;
        padding: 8px;
        box-shadow: 0 1px 4px rgba(0,0,0,0.25);
        font-size: 12px;
      }}
      .layer-toggle-box button {{
        display: block;
        width: 110px;
        margin: 4px 0;
        padding: 6px 8px;
        border: 1px solid #999;
        border-radius: 6px;
        background: #f7f7f7;
        cursor: pointer;
        font-size: 12px;
      }}
      .layer-toggle-box button:hover {{
        background: #ececec;
      }}
    </style>

    <div class="layer-toggle-box">
      <button type="button" onclick="toggleAllLayers_{map_name}(true)">전체선택</button>
      <button type="button" onclick="toggleAllLayers_{map_name}(false)">전체해제</button>
    </div>
    """

    script_html = f"""
    <script>
      function toggleAllLayers_{map_name}(showFlag) {{
        var labels = document.querySelectorAll('.leaflet-control-layers-overlays label');

        labels.forEach(function(label) {{
          var checkbox = label.querySelector('input.leaflet-control-layers-selector');
          if (!checkbox) return;

          if (checkbox.checked !== showFlag) {{
            label.click();
          }}
        }});
      }}
    </script>
    """

    m.get_root().html.add_child(folium.Element(button_html))
    m.get_root().script.add_child(folium.Element(script_html))

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
        route: f"{truck_request_map.get(route, '')} {int(row['ae_sum'])}/{int(row['af_sum'])}/{int(row['ag_sum'])}"
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
            lambda r: f"{int(r['first_stop'])}/{int(r['route_total'])} - {int(r['ae_sum'])}.{int(r['af_sum'])}.{int(r['ag_sum'])}",
            axis=1
        )
        grouped_normal["hover_text"] = grouped_normal.apply(
            lambda r: f"{r['first_time']} {int(r['ae_sum'])}/{int(r['af_sum'])}/{int(r['ag_sum'])}".strip(),
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
            lambda r: f"{int(r['first_stop'])}/{int(r['route_total'])} - CAMP",
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

    result["coords"] = result["address"].apply(lambda x: geocode_kakao(x, KAKAO_API_KEY, cache))
    grouped["coords"] = grouped["address"].apply(lambda x: geocode_kakao(x, KAKAO_API_KEY, cache))

    route_summary = (
        result.groupby(["route", "truck_request_id"], as_index=False)
        .agg(
            총정차수=("address", "count"),
            캠프수=("is_camp", "sum"),
            소형합=("ae", "sum"),
            중형합=("af", "sum"),
            대형합=("ag", "sum"),
        )
        .sort_values("route")
        .reset_index(drop=True)
    )

    st.subheader("기사 배정")
    st.caption("route / truck_request_id / 총정차수 / 소형 / 중+대 / 기사")

    assignment_rows = []

    for _, row in route_summary.iterrows():
        route = row["route"]
        truck_request_id = row["truck_request_id"]

        c1, c2, c3, c4, c5, c6 = st.columns([1.2, 1.2, 0.8, 0.8, 0.8, 1.4])
        c1.write(str(route))
        c2.write(str(truck_request_id))
        c3.write(int(row["총정차수"]))
        c4.write(int(row["소형합"]))
        c5.write(int(row["중형합"] + row["대형합"]))

        selected_driver = c6.selectbox(
            f"기사선택_{route}",
            options=[""] + drivers,
            index=0,
            label_visibility="collapsed"
        )

        assignment_rows.append({
            "route": route,
            "truck_request_id": truck_request_id,
            "총정차수": int(row["총정차수"]),
            "소형합": int(row["소형합"]),
            "중형합": int(row["중형합"]),
            "대형합": int(row["대형합"]),
            "assigned_driver": selected_driver
        })

    assignment_df = pd.DataFrame(assignment_rows)
    route_driver_map = dict(zip(assignment_df["route"], assignment_df["assigned_driver"]))

    result["assigned_driver"] = result["route"].map(route_driver_map)
    grouped["assigned_driver"] = grouped["route"].map(route_driver_map)
    route_summary["assigned_driver"] = route_summary["route"].map(route_driver_map)

    st.subheader("기사별 필터")
    driver_filter_options = ["전체", "미배정"] + drivers
    selected_filter = st.selectbox("지도 표시 대상", driver_filter_options)

    if selected_filter == "전체":
        map_result = result.copy()
        map_grouped = grouped.copy()
        map_route_summary = route_summary.copy()
    elif selected_filter == "미배정":
        map_result = result[result["assigned_driver"].fillna("") == ""].copy()
        map_grouped = grouped[grouped["assigned_driver"].fillna("") == ""].copy()
        map_route_summary = route_summary[route_summary["assigned_driver"].fillna("") == ""].copy()
    else:
        map_result = result[result["assigned_driver"] == selected_filter].copy()
        map_grouped = grouped[grouped["assigned_driver"] == selected_filter].copy()
        map_route_summary = route_summary[route_summary["assigned_driver"] == selected_filter].copy()

    valid_result = map_result[map_result["coords"].notna()].copy()
    valid_grouped = map_grouped[map_grouped["coords"].notna()].copy()

    st.subheader("배정 결과표")
    st.dataframe(assignment_df, use_container_width=True)

    csv_data = assignment_df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
    st.download_button(
        "배정 결과 CSV 다운로드",
        data=csv_data,
        file_name="route_assignment.csv",
        mime="text/csv"
    )

    st.subheader("지도")
    st.write(f"캐시 주소 수: {len(cache)}")
    st.write(f"현재 필터: {selected_filter}")
    st.caption("오른쪽 상단 LayerControl + 왼쪽 상단 전체선택/전체해제를 사용할 수 있습니다. HTML 다운로드 파일에서도 동작합니다.")

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

    route_debug = []

    for route in route_list:
        truck_request_id = truck_request_map.get(route, "")

        route_group = folium.FeatureGroup(
            name=f"{route} | {truck_request_id}",
            show=True
        )

        route_df_all = map_result[map_result["route"] == route].sort_values("stop_order")

        route_df_line = map_result[
            (map_result["route"] == route) &
            (map_result["is_camp"] == False) &
            (map_result["coords"].notna())
        ].sort_values("stop_order")

        line_points = []
        for _, row in route_df_line.iterrows():
            lat, lon = row["coords"]
            line_points.append([lat, lon])

        route_debug.append({
            "route": route,
            "truck_request_id": truck_request_id,
            "전체정차수": len(route_df_all),
            "라인용좌표수(캠프제외)": len(route_df_line),
            "라인생성여부": "Y" if len(line_points) >= 2 else "N"
        })

        if len(line_points) >= 2:
            folium.PolyLine(
                line_points,
                color="#111111",
                weight=8,
                opacity=0.55,
                tooltip=route_line_label.get(route, truck_request_id)
            ).add_to(route_group)

            folium.PolyLine(
                line_points,
                color=route_color_map[route],
                weight=5,
                opacity=0.95,
                tooltip=route_line_label.get(route, truck_request_id)
            ).add_to(route_group)

        route_grouped = valid_grouped[valid_grouped["route"] == route].copy()

        for _, row in route_grouped.iterrows():
            lat, lon = row["coords"]
            route_color = route_color_map.get(route, "#1e88e5")
            is_camp = bool(row["is_camp"])

            if is_camp:
                camp_no = extract_camp_number(row["company_name"])
                popup_html = f"""
                <b>루트:</b> {row['route']}<br>
                <b>트럭요청ID:</b> {row.get('truck_request_id', '')}<br>
                <b>기사:</b> {row.get('assigned_driver', '')}<br>
                <b>캠프명:</b> {row['company_name']}<br>
                <b>주소:</b> {row['address']}<br>
                <b>순서:</b> {int(row['first_stop'])}/{int(row['route_total'])}
                """

                folium.Marker(
                    [lat, lon],
                    popup=popup_html,
                    tooltip=row["hover_text"],
                    icon=make_camp_div_icon(camp_no)
                ).add_to(route_group)

            else:
                popup_html = f"""
                <b>루트:</b> {row['route']}<br>
                <b>트럭요청ID:</b> {row.get('truck_request_id', '')}<br>
                <b>기사:</b> {row.get('assigned_driver', '')}<br>
                <b>업체ID:</b> {row['company_id']}<br>
                <b>업체명:</b> {row['company_name']}<br>
                <b>주소:</b> {row['address']}<br>
                <b>순서:</b> {int(row['first_stop'])}/{int(row['route_total'])}<br>
                <b>건수:</b> {row['stop_count']}<br>
                <b>물량:</b> {int(row['ae_sum'])}.{int(row['af_sum'])}.{int(row['ag_sum'])}
                """

                folium.Marker(
                    [lat, lon],
                    popup=popup_html,
                    tooltip=row["hover_text"],
                    icon=make_stop_div_icon(route_color, str(int(row["first_stop"])))
                ).add_to(route_group)

        route_group.add_to(m)

    folium.LayerControl(collapsed=False, position="topright").add_to(m)
    add_layer_toggle_buttons(m)

    marker_count = len(valid_grouped)
    st.write(f"지도에 찍힌 핀 수: {marker_count}")

    st.subheader("루트별 좌표 상태")
    st.dataframe(pd.DataFrame(route_debug), use_container_width=True)

    failed_rows = map_result[map_result["coords"].isna()][["route", "truck_request_id", "stop_order", "company_name", "address", "is_camp"]].copy()
    if len(failed_rows) > 0:
        st.subheader("좌표 변환 실패 목록")
        st.dataframe(failed_rows, use_container_width=True)

    st.subheader("루트 요약")
    st.dataframe(map_route_summary, use_container_width=True)

    st_folium(m, width=None, height=1000)

    map_path = os.path.join(MAP_DIR, html_filename)

    # 문자열 render 대신 완전한 HTML 문서로 저장
    m.save(map_path)

    with open(map_path, "rb") as f:
        html_bytes = f.read()

    st.download_button(
        label=f"지도 다운로드 (HTML) - {html_filename}",
        data=html_bytes,
        file_name=html_filename,
        mime="text/html"
    )

    share_url = f"{APP_URL}/?map={html_filename}"

    st.subheader("지도 공유 링크")
    st.code(share_url)
    st.caption("이 링크를 카카오톡으로 보내면 같은 지도를 바로 열 수 있습니다. 단, 서버 재시작 시 저장 파일이 사라질 수 있습니다.")
