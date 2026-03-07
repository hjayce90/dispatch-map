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
# 설정
# =========================

KAKAO_API_KEY = "080375c5f09bbb8c7db9f368bc752d33"
TIME_COL_INDEX = 21

CACHE_FILE = "geocode_cache.csv"
MAP_DIR = "saved_maps"

APP_URL = "https://dispatch-map.streamlit.app"

os.makedirs(MAP_DIR, exist_ok=True)

# =========================
# 공유 링크 처리
# =========================

query_params = st.query_params
shared_map = query_params.get("map")

if shared_map:

    map_path = os.path.join(MAP_DIR, shared_map)

    if os.path.exists(map_path):

        with open(map_path, "r", encoding="utf-8") as f:
            html = f.read()

        components.html(html, height=1000, scrolling=True)

    else:

        st.error("저장된 지도를 찾을 수 없습니다.")

    st.stop()

# =========================
# 공통 함수
# =========================


def load_geocode_cache():

    if os.path.exists(CACHE_FILE):

        df = pd.read_csv(CACHE_FILE)

        cache = {}

        for _, row in df.iterrows():

            cache[row["address"]] = (row["lat"], row["lon"])

        return cache

    return {}


def save_geocode_cache(cache):

    rows = []

    for addr, coords in cache.items():

        rows.append({"address": addr, "lat": coords[0], "lon": coords[1]})

    pd.DataFrame(rows).to_csv(CACHE_FILE, index=False)


def geocode_kakao(address, cache):

    if address in cache:

        return cache[address]

    url = "https://dapi.kakao.com/v2/local/search/address.json"

    headers = {"Authorization": f"KakaoAK {KAKAO_API_KEY}"}

    params = {"query": address}

    try:

        res = requests.get(url, headers=headers, params=params)

        if res.status_code != 200:

            return None

        data = res.json()

        if not data["documents"]:

            return None

        y = float(data["documents"][0]["y"])
        x = float(data["documents"][0]["x"])

        cache[address] = (y, x)

        save_geocode_cache(cache)

        return y, x

    except:

        return None


def make_stop_div_icon(color, text):

    html = f"""
    <div style="
    width:24px;
    height:24px;
    border-radius:50%;
    background:{color};
    border:2px solid white;
    color:white;
    text-align:center;
    line-height:20px;
    font-size:11px;
    font-weight:700;
    ">
    {text}
    </div>
    """

    return DivIcon(html=html, icon_size=(24, 24), icon_anchor=(12, 12))


def add_layer_toggle_buttons(m):

    map_name = m.get_name()

    button_html = f"""
    <div style="
    position:absolute;
    top:10px;
    left:10px;
    z-index:9999;
    background:white;
    padding:8px;
    border-radius:8px;
    box-shadow:0 1px 4px rgba(0,0,0,0.25);
    ">

    <button onclick="toggleAllLayers_{map_name}(true)">전체선택</button>
    <button onclick="toggleAllLayers_{map_name}(false)">전체해제</button>

    </div>
    """

    script = f"""
    <script>

    function toggleAllLayers_{map_name}(flag) {{

        var labels = document.querySelectorAll(
        '.leaflet-control-layers-overlays label'
        );

        labels.forEach(function(label) {{

            var checkbox = label.querySelector('input');

            if(checkbox.checked !== flag) {{

                label.click();

            }}

        }});

    }}

    </script>
    """

    m.get_root().html.add_child(folium.Element(button_html))
    m.get_root().script.add_child(folium.Element(script))


# =========================
# 업로드
# =========================

uploaded_file = st.file_uploader("엑셀 업로드", type=["xlsx"])

if not uploaded_file:

    st.info("엑셀 파일 업로드하세요")
    st.stop()

# =========================
# 데이터 로드
# =========================

df = pd.read_excel(uploaded_file)

cache = load_geocode_cache()

coords_list = []

for _, row in df.iterrows():

    addr = str(row.iloc[24]).strip()

    coords = geocode_kakao(addr, cache)

    coords_list.append(coords)

df["coords"] = coords_list

valid = df[df["coords"].notna()]

if len(valid) == 0:

    st.error("좌표 없음")
    st.stop()

# =========================
# 지도 생성
# =========================

center_lat = valid.iloc[0]["coords"][0]
center_lon = valid.iloc[0]["coords"][1]

m = folium.Map(location=[center_lat, center_lon], zoom_start=11)

routes = valid.iloc[:, 0].dropna().unique()

colors = [
"#e53935","#1e88e5","#43a047","#8e24aa","#fb8c00",
"#3949ab","#00897b","#6d4c41","#546e7a","#d81b60"
]

color_map = {}

for i, r in enumerate(routes):

    color_map[r] = colors[i % len(colors)]

for route in routes:

    group = folium.FeatureGroup(name=str(route))

    sub = valid[valid.iloc[:, 0] == route]

    for _, row in sub.iterrows():

        lat, lon = row["coords"]

        folium.Marker(

            [lat, lon],

            icon=make_stop_div_icon(
                color_map[route],
                str(int(row.name))
            )

        ).add_to(group)

    group.add_to(m)

folium.LayerControl(collapsed=False).add_to(m)

add_layer_toggle_buttons(m)

# =========================
# 지도 출력
# =========================

st_folium(m, width=None, height=1000)

# =========================
# HTML 저장
# =========================

filename = "dispatch_map.html"

map_path = os.path.join(MAP_DIR, filename)

m.save(map_path)

with open(map_path, "rb") as f:

    html_bytes = f.read()

st.download_button(

    "HTML 다운로드",
    data=html_bytes,
    file_name=filename,
    mime="text/html"

)

# =========================
# 공유 링크
# =========================

share_url = f"{APP_URL}/?map={filename}"

st.subheader("지도 공유 링크")

st.code(share_url)
