from __future__ import annotations

from typing import Any, Optional

from fastapi import FastAPI, HTTPException
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


class PipelinePayload(BaseModel):
    pipeline: PipelineBase
    filters: PipelineFilters | None = None
    writer: PipelineWriter | None = None
    delivery: DeliveryEmail | DeliveryFeishu | None = None


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
