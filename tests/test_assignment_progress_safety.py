import re
import unittest
from pathlib import Path


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def app_source() -> str:
    return APP_PATH.read_text(encoding="utf-8")


def function_body(source: str, name: str) -> str:
    match = re.search(rf"^def {name}\(.*?(?=^def |\Z)", source, flags=re.S | re.M)
    if not match:
        raise AssertionError(f"missing function: {name}")
    return match.group(0)


def safe_int(value) -> int:
    try:
        return int(float(value))
    except Exception:
        return 0


def clean_map_text(value) -> str:
    return str(value or "").strip()


class AssignmentProgressSafetyTests(unittest.TestCase):
    def test_progress_message_tolerates_empty_or_partial_state(self):
        namespace = {
            "safe_int": safe_int,
            "clean_map_text": clean_map_text,
        }
        exec(function_body(app_source(), "build_assignment_progress_message"), namespace)

        build_message = namespace["build_assignment_progress_message"]

        empty_message = build_message("자동할당 실패", None)
        self.assertIn("[자동할당 실패]", empty_message)
        self.assertIn("전체 0건 중 0건 처리", empty_message)

        partial_message = build_message(
            "자동할당 실패",
            {
                "total": 22,
                "completed": 0,
                "last_exception_message": "original login failure",
            },
        )
        self.assertIn("전체 22건 중 0건 처리", partial_message)
        self.assertIn("original login failure", partial_message)

    def test_telegram_notification_helper_catches_unexpected_errors(self):
        body = function_body(app_source(), "send_assignment_progress_notification")

        self.assertIn("except Exception as exc", body)
        self.assertIn('return "failed", str(exc)', body)

    def test_progress_callback_notify_cannot_mask_assignment_exception(self):
        body = function_body(app_source(), "make_assignment_progress_callback")

        self.assertIn("def _notify", body)
        self.assertIn("try:", body)
        self.assertIn("send_assignment_progress_notification", body)
        self.assertIn("logger.warning", body)


if __name__ == "__main__":
    unittest.main()
