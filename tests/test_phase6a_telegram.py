"""Phase 6a — Telegram sense: update parsing, allowlist, offset
persistence, reply routing. Fully offline via an injected transport."""

import json

import pytest

from anima.relationships import AccessContext
from anima.runtime.senses.telegram_sense import TelegramSense
from anima.runtime.shell import RuntimeShell


class FakeTransport:
    """callable(url, data, timeout) -> parsed json. Scripted getUpdates
    batches; records every call."""

    def __init__(self, batches=()):
        self.batches = list(batches)
        self.calls = []  # [(url, data, timeout)]

    def __call__(self, url, data, timeout):
        self.calls.append((url, data, timeout))
        if "getUpdates" in url:
            batch = self.batches.pop(0) if self.batches else []
            return {"ok": True, "result": batch}
        if "sendMessage" in url:
            return {"ok": True, "result": {"message_id": 99}}
        return {"ok": True, "result": None}

    def sends(self):
        return [(u, d) for (u, d, _t) in self.calls if "sendMessage" in u]

    def polls(self):
        return [(u, d) for (u, d, _t) in self.calls if "getUpdates" in u]


def upd(update_id, chat_id, text, *, chat_type="private", user_id=None,
        first_name="Test", username=None):
    frm = {"id": user_id if user_id is not None else chat_id,
           "first_name": first_name}
    if username:
        frm["username"] = username
    return {"update_id": update_id,
            "message": {"message_id": update_id * 10,
                        "chat": {"id": chat_id, "type": chat_type},
                        "from": frm,
                        "text": text}}


CONFIG = {
    "token": "0000:TEST-TOKEN",  # literal test token; never a real one
    "allowed_chat_ids": [111, 222, -100333],
    "person_map": {"111": "christopher", "555": "antonia"},
    "operator_person": "christopher",
}


@pytest.fixture()
def shell(tmp_path):
    sh = RuntimeShell(str(tmp_path / "ent"))
    yield sh
    sh.entity.close()


def make_sense(shell, batches=(), config=None):
    transport = FakeTransport(batches)
    sense = TelegramSense(config=dict(config or CONFIG),
                          transport=transport, autostart=False)
    sense.start(shell)
    return sense, transport


# ── update parsing → AccessContext ────────────────────────────────────

def test_private_chat_mapped_person_direct_context(shell):
    sense, _ = make_sense(shell, [[upd(1, 111, "hello")]])
    wakes = sense.poll_once()
    assert len(wakes) == 1
    wake = wakes[0]
    ctx = AccessContext.from_dict(wake.payload["access_context"])
    assert ctx.kind == "direct"
    assert ctx.participants == ("christopher",)
    assert ctx.channel == "telegram"
    assert wake.payload["sender"] == "christopher"
    assert wake.payload["telegram_chat_id"] == 111
    assert wake.payload["via"] == "telegram"


def test_unmapped_allowed_user_gets_auto_person_upserted(shell):
    sense, _ = make_sense(
        shell, [[upd(1, 222, "hi", user_id=777, first_name="Stranger")]])
    wakes = sense.poll_once()
    assert wakes[0].payload["sender"] == "tg-777"
    person = shell.entity.relationships.get_person("tg-777")
    assert person is not None
    assert person["name"] == "Stranger"
    assert person["channels"].get("telegram") == "777"


def test_group_chat_yields_group_context(shell):
    sense, _ = make_sense(
        shell,
        [[upd(1, -100333, "hey all", chat_type="supergroup", user_id=555)]])
    wakes = sense.poll_once()
    ctx = AccessContext.from_dict(wakes[0].payload["access_context"])
    assert ctx.kind == "group"
    assert "antonia" in ctx.participants  # mapped via user_id
    assert wakes[0].payload["telegram_chat_id"] == -100333


# ── allowlist enforcement ─────────────────────────────────────────────

def test_disallowed_chat_ignored_with_ledger_note(shell):
    sense, _ = make_sense(shell, [[upd(1, 666, "let me in")]])
    wakes = sense.poll_once()
    assert wakes == []
    assert shell.entity.scheduler.pending_count() == 0
    recent = shell.entity.ledger.recent(10)
    assert any(r["kind"] == "ignored_chat" and "666" in r["detail"]
               for r in recent)


def test_empty_allowlist_fails_closed(shell):
    cfg = dict(CONFIG)
    cfg["allowed_chat_ids"] = []
    sense, _ = make_sense(shell, [[upd(1, 111, "hello?")]], config=cfg)
    assert sense.poll_once() == []


def test_missing_token_refused():
    cfg = dict(CONFIG)
    cfg.pop("token")
    cfg["token_env"] = "ANIMA_TEST_TOKEN_THAT_DOES_NOT_EXIST"
    with pytest.raises(ValueError, match="token"):
        TelegramSense(config=cfg, transport=FakeTransport(),
                      autostart=False)


# ── offset persistence ────────────────────────────────────────────────

def test_offset_persists_across_instances(shell, tmp_path):
    sense1, t1 = make_sense(shell, [[upd(7, 111, "first")]])
    sense1.poll_once()
    # first poll: no offset yet
    assert "offset" not in t1.polls()[0][1]

    state = json.loads(
        (tmp_path / "ent" / "senses" / "telegram_offset.json").read_text())
    assert state["offset"] == 7

    # a brand-new instance resumes AFTER the last processed update
    sense2, t2 = make_sense(shell, [[]])
    sense2.poll_once()
    assert t2.polls()[0][1]["offset"] == 8


def test_offset_advances_within_a_batch(shell):
    sense, t = make_sense(
        shell, [[upd(3, 111, "a"), upd(4, 111, "b")], []])
    sense.poll_once()
    sense.poll_once()
    assert t.polls()[1][1]["offset"] == 5


# ── replies ───────────────────────────────────────────────────────────

def test_deliver_sends_to_originating_chat(shell):
    sense, t = make_sense(shell, [[upd(1, 111, "ping")]])
    wake = sense.poll_once()[0]
    sense.deliver("pong", wake)
    sends = t.sends()
    assert len(sends) == 1
    url, data = sends[0]
    assert "bot0000:TEST-TOKEN/sendMessage" in url
    assert data == {"chat_id": 111, "text": "pong"}


def test_shell_routes_replies_back_through_telegram_sense(shell):
    sense, t = make_sense(shell, [[upd(1, 111, "ping")]])
    shell.add_sense("telegram", sense)
    wake = sense.poll_once()[0]
    shell._route_replies([{"wake": wake,
                           "report": {"replies": ["routed reply"]}}])
    assert t.sends() == [(sense._api("sendMessage"),
                          {"chat_id": 111, "text": "routed reply"})]


def test_deliver_without_chat_id_is_dropped_not_crashed(shell):
    sense, t = make_sense(shell)
    sense.deliver("orphan", None)
    assert t.sends() == []
