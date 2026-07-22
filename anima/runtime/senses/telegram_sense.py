"""Telegram sense — Bot API long-polling, pure stdlib (Phase 6a).

Config (senses/telegram.json inside the entity root, or a dict):

    {"token_env": "ANIMA_TELEGRAM_TOKEN",   # env var holding the token
     "token": "123:abc",                     # optional literal (wins)
     "allowed_chat_ids": [6902857843, -100123],  # FAIL CLOSED: empty = deny all
     "person_map": {"6902857843": "christopher"},# chat_id or user_id -> person
     "operator_person": "christopher"}

Behavior:
- getUpdates long poll (timeout=50s) in a daemon thread; offset persisted
  to senses/telegram_offset.json so restarts never replay old updates.
- Private chats → AccessContext.direct with the mapped person. Unmapped
  but allowed users get an auto person id "tg-<user_id>" upserted into
  the RelationshipStore — everyone the entity talks to is *somebody*.
- Group/supergroup chats → AccessContext.group with the mapped sender
  as participant. Private-scoped memory is structurally invisible there
  (Phase 4 walls).
- Messages from chats NOT in allowed_chat_ids are ignored and noted in
  the ledger. Fail closed: no allowlist means no chats.
- Replies go back via sendMessage to the chat the wake came from.

Transport is injectable for tests: callable(url, data_dict_or_None,
timeout_s) -> parsed-JSON dict. The default uses urllib. No test ever
touches the network.
"""

from __future__ import annotations

import json
import os
import threading
import urllib.request
from typing import Any, Callable, Dict, List, Optional

from ...relationships import AccessContext

Transport = Callable[[str, Optional[dict], float], dict]

API_BASE = "https://api.telegram.org"


