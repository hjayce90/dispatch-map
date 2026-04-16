from __future__ import annotations

import re
import unittest
from pathlib import Path


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
ALLOWED_RECOMMENDATION_KEYS = {
    "recommended_groups_result",
    "recommended_groups_meta",
    "recommended_groups_assignment_df",
    "recommended_groups_status",
    "recommended_groups_error",
    "recommended_groups_inputs_hash",
    "recommended_groups_dataset_key",
    "recommended_groups_selected_filter",
}
LEGACY_KEYS = {
    "recommended_group_map",
    "recommended_group_count",
    "selected_group_filter",
    "group_assignment_rows",
}
FORBIDDEN_CLEAR_KEYS = {
    "assignment_store",
    "memo",
    "map_cache",
    "share_payload",
    "loaded_payload",
    "saved_payload",
}


def app_source() -> str:
    return APP_PATH.read_text(encoding="utf-8")


def function_body(source: str, name: str) -> str:
    match = re.search(rf"^def {name}\(.*?(?=^def |\Z)", source, flags=re.S | re.M)
    if not match:
        raise AssertionError(f"missing function: {name}")
    return match.group(0)


class RecommendationStateSafetyTests(unittest.TestCase):
    def test_recommendation_namespace_is_the_only_internal_state_key_set(self):
        source = app_source()
        match = re.search(r"RECOMMENDATION_STATE_KEYS\s*=\s*\((.*?)\)", source, flags=re.S)
        self.assertIsNotNone(match)

        keys = set(re.findall(r'"([^"]+)"', match.group(1)))
        self.assertEqual(keys, ALLOWED_RECOMMENDATION_KEYS)

    def test_legacy_keys_are_not_written_to_session_state(self):
        source = app_source()

        for key in LEGACY_KEYS:
            self.assertNotIn(f'st.session_state["{key}"]', source)
            self.assertNotIn(f"st.session_state['{key}']", source)

    def test_error_helper_keeps_last_successful_result(self):
        body = function_body(app_source(), "mark_recommended_groups_error")

        self.assertNotIn('st.session_state["recommended_groups_result"] =', body)
        self.assertNotIn("clear_recommendation_state(", body)

    def test_clear_recommendation_state_does_not_touch_other_state(self):
        body = function_body(app_source(), "clear_recommendation_state")

        for key in FORBIDDEN_CLEAR_KEYS:
            self.assertNotIn(key, body)
        for key in LEGACY_KEYS:
            self.assertNotIn(f'st.session_state["{key}"]', body)
            self.assertNotIn(f"st.session_state['{key}']", body)

    def test_recommendation_dataframe_copy_uses_deep_copy(self):
        body = function_body(app_source(), "_copy_recommended_groups_assignment_df")

        self.assertIn("copy(deep=True)", body)


if __name__ == "__main__":
    unittest.main()
