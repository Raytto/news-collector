from __future__ import annotations

import sqlite3
from typing import Any, Optional

import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import db


class PipelineBase(BaseModel):
    name: str
    enabled: int = Field(1, ge=0, le=1)
    description: str | None = None


class PipelineFilters(BaseModel):
    all_categories: int = 1
    categories_json: list[str] | None = None
    all_src: int = 1
    include_src_json: list[str] | None = None


class PipelineWriter(BaseModel):
    type: str
    hours: int = 24
    weights_json: dict[str, Any] | None = None
    bonus_json: dict[str, Any] | None = None
    limit_per_category: dict[str, int] | int | None = None
    per_source_cap: int | None = None


class DeliveryEmail(BaseModel):
    kind: str = "email"
    email: str
    subject_tpl: str = ""


class DeliveryFeishu(BaseModel):
    kind: str = "feishu"
    app_id: str
    app_secret: str
    to_all_chat: int = 0
    chat_id: str | None = None
    title_tpl: str | None = None
    to_all: int = 0
    content_json: dict[str, Any] | None = None


class FeishuChatRequest(BaseModel):
    app_id: str
    app_secret: str


class PipelinePayload(BaseModel):
    pipeline: PipelineBase
    filters: PipelineFilters | None = None
    writer: PipelineWriter | None = None
    delivery: DeliveryEmail | DeliveryFeishu | None = None


class CategoryPayload(BaseModel):
    key: str
    label_zh: str
    enabled: int = Field(1, ge=0, le=1)


class CategoryUpdatePayload(BaseModel):
    key: Optional[str] = None
    label_zh: Optional[str] = None
    enabled: Optional[int] = Field(None, ge=0, le=1)


class SourcePayload(BaseModel):
    key: str
    label_zh: str
    enabled: int = Field(1, ge=0, le=1)
    category_key: str
    script_path: str
    addresses: list[str] = Field(default_factory=list)


class SourceUpdatePayload(BaseModel):
    key: Optional[str] = None
    label_zh: Optional[str] = None
    enabled: Optional[int] = Field(None, ge=0, le=1)
    category_key: Optional[str] = None
    script_path: Optional[str] = None
    addresses: Optional[list[str]] = None


class AiMetricPayload(BaseModel):
    key: str
    label_zh: str
    rate_guide_zh: Optional[str] = None
    default_weight: Optional[float] = None
    sort_order: int = 0
    active: int = Field(1, ge=0, le=1)


class AiMetricUpdatePayload(BaseModel):
    label_zh: Optional[str] = None
    rate_guide_zh: Optional[str] = None
    default_weight: Optional[float] = None
    sort_order: Optional[int] = None
    active: Optional[int] = Field(None, ge=0, le=1)


app = FastAPI(title="Pipelines Admin API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _init() -> None:
    db.ensure_db()


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/options")
def options() -> dict:
    with db.get_conn() as conn:
        return db.fetch_options(conn)


@app.get("/categories")
def list_categories() -> list[dict]:
    with db.get_conn() as conn:
        return db.fetch_categories(conn)


@app.post("/categories", status_code=201)
def create_category(payload: CategoryPayload) -> dict:
    with db.get_conn() as conn:
        try:
            new_id = db.create_category(conn, payload.model_dump())
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=400, detail="类别 key 已存在")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"id": new_id}


@app.put("/categories/{cid}")
def update_category(cid: int, payload: CategoryUpdatePayload) -> dict:
    with db.get_conn() as conn:
        try:
            db.update_category(conn, cid, payload.model_dump(exclude_unset=True))
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=400, detail="类别 key 已存在")
        except ValueError as exc:
            message = str(exc)
            status = 404 if "未找到类别" in message else 400
            raise HTTPException(status_code=status, detail=message)
        return {"id": cid}


