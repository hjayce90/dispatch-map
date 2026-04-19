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


class FakeLoginInput:
    def __init__(self):
        self.click_count = 0
        self.values = []

    def click(self):
        self.click_count += 1

    def send_keys(self, *values):
        self.values.extend(values)

    def clear(self):
        self.values = []


class FakeReadyElement:
    def is_displayed(self):
        return True


class FakeBody:
    def __init__(self, text):
        self.text = text


class FakeTruckDispatchDriver:
    def __init__(self):
        self.current_url = ""
        self.title = ""
        self.body_text = ""
        self.events = []
        self.screenshots = []

    def get(self, url):
        self.current_url = url
        self.events.append(("get", url))
        if url == assign_bot.LOGIN_URL:
            self.title = "fts login"
            self.body_text = "Sign in to your account"
        if url == assign_bot.TRUCK_DISPATCH_URL:
            self.title = "Linehaul Service"
            self.body_text = "LS Line-Haul/Truck Dispatch Order Date Request ID Search"

    def find_elements(self, by, selector):
        self.events.append(("find_elements", selector, self.current_url))
        if by == By.CSS_SELECTOR and selector == "input[type='password']":
            return []
        if assign_bot.TRUCK_DISPATCH_URL in self.current_url and (
            "Order Date" in selector or "Request ID" in selector or "Search" in selector
        ):
            return [FakeReadyElement()]
        return []

    def find_element(self, by, selector):
        if by == By.TAG_NAME and selector == "body":
            return FakeBody(self.body_text)
        raise RuntimeError("element unavailable")

    def save_screenshot(self, path):
        self.screenshots.append(path)
        return True


def immediate_safe_click(_driver, elem, **_kwargs):
    elem.click()
    return {"changed": None, "method": "test", "attempt": 1}


def login_safe_click(driver, elem, **_kwargs):
    elem.click()
    driver.current_url = assign_bot.LOGIN_URL
    driver.title = "Linehaul Service"
    driver.body_text = "LS Line-Haul Welcome to the Coupang Line-Haul Service System"
    return {"changed": True, "method": "test", "attempt": 1}


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

    def test_go_to_truck_dispatch_waits_for_target_dom_after_navigation(self):
        driver = FakeTruckDispatchDriver()

        assign_bot.go_to_truck_dispatch(driver, timeout=1)

        self.assertEqual(driver.events[0], ("get", assign_bot.TRUCK_DISPATCH_URL))
        dispatch_dom_events = [
            event for event in driver.events
            if event[0] == "find_elements"
            and ("Order Date" in event[1] or "Request ID" in event[1] or "Search" in event[1])
        ]
        self.assertTrue(dispatch_dom_events)
        self.assertTrue(
            all(event[2] == assign_bot.TRUCK_DISPATCH_URL for event in dispatch_dom_events)
        )

    def test_login_moves_to_truck_dispatch_before_dispatch_dom_lookup(self):
        driver = FakeTruckDispatchDriver()
        id_input = FakeLoginInput()
        pw_input = FakeLoginInput()
        login_button = FakeEditButton()

        with patch.object(assign_bot, "get_coupang_credentials", lambda: ("user", "pw")), patch.object(
            assign_bot,
            "find_first",
            side_effect=[id_input, pw_input, login_button],
        ), patch.object(
            assign_bot,
            "safe_click",
            login_safe_click,
        ), patch.object(
            assign_bot,
            "sleep_step",
            lambda _sec=0: None,
        ), patch.object(assign_bot, "log", lambda _msg: None):
            assign_bot.login(driver)

        get_events = [event for event in driver.events if event[0] == "get"]
        self.assertEqual(
            get_events,
            [("get", assign_bot.LOGIN_URL), ("get", assign_bot.TRUCK_DISPATCH_URL)],
        )
        dispatch_dom_events = [
            event for event in driver.events
            if event[0] == "find_elements"
            and ("Order Date" in event[1] or "Request ID" in event[1] or "Search" in event[1])
        ]
        self.assertTrue(dispatch_dom_events)
        self.assertTrue(
            all(event[2] == assign_bot.TRUCK_DISPATCH_URL for event in dispatch_dom_events)
        )

    def test_run_assignments_df_preserves_startup_exception_in_aborted_progress(self):
        events = []
        input_df = pd.DataFrame(
            [
                {
                    "registration_mode": "new",
                    "order_date": "2026-04-20",
                    "request_id": "TR-001",
                    "worker_login_id": "worker1",
                    "plate_number": "plate1",
                },
            ]
        )

        with patch.object(assign_bot, "build_driver", lambda: FakeAssignmentDriver()), patch.object(
            assign_bot,
            "login",
            side_effect=RuntimeError("original login failure"),
        ), patch.object(
            assign_bot,
            "sleep_step",
            lambda _sec=0: None,
        ):
            results_df = assign_bot.run_assignments_df(
                input_df,
                result_file=None,
                progress_callback=events.append,
                raise_on_abort=False,
            )

        self.assertTrue(results_df.empty)
        self.assertEqual(events[0]["event"], "start")
        self.assertEqual(events[-1]["event"], "aborted")
        self.assertEqual(events[-1]["error"], "original login failure")


if __name__ == "__main__":
    unittest.main()
