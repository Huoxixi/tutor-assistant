"""Opt-in Amap routes. No AI, chat bodies, automatic calls or disk response cache."""
import math
import re
import threading
import time
import uuid

import httpx

from .key_store import KeyStoreError, LocalKeyStore
from .privacy import redact
from .regions import REGION_INDEX, aliases
from .text_rules import Source
from .models import now
from .store import digest

MODES = {"transit": "公交", "walking": "步行", "driving": "驾车"}
PATHS = {"geocode": "/v3/geocode/geo",
         "transit": "/v3/direction/transit/integrated", "walking": "/v3/direction/walking",
         "driving": "/v3/direction/driving"}
COARSE = {"国家", "省", "市", "城市", "区县", "乡镇", "村庄"}
ADMINISTRATIVE_PLACES = {alias for node in REGION_INDEX.values() for alias in aliases(node['name'])}


class TravelError(Exception):
    pass


def coordinate(value):
    try:
        lon, lat = map(float, str(value).split(','))
        if not math.isfinite(lon + lat) or not -180 <= lon <= 180 or not -90 <= lat <= 90:
            raise ValueError
        return f"{lon:.6f},{lat:.6f}"
    except (ValueError, TypeError):
        raise TravelError("高德返回的位置坐标无效，请重新选择") from None


def text(value):
    return value.strip() if isinstance(value, str) else ""


