"""`anima service` — systemd user-unit lifecycle. Every test is
offline: the systemctl/loginctl shell-out layer is injected as a
recording fake; the pure functions (unit rendering, name/path/exe
resolution) need no fake at all."""

import os
import subprocess

import pytest

from anima.cli import main
from anima.service import (ServiceManager, build_exec_start,
                           default_name, render_unit,
                           resolve_anima_executable, unit_name,
                           unit_path)


# ── pure functions ────────────────────────────────────────────────────

class TestUnitGeneration:
    def test_render_unit_content(self):
        text = render_unit(
            "luna", "/home/u/entities/luna",
            build_exec_start("/home/u/.local/bin/anima",
                             "/home/u/entities/luna"))
        assert "Description=ANIMA entity: luna" in text
        assert ("ExecStart=/home/u/.local/bin/anima run "
                "--root /home/u/entities/luna --web") in text
        assert "Restart=on-failure" in text
        assert "RestartSec=5" in text
        assert "After=network-online.target" in text
        assert "WantedBy=default.target" in text

    def test_exec_start_flags(self):
        exe = "/usr/bin/anima"
        assert build_exec_start(exe, "/r", web=False).endswith(
            "run --root /r")
        assert "--telegram" in build_exec_start(exe, "/r",
                                                telegram=True)
        assert "--web" in build_exec_start(exe, "/r")

    def test_exec_start_quotes_awkward_paths(self):
        line = build_exec_start("/usr/bin/anima", "/roots/my entity")
        assert "'/roots/my entity'" in line

    def test_names_and_paths(self):
        assert default_name("/a/b/luna/") == "luna"
        assert unit_name("luna") == "anima-luna.service"
        assert unit_path("luna", "/tmp/units") == \
            "/tmp/units/anima-luna.service"

    def test_resolve_from_argv0(self, tmp_path):
        exe = tmp_path / "anima"
        exe.write_text("#!/bin/sh\n")
        exe.chmod(0o755)
        assert resolve_anima_executable(str(exe)) == str(exe)

    def test_resolve_falls_back_to_path(self, tmp_path, monkeypatch):
        exe = tmp_path / "anima"
        exe.write_text("#!/bin/sh\n")
        exe.chmod(0o755)
        monkeypatch.setenv("PATH", str(tmp_path))
        assert resolve_anima_executable("python3") == str(exe)

    def test_resolve_fails_loudly(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PATH", str(tmp_path))  # empty dir
        with pytest.raises(RuntimeError, match="console script"):
            resolve_anima_executable("python3")


# ── the manager, with a recording fake runner ─────────────────────────

class FakeRunner:
    def __init__(self, linger="Linger=yes\n", fail=()):
        self.calls = []
        self.linger = linger
        self.fail = set(fail)          # e.g. {"enable"} to fail enable

    def __call__(self, args, **kw):
        self.calls.append(args)
        verb = args[2] if args[0] == "systemctl" else args[1]
        rc = 1 if verb in self.fail else 0
        out = ""
        if args[0] == "loginctl" and args[1] == "show-user":
            out = self.linger
        if args[0] == "systemctl" and "status" in args:
            out = "● anima-luna.service - ANIMA entity\n   Active: active"
        return subprocess.CompletedProcess(args, rc, stdout=out,
                                           stderr="boom" if rc else "")


@pytest.fixture()
def root(tmp_path):
    r = tmp_path / "luna"
    assert main(["init", str(r)]) == 0
    return str(r)


def make_mgr(tmp_path, runner, *, has_systemctl=True):
    import io
    return ServiceManager(
        unit_dir=str(tmp_path / "units"), runner=runner,
        which=lambda name: ("/usr/bin/" + name if has_systemctl
                            else None),
        out=io.StringIO()), runner


class TestInstall:
    def test_install_writes_unit_and_enables(self, tmp_path, root):
        mgr, runner = make_mgr(tmp_path, FakeRunner())
        rc = mgr.install(root, anima_path="/usr/local/bin/anima")
        assert rc == 0
        path = unit_path("luna", str(tmp_path / "units"))
        text = open(path).read()
        assert f"--root {root} --web" in text
        assert ["systemctl", "--user", "daemon-reload"] in runner.calls
        assert ["systemctl", "--user", "enable", "--now",
                "anima-luna.service"] in runner.calls

    def test_install_refuses_existing_unit(self, tmp_path, root):
        mgr, _ = make_mgr(tmp_path, FakeRunner())
        mgr.install(root, anima_path="/x/anima")
        with pytest.raises(RuntimeError, match="already exists"):
            mgr.install(root, anima_path="/x/anima")

    def test_install_force_overwrites(self, tmp_path, root):
        mgr, _ = make_mgr(tmp_path, FakeRunner())
        mgr.install(root, anima_path="/x/anima")
        assert mgr.install(root, anima_path="/y/anima",
                           force=True) == 0
        text = open(unit_path("luna", str(tmp_path / "units"))).read()
        assert "/y/anima" in text

    def test_install_refuses_missing_root(self, tmp_path):
        mgr, _ = make_mgr(tmp_path, FakeRunner())
        with pytest.raises(RuntimeError, match="no entity root"):
            mgr.install(str(tmp_path / "ghost"), anima_path="/x/anima")

    def test_install_custom_name_and_flags(self, tmp_path, root):
        mgr, _ = make_mgr(tmp_path, FakeRunner())
        mgr.install(root, name="aster", web=False, telegram=True,
                    anima_path="/x/anima")
        path = unit_path("aster", str(tmp_path / "units"))
        text = open(path).read()
        assert "--telegram" in text and "--web" not in text

    def test_install_surfaces_systemctl_failure(self, tmp_path, root):
        mgr, _ = make_mgr(tmp_path, FakeRunner(fail={"enable"}))
        with pytest.raises(RuntimeError, match="enable"):
            mgr.install(root, anima_path="/x/anima")

    def test_non_systemd_platform_refused_clearly(self, tmp_path, root):
        mgr, runner = make_mgr(tmp_path, FakeRunner(),
                               has_systemctl=False)
        with pytest.raises(RuntimeError, match="README"):
            mgr.install(root, anima_path="/x/anima")
        assert runner.calls == []      # never shelled out

    def test_linger_off_prints_exact_command(self, tmp_path, root,
                                             monkeypatch):
        monkeypatch.setenv("USER", "chris")

        class NoLingerRunner(FakeRunner):
            def __call__(self, args, **kw):
                if args[:2] == ["loginctl", "enable-linger"]:
                    self.calls.append(args)
                    return subprocess.CompletedProcess(args, 1,
                                                       stdout="",
                                                       stderr="denied")
                return super().__call__(args, **kw)

        mgr, runner = make_mgr(tmp_path,
                               NoLingerRunner(linger="Linger=no\n"))
        mgr.install(root, anima_path="/x/anima")
        printed = mgr.out.getvalue()
        assert "loginctl enable-linger chris" in printed
        # it TRIED the non-privileged path first
        assert ["loginctl", "enable-linger", "chris"] in runner.calls


class TestLifecycleVerbs:
    def test_verbs_require_installed_unit(self, tmp_path, root):
        mgr, _ = make_mgr(tmp_path, FakeRunner())
        for verb in ("status", "stop", "restart", "uninstall"):
            with pytest.raises(RuntimeError, match="install"):
                getattr(mgr, verb)(root)

    def test_stop_restart_status(self, tmp_path, root):
        mgr, runner = make_mgr(tmp_path, FakeRunner())
        mgr.install(root, anima_path="/x/anima")
        assert mgr.stop(root) == 0
        assert ["systemctl", "--user", "stop",
                "anima-luna.service"] in runner.calls
        assert mgr.restart(root) == 0
        assert ["systemctl", "--user", "restart",
                "anima-luna.service"] in runner.calls
        assert mgr.status(root) == 0
        assert "Active: active" in mgr.out.getvalue()

    def test_uninstall_removes_unit(self, tmp_path, root):
        mgr, runner = make_mgr(tmp_path, FakeRunner())
        mgr.install(root, anima_path="/x/anima")
        path = unit_path("luna", str(tmp_path / "units"))
        assert os.path.exists(path)
        assert mgr.uninstall(root) == 0
        assert not os.path.exists(path)
        assert ["systemctl", "--user", "disable", "--now",
                "anima-luna.service"] in runner.calls
        assert runner.calls[-1] == ["systemctl", "--user",
                                    "daemon-reload"]


class TestCliWiring:
    def test_cli_service_bad_platform_exit_1(self, tmp_path, root,
                                             monkeypatch, capsys):
        monkeypatch.setenv("PATH", str(tmp_path))  # no systemctl
        assert main(["service", "install", "--root", root]) == 1
        assert "systemd" in capsys.readouterr().err

    def test_cli_requires_root_flag(self):
        with pytest.raises(SystemExit):
            main(["service", "install"])
