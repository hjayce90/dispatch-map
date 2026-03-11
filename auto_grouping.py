import math
from typing import Dict, List, Tuple

import pandas as pd


def safe_int(v) -> int:
    try:
        if pd.isna(v):
            return 0
        return int(float(v))
    except Exception:
        return 0


def minutes_to_korean_text(x) -> str:
    try:
        if pd.isna(x):
            return "0시간 00분"
        total = int(x)
        hh = total // 60
        mm = total % 60
        return f"{hh}시간 {mm:02d}분"
    except Exception:
        return "0시간 00분"


def calc_distance_km(lat1, lon1, lat2, lon2) -> float:
    if any(pd.isna(v) for v in [lat1, lon1, lat2, lon2]):
        return 0.0

    r = 6371.0
    p1 = math.radians(float(lat1))
    p2 = math.radians(float(lat2))
    dp = math.radians(float(lat2) - float(lat1))
    dl = math.radians(float(lon2) - float(lon1))

    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return r * c


def flatten_coords(coord_lists) -> List[Tuple[float, float]]:
    out = []
    for item in coord_lists:
        if isinstance(item, list):
            for xy in item:
                if isinstance(xy, (list, tuple)) and len(xy) == 2:
                    out.append((float(xy[0]), float(xy[1])))
        elif isinstance(item, tuple) and len(item) == 2:
            out.append((float(item[0]), float(item[1])))
    return out


def max_pairwise_distance(coords: List[Tuple[float, float]]) -> float:
    coords = flatten_coords(coords)
    if not coords or len(coords) <= 1:
        return 0.0

    max_d = 0.0
    for i in range(len(coords)):
        for j in range(i + 1, len(coords)):
            lat1, lon1 = coords[i]
            lat2, lon2 = coords[j]
            d = calc_distance_km(lat1, lon1, lat2, lon2)
            if d > max_d:
                max_d = d
    return max_d


def estimate_adjusted_minutes(total_minutes, total_boxes) -> int:
    base = safe_int(total_minutes)
    boxes = safe_int(total_boxes)

    if boxes < 20:
        return max(0, base - 5)
    elif boxes <= 40:
        return base
    return base + 10


def build_route_feature_df(route_summary: pd.DataFrame, grouped_delivery: pd.DataFrame) -> pd.DataFrame:
    rows = []

    for _, route_row in route_summary.iterrows():
        route = str(route_row["route"]).strip()
        route_points = grouped_delivery[grouped_delivery["route"] == route].copy()

        coords = []
        for _, p in route_points.iterrows():
            xy = p.get("coords")
            if isinstance(xy, (list, tuple)) and len(xy) == 2:
                coords.append((float(xy[0]), float(xy[1])))

        if coords:
            centroid_lat = sum(x[0] for x in coords) / len(coords)
            centroid_lon = sum(x[1] for x in coords) / len(coords)
        else:
            centroid_lat = None
            centroid_lon = None

        route_spread_km = max_pairwise_distance(coords)

        rows.append({
            "route": route,
            "truck_request_id": route_row["truck_request_id"],
            "route_prefix": route_row.get("route_prefix", ""),
            "camp_name": route_row.get("camp_name", ""),
            "스톱수": safe_int(route_row["스톱수"]),
            "소형합": safe_int(route_row["소형합"]),
            "중형합": safe_int(route_row["중형합"]),
            "대형합": safe_int(route_row["대형합"]),
            "총합": safe_int(route_row["총합"]),
            "총걸린분": safe_int(route_row["총걸린분"]),
            "보정걸린분": estimate_adjusted_minutes(route_row["총걸린분"], route_row["총합"]),
            "coords_list": coords,
            "centroid_lat": centroid_lat,
            "centroid_lon": centroid_lon,
            "route_spread_km": route_spread_km,
        })

    return pd.DataFrame(rows)


