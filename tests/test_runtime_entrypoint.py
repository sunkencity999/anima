"""Runtime entrypoint — missing sense configs degrade, never kill.

The 2026-08 incident: a live entity crash-looped silently for ten days
because `--http` was passed and senses/http.json didn't exist. These
tests pin the two-sided fix: init scaffolds http.json, and the
entrypoint drops a sense whose config is missing instead of dying.

All offline; the shell's run loop is replaced with an immediate
shutdown so main() returns.
"""

import json

import pytest

import anima.runtime.__main__ as rt
from anima.cli import main as cli_main
from anima.runtime.shell import RuntimeShell


@pytest.fixture
def root(tmp_path):
    root = tmp_path / "ent"
    assert cli_main(["init", str(root)]) == 0
    return root


@pytest.fixture
def no_run_loop(monkeypatch):
    """main() builds the real shell but never enters the wall-clock loop."""
    monkeypatch.setattr(RuntimeShell, "run", lambda self: self.shutdown())


def test_http_missing_config_warns_and_continues(root, no_run_loop, capsys):
    (root / "senses" / "http.json").unlink()
    assert rt.main(["--root", str(root), "--http"]) == 0
    err = capsys.readouterr().err
    assert "warning: --http requested" in err
    assert "continuing without the http sense" in err


def test_web_missing_config_warns_and_continues(root, no_run_loop, capsys):
    (root / "senses" / "web.json").unlink()
    assert rt.main(["--root", str(root), "--web"]) == 0
    err = capsys.readouterr().err
    assert "warning: --web requested" in err
    assert "continuing without the Observatory" in err


def test_telegram_missing_config_warns_and_continues(root, no_run_loop,
                                                     capsys):
    (root / "senses" / "telegram.json").unlink()
    assert rt.main(["--root", str(root), "--telegram"]) == 0
    err = capsys.readouterr().err
    assert "warning: --telegram requested" in err


def test_http_and_web_missing_together_still_boots(root, no_run_loop,
                                                   capsys):
    (root / "senses" / "http.json").unlink()
    (root / "senses" / "web.json").unlink()
    assert rt.main(["--root", str(root), "--web", "--http"]) == 0
    err = capsys.readouterr().err
    assert err.count("warning:") == 2


def test_http_present_config_attaches_sense(root, monkeypatch):
    """With the scaffolded config present, the sense actually attaches
    (port 0 override so the test binds an ephemeral port)."""
    cfg_path = root / "senses" / "http.json"
    cfg = json.loads(cfg_path.read_text())
    cfg["port"] = 0
    cfg_path.write_text(json.dumps(cfg))

    attached = {}
    real_add = RuntimeShell.add_sense

    def spy_add(self, name, sense):
        attached[name] = sense
        return real_add(self, name, sense)

    monkeypatch.setattr(RuntimeShell, "add_sense", spy_add)
    monkeypatch.setattr(RuntimeShell, "run", lambda self: self.shutdown())
    assert rt.main(["--root", str(root), "--http"]) == 0
    assert "http" in attached
