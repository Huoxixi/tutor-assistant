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


def test_unsupported_binary_reports_detected_version_without_sdk_text(monkeypatch):
    def fail(**kwargs):
        raise accessibility.CompatibilityError('binary_features_unrecognized', '4.1.15.56')
    monkeypatch.setattr(accessibility, 'run_activation', fail)
    with pytest.raises(HistoryReadError) as caught:
        accessibility.ensure_accessibility()
    assert caught.value.code == 'wechat_compatibility_unsupported'
    assert '4.1.15.56' in str(caught.value)
    assert '4.1.15.13' not in str(caught.value)


def test_readonly_diagnostic_never_requests_activation(monkeypatch, tmp_path):
    from app.desktop import compatibility_test
    monkeypatch.setitem(sys.modules, 'pythoncom', SimpleNamespace(CoInitialize=lambda: None, CoUninitialize=lambda: None))
    def diagnose(**kwargs):
        assert kwargs['apply'] is False
        return {'version': '4.1.15.56', 'state': 'audit_only'}
    monkeypatch.setattr(accessibility, 'run_activation', diagnose)
    target = tmp_path/'check'
    assert compatibility_test(target) == 0
    import json
    report = json.loads((target/'result.json').read_text(encoding='utf-8'))
    assert report['wechat_version'] == '4.1.15.56'
    assert not report['chat_contents_read'] and not report['write_attempted']
    assert not report['actual_read_verified']


def test_search_editor_handles_collapsed_and_resident_layouts():
    from app.wechat_navigation import search_editor
    editor = SimpleNamespace(is_visible=lambda: True, is_enabled=lambda: True)
    for collapsed in (False, True):
        opened, clicks = [not collapsed], []
        def click():
            clicks.append('open'); opened[0] = True
        button = SimpleNamespace(window_text=lambda: '搜索', is_visible=lambda: True,
                                 is_enabled=lambda: True, click_input=click)
        main = SimpleNamespace(descendants=lambda **kw: ([editor] if opened[0] else [])
                               if kw['control_type'] == 'Edit' else [button])
        assert search_editor(main) is editor
        assert clicks == (['open'] if collapsed else [])


def test_duplicate_search_inputs_and_results_refused():
    from app.wechat_navigation import search_editor, exact_result
    editor = SimpleNamespace(is_visible=lambda: True, is_enabled=lambda: True)
    with pytest.raises(HistoryReadError, match='不唯一'):
        search_editor(SimpleNamespace(descendants=lambda **kw: [editor, editor]))
    item = SimpleNamespace(class_name=lambda: 'mmui::SearchContentCellView', window_text=lambda: '完整群名')
    with pytest.raises(HistoryReadError, match='同名'):
        exact_result([item,item], '完整群名')
    with pytest.raises(HistoryReadError, match='完整群名'):
        exact_result([item], '部分群名')
    unrelated = SimpleNamespace(class_name=lambda: 'MobileSearch', window_text=lambda: '完整群名')
    assert exact_result([unrelated,item], '完整群名') is item


@pytest.mark.parametrize('mode', ['success', 'loaded-mismatch', 'tree-failure'])
def test_native_boundary_uses_discovered_address_and_loaded_checks(monkeypatch,tmp_path,mode):
    """Exercise Windows glue with an in-memory API, never a real process."""
    import ctypes
    from tests.test_accessibility_activation import adaptive_pe
    image = adaptive_pe()
    dll = tmp_path/'Weixin.dll'; dll.write_bytes(image)
    base, flag, writes = 0x180000000, [b'\0'], []
    class Handle(int):
        def Close(self): pass
    fake_api = SimpleNamespace(OpenProcess=lambda *args: Handle(22), error=OSError,
        GetFileVersionInfo=lambda *args: {'FileVersionMS':(4<<16)|1, 'FileVersionLS':(15<<16)|56})
    monkeypatch.setitem(sys.modules,'psutil',SimpleNamespace(process_iter=lambda *args:
        [SimpleNamespace(info={'name':'Weixin.exe'},pid=777)]))
    monkeypatch.setitem(sys.modules,'win32api',fake_api)
    monkeypatch.setitem(sys.modules,'win32process',SimpleNamespace(
        EnumProcessModules=lambda _: [base], GetModuleFileNameEx=lambda *args: str(dll)))
    def read(handle,address,buffer,size,count):
        rva = address-base
        if rva == 0x4050:
            value = flag[0]
        else:
            section = next(s for s in accessibility.pe_sections(image) if s[0] <= rva < s[0]+s[1])
            offset = section[2]+rva-section[0]
            value = image[offset:offset+size]
        if mode == 'loaded-mismatch' and rva == 0x1040:
            value = b'\xff'*size
        ctypes.memmove(buffer,value,size); count._obj.value=size
        return 1
    def write(handle,address,buffer,size,count):
        assert address == base+0x4050 and size == 1
        writes.append(ctypes.string_at(buffer,size)); flag[0]=writes[-1]
        count._obj.value=size
        return 1
    monkeypatch.setattr(ctypes,'WinDLL',lambda *a,**k: SimpleNamespace(
        ReadProcessMemory=read, WriteProcessMemory=write))
    monkeypatch.setattr(accessibility,'probe_accessibility',lambda _: {
        'accessible':flag[0] == b'\1' and mode != 'tree-failure'})
    monkeypatch.setattr(accessibility.time,'sleep',lambda _: None)
    if mode == 'loaded-mismatch':
        with pytest.raises(accessibility.CompatibilityError):
            accessibility.run_activation(apply=True)
        assert not writes and flag[0] == b'\0'
    else:
        report = accessibility.run_activation(apply=True)
        assert report['version'] == '4.1.15.56' and report['method'] == 'binary_features'
        assert report['state'] == ('activated' if mode == 'success' else 'activation_failed')
        assert writes == ([b'\1'] if mode == 'success' else [b'\1',b'\0'])
