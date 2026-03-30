"""
ChatGPT/Auth flow utilities.
"""

from dataclasses import dataclass, field
import base64
import hashlib
import json
import random
import re
import secrets
import string
import time
import uuid
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse


@dataclass
class FlowState:
    """Unified representation of OpenAI auth flow state."""

    page_type: str = ""
    continue_url: str = ""
    method: str = "GET"
    current_url: str = ""
    source: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)


def generate_random_name() -> Tuple[str, str]:
    """Generate a natural English first/last name pair."""
    first = [
        "James", "Robert", "John", "Michael", "David", "William", "Richard",
        "Mary", "Jennifer", "Linda", "Elizabeth", "Susan", "Jessica", "Sarah",
        "Emily", "Emma", "Olivia", "Sophia", "Liam", "Noah", "Oliver", "Ethan",
    ]
    last = [
        "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
        "Davis", "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Martin",
    ]
    return random.choice(first), random.choice(last)


def generate_random_birthday() -> str:
    """Generate a birthdate string in YYYY-MM-DD format."""
    year = random.randint(1996, 2006)
    month = random.randint(1, 12)
    day = random.randint(1, 28)
    return f"{year:04d}-{month:02d}-{day:02d}"


def generate_random_password(length: int = 16) -> str:
    """Generate a password satisfying mixed character classes."""
    chars = string.ascii_letters + string.digits + "!@#$%"
    pwd = list(
        random.choice(string.ascii_uppercase)
        + random.choice(string.ascii_lowercase)
        + random.choice(string.digits)
        + random.choice("!@#$%")
        + "".join(random.choice(chars) for _ in range(max(length - 4, 8)))
    )
    random.shuffle(pwd)
    return "".join(pwd)


def generate_datadog_trace() -> Dict[str, str]:
    """Generate lightweight Datadog tracing headers matching browser traffic."""
    trace_id = str(random.getrandbits(64))
    parent_id = str(random.getrandbits(64))
    trace_hex = format(int(trace_id), "016x")
    parent_hex = format(int(parent_id), "016x")
    return {
        "traceparent": f"00-0000000000000000{trace_hex}-{parent_hex}-01",
        "tracestate": "dd=s:1;o:rum",
        "x-datadog-origin": "rum",
        "x-datadog-parent-id": parent_id,
        "x-datadog-sampling-priority": "1",
        "x-datadog-trace-id": trace_id,
    }


def generate_pkce() -> tuple[str, str]:
    """Generate PKCE verifier/challenge."""
    code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