def choose_auto_group_count(route_feature_df: pd.DataFrame) -> int:
    if len(route_feature_df) == 0:
        return 1

    n_routes = len(route_feature_df)
    total_minutes = route_feature_df["보정걸린분"].sum()

    k = max(1, round(n_routes / 2.5))

    while k < n_routes:
        avg_minutes = total_minutes / k
        if avg_minutes <= 220:
            break
        k += 1

    return max(1, min(k, n_routes))


def resolve_group_count(route_feature_df: pd.DataFrame, manual_group_count=None) -> int:
    if len(route_feature_df) == 0:
        return 1

    k = safe_int(manual_group_count)
    if k <= 0:
        k = choose_auto_group_count(route_feature_df)

    return max(1, min(k, len(route_feature_df)))


def init_farthest_seeds(route_feature_df: pd.DataFrame, k: int):
    valid = route_feature_df.dropna(subset=["centroid_lat", "centroid_lon"]).copy()
    if len(valid) == 0:
        return []

    points = valid[["route", "centroid_lat", "centroid_lon"]].values.tolist()
    seeds = [points[0]]

    while len(seeds) < min(k, len(points)):
        best_point = None
        best_dist = -1

        for p in points:
            _, lat, lon = p
            min_d = float("inf")
            for s in seeds:
                _, slat, slon = s
                d = calc_distance_km(lat, lon, slat, slon)
                if d < min_d:
                    min_d = d
            if min_d > best_dist:
                best_dist = min_d
                best_point = p

        if best_point is None:
            break
        seeds.append(best_point)

    return seeds


def assign_routes_to_centers(route_feature_df: pd.DataFrame, centers: List[Tuple[float, float]]) -> Dict[str, str]:
    if len(route_feature_df) == 0:
        return {}

    group_map = {}
    for _, row in route_feature_df.iterrows():
        route = row["route"]
        lat = row["centroid_lat"]
        lon = row["centroid_lon"]

        if pd.isna(lat) or pd.isna(lon) or not centers:
            group_map[route] = "추천그룹 1"
            continue

        best_idx = 0
        best_dist = float("inf")

        for idx, center in enumerate(centers):
            clat, clon = center
            d = calc_distance_km(lat, lon, clat, clon)
            if d < best_dist:
                best_dist = d
                best_idx = idx

        group_map[route] = f"추천그룹 {best_idx + 1}"

    return group_map


def recompute_centers(route_feature_df: pd.DataFrame, group_map: Dict[str, str], k: int):
    centers = []
    for i in range(1, k + 1):
        gname = f"추천그룹 {i}"
        part = route_feature_df[route_feature_df["route"].map(group_map) == gname].copy()
        part = part.dropna(subset=["centroid_lat", "centroid_lon"])

        if len(part) == 0:
            centers.append((None, None))
        else:
            centers.append((
                part["centroid_lat"].mean(),
                part["centroid_lon"].mean()
            ))
    return centers


def count_routes_per_group(group_map: Dict[str, str], k: int) -> Dict[str, int]:
    counts = {f"추천그룹 {i}": 0 for i in range(1, k + 1)}
    for gname in group_map.values():
        if gname in counts:
            counts[gname] += 1
    return counts


def fill_empty_groups(route_feature_df: pd.DataFrame, group_map: Dict[str, str], k: int) -> Dict[str, str]:
    """빈 그룹이 없도록 다른 그룹에서 라우트를 하나씩 이동해 채운다."""
    if len(route_feature_df) == 0:
        return group_map

    adjusted_map = group_map.copy()
    score_cache = evaluate_group_score(route_feature_df, adjusted_map)

    while True:
        counts = count_routes_per_group(adjusted_map, k)
        empty_groups = [g for g, c in counts.items() if c == 0]
        if not empty_groups:
            break

        target_group = empty_groups[0]
        donor_groups = [g for g, c in counts.items() if c > 1]
        if not donor_groups:
            break

        best_route = None
        best_score_delta = float("inf")

        for donor_group in donor_groups:
            donor_routes = [r for r, g in adjusted_map.items() if g == donor_group]
            for route in donor_routes:
                test_map = adjusted_map.copy()
                test_map[route] = target_group
                test_score = evaluate_group_score(route_feature_df, test_map)
                score_delta = test_score - score_cache
                if score_delta < best_score_delta:
                    best_score_delta = score_delta
                    best_route = route

        if best_route is None:
            break

        adjusted_map[best_route] = target_group
        score_cache = evaluate_group_score(route_feature_df, adjusted_map)

    return adjusted_map


