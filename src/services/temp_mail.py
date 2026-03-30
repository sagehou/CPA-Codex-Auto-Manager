"""
Temp-Mail email service backed by cloudflare_temp_email.
"""

import json
import logging
import random
import re
import string
import time
from email import policy
from email.header import decode_header
from email.parser import Parser
from typing import Any, Dict, List, Optional

from .base import BaseEmailService, EmailServiceError, EmailServiceType
from ..core.http_client import HTTPClient, RequestConfig
from ..config.constants import OTP_CODE_PATTERN, OTP_CODE_SEMANTIC_PATTERN


logger = logging.getLogger(__name__)


class TempMailService(BaseEmailService):
    """Self-hosted Temp-Mail service for cloudflare_temp_email workers."""

    def __init__(self, config: Dict[str, Any] = None, name: str = None):
        super().__init__(EmailServiceType.TEMP_MAIL, name)

        default_config = {
            "enable_prefix": True,
            "timeout": 30,
            "max_retries": 3,
            "custom_auth": "",
        }
        self.config = {**default_config, **(config or {})}
        self.config["base_url"] = str(self.config.get("base_url") or "").rstrip("/")
        self.domains = self._normalize_domains(self.config.get("domain"))

        missing = []
        if not self.config["base_url"]:
            missing.append("base_url")
        if not str(self.config.get("admin_password") or "").strip():
            missing.append("admin_password")
        if not self.domains:
            missing.append("domain")
        if missing:
            raise ValueError(f"Missing required config: {missing}")

        self.http_client = HTTPClient(
            proxy_url=None,
            config=RequestConfig(
                timeout=int(self.config["timeout"]),
                max_retries=int(self.config["max_retries"]),
            ),
        )

        self._email_cache: Dict[str, Dict[str, Any]] = {}
        self._last_used_mail_ids: Dict[str, str] = {}

    @staticmethod
    def _normalize_domains(value: Any) -> List[str]:
        if isinstance(value, list):
            items = value
        elif isinstance(value, str):
            text = value.strip()
            if not text:
                items = []
            else:
                try:
                    parsed = json.loads(text)
                    items = parsed if isinstance(parsed, list) else [text]
                except Exception:
                    items = [part.strip() for part in text.split(",")]
        else:
            items = []

        domains: List[str] = []
        for item in items:
            domain = str(item or "").strip()
            if domain:
                domains.append(domain)
        return domains

    def _base_headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        custom_auth = str(self.config.get("custom_auth") or "").strip()
        if custom_auth:
            headers["x-custom-auth"] = custom_auth
        return headers

    def _admin_headers(self) -> Dict[str, str]:
        headers = self._base_headers()
        headers["x-admin-auth"] = str(self.config["admin_password"]).strip()
        return headers

    def _make_request(self, method: str, path: str, **kwargs) -> Any:
        headers = self._base_headers()
        request_headers = kwargs.pop("headers", {}) or {}
        if path.startswith("/admin/"):
            headers.update(self._admin_headers())
        headers.update(request_headers)

        response = self.http_client.request(
            method,
            f"{self.config['base_url']}{path}",
            headers=headers,
            **kwargs,
        )

        if response.status_code >= 400:
            try:
                detail = response.json()
            except Exception:
                detail = response.text[:200]
            raise EmailServiceError(f"Request failed: {response.status_code} - {detail}")

        try:
            return response.json()
        except Exception:
            return {"raw_response": response.text}

    @staticmethod
    def _extract_mails(response: Any) -> List[Dict[str, Any]]:
        if isinstance(response, list):
            return [item for item in response if isinstance(item, dict)]
        if not isinstance(response, dict):
            return []
        for key in ("results", "mails", "data", "items", "list"):
            value = response.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
            if isinstance(value, dict):
                nested = TempMailService._extract_mails(value)
                if nested:
                    return nested
        return []

    @staticmethod
    def _extract_mail_id(mail: Dict[str, Any]) -> str:
        for key in ("id", "mail_id", "mailId", "_id", "uuid"):
            value = mail.get(key)
            if value:
                return str(value)
        return json.dumps(mail, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _decode_header_value(value: Any) -> str:
        text = str(value or "")
        if not text:
            return ""

        decoded_parts: List[str] = []
        for part, charset in decode_header(text):
            if isinstance(part, bytes):
                try:
                    decoded_parts.append(part.decode(charset or "utf-8", errors="replace"))
                except Exception:
                    decoded_parts.append(part.decode("utf-8", errors="replace"))
            else:
                decoded_parts.append(str(part))
        return "".join(decoded_parts).strip()

    @classmethod
    def _parse_raw_message(cls, raw_message: str) -> Dict[str, str]:
        if not raw_message:
            return {"sender": "", "subject": "", "body": "", "raw": ""}

        try:
            message = Parser(policy=policy.default).parsestr(raw_message)
        except Exception:
            return {"sender": "", "subject": "", "body": "", "raw": raw_message}

        sender = cls._decode_header_value(message.get("From"))
        subject = cls._decode_header_value(message.get("Subject"))
        text_parts: List[str] = []
        html_parts: List[str] = []

        for part in message.walk():
            if part.is_multipart():
                continue

            content_type = part.get_content_type()
            try:
                payload = part.get_content()
            except Exception:
                try:
                    payload_bytes = part.get_payload(decode=True) or b""
                    payload = payload_bytes.decode(part.get_content_charset() or "utf-8", errors="replace")
                except Exception:
                    payload = ""

            if payload is None:
                continue

            content = str(payload).strip()
            if not content:
                continue

            if content_type == "text/plain":
                text_parts.append(content)
            elif content_type == "text/html":
                html_parts.append(content)

        body = "\n".join(text_parts).strip()
        raw = raw_message
        if not body and html_parts:
            body = re.sub(r"<[^>]+>", " ", "\n".join(html_parts))

        return {
            "sender": sender,
            "subject": subject,
            "body": body.strip(),
            "raw": raw,
        }

    @staticmethod
    def _extract_mail_fields(mail: Dict[str, Any]) -> Dict[str, str]:
        raw = str(mail.get("raw") or mail.get("source") or "").strip()
        parsed = TempMailService._parse_raw_message(raw)

        sender = (
            parsed["sender"]
            or str(mail.get("from") or mail.get("source") or mail.get("mail_from") or "").strip()
        )
        subject = (
            parsed["subject"]
            or str(mail.get("subject") or mail.get("title") or mail.get("mail_subject") or "").strip()
        )
        body = (
            parsed["body"]
            or str(mail.get("text") or mail.get("body") or mail.get("content") or mail.get("html") or mail.get("mail_text") or "").strip()
        )

        if not body and raw:
            body = re.sub(r"<[^>]+>", " ", raw)
        return {"sender": sender, "subject": subject, "body": body, "raw": raw}

    @staticmethod
    def _is_openai_otp_mail(sender: str, subject: str, body: str, raw: str) -> bool:
        blob = "\n".join([sender, subject, body, raw]).lower()
        if "openai" not in blob:
            return False
        return any(
            keyword in blob
            for keyword in (
                "verification code",
                "verify",
                "one-time code",
                "one time code",
                "otp",
                "security code",
                "log in",
                "login",
            )
        )

    @staticmethod
    def _extract_otp(content: str, pattern: str) -> Optional[str]:
        semantic = re.search(OTP_CODE_SEMANTIC_PATTERN, content, re.IGNORECASE)
        if semantic:
            return semantic.group(1)
        simple = re.search(pattern, content)
        if simple:
            return simple.group(1)
        return None

    def _fetch_user_mails(self, jwt: str) -> List[Dict[str, Any]]:
        if not jwt:
            return []
        try:
            response = self._make_request(
                "GET",
                "/api/mails",
                headers={
                    "Authorization": f"Bearer {jwt}",
                    "Accept": "application/json",
                },
                params={"limit": 50, "offset": 0},
            )
            return self._extract_mails(response)
        except Exception as exc:
            logger.debug("TempMail address mailbox fetch failed via /api/mails: %s", exc)
            return []

    def _fetch_admin_mails(self, email: str) -> List[Dict[str, Any]]:
        params_variants = [
            {"limit": 50, "offset": 0, "address": email},
            {"limit": 50, "offset": 0},
        ]

        for params in params_variants:
            try:
                response = self._make_request("GET", "/admin/mails", params=params)
                mails = self._extract_mails(response)
                if "address" not in params:
                    target = email.lower()
                    mails = [
                        mail for mail in mails
                        if target in str(mail.get("address") or mail.get("email") or mail.get("to") or "").lower()
                        or target in json.dumps(mail, ensure_ascii=False).lower()
                    ]
                if mails:
                    return mails
            except Exception as exc:
                logger.debug("TempMail admin mailbox fetch failed: %s", exc)
        return []

    def _choose_domain(self) -> str:
        return random.choice(self.domains)

    def create_email(self, config: Dict[str, Any] = None) -> Dict[str, Any]:
        letters = "".join(random.choices(string.ascii_lowercase, k=5))
        digits = "".join(random.choices(string.digits, k=random.randint(1, 3)))
        suffix = "".join(random.choices(string.ascii_lowercase, k=random.randint(1, 3)))
        name = f"{letters}{digits}{suffix}"

        request_config = config or {}
        domain = request_config.get("domain")
        if isinstance(domain, list):
            domain = random.choice([str(item).strip() for item in domain if str(item).strip()])
        domain = str(domain or self._choose_domain()).strip()

        response = self._make_request(
            "POST",
            "/admin/new_address",
            json={
                "enablePrefix": bool(self.config.get("enable_prefix", True)),
                "name": name,
                "domain": domain,
            },
        )

        address = str(response.get("address") or "").strip()
        jwt = str(response.get("jwt") or "").strip()
        address_id = str(response.get("address_id") or response.get("id") or response.get("addressId") or "").strip()

        if not address:
            raise EmailServiceError(f"Incomplete response from temp mail service: {response}")
        if not jwt:
            logger.warning("TempMail new_address response missing jwt for %s: %s", address, response)

        email_info = {
            "email": address,
            "jwt": jwt,
            "address_id": address_id,
            "service_id": jwt or address,
            "id": address,
            "created_at": time.time(),
        }
        self._email_cache[address] = email_info
        self.update_status(True)
        logger.info(
            "TempMail address created: %s (has_jwt=%s, address_id=%s)",
            address,
            bool(jwt),
            address_id or "-",
        )
        return email_info

    def get_verification_code(
        self,
        email: str,
        email_id: str = None,
        timeout: int = 120,
        pattern: str = OTP_CODE_PATTERN,
        otp_sent_at: Optional[float] = None,
    ) -> Optional[str]:
        del otp_sent_at
        start = time.time()
        effective_timeout = max(int(timeout), 60)
        seen_mail_ids: set[str] = set()
        jwt = str(email_id or self._email_cache.get(email, {}).get("jwt") or "").strip()
        last_used_mail_id = self._last_used_mail_ids.get(email)

        logger.info(
            "TempMail OTP polling started for %s (has_jwt=%s, timeout=%s)",
            email,
            bool(jwt),
            effective_timeout,
        )

        while time.time() - start < effective_timeout:
            try:
                mails = self._fetch_user_mails(jwt)
                if mails:
                    logger.debug("TempMail inbox fetch via address jwt returned %s mails for %s", len(mails), email)
                else:
                    mails = self._fetch_admin_mails(email)
                    if mails:
                        logger.debug("TempMail inbox fetch via admin returned %s mails for %s", len(mails), email)

                for mail in mails:
                    mail_id = self._extract_mail_id(mail)
                    if mail_id in seen_mail_ids or mail_id == last_used_mail_id:
                        continue
                    seen_mail_ids.add(mail_id)

                    parsed = self._extract_mail_fields(mail)
                    if not self._is_openai_otp_mail(
                        parsed["sender"],
                        parsed["subject"],
                        parsed["body"],
                        parsed["raw"],
                    ):
                        continue

                    code = self._extract_otp(
                        "\n".join([parsed["sender"], parsed["subject"], parsed["body"], parsed["raw"]]),
                        pattern,
                    )
                    if not code:
                        continue

                    self._last_used_mail_ids[email] = mail_id
                    self.update_status(True)
                    logger.info("TempMail OTP found for %s via mail %s", email, mail_id)
                    return code
            except Exception as exc:
                logger.debug("TempMail OTP polling failed for %s: %s", email, exc)

            time.sleep(3)

        logger.warning("TempMail OTP polling timed out for %s", email)
        return None

    def list_emails(self, limit: int = 50, offset: int = 0, **kwargs) -> List[Dict[str, Any]]:
        params = {"limit": max(1, int(limit)), "offset": max(0, int(offset))}
        params.update({key: value for key, value in kwargs.items() if value is not None})

        try:
            response = self._make_request("GET", "/admin/mails", params=params)
            mails = self._extract_mails(response)
            output = []
            for mail in mails:
                address = str(mail.get("address") or mail.get("email") or "").strip()
                output.append(
                    {
                        "id": str(mail.get("id") or address),
                        "service_id": str(mail.get("id") or address),
                        "email": address,
                        "subject": mail.get("subject"),
                        "from": mail.get("source") or mail.get("from"),
                        "created_at": mail.get("createdAt") or mail.get("created_at") or mail.get("date"),
                        "raw_data": mail,
                    }
                )
            self.update_status(True)
            return output
        except Exception as exc:
            logger.warning("TempMail list_emails failed: %s", exc)
            self.update_status(False, exc)
            return list(self._email_cache.values())

    def delete_email(self, email_id: str) -> bool:
        removed = False
        for address, info in list(self._email_cache.items()):
            candidate_ids = {address, str(info.get("id") or ""), str(info.get("service_id") or "")}
            if email_id in candidate_ids:
                self._email_cache.pop(address, None)
                removed = True
        return removed

    def check_health(self) -> bool:
        try:
            self._make_request("GET", "/admin/mails", params={"limit": 1, "offset": 0})
            self.update_status(True)
            return True
        except Exception as exc:
            logger.warning("TempMail health check failed: %s", exc)
            self.update_status(False, exc)
            return False
