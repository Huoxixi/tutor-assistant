"""The user-facing product cannot enable or call historical model integrations."""
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import Message, now
from app.service import Service


def test_legacy_enabled_configuration_is_preserved_but_never_used(tmp_path, monkeypatch):
    app = create_app(tmp_path / 'local.sqlite3')
    service, store = app.state.service, app.state.store
    store.set('cloud_model_enabled', True)
    store.set('ai_verification_enabled', True)
    store.set('model', {'base_url': 'https://synthetic.invalid', 'model': 'legacy'})
    store.update_groups(add='合成群')
    store.add_message(Message(group='合成群', text='【科目】初二英语【课酬】150/h', sent_at=now()))
    prior_config = store.get('model')
    monkeypatch.setattr(service, 'extractor', lambda: pytest.fail('A model was constructed'))
    service.process()
    service.orders()
    restarted = Service(store)
    assert not restarted.ai_enabled and not restarted.model_enabled
    assert store.get('model') == prior_config
    assert store.get('cloud_model_enabled') is True and store.get('ai_verification_enabled') is True


def test_all_model_network_entrypoints_rejected_without_mutating_settings(tmp_path, monkeypatch):
    app = create_app(tmp_path / 'disabled.sqlite3')
    service, store = app.state.service, app.state.store
    store.set('model', {'base_url': 'https://synthetic.invalid', 'model': 'legacy'})
    monkeypatch.setattr(service, 'extractor', lambda: pytest.fail('A model was constructed'))
    original = store.get('model')
    with TestClient(app, headers={'X-Tutor-Client': 'local-ui'}) as client:
        for path in ('/api/model/test', '/api/verify', '/api/orders/anything/review'):
            assert client.post(path, json={}).status_code == 422
        response = client.put('/api/model', json={'base_url': 'https://synthetic.invalid',
            'model': 'new', 'api_key': 'synthetic-secret', 'ai_enabled': True})
        assert response.status_code == 422
        state = client.get('/api/state').json()
        assert state['model']['ai_enabled'] is False and state['model']['enabled'] is False
    assert store.get('model') == original and not service.key_store.saved


def test_page_and_scripts_have_no_live_location_or_model_controls(tmp_path):
    with TestClient(create_app(tmp_path / 'ui.sqlite3')) as client:
        html = client.get('/').text
        js = client.get('/static/app.js').text + client.get('/static/travel.js').text
        assert 'id="site-sidebar"' in html and '通勤计算' in html
        for marker in ('id="travel-locate"', 'id="model-form"', 'id="ai-enabled"', 'id="verify-pending"'):
            assert marker not in html
        assert 'getCurrentPosition' not in js and 'navigator.geolocation' not in js
        assert '/api/model/test' not in js and '/api/verify' not in js
        assert client.get('/openapi.json').json()['paths'].get('/api/travel/origin/location') is None
