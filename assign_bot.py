import os
import sys
import time
import traceback
from pathlib import Path

import pandas as pd
from selenium import webdriver
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

# 환경변수로 넣기
COUPANG_ID = os.getenv("COUPANG_ID", "")
COUPANG_PW = os.getenv("COUPANG_PW", "")

# 비워두면 시스템 PATH의 chromedriver 사용
CHROMEDRIVER_PATH = ""

# 브라우저 보이게
HEADLESS = False

# 세션 종료 후 브라우저 창 유지
DETACH_BROWSER = True

STEP_SLEEP = 1.0

Path(SCREENSHOT_DIR).mkdir(exist_ok=True)


def log(msg):
    print(f"[LOG] {msg}")


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
        except Exception as e:
            last_error = e

    raise last_error if last_error else Exception("요소를 찾지 못했습니다.")


def find_all(driver, selectors):
    for by, selector in selectors:
        elems = driver.find_elements(by, selector)
        if elems:
            return elems
    return []


def safe_click(driver, elem):
    try:
        elem.click()
    except Exception:
        driver.execute_script("arguments[0].click();", elem)


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


def login(driver):
    if not COUPANG_ID or not COUPANG_PW:
        raise ValueError("환경변수 COUPANG_ID / COUPANG_PW를 먼저 넣어주세요.")

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

    clear_and_type(id_input, COUPANG_ID)
    sleep_step(0.5)
    clear_and_type(pw_input, COUPANG_PW)
    sleep_step(0.5)

    login_btn = find_first(driver, [
        (By.XPATH, "//button[contains(., '로그인')]"),
        (By.XPATH, "//button[contains(., 'Login')]"),
        (By.CSS_SELECTOR, "button[type='submit']"),
    ], timeout=10, clickable=True)

    safe_click(driver, login_btn)
    sleep_step(3)

    # 로그인 후 웰컴페이지가 떠서 트럭디스패치로 재이동
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

    safe_click(driver, search_btn)
    sleep_step(2)
    save_shot(driver, "after_search")


def select_registration_tab(driver, registration_mode):
    target = "Pending" if str(registration_mode).strip().lower() == "new" else "Processed"
    log(f"탭 선택: {target}")

    tab = find_first(driver, [
        (By.XPATH, f"//*[contains(text(), '{target}')]"),
        (By.XPATH, f"//a[contains(., '{target}')]"),
        (By.XPATH, f"//div[contains(., '{target}')]"),
    ], timeout=10, clickable=True)

    safe_click(driver, tab)
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


def click_edit(driver):
    edit_btn = find_first(driver, [
        (By.XPATH, "//a[contains(., 'Edit')]"),
        (By.XPATH, "//button[contains(., 'Edit')]"),
        (By.XPATH, "//*[contains(text(), 'Edit')]"),
    ], timeout=10, clickable=True)

    safe_click(driver, edit_btn)
    sleep_step(2)
    save_shot(driver, "after_edit_click")


def fill_worker_login_id(driver, worker_login_id):
    log(f"Worker Login ID 입력: {worker_login_id}")

    worker_input = find_first(driver, [
        (By.XPATH, "//label[contains(., 'Worker Login ID')]/following::input[1]"),
        (By.XPATH, "//input[contains(@placeholder, 'Worker Login ID')]"),
    ], timeout=10, visible=True)

    safe_click(driver, worker_input)
    sleep_step(0.3)
    clear_and_type(worker_input, worker_login_id)
    sleep_step(0.5)
    save_shot(driver, "worker_id_filled")


def click_search_driver(driver):
    btn = find_first(driver, [
        (By.XPATH, "//button[contains(., 'Search Driver')]"),
        (By.XPATH, "//span[contains(., 'Search Driver')]/ancestor::button[1]"),
    ], timeout=10, clickable=True)

    safe_click(driver, btn)
    sleep_step(2)
    save_shot(driver, "after_search_driver")


def check_driver_lookup(driver):
    fail = find_all(driver, [
        (By.XPATH, "//*[contains(text(), 'Failed to fetch Worker Details.')]"),
    ])
    if fail:
        return False, "Failed to fetch Worker Details."

    name_val = ""
    phone_val = ""

    try:
        name_input = find_first(driver, [
            (By.XPATH, "//label[contains(., 'Worker Name')]/following::input[1]"),
            (By.XPATH, "//label[contains(., 'Name')]/following::input[1]"),
        ], timeout=3, visible=True)
        name_val = (name_input.get_attribute("value") or "").strip()
    except Exception:
        pass

    try:
        phone_input = find_first(driver, [
            (By.XPATH, "//label[contains(., 'Phone Number')]/following::input[1]"),
            (By.XPATH, "//label[contains(., 'Phone')]/following::input[1]"),
        ], timeout=3, visible=True)
        phone_val = (phone_input.get_attribute("value") or "").strip()
    except Exception:
        pass

    if name_val or phone_val:
        return True, ""

    sleep_step(1.5)

    fail = find_all(driver, [
        (By.XPATH, "//*[contains(text(), 'Failed to fetch Worker Details.')]"),
    ])
    if fail:
        return False, "Failed to fetch Worker Details."

    return False, "이름/전화번호 자동채움 확인 실패"


