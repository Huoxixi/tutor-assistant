"""Public examples are synthetic and contain no chat history or addresses."""
import pytest

from app.local_extraction import LocalExtractor
from app.matching import match
from app.models import Preferences
from app.splitting import split_orders


def test_two_synthetic_references_keep_fields_in_their_own_orders():
    text = '单号：TEST20261019001A\n【学员】初二\n【科目】英语\n【课酬】150/h\n单号：TEST20261019002A\n【学员】三年级\n【科目】数学\n【课酬】120/h'
    assert len(split_orders(text)) == 2
    drafts = LocalExtractor().extract({'text': text})
    assert len(drafts) == 2
    assert drafts[0].facts['subject'].value == ['英语']
    assert drafts[1].facts['subject'].value == ['数学']
    assert drafts[0].facts['hourly_rate'].value == 150
    assert drafts[1].facts['hourly_rate'].value == 120


@pytest.mark.parametrize('separator', ['\n', '；', ';', '|'])
def test_bare_numeric_order_headers_split_without_field_leakage(separator):
    first='7101001【学员】初二【科目】英语【课酬】150/h'
    second='7101002【学员】三年级【科目】数学【课酬】120/h'
    drafts=LocalExtractor().extract({'text':first+separator+second})
    assert [d.reference for d in drafts]==['7101001','7101002']
    assert [d.facts['subject'].value for d in drafts]==[['英语'],['数学']]
    assert [d.facts['hourly_rate'].value for d in drafts]==[150,120]
    assert '7101002' not in drafts[0].source_text
    assert '7101001' not in drafts[1].source_text


def test_plain_grade_after_numeric_reference_is_an_order_start():
    text='7101001 初二英语 150/h\n7101002 三年级数学 120/h'
    assert [b.reference for b in split_orders(text)]==['7101001','7101002']


def test_numbered_multi_order_message_uses_ids_before_repeated_course_fields():
    first='【单号】TEST101\n【学员】初二\n【科目】英语\n【课酬】150/h\n【学员】初二\n【科目】数学\n【课酬】150/h'
    second='【单号】TEST102\n【学员】三年级\n【科目】语文\n【课酬】120/h'
    blocks=split_orders(first+'\n'+second)
    assert [(b.reference,b.text) for b in blocks]==[('TEST101',first),('TEST102',second)]


def test_inline_footer_references_label_preceding_orders():
    first='【科目】初二英语【课酬】150/h【地址】黄浦区【编号】TEST201'
    second='【科目】三年级数学【课酬】120/h【地址】杨浦区【编号】TEST202'
    drafts=LocalExtractor().extract({'text':first+'；'+second})
    assert [d.reference for d in drafts]==['TEST201','TEST202']
    assert [d.facts['subject'].value for d in drafts]==[['英语'],['数学']]
    assert [d.facts['hourly_rate'].value for d in drafts]==[150,120]


def test_repeated_current_identifier_does_not_create_an_empty_order():
    text='单号：TEST301\n【科目】初二英语【课酬】150/h\n单号：TEST301\n【单号】TEST302\n【科目】三年级数学【课酬】120/h'
    assert [b.reference for b in split_orders(text)]==['TEST301','TEST302']


@pytest.mark.parametrize('non_identifier',['13812345678','20261019','100-200元/小时','地址：合成路123456号'])
def test_contacts_prices_dates_and_street_numbers_are_not_order_boundaries(non_identifier):
    text='7101001【学员】初二【科目】英语【课酬】150/h；'+non_identifier+'；7101002【学员】三年级【科目】数学【课酬】120/h'
    assert [b.reference for b in split_orders(text)]==['7101001','7101002']


@pytest.mark.parametrize('text,expected', [
    ('【学员】初二\n【科目】英语\n【课酬】150/h', 'matched'),
    ('【学员】初二\n【科目】数学\n【课酬】150/h', 'rejected'),
    ('【学员】初二\n【科目】英语\n【课酬】80/h', 'rejected'),
    ('【学员】初二\n【科目】英语\n【课酬】面议', 'pending'),
])
def test_local_matching_preserves_hard_conditions(text, expected):
    draft = LocalExtractor().extract({'text': text})[0]
    prefs = Preferences(rules=[
        {'field': 'subject', 'value': ['英语'], 'mode': 'must'},
        {'field': 'hourly_rate', 'value': {'min': 100, 'max': 200}, 'mode': 'must'},
    ])
    result = match({key: fact.model_dump() for key, fact in draft.facts.items()}, prefs, text)
    assert result['bucket'] == expected