def evaluate_group_score(route_feature_df: pd.DataFrame, group_map: Dict[str, str]) -> float:
    if len(route_feature_df) == 0:
        return 0.0

    score = 0.0
    stops_by_group = []
    boxes_by_group = []

    group_names = sorted(set(group_map.values()), key=lambda x: int(str(x).replace("추천그룹 ", "")))

    for gname in group_names:
        part = route_feature_df[route_feature_df["route"].map(group_map) == gname].copy()
        if len(part) == 0:
            continue

        all_coords = []
        for _, row in part.iterrows():
            all_coords.extend(row["coords_list"])

        spread_km = max_pairwise_distance(all_coords)
        total_stops = part["스톱수"].sum()
        total_boxes = part["총합"].sum()
        total_minutes = part["보정걸린분"].sum()

        # 1순위: 퍼짐이 작을수록 좋음
        score += spread_km * 10.0

        # 너무 길게 찢어진 그룹 강한 패널티
        if spread_km > 20:
            score += (spread_km - 20) * 12.0

        # 시간은 참고용 패널티
        if total_minutes > 270:
            score += (total_minutes - 270) * 2.0

        stops_by_group.append(total_stops)
        boxes_by_group.append(total_boxes)

    if stops_by_group:
        avg_stops = sum(stops_by_group) / len(stops_by_group)
        imbalance = sum(abs(x - avg_stops) for x in stops_by_group)
        score += imbalance * 1.5

    if boxes_by_group:
        avg_boxes = sum(boxes_by_group) / len(boxes_by_group)
        if avg_boxes > 0:
            box_imbalance = 0.0
            over_20_penalty = 0.0
            for boxes in boxes_by_group:
                deviation_rate = abs(boxes - avg_boxes) / avg_boxes
                box_imbalance += deviation_rate
                if deviation_rate > 0.2:
                    over_20_penalty += (deviation_rate - 0.2) ** 2

            # 3순위 소프트 제약: 기본적으로는 균형을 유도하되,
            # 20%를 넘는 경우에만 강한 페널티를 추가해 "최대한 맞추되 불가 시 덜 나쁜 해"를 선택하도록 함.
            score += box_imbalance * 6.0
            score += over_20_penalty * 220.0

    return score


def recommend_route_groups(route_feature_df: pd.DataFrame, manual_group_count=None) -> Dict[str, str]:
    if len(route_feature_df) == 0:
        return {}

    k = resolve_group_count(route_feature_df, manual_group_count=manual_group_count)

    seeds = init_farthest_seeds(route_feature_df, k)
    if not seeds:
        return {r: "추천그룹 1" for r in route_feature_df["route"].tolist()}

    centers = [(s[1], s[2]) for s in seeds]
    group_map = assign_routes_to_centers(route_feature_df, centers)
    group_map = fill_empty_groups(route_feature_df, group_map, k)

    for _ in range(5):
        centers = recompute_centers(route_feature_df, group_map, k)
        valid_centers = []
        for idx, c in enumerate(centers):
            if c[0] is None or c[1] is None:
                if idx < len(seeds):
                    valid_centers.append((seeds[idx][1], seeds[idx][2]))
                else:
                    valid_centers.append((None, None))
            else:
                valid_centers.append(c)

        usable_centers = [(a, b) for a, b in valid_centers if a is not None and b is not None]
        group_map = assign_routes_to_centers(route_feature_df, usable_centers)
        group_map = fill_empty_groups(route_feature_df, group_map, k)

    current_score = evaluate_group_score(route_feature_df, group_map)
    routes = route_feature_df["route"].tolist()
    all_groups = [f"추천그룹 {i}" for i in range(1, k + 1)]

    improved = True
    loop_guard = 0
    while improved and loop_guard < 7:
        improved = False
        loop_guard += 1

        for route in routes:
            original_group = group_map.get(route)
            best_group = original_group
            best_score = current_score

            group_counts = count_routes_per_group(group_map, k)
            if group_counts.get(original_group, 0) <= 1:
                continue

            for candidate_group in all_groups:
                if candidate_group == original_group:
                    continue

                test_map = group_map.copy()
                test_map[route] = candidate_group
                test_score = evaluate_group_score(route_feature_df, test_map)

                if test_score < best_score:
                    best_score = test_score
                    best_group = candidate_group

            if best_group != original_group:
                group_map[route] = best_group
                current_score = best_score
                improved = True

    group_map = fill_empty_groups(route_feature_df, group_map, k)

    return group_map


