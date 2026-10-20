import queue
import sys
from types import SimpleNamespace

import pytest

from app.collector import describe_read_error, read_child
from app import history_reader, read_diagnostics


@pytest.fixture(autouse=True)
def isolated_diagnostics(monkeypatch, tmp_path):
    monkeypatch.setattr('app.accessibility.ensure_accessibility', lambda **kwargs: {"state": "already_accessible"})
    original = read_diagnostics.ReadDiagnostics
    monkeypatch.setattr(read_diagnostics, "ReadDiagnostics", lambda *args, **kwargs: original(tmp_path / "read.json", notify=kwargs.get("notify")))


@pytest.mark.parametrize("name,code", [
    ("NotFoundError", "wechat_ui_unavailable"),
    ("NotLoginError", "wechat_not_logged_in"),
    ("NotStartError", "wechat_not_running"),
    ("NoSuchFriendError", "group_not_found"),
    ("ModuleNotFoundError", "dependency_missing"),
    ("UnrecognizedError", "history_read_failed"),
])
def test_safe_actionable_read_failures(name, code):
    exc = type(name, (Exception,), {})("private chat text / key secret")
    result = describe_read_error(exc)
    assert result["error_code"] == code
    assert "secret" not in str(result) and "private" not in str(result)


def test_initialization_failure_is_distinct():
    assert describe_read_error(TypeError("private"), "initialize")["error_code"] == "adapter_initialization_failed"


def test_unavailable_accessibility_has_specific_recovery_without_echoing_sdk_text():
    exc = type('NotFoundError', (Exception,), {})('private chat / account / secret')
    result = describe_read_error(exc)
    assert result['error_code'] == 'wechat_ui_unavailable'
    assert '兼容性' in result['error'] and '讲述人' not in result['error']
    assert 'private' not in result['error'] and 'secret' not in result['error']


def test_live_child_protocol_reports_uia_failure_and_releases_com(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "pythoncom", SimpleNamespace(CoInitialize=lambda: calls.append("init"), CoUninitialize=lambda: calls.append("release")))
    def unavailable(group, count, diag, **kwargs):
        assert group == "test group" and count == 5
        raise type("NotFoundError", (Exception,), {})("private SDK error")
    monkeypatch.setattr(history_reader, "read_history", unavailable)
    output = queue.Queue()
    read_child("test group", 5, output)
    result = output.get_nowait()
    assert result["error_code"] == "wechat_ui_unavailable"
    assert "private" not in result["error"]
    assert calls == ["init", "release"]


def test_empty_history_protocol(monkeypatch):
    monkeypatch.setitem(sys.modules, "pythoncom", SimpleNamespace(CoInitialize=lambda: None, CoUninitialize=lambda: None))
    monkeypatch.setattr(history_reader, "read_history", lambda *args, **kwargs: {"rows": [], "coverage": False})
    output = queue.Queue()
    read_child("test group", 5, output)
    assert output.get_nowait() == {"rows": [], "coverage": False}
