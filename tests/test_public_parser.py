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