def build_group_assignment_df(route_feature_df: pd.DataFrame, group_map: Dict[str, str]) -> pd.DataFrame:
    if len(route_feature_df) == 0:
        return pd.DataFrame()

    out = route_feature_df.copy()
    out["추천그룹"] = out["route"].map(group_map).fillna("추천그룹 1")
    out["추천이유"] = ""
    return out


def build_group_summary_df(group_assignment_df: pd.DataFrame) -> pd.DataFrame:
    if len(group_assignment_df) == 0:
        return pd.DataFrame()

    rows = []
    group_names = sorted(
        group_assignment_df["추천그룹"].dropna().unique().tolist(),
        key=lambda x: int(str(x).replace("추천그룹 ", ""))
    )

    avg_stops = group_assignment_df.groupby("추천그룹")["스톱수"].sum().mean()
    avg_boxes = group_assignment_df.groupby("추천그룹")["총합"].sum().mean()

    for gname in group_names:
        part = group_assignment_df[group_assignment_df["추천그룹"] == gname].copy()

        all_coords = []
        for _, row in part.iterrows():
            all_coords.extend(row["coords_list"])

        spread_km = max_pairwise_distance(all_coords)
        total_stops = safe_int(part["스톱수"].sum())
        total_boxes = safe_int(part["총합"].sum())
        total_minutes = safe_int(part["보정걸린분"].sum())
        route_list = part["route"].astype(str).tolist()
        box_deviation_rate = 0.0
        if avg_boxes:
            box_deviation_rate = abs(total_boxes - avg_boxes) / avg_boxes

        reasons = []
        if spread_km <= 10:
            reasons.append("라우트가 비교적 좁게 모여 있음")
        elif spread_km <= 18:
            reasons.append("라우트가 무난한 범위로 묶임")
        else:
            reasons.append("라우트 범위가 다소 길게 퍼짐")

        if avg_stops:
            gap = abs(total_stops - avg_stops)
            if gap <= 5:
                reasons.append("스톱 수 균형이 양호함")
            else:
                reasons.append("스톱 수 편차가 다소 있음")

        if box_deviation_rate <= 0.2:
            reasons.append("수량 차이가 20% 이내")
        else:
            reasons.append("수량 차이가 20% 초과")

        if total_minutes <= 240:
            reasons.append("예상시간이 비교적 안정적임")
        elif total_minutes <= 270:
            reasons.append("예상시간이 상한에 가까움")
        else:
            reasons.append("예상시간 주의 필요")

        rows.append({
            "추천그룹": gname,
            "라우트수": len(route_list),
            "라우트목록": ", ".join(route_list),
            "총스톱수": total_stops,
            "총박스수": total_boxes,
            "수량편차율": round(box_deviation_rate * 100, 1),
            "보정예상분": total_minutes,
            "보정예상시간": minutes_to_korean_text(total_minutes),
            "최대퍼짐km": round(spread_km, 1),
            "추천이유": " / ".join(reasons),
        })

    return pd.DataFrame(rows)