def decode_jwt_payload(token: str) -> Dict[str, Any]:
    """Decode JWT payload without verifying signature."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return {}
        payload = parts[1]
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += "=" * padding
        decoded = base64.urlsafe_b64decode(payload)
        return json.loads(decoded)
    except Exception:
        return {}


def normalize_page_type(value: str) -> str:
    """Normalize page.type to snake_case for branch logic."""
    return str(value or "").strip().lower().replace("-", "_").replace("/", "_").replace(" ", "_")


def normalize_flow_url(url: str, auth_base: str = "https://auth.openai.com") -> str:
    """Normalize relative flow URLs to absolute URLs."""
    value = str(url or "").strip()
    if not value:
        return ""
    if value.startswith("//"):
        return f"https:{value}"
    if value.startswith("/"):
        return f"{auth_base.rstrip('/')}{value}"
    return value


def infer_page_type_from_url(url: str) -> str:
    """Infer flow state from URL when server payload is incomplete."""
    if not url:
        return ""

    try:
        parsed = urlparse(url)
    except Exception:
        return ""

    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").lower()

    if "code=" in (parsed.query or ""):
        return "oauth_callback"
    if "chatgpt.com" in host and "/api/auth/callback/" in path:
        return "callback"
    if "create-account/password" in path:
        return "create_account_password"
    if "email-verification" in path or "email-otp" in path:
        return "email_otp_verification"
    if "about-you" in path:
        return "about_you"
    if "log-in/password" in path:
        return "login_password"
    if "sign-in-with-chatgpt" in path and "consent" in path:
        return "consent"
    if "workspace" in path and "select" in path:
        return "workspace_selection"
    if "organization" in path and "select" in path:
        return "organization_selection"
    if "add-phone" in path:
        return "add_phone"
    if "callback" in path:
        return "callback"
    if "chatgpt.com" in host and path in {"", "/"}:
        return "chatgpt_home"
    if path:
        return normalize_page_type(path.strip("/").replace("/", "_"))
    return ""


def extract_flow_state(
    data: Optional[Dict[str, Any]] = None,
    current_url: str = "",
    auth_base: str = "https://auth.openai.com",
    default_method: str = "GET",
) -> FlowState:
    """Extract normalized flow state from API response or URL."""
    raw = data if isinstance(data, dict) else {}
    page = raw.get("page") or {}
    payload = page.get("payload") or {}

    continue_url = normalize_flow_url(
        raw.get("continue_url") or payload.get("url") or "",
        auth_base=auth_base,
    )
    effective_current_url = continue_url if raw and continue_url else current_url
    current = normalize_flow_url(effective_current_url or continue_url, auth_base=auth_base)
    page_type = normalize_page_type(page.get("type")) or infer_page_type_from_url(continue_url or current)
    method = str(raw.get("method") or payload.get("method") or default_method or "GET").upper()

    return FlowState(
        page_type=page_type,
        continue_url=continue_url,
        method=method,
        current_url=current,
        source="api" if raw else "url",
        payload=payload if isinstance(payload, dict) else {},
        raw=raw,
    )


def describe_flow_state(state: FlowState) -> str:
    """Generate a compact log-friendly state description."""
    target = state.continue_url or state.current_url or "-"
    return f"page={state.page_type or '-'} method={state.method or '-'} next={target[:80]}..."


def random_delay(low: float = 0.3, high: float = 1.0) -> None:
    """Sleep for a random interval."""
    time.sleep(random.uniform(low, high))


def extract_chrome_full_version(user_agent: str) -> str:
    """Extract full Chrome version from user agent."""
    if not user_agent:
        return ""
    match = re.search(r"Chrome/([0-9.]+)", user_agent)
    return match.group(1) if match else ""


def _registrable_domain(hostname: str) -> str:
    """Roughly estimate registrable domain for Sec-Fetch-Site."""
    if not hostname:
        return ""
    host = hostname.split(":")[0].strip(".").lower()
    parts = [part for part in host.split(".") if part]
    if len(parts) <= 2:
        return ".".join(parts)
    return ".".join(parts[-2:])


def infer_sec_fetch_site(url: str, referer: Optional[str] = None, navigation: bool = False) -> str:
    """Infer Sec-Fetch-Site from target URL and referer."""
    if not referer:
        return "none" if navigation else "same-origin"

    try:
        target = urlparse(url or "")
        source = urlparse(referer or "")
        if not target.scheme or not target.netloc or not source.netloc:
            return "none" if navigation else "same-origin"
        if (target.scheme, target.netloc) == (source.scheme, source.netloc):
            return "same-origin"
        if _registrable_domain(target.hostname or "") == _registrable_domain(source.hostname or ""):
            return "same-site"
    except Exception:
        pass

    return "cross-site"


def build_sec_ch_ua_full_version_list(sec_ch_ua: str, chrome_full_version: str) -> str:
    """Build sec-ch-ua-full-version-list from sec-ch-ua."""
    if not sec_ch_ua or not chrome_full_version:
        return ""

    entries = []
    for brand, version in re.findall(r'"([^"]+)";v="([^"]+)"', sec_ch_ua):
        full_version = chrome_full_version if brand in {"Chromium", "Google Chrome"} else f"{version}.0.0.0"
        entries.append(f'"{brand}";v="{full_version}"')
    return ", ".join(entries)


def build_browser_headers(
    *,
    url: str,
    user_agent: str,
    sec_ch_ua: Optional[str] = None,
    chrome_full_version: Optional[str] = None,
    accept: Optional[str] = None,
    accept_language: str = "en-US,en;q=0.9",
    referer: Optional[str] = None,
    origin: Optional[str] = None,
    content_type: Optional[str] = None,
    navigation: bool = False,
    fetch_mode: Optional[str] = None,
    fetch_dest: Optional[str] = None,
    fetch_site: Optional[str] = None,
    headed: bool = False,
    extra_headers: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """Build request headers close to real Chrome traffic."""
    chrome_full = chrome_full_version or extract_chrome_full_version(user_agent)
    full_version_list = build_sec_ch_ua_full_version_list(sec_ch_ua or "", chrome_full)

    headers = {
        "User-Agent": user_agent or "Mozilla/5.0",
        "Accept-Language": accept_language,
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-ch-ua-arch": '"x86"',
        "sec-ch-ua-bitness": '"64"',
    }

    if accept:
        headers["Accept"] = accept
    if referer:
        headers["Referer"] = referer
    if origin:
        headers["Origin"] = origin
    if content_type:
        headers["Content-Type"] = content_type
    if sec_ch_ua:
        headers["sec-ch-ua"] = sec_ch_ua
    if chrome_full:
        headers["sec-ch-ua-full-version"] = f'"{chrome_full}"'
        headers["sec-ch-ua-platform-version"] = '"15.0.0"'
    if full_version_list:
        headers["sec-ch-ua-full-version-list"] = full_version_list

    if navigation:
        headers["Sec-Fetch-Dest"] = "document"
        headers["Sec-Fetch-Mode"] = "navigate"
        headers["Sec-Fetch-User"] = "?1"
        headers["Upgrade-Insecure-Requests"] = "1"
        headers["Cache-Control"] = "max-age=0"
    else:
        headers["Sec-Fetch-Dest"] = fetch_dest or "empty"
        headers["Sec-Fetch-Mode"] = fetch_mode or "cors"

    headers["Sec-Fetch-Site"] = fetch_site or infer_sec_fetch_site(url, referer, navigation=navigation)

    if headed:
        headers.setdefault("Priority", "u=0, i" if navigation else "u=1, i")
        headers.setdefault("DNT", "1")
        headers.setdefault("Sec-GPC", "1")

    if extra_headers:
        for key, value in extra_headers.items():
            if value is not None:
                headers[key] = value

    return headers


def seed_oai_device_cookie(session, device_id: str) -> None:
    """Seed oai-did cookie across relevant domains."""
    for domain in (
        "chatgpt.com",
        ".chatgpt.com",
        "openai.com",
        ".openai.com",
        "auth.openai.com",
        ".auth.openai.com",
    ):
        try:
            session.cookies.set("oai-did", device_id, domain=domain)
        except Exception:
            continue