def _urllib_transport(url: str, data: Optional[dict],
                      timeout: float) -> dict:
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json"},
        method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class TelegramSense:
    name = "telegram"

    def __init__(
        self,
        config: Optional[dict] = None,
        *,
        config_path: Optional[str] = None,
        transport: Optional[Transport] = None,
        state_path: Optional[str] = None,
        autostart: bool = True,
        poll_timeout_s: int = 50,
    ):
        if config is None:
            if not config_path or not os.path.exists(config_path):
                raise ValueError(
                    "TelegramSense needs a config dict or an existing "
                    "config_path (senses/telegram.json)")
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)

        token = config.get("token") or os.environ.get(
            str(config.get("token_env") or "ANIMA_TELEGRAM_TOKEN"), "")
        if not token:
            raise ValueError(
                "no Telegram token: set config['token'] or export the "
                f"env var {config.get('token_env', 'ANIMA_TELEGRAM_TOKEN')!r}")
        self.token = str(token)

        # Allowlist — fail closed. Normalized to str for comparison.
        self.allowed_chat_ids = {
            str(c) for c in (config.get("allowed_chat_ids") or [])}
        self.person_map: Dict[str, str] = {
            str(k): str(v)
            for k, v in (config.get("person_map") or {}).items()}
        self.operator_person = str(
            config.get("operator_person") or "operator")

        self.transport: Transport = transport or _urllib_transport
        self.poll_timeout_s = int(poll_timeout_s)
        self.autostart = autostart
        self._state_path = state_path  # resolved at start() if None
        self._offset: Optional[int] = None  # last processed update_id
        self.shell: Any = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ── API plumbing ──────────────────────────────────────────────────
    def _api(self, method: str) -> str:
        return f"{API_BASE}/bot{self.token}/{method}"

    # ── offset persistence ────────────────────────────────────────────
    @property
    def state_path(self) -> str:
        if self._state_path:
            return self._state_path
        if self.shell is not None:
            return os.path.join(self.shell.entity.root, "senses",
                                "telegram_offset.json")
        raise RuntimeError("state_path unknown before start()")

    def _load_offset(self) -> None:
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                doc = json.load(f)
            self._offset = int(doc.get("offset"))
        except (FileNotFoundError, ValueError, TypeError,
                json.JSONDecodeError):
            self._offset = None

    def _save_offset(self) -> None:
        if self._offset is None:
            return
        path = self.state_path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"offset": self._offset}, f)
        os.replace(tmp, path)

    # ── shell lifecycle hooks ─────────────────────────────────────────
    def start(self, shell: Any) -> None:
        self.shell = shell
        self._load_offset()
        if self.autostart:
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._poll_forever, daemon=True,
                name="anima-telegram-sense")
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            # Long poll may be mid-flight (up to poll_timeout_s); it is a
            # daemon thread, so a short join is enough — do not block
            # shutdown-as-settle on Telegram's servers.
            self._thread.join(timeout=2)
            self._thread = None
        self.shell = None

    def _poll_forever(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:
                # network blip / API hiccup: back off briefly, keep living
                self._stop.wait(3.0)

    # ── inbound ───────────────────────────────────────────────────────
    def poll_once(self) -> List[Any]:
        """One getUpdates round: fetch, handle, persist offset.
        Returns the wakes injected (tests use this directly)."""
        payload: dict = {"timeout": self.poll_timeout_s,
                         "allowed_updates": ["message"]}
        if self._offset is not None:
            payload["offset"] = self._offset + 1
        doc = self.transport(self._api("getUpdates"), payload,
                             float(self.poll_timeout_s + 10))
        wakes: List[Any] = []
        for update in (doc.get("result") or []):
            update_id = update.get("update_id")
            wake = self.handle_update(update)
            if wake is not None:
                wakes.append(wake)
            if isinstance(update_id, int):
                self._offset = (update_id if self._offset is None
                                else max(self._offset, update_id))
        self._save_offset()
        return wakes

    def _person_for(self, chat_id: str, user: dict) -> str:
        user_id = str(user.get("id", ""))
        person = self.person_map.get(chat_id) or self.person_map.get(user_id)
        if person:
            return person
        # Unmapped-but-allowed: auto-identity. Everyone is somebody.
        person = f"tg-{user_id or 'unknown'}"
        name = " ".join(x for x in (user.get("first_name"),
                                    user.get("last_name")) if x) \
            or user.get("username") or person
        try:
            self.shell.entity.relationships.upsert_person(
                person, name=name,
                channels={"telegram": user_id or chat_id})
        except Exception:
            pass  # relationship bookkeeping must not drop the message
        return person

    def handle_update(self, update: dict) -> Optional[Any]:
        """Process one Telegram update → inject a message wake (or ignore).
        Returns the wake, or None when the update was dropped."""
        msg = update.get("message")
        if not isinstance(msg, dict):
            return None
        chat = msg.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        chat_type = str(chat.get("type", ""))
        text = msg.get("text")
        if not chat_id or not isinstance(text, str) or not text:
            return None

        if chat_id not in self.allowed_chat_ids:
            # Ignored, but on the record: silence with receipts.
            try:
                self.shell.entity.ledger.log(
                    "sense-telegram", "ignored_chat",
                    detail=f"message from disallowed chat {chat_id} "
                           f"({chat_type}) dropped",
                    source="telegram", outcome="ignored",
                    ts=self.shell.clock())
            except Exception:
                pass
            return None

        sender = msg.get("from") or {}
        person = self._person_for(chat_id, sender)

        if chat_type == "private":
            ctx = AccessContext.direct(person, channel="telegram")
        else:
            # group / supergroup / channel → shared room. Private-scoped
            # memory is structurally invisible here (Phase 4).
            ctx = AccessContext.group([person], channel="telegram")

        wake = self.shell.inject_message(person, text, context=ctx,
                                         via="telegram")
        wake.payload["telegram_chat_id"] = chat.get("id")
        mid = msg.get("message_id")
        if mid is not None:
            wake.payload["telegram_message_id"] = mid
        return wake

    # ── outbound ──────────────────────────────────────────────────────
    def deliver(self, text: str, wake: Any = None) -> None:
        chat_id = None
        if wake is not None:
            chat_id = (wake.payload or {}).get("telegram_chat_id")
        if chat_id is None:
            return  # no originating chat — nowhere honest to send it
        try:
            self.transport(self._api("sendMessage"),
                           {"chat_id": chat_id, "text": text}, 30.0)
        except Exception:
            pass  # a broken sense must not kill the loop