class TravelService:
    def __init__(self, store):
        self.store = store
        self.key_store = LocalKeyStore(store.path, purpose="amap")
        self.key_load_error = False
        try:
            self.key = self.key_store.load()
        except KeyStoreError:
            self.key, self.key_load_error = "", True
        self.authorized = False
        self.session = uuid.uuid4().hex
        self.version = 0
        self.lock = threading.RLock()
        self.request_lock = threading.Lock()
        self.last_request = 0
        self.requests = 0
        self.choices = {}
        self.transport = None  # Injected only by isolated tests.

    @property
    def state(self):
        with self.lock:
            return {"authorized": self.authorized, "scope": "current_run", "session": self.session,
                    "key_saved": self.key_store.saved, "key_configured": bool(self.key),
                    "key_load_error": self.key_load_error, "mode": self.store.get('travel_mode', 'transit'),
                    "origin": self.store.get('travel_origin'), "version": self.version,
                    "requests_current_run": self.requests}

    def invalidate(self):
        self.version += 1
        self.choices.clear()

    def authorize(self, authorized):
        with self.lock:
            self.authorized = authorized is True
            self.invalidate()
            return self.state

    def configure(self, mode, key=None):
        with self.lock:
            if mode not in MODES:
                raise TravelError('请选择公交、步行或驾车')
            if key is not None:
                key = key.strip()
                if key and not re.fullmatch(r'[A-Za-z0-9_-]{16,128}', key):
                    raise TravelError('Key格式无效，请填写高德Web服务类型Key')
                self.key_store.save(key)
                self.key, self.key_load_error = key, False
            self.store.set('travel_mode', mode)
            self.invalidate()
            return self.state

    def require(self, version=None):
        if not self.authorized:
            raise PermissionError('请先授权向高德发送出发点和家教地点')
        if not self.key:
            raise TravelError('请先在“通勤计算”填写高德Web服务Key')
        if version is not None and version != self.version:
            raise TravelError('授权、出发点或设置已变化，请重新查询')

    def request(self, name, params, version):
        # Single request at a time and <= 2 QPS across all travel operations.
        with self.request_lock:
            delay = .55 - (time.monotonic() - self.last_request)
            if delay > 0:
                time.sleep(delay)
            with self.lock:
                self.require(version)
                key = self.key
                self.requests += 1
            self.last_request = time.monotonic()
            try:
                with httpx.Client(timeout=httpx.Timeout(15, connect=5), transport=self.transport,
                                  trust_env=False, follow_redirects=False) as client:
                    response = client.get('https://restapi.amap.com' + PATHS[name],
                                          params={**params, 'key': key, 'output': 'JSON'})
                    response.raise_for_status()
                    if len(response.content) > 1_000_000:
                        raise TravelError('高德返回内容过大，本次未计算')
                    result = response.json()
            except (httpx.HTTPError, ValueError):
                # Never echo an exception or URL containing the Key/coordinates.
                raise TravelError('高德请求未完成，请检查网络、Key权限和配额；未自动重试') from None
            with self.lock:
                self.require(version)
            if not isinstance(result, dict) or result.get('status') != '1':
                raise TravelError('高德未返回有效结果，请检查Key、权限及配额，或换用更明确的地址')
            return result

    def geocode(self, address, city, version):
        if not text(address) or not text(city):
            raise TravelError('请填写明确的城市和详细地点')
        result = self.request('geocode', {'address': address, 'city': city}, version)
        candidates = []
        items = result.get('geocodes')
        if not isinstance(items, list):
            raise TravelError('高德地点结果格式无效，请稍后重新查询')
        for item in items[:10]:
            if not isinstance(item, dict) or text(item.get('level')) in COARSE or not text(item.get('level')):
                continue
            try:
                loc = coordinate(item.get('location'))
            except TravelError:
                continue
            candidate = {"label": text(item.get('formatted_address'))[:240], 'location': loc,
                         'city': text(item.get('city')) or text(item.get('province')),
                         'citycode': text(item.get('citycode')), 'adcode': text(item.get('adcode')),
                         'level': text(item.get('level'))}
            if not candidate['label'] or not candidate['city']:
                continue
            candidates.append(candidate)
        if not candidates:
            raise TravelError('未找到明确地点，仅城市或区县中心不能用于通勤；请补充小区、学校或道路位置')
        return candidates

    def offer(self, candidates, kind, version, context=None):
        with self.lock:
            self.require(version)
            # Retain only a bounded, short-lived set of current confirmation choices.
            cutoff = time.monotonic() - 600
            self.choices = {k: v for k, v in self.choices.items() if v['created'] > cutoff}
            if len(self.choices) > 80:
                self.choices.clear()
            answer = []
            for item in candidates:
                cid = uuid.uuid4().hex
                self.choices[cid] = {'candidate': item, 'kind': kind, 'version': version,
                                     'context': context, 'created': time.monotonic()}
                answer.append({'id': cid, **item})
            return {'candidates': answer, 'version': version}

    def choice(self, cid, kind):
        row = self.choices.get(cid)
        if not row or row['kind'] != kind or time.monotonic() - row['created'] > 600:
            raise TravelError('位置确认已过期，请重新查询')
        self.require(row['version'])
        return row

    def search_origin(self, address, city):
        with self.lock:
            self.require()
            version = self.version
        return self.offer(self.geocode(address, city, version), 'origin', version)

    def confirm_origin(self, cid):
        with self.lock:
            row = self.choice(cid, 'origin')
            self.store.set('travel_origin', row['candidate'])
            self.invalidate()
            return self.state

    def clear_origin(self):
        with self.lock:
            self.store.set('travel_origin', None)
            self.invalidate()
            return self.state

    def destination_context(self, order):
        facts = order.get('facts', {})
        raw = text(facts.get('location', {}).get('value'))
        city = text(facts.get('city', {}).get('value'))
        province = text(facts.get('province', {}).get('value'))
        area = text(facts.get('area', {}).get('value'))
        # A unique explicit district can supply its city for routing only.
        # Never infer a destination city from the chosen origin or a landmark.
        if not city and area and not any('存在多个' in issue and any(label in issue for label in ('城市', '省份'))
                                        for issue in order.get('validation_issues', [])):
            parents = {node['path'][1] for node in REGION_INDEX.values()
                       if node['level'] == 3 and area in aliases(node['name'])
                       and (not province or province in aliases(REGION_INDEX[node['path'][0]]['name']))}
            if len(parents) == 1:
                city = REGION_INDEX[next(iter(parents))]['name']
        if not raw or not city:
            raise TravelError('原消息缺少明确的城市或详细授课地点，请先核对原文')
        address = Source(raw).text
        address = re.sub(r'^\s*(?:【(?:地址|地点|授课地址|教学地点)】|(?:地址|地点|授课地址|教学地点)\s*[:：])\s*', '', address)
        # Query only the extracted address, never a source message or another field.
        address = re.split(r'【|联系人|联系电话|联系方式|微信|(?i:wechat|wx|qq)|电话|手机|教员要求|老师要求|课酬|薪资', address)[0]
        address = redact(address, preserve_street_numbers=True)
        address = re.sub(r'(?:\d+|[一二三四五六七八九十百零]+)\s*(?:号楼|楼栋|栋|幢|单元|室|层|楼)', '', address)
        address = re.sub(r'【隐私\d+】|【门牌已隐去】|附近|周边|(?:可)?上门|线下授课', '', address)
        address = re.sub(r'\s+', '', address).strip(' :：,，。;；、')
        if len(address) < 2 or len(address) > 240 or re.search(r'\d{6,}|https?://|@|待定|待提供|未提供|地址不详|待确认|另行通知|未知', address):
            raise TravelError('详细授课地址不明确，请核对原文中的路名、路号或小区')
        remaining = address
        for name in (province, city):
            for alias in sorted(aliases(name), key=len, reverse=True) if name else []:
                if remaining.startswith(alias):
                    remaining = remaining[len(alias):]
                    break
        if not remaining or remaining in ADMINISTRATIVE_PLACES:
            raise TravelError('原文只有城市或区县，请补充路名、路号、小区或学校后再计算')
        if area and area not in address:
            address = area + address
        return {'place': address, 'city': city, 'province': province}

    def search_destination(self, order):
        with self.lock:
            self.require()
            if not self.store.get('travel_origin'):
                raise TravelError('请先设置并确认出发点')
            version = self.version
        context = self.destination_context(order)
        answer = self.offer(self.geocode(context['place'], context['city'], version),
                            'destination', version, {'order_id': order['id'], 'address': digest(context)})
        return {**answer, 'query': context['place'], 'city': context['city']}

    def calculate(self, order, cid):
        with self.lock:
            row = self.choice(cid, 'destination')
            if row['context'] != {'order_id': order['id'], 'address': digest(self.destination_context(order))}:
                raise TravelError('订单地点已变化，请重新核对')
            version, destination = self.version, row['candidate']
            origin = self.store.get('travel_origin')
            if not origin:
                raise TravelError('请先确认出发点')
            mode = self.store.get('travel_mode', 'transit')
        params = {'origin': origin['location'], 'destination': destination['location']}
        if mode == 'transit':
            params.update(city=origin['citycode'] or origin['city'],
                          cityd=destination['citycode'] or destination['city'], strategy='0')
        result = self.request(mode, params, version)
        route = result.get('route')
        options = route.get('transits' if mode == 'transit' else 'paths', []) if isinstance(route, dict) else []
        valid = []
        if not isinstance(options, list):
            raise TravelError('高德路线结果格式无效，请稍后重新查询')
        for option in options:
            try:
                distance, duration = float(option['distance']), float(option['duration'])
                if not math.isfinite(distance + duration) or min(distance, duration) < 0:
                    continue
                valid.append((duration, distance))
            except (KeyError, ValueError, TypeError):
                continue
        if not valid:
            raise TravelError('未取得可用路线，不能据此认定符合通勤要求')
        duration, distance = min(valid)
        return {'order_id': order['id'], 'version': version, 'session': self.session,
                'mode': mode, 'mode_label': MODES[mode],
                'distance_m': distance, 'duration_seconds': duration, 'origin': origin['label'],
                'destination': destination['label'], 'estimated': True, 'provider': '高德地图',
                'calculated_at': now().isoformat(), 'affects_matching': False}
