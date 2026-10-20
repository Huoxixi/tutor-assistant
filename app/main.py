import os
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StrictBool, SecretStr
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .collector import preflight
from .extraction import ModelError, validate_preferences
from .models import LABELS, Preferences
from .local_extraction import PARSER_VERSION
from .service import Service
from .store import Store
from .key_store import KeyStoreError
from .travel import TravelError

ROOT = Path(__file__).resolve().parent.parent


class ScanBody(BaseModel):
    groups: list[str] = Field(min_length=1, max_length=10)


class WeChatAuthorizationBody(BaseModel):
    authorized: StrictBool


class GroupSelection(BaseModel):
    groups: list[str] = Field(max_length=10)


class GroupName(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class ModelBody(BaseModel):
    base_url: str = Field(default="", max_length=2000)
    model: str = Field(default="", max_length=200)
    api_key: str | None = Field(default=None, max_length=4000)
    ai_enabled: bool | None = None


class ProcessBody(BaseModel):
    limit: int | None = Field(default=None, ge=1, le=5000)


class TravelConfigBody(BaseModel):
    mode: Literal['transit', 'walking', 'driving'] = 'transit'
    api_key: SecretStr | None = None


class TravelAddressBody(BaseModel):
    address: str = Field(min_length=2, max_length=240)
    city: str = Field(min_length=2, max_length=50)


class TravelChoiceBody(BaseModel):
    candidate_id: str = Field(min_length=1, max_length=64)


def create_app(data_path=None):
    app = FastAPI(title="家教拾单", docs_url=None, redoc_url=None)
    store = Store(data_path or os.environ.get("TUTOR_DB", str(ROOT / "data" / "tutor.sqlite3")))
    service = Service(store)
    app.state.store, app.state.service = store, service
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver", "[::1]"])

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
            return JSONResponse({"detail": "仅允许本地页面访问"}, status_code=403)
        if request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "不允许跨站访问本地数据"}, status_code=403)
        if request.method in {"POST", "PUT", "DELETE"} and request.headers.get("x-tutor-client") != "local-ui":
            return JSONResponse({"detail": "请求缺少本地客户端标记"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    @app.exception_handler(RuntimeError)
    async def busy(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(PermissionError)
    async def authorization_required(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=403)

    @app.exception_handler(ModelError)
    async def model_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(KeyStoreError)
    async def key_storage_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(TravelError)
    async def travel_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.get('/api/travel')
    def travel_state():
        return service.travel.state

    @app.put('/api/travel/settings')
    def travel_settings(body: TravelConfigBody):
        return service.travel.configure(body.mode, body.api_key.get_secret_value() if body.api_key is not None else None)

    @app.put('/api/travel/authorization')
    def travel_authorization(body: WeChatAuthorizationBody):
        return service.travel.authorize(body.authorized)

    @app.post('/api/travel/origin/search')
    def travel_origin_search(body: TravelAddressBody):
        return service.travel.search_origin(body.address.strip(), body.city.strip())

    @app.put('/api/travel/origin')
    def travel_origin(body: TravelChoiceBody):
        return service.travel.confirm_origin(body.candidate_id)

    @app.delete('/api/travel/origin')
    def travel_clear_origin():
        return service.travel.clear_origin()

    def travel_order(oid):
        order = next((row for row in service.orders(favorites=True)['orders']
                      if row['id'] == oid and not row['synthetic']), None)
        if order is None:
            raise HTTPException(404, '只支持计算已收藏的订单，请先收藏或重新选择')
        return order

    @app.post('/api/travel/orders/{oid}/destinations')
    def travel_destinations(oid: str):
        order = travel_order(oid)
        result = service.travel.search_destination(order)
        current = travel_order(oid)
        if service.travel.destination_context(current) != service.travel.destination_context(order):
            raise TravelError('收藏中的地点已变化，请重新核对')
        return result

    @app.post('/api/travel/orders/{oid}/route')
    def travel_route(oid: str, body: TravelChoiceBody):
        order = travel_order(oid)
        result = service.travel.calculate(order, body.candidate_id)
        current = travel_order(oid)
        if service.travel.destination_context(current) != service.travel.destination_context(order):
            raise TravelError('收藏中的地点已变化，已忽略旧路线')
        return result

    @app.get("/api/state")
    def state():
        counts,selected_counts = {},{}
        selected=set(store.group_state()['groups'])
        for message in store.messages():
            if not message["payload"]["synthetic"]:
                counts[message["state"]] = counts.get(message["state"], 0) + 1
                if message['payload']['group'] in selected:
                    selected_counts[message['state']]=selected_counts.get(message['state'],0)+1
        return {"wechat_authorization": service.wechat_authorization, "preferences": store.preferences(), **store.group_state(), "parser": {"mode": "local", "version": PARSER_VERSION, "requires_model": False}, "model": {**store.get("model", {}), "key_configured": bool(service.key), "key_saved": service.key_store.saved, "key_load_error": service.key_load_error, "enabled": service.model_enabled, "ai_enabled":service.ai_enabled}, "scans": store.scans(), "processing": service.progress, "busy": service.lock.locked(), "counts": counts, "selected_counts":selected_counts, "labels": LABELS}

    @app.get("/api/connection")
    def connection():
        return service.check_wechat_connection(preflight)

    @app.put("/api/wechat/authorization")
    def wechat_authorization(body: WeChatAuthorizationBody):
        return service.set_wechat_authorization(body.authorized)

    @app.get("/api/regions")
    def regions():
        return FileResponse(ROOT / "app" / "assets" / "regions.json", media_type="application/json")

    @app.put("/api/preferences")
    def preferences(body: Preferences):
        try:
            validate_preferences(body)
        except (ValueError, TypeError, KeyError, ModelError):
            raise HTTPException(422, "筛选条件格式无效，请检查数值、范围和时间段") from None
        store.set("preferences", body.model_dump())
        return {"saved": True}

    @app.put("/api/groups")
    def groups(body: GroupSelection):
        names = list(dict.fromkeys(g.strip() for g in body.groups if g.strip()))
        if any(len(g) > 120 for g in names):
            raise HTTPException(422, "群名称不能超过120字")
        try:
            return store.update_groups(selected=names)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.post("/api/groups")
    def add_group(body: GroupName):
        name = body.name.strip()
        if not name:
            raise HTTPException(422, "请填写微信群名")
        return store.update_groups(add=name)

    @app.delete("/api/groups")
    def remove_group(body: GroupName):
        return store.update_groups(remove=body.name.strip())

    @app.put("/api/model")
    def model(body: ModelBody):
        if body.ai_enabled:
            raise ModelError('AI调用已停用；请使用本地筛选与高德通勤计算')
        from urllib.parse import urlparse
        cfg = body.model_dump(exclude={"api_key","ai_enabled"})
        base = urlparse(cfg["base_url"])
        if base.username or base.password or base.query or base.fragment:
            raise HTTPException(422, "接口地址不能包含密钥或查询参数")
        if cfg["base_url"] and (not base.hostname or base.scheme not in {"https", "http"} or base.scheme == "http" and base.hostname not in {"127.0.0.1", "localhost", "::1"}):
            raise HTTPException(422, "云端接口需要HTTPS，本地接口使用回环地址")
        if body.ai_enabled and (not cfg['base_url'] or not cfg['model'] or base.hostname=='api.deepseek.com' and not (body.api_key if body.api_key is not None else service.key)):
            raise HTTPException(422,'启用AI前请填写模型地址、名称和所需密钥')
        if body.api_key is not None:
            service.save_key(body.api_key)
        store.set("model", cfg)
        if body.ai_enabled is not None:
            store.set('ai_verification_enabled',body.ai_enabled)
            if body.ai_enabled:
                store.set('cloud_model_enabled',True)
        return {"saved": True, "key_configured": bool(service.key), "key_saved": service.key_store.saved}

    @app.post("/api/model/test")
    def test_model():
        raise ModelError('AI调用已暂停，当前使用本地筛选与高德通勤计算')

    @app.post("/api/process")
    def process(body: ProcessBody | None = None):
        if body is not None and body.limit is not None:
            service.launch(service.run_process, body.limit)
        else:
            service.launch(service.run_process)
        return {"started": True}

    @app.post('/api/verify')
    def verify_pending(body: ProcessBody | None = None):
        if not service.ai_enabled:
            raise ModelError('AI核验未启用，本地字段核对正常运行')
        service.launch(service.verify_pending,min(body.limit if body and body.limit else 20,200))
        return {'started':True}

    @app.post("/api/scans")
    def scan(body: ScanBody):
        groups = list(dict.fromkeys(g.strip() for g in body.groups if g.strip()))
        if not groups or any(len(g) > 120 for g in groups):
            raise HTTPException(422, "请选择1—10个群")
        status = service.check_wechat_connection(preflight)
        if not status["adapter_installed"] or not status["wechat_running"]:
            raise HTTPException(422, status["detail"])
        return service.start_scan(groups)

    @app.post("/api/cancel")
    def cancel():
        service.cancel.set()
        return {"cancelling": True, "detail": "正在停止；已读取的记录保留"}

    @app.get("/api/orders")
    def orders(favorites: bool = False):
        return service.orders(favorites)

    @app.put("/api/orders/{oid}/favorite")
    def favorite(oid: str, body: dict):
        if type(body.get("enabled")) is not bool:
            raise HTTPException(422, "请选择收藏状态")
        if not store.favorite(oid, body["enabled"]):
            raise HTTPException(404, "订单不存在")
        return {"saved": True}

    @app.post("/api/orders/{oid}/review")
    def review_order(oid: str):
        try:
            return service.review_order(oid)
        except KeyError:
            raise HTTPException(404, "订单不存在") from None

    @app.get("/")
    def home():
        return FileResponse(ROOT / "app" / "static" / "index.html")

    @app.get("/guide")
    def guide():
        return FileResponse(ROOT / "README.md", media_type="text/plain; charset=utf-8")

    @app.get("/api/docs", include_in_schema=False)
    def docs():
        from html import escape
        rows = "".join("<tr><td>" + escape(method.upper()) + "</td><td>" + escape(path) + "</td></tr>" for path, operations in app.openapi()["paths"].items() for method in operations)
        return HTMLResponse('<!doctype html><html lang="zh-CN"><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>本地API · 家教拾单</title><link rel="stylesheet" href="/static/app.css"><main><h1>本地API</h1><p>写请求需要请求头 X-Tutor-Client: local-ui。</p><p><a href="/openapi.json">完整 OpenAPI 定义（JSON）</a> · <a href="/guide">使用说明</a></p><div class="table-scroll"><table><thead><tr><th>方法</th><th>路径</th></tr></thead><tbody>' + rows + '</tbody></table></div></main></html>')

    app.mount("/static", StaticFiles(directory=ROOT / "app" / "static"), name="static")
    return app
