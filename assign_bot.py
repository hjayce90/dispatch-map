import argparse
import os
import sys
import time
import traceback
from pathlib import Path

import pandas as pd
from selenium import webdriver
from selenium.common.exceptions import StaleElementReferenceException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


LOGIN_URL = "https://ls.coupang.com/#/welcomePage"
TRUCK_DISPATCH_URL = "https://ls.coupang.com/#/orderManagement/truckDispatch"
TRUCK_DISPATCH_URL_KEY = "ordermanagement/truckdispatch"
TRUCK_DISPATCH_READY_SELECTORS = [
    (By.XPATH, "//label[contains(., 'Request ID')]/following::input[1]"),
    (By.XPATH, "//input[contains(@placeholder, 'Request ID')]"),
    (By.XPATH, "//button[contains(., 'Search')]"),
    (By.XPATH, "//span[contains(., 'Search')]/ancestor::button[1]"),
]
REQUEST_SEARCH_MAX_WAIT_SECONDS = 8
REQUEST_SEARCH_POLL_INTERVAL_SECONDS = 0.1
REQUEST_SEARCH_RESULT_SETTLE_SECONDS = 0.2
PROCESSED_TAB_MAX_WAIT_SECONDS = 3
PROCESSED_TAB_POLL_INTERVAL_SECONDS = 0.1
PROCESSED_TAB_SETTLE_SECONDS = 0.1
REQUEST_ACTION_ROW_MAX_WAIT_SECONDS = 1.5
REQUEST_ACTION_ROW_POLL_INTERVAL_SECONDS = 0.1
MODAL_READY_MAX_WAIT_SECONDS = 3
MODAL_READY_POLL_INTERVAL_SECONDS = 0.1
FIELD_VALUE_MAX_WAIT_SECONDS = 0.4
FIELD_VALUE_POLL_INTERVAL_SECONDS = 0.05
POST_CONFIRM_DELAY_SECONDS = 0.1
ACTION_CLICK_STATE_TIMEOUT_SECONDS = 2
SEARCH_DRIVER_STABILIZE_SECONDS = 0.2
DRIVER_LOOKUP_MAX_WAIT_SECONDS = 1.2
DRIVER_LOOKUP_POLL_INTERVAL_SECONDS = 0.2
SAVE_RESULT_MAX_WAIT_SECONDS = 8
SAVE_RESULT_POLL_INTERVAL_SECONDS = 0.1

SCREENSHOT_DIR = "bot_screenshots"
DEBUG_SOURCE_CHARS = 20000
RESULT_FILE = "assign_results.csv"
RESULT_COLUMNS = [
    "request_id",
    "status",
    "reason",
    "registration_mode",
    "order_date",
    "worker_login_id",
    "plate_number",
]
PROGRESS_STAGES = (
    "queued",
    "searching",
    "clicking",
    "waiting_response",
    "verifying",
    "saved",
    "failed",
)
ASSIGN_INPUT_REQUIRED_COLUMNS = [
    "registration_mode",
    "order_date",
    "request_id",
    "worker_login_id",
    "plate_number",
]

# 환경변수로 넣기. LS 할당 수집과 같은 계정을 우선 사용한다.
COUPANG_ID = os.getenv("COUPANG_LS_ID", "").strip() or os.getenv("COUPANG_ID", "").strip()
COUPANG_PW = os.getenv("COUPANG_LS_PW", "").strip() or os.getenv("COUPANG_PW", "").strip()

# 비워두면 시스템 PATH의 chromedriver 사용
CHROMEDRIVER_PATH = ""

def env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return bool(default)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return bool(default)

# 쿠팡 인증 페이지가 headless Chrome을 차단할 수 있어 기본은 일반 창으로 실행한다.
HEADLESS = env_bool("COUPANG_ASSIGN_HEADLESS", False)
DETACH_BROWSER = env_bool("COUPANG_ASSIGN_DETACH_BROWSER", False)

STEP_SLEEP = 1.0
CLICK_DIAGNOSTICS = {
    "retry_count": 0,
    "last_reason": "",
}

Path(SCREENSHOT_DIR).mkdir(exist_ok=True)


def log(msg):
    print(f"[LOG] {msg}")


def safe_name(value):
    text = str(value or "").strip().replace("\\", "_").replace("/", "_")
    return "".join(ch if ch.isalnum() or ch in {"_", "-", "."} else "_" for ch in text)[:80] or "snapshot"


def driver_current_url(driver):
    try:
        return str(getattr(driver, "current_url", "") or "")
    except Exception as exc:
        return f"<current_url error: {exc}>"


def driver_title(driver):
    try:
        return str(getattr(driver, "title", "") or "")
    except Exception as exc:
        return f"<title error: {exc}>"


def driver_body_head(driver, limit=800):
    try:
        body = driver.find_element(By.TAG_NAME, "body").text
    except Exception as exc:
        return f"<body error: {exc}>"
    return str(body or "").replace("\r", " ").replace("\n", " | ")[:limit]


def selector_text(selectors):
    return [f"{by} => {selector}" for by, selector in selectors]


def save_page_source(driver, name, limit=DEBUG_SOURCE_CHARS):
    ts = time.strftime("%Y%m%d_%H%M%S")
    path = os.path.join(SCREENSHOT_DIR, f"{ts}_{safe_name(name)}.html")
    try:
        source = str(getattr(driver, "page_source", "") or "")
        if limit and len(source) > limit:
            source = source[:limit] + "\n<!-- truncated -->\n"
        Path(path).write_text(source, encoding="utf-8", errors="replace")
        log(f"page_source saved: {path}")
        return path
    except Exception as exc:
        log(f"page_source save failed: {name}: {exc}")
        return ""


def debug_snapshot(driver, label, *, include_source=False, selectors=None):
    log(f"[stage] {label}")
    log(f"[stage] current_url={driver_current_url(driver)}")
    log(f"[stage] title={driver_title(driver)}")
    log(f"[stage] body_head={driver_body_head(driver)}")
    if selectors:
        log("[stage] selectors=" + " || ".join(selector_text(selectors)))
    shot_path = save_shot(driver, f"debug_{safe_name(label)}")
    source_path = save_page_source(driver, f"debug_{safe_name(label)}") if include_source else ""
    return {"screenshot": shot_path, "page_source": source_path}


def reset_click_diagnostics():
    CLICK_DIAGNOSTICS["retry_count"] = 0
    CLICK_DIAGNOSTICS["last_reason"] = ""


def note_click_retry(reason):
    CLICK_DIAGNOSTICS["retry_count"] = int(CLICK_DIAGNOSTICS.get("retry_count", 0)) + 1
    CLICK_DIAGNOSTICS["last_reason"] = str(reason or "").strip()


def current_click_retry_count():
    try:
        return int(CLICK_DIAGNOSTICS.get("retry_count", 0))
    except Exception:
        return 0


