import os
import threading
import uuid
from datetime import timedelta

from .collector import Collector, preflight
from .extraction import Extractor, ModelError
from .local_extraction import LocalExtractor, PARSER_VERSION
from .location_verification import LocationVerifier, landmark_context
from .store import digest
from .matching import match
from .models import now
from .key_store import KeyStoreError, LocalKeyStore
from .deduplication import order_key,deduplicate_orders
from .travel import TravelService


class Service:
    def __init__(self, store, collector=None):
        self.store = store
        self.travel = TravelService(store)
        self.collector = collector or Collector()
        self.key_store = LocalKeyStore(store.path)
        self.key_load_error = False
        try:
            saved_key = self.key_store.load()
        except KeyStoreError:
            saved_key = ""
            self.key_load_error = True
        self.key = saved_key or os.environ.get("TUTOR_MODEL_KEY", "")
        self.cancel = threading.Event()
        self.lock = threading.Lock()
        self.progress = {"state": "idle", "processed": 0, "total": 0, "errors": 0, "detail": "尚未开始"}
        self.discovery = {"state": "idle", "detail": ""}
        # Consent belongs to this running instance, never to copied databases.
        self._wechat_lock = threading.RLock()
        self._wechat_authorized = False
        self._wechat_reading = False

    @property
    def wechat_authorization(self):
        with self._wechat_lock:
            return {"authorized": self._wechat_authorized, "scope": "current_run"}

    def require_wechat_authorization(self):
        if not self._wechat_authorized:
            raise PermissionError("请先同意并授权使用电脑微信，再连接或提取信息")

    def set_wechat_authorization(self, authorized):
        with self._wechat_lock:
            self._wechat_authorized = authorized is True
            if not self._wechat_authorized and self._wechat_reading:
                self.cancel.set()
            return self.wechat_authorization

    def check_wechat_connection(self, check):
        with self._wechat_lock:
            self.require_wechat_authorization()
            return check()

    def _launch_wechat(self, target):
        with self._wechat_lock:
            self.require_wechat_authorization()
            def guarded():
                try:
                    if self.cancel.is_set() or not self.wechat_authorization["authorized"]:
                        return
                    target()
                finally:
                    with self._wechat_lock:
                        self._wechat_reading = False
            was_reading = self._wechat_reading
            self._wechat_reading = True
            try:
                self.launch(guarded)
            except Exception:
                self._wechat_reading = was_reading
                raise

    @property
    def model_enabled(self):
        # Keep historic settings/keys for recovery, but this product flow is local + Amap.
        return False

    @property
    def ai_enabled(self):
        return False

    def verification_key(self, order):
        context=landmark_context(order)
        return digest([PARSER_VERSION,context]) if context else None

    def verify_location(self, order, cancel=None):
        if not self.ai_enabled:
            raise ModelError('AI核验未启用，本地字段核对正常运行')
        context=landmark_context(order)
        if not context:
            return {'confirmed':False,'summary':'原文缺少可搜索的明确地点，需向发布者确认','sources':[]}
        cached=self.store.verifications().get(self.verification_key(order),{})
        if cached.get('confirmed') is True and cached.get('checked_at','') >= (now()-timedelta(days=7)).isoformat():
            return cached
        result=LocationVerifier(self.extractor()).verify(context,cancel)
        result.update(checked_at=now().isoformat(),context=context)
        self.store.save_verification(self.verification_key(order),result)
        return result

    def save_key(self, value):
        self.key_store.save(value)
        self.key = value
        self.key_load_error = False

    def extractor(self):
        if not self.model_enabled:
            raise ModelError("云端解析已暂停，当前仅读取并保存微信消息")
        return Extractor(self.store.get("model", {}), self.key)

    def local_extractor(self):
        return LocalExtractor()

    def review_order(self, oid):
        if not self.ai_enabled:
            raise ModelError('AI核验未启用，本地字段核对正常运行')
        if not self.lock.acquire(blocking=False):
            raise RuntimeError('已有任务运行，请等待或先停止')
        self.cancel.clear()
        self.progress={'state':'verifying','processed':0,'total':1,'errors':0,'detail':'正在核验这一单的未确定条件'}
        try:
            result=self._review_order(oid)
            self.progress.update(state='cancelled' if self.cancel.is_set() else 'complete',processed=1,detail='核验已结束，本地字段保留')
            return result
        except ModelError:
            self.progress.update(state='partial',errors=1,detail='AI核验未完成，本地字段保留')
            raise
        finally:
            self.lock.release()

    def _review_order(self, oid):
        if not self.ai_enabled:
            raise ModelError('AI核验未启用，本地字段核对正常运行')
        orders = self.orders(favorites=True)['orders'] + self.orders()['orders']
        order = next((order for order in orders if order["id"] == oid and not order["synthetic"]), None)
        if order is None:
            raise KeyError(oid)
        if order['match']['bucket']!='pending':
            return {'summary':'本地已得到明确筛选结果，无需调用AI','concerns':[]}
        if order['match']['location_status']=='unconfirmed':
            result=self.verify_location(order,self.cancel)
            return {**result,'concerns':[]}
        # Review one source block; never send group names, sender or personal profile.
        from .privacy import redact
        source = order["sources"][0] if order["sources"] else {}
        text = redact(source.get("text", ""), source.get("sender", ""))
        return self.extractor().review(order["facts"], text)

    def process(self, cancel=None, limit=None, groups=None):
        cancel = cancel or self.cancel
        rows = sorted(self.store.messages({"received", "error"}), key=lambda r: r["payload"]["sent_at"] or "", reverse=True)
        if groups is not None:
            rows=[row for row in rows if row['payload']['group'] in groups]
        if limit is not None:
            rows = rows[:limit]
        self.progress = {"state": "processing", "processed": 0, "total": len(rows), "errors": 0, "detail": "正在本地识别订单并筛选"}
        if not rows:
            self.progress.update(state="no_messages", detail="已按当前条件筛选已有订单")
            return
        extractor = self.local_extractor()
        for row in rows:
            if cancel.is_set():
                self.progress.update(state="cancelled", detail="已停止解析，已保存结果保留")
                return
            try:
                drafts = extractor.extract(row["payload"])
                self.store.save_drafts(row["id"], drafts)
            except ModelError as exc:
                self.store.mark(row["id"], "error", str(exc))
                self.progress["errors"] += 1
                if exc.code in {"model_http_error", "model_timeout", "model_connection"}:
                    self.progress["processed"] += 1
                    self.progress.update(state="model_error", detail=str(exc))
                    return
            except Exception:
                self.store.mark(row["id"], "error", "解析失败，原文已保留，可重试")
                self.progress["errors"] += 1
            self.progress["processed"] += 1
        self.progress.update(state="partial" if self.progress["errors"] else "complete", detail="部分消息筛选失败，可重试；已完成结果保留" if self.progress["errors"] else "筛选完成，可查看下方结果")
        if self.ai_enabled and not cancel.is_set():
            self.verify_pending(cancel=cancel)

    def verify_pending(self, limit=20, cancel=None):
        if not self.ai_enabled:
            raise ModelError('AI核验未启用，本地字段核对正常运行')
        cancel=cancel or self.cancel
        candidates=[order for order in self.orders()['orders'] if order['match']['bucket']=='pending' and order['match']['location_status']=='unconfirmed' and landmark_context(order)]
        candidates=list({self.verification_key(order):order for order in candidates}.values())
        candidates=candidates[:limit]
        self.progress={'state':'verifying','processed':0,'total':len(candidates),'errors':0,'detail':'正在联网核验未确定地点'}
        for order in candidates:
            if cancel.is_set() or not self.ai_enabled:
                self.progress.update(state='cancelled',detail='已停止AI核验，本地结果保留')
                return
            try:
                self.verify_location(order,cancel)
            except ModelError as exc:
                self.progress['errors']+=1
                self.progress.update(state='partial',detail=str(exc))
                return
            self.progress['processed']+=1
        self.progress.update(state='complete',detail='核验完成；没有可靠网页依据的订单仍待确认')

    def run_process(self, limit=None):
        self.process(limit=limit,groups=self.store.group_state()['groups'] or None)

    def start_discovery(self):
        def run():
            self.discovery = {"state": "reading", "detail": "正在识别微信群"}
            try:
                names = self.collector.discover(self.cancel)
                self.store.set("group_catalog", names)
                self.discovery = {"state": "complete", "detail": f"识别到{len(names)}个群"}
            except Exception:
                self.discovery = {"state": "cancelled" if self.cancel.is_set() else "error", "detail": "群名识别未完成，可重试或直接填写群名"}
        self._launch_wechat(run)

    def launch(self, target, *args):
        if not self.lock.acquire(blocking=False):
            raise RuntimeError("已有扫描或解析任务运行，请等待或先取消")
        self.cancel.clear()

        def wrapped():
            try:
                target(*args)
            finally:
                self.lock.release()
        threading.Thread(target=wrapped, daemon=True).start()

    def start_scan(self, groups):
        end = now()
        scan = {"id": uuid.uuid4().hex, "state": "running", "start": (end - timedelta(hours=48)).isoformat(), "end": end.isoformat(), "groups": [{"group": group, "state": "queued", "count": 0, "detail": "等待扫描"} for group in groups]}

        def run():
            self.progress={"state":"reading","processed":0,"total":0,"errors":0,"detail":"正在提取微信消息"}
            self.store.save_scan(scan)
            try:
                for index, group in enumerate(groups):
                    if self.cancel.is_set():
                        for row in scan["groups"][index:]:
                            row.update(state="cancelled", detail="已取消，尚未读取")
                        break
                    scan["groups"][index].update(state="reading", detail="正在读取微信历史记录")
                    self.store.save_scan(scan)
                    def save_batch(batch):
                        for message in batch:
                            self.store.add_message(message)
                        scan["groups"][index]["count"] = scan["groups"][index].get("count", 0) + len(batch)
                        self.progress['processed']=sum(row.get('count',0) for row in scan['groups'])
                        self.store.save_scan(scan)
                    def save_progress(progress):
                        scan["groups"][index].update(progress)
                        self.store.save_scan(scan)
                    report = self.collector.collect(group, end - timedelta(hours=48), end, self.cancel, save_batch, on_progress=save_progress)
                    scan["groups"][index] = report
                    self.store.save_scan(scan)
                scan["state"] = "cancelled" if self.cancel.is_set() else "complete" if all(g["state"] == "complete" for g in scan["groups"]) else "incomplete"
                self.store.save_scan(scan)
            except Exception:
                scan["state"] = "incomplete"
                for group in scan["groups"]:
                    if group["state"] in {"reading", "queued"}:
                        group.update(state="incomplete", detail="扫描异常结束，已读取记录保留，请重试")
                self.store.save_scan(scan)
            count=sum(row.get('count',0) for row in scan['groups'])
            state='extracted' if scan['state']=='complete' else 'extraction_cancelled' if scan['state']=='cancelled' else 'extraction_partial'
            detail='提取完成，点击“开始筛选”处理已保存的信息' if state=='extracted' else '提取已停止，已保存的信息可点击“开始筛选”' if state=='extraction_cancelled' else '提取不完整，已保存的信息可点击“开始筛选”'
            self.progress={"state":state,"processed":count,"total":0,"errors":0,"detail":detail}

        self._launch_wechat(run)
        return scan

    def orders(self, favorites=False):
        orders, orphans = self.store.orders()
        favorite_keys={order_key(order) for order in orders if order['favorite_at']}
        verifications=self.store.verifications() if self.ai_enabled else {}
        prefs, end = self.store.preferences(), now()
        results = []
        groups = set(self.store.group_state()["groups"])
        for order in orders:
            if order["synthetic"] or favorites and order_key(order) not in favorite_keys:
                continue
            visible_sources = order["sources"] if favorites else [source for source in order["sources"] if source["group"] in groups]
            if not visible_sources:
                continue
            dates = [m["sent_at"] for m in visible_sources if m["sent_at"]]
            recent = any((end - timedelta(hours=48)).isoformat() <= stamp <= end.isoformat() for stamp in dates)
            unknown = any(m["sent_at"] is None for m in visible_sources)
            if not favorites and not recent and not unknown:
                continue
            order['_visible_updated_at']=max((stamp for stamp in dates if stamp<=end.isoformat()),default='')
            order["expired"] = not recent and not unknown
            verification=verifications.get(self.verification_key(order),{})
            verified=verification.get('confirmed') is True and verification.get('checked_at','') >= (end-timedelta(days=7)).isoformat()
            order['verification']=verification
            order["match"] = match(order["facts"], prefs, "\n".join(m["text"] for m in order["sources"]), order["status"] == "closed", not recent and unknown, [verification['region_code']] if verified else None)
            issues=order.get('validation_issues',[])
            requested={rule.field for rule in prefs.rules}
            def relevant_issue(issue):
                scopes=[(('科目','全科包含排除'),{'subject'}),(('年级','升学'),{'grade'}),(('城市','省份','区县','教学地区'),{'location','city','area'}),(('课酬','时薪','月薪','单次课酬'),{'hourly_rate','lesson_rate','monthly_rate'}),(('上课时间','星期','时间段'),{'schedule'}),(('课时','每次时长'),{'duration_hours','hourly_rate'}),(('每周次数',),{'weekly_sessions'})]
                for prefixes,fields in scopes:
                    if issue.startswith(prefixes):
                        return bool(requested & fields)
                optional=[(('学员性别',),{'student_gender'}),(('学员人数',),{'students_count'}),(('重点科目',),{'focus_subjects','subject'}),(('补充说明',),{'notes'}),(('授课方式',),{'mode'}),(('长期/短期',),{'term'}),(('是否接受试课',),{'trial'}),(('最高中介费','比例中介费'),{'agency_fee'})]
                for prefixes,fields in optional:
                    if issue.startswith(prefixes):
                        return bool(requested & fields)
                return True
            issues=[issue for issue in issues if relevant_issue(issue)]
            if verified:
                issues=[issue for issue in issues if not issue.startswith('教学地区只有地标或未识别地名')]
                order['match']['reasons'].append('地点归属已根据网页来源核验')
            if issues:
                order["match"]["unknowns"].extend(issues)
                if order["match"]["bucket"] == "matched":
                    order["match"]["bucket"] = "pending"
            results.append(order)
        before=len(results)
        results=deduplicate_orders(results,orders)
        results.sort(key=lambda o: (o["match"]["score"], o["updated_at"]), reverse=True)
        return {"orders": results, "unlinked_updates": orphans,"duplicate_orders_removed":before-len(results)}
