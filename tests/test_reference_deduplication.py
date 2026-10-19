from datetime import timedelta

from app.models import Message,Draft,now
from app.store import Store
from app.service import Service


def prepared(tmp_path):
    store=Store(tmp_path/'dedup.sqlite3')
    store.update_groups(add='甲群')
    store.set('preferences',{'profile':{},'rules':[{'field':'hourly_rate','value':{'min':100,'max':200},'mode':'must'}]})
    return store,Service(store)


def add(store,reference,rate,stamp,group='甲群',sender='合成发布者',suffix='',synthetic=False):
    text=f'{reference or "无编号"}\n英语 {rate}/h\n{suffix}'
    mid=store.add_message(Message(group=group,sender=sender,text=text,sent_at=stamp,synthetic=synthetic))
    store.save_drafts(mid,[Draft(reference=reference,source_text=text,facts={'hourly_rate':{'value':rate,'evidence':f'{rate}/h'}})])
    return next(o['id'] for o in store.orders()[0] if any(s['id']==mid for s in o['sources']))


def test_same_reference_is_one_visible_row_with_newest_facts_and_all_originals(tmp_path):
    store,service=prepared(tmp_path);stamp=now()
    add(store,'001A',130,stamp-timedelta(hours=1),suffix='第一版')
    latest=add(store,'001A',150,stamp,suffix='第二版')
    result=service.orders()
    assert len(result['orders'])==1 and result['duplicate_orders_removed']==1
    row=result['orders'][0]
    assert row['id']==latest and row['facts']['hourly_rate']['value']==150
    assert len(row['sources'])==2 and row['sources'][0]['text'].endswith('第二版')
    assert row['deduplication']['version_count']==2 and not row['possible_duplicates']
    assert len(store.messages())==len(store.orders()[0])==2


def test_latest_conflicting_price_cannot_be_replaced_by_older_passing_price(tmp_path):
    store,service=prepared(tmp_path);stamp=now()
    add(store,'A1',130,stamp-timedelta(hours=1))
    add(store,'A1',250,stamp)
    row=service.orders()['orders'][0]
    assert row['facts']['hourly_rate']['value']==250 and row['match']['bucket']=='rejected'


def test_latest_tied_conflicting_versions_remain_one_pending_row(tmp_path):
    store,service=prepared(tmp_path);stamp=now()
    add(store,'A1',130,stamp)
    add(store,'A1',250,stamp)
    rows=service.orders()['orders']
    assert len(rows)==1 and rows[0]['match']['bucket']=='pending'
    assert any('最新版本内容不一致' in r for r in rows[0]['match']['unknowns'])


def test_undated_conflicting_version_cannot_silently_overwrite_known_version(tmp_path):
    store,service=prepared(tmp_path)
    add(store,'A1',130,now());add(store,'A1',250,None)
    row=service.orders()['orders'][0]
    assert row['match']['bucket']=='pending' and len(row['sources'])==2


def test_missing_or_different_references_are_not_collapsed(tmp_path):
    store,service=prepared(tmp_path);stamp=now()
    for ref,suffix in [(None,'一'),(None,'二'),('A1','三'),('A2','四'),('001','五'),('1','六')]:
        add(store,ref,130,stamp,suffix=suffix)
    assert len(service.orders()['orders'])==6


def test_reference_normalization_preserves_prefixes_and_leading_zeros(tmp_path):
    store,service=prepared(tmp_path);stamp=now()
    add(store,'Ａ 001',130,stamp-timedelta(minutes=1))
    add(store,'A001',130,stamp,suffix='重发')
    add(store,'B001',130,stamp)
    assert len(service.orders()['orders'])==2


def test_unselected_group_does_not_supply_representative_facts(tmp_path):
    store,service=prepared(tmp_path);stamp=now()
    add(store,'A1',130,stamp-timedelta(hours=1))
    add(store,'A1',250,stamp,group='乙群')
    row=service.orders()['orders'][0]
    assert row['match']['bucket']=='matched' and row['facts']['hourly_rate']['value']==130
    assert len(row['sources'])==2 and row['sources'][0]['group']=='甲群'
    store.update_groups(add='乙群');store.update_groups(selected=['乙群'])
    assert service.orders()['orders'][0]['match']['bucket']=='rejected'


def test_other_group_newer_repost_time_does_not_promote_an_older_version(tmp_path):
    store,service=prepared(tmp_path);stamp=now()
    add(store,'A1',130,stamp-timedelta(hours=2),suffix='旧版')
    add(store,'A1',130,stamp,group='乙群',suffix='旧版')
    add(store,'A1',250,stamp-timedelta(hours=1),suffix='新版')
    row=service.orders()['orders'][0]
    assert row['facts']['hourly_rate']['value']==250


def test_expired_history_is_kept_in_details_without_overriding_current_version(tmp_path):
    store,service=prepared(tmp_path);stamp=now()
    add(store,'A1',250,stamp-timedelta(days=4))
    add(store,'A1',130,stamp)
    row=service.orders()['orders'][0]
    assert row['match']['bucket']=='matched' and len(row['sources'])==2


def test_favorite_on_old_version_survives_and_unfavorite_clears_whole_reference(tmp_path):
    store,service=prepared(tmp_path);stamp=now()
    old=add(store,'A1',130,stamp-timedelta(hours=1));store.favorite(old,True)
    latest=add(store,'A1',150,stamp)
    assert service.orders()['orders'][0]['favorite_at']
    favorites=service.orders(favorites=True)['orders']
    assert len(favorites)==1 and favorites[0]['id']==latest and len(favorites[0]['sources'])==2
    store.favorite(latest,True)
    assert len(service.orders(favorites=True)['orders'])==1
    store.favorite(latest,False)
    assert not service.orders(favorites=True)['orders']
    assert len(store.messages())==2


def test_real_and_synthetic_same_reference_never_merge(tmp_path):
    store,service=prepared(tmp_path);stamp=now()
    add(store,'A1',130,stamp)
    add(store,'A1',250,stamp,synthetic=True)
    row=service.orders()['orders'][0]
    assert len(row['sources'])==1 and row['match']['bucket']=='matched'


def test_closure_update_still_links_after_same_issuer_reposts(tmp_path):
    store,service=prepared(tmp_path);stamp=now()
    add(store,'A1',130,stamp-timedelta(hours=2));add(store,'A1',150,stamp-timedelta(hours=1))
    mid=store.add_message(Message(group='甲群',sender='合成发布者',text='A1已出',sent_at=stamp))
    store.save_drafts(mid,[Draft(kind='update',reference='A1',status='closed',status_evidence='已出',source_text='A1已出')])
    result=service.orders()
    assert result['unlinked_updates']==0 and len(result['orders'])==1
    assert result['orders'][0]['match']['bucket']=='rejected'
    assert any(s.get('is_update') for s in result['orders'][0]['sources'])


def test_wrong_issuer_update_cannot_close_a_grouped_order(tmp_path):
    store,service=prepared(tmp_path);stamp=now()
    add(store,'A1',130,stamp-timedelta(hours=2));add(store,'A1',150,stamp-timedelta(hours=1))
    mid=store.add_message(Message(group='甲群',sender='其他人',text='A1已出',sent_at=stamp))
    store.save_drafts(mid,[Draft(kind='update',reference='A1',status='closed',status_evidence='已出')])
    result=service.orders()
    assert result['unlinked_updates']==1 and result['orders'][0]['match']['bucket']=='matched'
