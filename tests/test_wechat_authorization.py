"""Consent gates all desktop entry points, without touching a real WeChat."""
import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import Message, now
from app.service import Service


@pytest.fixture
def setup(tmp_path, monkeypatch):
    app = create_app(tmp_path / 'consent.sqlite3')
    service, store = app.state.service, app.state.store
    store.update_groups(add='合成群')
    monkeypatch.setattr('app.main.preflight', lambda: pytest.fail('Unexpected desktop detection'))
    service.collector = SimpleNamespace(
        collect=lambda *a, **kw: pytest.fail('Unexpected desktop reading'),
        discover=lambda *a, **kw: pytest.fail('Unexpected desktop discovery'))
    with TestClient(app, headers={'x-tutor-client': 'local-ui'}) as client:
        yield client, service, store


def test_fresh_start_and_page_reads_never_detect_wechat(setup):
    client, service, store = setup
    for path in ('/', '/api/state', '/api/orders'):
        assert client.get(path).status_code == 200
    assert client.get('/api/state').json()['wechat_authorization'] == {
        'authorized': False, 'scope': 'current_run'}
    assert not store.scans()


def test_unauthorized_connection_and_extraction_rejected_before_detection(setup):
    client, service, store = setup
    for response in (client.get('/api/connection'),
                     client.post('/api/scans', json={'groups': ['合成群']})):
        assert response.status_code == 403 and '授权' in response.json()['detail']
    assert not store.scans() and not service.lock.locked()
    assert not service._wechat_reading


@pytest.mark.parametrize('entry', ['start_scan', 'start_discovery'])
def test_direct_service_entry_also_requires_consent(setup, entry):
    client, service, store = setup
    with pytest.raises(PermissionError, match='授权'):
        getattr(service, entry)(*([['合成群']] if entry == 'start_scan' else []))
    assert not store.scans() and not service.lock.locked()


def test_consent_itself_does_not_connect_or_extract_and_is_not_saved(setup):
    client, service, store = setup
    original = store.group_state(), store.preferences(), store.messages()
    assert client.put('/api/wechat/authorization', json={'authorized': True}).json()['authorized']
    assert service.wechat_authorization['authorized']
    assert (store.group_state(), store.preferences(), store.messages()) == original
    assert not Service(store).wechat_authorization['authorized']
    assert not store.scans()


@pytest.mark.parametrize('body', [{}, {'authorized': 'true'}, {'authorized': 1}, {'authorized': None}])
def test_consent_requires_explicit_boolean(setup, body):
    client, service, store = setup
    assert client.put('/api/wechat/authorization', json=body).status_code == 422
    assert not service.wechat_authorization['authorized']


def test_consent_endpoint_uses_existing_local_client_protection(setup):
    client, service, store = setup
    response = client.put('/api/wechat/authorization', json={'authorized': True},
                          headers={'x-tutor-client': ''})
    assert response.status_code == 403
    assert not service.wechat_authorization['authorized']


def test_authorized_detection_and_read_then_revocation(setup, monkeypatch):
    client, service, store = setup
    calls = []
    def detect():
        calls.append('detect')
        return {'adapter_installed': True, 'wechat_running': True}
    monkeypatch.setattr('app.main.preflight', detect)
    monkeypatch.setattr(service, 'launch', lambda fn: fn())
    def collect(group, start, end, cancel, on_batch, **kwargs):
        calls.append('read')
        on_batch([Message(group=group, text='合成消息', sent_at=end)])
        return {'group': group, 'state': 'complete', 'count': 1}
    service.collector.collect = collect
    client.put('/api/wechat/authorization', json={'authorized': True})
    assert calls == []
    assert client.get('/api/connection').status_code == 200
    assert client.post('/api/scans', json={'groups': ['合成群']}).status_code == 200
    assert calls == ['detect', 'detect', 'read']
    assert client.put('/api/wechat/authorization', json={'authorized': False}).json()['authorized'] is False
    assert client.get('/api/connection').status_code == 403
    assert client.post('/api/scans', json={'groups': ['合成群']}).status_code == 403
    assert calls == ['detect', 'detect', 'read']
    assert len(store.messages()) == 1


def test_revoking_active_read_stops_before_next_group_and_preserves_batch(setup):
    client, service, store = setup
    started, stopped = threading.Event(), threading.Event()
    calls = []
    def collect(group, start, end, cancel, on_batch, **kwargs):
        calls.append(group)
        on_batch([Message(group=group, text='合成消息', sent_at=end)])
        started.set()
        assert cancel.wait(3), 'Revocation did not request cancellation'
        stopped.set()
        return {'group': group, 'state': 'cancelled', 'count': 1}
    service.collector.collect = collect
    service.set_wechat_authorization(True)
    service.start_scan(['合成群', '其他群'])
    assert started.wait(3)
    # A busy second launch must retain the active reader's consent state.
    with pytest.raises(RuntimeError):
        service.start_scan(['其他群'])
    service.set_wechat_authorization(False)
    assert stopped.wait(3)
    assert service.lock.acquire(timeout=3)
    service.lock.release()
    assert calls == ['合成群']
    assert len(store.messages()) == 1
    assert store.scans()[0]['state'] == 'cancelled'
    assert not service.wechat_authorization['authorized']


def test_revocation_between_queue_and_worker_prevents_access(setup, monkeypatch):
    client, service, store = setup
    queued = []
    monkeypatch.setattr(service, 'launch', queued.append)
    service.set_wechat_authorization(True)
    service.start_scan(['合成群'])
    service.set_wechat_authorization(False)
    queued[0]()
    assert not store.scans() and not service._wechat_reading


def test_local_filter_remains_available_without_consent(setup, monkeypatch):
    client, service, store = setup
    store.add_message(Message(group='合成群', text='【科目】初二英语【课酬】120/h', sent_at=now()))
    monkeypatch.setattr(service, 'launch', lambda fn: fn())
    assert client.post('/api/process', json={}).status_code == 200
    assert store.messages()[0]['state'] == 'processed'
    assert not service.wechat_authorization['authorized']
    assert not store.scans()
    assert not service.cancel.is_set()
    service.set_wechat_authorization(False)
    assert not service.cancel.is_set()