def emit_progress(progress_callback, event, stage="", request_id="", **kwargs):
    if progress_callback is None:
        return
    payload = {
        "event": str(event or "").strip(),
        "stage": str(stage or "").strip(),
        "request_id": str(request_id or "").strip(),
        "retry_count": current_click_retry_count(),
        "last_click_reason": str(CLICK_DIAGNOSTICS.get("last_reason", "") or "").strip(),
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    payload.update(kwargs)
    try:
        progress_callback(payload)
    except Exception as exc:
        log(f"progress callback failed: {exc}")


def save_shot(driver, name):
    ts = time.strftime("%Y%m%d_%H%M%S")
    path = os.path.join(SCREENSHOT_DIR, f"{ts}_{name}.png")
    try:
        driver.save_screenshot(path)
        log(f"스크린샷 저장: {path}")
        return path
    except Exception as exc:
        log(f"screenshot save failed: {path}: {exc}")
        return ""


def sleep_step(sec=STEP_SLEEP):
    time.sleep(sec)


def build_driver():
    options = Options()
    if HEADLESS:
        options.add_argument("--headless=new")

    options.add_argument("--start-maximized")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("detach", DETACH_BROWSER)

    if CHROMEDRIVER_PATH:
        service = Service(CHROMEDRIVER_PATH)
        driver = webdriver.Chrome(service=service, options=options)
    else:
        driver = webdriver.Chrome(options=options)

    driver.implicitly_wait(1)
    return driver


def wait(driver, timeout=15):
    return WebDriverWait(driver, timeout)


def session_expired_suspected(scope):
    try:
        current_url = str(getattr(scope, "current_url", "") or "").lower()
    except Exception:
        current_url = ""
    if "login" in current_url:
        return True

    try:
        body_text = scope.find_element(By.TAG_NAME, "body").text.lower()
    except Exception:
        return False

    session_terms = [
        "session expired",
        "sign in",
        "please log in",
        "unauthorized",
    ]
    return any(term in body_text for term in session_terms)


def wait_for_login_complete(driver, timeout=30):
    def _ready(_driver):
        current_url = driver_current_url(_driver).lower()
        if "login" in current_url:
            return False
        try:
            password_inputs = _driver.find_elements(By.CSS_SELECTOR, "input[type='password']")
            for elem in password_inputs:
                try:
                    if elem.is_displayed():
                        return False
                except Exception:
                    continue
        except Exception:
            pass
        title = driver_title(_driver).lower()
        body = driver_body_head(_driver, limit=1200).lower()
        if "linehaul service" in title or "line-haul" in body or "truck dispatch" in body:
            return True
        return False

    try:
        return WebDriverWait(driver, timeout).until(_ready)
    except TimeoutException as exc:
        debug_snapshot(driver, "login_complete_timeout", include_source=True)
        raise TimeoutException("timeout waiting for login complete detection") from exc


def is_truck_dispatch_url(driver):
    try:
        current_url = str(getattr(driver, "current_url", "") or "").lower()
    except Exception:
        return False
    return TRUCK_DISPATCH_URL_KEY in current_url


def wait_for_truck_dispatch_page(driver, timeout=30):
    log("[stage] truckDispatch readiness wait start")
    log("[stage] selectors=" + " || ".join(selector_text(TRUCK_DISPATCH_READY_SELECTORS)))

    def _ready(_driver):
        if not is_truck_dispatch_url(_driver):
            return False
        if session_expired_suspected(_driver):
            raise RuntimeError("session/login expired suspected before truckDispatch page ready")
        for by, selector in TRUCK_DISPATCH_READY_SELECTORS:
            try:
                elems = _driver.find_elements(by, selector)
            except Exception:
                continue
            for elem in elems:
                try:
                    if elem.is_displayed():
                        log(f"[stage] truckDispatch readiness matched selector: {selector}")
                        return elem
                except Exception:
                    log(f"[stage] truckDispatch readiness matched selector without display check: {selector}")
                    return elem
        return False

    try:
        return WebDriverWait(driver, timeout).until(_ready)
    except TimeoutException as exc:
        debug_snapshot(
            driver,
            "truck_dispatch_ready_timeout",
            include_source=True,
            selectors=TRUCK_DISPATCH_READY_SELECTORS,
        )
        reason = "timeout waiting for truckDispatch page ready"
        if session_expired_suspected(driver):
            reason = f"{reason}; session/login expired suspected"
        else:
            reason = f"{reason}; server slow response suspected"
        raise TimeoutException(reason) from exc


def go_to_truck_dispatch(driver, timeout=30):
    debug_snapshot(driver, "before truckDispatch move")
    driver.get(TRUCK_DISPATCH_URL)
    debug_snapshot(driver, "after truckDispatch move")
    ready_elem = wait_for_truck_dispatch_page(driver, timeout=timeout)
    debug_snapshot(driver, "truckDispatch ready")
    return ready_elem


def find_first(driver, selectors, timeout=10, clickable=False, visible=False):
    last_error = None

    for by, selector in selectors:
        try:
            if clickable:
                elem = WebDriverWait(driver, timeout).until(
                    EC.element_to_be_clickable((by, selector))
                )
            elif visible:
                elem = WebDriverWait(driver, timeout).until(
                    EC.visibility_of_element_located((by, selector))
                )
            else:
                elem = WebDriverWait(driver, timeout).until(
                    EC.presence_of_element_located((by, selector))
                )
            return elem
        except TimeoutException:
            if clickable:
                reason = "timeout while waiting clickable"
            elif visible:
                reason = "timeout while waiting visible"
            else:
                reason = "timeout while waiting present"
            if session_expired_suspected(driver):
                reason = f"{reason}; session/login expired suspected"
            log(f"{reason}: {selector}")
            last_error = TimeoutException(reason)
        except Exception as e:
            last_error = e

    raise last_error if last_error else Exception("요소를 찾지 못했습니다.")


def find_all(driver, selectors):
    for by, selector in selectors:
        elems = driver.find_elements(by, selector)
        if elems:
            return elems
    return []


def find_visible_all(scope, by, selector):
    elems = scope.find_elements(by, selector)
    visible = []
    for elem in elems:
        try:
            if elem.is_displayed():
                visible.append(elem)
        except Exception:
            pass
    return visible


ACTIVE_MODAL_SELECTORS = [
    (By.XPATH, "//*[@role='dialog' and .//button[contains(., 'Save')]]"),
    (By.XPATH, "//*[contains(@class, 'modal') and .//button[contains(., 'Save')]]"),
    (By.XPATH, "//*[contains(@class, 'Modal') and .//button[contains(., 'Save')]]"),
]


def element_has_visible_rect(driver, elem):
    try:
        if not elem.is_displayed():
            return False
    except Exception:
        return False

    if driver is None:
        return True

    try:
        rect = driver.execute_script("""
            const el = arguments[0];
            const r = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            return {
                width: r.width,
                height: r.height,
                display: style.display,
                visibility: style.visibility,
                pointerEvents: style.pointerEvents
            };
        """, elem)
        return (
            rect.get("width", 0) > 0
            and rect.get("height", 0) > 0
            and rect.get("display") != "none"
            and rect.get("visibility") != "hidden"
            and rect.get("pointerEvents") != "none"
        )
    except Exception:
        return True


def xpath_literal(value):
    text = str(value)
    if "'" not in text:
        return f"'{text}'"
    if '"' not in text:
        return f'"{text}"'
    parts = text.split("'")
    return "concat(" + ', "\'", '.join(f"'{part}'" for part in parts) + ")"


def page_state_fingerprint(driver):
    try:
        body_text = driver.find_element(By.TAG_NAME, "body").text
    except Exception:
        body_text = ""
    try:
        dialog_count = len(driver.find_elements(By.XPATH, "//*[@role='dialog' or contains(@class, 'modal') or contains(@class, 'Modal')]"))
    except Exception:
        dialog_count = 0
    try:
        loading_count = len(driver.find_elements(By.XPATH, "//*[contains(@class, 'loading') or contains(@class, 'spin') or contains(@class, 'ant-spin')]"))
    except Exception:
        loading_count = 0
    try:
        current_url = str(driver.current_url or "")
    except Exception:
        current_url = ""
    return (
        current_url,
        dialog_count,
        loading_count,
        len(body_text),
        body_text[:1200],
    )


def modal_state_fingerprint(driver):
    try:
        dialog_count = len(driver.find_elements(By.XPATH, "//*[@role='dialog' or contains(@class, 'modal') or contains(@class, 'Modal')]"))
    except Exception:
        dialog_count = 0
    try:
        message_text = "\n".join(
            elem.text for elem in driver.find_elements(By.XPATH, "//*[contains(@class, 'message') or contains(@class, 'toast')]")
            if (elem.text or "").strip()
        )
    except Exception:
        message_text = ""
    try:
        current_url = str(driver.current_url or "")
    except Exception:
        current_url = ""
    return (current_url, dialog_count, message_text[:1200])


def _wait_existing_clickable(driver, elem, timeout=10, label="element"):
    def _ready(_driver):
        try:
            elem.tag_name
            return elem if elem.is_enabled() and element_has_visible_rect(_driver, elem) else False
        except StaleElementReferenceException:
            return False
        except Exception:
            return False

    try:
        return WebDriverWait(driver, timeout).until(_ready)
    except TimeoutException:
        reason = f"timeout while waiting clickable: {label}"
        if session_expired_suspected(driver):
            reason = f"{reason}; session/login expired suspected"
        raise TimeoutException(reason)


def safe_click(
    driver,
    elem,
    label="element",
    state_probe=None,
    state_change_timeout=6,
    require_change=False,
):
    last_error = None
    attempts = 2

    for attempt in range(attempts):
        try:
            _wait_existing_clickable(driver, elem, timeout=10, label=label)
            driver.execute_script(
                "arguments[0].scrollIntoView({block:'center', inline:'center'});",
                elem,
            )
            sleep_step(0.2)

            before_state = state_probe(driver) if callable(state_probe) else None
            click_method = "normal"
            try:
                elem.click()
            except Exception as click_exc:
                click_method = "js"
                log(f"{label} normal click failed; JS click fallback: {click_exc}")
                driver.execute_script("arguments[0].click();", elem)

            if callable(state_probe):
                try:
                    WebDriverWait(driver, state_change_timeout).until(
                        lambda _driver: state_probe(_driver) != before_state
                    )
                    return {"changed": True, "method": click_method, "attempt": attempt + 1}
                except TimeoutException as timeout_exc:
                    reason = f"post-click state unchanged: {label}"
                    if session_expired_suspected(driver):
                        reason = f"{reason}; session/login expired suspected"
                    else:
                        reason = f"{reason}; server slow response suspected"
                    last_error = TimeoutException(reason)
                    log(reason)
                    if attempt < attempts - 1:
                        note_click_retry(reason)
                        sleep_step(0.7)
                        continue
                    if require_change:
                        raise last_error from timeout_exc
                    return {"changed": False, "method": click_method, "attempt": attempt + 1}

            return {"changed": None, "method": click_method, "attempt": attempt + 1}
        except Exception as exc:
            last_error = exc
            reason = f"click attempt failed: {label}: {exc}"
            log(reason)
            if attempt < attempts - 1:
                note_click_retry(reason)
                sleep_step(0.7)
                continue
            raise

    if last_error:
        raise last_error
    return {"changed": None, "method": "", "attempt": attempts}


def clear_and_type(elem, text):
    elem.click()
    try:
        elem.send_keys(Keys.CONTROL, "a")
        elem.send_keys(Keys.DELETE)
    except Exception:
        pass

    try:
        elem.clear()
    except Exception:
        pass

    elem.send_keys(str(text))


def js_set_value(driver, elem, value):
    driver.execute_script("""
        const el = arguments[0];
        const val = arguments[1];
        el.focus();
        el.value = val;
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
        el.blur();
    """, elem, value)


def press_escape(driver):
    try:
        driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
    except Exception:
        pass


def get_coupang_credentials():
    coupang_id = os.getenv("COUPANG_LS_ID", "").strip() or os.getenv("COUPANG_ID", "").strip() or COUPANG_ID
    coupang_pw = os.getenv("COUPANG_LS_PW", "").strip() or os.getenv("COUPANG_PW", "").strip() or COUPANG_PW
    return coupang_id, coupang_pw


def login(driver):
    coupang_id, coupang_pw = get_coupang_credentials()
    if not coupang_id or not coupang_pw:
        raise ValueError("환경변수 COUPANG_LS_ID / COUPANG_LS_PW 또는 COUPANG_ID / COUPANG_PW를 먼저 넣어주세요.")

    debug_snapshot(driver, "before login")
    driver.get(LOGIN_URL)
    debug_snapshot(driver, "after login page open")
    sleep_step(2)

    id_input = find_first(driver, [
        (By.CSS_SELECTOR, "input[type='text']"),
        (By.XPATH, "//input[contains(@placeholder, 'ID')]"),
        (By.XPATH, "//input[contains(@name, 'id')]"),
    ], timeout=15, visible=True)

    pw_input = find_first(driver, [
        (By.CSS_SELECTOR, "input[type='password']"),
        (By.XPATH, "//input[contains(@placeholder, 'PW')]"),
        (By.XPATH, "//input[contains(@name, 'pw')]"),
    ], timeout=15, visible=True)

    clear_and_type(id_input, coupang_id)
    sleep_step(0.5)
    clear_and_type(pw_input, coupang_pw)
    sleep_step(0.5)

    login_btn = find_first(driver, [
        (By.ID, "kc-login"),
        (By.CSS_SELECTOR, "input[type='submit']"),
        (By.XPATH, "//button[contains(., '로그인')]"),
        (By.XPATH, "//button[contains(., 'Login')]"),
        (By.CSS_SELECTOR, "button[type='submit']"),
    ], timeout=10, clickable=True)

    safe_click(
        driver,
        login_btn,
        label="Login",
        state_probe=page_state_fingerprint,
        state_change_timeout=6,
    )
    debug_snapshot(driver, "after login submit")
    wait_for_login_complete(driver)
    debug_snapshot(driver, "after login complete detection")
    sleep_step(3)

    # 로그인 후 웰컴페이지가 떠서 트럭디스패치로 재이동
    go_to_truck_dispatch(driver)
    save_shot(driver, "after_login")


def click_search(driver):
    search_btn = find_first(driver, [
        (By.XPATH, "//button[contains(., 'Search')]"),
        (By.XPATH, "//span[contains(., 'Search')]/ancestor::button[1]"),
    ], timeout=10, clickable=True)

    safe_click(
        driver,
        search_btn,
        label="Search",
    )


def select_registration_tab(driver, registration_mode):
    mode = str(registration_mode).strip().lower()
    if mode == "new":
        log("new mode -> keep default Pending tab")
        return

    target = "Processed"
    log(f"탭 선택: {target}")

    tab = find_first(driver, [
        (By.XPATH, f"//*[@role='tab' and contains(., '{target}')]"),
        (By.XPATH, f"//button[contains(., '{target}')]"),
        (By.XPATH, f"//a[contains(., '{target}')]"),
    ], timeout=10, clickable=True)

    safe_click(
        driver,
        tab,
        label=f"registration tab {target}",
    )
    tab_result = wait_for_processed_tab_ready(driver, tab)
    log(
        f"[timing] processed tab settled in {tab_result['elapsed']:.2f}s "
        f"(overlay_seen={tab_result['overlay_seen']})"
    )
    save_shot(driver, f"tab_{target.lower()}")


def search_request_id(driver, request_id):
    log(f"Request ID 검색: {request_id}")

    req_input = find_first(driver, [
        (By.XPATH, "//label[contains(., 'Request ID')]/following::input[1]"),
        (By.XPATH, "//input[contains(@placeholder, 'Request ID')]"),
    ], timeout=10, visible=True)

    safe_click(driver, req_input)
    sleep_step(0.3)
    clear_and_type(req_input, request_id)
    sleep_step(0.5)

    click_search(driver)
    search_result = wait_for_request_search_result(driver, request_id)
    log(
        f"[timing] request search settled in {search_result['elapsed']:.2f}s "
        f"(state={search_result['state']}, overlay_seen={search_result['overlay_seen']})"
    )
    save_shot(driver, "after_search")


def build_request_row_xpath(request_id):
    request_literal = xpath_literal(str(request_id).strip())
    return (
        f".//tr[@data-row-key={request_literal} or .//*[normalize-space()={request_literal} "
        f"or @title={request_literal} or @data-row-key={request_literal}]] | "
        f".//*[@role='row' and (@data-row-key={request_literal} or .//*[normalize-space()={request_literal} "
        f"or @title={request_literal} or @data-row-key={request_literal}])]"
    )


def append_unique_element(items, elem):
    for existing in items:
        try:
            if existing == elem:
                return
        except Exception:
            pass
    items.append(elem)


def get_row_key(row):
    try:
        return (row.get_attribute("data-row-key") or "").strip()
    except Exception:
        return ""


def wait_for_request_rows(driver, request_id):
    row_xpath = build_request_row_xpath(request_id)
    deadline = time.monotonic() + REQUEST_ACTION_ROW_MAX_WAIT_SECONDS
    last_fallback_rows = []

    while True:
        rows = find_visible_all(driver, By.XPATH, row_xpath)
        if rows:
            return rows

        fallback_rows = find_visible_result_rows(driver)
        last_fallback_rows = fallback_rows
        if len(fallback_rows) == 1 and page_contains_text(driver, request_id):
            log(f"Request ID row not matched by text; using single visible result row fallback for {request_id}")
            return fallback_rows

        if time.monotonic() >= deadline:
            break
        sleep_step(REQUEST_ACTION_ROW_POLL_INTERVAL_SECONDS)

    raise RuntimeError(
        f"Request ID row not found: {request_id}; visible result rows={len(last_fallback_rows)}"
    )


def build_action_xpath(prefix, action_labels):
    parts = []
    for label in action_labels:
        label_literal = xpath_literal(label)
        parts.append(
            f"{prefix}//*[self::a or self::button]"
            f"[normalize-space()={label_literal} or .//*[normalize-space()={label_literal}]]"
        )
    return " | ".join(parts)


def find_fixed_action_buttons(driver, rows, action_labels):
    action_buttons = []
    row_keys = [get_row_key(row) for row in rows]
    row_keys = [row_key for row_key in row_keys if row_key]

    selectors = []
    for row_key in row_keys:
        row_key_literal = xpath_literal(row_key)
        selectors.append(build_action_xpath(
            f"//div[contains(@class, 'ant-table-fixed-right')]//tr[@data-row-key={row_key_literal}]",
            action_labels,
        ))
        selectors.append(build_action_xpath(
            f"//tr[@data-row-key={row_key_literal}]"
            f"//td[contains(@class, 'ant-table-row-cell-last') or contains(@class, 'ant-table-fixed-columns-in-body')]",
            action_labels,
        ))

    selectors.extend([
        build_action_xpath("//div[contains(@class, 'ant-table-fixed-right')]", action_labels),
        build_action_xpath(
            "//td[contains(@class, 'ant-table-row-cell-last') or contains(@class, 'ant-table-fixed-columns-in-body')]",
            action_labels,
        ),
    ])

    for selector in selectors:
        for elem in driver.find_elements(By.XPATH, selector):
            try:
                if elem.is_enabled() and element_has_visible_rect(driver, elem):
                    append_unique_element(action_buttons, elem)
            except Exception:
                continue

    return action_buttons


def find_visible_result_rows(driver):
    row_selectors = [
        "//div[contains(@class, 'ant-table-body')]//tr[not(contains(@class, 'ant-table-placeholder'))]",
        "//tbody[contains(@class, 'ant-table-tbody')]//tr[not(contains(@class, 'ant-table-placeholder'))]",
        "//*[@role='row' and not(contains(@class, 'ant-table-placeholder'))]",
    ]
    rows = []
    for selector in row_selectors:
        for row in find_visible_all(driver, By.XPATH, selector):
            row_text = ""
            try:
                row_text = (row.text or "").strip()
            except Exception:
                pass
            if row_text and "No Data" not in row_text:
                append_unique_element(rows, row)
        if rows:
            break
    return rows


def page_contains_text(driver, text):
    needle = str(text or "").strip()
    if not needle:
        return False
    try:
        body_text = driver.find_element(By.TAG_NAME, "body").text
    except Exception:
        return False
    return needle in str(body_text or "")


def document_class_names(driver):
    try:
        result = driver.execute_script("""
            return {
                html: (document.documentElement && document.documentElement.className) || '',
                body: (document.body && document.body.className) || '',
            };
        """)
        if isinstance(result, dict):
            return str(result.get("html") or ""), str(result.get("body") or "")
    except Exception:
        pass
    return "", ""


def element_state_snapshot(driver, elem):
    try:
        result = driver.execute_script("""
            const el = arguments[0];
            if (!el) return null;
            return {
                className: el.className || '',
                ariaSelected: el.getAttribute('aria-selected') || '',
                text: (el.innerText || el.textContent || '').trim(),
            };
        """, elem)
        return result if isinstance(result, dict) else {}
    except Exception:
        return {}


def has_request_search_loading_overlay(driver):
    html_class, body_class = document_class_names(driver)
    combined_classes = f"{html_class} {body_class}".lower()
    if any(token in combined_classes for token in ["nprogress-busy", "ant-spin-blur", "loading", "spinner"]):
        return True

    try:
        nprogress_busy = driver.execute_script("""
            return !!document.querySelector(
                '#nprogress, .nprogress-busy, .pace-active, .pace-running, .ant-spin-spinning'
            );
        """)
        if nprogress_busy:
            return True
    except Exception:
        pass

    selectors = [
        (By.XPATH, "//*[contains(@class, 'ant-spin-spinning') and not(contains(@style, 'display: none'))]"),
        (By.XPATH, "//*[contains(@class, 'ant-spin-blur')]"),
        (By.XPATH, "//*[@aria-busy='true']"),
        (By.XPATH, "//*[contains(@class, 'loading') or contains(@class, 'spinner')]"),
    ]
    for by, selector in selectors:
        for elem in find_visible_all(driver, by, selector):
            if element_has_visible_rect(driver, elem):
                return True
    return False


def has_no_data_placeholder(driver):
    selectors = [
        (By.XPATH, "//*[contains(@class, 'ant-empty') and contains(., 'No Data')]"),
        (By.XPATH, "//td[contains(., 'No Data')]"),
        (By.XPATH, "//*[contains(@class, 'ant-table-placeholder') and contains(., 'No Data')]"),
    ]
    for by, selector in selectors:
        if find_visible_all(driver, by, selector):
            return True
    return False


def is_processed_tab_active(driver, tab=None):
    tab_state = element_state_snapshot(driver, tab) if tab is not None else {}
    class_name = str(tab_state.get("className") or "").lower()
    aria_selected = str(tab_state.get("ariaSelected") or "").lower()
    if aria_selected == "true" or "active" in class_name or "selected" in class_name:
        return True

    if page_contains_text(driver, "CONFIRMED") and page_contains_text(driver, "BACK"):
        return True

    selectors = [
        (By.XPATH, "//*[@role='tab' and contains(., 'Processed') and (@aria-selected='true' or contains(@class, 'active') or contains(@class, 'selected'))]"),
        (By.XPATH, "//*[contains(@class, 'active') and contains(., 'Processed')]"),
        (By.XPATH, "//*[normalize-space()='CONFIRMED']"),
        (By.XPATH, "//*[normalize-space()='BACK']"),
    ]
    for by, selector in selectors:
        if find_visible_all(driver, by, selector):
            return True
    return False


def wait_for_processed_tab_ready(driver, tab=None):
    started = time.monotonic()
    deadline = started + PROCESSED_TAB_MAX_WAIT_SECONDS
    overlay_seen = False

    while True:
        if has_request_search_loading_overlay(driver):
            overlay_seen = True
        if is_processed_tab_active(driver, tab=tab):
            if PROCESSED_TAB_SETTLE_SECONDS > 0:
                sleep_step(PROCESSED_TAB_SETTLE_SECONDS)
            return {
                "overlay_seen": overlay_seen,
                "elapsed": time.monotonic() - started,
            }

        if time.monotonic() >= deadline:
            break
        sleep_step(PROCESSED_TAB_POLL_INTERVAL_SECONDS)

    raise RuntimeError(
        f"processed tab readiness timeout (overlay_seen={overlay_seen})"
    )


def wait_for_active_modal(driver, timeout=MODAL_READY_MAX_WAIT_SECONDS):
    deadline = time.monotonic() + timeout
    while True:
        for by, selector in ACTIVE_MODAL_SELECTORS:
            elems = find_visible_all(driver, by, selector)
            for elem in elems:
                if element_has_visible_rect(driver, elem):
                    return elem
        if time.monotonic() >= deadline:
            break
        sleep_step(MODAL_READY_POLL_INTERVAL_SECONDS)

    raise TimeoutException("timeout while waiting active modal")


def resolve_request_search_state(driver, request_id):
    row_xpath = build_request_row_xpath(request_id)
    rows = find_visible_all(driver, By.XPATH, row_xpath)
    if rows:
        return {"state": "matched", "rows": len(rows)}

    fallback_rows = find_visible_result_rows(driver)
    if len(fallback_rows) == 1 and page_contains_text(driver, request_id):
        return {"state": "matched_fallback", "rows": 1}

    if has_no_data_placeholder(driver):
        return {"state": "no_data", "rows": 0}

    if has_request_search_loading_overlay(driver):
        return {"state": "loading", "rows": len(fallback_rows)}

    return {"state": "pending", "rows": len(fallback_rows)}


def wait_for_request_search_result(driver, request_id):
    started = time.monotonic()
    deadline = started + REQUEST_SEARCH_MAX_WAIT_SECONDS
    overlay_seen = False
    last_state = {"state": "pending", "rows": 0}

    while True:
        current_state = resolve_request_search_state(driver, request_id)
        last_state = current_state
        state_name = current_state["state"]

        if state_name == "loading":
            if not overlay_seen:
                overlay_seen = True
                log("[search] loading overlay detected after Search click")
        elif state_name in {"matched", "matched_fallback", "no_data"}:
            if REQUEST_SEARCH_RESULT_SETTLE_SECONDS > 0:
                sleep_step(REQUEST_SEARCH_RESULT_SETTLE_SECONDS)
            return {
                "state": state_name,
                "overlay_seen": overlay_seen,
                "elapsed": time.monotonic() - started,
                "rows": current_state.get("rows", 0),
            }

        if time.monotonic() >= deadline:
            break
        sleep_step(REQUEST_SEARCH_POLL_INTERVAL_SECONDS)

    reason = (
        "request search timed out after loading overlay"
        if overlay_seen
        else "request search timed out without loading overlay"
    )
    raise RuntimeError(
        f"{reason}: request_id={request_id}, "
        f"last_state={last_state.get('state')}, visible_rows={last_state.get('rows', 0)}"
    )


def click_action_for_request_id(driver, request_id, action_labels, wait_for_modal=False):
    rows = wait_for_request_rows(driver, request_id)

    action_name = "/".join(action_labels)
    action_xpath = build_action_xpath(".", action_labels)
    for row in rows:
        action_buttons = find_visible_all(row, By.XPATH, action_xpath)
        for action_btn in action_buttons:
            try:
                if action_btn.is_enabled():
                    safe_click(
                        driver,
                        action_btn,
                        label=f"{action_name} for request {request_id}",
                    )
                    modal = None
                    if wait_for_modal:
                        modal = wait_for_active_modal(driver, timeout=MODAL_READY_MAX_WAIT_SECONDS)
                        if POST_CONFIRM_DELAY_SECONDS > 0:
                            sleep_step(POST_CONFIRM_DELAY_SECONDS)
                    save_shot(driver, f"after_{action_name.lower().replace('/', '_')}_click")
                    return modal
            except Exception:
                continue

    visible_action_candidates = find_fixed_action_buttons(driver, rows, action_labels)
    if len(rows) == 1 and len(visible_action_candidates) == 1:
        action_btn = visible_action_candidates[0]
        log(f"{action_name} button is in a fixed action column; using single-result fallback")
        safe_click(
            driver,
            action_btn,
            label=f"{action_name} fixed action for request {request_id}",
        )
        modal = None
        if wait_for_modal:
            modal = wait_for_active_modal(driver, timeout=MODAL_READY_MAX_WAIT_SECONDS)
            if POST_CONFIRM_DELAY_SECONDS > 0:
                sleep_step(POST_CONFIRM_DELAY_SECONDS)
        save_shot(driver, f"after_{action_name.lower().replace('/', '_')}_click")
        return modal

    raise RuntimeError(f"{action_name} button not found in Request ID row: {request_id}")


def click_edit_for_request_id(driver, request_id, wait_for_modal=False):
    return click_action_for_request_id(driver, request_id, ("Edit",), wait_for_modal=wait_for_modal)


def click_registration_action_for_request_id(driver, request_id, registration_mode, wait_for_modal=False):
    mode = str(registration_mode).strip().lower()
    action_labels = ("Confirm",) if mode == "new" else ("Edit",)
    return click_action_for_request_id(driver, request_id, action_labels, wait_for_modal=wait_for_modal)


def get_active_modal(driver, timeout=10):
    return wait_for_active_modal(driver, timeout=timeout)


def wait_for_input_value(driver, elem, expected_value, timeout=FIELD_VALUE_MAX_WAIT_SECONDS):
    expected = str(expected_value or "").strip()
    deadline = time.monotonic() + timeout
    while True:
        try:
            current = (elem.get_attribute("value") or "").strip()
        except Exception:
            current = ""
        if current == expected:
            return True
        if time.monotonic() >= deadline:
            break
        sleep_step(FIELD_VALUE_POLL_INTERVAL_SECONDS)
    return False


def fill_worker_login_id(driver, modal, worker_login_id):
    log(f"Worker Login ID 입력: {worker_login_id}")

    worker_input = find_first(modal, [
        (By.CSS_SELECTOR, "input[id$='-workerLoginId']"),
        (By.XPATH, ".//label[normalize-space()='ID' or normalize-space()='Worker Login ID']/following::input[1]"),
        (By.XPATH, ".//input[contains(@placeholder, 'Worker Login ID')]"),
        (By.XPATH, ".//input[contains(@placeholder, 'ID')]"),
    ], timeout=10, visible=True)

    safe_click(driver, worker_input)
    clear_and_type(worker_input, worker_login_id)
    if not wait_for_input_value(driver, worker_input, worker_login_id):
        js_set_value(driver, worker_input, worker_login_id)
        if not wait_for_input_value(driver, worker_input, worker_login_id):
            raise RuntimeError("worker_login_id value was not applied to the modal input")
    save_shot(driver, "worker_id_filled")


def click_search_driver(driver, modal):
    btn = find_first(modal, [
        (By.XPATH, ".//button[contains(., 'Search Driver')]"),
        (By.XPATH, ".//span[contains(., 'Search Driver')]/ancestor::button[1]"),
    ], timeout=10, clickable=True)

    log(f"[timing] Search Driver stabilize target={SEARCH_DRIVER_STABILIZE_SECONDS}s")
    safe_click(
        driver,
        btn,
        label="Search Driver",
    )
    sleep_step(SEARCH_DRIVER_STABILIZE_SECONDS)
    save_shot(driver, "after_search_driver")


def _first_visible_input_value(scope, selectors):
    for by, selector in selectors:
        try:
            elems = scope.find_elements(by, selector)
        except Exception:
            continue
        for elem in elems:
            try:
                if elem.is_displayed():
                    return (elem.get_attribute("value") or "").strip()
            except Exception:
                continue
    return ""


def _lookup_driver_contact_values(modal):
    name_val = ""
    phone_val = ""

    name_val = _first_visible_input_value(modal, [
        (By.XPATH, ".//label[contains(., 'Worker Name')]/following::input[1]"),
        (By.XPATH, ".//label[contains(., 'Name')]/following::input[1]"),
    ])
    phone_val = _first_visible_input_value(modal, [
        (By.XPATH, ".//label[contains(., 'Phone Number')]/following::input[1]"),
        (By.XPATH, ".//label[contains(., 'Phone')]/following::input[1]"),
    ])

    return name_val, phone_val


def check_driver_lookup(driver, modal):
    deadline = time.monotonic() + DRIVER_LOOKUP_MAX_WAIT_SECONDS
    while True:
        fail = find_all(modal, [
            (By.XPATH, ".//*[contains(text(), 'Failed to fetch Worker Details.')]"),
        ])
        if fail:
            return False, "Failed to fetch Worker Details."

        name_val, phone_val = _lookup_driver_contact_values(modal)
        if name_val or phone_val:
            return True, ""

        if time.monotonic() >= deadline:
            break
        sleep_step(DRIVER_LOOKUP_POLL_INTERVAL_SECONDS)

    return False, "이름/전화번호 자동채움 확인 실패"


def fill_plate_number(driver, modal, plate_number):
    log(f"Plate Number 입력: {plate_number}")

    plate_input = find_first(modal, [
        (By.CSS_SELECTOR, "input[id$='-plateNumber']"),
        (By.XPATH, ".//label[contains(., 'Plate Number')]/following::input[1]"),
        (By.XPATH, ".//input[contains(@placeholder, 'Plate Number')]"),
    ], timeout=10, visible=True)

    safe_click(driver, plate_input)
    clear_and_type(plate_input, plate_number)
    if not wait_for_input_value(driver, plate_input, plate_number):
        js_set_value(driver, plate_input, plate_number)
        if not wait_for_input_value(driver, plate_input, plate_number):
            raise RuntimeError("plate_number value was not applied to the modal input")
    save_shot(driver, "plate_filled")


def ensure_switch_enabled(driver, switch_elem, label="switch"):
    checked = str(switch_elem.get_attribute("aria-checked") or "").strip().lower()
    if checked == "true":
        log(f"{label} already enabled")
        return False
    if checked != "false":
        raise RuntimeError(f"{label} aria-checked is not readable: {checked}")

    safe_click(
        driver,
        switch_elem,
        label=label,
        state_probe=lambda _driver: str(switch_elem.get_attribute("aria-checked") or "").strip().lower(),
        state_change_timeout=4,
        require_change=True,
    )
    sleep_step(0.7)
    checked_after = str(switch_elem.get_attribute("aria-checked") or "").strip().lower()
    if checked_after != "true":
        raise RuntimeError(f"{label} did not become enabled after click")
    return True


def ensure_driver_public_enabled(driver, modal):
    log("Ensure Driver Public enabled")

    public_elem = find_first(modal, [
        (By.CSS_SELECTOR, "[role='switch'][id$='-driverPublic']"),
        (By.CSS_SELECTOR, "[id$='-driverPublic']"),
        (
            By.XPATH,
            ".//*[normalize-space()='Driver Public']/ancestor::*[self::label or self::div][1]"
            "//*[(@role='switch') or contains(@id, 'driverPublic')]",
        ),
        (By.XPATH, ".//*[@role='switch' and contains(@id, 'driverPublic')]"),
    ], timeout=10, clickable=True)

    clicked = ensure_switch_enabled(driver, public_elem, label="Driver Public")
    if clicked:
        save_shot(driver, "driver_public_enabled")


def _is_modal_closed(modal):
    try:
        return not modal.is_displayed()
    except StaleElementReferenceException:
        return True
    except Exception:
        return False


def _visible_texts(scope, selectors):
    texts = []
    for by, selector in selectors:
        for elem in find_visible_all(scope, by, selector):
            text = (elem.text or "").strip()
            if text:
                texts.append(text)
    return texts


def click_save(driver, modal):
    save_btn = find_first(modal, [
        (By.XPATH, ".//button[contains(., 'Save')]"),
        (By.XPATH, ".//span[contains(., 'Save')]/ancestor::button[1]"),
    ], timeout=10, clickable=True)

    safe_click(
        driver,
        save_btn,
        label="Save",
        state_probe=modal_state_fingerprint,
        state_change_timeout=6,
    )
    deadline = time.monotonic() + SAVE_RESULT_MAX_WAIT_SECONDS
    while True:
        if _is_modal_closed(modal):
            return True, ""

        success_texts = _visible_texts(driver, [
            (By.XPATH, "//*[contains(@class, 'message') and (contains(., 'Success') or contains(., 'success') or contains(., 'Saved') or contains(., 'saved'))]"),
            (By.XPATH, "//*[contains(@class, 'toast') and (contains(., 'Success') or contains(., 'success') or contains(., 'Saved') or contains(., 'saved'))]"),
        ])
        if success_texts:
            return True, ""

        error_texts = _visible_texts(modal, [
            (By.XPATH, ".//*[contains(@class, 'error') or contains(@class, 'Error')]"),
            (By.XPATH, ".//*[contains(., 'Failed') or contains(., 'failed') or contains(., 'Error') or contains(., 'error')]"),
        ])
        if error_texts:
            save_shot(driver, "save_failed_popup_still_open")
            return False, "; ".join(dict.fromkeys(error_texts))

        if time.monotonic() >= deadline:
            break
        sleep_step(SAVE_RESULT_POLL_INTERVAL_SECONDS)

    save_shot(driver, "save_failed_popup_still_open")
    return False, "save failed: modal still open and no success confirmation"


def process_one(driver, row, progress_callback=None):
    request_id = str(row["request_id"]).strip()
    registration_mode = str(row["registration_mode"]).strip().lower()
    order_date = str(row["order_date"]).strip()
    worker_login_id = str(row["worker_login_id"]).strip()
    plate_number = str(row["plate_number"]).strip()
    reset_click_diagnostics()

    def progress(event, stage, reason="", error=""):
        emit_progress(
            progress_callback,
            event=event,
            stage=stage,
            request_id=request_id,
            reason=str(reason or "").strip(),
            error=str(error or "").strip(),
        )

    def result(status, reason=""):
        return {
            "request_id": request_id,
            "status": status,
            "reason": reason,
            "registration_mode": registration_mode,
            "order_date": order_date,
            "worker_login_id": worker_login_id,
            "plate_number": plate_number,
        }

    if registration_mode not in {"new", "modify"}:
        raise ValueError(f"registration_mode 값 오류: {registration_mode}")

    progress("stage", "searching")
    go_to_truck_dispatch(driver)

    select_registration_tab(driver, registration_mode)
    search_request_id(driver, request_id)
    progress("stage", "clicking")
    modal = click_registration_action_for_request_id(driver, request_id, registration_mode, wait_for_modal=True)

    progress("stage", "waiting_response")
    fill_worker_login_id(driver, modal, worker_login_id)
    click_search_driver(driver, modal)

    progress("stage", "verifying")
    ok, reason = check_driver_lookup(driver, modal)
    if not ok:
        save_shot(driver, f"driver_lookup_failed_{request_id}")
        progress("row_failed", "failed", reason=reason)
        return result("fail", reason)

    fill_plate_number(driver, modal, plate_number)
    ensure_driver_public_enabled(driver, modal)

    progress("stage", "clicking")
    saved, save_reason = click_save(driver, modal)
    if not saved:
        progress("row_failed", "failed", reason=save_reason or "save failed")
        return result("fail", save_reason or "save failed")

    progress("row_saved", "saved")
    return result("success", "")


def prepare_assignments_df(df):
    for col in ASSIGN_INPUT_REQUIRED_COLUMNS:
        if col not in df.columns:
            raise ValueError(f"missing required column: {col}")

    work_df = df.copy()
    work_df["worker_login_id"] = work_df["worker_login_id"].astype(str).str.strip()
    work_df["plate_number"] = work_df["plate_number"].astype(str).str.strip()
    work_df["request_id"] = work_df["request_id"].astype(str).str.strip()

    return work_df[
        (work_df["request_id"] != "")
        & (work_df["worker_login_id"] != "")
        & (work_df["plate_number"] != "")
    ].copy()


def _result_counts(results):
    success_count = sum(1 for row in results if str(row.get("status", "")).strip() == "success")
    failure_count = len(results) - success_count
    return success_count, failure_count


def _write_results_file(results, result_file):
    if result_file:
        pd.DataFrame(results).reindex(columns=RESULT_COLUMNS).to_csv(
            result_file,
            index=False,
            encoding="utf-8-sig",
        )


def run_assignments_df(
    df,
    result_file=RESULT_FILE,
    progress_callback=None,
    progress_interval=5,
    raise_on_abort=True,
):
    work_df = prepare_assignments_df(df)
    total_count = len(work_df)
    if len(work_df) == 0:
        emit_progress(
            progress_callback,
            event="completed",
            stage="saved",
            total=0,
            completed=0,
            success_count=0,
            failure_count=0,
        )
        return pd.DataFrame(columns=RESULT_COLUMNS)

    emit_progress(
        progress_callback,
        event="start",
        stage="queued",
        total=total_count,
        completed=0,
        success_count=0,
        failure_count=0,
    )
    driver = None
    results = []

    try:
        driver = build_driver()
        emit_progress(
            progress_callback,
            event="stage",
            stage="searching",
            total=total_count,
            completed=0,
            success_count=0,
            failure_count=0,
        )
        login(driver)

        for row_index, row in work_df.reset_index(drop=True).iterrows():
            request_id = str(row.get("request_id", "")).strip()
            emit_progress(
                progress_callback,
                event="row_start",
                stage="queued",
                request_id=request_id,
                row_index=row_index + 1,
                total=total_count,
                completed=len(results),
                success_count=_result_counts(results)[0],
                failure_count=_result_counts(results)[1],
            )
            try:
                log("=" * 70)
                log(f"Process start: request_id={request_id}")
                result = process_one(driver, row, progress_callback=progress_callback)
            except Exception as e:
                traceback.print_exc()
                save_shot(driver, f"exception_{request_id}")
                emit_progress(
                    progress_callback,
                    event="row_failed",
                    stage="failed",
                    request_id=request_id,
                    row_index=row_index + 1,
                    total=total_count,
                    completed=len(results),
                    success_count=_result_counts(results)[0],
                    failure_count=_result_counts(results)[1],
                    error=str(e),
                )
                result = {
                    "request_id": request_id,
                    "status": "error",
                    "reason": str(e),
                    "registration_mode": str(row.get("registration_mode", "")).strip(),
                    "order_date": str(row.get("order_date", "")).strip(),
                    "worker_login_id": str(row.get("worker_login_id", "")).strip(),
                    "plate_number": str(row.get("plate_number", "")).strip(),
                }

            results.append(result)
            _write_results_file(results, result_file)
            success_count, failure_count = _result_counts(results)
            row_stage = "saved" if str(result.get("status", "")).strip() == "success" else "failed"
            emit_progress(
                progress_callback,
                event="row_done",
                stage=row_stage,
                request_id=request_id,
                row_index=row_index + 1,
                total=total_count,
                completed=len(results),
                success_count=success_count,
                failure_count=failure_count,
                reason=str(result.get("reason", "") or "").strip(),
            )
            if progress_interval and len(results) < total_count and len(results) % int(progress_interval) == 0:
                emit_progress(
                    progress_callback,
                    event="progress",
                    stage=row_stage,
                    request_id=request_id,
                    row_index=row_index + 1,
                    total=total_count,
                    completed=len(results),
                    success_count=success_count,
                    failure_count=failure_count,
                    reason=str(result.get("reason", "") or "").strip(),
                )
            sleep_step(1)

        success_count, failure_count = _result_counts(results)
        emit_progress(
            progress_callback,
            event="completed",
            stage="saved" if failure_count == 0 else "failed",
            total=total_count,
            completed=len(results),
            success_count=success_count,
            failure_count=failure_count,
        )
        return pd.DataFrame(results).reindex(columns=RESULT_COLUMNS)

    except Exception as exc:
        traceback.print_exc()
        success_count, failure_count = _result_counts(results)
        emit_progress(
            progress_callback,
            event="aborted",
            stage="failed",
            total=total_count,
            completed=len(results),
            success_count=success_count,
            failure_count=failure_count,
            error=str(exc),
        )
        if raise_on_abort:
            raise
        return pd.DataFrame(results).reindex(columns=RESULT_COLUMNS)

    finally:
        if driver is not None and not DETACH_BROWSER:
            try:
                driver.quit()
            except Exception as exc:
                log(f"driver quit failed: {exc}")


def _load_first_debug_row(file_path):
    if not file_path:
        raise ValueError("debug input file is required for --debug-search-first-row")
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"debug input file not found: {file_path}")
    df = pd.read_csv(file_path, dtype=str).fillna("")
    work_df = prepare_assignments_df(df)
    if len(work_df) == 0:
        raise ValueError("debug input file has no runnable rows")
    return work_df.reset_index(drop=True).iloc[0]


