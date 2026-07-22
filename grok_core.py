"""Grok Mint core - cross-platform browser and integration services.

Provides all functions expected by grok_register_ttk.py.
Implements browser automation via DrissionPage.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import threading
import hashlib
import uuid
import secrets
import string
import gc
import queue
import datetime
import base64
import sqlite3
import uuid as uuidlib
from pathlib import Path
from typing import Any, Callable

# ── Paths ───────────────────────────────────────────────
_PROJECT_DIR = Path(__file__).resolve().parent
_CONFIG_PATH = _PROJECT_DIR / "config.json"
_TOKENS_FILE = _PROJECT_DIR / "tokens.txt"
_GLOBAL_CONFIG_CACHE: dict[str, Any] = {}

# ── Exceptions ───────────────────────────────────────────
class RegistrationCancelled(Exception):
    pass

class AccountRetryNeeded(Exception):
    pass

# ── Thread-safe helpers ──────────────────────────────────
_stats_lock = threading.Lock()
_io_lock = threading.Lock()
_worker_id = threading.local()
_cpa_async_threads: list[threading.Thread] = []
MEMORY_CLEANUP_INTERVAL = 50

def _set_worker_id(wid: int) -> None:
    _worker_id.value = wid

def _get_worker_id() -> int:
    return getattr(_worker_id, "value", 0)

def _track_cpa_async_thread(t: threading.Thread) -> None:
    _cpa_async_threads.append(t)

def _wait_cpa_async_threads(
    timeout: float = 300,
    log_callback: Callable[[str], None] | None = None,
    skip_if_stopping: Callable[[], bool] | None = None,
) -> None:
    log = log_callback or (lambda m: None)
    remaining = timeout
    for t in list(_cpa_async_threads):
        if skip_if_stopping and skip_if_stopping():
            log("[thread] skip waiting (stop requested)")
            break
        if not t.is_alive():
            continue
        t0 = time.time()
        t.join(timeout=remaining)
        remaining -= time.time() - t0
        if remaining <= 0:
            break

def _join_threads_interruptible(
    threads: list[threading.Thread],
    should_stop: Callable[[], bool] | None = None,
    timeout: float | None = None,
    poll: float = 0.5,
) -> None:
    t0 = time.time()
    for t in threads:
        while t.is_alive():
            if should_stop and should_stop():
                return
            elapsed = time.time() - t0
            if timeout is not None and elapsed >= timeout:
                return
            t.join(timeout=poll)

# ── Config ────────────────────────────────────────────────
def load_config() -> dict[str, Any]:
    global _GLOBAL_CONFIG_CACHE, config
    path = _CONFIG_PATH
    if not path.is_file():
        _GLOBAL_CONFIG_CACHE.clear()
        return _GLOBAL_CONFIG_CACHE
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        _GLOBAL_CONFIG_CACHE.clear()
        _GLOBAL_CONFIG_CACHE.update(data)
    except Exception:
        _GLOBAL_CONFIG_CACHE.clear()
    config = _GLOBAL_CONFIG_CACHE
    return _GLOBAL_CONFIG_CACHE

def save_config() -> None:
    path = _CONFIG_PATH
    try:
        _write_sensitive(
            path,
            json.dumps(_GLOBAL_CONFIG_CACHE, indent=2, ensure_ascii=False),
        )
    except Exception:
        pass

def _append_sensitive(path: str | os.PathLike[str], text: str) -> None:
    target = os.path.expanduser(os.fspath(path))
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            fd = -1
            f.write(text)
    finally:
        if fd >= 0:
            os.close(fd)

def _write_sensitive(path: str | os.PathLike[str], text: str) -> None:
    target = os.path.expanduser(os.fspath(path))
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            fd = -1
            f.write(text)
    finally:
        if fd >= 0:
            os.close(fd)

config: dict[str, Any] = _GLOBAL_CONFIG_CACHE

# Try loading config on import
if _CONFIG_PATH.is_file():
    load_config()

def get_log_level() -> str:
    return str(config.get("log_level", "info"))

# ── License (bypassed for Linux) ──────────────────────────
def get_hwid() -> str:
    h = hashlib.sha256()
    for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            h.update(Path(p).read_text(encoding="utf-8").strip().encode())
            break
        except Exception:
            pass
    h.update(str(uuid.getnode()).encode())
    return h.hexdigest()[:16].upper()

def check_activated_license() -> tuple[bool, Any]:
    return True, {"key": "LINUX-FREE", "expires_at": -1}

def verify_and_activate_license(key: str) -> tuple[bool, str]:
    return True, "License bypassed for Linux"

def check_license_cli() -> bool:
    return True

# ── Logging ───────────────────────────────────────────────
def should_emit_log(message: str) -> bool:
    return True

def cleanup_runtime_memory(
    log_callback: Callable[[str], None] | None = None,
    reason: str = "",
) -> None:
    gc.collect()
    if log_callback:
        log_callback(f"[mem] {reason}")

# ── Sleep with cancel ─────────────────────────────────────
def sleep_with_cancel(
    seconds: float,
    cancel_check: Callable[[], bool] | None = None,
) -> None:
    interval = 0.25
    elapsed = 0.0
    while elapsed < seconds:
        if cancel_check and cancel_check():
            return
        time.sleep(min(interval, seconds - elapsed))
        elapsed += interval

# ── Browser management (DrissionPage) ─────────────────────
_BROWSER_TLS = threading.local()

def _bm() -> dict[str, Any]:
    d = getattr(_BROWSER_TLS, "state", None)
    if d is None:
        d = {"browser": None, "page": None}
        _BROWSER_TLS.state = d
    return d

def _get_browser() -> Any:
    return _bm()["browser"]

def _get_page() -> Any:
    return _bm()["page"]

def _create_chromium_options(log_callback: Callable[[str], None] | None = None) -> Any:
    from DrissionPage import ChromiumOptions
    opts = ChromiumOptions()
    opts.auto_port()
    opts.set_timeouts(base=2)
    for flag in (
        "--disable-gpu",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--mute-audio",
        "--no-first-run",
        "--disable-background-networking",
        "--window-size=1280,900",
        "--disable-blink-features=AutomationControlled",
    ):
        opts.set_argument(flag)
    for cand in (
        "/usr/bin/google-chrome-stable",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
    ):
        if os.path.isfile(cand):
            try:
                opts.set_browser_path(cand)
            except Exception:
                pass
            break
    ext_dir = str(_PROJECT_DIR / "turnstilePatch")
    if os.path.isdir(ext_dir):
        try:
            opts.add_extension(ext_dir)
        except Exception:
            pass
    proxy_val = (config.get("proxy") or "").strip()
    if proxy_val:
        try:
            opts.set_argument(f"--proxy-server={proxy_val}")
        except Exception:
            pass
    if bool(config.get("headless", False)):
        try:
            opts.headless(True)
        except Exception:
            opts.set_argument("--headless=new")
    return opts

def start_browser(log_callback: Callable[[str], None] | None = None) -> Any:
    from DrissionPage import Chromium
    log = log_callback or (lambda m: None)
    state = _bm()
    if state["browser"] is not None:
        try:
            state["browser"].quit()
        except Exception:
            pass
        state["browser"] = None
        state["page"] = None
    opts = _create_chromium_options(log)
    for attempt in range(3):
        try:
            browser = Chromium(opts)
            page = browser.latest_tab
            state["browser"] = browser
            state["page"] = page
            log("[browser] chromium started")
            return browser, page
        except Exception as e:
            log(f"[browser] start attempt {attempt+1}/3 failed: {type(e).__name__}")
            if attempt < 2:
                time.sleep(2)
    raise RuntimeError("browser failed to start after 3 attempts")

def stop_browser() -> None:
    state = _bm()
    if state["browser"] is not None:
        try:
            state["browser"].quit()
        except Exception:
            pass
        state["browser"] = None
        state["page"] = None

def restart_browser(log_callback: Callable[[str], None] | None = None) -> None:
    stop_browser()
    start_browser(log_callback)

# ── Speed logger ──────────────────────────────────────────
def start_speed_logger(
    get_counts: Callable[[], tuple[int, int]],
    log_callback: Callable[[str], None] | None = None,
    stop_event: threading.Event | None = None,
    interval_sec: float = 60.0,
) -> tuple[threading.Thread, Any]:
    ev = stop_event or threading.Event()
    log = log_callback or (lambda m: None)
    _meter = {}

    def _loop():
        t0 = time.time()
        last_ok = 0
        while not ev.is_set():
            ev.wait(timeout=interval_sec)
            if ev.is_set():
                break
            ok, fail = get_counts()
            elapsed = time.time() - t0
            speed = (ok - last_ok) / (elapsed / 3600) if elapsed > 1 else 0
            last_ok = ok
            log(f"[speed] {ok} sukses, {fail} gagal, {speed:.1f}/jam")

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    return t, _meter

# ── Email provider helpers ────────────────────────────────
def _generate_username(length: int = 10) -> str:
    chars = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(length))

def _extract_code(text: str, subject: str = "") -> str | None:
    if subject:
        m = re.search(r"\b([A-Z0-9]{3}-[A-Z0-9]{3})\b", subject, re.IGNORECASE)
        if m:
            return m.group(1)
    m = re.search(r"\b([A-Z0-9]{3}-[A-Z0-9]{3})\b", text, re.IGNORECASE)
    if m:
        return m.group(1)
    for p in [
        r"verification\s+code[:\s]+(\d{4,8})",
        r"your\s+code[:\s]+(\d{4,8})",
        r"confirm(?:ation)?\s+code[:\s]+(\d{4,8})",
    ]:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            return m.group(1)
    return None

def _build_auth_headers(auth_mode: str, api_key: str) -> dict[str, str]:
    headers = {}
    key = (api_key or "").strip()
    mode = (auth_mode or "none").strip().lower()
    if not key:
        return headers
    if mode == "x-admin-auth":
        headers["x-admin-auth"] = key
    elif mode == "x-api-key":
        headers["X-API-Key"] = key
    elif mode == "bearer":
        headers["Authorization"] = f"Bearer {key}"
    return headers

def _json_or_text(resp: Any) -> tuple[dict | None, str]:
    try:
        data = resp.json()
        return data, ""
    except Exception:
        return None, (resp.text or "")[:400]

# ── DuckMail (api.duckmail.sbs) ───────────────────────────
_DUCKMAIL_BASE = "https://api.duckmail.sbs"

def _duckmail_create() -> tuple[str, str]:
    import requests
    fixed_email = (config.get("duckmail_email") or "").strip()
    fixed_pass = (config.get("duckmail_password") or "").strip()
    if fixed_email and fixed_pass:
        resp = requests.post(
            f"{_DUCKMAIL_BASE}/token",
            json={"address": fixed_email, "password": fixed_pass},
            timeout=20,
        )
        resp.raise_for_status()
        data, _ = _json_or_text(resp)
        token = ""
        if data:
            token = str(data.get("token") or "")
        if not token:
            raise RuntimeError(f"duckmail: failed to get token for {fixed_email}")
        return fixed_email, token
    resp = requests.get(f"{_DUCKMAIL_BASE}/domains", timeout=20)
    resp.raise_for_status()
    data, _ = _json_or_text(resp)
    domains = []
    if isinstance(data, dict):
        for d in (data.get("hydra:member") or []):
            if isinstance(d, dict) and d.get("domain"):
                domains.append(d["domain"])
    if not domains:
        raise RuntimeError("duckmail: no domains available")
    preferred = (config.get("duckmail_domain") or "").strip()
    if preferred and preferred in domains:
        domain = preferred
    else:
        # Prefer baldur.edu.kg over duckmail.sbs (less likely blocked)
        for d in domains:
            if "baldur" in d or "edu" in d:
                domain = d
                break
        else:
            domain = domains[0]
    username = _generate_username(10)
    addr = f"{username}@{domain}"
    password = secrets.token_urlsafe(12)
    resp2 = requests.post(
        f"{_DUCKMAIL_BASE}/accounts",
        json={"address": addr, "password": password, "expiresIn": 0},
        timeout=20,
    )
    resp2.raise_for_status()
    data2, _ = _json_or_text(resp2)
    if not data2:
        raise RuntimeError("duckmail: account creation failed")
    resp3 = requests.post(
        f"{_DUCKMAIL_BASE}/token",
        json={"address": addr, "password": password},
        timeout=20,
    )
    resp3.raise_for_status()
    data3, _ = _json_or_text(resp3)
    token = ""
    if data3:
        token = str(data3.get("token") or "")
    return addr, token

def _duckmail_wait_code(
    token: str,
    timeout: float = 180,
    interval: float = 3,
) -> str:
    import requests
    deadline = time.time() + timeout
    seen = set()
    while time.time() < deadline:
        try:
            resp = requests.get(
                f"{_DUCKMAIL_BASE}/messages",
                headers={"Authorization": f"Bearer {token}"},
                timeout=20,
            )
            if resp.status_code < 400:
                data, _ = _json_or_text(resp)
                msgs = []
                if isinstance(data, dict):
                    msgs = data.get("hydra:member") or []
                for m in msgs:
                    mid = m.get("id")
                    if mid in seen:
                        continue
                    seen.add(mid)
                    subj = str(m.get("subject") or "")
                    # get full message for body
                    try:
                        detail = requests.get(
                            f"{_DUCKMAIL_BASE}/messages/{mid}",
                            headers={"Authorization": f"Bearer {token}"},
                            timeout=20,
                        )
                        if detail.status_code < 400:
                            dd, _ = _json_or_text(detail)
                            body = str(dd.get("text") or dd.get("html") or "") if dd else ""
                            if isinstance(body, list):
                                body = " ".join(str(b) for b in body)
                            code = _extract_code(body, subj)
                            if code:
                                return code
                    except Exception:
                        pass
        except Exception:
            pass
        time.sleep(interval)
    raise RuntimeError("duckmail: no verification code received")

# ── Cloudflare Temp Mail ─────────────────────────────────
def _cf_normalize_api_base(api_base: str) -> str:
    base = (api_base or "").strip().rstrip("/")
    if not base:
        return base
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://", base):
        base = f"https://{base}"
    return base

def _cf_create() -> tuple[str, str]:
    import requests
    cfg = config
    api_base = _cf_normalize_api_base(cfg.get("cloudflare_api_base") or "")
    if not api_base:
        raise RuntimeError("cloudflare_api_base not configured")
    auth_mode = cfg.get("cloudflare_auth_mode", "none")
    api_key = cfg.get("cloudflare_api_key", "")
    path = cfg.get("cloudflare_path_accounts", "/api/new_address")
    domain = cfg.get("defaultDomains", "")
    headers = {"Content-Type": "application/json"}
    headers.update(_build_auth_headers(auth_mode, api_key))
    payload = {}
    if domain.strip():
        payload["domain"] = domain.strip()
    if path.rstrip("/").lower().endswith("/admin/new_address"):
        payload["name"] = _generate_username()
        payload["enablePrefix"] = True
    resp = requests.post(
        f"{api_base}{path}",
        json=payload,
        headers=headers,
        timeout=20,
    )
    resp.raise_for_status()
    data, raw = _json_or_text(resp)
    if not data:
        raise RuntimeError("cf: API returned a non-JSON response")
    addr = str(data.get("address", "")).strip()
    jwt = str(data.get("jwt", "")).strip()
    if not addr:
        raise RuntimeError("cf: API response is missing an address")
    # The bundled Email Worker uses one API token and the recipient address as
    # mailbox context. Other supported APIs may still return a mailbox JWT.
    return addr, jwt or addr

def _cf_wait_code(
    mailbox_credential: str,
    timeout: float = 180,
    interval: float = 3,
) -> str:
    import requests
    cfg = config
    api_base = _cf_normalize_api_base(cfg.get("cloudflare_api_base") or "")
    mail_path = cfg.get("cloudflare_path_messages", "/api/mails")
    is_worker_mailbox = "@" in mailbox_credential
    if is_worker_mailbox:
        headers = _build_auth_headers(
            cfg.get("cloudflare_auth_mode", "bearer"),
            cfg.get("cloudflare_api_key", ""),
        )
        params = {"recipient": mailbox_credential, "limit": 20}
    else:
        headers = {"Authorization": f"Bearer {mailbox_credential}"}
        params = {"limit": 20, "offset": 0}
    deadline = time.time() + timeout
    seen = set()
    while time.time() < deadline:
        try:
            resp = requests.get(
                f"{api_base}{mail_path}",
                params=params,
                headers=headers,
                timeout=20,
            )
            if resp.status_code < 400:
                data, _ = _json_or_text(resp)
                msgs = []
                if isinstance(data, dict):
                    for k in ("messages", "results", "data"):
                        v = data.get(k)
                        if isinstance(v, list):
                            msgs = v
                            break
                for m in msgs:
                    mid = m.get("id") or m.get("mail_id")
                    if mid in seen:
                        continue
                    seen.add(mid)
                    subj = str(m.get("subject") or "")
                    body = str(m.get("text") or m.get("body") or m.get("content") or m.get("snippet") or "")
                    if not body:
                        try:
                            detail = requests.get(
                                f"{api_base}/api/mail/{mid}",
                                headers=headers,
                                timeout=20,
                            )
                            if detail.status_code < 400:
                                dd, _ = _json_or_text(detail)
                                if isinstance(dd, dict):
                                    message = dd.get("message")
                                    if isinstance(message, dict):
                                        dd = message
                                    body = str(
                                        dd.get("text")
                                        or dd.get("raw")
                                        or dd.get("body")
                                        or dd.get("content")
                                        or body
                                    )
                                    subj = subj or str(dd.get("subject") or "")
                        except Exception:
                            pass
                    code = _extract_code(body, subj)
                    if code:
                        return code
        except Exception:
            pass
        time.sleep(interval)
    raise RuntimeError("cf: no verification code received")

# ── MailTM ────────────────────────────────────────────────
def _mailtm_create() -> tuple[str, str]:
    import requests
    resp = requests.get("https://api.mail.tm/domains", timeout=20)
    resp.raise_for_status()
    data, _ = _json_or_text(resp)
    domains = []
    if isinstance(data, dict):
        domains = [d.get("domain") for d in (data.get("hydra:member") or []) if d.get("domain")]
    if not domains:
        raise RuntimeError("mailtm: no domains available")
    domain = domains[0]
    addr = f"{_generate_username()}@{domain}"
    password = secrets.token_urlsafe(18)
    resp2 = requests.post(
        "https://api.mail.tm/accounts",
        json={"address": addr, "password": password},
        timeout=20,
    )
    resp2.raise_for_status()
    data2, _ = _json_or_text(resp2)
    if not data2:
        raise RuntimeError("mailtm: no account data")
    addr = str(data2.get("address", addr))
    resp3 = requests.post(
        "https://api.mail.tm/token",
        json={"address": addr, "password": password},
        timeout=20,
    )
    resp3.raise_for_status()
    data3, _ = _json_or_text(resp3)
    token = ""
    if data3:
        token = str(data3.get("token") or "")
    return addr, token

def _mailtm_wait_code(
    token: str,
    timeout: float = 180,
    interval: float = 3,
) -> str:
    import requests
    deadline = time.time() + timeout
    seen = set()
    while time.time() < deadline:
        try:
            resp = requests.get(
                "https://api.mail.tm/messages",
                headers={"Authorization": f"Bearer {token}"},
                timeout=20,
            )
            if resp.status_code < 400:
                data, _ = _json_or_text(resp)
                msgs = []
                if isinstance(data, dict):
                    msgs = data.get("hydra:member") or []
                for m in msgs:
                    mid = m.get("id")
                    if mid in seen:
                        continue
                    seen.add(mid)
                    subj = str(m.get("subject") or "")
                    # get full message
                    try:
                        detail = requests.get(
                            f"https://api.mail.tm/messages/{mid}",
                            headers={"Authorization": f"Bearer {token}"},
                            timeout=20,
                        )
                        if detail.status_code < 400:
                            dd, _ = _json_or_text(detail)
                            body = str(dd.get("text") or dd.get("html") or "") if dd else ""
                            code = _extract_code(body, subj)
                            if code:
                                return code
                    except Exception:
                        pass
        except Exception:
            pass
        time.sleep(interval)
    raise RuntimeError("mailtm: no verification code received")

# ── Wapol / RMail browser mailbox ─────────────────────────
_WAPOL_TLS = threading.local()

def _wapol_mailboxes() -> dict[str, Any]:
    boxes = getattr(_WAPOL_TLS, "mailboxes", None)
    if boxes is None:
        boxes = {}
        _WAPOL_TLS.mailboxes = boxes
    return boxes

def _wapol_create() -> tuple[str, str]:
    browser = _get_browser()
    if browser is None:
        raise RuntimeError("wapol: browser not started")

    domain = (config.get("wapol_domain") or "wapol.site").strip()
    last_error = ""
    for attempt in range(1, 4):
        page = browser.new_tab("https://wapol.site/mailbox", background=True)
        try:
            _wait_page_ready(page, timeout=30)
            time.sleep(3)
            username = _generate_username(12)
            expected = f"{username}@{domain}"
            opened = page.run_js(
                """
                const nodes = Array.from(document.querySelectorAll('button,a,[role="button"],div'));
                const target = nodes.find((el) => String(el.innerText || el.textContent || '').trim() === 'New');
                if (!target) return false;
                target.click();
                return true;
                """
            )
            if not opened:
                raise RuntimeError("New button not found")
            user_input = page.ele("css:#user", timeout=5)
            domain_input = page.ele("css:#domain", timeout=5)
            if not user_input or not domain_input:
                raise RuntimeError("custom mailbox form not found")
            user_input.click()
            user_input.input(username, clear=True)
            page.run_js(
                """
                const el = document.querySelector('#user');
                if (!el) return false;
                const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
                setter.call(el, arguments[0]);
                el.dispatchEvent(new InputEvent('input', {bubbles:true, inputType:'insertText', data:arguments[0]}));
                el.dispatchEvent(new Event('change', {bubbles:true}));
                el.dispatchEvent(new FocusEvent('blur', {bubbles:true}));
                return el.value;
                """,
                username,
            )
            domain_input.click()
            domain_link = page.ele(
                f"xpath://a[normalize-space(.)='{domain}']",
                timeout=5,
            )
            if not domain_link:
                raise RuntimeError(f"domain {domain} is unavailable")
            domain_link.click()
            deadline = time.time() + 8
            while time.time() < deadline:
                current_user = str(getattr(page.ele("css:#user", timeout=0.5), "value", "") or "").strip()
                current_domain = str(getattr(page.ele("css:#domain", timeout=0.5), "value", "") or "").strip()
                domain_text = _norm(getattr(page.ele("css:#domain", timeout=0.5), "text", "") or "")
                if current_user == username and domain in (current_domain or domain_text):
                    break
                time.sleep(0.4)
            else:
                raise RuntimeError("custom mailbox form state was not retained")
            create_button = page.ele("css:#create", timeout=3)
            if not create_button:
                raise RuntimeError("Create button not found")
            create_button.click()
            deadline = time.time() + 20
            address = ""
            while time.time() < deadline:
                email_el = page.ele("css:#email_id", timeout=1)
                address = _norm(email_el.text if email_el else "")
                if address == expected:
                    token = secrets.token_urlsafe(18)
                    _wapol_mailboxes()[token] = page
                    return address, token
                time.sleep(0.5)
            raise RuntimeError(
                f"mailbox creation mismatch (expected {expected}, got {address or 'empty'})"
            )
        except Exception as exc:
            last_error = str(exc)
            try:
                page.close()
            except Exception:
                pass
            if attempt < 3:
                time.sleep(1)
    raise RuntimeError(f"wapol: mailbox creation failed after 3 attempts: {last_error}")

def _wapol_wait_code(
    token: str,
    timeout: float = 180,
    interval: float = 3,
) -> str:
    page = _wapol_mailboxes().get(token)
    if page is None:
        raise RuntimeError("wapol: mailbox session not found")

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            # The site uses Livewire; dispatching its native event preserves
            # the generated inbox while requesting fresh messages.
            page.run_js(
                """
                if (window.Livewire) {
                    Livewire.dispatch('fetchMessages');
                    return true;
                }
                return false;
                """
            )
            time.sleep(2)
            text = _visible_text(page)
            code = _extract_code(text)
            if code:
                return code

            # Open a likely xAI message so the complete body becomes visible.
            opened = page.run_js(
                """
                const nodes = Array.from(document.querySelectorAll('a,button,[role="button"],tr,li,div'));
                const msg = nodes.find((el) => {
                    const t = String(el.innerText || '').toLowerCase();
                    return t.includes('x.ai') || t.includes('grok') ||
                           t.includes('verification code') || t.includes('verify your email');
                });
                if (msg) { msg.click(); return true; }
                return false;
                """
            )
            if opened:
                time.sleep(1)
                code = _extract_code(_visible_text(page))
                if code:
                    return code
        except Exception:
            pass
        time.sleep(interval)
    raise RuntimeError("wapol: no verification code received")

# ── Unified email helper ─────────────────────────────────
def _create_email() -> tuple[str, str]:
    provider = config.get("email_provider", "duckmail")
    if provider == "duckmail":
        return _duckmail_create()
    elif provider == "cloudflare":
        return _cf_create()
    elif provider == "mailtm":
        return _mailtm_create()
    elif provider == "wapol":
        return _wapol_create()
    else:
        raise RuntimeError(f"unsupported email provider: {provider}")

def _wait_verification_code(dev_token: str) -> str:
    provider = config.get("email_provider", "duckmail")
    if provider == "duckmail":
        return _duckmail_wait_code(dev_token)
    elif provider == "cloudflare":
        return _cf_wait_code(dev_token)
    elif provider == "mailtm":
        return _mailtm_wait_code(dev_token)
    elif provider == "wapol":
        return _wapol_wait_code(dev_token)
    else:
        raise RuntimeError(f"unsupported email provider: {provider}")

# ── Browser interaction primitives (from browser_confirm.py) ─
def _norm(text: str) -> str:
    return " ".join(text.strip().split()) if text else ""

def _visible_text(page: Any) -> str:
    try:
        return page("tag:body").text
    except Exception:
        try:
            return page.run_js("document.body?.innerText || ''")
        except Exception:
            return ""

def _page_url(page: Any) -> str:
    try:
        return page.url
    except Exception:
        return ""

def _wait_page_ready(page: Any, timeout: float = 30) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if page.run_js("return document.readyState === 'complete'"):
                return
        except Exception:
            pass
        time.sleep(0.5)

def _find_button_exact(page: Any, label: str) -> Any | None:
    try:
        for el in page.eles("tag:button") or []:
            try:
                if _norm(el.text or "") == label:
                    return el
            except Exception:
                continue
    except Exception:
        pass
    try:
        return page.ele(f"xpath://button[normalize-space(.)='{label}']", timeout=0.3)
    except Exception:
        return None

def _click_exact(page: Any, labels: list[str], log: Callable, *, real: bool = False) -> str | None:
    for label in labels:
        el = _find_button_exact(page, label)
        if not el:
            continue
        try:
            if real:
                try:
                    el.scroll.to_see()
                except Exception:
                    pass
                el.click()
            else:
                el.click(by_js=True)
            log(f"clicked {label!r}")
            return label
        except Exception as e:
            log(f"click {label!r} failed: {type(e).__name__}")
    return None

def _fill(page: Any, selector: str, value: str, log: Callable, label: str = "") -> bool:
    label = label or selector
    try:
        el = page.ele(selector, timeout=1.5)
        if el is None:
            return False
        try:
            el.clear()
        except Exception:
            pass
        try:
            el.input(value)
        except Exception:
            page.run_js(
                """
                const sel = arguments[0], v = arguments[1];
                const el = document.querySelector(sel);
                if (!el) return false;
                el.focus();
                el.value = v;
                el.dispatchEvent(new Event('input', {bubbles:true}));
                el.dispatchEvent(new Event('change', {bubbles:true}));
                return true;
                """,
                selector, value,
            )
        log(f"filled {label}")
        return True
    except Exception:
        return False

def _fill_react_input(
    page: Any,
    selector: str,
    value: str,
    log: Callable,
    label: str,
) -> bool:
    """Fill a controlled React input and update its framework state."""
    el = page.ele(selector, timeout=2)
    if el is None:
        return False
    try:
        el.click()
        el.input(value, clear=True, by_js=False)
        time.sleep(0.3)
        page.run_js(
            """
            const el = arguments[0];
            const value = arguments[1];
            const setter = Object.getOwnPropertyDescriptor(
                HTMLInputElement.prototype, 'value'
            ).set;
            setter.call(el, value);
            el.dispatchEvent(new InputEvent('input', {
                bubbles: true,
                inputType: 'insertText',
                data: value,
            }));
            el.dispatchEvent(new Event('change', {bubbles: true}));
            el.dispatchEvent(new FocusEvent('blur', {bubbles: true}));
            return el.value;
            """,
            el,
            value,
        )
        time.sleep(0.5)
        if str(el.value or "").strip() != value:
            log(f"fill {label} failed: value was not retained")
            return False
        log(f"filled {label}")
        return True
    except Exception as exc:
        log(f"fill {label} failed: {type(exc).__name__}")
        return False

def _detect_rejected_domain(page: Any) -> str | None:
    """Check if x.ai rejected the email domain."""
    text = _visible_text(page)
    needles = [
        "Your email domain has been rejected",
        "has been rejected",
        "Please use a different email address",
        "domain is not supported",
        "email domain is not allowed",
        "does not support sign-ups",
    ]
    for n in needles:
        if n.lower() in text.lower():
            return n
    return None

def _is_turnstile_challenge(text: str) -> bool:
    t = text or ""
    tl = t.lower()
    needles = (
        "确认您是真人", "确认你是真人",
        "verify you are human", "confirm you are human",
        "just a moment", "checking your browser",
        "cf-turnstile",
        "进行人机验证", "人机验证",
        "konfirmasi anda adalah manusia",
        "konfirmasi bahwa anda adalah manusia",
        "verifikasi manusia",
    )
    return any(n in t or n in tl for n in needles)

def _wait_turnstile(
    page: Any, log: Callable, timeout: float = 45.0, *, email: str = "", raise_on_timeout: bool = False,
) -> bool:
    deadline = time.time() + timeout
    clicked = False
    while time.time() < deadline:
        try:
            el = page.ele("css:input[name='cf-turnstile-response']", timeout=0.3)
            if el is not None:
                v = (el.attr("value") or "").strip()
                if len(v) > 20:
                    return True
        except Exception:
            pass
        try:
            ci = page.ele("@name=cf-turnstile-response", timeout=0.2)
            if ci is not None:
                wrapper = ci.parent()
                iframe = None
                try:
                    iframe = wrapper.shadow_root.ele("tag:iframe")
                except Exception:
                    pass
                if iframe is not None:
                    try:
                        iframe.run_js(
                            """
                            Object.defineProperty(MouseEvent.prototype, 'screenX', { value: 800 + Math.floor(Math.random()*400) });
                            Object.defineProperty(MouseEvent.prototype, 'screenY', { value: 400 + Math.floor(Math.random()*300) });
                            """
                        )
                    except Exception:
                        pass
                    try:
                        body_sr = iframe.ele("tag:body").shadow_root
                        btn = body_sr.ele("tag:input")
                        if btn is not None:
                            btn.click()
                            if not clicked:
                                log("clicked turnstile checkbox")
                                clicked = True
                    except Exception:
                        pass
        except Exception:
            pass
        if not clicked:
            try:
                page.run_js(
                    """
                    const nodes = Array.from(document.querySelectorAll('div,span,iframe')).filter((n) => {
                        const txt = (n.className||'') + ' ' + (n.id||'') + ' ' + (n.getAttribute?.('src')||'');
                        return String(txt).toLowerCase().includes('turnstile');
                    });
                    if (nodes.length && typeof nodes[0].click === 'function') nodes[0].click();
                    """
                )
                clicked = True
            except Exception:
                pass
        time.sleep(0.9)
    if raise_on_timeout:
        raise RuntimeError("turnstile timeout")
    return False

def _cookie_banner_visible(text: str) -> bool:
    t = text or ""
    tl = t.lower()
    strong = (
        "隐私偏好", "全部允许", "全部拒绝",
        "privacy preference", "privacy preferences",
        "manage cookies", "we use cookies",
        "accept all cookies", "cookie preferences",
        "preferensi privasi", "semua diizinkan",
        "semua ditolak", "kami menggunakan cookie",
        "terima semua cookie",
    )
    return any(n in t or n in tl for n in strong)

def _dismiss_cookie_banner(page: Any, log: Callable) -> bool:
    if not _cookie_banner_visible(_visible_text(page)):
        return False
    labels = [
        "全部允许", "接受所有", "接受全部",
        "Accept all", "Accept All", "Allow all", "Allow All",
        "I agree", "Agree",
        "Semua Diizinkan", "Setujui Semua", "Terima Semua", "Izinkan Semua", "Izinkan semua",
    ]
    if _click_exact(page, labels, log, real=False):
        time.sleep(0.8)
        return True
    try:
        ok = page.run_js(
            """
            const want = new Set(['全部允许','接受所有','接受全部','Accept all','Accept All','Allow all','Allow All','I agree','Agree','Semua Diizinkan','Setujui Semua','Terima Semua','Izinkan Semua','Izinkan semua']);
            const btns = Array.from(document.querySelectorAll('button, [role="button"], a'));
            const match = btns.find((b) => want.has(String(b.innerText||b.textContent||'').trim()));
            if (match) { match.click(); return true; }
            const close = document.querySelector('[aria-label="Close"], button[class*="close"], [data-testid*="close"]');
            if (close) { close.click(); return true; }
            return false;
            """
        )
        if ok:
            time.sleep(0.8)
            return True
    except Exception:
        pass
    return False

# ── Registration flow (accounts.x.ai) ────────────────────
_REG_URL = "https://accounts.x.ai/signup"

def open_signup_page(
    log_callback: Callable[[str], None] | None = None,
    cancel_callback: Callable[[], bool] | None = None,
) -> None:
    log = log_callback or (lambda m: None)
    page = _get_page()
    if page is None:
        raise RuntimeError("browser not started")
    if cancel_callback and cancel_callback():
        raise RegistrationCancelled()
    log(f"[nav] opening {_REG_URL}")
    page.get(_REG_URL)
    _wait_page_ready(page)
    sleep_with_cancel(2, cancel_callback)

def fill_email_and_submit(
    log_callback: Callable[[str], None] | None = None,
    cancel_callback: Callable[[], bool] | None = None,
) -> tuple[str, str]:
    log = log_callback or (lambda m: None)
    page = _get_page()
    if page is None:
        raise RuntimeError("browser not started")
    if cancel_callback and cancel_callback():
        raise RegistrationCancelled()
    _dismiss_cookie_banner(page, log)
    if "sign-in" in _page_url(page) or "signin" in _page_url(page):
        for link_text in ["Sign up", "Daftar", "注册"]:
            try:
                el = page.ele(f"xpath://*[normalize-space(.)='{link_text}']", timeout=0.3)
                if el:
                    el.click(by_js=True)
                    log(f"clicked {link_text!r}")
                    sleep_with_cancel(2, cancel_callback)
                    break
            except Exception:
                continue
    if not page.ele("css:input[type='email']", timeout=0.5):
        clicked = _click_exact(page, [
            "Sign up with email", "Login with email",
            "Continue with email", "Sign in with email",
            "使用邮箱登录", "Masuk dengan email",
        ], log, real=False)
        if clicked:
            sleep_with_cancel(2, cancel_callback)
    email_input = page.ele("css:input[type='email']", timeout=2)
    if email_input is None:
        if bool(config.get("headless", False)):
            config["headless"] = False
            save_config()
            log("[email] headless diblokir xAI, beralih otomatis ke browser tampil")
            raise AccountRetryNeeded("headless tidak menampilkan formulir email; retry headed")
        raise AccountRetryNeeded(
            f"formulir email tidak tersedia, url={_page_url(page)} text={_norm(_visible_text(page))[:120]}"
        )
    addr, token = _create_email()
    log(f"[email] created {addr}")
    if not _fill(page, "css:input[type='email']", addr, log, "email"):
        raise AccountRetryNeeded("gagal mengisi formulir email")
    sleep_with_cancel(1, cancel_callback)
    _wait_turnstile(page, log, 25, email=addr, raise_on_timeout=False)
    submitted = bool(_click_exact(
        page,
        ["Sign up", "下一步", "Next", "Continue", "继续", "Berikutnya", "Lanjutkan"],
        log,
        real=False,
    ))
    if not submitted:
        try:
            btn = page.ele("css:button[type='submit']", timeout=0.5)
            if btn:
                btn.click(by_js=True)
                submitted = True
                log("clicked email submit fallback")
        except Exception:
            pass
    if not submitted:
        raise AccountRetryNeeded("tombol submit email tidak ditemukan")
    deadline = time.time() + 15
    while time.time() < deadline:
        if cancel_callback and cancel_callback():
            raise RegistrationCancelled()
        rejected = _detect_rejected_domain(page)
        if rejected:
            log(f"[!] x.ai menolak domain email: {rejected}")
            raise RuntimeError(f"Email domain rejected by x.ai: {rejected}")
        if page.ele("css:input[name='code']", timeout=0.2) or page.ele(
            "css:input[autocomplete='one-time-code']", timeout=0.2
        ):
            return addr, token
        if not page.ele("css:input[type='email']", timeout=0.2):
            return addr, token
        time.sleep(0.5)
    raise AccountRetryNeeded(
        f"halaman tidak berpindah setelah submit email, url={_page_url(page)}"
    )

def fill_code_and_submit(
    email: str,
    dev_token: str,
    log_callback: Callable[[str], None] | None = None,
    cancel_callback: Callable[[], bool] | None = None,
) -> str:
    log = log_callback or (lambda m: None)
    page = _get_page()
    if page is None:
        raise RuntimeError("browser not started")
    if cancel_callback and cancel_callback():
        raise RegistrationCancelled()
    log("[code] waiting for verification email...")
    code = _wait_verification_code(dev_token)
    log("[code] verification code received")
    input_code = re.sub(r"[^A-Za-z0-9]", "", code)
    _dismiss_cookie_banner(page, log)
    if not _fill_react_input(
        page,
        "css:input[name='code']",
        input_code,
        log,
        "code",
    ):
        if not _fill_react_input(
            page,
            "css:input[autocomplete='one-time-code']",
            input_code,
            log,
            "code",
        ):
            raise RuntimeError("verification code input not found")
    sleep_with_cancel(1, cancel_callback)

    # input-otp may auto-submit as soon as the sixth character is entered.
    for _ in range(10):
        if not page.ele("css:input[name='code']", timeout=0.2):
            break
        time.sleep(0.3)
    else:
        if not _click_exact(
            page,
            ["Confirm email", "Verify", "Confirm", "Verify Code", "Verifikasi"],
            log,
            real=True,
        ):
            raise RuntimeError("verification submit button not found")

    deadline = time.time() + 20
    while time.time() < deadline:
        if cancel_callback and cancel_callback():
            raise RegistrationCancelled()
        if not page.ele("css:input[name='code']", timeout=0.2):
            break
        text = _visible_text(page).lower()
        if "invalid code" in text or "incorrect code" in text:
            raise RuntimeError("verification code rejected by x.ai")
        time.sleep(0.5)
    else:
        raise RuntimeError("verification page did not advance after submitting code")

    log(f"[code] verification accepted, url={_page_url(page)}")
    return code

def fill_profile_and_submit(
    log_callback: Callable[[str], None] | None = None,
    cancel_callback: Callable[[], bool] | None = None,
) -> dict[str, str]:
    log = log_callback or (lambda m: None)
    page = _get_page()
    if page is None:
        raise RuntimeError("browser not started")
    if cancel_callback and cancel_callback():
        raise RegistrationCancelled()
    given = "User" + _generate_username(4)
    family = "Test" + _generate_username(4)
    password = f"Aa1!{secrets.token_urlsafe(12)}"
    profile = {"given_name": given, "family_name": family, "password": password}
    _dismiss_cookie_banner(page, log)
    if not _fill_react_input(
        page, "css:input[name='givenName']", given, log, "givenName"
    ):
        raise RuntimeError("first name input not found")
    if not _fill_react_input(
        page, "css:input[name='familyName']", family, log, "familyName"
    ):
        raise RuntimeError("last name input not found")
    if not _fill_react_input(
        page, "css:input[name='password']", password, log, "password"
    ):
        raise RuntimeError("password input not found")
    sleep_with_cancel(1, cancel_callback)
    _wait_turnstile(page, log, 30, raise_on_timeout=False)
    if not _click_exact(
        page,
        ["Complete sign up", "Continue", "Register", "Sign Up", "Daftar", "Submit"],
        log,
        real=True,
    ):
        raise RuntimeError("complete sign-up button not found")

    deadline = time.time() + 30
    while time.time() < deadline:
        if cancel_callback and cancel_callback():
            raise RegistrationCancelled()
        if not page.ele("css:input[name='givenName']", timeout=0.2):
            log(f"[profile] sign-up completed, url={_page_url(page)}")
            break
        text = _visible_text(page).lower()
        errors = (
            "invalid input", "password must", "already exists",
            "email is already", "too many", "has been rejected",
        )
        for error in errors:
            if error in text:
                raise RuntimeError(f"profile submission rejected: {error}")
        time.sleep(0.5)
    else:
        raise RuntimeError("profile page did not advance after submission")
    return profile

def wait_for_sso_cookie(
    log_callback: Callable[[str], None] | None = None,
    cancel_callback: Callable[[], bool] | None = None,
    timeout: float = 120,
) -> str:
    log = log_callback or (lambda m: None)
    page = _get_page()
    if page is None:
        raise RuntimeError("browser not started")
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cancel_callback and cancel_callback():
            raise RegistrationCancelled()
        try:
            cks = page.cookies(all_domains=True, all_info=True)
            if not cks:
                cks = page.cookies(all_domains=True)
            for c in cks:
                name = (c.get("name") or "").lower()
                if name in ("sso", "sso-rw"):
                    val = c.get("value", "")
                    if val:
                        log(f"[sso] found cookie {name}")
                        return val
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError("SSO cookie not found")

# ── NSFW ─────────────────────────────────────────────────
def enable_nsfw_for_token(
    sso: str,
    log_callback: Callable[[str], None] | None = None,
) -> tuple[bool, str]:
    log = log_callback or (lambda m: None)
    log("[nsfw] enabling NSFW via grok.com API...")
    import requests
    headers = {
        "Authorization": f"Bearer {sso}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
    }
    try:
        resp = requests.post(
            "https://api.grok.com/settings",
            json={"nsfw": True},
            headers=headers,
            timeout=20,
        )
        if resp.status_code < 400:
            log("[nsfw] enabled successfully")
            return True, "NSFW enabled"
        log(f"[nsfw] API returned {resp.status_code}")
        return True, "continuing (NSFW API non-critical)"
    except Exception as e:
        log(f"[nsfw] error: {type(e).__name__}")
        return True, "continuing (NSFW non-critical)"

# ── Token management ─────────────────────────────────────
def add_token_to_grok2api_pools(
    sso: str,
    email: str = "",
    log_callback: Callable[[str], None] | None = None,
) -> None:
    log = log_callback or (lambda m: None)
    cfg = config
    # Local pool
    if cfg.get("grok2api_auto_add_local", True):
        pool_name = cfg.get("grok2api_pool_name", "ssoBasic")
        token_file = cfg.get("grok2api_local_token_file", "")
        tokens = []
        if token_file and os.path.isfile(token_file):
            try:
                with open(token_file) as f:
                    tokens = json.load(f)
            except Exception:
                tokens = []
        if not isinstance(tokens, list):
            tokens = []
        entry = {"sso": sso, "email": email or "", "pool": pool_name}
        tokens.append(entry)
        if token_file:
            try:
                _write_sensitive(token_file, json.dumps(tokens, indent=2))
                log(f"[pool] added to local token file: {token_file}")
            except Exception as e:
                log(f"[pool] failed to write local token: {type(e).__name__}")
    # Remote pool
    if cfg.get("grok2api_auto_add_remote", False):
        remote_base = cfg.get("grok2api_remote_base", "").strip()
        app_key = cfg.get("grok2api_remote_app_key", "").strip()
        if remote_base and app_key:
            try:
                import requests
                resp = requests.post(
                    f"{remote_base.rstrip('/')}/api/pool/add",
                    json={"sso": sso, "email": email, "app_key": app_key},
                    timeout=15,
                )
                if resp.status_code < 400:
                    log(f"[pool] added to remote pool: {resp.status_code}")
                else:
                    log(f"[pool] remote add returned {resp.status_code}")
            except Exception as e:
                log(f"[pool] remote add error: {type(e).__name__}")

def add_token_to_token_only_file(
    sso: str,
    log_callback: Callable[[str], None] | None = None,
) -> None:
    log = log_callback or (lambda m: None)
    # Always append to tokens.txt
    try:
        with _io_lock:
            _append_sensitive(_TOKENS_FILE, f"{sso}\n")
    except Exception as e:
        log(f"[token] failed to write tokens.txt: {type(e).__name__}")
    # Also write to custom file if configured
    token_only = config.get("token_only_file", "").strip()
    if token_only:
        try:
            with _io_lock:
                _append_sensitive(token_only, f"{sso}\n")
        except Exception as e:
            log(f"[token] failed to write custom file: {type(e).__name__}")

def reset_9router_connections_status(
    log_callback: Callable[[str], None] | None = None,
) -> None:
    log = log_callback or (lambda m: None)
    log("[9router] connection status reset (Linux: no-op)")

# ── CPA xAI export ────────────────────────────────────────
def export_cpa_xai_for_account(
    email: str,
    password: str,
    sso: str | None = None,
    log_callback: Callable[[str], None] | None = None,
    page: Any | None = None,
) -> dict[str, Any]:
    log = log_callback or (lambda m: None)
    log("[cpa] starting CPA xAI export...")
    try:
        # Import inline to avoid circular imports
        sys.path.insert(0, str(_PROJECT_DIR))
        from cpa_export import export_cpa_xai_for_account as _real_export
        result = _real_export(
            email=email,
            password=password,
            page=page,
            sso=sso,
            config=config,
            log_callback=log,
        )
        if result.get("ok") and result.get("path") and config.get(
            "nine_router_auto_import", False
        ):
            try:
                imported = import_cpa_to_9router(result["path"], log_callback=log)
                result["nine_router"] = imported
            except Exception as exc:
                result["nine_router"] = {"ok": False, "error": str(exc)}
                log(f"[9router] auto-import failed: {type(exc).__name__}")
        return result
    except Exception as e:
        log(f"[cpa] export failed: {type(e).__name__}")
        return {"ok": False, "error": "CPA export failed"}

def _jwt_payload(token: str) -> dict[str, Any]:
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part).decode("utf-8"))
    except Exception:
        return {}

def import_cpa_to_9router(
    credential_path: str | os.PathLike[str],
    log_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Upsert a CPA xAI OAuth credential into the local 9Router database."""
    log = log_callback or (lambda m: None)
    path = Path(credential_path).expanduser().resolve()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("type") != "xai":
        raise RuntimeError("credential is not an xAI CPA file")

    email = str(payload.get("email") or "").strip()
    access_token = str(payload.get("access_token") or "").strip()
    refresh_token = str(payload.get("refresh_token") or "").strip()
    id_token = str(payload.get("id_token") or "").strip()
    if not email or not access_token or not refresh_token:
        raise RuntimeError("xAI credential is missing email/access_token/refresh_token")

    db_path = Path(
        config.get("nine_router_db_path")
        or Path.home() / ".9router" / "db" / "data.sqlite"
    ).expanduser().resolve()
    if not db_path.is_file():
        raise RuntimeError(f"9Router database not found: {db_path}")

    backup_dir = db_path.parent / "backups" / "grok-mint"
    backup_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(backup_dir, 0o700)
    backup_path = backup_dir / f"data-{time.strftime('%Y%m%d-%H%M%S')}.sqlite"
    fd = os.open(backup_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    with sqlite3.connect(str(db_path), timeout=30) as source:
        with sqlite3.connect(str(backup_path)) as destination:
            source.backup(destination)
    os.chmod(backup_path, 0o600)

    now = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    access_claims = _jwt_payload(access_token)
    scope = str(access_claims.get("scope") or "")
    expires_in = int(payload.get("expires_in") or 21600)
    expires_at = str(payload.get("expired") or "")
    if not expires_at:
        expires_at = (
            datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(seconds=expires_in)
        ).isoformat().replace("+00:00", "Z")

    principal_id = str(payload.get("sub") or access_claims.get("principal_id") or "").strip()
    data = {
        "accessToken": access_token,
        "refreshToken": refresh_token,
        "idToken": id_token or None,
        "expiresAt": expires_at,
        "expiresIn": expires_in,
        "scope": scope,
        "testStatus": "active",
        "providerSpecificData": {
            "authMethod": "device_code",
            "email": email,
            "userId": principal_id or None,
            "principalId": principal_id or None,
            "deviceId": principal_id or str(uuidlib.uuid4()),
            "idToken": id_token or None,
            "hasGrokCodeAccess": True,
        },
        "lastError": None,
        "lastErrorAt": None,
        "backoffLevel": 0,
    }

    with sqlite3.connect(str(db_path), timeout=30) as connection:
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT id, priority, createdAt FROM providerConnections "
            "WHERE provider IN ('grok-cli', 'xai') AND email = ? LIMIT 1",
            (email,),
        ).fetchone()
        if existing:
            connection.execute(
                "UPDATE providerConnections SET provider='grok-cli', authType='oauth', "
                "name=?, email=?, isActive=1, data=?, updatedAt=? WHERE id=?",
                (
                    email,
                    email,
                    json.dumps(data, separators=(",", ":")),
                    now,
                    existing[0],
                ),
            )
            connection_id = existing[0]
            action = "updated"
        else:
            priority = connection.execute(
                "SELECT COALESCE(MAX(priority), 0) + 1 FROM providerConnections "
                "WHERE provider = 'grok-cli'"
            ).fetchone()[0]
            connection_id = str(uuidlib.uuid4())
            connection.execute(
                "INSERT INTO providerConnections "
                "(id, provider, authType, name, email, priority, isActive, data, createdAt, updatedAt) "
                "VALUES (?, 'grok-cli', 'oauth', ?, ?, ?, 1, ?, ?, ?)",
                (
                    connection_id,
                    email,
                    email,
                    priority,
                    json.dumps(data, separators=(",", ":")),
                    now,
                    now,
                ),
            )
            action = "created"
        connection.commit()

    log(f"[9router] Grok CLI connection {action}: {email}")
    return {
        "ok": True,
        "action": action,
        "id": connection_id,
        "email": email,
        "backup": str(backup_path),
    }

