"""Extraction and filtering are independent, with no live desktop access."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import Message,now


@pytest.fixture
def setup(tmp_path,monkeypatch):
    app=create_app(tmp_path/'split.sqlite3')
    service=app.state.service;store=app.state.store
    store.update_groups(add='合成群')
    store.set('preferences',{'profile':{'teacher_gender':'男','school_tier':['211']},'rules':[{'field':'subject','value':['英语'],'mode':'must'}]})
    monkeypatch.setattr(service,'launch',lambda fn,*args:fn(*args))
    with TestClient(app,headers={'x-tutor-client':'local-ui'}) as client:
        yield client,service,store


def test_extraction_endpoint_only_saves_raw_information(setup,monkeypatch):
    client,service,store=setup
    service.set_wechat_authorization(True)
    monkeypatch.setattr('app.main.preflight',lambda:{'adapter_installed':True,'wechat_running':True})
    monkeypatch.setattr(service,'local_extractor',lambda:pytest.fail('Extraction also parsed orders'))
    monkeypatch.setattr(service,'verify_pending',lambda *a,**kw:pytest.fail('Extraction called AI'))
    def collect(group,start,end,cancel,on_batch,**kwargs):
        on_batch([Message(group=group,text='【科目】初二英语【课酬】120/h',sent_at=end)])
        return {'group':group,'state':'complete','count':1,'detail':'合成读取完成'}
    service.collector=SimpleNamespace(collect=collect)
    assert client.post('/api/scans',json={'groups':['合成群']}).status_code==200
    assert store.messages()[0]['state']=='received'
    assert not store.orders()[0]
    state=client.get('/api/state').json()
    assert state['processing']['state']=='extracted'
    assert state['selected_counts']=={'received':1}
    assert '开始筛选' in state['processing']['detail']


def test_filter_endpoint_reuses_saved_information_even_without_wechat(setup,monkeypatch):
    client,service,store=setup
    store.add_message(Message(group='合成群',text='【科目】初二英语【课酬】120/h',sent_at=now()))
    monkeypatch.setattr('app.main.preflight',lambda:pytest.fail('Filter checked WeChat'))
    service.collector=SimpleNamespace(collect=lambda *a,**kw:pytest.fail('Filter read WeChat'))
    monkeypatch.setattr(service,'extractor',lambda:pytest.fail('Local filter constructed AI'))
    original=store.get('preferences')
    assert client.post('/api/process',json={}).status_code==200
    assert store.get('preferences')==original
    assert client.get('/api/orders').json()['orders'][0]['match']['bucket']=='matched'
    assert not store.scans() and store.messages()[0]['state']=='processed'
    changed={**original,'rules':[{'field':'subject','value':['数学'],'mode':'must'}]}
    assert client.put('/api/preferences',json=changed).status_code==200
    assert client.post('/api/process',json={}).status_code==200
    assert client.get('/api/orders').json()['orders'][0]['match']['bucket']=='rejected'
    assert len(store.messages())==1 and not store.scans()


def test_stopped_extraction_can_be_filtered_without_rereading(setup,monkeypatch):
    client,service,store=setup
    service.set_wechat_authorization(True)
    def collect(group,start,end,cancel,on_batch,**kwargs):
        on_batch([Message(group=group,text='【科目】初二英语【课酬】120/h',sent_at=end)])
        cancel.set()
        return {'group':group,'state':'cancelled','count':1,'detail':'合成停止'}
    service.collector=SimpleNamespace(collect=collect)
    service.start_scan(['合成群'])
    assert service.progress['state']=='extraction_cancelled'
    assert store.messages()[0]['state']=='received' and not store.orders()[0]
    service.collector=SimpleNamespace(collect=lambda *a,**kw:pytest.fail('Reread after stop'))
    service.cancel.clear()
    service.run_process()
    assert service.orders()['orders'][0]['match']['bucket']=='matched'


def test_saved_information_count_follows_selected_sources(setup):
    client,service,store=setup
    for group,synthetic in [('合成群',False),('其他群',False),('合成群',True)]:
        store.add_message(Message(group=group,text='合成'+str(synthetic),sent_at=now(),synthetic=synthetic))
    state=client.get('/api/state').json()
    assert state['counts']=={'received':2} and state['selected_counts']=={'received':1}
    store.update_groups(selected=[])
    assert client.get('/api/state').json()['selected_counts']=={}


def test_filter_only_processes_saved_messages_from_selected_groups(setup):
    client,service,store=setup
    for group in ('合成群','其他群'):
        store.add_message(Message(group=group,text='【科目】初二英语【课酬】120/h',sent_at=now()))
    service.collector=SimpleNamespace(collect=lambda *a,**kw:pytest.fail('Filter read WeChat'))
    assert client.post('/api/process',json={}).status_code==200
    assert {m['payload']['group']:m['state'] for m in store.messages()}=={'合成群':'processed','其他群':'received'}
