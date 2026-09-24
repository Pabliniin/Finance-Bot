"""Actualizacion automatica: solo sobre instalaciones sin git, con copia de
seguridad, y con vuelta atras si el codigo nuevo no arranca."""

from __future__ import annotations

import io
import json
import zipfile
from types import SimpleNamespace

import pytest

from finance_bot import updater


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Una instalacion falsa (sin .git) en una carpeta temporal."""
    root = tmp_path / "bot"
    (root / "finance_bot").mkdir(parents=True)
    (root / "finance_bot" / "__main__.py").write_text("VERSION = 'vieja'\n", encoding="utf-8")
    (root / "config").mkdir()
    (root / "config" / "settings.yaml").write_text("mode: strict\n", encoding="utf-8")
    (root / "data").mkdir()
    (root / "data" / "bot.sqlite3").write_text("mis datos", encoding="utf-8")
    (root / ".env").write_text("DISCORD_BOT_TOKEN=secreto\n", encoding="utf-8")
    monkeypatch.setattr(updater, "PROJECT_ROOT", root)
    monkeypatch.setattr(updater, "STATE_FILE", root / "data" / "update_state.json")
    monkeypatch.setattr(updater, "BACKUP_DIR", root / ".update_backup")
    # ni pip ni git reales: un resultado vacio, como en una instalacion sin git
    monkeypatch.setattr(updater.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="", returncode=0))
    return root


def _fake_zip(sha: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(f"Finance-Bot-{sha}/finance_bot/__main__.py", "VERSION = 'nueva'\n")
        archive.writestr(f"Finance-Bot-{sha}/config/settings.yaml", "mode: informative\n")
    return buffer.getvalue()


class _Response:
    def __init__(self, content: bytes | None = None, payload: dict | None = None):
        self.content, self._payload = content, payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._payload or {}


def test_developer_install_is_never_touched(sandbox, monkeypatch) -> None:
    (sandbox / ".git").mkdir()
    monkeypatch.setattr(updater, "latest_sha", lambda timeout=20: "abc123")
    assert updater.update_available() is None
    assert updater.update_available(force=True) == "abc123"


def test_update_replaces_code_keeps_data_and_backs_up(sandbox, monkeypatch) -> None:
    def fake_get(url, timeout=None, headers=None):
        if "codeload" in url:
            return _Response(content=_fake_zip("abc123"))
        return _Response(payload={"sha": "abc123"})

    monkeypatch.setattr(updater.requests, "get", fake_get)
    assert updater.update_available() == "abc123"
    assert updater.check_and_apply() is True

    assert "nueva" in (sandbox / "finance_bot" / "__main__.py").read_text(encoding="utf-8")
    assert "informative" in (sandbox / "config" / "settings.yaml").read_text(encoding="utf-8")
    assert (sandbox / "data" / "bot.sqlite3").read_text(encoding="utf-8") == "mis datos"  # datos intactos
    assert (sandbox / ".env").read_text(encoding="utf-8").startswith("DISCORD_BOT_TOKEN=secreto")  # .env intacto
    assert "vieja" in (sandbox / ".update_backup" / "finance_bot" / "__main__.py").read_text(encoding="utf-8")
    assert updater.current_sha() == "abc123"
    assert updater.update_available() is None  # ya al dia


def test_broken_update_rolls_back_after_failed_boots(sandbox, monkeypatch) -> None:
    def fake_get(url, timeout=None, headers=None):
        return _Response(content=_fake_zip("bad")) if "codeload" in url else _Response(payload={"sha": "bad"})

    monkeypatch.setattr(updater.requests, "get", fake_get)
    assert updater.check_and_apply() is True
    for _ in range(updater.MAX_FAILED_BOOTS + 1):
        updater.boot_guard_start()  # arranca... y muere antes de boot_guard_ok
    assert "vieja" in (sandbox / "finance_bot" / "__main__.py").read_text(encoding="utf-8")
    assert json.loads((sandbox / "data" / "update_state.json").read_text(encoding="utf-8"))["previous"] is False


def test_successful_boot_resets_the_counter(sandbox, monkeypatch) -> None:
    def fake_get(url, timeout=None, headers=None):
        return _Response(content=_fake_zip("ok1")) if "codeload" in url else _Response(payload={"sha": "ok1"})

    monkeypatch.setattr(updater.requests, "get", fake_get)
    updater.check_and_apply()
    updater.boot_guard_start()
    updater.boot_guard_ok()
    state = json.loads((sandbox / "data" / "update_state.json").read_text(encoding="utf-8"))
    assert state["failed_boots"] == 0 and state["sha"] == "ok1"


def test_bad_download_leaves_everything_untouched(sandbox, monkeypatch) -> None:
    def fake_get(url, timeout=None, headers=None):
        if "codeload" in url:
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                archive.writestr("otra-cosa/README.md", "no es el bot")
            return _Response(content=buffer.getvalue())
        return _Response(payload={"sha": "zzz"})

    monkeypatch.setattr(updater.requests, "get", fake_get)
    assert updater.check_and_apply() is False
    assert "vieja" in (sandbox / "finance_bot" / "__main__.py").read_text(encoding="utf-8")


def test_state_written_by_powershell_with_bom_is_readable(sandbox) -> None:
    """preparar_traslado.ps1 escribe el JSON con BOM (Set-Content -Encoding utf8)."""
    (sandbox / "data" / "update_state.json").write_bytes(b'\xef\xbb\xbf{"sha": "empaquetado", "previous": false}')
    assert updater.current_sha() == "empaquetado"


def test_restart_reexecs_the_process_on_posix(monkeypatch) -> None:
    """En Linux el bot se reinicia solo re-ejecutandose, sin depender de un
    supervisor externo."""
    calls: dict = {}
    monkeypatch.setattr(updater.sys, "platform", "linux")
    monkeypatch.setattr(updater.os, "execv", lambda exe, args: calls.setdefault("execv", (exe, args)))
    updater.restart_process()
    exe, args = calls["execv"]
    assert exe == updater.sys.executable
    assert args[1:] == ["-m", "finance_bot", "run"]


def test_restart_uses_the_task_on_windows(monkeypatch) -> None:
    """En Windows se mantiene el metodo probado (tarea programada), sin os.execv."""
    calls: dict = {}
    monkeypatch.setattr(updater.sys, "platform", "win32")
    monkeypatch.setattr(updater.os, "execv", lambda *a: calls.setdefault("execv", True))
    monkeypatch.setattr(updater.subprocess, "Popen", lambda *a, **k: calls.setdefault("popen", True))
    updater.restart_process()
    assert calls.get("popen") and "execv" not in calls