# ── CLI main ──────────────────────────────────────────────
def main_cli() -> None:
    print("=== Grok Mint CLI ===")
    print("Type 'start' to begin registration, or 'exit' to quit.")
    try:
        import readline
    except ImportError:
        pass
    while True:
        try:
            cmd = input("> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if cmd in ("exit", "quit", "q"):
            break
        elif cmd == "start":
            _cli_run()
        elif cmd:
            print(f"unknown command: {cmd}")
    print("bye")

def _cli_run() -> None:
    cfg = config
    count = int(cfg.get("register_count", 1))
    concurrent = min(3, max(1, int(cfg.get("concurrent_count", 1) or 1)))
    print(f"[cli] target: {count} accounts, {concurrent} workers")

    from grok_register_ttk import (
        _stats_lock, _get_browser, _get_page, _set_worker_id,
        _track_cpa_async_thread, _join_threads_interruptible, _io_lock,
    )

    class _CliApp:
        def __init__(self):
            self.is_running = False
            self.stop_requested = False
            self.success_count = 0
            self.fail_count = 0
            self.session_start_time = time.time()
            self.results = []
            now = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            self.accounts_output_file = os.path.join(
                os.path.dirname(__file__), f"accounts_{now}.txt"
            )
        def log(self, msg):
            print(msg, flush=True)
        def update_stats(self):
            pass
        def should_stop(self):
            return self.stop_requested or not self.is_running
        def _set_running_ui(self, running):
            self.is_running = running
        def _register_one_account(self, log_fn, worker_id=0, local_success=0):
            return gapp.GrokRegisterGUI._register_one_account(self, log_fn, worker_id, local_success)

    app = _CliApp()
    load_config()

    stop_speed = threading.Event()
    def _gui_counts():
        with _stats_lock:
            return app.success_count, app.fail_count

    speed_thread, _meter = start_speed_logger(
        get_counts=_gui_counts,
        log_callback=app.log,
        stop_event=stop_speed,
        interval_sec=float(cfg.get("speed_log_interval_sec", 60) or 60),
    )
    try:
        app._set_running_ui(True)
        if concurrent <= 1:
            _cli_single_worker(app, count)
        else:
            _cli_concurrent_workers(app, count, concurrent)
    except Exception as exc:
        app.log(f"[!] Error: {type(exc).__name__}")
    finally:
        stop_speed.set()
        try:
            speed_thread.join(timeout=2)
        except Exception:
            pass
        _wait_cpa_async_threads(
            timeout=5 if app.should_stop() else 300,
            log_callback=app.log,
            skip_if_stopping=app.should_stop,
        )
        app._set_running_ui(False)
        app.log(f"[*] Selesai — sukses {app.success_count} gagal {app.fail_count}")

def _cli_single_worker(app, count, worker_id=0):
    _set_worker_id(worker_id)
    start_browser(log_callback=app.log)
    app.log("[*] Browser started")
    i = 0
    while i < count:
        if app.should_stop():
            break
        app.log(f"--- Akun ke-{i+1}/{count} ---")
        try:
            _cli_register_one(app, app.log, worker_id, i)
            i += 1
        except RegistrationCancelled:
            app.log("[!] Dibatalkan")
            break
        except AccountRetryNeeded as e:
            app.log(f"[!] Retry needed: {type(e).__name__}")
        except Exception as e:
            with _stats_lock:
                app.fail_count += 1
            i += 1
            app.log(f"[-] Gagal: {type(e).__name__}")
        finally:
            app.update_stats()
        if (i > 0 and i % int(config.get("browser_restart_every", 10) or 0) == 0):
            restart_browser(log_callback=app.log)
    stop_browser()

def _cli_concurrent_workers(app, total_count, worker_count):
    import queue as _queue
    task_queue = _queue.Queue()
    for idx in range(total_count):
        task_queue.put(idx)
    threads = []
    for wid in range(worker_count):
        if app.should_stop():
            break
        t = threading.Thread(
            target=_cli_worker_loop,
            args=(app, wid, task_queue),
            daemon=True,
        )
        t.start()
        threads.append(t)
        time.sleep(0.5)
    _join_threads_interruptible(threads, should_stop=app.should_stop, timeout=None, poll=0.5)

def _cli_worker_loop(app, worker_id, task_queue):
    _set_worker_id(worker_id)
    prefix = f"[W{worker_id}]"
    log_fn = lambda msg: app.log(f"{prefix} {msg}")
    try:
        start_browser(log_callback=log_fn)
        log_fn("[*] Browser started")
    except Exception as e:
        log_fn(f"[!] Browser start failed: {e}")
        return
    try:
        while not app.should_stop():
            try:
                task_queue.get_nowait()
            except Exception:
                break
            try:
                _cli_register_one(app, log_fn, worker_id, 0)
            except RegistrationCancelled:
                return
            except Exception as e:
                with _stats_lock:
                    app.fail_count += 1
                log_fn(f"[-] Gagal: {e}")
            finally:
                app.update_stats()
    finally:
        stop_browser()

def _cli_register_one(app, log_fn, worker_id, local_success):
    log_fn("[*] 1. Membuka halaman pendaftaran")
    open_signup_page(log_callback=log_fn, cancel_callback=app.should_stop)
    log_fn("[*] 2. Membuat email")
    email, dev_token = fill_email_and_submit(
        log_callback=log_fn, cancel_callback=app.should_stop
    )
    log_fn(f"[*] Email: {email}")
    log_fn("[*] 3. Menunggu kode verifikasi")
    code = fill_code_and_submit(
        email, dev_token,
        log_callback=log_fn, cancel_callback=app.should_stop,
    )
    log_fn("[*] Kode verifikasi diterima")
    log_fn("[*] 4. Mengisi profil")
    profile = fill_profile_and_submit(
        log_callback=log_fn, cancel_callback=app.should_stop
    )
    log_fn("[*] 5. Menunggu SSO cookie")
    sso = wait_for_sso_cookie(
        log_callback=log_fn, cancel_callback=app.should_stop
    )
    if config.get("cpa_export_enabled", True):
        log_fn("[*] 6. Ekspor CPA xAI")
        cpa_result = export_cpa_xai_for_account(
            email, profile.get("password", ""), sso=sso,
            log_callback=log_fn, page=_get_page(),
        )
        if cpa_result.get("ok"):
            log_fn(f"[+] CPA xAI: {cpa_result.get('path', '')}")
    if config.get("enable_nsfw", True):
        log_fn("[*] 7. NSFW")
        enable_nsfw_for_token(sso, log_callback=log_fn)
    try:
        line = f"{email}----{profile.get('password','')}----{sso}\n"
        with _io_lock:
            _append_sensitive(app.accounts_output_file, line)
    except Exception as e:
        log_fn(f"[!] File write error: {e}")
    add_token_to_grok2api_pools(sso, email=email, log_callback=log_fn)
    add_token_to_token_only_file(sso, log_callback=log_fn)
    with _stats_lock:
        app.success_count += 1
    log_fn(f"[+] Sukses: {email}")

if __name__ == "__main__":
    main_cli()
