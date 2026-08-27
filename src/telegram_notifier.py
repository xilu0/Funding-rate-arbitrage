import os
import json
import logging
from typing import Optional, Dict, Any, Tuple
import src.env

try:
    import requests
except ImportError:
    requests = None

logger = logging.getLogger("telegram_notifier")

class TelegramNotifier:
    """
    Client for sending alert messages via Telegram Bot API.
    Supports HTTP/HTTPS and SOCKS5 proxies, Markdown parse mode with plain-text fallback,
    and bot credential verification.
    """

    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
        proxy: Optional[str] = None,
        timeout: int = 15
    ):
        self.bot_token = (os.getenv("TELEGRAM_BOT_TOKEN", "") if bot_token is None else bot_token).strip()
        self.chat_id = (os.getenv("TELEGRAM_CHAT_ID", "") if chat_id is None else chat_id).strip()
        self.proxy = (os.getenv("TELEGRAM_PROXY", "") if proxy is None else proxy).strip()
        self.timeout = timeout
        self._session = None

        if requests is not None:
            self._session = requests.Session()
            if self.proxy:
                self._session.proxies = {
                    "http": self.proxy,
                    "https": self.proxy,
                }

    def is_configured(self) -> bool:
        """Returns True if bot_token and chat_id are both non-empty."""
        return bool(self.bot_token and self.chat_id)

    def verify_bot(self) -> Tuple[bool, Dict[str, Any]]:
        """
        Tests Telegram bot connectivity and credentials via getMe.
        Returns (success, info_dict_or_error).
        """
        if not self.bot_token:
            return False, {"error": "TELEGRAM_BOT_TOKEN is not configured"}

        url = f"https://api.telegram.org/bot{self.bot_token}/getMe"
        try:
            if self._session is not None:
                resp = self._session.get(url, timeout=self.timeout)
                data = resp.json()
            else:
                import urllib.request
                req = urllib.request.Request(url, headers={"User-Agent": "CapitalRateArbitrage/1.0"})
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    data = json.loads(r.read().decode("utf-8"))

            if data.get("ok"):
                return True, data.get("result", {})
            return False, {"error": data.get("description", "Unknown Telegram API error")}
        except Exception as e:
            return False, {"error": str(e)}

    def send_message(
        self,
        text: str,
        parse_mode: Optional[str] = "Markdown",
        disable_web_page_preview: bool = True
    ) -> Tuple[bool, str]:
        """
        Sends a message to the configured Telegram chat.
        If parse_mode (e.g. Markdown) causes a 400 Bad Request error due to formatting entities,
        it automatically falls back to plain text to guarantee message delivery.
        
        Returns (success, response_message).
        """
        if not self.is_configured():
            return False, "Telegram Bot Token or Chat ID not configured"

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "disable_web_page_preview": disable_web_page_preview,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode

        try:
            if self._session is not None:
                resp = self._session.post(url, json=payload, timeout=self.timeout)
                try:
                    data = resp.json()
                except Exception:
                    data = {}

                if resp.status_code == 200 and data.get("ok"):
                    return True, "Message sent successfully"

                # If Markdown entity parsing failed (HTTP 400), retry as plain text
                if resp.status_code == 400 and parse_mode:
                    payload.pop("parse_mode", None)
                    fallback_resp = self._session.post(url, json=payload, timeout=self.timeout)
                    try:
                        fallback_data = fallback_resp.json()
                    except Exception:
                        fallback_data = {}
                    if fallback_resp.status_code == 200 and fallback_data.get("ok"):
                        return True, "Message sent successfully (plain text fallback)"

                err_desc = data.get("description", f"HTTP status {resp.status_code}")
                return False, f"Telegram API error: {err_desc}"
            else:
                import urllib.request
                req_data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    url,
                    data=req_data,
                    headers={"Content-Type": "application/json", "User-Agent": "CapitalRateArbitrage/1.0"}
                )
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    data = json.loads(r.read().decode("utf-8"))
                    if data.get("ok"):
                        return True, "Message sent successfully"
                    return False, f"Telegram API error: {data.get('description', 'Unknown error')}"

        except Exception as e:
            return False, f"Network or request error: {str(e)}"
