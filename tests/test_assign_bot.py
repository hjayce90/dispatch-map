import unittest
from unittest.mock import patch

import pandas as pd
from selenium.webdriver.common.by import By

import assign_bot


class FakeSwitch:
    def __init__(self, checked):
        self.checked = checked
        self.click_count = 0

    def get_attribute(self, name):
        if name == "aria-checked":
            return self.checked
        return ""

    def click(self):
        self.click_count += 1
        if self.checked == "false":
            self.checked = "true"


class FakeEditButton:
    def __init__(self):
        self.click_count = 0

    def is_displayed(self):
        return True

    def is_enabled(self):
        return True

    def click(self):
        self.click_count += 1


class FakeRow:
    def __init__(self, edit_button):
        self.edit_button = edit_button
        self.queries = []

    def is_displayed(self):
        return True

    def find_elements(self, by, selector):
        self.queries.append((by, selector))
        if by == By.XPATH and "Edit" in selector:
            return [self.edit_button]
        return []


class FakeRowWithoutEdit:
    def __init__(self, row_key="1"):
        self.row_key = row_key
        self.queries = []

    def is_displayed(self):
        return True

    def is_enabled(self):
        return True

    def get_attribute(self, name):
        if name == "data-row-key":
            return self.row_key
        return ""

    def find_elements(self, by, selector):
        self.queries.append((by, selector))
        return []


class FakeDriver:
    def __init__(self, row, fixed_edit_button=None, fixed_confirm_button=None):
        self.row = row
        self.fixed_edit_button = fixed_edit_button
        self.fixed_confirm_button = fixed_confirm_button
        self.queries = []

    def find_elements(self, by, selector):
        self.queries.append((by, selector))
        if by == By.XPATH and "TR-001" in selector and "role='row'" in selector:
            return [self.row]
        if (
            by == By.XPATH
            and self.fixed_edit_button is not None
            and "ant-table-fixed-right" in selector
            and "data-row-key='1'" in selector
        ):
            return [self.fixed_edit_button]
        if (
            by == By.XPATH
            and self.fixed_confirm_button is not None
            and "ant-table-fixed-right" in selector
            and "data-row-key='1'" in selector
            and "Confirm" in selector
        ):
            return [self.fixed_confirm_button]
        return []

    def execute_script(self, script, *_args):
        if "getBoundingClientRect" in script:
            return {
                "width": 22,
                "height": 17,
                "display": "inline",
                "visibility": "visible",
                "pointerEvents": "auto",
            }
        raise AssertionError("JS click fallback should not be needed")

    def save_screenshot(self, _path):
        return True


class FakeAssignmentDriver:
    def __init__(self):
        self.quit_count = 0

    def quit(self):
        self.quit_count += 1


def immediate_safe_click(_driver, elem, **_kwargs):
    elem.click()
    return {"changed": None, "method": "test", "attempt": 1}


class AssignBotHelperTests(unittest.TestCase):
    def test_driver_public_true_does_not_click(self):
        switch = FakeSwitch("true")

        with patch.object(assign_bot, "log", lambda _msg: None):
            clicked = assign_bot.ensure_switch_enabled(None, switch, label="Driver Public")

        self.assertFalse(clicked)
        self.assertEqual(switch.click_count, 0)

    def test_driver_public_false_clicks_once(self):
        switch = FakeSwitch("false")

        with patch.object(assign_bot, "sleep_step", lambda _sec=0: None), patch.object(
            assign_bot,
            "safe_click",
            immediate_safe_click,
        ):
            clicked = assign_bot.ensure_switch_enabled(None, switch, label="Driver Public")

        self.assertTrue(clicked)
        self.assertEqual(switch.click_count, 1)
        self.assertEqual(switch.get_attribute("aria-checked"), "true")

    def test_click_edit_for_request_id_uses_matching_row_scope(self):
        edit_button = FakeEditButton()
        row = FakeRow(edit_button)
        driver = FakeDriver(row)

        with patch.object(assign_bot, "sleep_step", lambda _sec=0: None), patch.object(
            assign_bot,
            "save_shot",
            lambda *_args, **_kwargs: None,
        ), patch.object(
            assign_bot,
            "safe_click",
            immediate_safe_click,
        ):
            assign_bot.click_edit_for_request_id(driver, "TR-001")

        self.assertEqual(edit_button.click_count, 1)
        self.assertTrue(driver.queries)
        self.assertIn("TR-001", driver.queries[0][1])
        self.assertTrue(row.queries)
        self.assertIn("Edit", row.queries[0][1])

    def test_click_edit_for_request_id_uses_fixed_action_column_fallback(self):
        edit_button = FakeEditButton()
        row = FakeRowWithoutEdit(row_key="1")
        driver = FakeDriver(row, fixed_edit_button=edit_button)

        with patch.object(assign_bot, "sleep_step", lambda _sec=0: None), patch.object(
            assign_bot,
            "save_shot",
            lambda *_args, **_kwargs: None,
        ), patch.object(
            assign_bot,
            "safe_click",
            immediate_safe_click,
        ), patch.object(assign_bot, "log", lambda _msg: None):
            assign_bot.click_edit_for_request_id(driver, "TR-001")

        self.assertEqual(edit_button.click_count, 1)
        self.assertTrue(any("ant-table-fixed-right" in query[1] for query in driver.queries))

    def test_click_registration_action_for_new_uses_confirm(self):
        confirm_button = FakeEditButton()
        row = FakeRowWithoutEdit(row_key="1")
        driver = FakeDriver(row, fixed_confirm_button=confirm_button)

        with patch.object(assign_bot, "sleep_step", lambda _sec=0: None), patch.object(
            assign_bot,
            "save_shot",
            lambda *_args, **_kwargs: None,
        ), patch.object(
            assign_bot,
            "safe_click",
            immediate_safe_click,
        ), patch.object(assign_bot, "log", lambda _msg: None):
            assign_bot.click_registration_action_for_request_id(driver, "TR-001", "new")

        self.assertEqual(confirm_button.click_count, 1)
        self.assertTrue(any("Confirm" in query[1] for query in driver.queries))

    def test_run_assignments_df_can_run_without_result_file(self):
        calls = []
        fake_driver = FakeAssignmentDriver()
        input_df = pd.DataFrame(
            [
                {
                    "registration_mode": "new",
                    "order_date": "2026-04-15",
                    "request_id": "TR-001",
                    "worker_login_id": "worker1",
                    "plate_number": "plate1",
                },
                {
                    "registration_mode": "new",
                    "order_date": "2026-04-15",
                    "request_id": "TR-002",
                    "worker_login_id": "worker2",
                    "plate_number": "",
                },
            ]
        )

        def fake_process_one(_driver, row, progress_callback=None):
            calls.append(row["request_id"])
            return {
                "request_id": row["request_id"],
                "status": "success",
                "reason": "",
                "registration_mode": row["registration_mode"],
                "order_date": row["order_date"],
                "worker_login_id": row["worker_login_id"],
                "plate_number": row["plate_number"],
            }

        with patch.object(assign_bot, "build_driver", lambda: fake_driver), patch.object(
            assign_bot,
            "login",
            lambda _driver: None,
        ), patch.object(assign_bot, "process_one", fake_process_one), patch.object(
            assign_bot,
            "sleep_step",
            lambda _sec=0: None,
        ):
            results_df = assign_bot.run_assignments_df(input_df, result_file=None)

        self.assertEqual(calls, ["TR-001"])
        self.assertEqual(results_df["status"].tolist(), ["success"])
        self.assertEqual(results_df["request_id"].tolist(), ["TR-001"])


if __name__ == "__main__":
    unittest.main()