def debug_login_flow(
    file_path=None,
    request_id="",
    registration_mode="new",
    search_first_row=False,
    confirm_only=False,
    driver_lookup_only=False,
    worker_login_id="",
    keep_browser=False,
):
    driver = None
    try:
        driver = build_driver()
        debug_snapshot(driver, "debug flow start")
        login(driver)
        debug_snapshot(driver, "debug after truckDispatch ready", include_source=True)

        worker_login_id = str(worker_login_id or "").strip()
        plate_number = ""
        if search_first_row:
            row = _load_first_debug_row(file_path)
            request_id = str(row.get("request_id", "") or "").strip()
            registration_mode = str(row.get("registration_mode", registration_mode) or registration_mode).strip()
            if not worker_login_id:
                worker_login_id = str(row.get("worker_login_id", "") or "").strip()
            plate_number = str(row.get("plate_number", "") or "").strip()
            log(
                "[debug] first row loaded: "
                f"request_id={request_id}, registration_mode={registration_mode}, worker_login_id={worker_login_id}"
            )

        mode = str(registration_mode or "new").strip().lower() or "new"
        if mode in {"new", "modify"}:
            log(f"[debug] selecting registration mode={mode}")
            select_registration_tab(driver, mode)

        if request_id:
            log(f"[debug] searching request_id={request_id}")
            search_request_id(driver, request_id)
            debug_snapshot(driver, "debug after request search", include_source=True)
            if confirm_only or driver_lookup_only:
                log("[debug] opening assignment action modal only; Save will not be clicked")
                modal = click_registration_action_for_request_id(driver, request_id, mode, wait_for_modal=True)
                debug_snapshot(driver, "debug after confirm action modal", include_source=True)
                if driver_lookup_only:
                    if not worker_login_id:
                        raise ValueError("debug driver lookup requires worker_login_id from the input row")
                    fill_worker_login_id(driver, modal, worker_login_id)
                    click_search_driver(driver, modal)
                    ok, reason = check_driver_lookup(driver, modal)
                    log(f"[debug] driver lookup result: ok={ok}, reason={reason}")
                    debug_snapshot(driver, "debug after driver lookup", include_source=True)
                    if not ok:
                        raise RuntimeError(f"driver lookup failed: {reason}")

        debug_snapshot(driver, "debug flow completed", include_source=True)
        return 0
    except Exception as exc:
        log(f"[debug] debug_login_flow failed: {exc}")
        traceback.print_exc()
        if driver is not None:
            debug_snapshot(driver, "debug flow failed", include_source=True, selectors=TRUCK_DISPATCH_READY_SELECTORS)
        return 1
    finally:
        if driver is not None and not keep_browser:
            try:
                driver.quit()
            except Exception as exc:
                log(f"driver quit failed: {exc}")


