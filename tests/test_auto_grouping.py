import unittest

import pandas as pd

from auto_grouping import (
    DRIVER_PREFERENCE_COLUMNS,
    build_driver_preference_df,
    build_group_assignment_df,
    build_group_map_data,
    filter_group_map_for_routes,
    has_complete_group_map,
)


def make_route_feature_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "route": "A-1",
                "총합": 12,
                "스톱수": 8,
                "보정걸린분": 95,
                "route_spread_km": 4.2,
                "route_prefix": "A",
                "truck_request_id": "TR-001",
            },
            {
                "route": "B-1",
                "총합": 18,
                "스톱수": 11,
                "보정걸린분": 120,
                "route_spread_km": 7.6,
                "route_prefix": "B",
                "truck_request_id": "TR-002",
            },
        ]
    )


class AutoGroupingRegressionTests(unittest.TestCase):
    def test_build_driver_preference_df_returns_stable_empty_schema(self):
        result = build_driver_preference_df(pd.DataFrame(), {})

        self.assertEqual(list(result.columns), DRIVER_PREFERENCE_COLUMNS)
        self.assertTrue(result.empty)

    def test_stale_group_map_is_filtered_out_for_new_upload_routes(self):
        route_feature_df = make_route_feature_df()
        stale_group_map = {
            "OLD-1": "추천그룹 1",
            "OLD-2": "추천그룹 2",
        }

        filtered_group_map = filter_group_map_for_routes(route_feature_df, stale_group_map)
        preference_df = build_driver_preference_df(route_feature_df, stale_group_map)
        assignment_df = build_group_assignment_df(route_feature_df, stale_group_map)

        self.assertEqual(filtered_group_map, {})
        self.assertFalse(has_complete_group_map(route_feature_df, stale_group_map))
        self.assertEqual(list(preference_df.columns), DRIVER_PREFERENCE_COLUMNS)
        self.assertTrue(preference_df.empty)
        self.assertTrue(assignment_df.empty)

    def test_complete_group_map_for_current_routes_still_builds_preference_rows(self):
        route_feature_df = make_route_feature_df()
        group_map = {
            "A-1": "추천그룹 1",
            "B-1": "추천그룹 2",
            "OLD-1": "추천그룹 9",
        }

        filtered_group_map = filter_group_map_for_routes(route_feature_df, group_map)
        preference_df = build_driver_preference_df(route_feature_df, group_map)

        self.assertEqual(
            filtered_group_map,
            {"A-1": "추천그룹 1", "B-1": "추천그룹 2"},
        )
        self.assertTrue(has_complete_group_map(route_feature_df, group_map))
        self.assertEqual(list(preference_df.columns), DRIVER_PREFERENCE_COLUMNS)
        self.assertEqual(preference_df["추천그룹"].tolist(), ["추천그룹 1", "추천그룹 2"])
        self.assertEqual(preference_df["선호예상순위"].tolist(), [1, 2])

    def test_group_helpers_do_not_mutate_input_dataframes(self):
        route_feature_df = make_route_feature_df()
        route_feature_before = route_feature_df.copy(deep=True)
        result_delivery = pd.DataFrame(
            [
                {"route": "A-1", "coords": (37.1, 127.1), "value": "a"},
                {"route": "B-1", "coords": (37.2, 127.2), "value": "b"},
            ]
        )
        grouped_delivery = pd.DataFrame(
            [
                {"route": "A-1", "coords": (37.1, 127.1), "stop": 1},
                {"route": "B-1", "coords": (37.2, 127.2), "stop": 2},
            ]
        )
        result_before = result_delivery.copy(deep=True)
        grouped_before = grouped_delivery.copy(deep=True)
        group_map = {
            "A-1": "추천그룹 1",
            "B-1": "추천그룹 2",
        }

        build_group_assignment_df(route_feature_df, group_map)
        build_driver_preference_df(route_feature_df, group_map)
        build_group_map_data(result_delivery, grouped_delivery, group_map)

        pd.testing.assert_frame_equal(route_feature_df, route_feature_before)
        pd.testing.assert_frame_equal(result_delivery, result_before)
        pd.testing.assert_frame_equal(grouped_delivery, grouped_before)
        self.assertNotIn("추천그룹", result_delivery.columns)
        self.assertNotIn("추천그룹", grouped_delivery.columns)


if __name__ == "__main__":
    unittest.main()
