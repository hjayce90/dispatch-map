import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from assign_input_builder import (
    ASSIGN_ERROR_COLUMNS,
    ASSIGN_INPUT_COLUMNS,
    build_assign_input_df,
    build_assign_input_from_csv,
)


class AssignInputBuilderTests(unittest.TestCase):
    def make_drivers_df(self):
        return pd.DataFrame(
            [
                {
                    "driver_name": "박재우",
                    "worker_login_id": "zkfltm79",
                    "plate_number": "인천85배2386",
                },
                {
                    "driver_name": "김태경",
                    "worker_login_id": "ktk900",
                    "plate_number": "경기83사9271",
                },
            ]
        )

    def test_merge_success_builds_assign_bot_columns(self):
        assignment_df = pd.DataFrame(
            [{"truck_request_id": "TR-001", "assigned_driver": "박재우"}]
        )

        success_df, error_df = build_assign_input_df(
            assignment_df,
            self.make_drivers_df(),
            order_date="2026-04-14",
            registration_mode="new",
        )

        self.assertEqual(list(success_df.columns), ASSIGN_INPUT_COLUMNS)
        self.assertTrue(error_df.empty)
        self.assertEqual(success_df.iloc[0].to_dict(), {
            "registration_mode": "new",
            "order_date": "2026-04-14",
            "request_id": "TR-001",
            "worker_login_id": "zkfltm79",
            "plate_number": "인천85배2386",
        })

    def test_unknown_driver_is_excluded_and_reported(self):
        assignment_df = pd.DataFrame(
            [{"truck_request_id": "TR-404", "assigned_driver": "없는기사"}]
        )

        success_df, error_df = build_assign_input_df(
            assignment_df,
            self.make_drivers_df(),
            order_date="2026-04-14",
            registration_mode="new",
        )

        self.assertTrue(success_df.empty)
        self.assertEqual(list(error_df.columns), ASSIGN_ERROR_COLUMNS)
        self.assertEqual(error_df.iloc[0]["request_id"], "TR-404")
        self.assertEqual(error_df.iloc[0]["driver_name"], "없는기사")
        self.assertIn("driver_name not found", error_df.iloc[0]["error_reason"])

    def test_header_candidates_are_normalized(self):
        cases = [
            ({"truck_request_id": "TR-001", "assigned_driver": "박재우"}, "TR-001"),
            ({"트럭요청ID": "TR-002", "이름": "김태경"}, "TR-002"),
            ({"Request ID": "TR-003", "기사명": "박재우"}, "TR-003"),
        ]

        for row, expected_request_id in cases:
            with self.subTest(row=row):
                success_df, error_df = build_assign_input_df(
                    pd.DataFrame([row]),
                    self.make_drivers_df(),
                    order_date="2026-04-14",
                    registration_mode="modify",
                )

                self.assertTrue(error_df.empty)
                self.assertEqual(success_df.iloc[0]["request_id"], expected_request_id)
                self.assertEqual(success_df.iloc[0]["registration_mode"], "modify")

    def test_defaults_use_new_and_kst_tomorrow(self):
        now = datetime(2026, 4, 13, 23, 30, tzinfo=ZoneInfo("Asia/Seoul"))
        assignment_df = pd.DataFrame(
            [{"truck_request_id": "TR-001", "assigned_driver": "박재우"}]
        )

        success_df, _ = build_assign_input_df(
            assignment_df,
            self.make_drivers_df(),
            now=now,
        )

        self.assertEqual(success_df.iloc[0]["registration_mode"], "new")
        self.assertEqual(success_df.iloc[0]["order_date"], "2026-04-14")

    def test_missing_driver_csv_path_fails_clearly(self):
        project_dir = Path(__file__).resolve().parents[1]
        existing_assignment_path = project_dir / "drivers.csv"

        with self.assertRaisesRegex(FileNotFoundError, "drivers.csv not found"):
            build_assign_input_from_csv(
                existing_assignment_path,
                project_dir / "missing_drivers.csv",
                order_date="2026-04-14",
            )

    def test_driver_csv_missing_required_columns_fails_clearly(self):
        assignment_df = pd.DataFrame(
            [{"truck_request_id": "TR-001", "assigned_driver": "박재우"}]
        )
        bad_drivers_df = pd.DataFrame([{"driver_name": "박재우"}])

        with self.assertRaisesRegex(ValueError, "drivers.csv missing required columns"):
            build_assign_input_df(
                assignment_df,
                bad_drivers_df,
                order_date="2026-04-14",
            )


if __name__ == "__main__":
    unittest.main()