def main():
    parser = argparse.ArgumentParser(description="Run or diagnose Coupang LS truck dispatch assignment.")
    parser.add_argument("file_path", nargs="?", default="route_assignment_new.csv")
    parser.add_argument("--debug-login", action="store_true", help="Only verify login -> truckDispatch readiness.")
    parser.add_argument("--debug-search-first-row", action="store_true", help="In debug mode, search the first runnable input row only.")
    parser.add_argument("--debug-request-id", default="", help="In debug mode, search this request_id only.")
    parser.add_argument("--debug-registration-mode", default="new", choices=["new", "modify"])
    parser.add_argument("--debug-confirm-only", action="store_true", help="In debug mode, open the Confirm/Edit modal but do not save.")
    parser.add_argument("--debug-driver-lookup-only", action="store_true", help="In debug mode, open the modal and run Search Driver but do not save.")
    parser.add_argument("--debug-worker-login-id", default="", help="In debug mode, worker_login_id for Search Driver lookup.")
    parser.add_argument("--keep-browser", action="store_true", help="Leave the browser open after debug mode.")
    args = parser.parse_args()

    if args.debug_login:
        raise SystemExit(debug_login_flow(
            file_path=args.file_path,
            request_id=args.debug_request_id,
            registration_mode=args.debug_registration_mode,
            search_first_row=args.debug_search_first_row,
            confirm_only=args.debug_confirm_only,
            driver_lookup_only=args.debug_driver_lookup_only,
            worker_login_id=args.debug_worker_login_id,
            keep_browser=args.keep_browser,
        ))

    file_path = args.file_path

    if not os.path.exists(file_path):
        raise FileNotFoundError(f"파일이 없습니다: {file_path}")

    df = pd.read_csv(file_path, dtype=str).fillna("")

    results_df = run_assignments_df(df, result_file=RESULT_FILE)
    if len(results_df) == 0:
        print("No rows to process.")
    else:
        print(results_df)
    return


if __name__ == "__main__":
    main()
