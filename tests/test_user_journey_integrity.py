"""User-visible upgrade, scope, partial-failure and favorite regressions."""
import queue
import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.local_extraction import LocalExtractor,PARSER_VERSION
from app.models import Draft,Message,now
from app.store import Store
from app.service import Service
from app.collector import Collector
from app.history_reader import HistoryReadError

RAW='7103001【学员】初二【科目】英语【课酬】150/h；7103002【学员】三年级【科目】数学【课酬】120/h'


def legacy(store,group='甲群',favorite=True):
    store.update_groups(add=group)
    mid=store.add_message(Message(group=group,text=RAW,sent_at=now()))
    store.save_drafts(mid,[Draft(reference='7103001',source_text=RAW,facts={'subject':{'value':['英语'],'evidence':'英语'}})])
    oid=store.orders()[0][0]['id']
    if favorite:store.favorite(oid,True)
    return mid,oid


def test_upgrade_reparses_saved_message_and_moves_favorite_by_reference(tmp_path):
    store=Store(tmp_path/'legacy.sqlite3');mid,old=legacy(store)
    before=store.messages()
    Service(store).run_process()
    orders=store.orders()[0]
    assert {o['reference'] for o in orders}=={'7103001','7103002'}
    assert old not in {o['id'] for o in orders}
    assert [(o['reference']) for o in orders if o['favorite_at']]==['7103001']
    assert store.messages()==before
    assert store.parser_versions()[mid]==PARSER_VERSION
    assert len(list((tmp_path/'backups').glob('*.sqlite3')))==1


def test_second_filter_does_not_parse_current_messages_again(tmp_path,monkeypatch):
    store=Store(tmp_path/'current.sqlite3');legacy(store)
    service=Service(store);service.run_process()
    monkeypatch.setattr(service,'local_extractor',lambda:pytest.fail('Repeated parsing'))
    service.run_process()
    assert service.progress['state']=='no_messages'


def test_reparse_is_atomic_when_saving_fails(tmp_path):
    store=Store(tmp_path/'atomic.sqlite3');mid,_=legacy(store)
    before=store.orders()[0]
    class Broken:
        def model_dump(self,**kwargs):raise ValueError('synthetic save failure')
    with pytest.raises(ValueError):store.save_drafts(mid,[Broken()],replace=True,parser_version=PARSER_VERSION)
    assert store.orders()[0]==before
    assert not store.parser_versions()


def test_unmatched_historical_favorite_keeps_source_and_requires_review(tmp_path):
    store=Store(tmp_path/'favorite.sqlite3');store.update_groups(add='甲群')
    mid=store.add_message(Message(group='甲群',text='历史收藏的原始文本',sent_at=now()))
    store.save_drafts(mid,[Draft(reference='OLD001')]);oid=store.orders()[0][0]['id'];store.favorite(oid,True)
    Service(store).run_process()
    item=Service(store).orders(favorites=True)['orders'][0]
    assert item['favorite_at'] and item['facts']=={} and item['match']['bucket']=='pending'
    assert item['sources'][0]['text']=='历史收藏的原始文本'


def test_reparse_refreshes_facts_when_source_id_is_stable(tmp_path):
    store=Store(tmp_path/'facts.sqlite3');text='单号：TEST410\n【科目】初二数学【课酬】150/h'
    mid=store.add_message(Message(group='甲群',text=text,sent_at=now()))
    store.save_drafts(mid,[Draft(reference='TEST410',source_text=text,facts={'hourly_rate':{'value':999,'evidence':'150/h'}})])
    old=store.orders()[0][0]['id']
    Service(store).process()
    item=store.orders()[0][0]
    assert item['id']==old and item['facts']['hourly_rate']['value']==150


def test_only_selected_groups_are_reparsed_and_empty_selection_reads_nothing(tmp_path):
    store=Store(tmp_path/'groups.sqlite3');a,_=legacy(store,'甲群',False);b,_=legacy(store,'乙群',False)
    store.update_groups(selected=['甲群']);Service(store).run_process()
    assert a in store.parser_versions() and b not in store.parser_versions()
    store.update_groups(selected=[]);before=store.messages();Service(store).run_process()
    assert store.messages()==before and b not in store.parser_versions()


def test_upgrade_notice_is_read_only_until_user_starts_filter(tmp_path,monkeypatch):
    app=create_app(tmp_path/'notice.sqlite3');store=app.state.store;mid,_=legacy(store)
    monkeypatch.setattr(app.state.service,'launch',lambda fn,*args:fn(*args))
    with TestClient(app,headers={'x-tutor-client':'local-ui'}) as client:
        assert client.get('/api/state').json()['parser']['pending_reparse']==1
        assert not (tmp_path/'backups').exists()
        assert client.post('/api/process',json={}).status_code==200
        assert client.get('/api/state').json()['parser']['pending_reparse']==0