@app.delete("/categories/{cid}")
def remove_category(cid: int) -> dict:
    with db.get_conn() as conn:
        try:
            db.delete_category(conn, cid)
        except ValueError as exc:
            message = str(exc)
            status = 404 if "未找到类别" in message else 400
            raise HTTPException(status_code=status, detail=message)
        return {"ok": True}


@app.get("/sources")
def list_sources() -> list[dict]:
    with db.get_conn() as conn:
        return db.fetch_sources(conn)


@app.post("/sources", status_code=201)
def create_source(payload: SourcePayload) -> dict:
    with db.get_conn() as conn:
        try:
            new_id = db.create_source(conn, payload.model_dump())
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=400, detail="来源 key 已存在")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"id": new_id}


@app.put("/sources/{sid}")
def update_source(sid: int, payload: SourceUpdatePayload) -> dict:
    with db.get_conn() as conn:
        try:
            db.update_source(conn, sid, payload.model_dump(exclude_unset=True))
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=400, detail="来源 key 已存在")
        except ValueError as exc:
            message = str(exc)
            status = 404 if "未找到来源" in message else 400
            raise HTTPException(status_code=status, detail=message)
        return {"id": sid}


@app.delete("/sources/{sid}")
def remove_source(sid: int) -> dict:
    with db.get_conn() as conn:
        try:
            db.delete_source(conn, sid)
        except ValueError as exc:
            message = str(exc)
            status = 404 if "未找到来源" in message else 400
            raise HTTPException(status_code=status, detail=message)
        return {"ok": True}


@app.get("/infos")
def list_infos(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    category: Optional[str] = None,
    source: Optional[str] = None,
    q: Optional[str] = None,
) -> dict:
    limit = page_size
    offset = (page - 1) * page_size
    with db.get_conn() as conn:
        return db.fetch_info_list(
            conn,
            limit=limit,
            offset=offset,
            category=category,
            source=source,
            search=q,
        )


@app.get("/infos/{info_id}")
def get_info_detail(info_id: int) -> dict:
    with db.get_conn() as conn:
        detail = db.fetch_info_detail(conn, info_id)
        if not detail:
            raise HTTPException(status_code=404, detail="资讯不存在")
        return detail


@app.get("/infos/{info_id}/ai_review")
def get_info_ai_review(info_id: int) -> dict:
    with db.get_conn() as conn:
        exists = conn.execute("SELECT 1 FROM info WHERE id=?", (info_id,)).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="资讯不存在")
        review = db.fetch_info_ai_review(conn, info_id)
        review["has_review"] = review["final_score"] is not None or bool(review["scores"])
        return review


@app.get("/ai-metrics")
def list_ai_metrics() -> list[dict]:
    with db.get_conn() as conn:
        return db.fetch_ai_metrics(conn)


@app.post("/ai-metrics", status_code=201)
def create_ai_metric(payload: AiMetricPayload) -> dict:
    with db.get_conn() as conn:
        try:
            new_id = db.create_ai_metric(conn, payload.model_dump())
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=400, detail="指标 key 已存在")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"id": new_id}


@app.put("/ai-metrics/{metric_id}")
def update_ai_metric(metric_id: int, payload: AiMetricUpdatePayload) -> dict:
    with db.get_conn() as conn:
        try:
            db.update_ai_metric(conn, metric_id, payload.model_dump(exclude_unset=True))
        except ValueError as exc:
            message = str(exc)
            status = 404 if "未找到指标" in message else 400
            raise HTTPException(status_code=status, detail=message)
        return {"id": metric_id}


@app.delete("/ai-metrics/{metric_id}")
def remove_ai_metric(metric_id: int) -> dict:
    with db.get_conn() as conn:
        try:
            db.delete_ai_metric(conn, metric_id)
        except ValueError as exc:
            message = str(exc)
            status = 404 if "未找到指标" in message else 400
            raise HTTPException(status_code=status, detail=message)
        return {"ok": True}


