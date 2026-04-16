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

SCREENSHOT_DIR = "bot_screenshots"
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

# 브라우저 보이게
HEADLESS = False

# 세션 종료 후 브라우저 창 유지
DETACH_BROWSER = True

STEP_SLEEP = 1.0
CLICK_DIAGNOSTICS = {
    "retry_count": 0,
    "last_reason": "",
}

Path(SCREENSHOT_DIR).mkdir(exist_ok=True)


def log(msg):
    print(f"[LOG] {msg}")


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
    driver.save_screenshot(path)
    log(f"스크린샷 저장: {path}")


def sleep_step(sec=STEP_SLEEP):
    time.sleep(sec)


def build_driver():
    options = Options()
    if HEADLESS:
        options.add_argument("--headless=new")

    options.add_argument("--start-maximized")
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
    if "login" in current_url or "welcome" in current_url:
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

    driver.get(LOGIN_URL)
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
    sleep_step(3)

    # 로그인 후 웰컴페이지가 떠서 트럭디스패치로 재이동
    progress("stage", "searching")
    driver.get(TRUCK_DISPATCH_URL)
    sleep_step(3)
    save_shot(driver, "after_login")


def set_order_date(driver, order_date):
    log(f"날짜 설정: {order_date}")

    date_input = find_first(driver, [
        (By.XPATH, "//label[contains(., 'Order Date')]/following::input[1]"),
        (By.XPATH, "//input[contains(@placeholder, 'Order Date')]"),
        (By.XPATH, "//input[contains(@class, 'calendar')]"),
        (By.XPATH, "(//input)[1]"),
    ], timeout=10, visible=True)

    safe_click(driver, date_input)
    sleep_step(0.5)

    # 텍스트 입력
    try:
        clear_and_type(date_input, order_date)
        sleep_step(0.5)
    except Exception:
        pass

    # JS 강제 입력
    try:
        js_set_value(driver, date_input, order_date)
        sleep_step(0.5)
    except Exception:
        pass

    press_escape(driver)
    sleep_step(0.5)
    save_shot(driver, "date_set")


def click_search(driver):
    search_btn = find_first(driver, [
        (By.XPATH, "//button[contains(., 'Search')]"),
        (By.XPATH, "//span[contains(., 'Search')]/ancestor::button[1]"),
    ], timeout=10, clickable=True)

    safe_click(
        driver,
        search_btn,
        label="Search",
        state_probe=page_state_fingerprint,
        state_change_timeout=5,
    )
    sleep_step(2)
    save_shot(driver, "after_search")


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
        state_probe=page_state_fingerprint,
        state_change_timeout=5,
    )
    sleep_step(2)
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


def click_action_for_request_id(driver, request_id, action_labels):
    row_xpath = build_request_row_xpath(request_id)
    rows = []
    for _ in range(10):
        rows = find_visible_all(driver, By.XPATH, row_xpath)
        if rows:
            break
        sleep_step(0.5)

    if not rows:
        raise RuntimeError(f"Request ID row not found: {request_id}")

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
                        state_probe=modal_state_fingerprint,
                        state_change_timeout=6,
                    )
                    sleep_step(2)
                    save_shot(driver, f"after_{action_name.lower().replace('/', '_')}_click")
                    return
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
            state_probe=modal_state_fingerprint,
            state_change_timeout=6,
        )
        sleep_step(2)
        save_shot(driver, f"after_{action_name.lower().replace('/', '_')}_click")
        return

    raise RuntimeError(f"{action_name} button not found in Request ID row: {request_id}")


def click_edit_for_request_id(driver, request_id):
    click_action_for_request_id(driver, request_id, ("Edit",))


def click_registration_action_for_request_id(driver, request_id, registration_mode):
    mode = str(registration_mode).strip().lower()
    action_labels = ("Confirm",) if mode == "new" else ("Edit",)
    click_action_for_request_id(driver, request_id, action_labels)


def get_active_modal(driver, timeout=10):
    return find_first(driver, [
        (By.XPATH, "//*[@role='dialog' and .//button[contains(., 'Save')]]"),
        (By.XPATH, "//*[contains(@class, 'modal') and .//button[contains(., 'Save')]]"),
        (By.XPATH, "//*[contains(@class, 'Modal') and .//button[contains(., 'Save')]]"),
    ], timeout=timeout, visible=True)


