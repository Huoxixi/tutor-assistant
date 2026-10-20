"""Amap calls are exclusively httpx mocks; all locations and keys are synthetic."""
import json
import threading
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import Draft, Message, now
from app.service import Service
from app.travel import TravelError, TravelService

KEY = 'synthetic_amap_key_for_tests_0000'


def place(label='合成学校', level='兴趣点', location='121.400000,31.200000'):
    return {'formatted_address': label, 'level': level, 'city': '上海市',
            'province': '上海市', 'citycode': '021', 'adcode': '310101', 'location': location}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    app = create_app(tmp_path / 'travel.sqlite3')
    service, store = app.state.service, app.state.store
    travel = service.travel
    calls = []
    def provider(request):
        calls.append(request)
        if request.url.path.endswith('/geo'):
            return httpx.Response(200, json={'status': '1', 'geocodes': [place()]})
        if request.url.path.endswith('/convert'):
            return httpx.Response(200, json={'status': '1', 'locations': '121.405000,31.205000'})
        if request.url.path.endswith('/regeo'):
            return httpx.Response(200, json={'status': '1', 'regeocode': {'addressComponent': place()}})
        return httpx.Response(200, json={'status': '1', 'route': {
            'transits': [{'distance': '5000', 'duration': '1800'}, {'distance': '6000', 'duration': '1500'}],
            'paths': [{'distance': '2000', 'duration': '1200'}]}})
    travel.transport = httpx.MockTransport(provider)
    # Only skip rate-limit waiting, preserving the provider-call guards.
    monkeypatch.setattr('app.travel.time.sleep', lambda _: None)
    store.update_groups(add='合成群')
    payload = '【科目】初二英语\n【课酬】150/h\n【地址】上海市合成教学点'
    mid = store.add_message(Message(group='合成群', text=payload, sent_at=now()))
    store.save_drafts(mid, [Draft(reference='SYNTHETIC001', source_text=payload, facts={
        'location': {'value': '合成教学点', 'evidence': '合成教学点'},
        'city': {'value': '上海市', 'evidence': '上海市'}})])
    oid = store.orders()[0][0]['id']
    store.favorite(oid, True)
    with TestClient(app, headers={'x-tutor-client': 'local-ui'}) as client:
        yield SimpleNamespace(client=client, travel=travel, service=service, store=store, calls=calls, oid=oid)


def enable(s):
    s.travel.key = KEY
    assert s.client.put('/api/travel/authorization', json={'authorized': True}).status_code == 200


def origin(s):
    enable(s)
    found = s.client.post('/api/travel/origin/search', json={'address': '合成学校', 'city': '上海市'})
    assert found.status_code == 200
    cid = found.json()['candidates'][0]['id']
    assert s.client.put('/api/travel/origin', json={'candidate_id': cid}).status_code == 200


def destination(s):
    result = s.client.post(f'/api/travel/orders/{s.oid}/destinations', json={})
    assert result.status_code == 200
    return result.json()['candidates'][0]['id']


def test_boot_reads_and_local_filter_never_call_amap(setup, monkeypatch):
    s = setup
    monkeypatch.setattr(s.service, 'launch', lambda fn: fn())
    for path in ('/', '/api/state', '/api/orders', '/api/travel'):
        assert s.client.get(path).status_code == 200
    assert s.client.post('/api/process', json={}).status_code == 200
    assert not s.calls
    assert not s.travel.state['authorized'] and not s.service.ai_enabled


def test_wechat_consent_does_not_grant_location_consent(setup):
    s = setup
    s.service.set_wechat_authorization(True)
    s.travel.key = KEY
    r = s.client.post('/api/travel/origin/search', json={'address': '合成学校', 'city': '上海市'})
    assert r.status_code == 403 and not s.calls


def test_authorization_with_missing_key_makes_no_request(setup):
    s = setup
    s.client.put('/api/travel/authorization', json={'authorized': True})
    r = s.client.post('/api/travel/origin/search', json={'address': '合成学校', 'city': '上海市'})
    assert r.status_code == 422 and 'Key' in r.json()['detail'] and not s.calls


def test_settings_and_consent_do_not_query_and_never_expose_key(setup):
    s = setup
    original_model = s.service.key
    r = s.client.put('/api/travel/settings', json={'api_key': KEY, 'mode': 'walking'})
    assert r.status_code == 200 and r.json()['key_saved']
    assert s.travel.key_store.path.name.endswith('.amap-key.dpapi')
    assert KEY.encode() not in s.travel.key_store.path.read_bytes()
    assert KEY not in s.client.get('/api/travel').text
    assert KEY not in s.client.get('/api/state').text
    with s.store.db() as db:
        assert KEY not in str(db.execute('SELECT * FROM settings').fetchall())
    assert s.service.key == original_model and not s.service.ai_enabled and not s.calls
    restarted = Service(s.store).travel
    assert restarted.key == KEY and restarted.state['mode'] == 'walking'
    assert not restarted.authorized
    assert s.client.put('/api/travel/settings', json={'api_key': '', 'mode': 'walking'}).status_code == 200
    assert not s.travel.key_store.saved and not s.travel.key