@app.post("/feishu/chats")
def list_feishu_chats(payload: FeishuChatRequest) -> dict:
    app_id = payload.app_id.strip()
    app_secret = payload.app_secret.strip()
    if not app_id or not app_secret:
        raise HTTPException(status_code=400, detail="App ID 和 App Secret 不能为空")

    try:
        token_resp = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
            timeout=10,
        )
        token_data = token_resp.json()
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail="无法访问飞书接口") from exc
    except ValueError as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=502, detail="飞书返回了无效的数据") from exc

    if token_resp.status_code != 200 or token_data.get("code") != 0:
        detail = token_data.get("msg") or "App ID 或 App Secret 校验失败"
        raise HTTPException(status_code=400, detail=detail)

    access_token = token_data.get("tenant_access_token")
    if not access_token:
        raise HTTPException(status_code=400, detail="未获取到飞书租户凭证")

    chats: list[dict[str, str | None]] = []
    page_token: str | None = None
    headers = {"Authorization": f"Bearer {access_token}"}
    while True:
        params = {"page_size": 200}
        if page_token:
            params["page_token"] = page_token
        try:
            chat_resp = requests.get(
                "https://open.feishu.cn/open-apis/im/v1/chats",
                headers=headers,
                params=params,
                timeout=10,
            )
            chat_data = chat_resp.json()
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail="获取群列表失败") from exc
        except ValueError as exc:  # pragma: no cover - defensive
            raise HTTPException(status_code=502, detail="飞书返回了无效的数据") from exc

        if chat_resp.status_code != 200 or chat_data.get("code"):
            detail = chat_data.get("msg") or "获取群列表失败"
            raise HTTPException(status_code=400, detail=detail)

        data = chat_data.get("data") or {}
        for item in data.get("items") or []:
            if not isinstance(item, dict):
                continue
            chat_id = item.get("chat_id")
            if not chat_id:
                continue
            name = item.get("name") or item.get("chat_alias") or item.get("description") or chat_id
            chats.append({"chat_id": chat_id, "name": name})

        if not data.get("has_more"):
            break
        page_token = data.get("page_token")
        if not page_token:
            break

    unique: list[dict[str, str | None]] = []
    seen_ids: set[str] = set()
    for chat in chats:
        chat_id = chat.get("chat_id")
        if not chat_id or chat_id in seen_ids:
            continue
        seen_ids.add(chat_id)
        unique.append(chat)

    unique.sort(key=lambda item: ((item.get("name") or "")[:128].lower(), item.get("chat_id") or ""))

    return {"items": unique}


@app.get("/pipelines")
def list_pipelines() -> list[dict]:
    with db.get_conn() as conn:
        return db.fetch_pipeline_list(conn)


@app.get("/pipelines/{pid}")
def get_pipeline(pid: int) -> dict:
    with db.get_conn() as conn:
        result = db.fetch_pipeline(conn, pid)
        if not result:
            raise HTTPException(status_code=404, detail="Pipeline not found")
        return result


@app.post("/pipelines", status_code=201)
def create_pipeline(payload: PipelinePayload) -> dict:
    with db.get_conn() as conn:
        new_id = db.create_or_update_pipeline(conn, payload.model_dump())
        return {"id": new_id}


@app.put("/pipelines/{pid}")
def update_pipeline(pid: int, payload: PipelinePayload) -> dict:
    with db.get_conn() as conn:
        # Ensure exists
        existed = db.fetch_pipeline(conn, pid)
        if not existed:
            raise HTTPException(status_code=404, detail="Pipeline not found")
        db.create_or_update_pipeline(conn, payload.model_dump(), pid=pid)
        return {"id": pid}


@app.delete("/pipelines/{pid}")
def remove_pipeline(pid: int) -> dict:
    with db.get_conn() as conn:
        existed = db.fetch_pipeline(conn, pid)
        if not existed:
            raise HTTPException(status_code=404, detail="Pipeline not found")
        db.delete_pipeline(conn, pid)
        return {"ok": True}
