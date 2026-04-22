import asyncio
import base64
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
from playwright.sync_api import sync_playwright, Page, Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError

# ------------------ CONFIG ------------------ #
st.set_page_config(page_title="Cuh AI Form Engine SaaS", page_icon="⚡", layout="wide")

PRIMARY = "#D4AF37"
PROFILE_STORE_PATH = Path("saved_autofill_profile.json")

REQUIRED_PROFILE_FIELDS = ("first_name", "last_name", "email", "phone", "address", "city", "state", "zip")

# ------------------ STREAMLIT SAFETY ------------------ #
if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

# ------------------ CAPTCHA DETECTION ------------------ #
CAPTCHA_SELECTOR = ", ".join([
    "iframe[src*='recaptcha']",
    "iframe[title*='captcha' i]",
    ".g-recaptcha",
    "#captcha",
    "[id*='captcha']",
    "[class*='captcha']",
    "[data-sitekey]",
])


# ------------------ PROFILE ------------------ #
def load_profile():
    if PROFILE_STORE_PATH.exists():
        try:
            return json.loads(PROFILE_STORE_PATH.read_text())
        except:
            return {}
    return {}


def save_profile(profile):
    PROFILE_STORE_PATH.write_text(json.dumps(profile, indent=2))


if "profile" not in st.session_state:
    st.session_state.profile = load_profile()

if "jobs" not in st.session_state:
    st.session_state.jobs = []


# ------------------ HELPERS ------------------ #
def captcha_present(page: Page) -> bool:
    try:
        return page.query_selector(CAPTCHA_SELECTOR) is not None
    except:
        return False


def wait_for_manual_captcha(page: Page, timeout=240):
    start = time.time()
    while time.time() - start < timeout:
        if not captcha_present(page):
            return True
        time.sleep(2)
    return False


def detect_field(field: dict):
    text = " ".join([
        field.get("name", ""),
        field.get("id", ""),
        field.get("placeholder", ""),
    ]).lower()

    mapping = {
        "first_name": ["first", "given"],
        "last_name": ["last", "surname"],
        "email": ["email"],
        "phone": ["phone", "mobile"],
        "address": ["address", "street"],
        "city": ["city"],
        "state": ["state"],
        "zip": ["zip", "postal"],
    }

    for key, keywords in mapping.items():
        if any(k in text for k in keywords):
            return key
    return None


def scan_fields(page: Page):
    fields = page.query_selector_all("input, textarea")
    result = []

    for f in fields:
        result.append({
            "el": f,
            "name": f.get_attribute("name") or "",
            "id": f.get_attribute("id") or "",
            "placeholder": f.get_attribute("placeholder") or "",
        })

    return result


def build_plan(schema, profile):
    plan = []
    for field in schema:
        key = detect_field(field)
        if key and profile.get(key):
            plan.append((field["el"], profile[key]))
    return plan


def click_submit(page: Page):
    buttons = page.query_selector_all("button, input[type='submit']")
    for b in buttons:
        try:
            text = (b.inner_text() or "").lower()
            if any(x in text for x in ["submit", "send", "apply", "continue", "register"]):
                b.click()
                return True
        except:
            continue
    return False


def submit_flow(page: Page):
    if not click_submit(page):
        return "no_submit"

    time.sleep(1)

    if captcha_present(page):
        return "captcha"

    return "submitted"


# ------------------ ENGINE ------------------ #
def run_engine():
    profile = st.session_state.profile
    jobs = st.session_state.jobs

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )

        try:
            context = browser.new_context()

            for i, job in enumerate(jobs, start=1):
                url = job["url"]
                job["status"] = "processing"

                page = context.new_page()

                try:
                    page.goto(url, timeout=60000)

                    schema = scan_fields(page)
                    plan = build_plan(schema, profile)

                    for element, value in plan:
                        try:
                            element.fill(str(value))
                        except:
                            pass

                    # CAPTCHA CHECK BEFORE SUBMIT
                    if captcha_present(page):
                        st.warning(f"CAPTCHA detected: {url}")
                        job["status"] = "captcha_required"

                        solved = wait_for_manual_captcha(page)

                        if not solved:
                            job["status"] = "captcha_timeout"
                            page.close()
                            continue

                    result = submit_flow(page)

                    if result == "submitted":
                        job["status"] = "completed"
                        st.success(f"Submitted: {url}")

                    elif result == "captcha":
                        job["status"] = "captcha_required"

                        solved = wait_for_manual_captcha(page)
                        if solved:
                            submit_flow(page)
                            job["status"] = "completed"
                        else:
                            job["status"] = "captcha_timeout"

                    else:
                        job["status"] = "failed"

                except PlaywrightTimeoutError:
                    job["status"] = "timeout"

                except PlaywrightError:
                    job["status"] = "error"

                finally:
                    page.close()

                time.sleep(0.2)

        finally:
            browser.close()


# ------------------ UI ------------------ #
st.title("⚡ Cuh AI Form Engine")

st.sidebar.header("Controls")

if st.sidebar.button("Start Processing"):
    if not st.session_state.jobs:
        st.warning("No jobs loaded")
    else:
        run_engine()
        st.success("Done")


uploaded = st.file_uploader("Upload CSV", type=["csv"])

if uploaded:
    df = pd.read_csv(uploaded)

    if "url" in df.columns:
        st.session_state.jobs = [
            {"url": u, "status": "queued"}
            for u in df["url"].dropna().tolist()
        ]
        st.success(f"Loaded {len(st.session_state.jobs)} jobs")

st.subheader("Jobs")
st.dataframe(pd.DataFrame(st.session_state.jobs))
