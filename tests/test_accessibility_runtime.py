"""Compatibility regression tests never touch a live WeChat process."""
import queue
import sys
from types import SimpleNamespace

import pytest

from app import accessibility, history_reader
from app.collector import read_child
from app.history_reader import HistoryReadError


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv('TUTOR_DB', str(tmp_path / 'unused.sqlite3'))


def test_rechecks_each_read_after_process_state_resets(monkeypatch):
    calls = []
    def activate(**kwargs):
        assert kwargs['apply'] is True
        calls.append(kwargs)
        return {'state': 'activated'}
    monkeypatch.setattr(accessibility, 'run_activation', activate)
    accessibility.ensure_accessibility()
    accessibility.ensure_accessibility()
    assert len(calls) == 2


@pytest.mark.parametrize('state', ['activation_failed', 'restore_failed', 'flag_already_enabled_but_tree_unavailable'])
def test_no_read_when_flag_does_not_restore_tree(monkeypatch, state):
    monkeypatch.setattr(accessibility, 'run_activation', lambda **kwargs: {'state': state})
    with pytest.raises(HistoryReadError, match='界面访问初始化失败'):
        accessibility.ensure_accessibility()


def test_unverified_version_and_file_rejected():
    for version in (accessibility.EXPECTED_VERSION, '9.9.9.9'):
        with pytest.raises(ValueError):
            accessibility.verify_build(version, b'unverified DLL')


def test_cancelled_before_write_has_no_mutation():
    result = accessibility.activate_checked(lambda: b'\0', lambda _: pytest.fail('write'),
        lambda: {'accessible': False}, lambda _: None, stopped=lambda: True)
    assert result['state'] == 'cancelled'
    assert not result['write_attempted']


@pytest.mark.parametrize('count', [0, 5])
def test_worker_initializes_before_both_discovery_and_history(monkeypatch, count):
    calls = []
    monkeypatch.setitem(sys.modules, 'pythoncom', SimpleNamespace(
        CoInitialize=lambda: calls.append('com'), CoUninitialize=lambda: calls.append('release')))
    monkeypatch.setattr(accessibility, 'ensure_accessibility', lambda **kwargs: calls.append('compatibility'))
    def groups(**kwargs):
        calls.append('read')
        return ['synthetic group']
    def history(*args, **kwargs):
        calls.append('read')
        return {'rows': [{'synthetic': True}]}
    monkeypatch.setitem(sys.modules, 'pyweixin', SimpleNamespace(Contacts=SimpleNamespace(get_groups_info=groups)))
    monkeypatch.setattr(history_reader, 'read_history', history)
    output = queue.Queue()
    read_child('synthetic group', count, output)
    assert calls == ['com', 'compatibility', 'read', 'release']
    assert output.get_nowait()['rows']


def test_worker_stops_before_reader_on_initialization_failure(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, 'pythoncom', SimpleNamespace(
        CoInitialize=lambda: None, CoUninitialize=lambda: calls.append('release')))
    def fail(**kwargs):
        raise HistoryReadError('wechat_compatibility_unsupported', '不支持当前微信版本')
    monkeypatch.setattr(accessibility, 'ensure_accessibility', fail)
    monkeypatch.setattr(history_reader, 'read_history', lambda *a, **k: pytest.fail('must not read'))
    output = queue.Queue()
    read_child('synthetic', 5, output)
    assert output.get_nowait()['error_code'] == 'wechat_compatibility_unsupported'
    assert calls == ['release']


def test_native_exception_text_is_not_exposed(monkeypatch):
    def fail(**kwargs):
        raise OSError('private account or path')
    monkeypatch.setattr(accessibility, 'run_activation', fail)
    with pytest.raises(HistoryReadError) as caught:
        accessibility.ensure_accessibility()
    assert 'private' not in str(caught.value)


def test_live_acceptance_saves_counts_only(monkeypatch, tmp_path):
    from app.desktop import wechat_read_test
    from app.collector import Collector
    monkeypatch.setattr(Collector, 'page', lambda *a: [{'消息类型': '文本', '消息内容': 'private message'}])
    target = tmp_path / 'acceptance'
    assert wechat_read_test(target, 'private group') == 0
    report = (target / 'result.json').read_text(encoding='utf-8')
    assert 'private' not in report
    assert '"rows": 1' in report
