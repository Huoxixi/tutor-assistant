import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .models import Message, Preferences, now


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def normalized(text):
    return "".join(text.split())


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY,payload TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'received',error TEXT);
                CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY,facts TEXT NOT NULL,reference TEXT,status TEXT NOT NULL,updated_at TEXT NOT NULL,synthetic INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS sources(order_id TEXT NOT NULL,message_id TEXT NOT NULL,PRIMARY KEY(order_id,message_id));
                CREATE TABLE IF NOT EXISTS favorites(order_id TEXT PRIMARY KEY,created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS updates(id TEXT PRIMARY KEY,message_id TEXT NOT NULL,payload TEXT NOT NULL,linked_order TEXT);
                CREATE TABLE IF NOT EXISTS scans(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS order_blocks(order_id TEXT NOT NULL,message_id TEXT NOT NULL,text TEXT NOT NULL,PRIMARY KEY(order_id,message_id));
                CREATE TABLE IF NOT EXISTS order_validation(order_id TEXT NOT NULL,message_id TEXT NOT NULL,issues TEXT NOT NULL,PRIMARY KEY(order_id,message_id));
                CREATE TABLE IF NOT EXISTS location_verifications(key TEXT PRIMARY KEY,payload TEXT NOT NULL);
            """)
            # An interrupted UI worker never claims a completed scan after restart.
            for row in db.execute("SELECT id,payload FROM scans").fetchall():
                scan = json.loads(row["payload"])
                if scan["state"] in {"running", "cancelling"}:
                    scan["state"] = "interrupted"
                    for group in scan["groups"]:
                        if group["state"] in {"queued", "reading"}:
                            group.update(state="incomplete", detail="程序中断，请重试")
                    db.execute("UPDATE scans SET payload=? WHERE id=?", (json.dumps(scan, ensure_ascii=False), row["id"]))

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, key, default=None):
        with self.db() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value, ensure_ascii=False)))

    def verifications(self):
        with self.db() as db:
            return {row['key']:json.loads(row['payload']) for row in db.execute('SELECT * FROM location_verifications')}

    def save_verification(self, key, payload):
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO location_verifications VALUES (?,?)',(key,json.dumps(payload,ensure_ascii=False)))

    def preferences(self):
        prefs = Preferences.model_validate(self.get("preferences", {}))
        prefs.profile = {key: value for key, value in prefs.profile.items() if key in {"teacher_gender", "school_tier"}}
        prefs.rules = [rule for rule in prefs.rules if rule.field in {"subject", "grade", "hourly_rate", "lesson_rate", "monthly_rate", "location"}]
        return prefs

    def group_state(self):
        selected = self.get("groups", [])
        catalog = list(dict.fromkeys([*self.get("group_catalog", []), *selected]))
        return {"groups": selected, "group_catalog": catalog}

    def update_groups(self, *, add=None, remove=None, selected=None):
        # Keep the saved list separate from scan selection, in one transaction.
        with self.db() as db:
            values = {row["key"]: json.loads(row["value"]) for row in db.execute("SELECT * FROM settings WHERE key IN ('groups','group_catalog')")}
            active = values.get("groups", [])
            catalog = list(dict.fromkeys([*values.get("group_catalog", []), *active]))
            if add:
                if add not in catalog:
                    catalog.append(add)
                    if len(active) < 10:
                        active.append(add)
            if remove:
                catalog = [name for name in catalog if name != remove]
                active = [name for name in active if name != remove]
            if selected is not None:
                if any(name not in catalog for name in selected):
                    raise ValueError("请先添加群名，再勾选筛选来源")
                active = selected
            for key, value in [("groups", active), ("group_catalog", catalog)]:
                db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value, ensure_ascii=False)))
        return {"groups": active, "group_catalog": catalog}

    def add_message(self, message: Message):
        payload = message.model_dump(mode="json")
        identity = [message.group, message.source_id] if message.source_id else [message.group, message.sender, payload["sent_at"], message.kind, message.text, message.synthetic]
        mid = digest(identity)
        with self.db() as db:
            db.execute("INSERT OR IGNORE INTO messages(id,payload,state) VALUES (?,?,?)", (mid, json.dumps(payload, ensure_ascii=False), "received" if message.kind == "text" else "unparsed"))
        return mid

    def messages(self, states=None):
        with self.db() as db:
            rows = db.execute("SELECT * FROM messages").fetchall()
        result = [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]
        return [r for r in result if not states or r["state"] in states]

    def mark(self, mid, state, error=None):
        with self.db() as db:
            db.execute("UPDATE messages SET state=?,error=? WHERE id=?", (state, error, mid))

    def save_drafts(self, mid, drafts):
        with self.db() as db:
            msg = json.loads(db.execute("SELECT payload FROM messages WHERE id=?", (mid,)).fetchone()[0])
            for index, draft in enumerate(drafts):
                data = draft.model_dump(mode="json")
                if draft.kind == "update":
                    db.execute("INSERT OR IGNORE INTO updates VALUES (?,?,?,NULL)", (digest([mid, index]), mid, json.dumps(data, ensure_ascii=False)))
                    continue
                # Only exact content + same extracted order merges across groups.
                # Similar facts alone are insufficient evidence of identity.
                source_text = draft.source_text or msg["text"]
                oid = digest([normalized(source_text), draft.reference or index, msg["synthetic"]])
                row = db.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
                stamp = msg["sent_at"] or ""
                if not row:
                    db.execute("INSERT INTO orders VALUES (?,?,?,?,?,?)", (oid, json.dumps(data["facts"], ensure_ascii=False), draft.reference, draft.status, stamp, int(msg["synthetic"])))
                elif stamp > row["updated_at"]:
                    db.execute("UPDATE orders SET updated_at=? WHERE id=?", (stamp, oid))
                db.execute("INSERT OR IGNORE INTO sources VALUES (?,?)", (oid, mid))
                db.execute("INSERT OR REPLACE INTO order_blocks VALUES (?,?,?)", (oid, mid, source_text))
                db.execute("INSERT OR REPLACE INTO order_validation VALUES (?,?,?)", (oid, mid, json.dumps(draft.validation_issues, ensure_ascii=False)))
            db.execute("UPDATE messages SET state=?,error=NULL WHERE id=?", ("processed" if drafts else "ignored", mid))
            self._apply_updates(db)

    def _apply_updates(self, db):
        for update in db.execute("SELECT * FROM updates").fetchall():
            draft = json.loads(update["payload"])
            if not draft["reference"]:
                continue
            message = json.loads(db.execute("SELECT payload FROM messages WHERE id=?", (update["message_id"],)).fetchone()[0])
            candidates = []
            for order in db.execute("SELECT * FROM orders WHERE reference=?", (draft["reference"],)).fetchall():
                sources = db.execute("SELECT m.payload FROM messages m JOIN sources s ON s.message_id=m.id WHERE s.order_id=?", (order["id"],)).fetchall()
                if any((p := json.loads(s[0]))["group"] == message["group"] and p["sender"] == message["sender"] and p["synthetic"] == message["synthetic"] for s in sources):
                    candidates.append(order)
            if not candidates:
                db.execute("UPDATE updates SET linked_order=NULL WHERE id=?", (update["id"],))
                continue
            # Reposts with this reference now represent one logical order.
            # Keep the existing same-group/same-issuer proof and update its
            # newest version, which is the version shown by the service.
            order = max(candidates,key=lambda item:(item['updated_at'],item['id']))
            stamp = message["sent_at"]
            # Undated or older updates never overwrite newer information.
            if not stamp or stamp < order["updated_at"]:
                continue
            facts = {**json.loads(order["facts"]), **draft["facts"]}
            status = "closed" if "closed" in {order["status"], draft["status"]} else "open"
            db.execute("UPDATE orders SET facts=?,status=?,updated_at=? WHERE id=?", (json.dumps(facts, ensure_ascii=False), status, stamp, order["id"]))
            db.execute("INSERT OR IGNORE INTO sources VALUES (?,?)", (order["id"], update["message_id"]))
            db.execute("INSERT OR REPLACE INTO order_validation VALUES (?,?,?)", (order["id"], update["message_id"], json.dumps(draft.get("validation_issues", []), ensure_ascii=False)))
            if draft.get("source_text"):
                db.execute("INSERT OR REPLACE INTO order_blocks VALUES (?,?,?)", (order["id"], update["message_id"], draft["source_text"]))
            db.execute("UPDATE updates SET linked_order=? WHERE id=?", (order["id"], update["id"]))

    def orders(self):
        with self.db() as db:
            rows = db.execute("SELECT o.*,f.created_at favorite_at FROM orders o LEFT JOIN favorites f ON f.order_id=o.id").fetchall()
            results = []
            for row in rows:
                item = dict(row)
                item["facts"] = json.loads(item["facts"])
                item["validation_issues"] = list(dict.fromkeys(issue for result in db.execute("SELECT issues FROM order_validation WHERE order_id=?", (item["id"],)).fetchall() for issue in json.loads(result[0])))
                item["sources"] = [{"id": s["id"], **json.loads(s["payload"])} for s in db.execute("SELECT m.* FROM messages m JOIN sources s ON s.message_id=m.id WHERE s.order_id=?", (item["id"],)).fetchall()]
                for source in item["sources"]:
                    source['is_update'] = db.execute('SELECT 1 FROM updates WHERE linked_order=? AND message_id=?',(item['id'],source['id'])).fetchone() is not None
                    block = db.execute("SELECT text FROM order_blocks WHERE order_id=? AND message_id=?", (item["id"], source["id"])).fetchone()
                    if block:
                        source["text"] = block[0]
                results.append(item)
            orphans = db.execute("SELECT COUNT(*) FROM updates WHERE linked_order IS NULL").fetchone()[0]
        # Similarity is a warning, not a merge operation.
        index = {}
        for item in results:
            core = [item["facts"].get(k, {}).get("value") for k in ["subject", "grade", "area"]]
            key = digest([core, item["synthetic"]]) if all(v is not None for v in core) else item["id"]
            item["_duplicate_key"] = key
            index.setdefault(key, []).append(item["id"])
        for item in results:
            item["possible_duplicates"] = [oid for oid in index[item.pop("_duplicate_key")] if oid != item["id"]]
        return results, orphans

    def favorite(self, oid, enabled):
        with self.db() as db:
            order=db.execute("SELECT reference,synthetic FROM orders WHERE id=?", (oid,)).fetchone()
            if not order:
                return False
            if enabled:
                db.execute("INSERT OR IGNORE INTO favorites VALUES (?,?)", (oid, now().isoformat()))
            else:
                from .deduplication import reference_key
                key=reference_key(order['reference'])
                ids=[oid] if not key else [row['id'] for row in db.execute('SELECT id,reference FROM orders WHERE synthetic=?',(order['synthetic'],)) if reference_key(row['reference'])==key]
                db.executemany("DELETE FROM favorites WHERE order_id=?", [(item,) for item in ids])
        return True

    def save_scan(self, scan):
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO scans VALUES (?,?)", (scan["id"], json.dumps(scan, ensure_ascii=False)))

    def scans(self):
        with self.db() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT payload FROM scans ORDER BY rowid DESC LIMIT 20").fetchall()]