def test_origin_requires_explicit_candidate_confirmation(setup):
    s = setup
    enable(s)
    result = s.client.post('/api/travel/origin/search', json={'address': '合成学校', 'city': '上海市'})
    assert not s.travel.state['origin']
    cid = result.json()['candidates'][0]['id']
    assert s.client.put('/api/travel/origin', json={'candidate_id': cid}).status_code == 200
    assert s.travel.state['origin']['label'] == '合成学校'
    assert len(s.calls) == 1
    assert s.calls[0].url.host == 'restapi.amap.com'


@pytest.mark.parametrize('mode,expected', [('transit', 1500), ('walking', 1200), ('driving', 1200)])
def test_routes_from_confirmed_destination_and_no_matching_mutations(setup, mode, expected):
    s = setup
    origin(s)
    s.travel.configure(mode)
    cid = destination(s)
    before = s.store.preferences(), s.store.messages(), s.store.orders(), s.store.scans()
    r = s.client.post(f'/api/travel/orders/{s.oid}/route', json={'candidate_id': cid})
    assert r.status_code == 200
    assert r.json()['duration_seconds'] == expected
    assert r.json()['mode'] == mode and r.json()['estimated']
    assert r.json()['affects_matching'] is False
    assert before == (s.store.preferences(), s.store.messages(), s.store.orders(), s.store.scans())
    assert len(s.calls) == 3
    assert s.calls[-1].url.params['origin'] == '121.400000,31.200000'
    # No sender, group, raw message, profile or model key is sent to Amap.
    assert all('合成群' not in str(request.url) and '课酬' not in str(request.url) for request in s.calls)


def test_live_location_endpoint_removed_without_any_provider_call(setup):
    s = setup
    enable(s)
    r = s.client.post('/api/travel/origin/location', json={
        'longitude': 121.4, 'latitude': 31.2, 'accuracy': 60, 'city': '错误城市'})
    assert r.status_code == 404 and not s.calls
    assert not s.travel.state['origin']


@pytest.mark.parametrize('coords', [
    {'longitude': 500, 'latitude': 30, 'accuracy': 10},
    {'longitude': 120, 'latitude': 100, 'accuracy': 10},
    {'longitude': 120, 'latitude': 30, 'accuracy': 1001},
])
def test_invalid_or_inaccurate_browser_coordinates_not_sent(setup, coords):
    s = setup
    enable(s)
    assert s.client.post('/api/travel/origin/location', json={**coords, 'city': '上海市'}).status_code == 404
    assert not s.calls


@pytest.mark.parametrize('payload', [
    {'status': '1', 'geocodes': [place(level='区县')]},
    {'status': '1', 'geocodes': [place(location='nan,31')]},
    {'status': '1', 'geocodes': []},
    {'status': '1', 'geocodes': None},
    {'status': '0', 'info': 'private message ' + KEY},
])
def test_bad_geocodes_cannot_create_origin_or_leak_key(setup, payload):
    s = setup
    enable(s)
    s.travel.transport = httpx.MockTransport(lambda req: httpx.Response(200, json=payload))
    r = s.client.post('/api/travel/origin/search', json={'address': '合成学校', 'city': '上海市'})
    assert r.status_code == 422 and KEY not in r.text
    assert not s.travel.state['origin']


def test_network_failure_never_echoes_request_url_or_retries(setup):
    s = setup
    enable(s)
    calls = []
    def fail(request):
        calls.append(request)
        raise httpx.ConnectError('secret url ' + str(request.url))
    s.travel.transport = httpx.MockTransport(fail)
    r = s.client.post('/api/travel/origin/search', json={'address': '合成学校', 'city': '上海市'})
    assert r.status_code == 422 and KEY not in r.text and '合成学校' not in r.text
    assert len(calls) == 1


def test_revoke_and_settings_changes_invalidate_destination(setup):
    s = setup
    origin(s)
    for change in (lambda: s.travel.configure('walking'), lambda: s.travel.authorize(False)):
        if not s.travel.authorized:
            s.travel.authorize(True)
        cid = destination(s)
        before = len(s.calls)
        change()
        r = s.client.post(f'/api/travel/orders/{s.oid}/route', json={'candidate_id': cid})
        assert r.status_code in (403, 422) and len(s.calls) == before


def test_revoke_during_request_ignores_returned_data(setup):
    s = setup
    enable(s)
    def revoked(request):
        s.travel.authorize(False)
        return httpx.Response(200, json={'status': '1', 'geocodes': [place()]})
    s.travel.transport = httpx.MockTransport(revoked)
    r = s.client.post('/api/travel/origin/search', json={'address': '合成学校', 'city': '上海市'})
    assert r.status_code == 403 and not s.travel.choices