def test_backup_failure_keeps_original_and_provides_recovery(tmp_path,monkeypatch):
    store=Store(tmp_path/'backup.sqlite3');legacy(store);before=store.orders()[0]
    monkeypatch.setattr(store,'parser_backup',lambda:(_ for _ in ()).throw(OSError('private path')))
    service=Service(store);service.run_process()
    assert service.progress['state']=='partial' and '备份未完成' in service.progress['detail']
    assert 'private' not in service.progress['detail'] and store.orders()[0]==before


def test_one_bad_order_does_not_hide_its_neighbors(monkeypatch):
    parser=LocalExtractor();original=parser.parse
    def parse(block):
        if block.reference=='7103001':raise ValueError('private source or SDK error')
        return original(block)
    monkeypatch.setattr(parser,'parse',parse)
    drafts=parser.extract({'text':RAW})
    assert [d.reference for d in drafts]==['7103001','7103002']
    assert not drafts[0].facts and drafts[0].validation_issues
    assert 'private' not in str(drafts[0])
    assert drafts[1].facts['hourly_rate'].value==120


def test_scope_and_favorite_changes_are_blocked_while_task_runs(tmp_path):
    app=create_app(tmp_path/'busy.sqlite3');_,oid=legacy(app.state.store)
    app.state.service.lock.acquire()
    try:
        with TestClient(app,headers={'x-tutor-client':'local-ui'}) as client:
            assert client.put('/api/orders/'+oid+'/favorite',json={'enabled':False}).status_code==409
            assert client.put('/api/groups',json={'groups':[]}).status_code==409
            assert client.post('/api/groups',json={'name':'乙群'}).status_code==409
            assert client.request('DELETE','/api/groups',json={'name':'甲群'}).status_code==409
    finally:app.state.service.lock.release()


def test_no_selected_groups_cannot_start_filter(tmp_path):
    with TestClient(create_app(tmp_path/'empty.sqlite3'),headers={'x-tutor-client':'local-ui'}) as client:
        assert client.post('/api/process',json={}).status_code==422


def test_cancelling_worker_drains_its_final_short_batch(monkeypatch):
    cancel=threading.Event();stop=threading.Event();calls=[]
    events=iter([{'event':'progress','phase':'read_history'}, {'event':'batch','rows':[{'消息内容':'合成最后一条'}]}, {'error':'已停止','error_code':'read_cancelled'}])
    class Output:
        def get(self,**kwargs):
            result=next(events)
            if result.get('event')=='progress':cancel.set()
            return result
        def close(self):pass
    process=SimpleNamespace(start=lambda:None,join=lambda **k:None,is_alive=lambda:True,terminate=lambda:None)
    ctx=SimpleNamespace(Queue=lambda **k:Output(),Event=lambda:stop,Process=lambda **k:process)
    monkeypatch.setattr('app.collector.multiprocessing.get_context',lambda _:ctx)
    with pytest.raises(HistoryReadError):
        for event in Collector()._events('合成群',5,cancel):calls.append(event)
    assert any(event.get('event')=='batch' for event in calls)
    assert stop.is_set()


def test_reparse_replays_closure_update_for_only_its_reference(tmp_path):
    from datetime import timedelta
    store=Store(tmp_path/'closure.sqlite3');legacy(store)
    mid=store.add_message(Message(group='甲群',text='单号：7103001 已出',sent_at=now()+timedelta(seconds=1)))
    Service(store).run_process()
    orders={o['reference']:o for o in store.orders()[0]}
    assert orders['7103001']['status']=='closed'
    assert orders['7103002']['status']=='open'
    assert store.orders()[1]==0


def test_oversized_multi_order_message_keeps_every_reference(monkeypatch):
    from app.splitting import Block
    monkeypatch.setattr('app.local_extraction.split_orders',lambda text:[Block('TEST'+str(i),'synthetic '+str(i)) for i in range(102)])
    parser=LocalExtractor()
    monkeypatch.setattr(parser,'parse',lambda block:Draft(reference=block.reference,source_text=block.text))
    drafts=parser.extract({'text':'synthetic'})
    assert len(drafts)==102 and drafts[101].reference=='TEST101'
    assert drafts[101].validation_issues and drafts[101].source_text=='synthetic 101'