def fill_worker_login_id(driver, modal, worker_login_id):
    log(f"Worker Login ID 입력: {worker_login_id}")

    worker_input = find_first(modal, [
        (By.CSS_SELECTOR, "input[id$='-workerLoginId']"),
        (By.XPATH, ".//label[normalize-space()='ID' or normalize-space()='Worker Login ID']/following::input[1]"),
        (By.XPATH, ".//input[contains(@placeholder, 'Worker Login ID')]"),
        (By.XPATH, ".//input[contains(@placeholder, 'ID')]"),
    ], timeout=10, visible=True)

    safe_click(driver, worker_input)
    sleep_step(0.3)
    clear_and_type(worker_input, worker_login_id)
    sleep_step(0.5)
    save_shot(driver, "worker_id_filled")


def click_search_driver(driver, modal):
    btn = find_first(modal, [
        (By.XPATH, ".//button[contains(., 'Search Driver')]"),
        (By.XPATH, ".//span[contains(., 'Search Driver')]/ancestor::button[1]"),
    ], timeout=10, clickable=True)

    safe_click(
        driver,
        btn,
        label="Search Driver",
        state_probe=page_state_fingerprint,
        state_change_timeout=5,
    )
    sleep_step(2)
    save_shot(driver, "after_search_driver")


def check_driver_lookup(driver, modal):
    fail = find_all(modal, [
        (By.XPATH, ".//*[contains(text(), 'Failed to fetch Worker Details.')]"),
    ])
    if fail:
        return False, "Failed to fetch Worker Details."

    name_val = ""
    phone_val = ""

    try:
        name_input = find_first(modal, [
            (By.XPATH, ".//label[contains(., 'Worker Name')]/following::input[1]"),
            (By.XPATH, ".//label[contains(., 'Name')]/following::input[1]"),
        ], timeout=3, visible=True)
        name_val = (name_input.get_attribute("value") or "").strip()
    except Exception:
        pass

    try:
        phone_input = find_first(modal, [
            (By.XPATH, ".//label[contains(., 'Phone Number')]/following::input[1]"),
            (By.XPATH, ".//label[contains(., 'Phone')]/following::input[1]"),
        ], timeout=3, visible=True)
        phone_val = (phone_input.get_attribute("value") or "").strip()
    except Exception:
        pass

    if name_val or phone_val:
        return True, ""

    sleep_step(1.5)

    fail = find_all(modal, [
        (By.XPATH, ".//*[contains(text(), 'Failed to fetch Worker Details.')]"),
    ])
    if fail:
        return False, "Failed to fetch Worker Details."

    return False, "이름/전화번호 자동채움 확인 실패"


def fill_plate_number(driver, modal, plate_number):
    log(f"Plate Number 입력: {plate_number}")

    plate_input = find_first(modal, [
        (By.CSS_SELECTOR, "input[id$='-plateNumber']"),
        (By.XPATH, ".//label[contains(., 'Plate Number')]/following::input[1]"),
        (By.XPATH, ".//input[contains(@placeholder, 'Plate Number')]"),
    ], timeout=10, visible=True)

    safe_click(driver, plate_input)
    sleep_step(0.3)
    clear_and_type(plate_input, plate_number)
    sleep_step(0.5)
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
    sleep_step(2)

    try:
        WebDriverWait(driver, 8).until(lambda _driver: _is_modal_closed(modal))
        return True, ""
    except TimeoutException:
        pass

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

    driver.get(TRUCK_DISPATCH_URL)
    sleep_step(2)

    set_order_date(driver, order_date)
    click_search(driver)
    select_registration_tab(driver, registration_mode)
    search_request_id(driver, request_id)
    progress("stage", "clicking")
    click_registration_action_for_request_id(driver, request_id, registration_mode)

    progress("stage", "waiting_response")
    modal = get_active_modal(driver)
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
            driver.quit()


def main():
    file_path = sys.argv[1] if len(sys.argv) >= 2 else "route_assignment_new.csv"

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