def fill_plate_number(driver, plate_number):
    log(f"Plate Number 입력: {plate_number}")

    plate_input = find_first(driver, [
        (By.XPATH, "//label[contains(., 'Plate Number')]/following::input[1]"),
        (By.XPATH, "//input[contains(@placeholder, 'Plate Number')]"),
    ], timeout=10, visible=True)

    safe_click(driver, plate_input)
    sleep_step(0.3)
    clear_and_type(plate_input, plate_number)
    sleep_step(0.5)
    save_shot(driver, "plate_filled")


def click_driver_public_if_new(driver, registration_mode):
    if str(registration_mode).strip().lower() != "new":
        log("modify 모드 -> Driver Public 건드리지 않음")
        return

    log("new 모드 -> Driver Public 클릭")

    public_elem = find_first(driver, [
        (By.XPATH, "//*[contains(text(), 'Driver Public')]"),
        (By.XPATH, "//label[contains(., 'Driver Public')]"),
    ], timeout=10, clickable=True)

    safe_click(driver, public_elem)
    sleep_step(0.7)
    save_shot(driver, "driver_public_clicked")


def click_save(driver):
    save_btn = find_first(driver, [
        (By.XPATH, "//button[contains(., 'Save')]"),
        (By.XPATH, "//span[contains(., 'Save')]/ancestor::button[1]"),
    ], timeout=10, clickable=True)

    safe_click(driver, save_btn)
    sleep_step(2)

    # 팝업이 닫히면 성공으로 판단
    try:
        WebDriverWait(driver, 10).until_not(
            EC.presence_of_element_located((By.XPATH, "//button[contains(., 'Save')]"))
        )
        return True
    except Exception:
        save_shot(driver, "save_failed_popup_still_open")
        return False


def process_one(driver, row):
    request_id = str(row["request_id"]).strip()
    registration_mode = str(row["registration_mode"]).strip().lower()
    order_date = str(row["order_date"]).strip()
    worker_login_id = str(row["worker_login_id"]).strip()
    plate_number = str(row["plate_number"]).strip()

    if registration_mode not in {"new", "modify"}:
        raise ValueError(f"registration_mode 값 오류: {registration_mode}")

    driver.get(TRUCK_DISPATCH_URL)
    sleep_step(2)

    set_order_date(driver, order_date)
    click_search(driver)
    select_registration_tab(driver, registration_mode)
    search_request_id(driver, request_id)
    click_edit(driver)

    fill_worker_login_id(driver, worker_login_id)
    click_search_driver(driver)

    ok, reason = check_driver_lookup(driver)
    if not ok:
        save_shot(driver, f"driver_lookup_failed_{request_id}")
        return {
            "request_id": request_id,
            "status": "fail",
            "reason": reason,
        }

    fill_plate_number(driver, plate_number)
    click_driver_public_if_new(driver, registration_mode)

    saved = click_save(driver)
    if not saved:
        return {
            "request_id": request_id,
            "status": "fail",
            "reason": "save failed",
        }

    return {
        "request_id": request_id,
        "status": "success",
        "reason": "",
    }


def main():
    file_path = sys.argv[1] if len(sys.argv) >= 2 else "route_assignment_new.csv"

    if not os.path.exists(file_path):
        raise FileNotFoundError(f"파일이 없습니다: {file_path}")

    df = pd.read_csv(file_path, dtype=str).fillna("")

    required_cols = [
        "registration_mode",
        "order_date",
        "request_id",
        "worker_login_id",
        "plate_number",
    ]
    for col in required_cols:
        if col not in df.columns:
            raise ValueError(f"필수 컬럼 없음: {col}")

    # 매핑이 안 된 행 제거
    work_df = df.copy()
    work_df["worker_login_id"] = work_df["worker_login_id"].astype(str).str.strip()
    work_df["plate_number"] = work_df["plate_number"].astype(str).str.strip()
    work_df["request_id"] = work_df["request_id"].astype(str).str.strip()

    work_df = work_df[
        (work_df["request_id"] != "")
        & (work_df["worker_login_id"] != "")
        & (work_df["plate_number"] != "")
    ].copy()

    if len(work_df) == 0:
        print("실행할 데이터가 없습니다.")
        return

    driver = build_driver()
    results = []

    try:
        login(driver)

        for _, row in work_df.iterrows():
            request_id = str(row.get("request_id", "")).strip()
            try:
                log("=" * 70)
                log(f"처리 시작: request_id={request_id}")
                result = process_one(driver, row)
            except Exception as e:
                traceback.print_exc()
                save_shot(driver, f"exception_{request_id}")
                result = {
                    "request_id": request_id,
                    "status": "error",
                    "reason": str(e),
                }

            results.append(result)
            pd.DataFrame(results).to_csv(RESULT_FILE, index=False, encoding="utf-8-sig")
            sleep_step(1)

        print(pd.DataFrame(results))

    finally:
        if not DETACH_BROWSER:
            driver.quit()


if __name__ == "__main__":
    main()