def test_wrong_order_cannot_reuse_destination_or_send_entire_chat(setup):
    s = setup
    origin(s)
    cid = destination(s)
    order = s.service.orders()['orders'][0]
    with pytest.raises(TravelError):
        s.travel.calculate({**order, 'id': 'different-id'}, cid)
    assert len(s.calls) == 2


@pytest.mark.parametrize('routes', [[], None, [{'distance': 'nan', 'duration': '100'}], [{'distance': '5'}]])
def test_unavailable_route_not_reported_as_passing_distance(setup, routes):
    s = setup
    origin(s)
    cid = destination(s)
    s.travel.transport = httpx.MockTransport(lambda req: httpx.Response(200, json={
        'status': '1', 'route': {'transits': routes}}))
    r = s.client.post(f'/api/travel/orders/{s.oid}/route', json={'candidate_id': cid})
    assert r.status_code == 422


def test_missing_address_does_not_guess_from_origin_city(setup):
    s = setup
    origin(s)
    order = s.service.orders()['orders'][0]
    with pytest.raises(TravelError):
        s.travel.search_destination({**order, 'facts': {}})
    assert len(s.calls) == 1


@pytest.mark.parametrize('address,expected', [
    ('上海市黄浦区合成路128号合成花园3号楼2单元301室\n联系电话：13800000000',
     '上海市黄浦区合成路128号合成花园'),
    ('黄浦区\n合成路128弄56号合成花园', '黄浦区合成路128弄56号合成花园'),
    ('合成路128号合成花园4楼，联系人：合成发布者，课酬150元', '黄浦区合成路128号合成花园，'),
])
def test_destination_preserves_street_numbers_and_multiline_address(setup, address, expected):
    s = setup
    origin(s)
    order = s.service.orders()['orders'][0]
    detailed = {**order, 'facts': {'city': {'value': '上海市'}, 'area': {'value': '黄浦区'},
                                 'location': {'value': address}}}
    answer = s.travel.search_destination(detailed)
    assert answer['query'] == expected.rstrip('，')
    assert s.calls[-1].url.params['address'] == expected.rstrip('，')
    assert s.calls[-1].url.params['city'] == '上海市'
    assert all(word not in str(s.calls[-1].url) for word in ('13800000000', '301室', '合成发布者', '课酬'))
    assert len(s.calls) == 2


@pytest.mark.parametrize('address', ['黄浦区', '黄浦', '上海市黄浦区', '上海市', '上海市黄浦区附近', '详细地址待定'])
def test_coarse_destination_rejected_before_map_request(setup, address):
    s = setup
    origin(s)
    order = s.service.orders()['orders'][0]
    detailed = {**order, 'facts': {'city': {'value': '上海市'}, 'area': {'value': '黄浦区'},
                                 'location': {'value': address}}}
    with pytest.raises(TravelError):
        s.travel.search_destination(detailed)
    assert len(s.calls) == 1


def test_default_redaction_still_hides_street_numbers():
    from app.privacy import redact
    assert '128号' not in redact('合成路128号3栋301室')
    detailed = redact('合成路128号3栋301室', preserve_street_numbers=True)
    assert '128号' in detailed and '301室' not in detailed


def test_unique_district_supplies_query_city_without_changing_facts(setup):
    s = setup
    origin(s)
    order = s.service.orders()['orders'][0]
    facts = {'area': {'value': '黄浦区'}, 'location': {'value': '黄浦区合成路128号'}}
    before = json.dumps(facts, ensure_ascii=False)
    answer = s.travel.search_destination({**order, 'facts': facts})
    assert answer['city'] == '上海市' and '合成路128号' in answer['query']
    assert s.calls[-1].url.params['city'] == '上海市'
    assert json.dumps(facts, ensure_ascii=False) == before


@pytest.mark.parametrize('facts,issues', [
    ({'area': {'value': '鼓楼区'}, 'location': {'value': '合成路128号'}}, []),
    ({'location': {'value': '合成路128号'}}, []),
    ({'area': {'value': '黄浦区'}, 'location': {'value': '合成路128号'}}, ['城市存在多个地点，需核对原文']),
])
def test_missing_or_conflicting_city_never_uses_origin_city(setup, facts, issues):
    s = setup
    origin(s)
    order = s.service.orders()['orders'][0]
    with pytest.raises(TravelError):
        s.travel.search_destination({**order, 'facts': facts, 'validation_issues': issues})
    assert len(s.calls) == 1


def test_travel_write_endpoints_require_local_client_header(setup):
    s = setup
    r = s.client.put('/api/travel/authorization', headers={'x-tutor-client': ''}, json={'authorized': True})
    assert r.status_code == 403 and not s.travel.authorized


def test_nonfavorite_and_unfavorited_orders_never_query_provider(setup):
    s = setup
    origin(s)
    cid = destination(s)
    s.store.favorite(s.oid, False)
    before = len(s.calls)
    assert s.client.post(f'/api/travel/orders/{s.oid}/destinations', json={}).status_code == 404
    assert s.client.post(f'/api/travel/orders/{s.oid}/route', json={'candidate_id': cid}).status_code == 404
    assert len(s.calls) == before
