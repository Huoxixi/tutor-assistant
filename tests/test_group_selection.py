from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.matching import match
from app.models import Draft, Message, Preferences, now
from app.service import Service
from app.store import Store


def test_saved_group_lifecycle_and_restart(tmp_path):
    path = tmp_path / "groups.sqlite3"
    with TestClient(create_app(path), headers={"x-tutor-client": "local-ui"}) as client:
        for name in ["甲群", "乙群", "甲群"]:
            assert client.post("/api/groups", json={"name": name}).status_code == 200
        assert client.put("/api/groups", json={"groups": ["乙群"]}).json() == {"groups": ["乙群"], "group_catalog": ["甲群", "乙群"]}
        assert client.put("/api/groups", json={"groups": []}).status_code == 200
        assert client.put("/api/groups", json={"groups": ["未添加群"]}).status_code == 422
        assert client.post("/api/groups", json={"name": " "}).status_code == 422
        assert client.post("/api/demo").status_code == 404
        assert client.post("/api/groups/discover").status_code == 404
    with TestClient(create_app(path), headers={"x-tutor-client": "local-ui"}) as client:
        state = client.get("/api/state").json()
        assert state["group_catalog"] == ["甲群", "乙群"] and state["groups"] == []
        client.put("/api/groups", json={"groups": ["甲群", "乙群"]})
        assert client.request("DELETE", "/api/groups", json={"name": "甲群"}).json() == {"groups": ["乙群"], "group_catalog": ["乙群"]}


def test_group_migration_and_ten_active_limit(tmp_path):
    store = Store(tmp_path / "old.sqlite3")
    store.set("groups", ["旧群"])
    assert store.group_state() == {"groups": ["旧群"], "group_catalog": ["旧群"]}
    for number in range(12):
        store.update_groups(add=f"群{number}")
    assert len(store.group_state()["groups"]) == 10
    assert len(store.group_state()["group_catalog"]) == 13
    store.update_groups(selected=[])
    store.update_groups(add="群11")  # Re-adding a saved name does not undo deselection.
    assert store.group_state()["groups"] == []


def test_orders_follow_checked_sources_and_favorites_stay(tmp_path):
    app = create_app(tmp_path / "sources.sqlite3")
    store = app.state.store
    for name in ["甲群", "乙群"]:
        store.update_groups(add=name)
    for group, synthetic, hours in [("甲群", False, 1), ("乙群", False, 1), ("甲群", True, 1)]:
        mid = store.add_message(Message(group=group, text=f"{group}{synthetic}数学", sent_at=now()-timedelta(hours=hours), synthetic=synthetic))
        store.save_drafts(mid, [Draft(reference=f"{group}{synthetic}", facts={"subject": {"value": ["数学"], "evidence": "数学"}})])
    for order in store.orders()[0]:
        store.favorite(order["id"], True)
    service = Service(store)
    store.update_groups(selected=["乙群"])
    assert [o["reference"] for o in service.orders()["orders"]] == ["乙群False"]
    assert len(service.orders(favorites=True)["orders"]) == 2
    store.update_groups(selected=[])
    assert service.orders()["orders"] == []
    with TestClient(app) as client:
        assert client.get("/api/orders?synthetic=true").json()["orders"] == []
        assert len(client.get("/api/orders?favorites=true&synthetic=true").json()["orders"]) == 2


def test_old_selected_source_not_made_recent_by_other_group(tmp_path):
    store = Store(tmp_path / "dates.sqlite3")
    store.update_groups(add="甲群")
    for group, hours in [("甲群", 72), ("乙群", 1)]:
        mid = store.add_message(Message(group=group, text="A1数学", sent_at=now()-timedelta(hours=hours)))
        store.save_drafts(mid, [Draft(reference="A1")])
    assert len(store.orders()[0]) == 1
    assert Service(store).orders()["orders"] == []


@pytest.mark.parametrize("subject", ["全科", "数学", "语文", "物理", "作业辅导", "钢琴", "编程", "日语"])
def test_all_subjects_includes_custom_subjects(subject):
    prefs = Preferences(rules=[{"field": "subject", "value": ["全科"]}])
    assert match({"subject": {"value": [subject], "evidence": subject}}, prefs, "")["bucket"] == "matched"
    assert match({}, prefs, "")["bucket"] == "pending"
