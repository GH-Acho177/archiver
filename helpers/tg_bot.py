"""
Minimal Telegram bot using long-polling — stdlib only, no third-party packages.
"""
import json
import threading
import time
import urllib.error
import urllib.request


class TelegramBot:
    """
    Long-polls the Telegram Bot API in a daemon thread.

    on_message(text, chat_id, user_id) — called for every inbound text message.
    on_error(reason)                   — called once on unrecoverable errors
                                         (e.g. "invalid_token").
    Both callbacks may be invoked from the background thread; callers should
    use after() to marshal work onto the tkinter main thread.
    """

    def __init__(self, token: str, on_message, on_error=None, on_log=None):
        self._token      = token
        self._on_message = on_message
        self._on_error   = on_error
        self._on_log     = on_log   # on_log(text) — optional, called from bg thread
        self._stop       = threading.Event()
        self._thread: "threading.Thread | None" = None

    def _log(self, text: str):
        if self._on_log:
            self._on_log(text)

    # ── Public ────────────────────────────────────────────────────────────────

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._start_sequence, daemon=True, name="tg-bot-poll"
        )
        self._thread.start()

    def _start_sequence(self):
        # Remove any existing webhook so getUpdates works
        try:
            self._request("deleteWebhook", drop_pending_updates=False)
        except Exception as exc:
            self._log(f"[Bot] deleteWebhook failed: {exc}\n")
        try:
            info = self._request("getMe")
            name = info.get("result", {}).get("username", "?")
            self._log(f"[Bot] Connected as @{name} — ready\n")
        except Exception as exc:
            self._log(f"[Bot] getMe failed: {exc}\n")
            if self._on_error:
                self._on_error("invalid_token")
            return
        self._poll_loop()

    def stop(self):
        self._stop.set()

    def send_message(self, chat_id: int, text: str) -> "tuple[bool, str]":
        """Send a message; returns (True, '') on success or (False, error) on failure."""
        try:
            self._request("sendMessage", chat_id=chat_id, text=text)
            return True, ""
        except Exception as exc:
            return False, str(exc)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _request(self, method: str, **params):
        url  = f"https://api.telegram.org/bot{self._token}/{method}"
        body = json.dumps(params).encode()
        req  = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=35) as resp:
            return json.loads(resp.read())

    def _poll_loop(self):
        offset = 0
        while not self._stop.is_set():
            try:
                result = self._request(
                    "getUpdates",
                    offset=offset,
                    timeout=30,
                    allowed_updates=["message"],
                )
                for update in result.get("result", []):
                    offset = update["update_id"] + 1
                    self._dispatch(update)
            except urllib.error.HTTPError as exc:
                if exc.code == 401:
                    self._stop.set()
                    if self._on_error:
                        self._on_error("invalid_token")
                    return
                self._log(f"[Bot] Poll error {exc.code}: {exc}\n")
                if not self._stop.is_set():
                    time.sleep(5)
            except Exception as exc:
                self._log(f"[Bot] Poll error: {exc}\n")
                if not self._stop.is_set():
                    time.sleep(5)

    def _dispatch(self, update: dict):
        msg     = update.get("message", {})
        text    = msg.get("text", "").strip()
        chat_id = msg.get("chat", {}).get("id")
        user_id = msg.get("from", {}).get("id")
        if text and chat_id and user_id:
            self._on_message(text, chat_id, user_id)