def build_group_detail_stats_df(group_assignment_df: pd.DataFrame) -> pd.DataFrame:
    if len(group_assignment_df) == 0:
        return pd.DataFrame(columns=["추천그룹", "라우트개수", "박스총개수", "소형합", "중형합", "대형합"])

    grouped = (
        group_assignment_df.groupby("추천그룹", as_index=False)
        .agg({
            "route": "nunique",
            "총합": "sum",
            "소형합": "sum",
            "중형합": "sum",
            "대형합": "sum",
        })
        .rename(columns={
            "route": "라우트개수",
            "총합": "박스총개수",
            "소형합": "소형합",
            "중형합": "중형합",
            "대형합": "대형합",
        })
    )

    grouped = grouped.sort_values(
        by="추천그룹",
        key=lambda col: col.map(lambda x: int(str(x).replace("추천그룹 ", "")))
    ).reset_index(drop=True)
    return grouped


def _inverse_minmax(series: pd.Series) -> pd.Series:
    s = series.fillna(0).astype(float)
    s_min = s.min()
    s_max = s.max()
    if s_max == s_min:
        return pd.Series([1.0] * len(s), index=s.index)
    return 1.0 - ((s - s_min) / (s_max - s_min))


def build_driver_preference_df(route_feature_df: pd.DataFrame, group_map: Dict[str, str]) -> pd.DataFrame:
    if len(route_feature_df) == 0:
        return pd.DataFrame()

    out = route_feature_df.copy()
    out["추천그룹"] = out["route"].map(group_map).fillna("")

    box_score = _inverse_minmax(out["총합"])
    stop_score = _inverse_minmax(out["스톱수"])
    minute_score = _inverse_minmax(out["보정걸린분"])
    spread_score = _inverse_minmax(out["route_spread_km"])

    out["선호예상점수"] = (
        box_score * 0.35
        + stop_score * 0.20
        + minute_score * 0.30
        + spread_score * 0.15
    ) * 100

    out["선호예상순위"] = out["선호예상점수"].rank(method="dense", ascending=False).astype(int)

    reason_labels = {
        "box": "박스 수가 상대적으로 적음",
        "stop": "스톱 수가 상대적으로 적음",
        "minute": "예상 소요시간이 짧은 편",
        "spread": "라우트 퍼짐이 작은 편",
    }

    reason_rows = []
    for i in out.index:
        strengths = [
            ("box", float(box_score.loc[i])),
            ("stop", float(stop_score.loc[i])),
            ("minute", float(minute_score.loc[i])),
            ("spread", float(spread_score.loc[i])),
        ]
        strengths.sort(key=lambda x: x[1], reverse=True)
        top2 = [reason_labels[k] for k, _ in strengths[:2]]
        reason_rows.append(" / ".join(top2))

    out["선호이유"] = reason_rows

    return out[[
        "route",
        "추천그룹",
        "총합",
        "스톱수",
        "보정걸린분",
        "route_spread_km",
        "선호예상점수",
        "선호예상순위",
        "선호이유",
    ]].sort_values(["선호예상순위", "route"]).reset_index(drop=True)


def default_group_edit_map(group_assignment_df: pd.DataFrame) -> Dict[str, str]:
    if len(group_assignment_df) == 0:
        return {}
    return {row["route"]: row["추천그룹"] for _, row in group_assignment_df.iterrows()}


def apply_group_edit_map(route_feature_df: pd.DataFrame, group_edit_map: Dict[str, str]) -> pd.DataFrame:
    out = route_feature_df.copy()
    out["추천그룹"] = out["route"].map(group_edit_map).fillna("추천그룹 1")
    return out


def build_group_map_data(result_delivery: pd.DataFrame, grouped_delivery: pd.DataFrame, group_map: Dict[str, str]):
    result2 = result_delivery.copy()
    grouped2 = grouped_delivery.copy()

    result2["추천그룹"] = result2["route"].map(group_map).fillna("")
    grouped2["추천그룹"] = grouped2["route"].map(group_map).fillna("")

    valid_result = result2[result2["coords"].notna()].copy()
    valid_grouped = grouped2[grouped2["coords"].notna()].copy()
    return valid_result, valid_grouped
