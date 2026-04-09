"""Thin orchestration helpers around recommendation grouping."""

from __future__ import annotations

import pandas as pd

from auto_grouping import (
    build_group_assignment_df,
    build_group_summary_df,
    build_driver_preference_df,
    build_route_feature_df,
    choose_auto_group_count,
    empty_driver_preference_df,
    recommend_route_groups,
    resolve_group_count,
)


def build_recommendation_route_feature_df(
    route_summary: pd.DataFrame,
    grouped_delivery: pd.DataFrame,
    camp_coords: dict | None = None,
) -> pd.DataFrame:
    return build_route_feature_df(route_summary, grouped_delivery, camp_coords=camp_coords)


def build_auto_group_count_preview(
    route_summary: pd.DataFrame,
    grouped_delivery: pd.DataFrame,
    camp_coords: dict | None = None,
) -> tuple[pd.DataFrame, int]:
    route_feature_df = build_recommendation_route_feature_df(route_summary, grouped_delivery, camp_coords=camp_coords)
    return route_feature_df, choose_auto_group_count(route_feature_df)


def run_route_group_recommendation(
    route_summary: pd.DataFrame,
    grouped_delivery: pd.DataFrame,
    manual_group_count=None,
    camp_coords: dict | None = None,
) -> tuple[pd.DataFrame, int, dict]:
    route_feature_df = build_recommendation_route_feature_df(route_summary, grouped_delivery, camp_coords=camp_coords)
    recommended_group_count = resolve_group_count(route_feature_df, manual_group_count=manual_group_count)
    recommended_group_map = recommend_route_groups(route_feature_df, manual_group_count=manual_group_count)
    return route_feature_df, recommended_group_count, recommended_group_map


def build_group_recommendation_tables(route_feature_df: pd.DataFrame, recommended_group_map: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    group_assignment_df = build_group_assignment_df(route_feature_df, recommended_group_map)
    group_summary_df = build_group_summary_df(group_assignment_df)
    return group_assignment_df, group_summary_df


def build_driver_preference_table(route_feature_df: pd.DataFrame, group_map: dict) -> pd.DataFrame:
    if route_feature_df is None or len(route_feature_df) == 0:
        return empty_driver_preference_df()

    preference_df = build_driver_preference_df(route_feature_df, group_map)
    if preference_df is None or len(preference_df) == 0:
        return empty_driver_preference_df()

    fallback_df = empty_driver_preference_df()
    out = fallback_df.copy()
    for col in preference_df.columns:
        out[col] = preference_df[col]

    for col in ["라우트수", "총합", "스톱수", "보정걸린분", "선호예상순위"]:
        out[col] = pd.to_numeric(out.get(col, 0), errors="coerce").fillna(0).astype(int)
    for col in ["route_spread_km", "선호예상점수"]:
        out[col] = pd.to_numeric(out.get(col, 0), errors="coerce").fillna(0.0)
    out["추천그룹"] = out["추천그룹"].fillna("").astype(str)
    out["선호이유"] = out["선호이유"].fillna("").astype(str)

    return out[fallback_df.columns.tolist()].copy()
