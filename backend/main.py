from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import requests
import smtplib
import subprocess
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid
from email.header import Header
from fastapi import BackgroundTasks, Cookie, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import db

FEISHU_CHAT_MAX_PAGE_SIZE = 100  # Per Feishu docs, current hard limit is 100


class PipelineBase(BaseModel):
    # Name can be empty or duplicated; keep optional
    name: str | None = None
    enabled: int = Field(1, ge=0, le=1)
    description: str | None = None
    debug_enabled: Optional[int] = Field(None, ge=0, le=1)


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


# -------------------- Auth Config --------------------

def _get_env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


AUTH_SESSION_DAYS = int(os.getenv("AUTH_SESSION_DAYS", "30") or 30)
AUTH_CODE_TTL_MINUTES = int(os.getenv("AUTH_CODE_TTL_MINUTES", "10") or 10)
AUTH_CODE_LENGTH = int(os.getenv("AUTH_CODE_LENGTH", "4") or 4)
AUTH_CODE_COOLDOWN_SECONDS = int(os.getenv("AUTH_CODE_COOLDOWN_SECONDS", "60") or 60)
AUTH_CODE_MAX_ATTEMPTS = int(os.getenv("AUTH_CODE_MAX_ATTEMPTS", "5") or 5)
AUTH_HOURLY_PER_EMAIL = int(os.getenv("AUTH_HOURLY_PER_EMAIL", "5") or 5)
AUTH_DAILY_PER_EMAIL = int(os.getenv("AUTH_DAILY_PER_EMAIL", "20") or 20)
AUTH_HOURLY_PER_IP = int(os.getenv("AUTH_HOURLY_PER_IP", "30") or 30)
AUTH_CODE_PEPPER = os.getenv("AUTH_CODE_PEPPER") or "dev-pepper-unsafe"
AUTH_COOKIE_SECURE = _get_env_bool("AUTH_COOKIE_SECURE", False)  # Default off for local dev

# SMTP + mail settings (align with deliver/mail_deliver.py)
SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "0") or 0)
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASS = os.getenv("SMTP_PASS", "").strip()
SMTP_USE_SSL = _get_env_bool("SMTP_USE_SSL", False)
SMTP_USE_TLS = _get_env_bool("SMTP_USE_TLS", False)
MAIL_FROM = os.getenv("MAIL_FROM", "noreply@email.pangruitao.com").strip() or "noreply@email.pangruitao.com"
MAIL_SUBJECT_PREFIX = os.getenv("MAIL_SUBJECT_PREFIX", "[情报鸭]").strip() or "[情报鸭]"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _fmt_expires(days: int) -> str:
    # SQLite expects local time strings or ISO; we keep UTC ISO
    return (_utc_now() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def _mask_email(email: str) -> str:
    try:
        name, host = email.split("@", 1)
        masked = (name[:1] + "***") if name else "***"
        return f"{masked}@{host}"
    except Exception:
        return "***"


def _set_session_cookie(resp: Response, sid: str) -> None:
    # 30 days by default
    max_age = AUTH_SESSION_DAYS * 24 * 3600
    resp.set_cookie(
        key="sid",
        value=sid,
        max_age=max_age,
        httponly=True,
        secure=AUTH_COOKIE_SECURE,
        samesite="lax",
        path="/",
    )


def _clear_session_cookie(resp: Response) -> None:
    resp.delete_cookie(key="sid", path="/")


class AuthEmailPayload(BaseModel):
    email: str
    name: Optional[str] = None


class AuthVerifyPayload(BaseModel):
    email: str
    code: str
    name: Optional[str] = None


class MeResponse(BaseModel):
    id: int
    email: str
    name: str
    is_admin: int


async def _require_user(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    return user


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if path == "/health" or path.startswith("/auth/"):
        return await call_next(request)
    sid = request.cookies.get("sid")
    if not sid:
        return Response(status_code=401)
    token_hash = _sha256(sid)
    with db.get_conn() as conn:
        sess = db.get_session_with_user(conn, token_hash)
        if not sess:
            return Response(status_code=401)
        # Check revoked/expired
        if sess.get("revoked_at"):
            return Response(status_code=401)
        try:
            # Compare as strings via SQLite to avoid tz ambiguity
            expired = int(conn.execute("SELECT CASE WHEN ? <= CURRENT_TIMESTAMP THEN 1 ELSE 0 END", (sess["expires_at"],)).fetchone()[0])
        except Exception:
            expired = 1
        if expired:
            return Response(status_code=401)
        # If user is disabled, revoke session and reject
        user_obj = sess.get("user") or {}
        try:
            if int(user_obj.get("enabled", 0)) != 1:
                db.revoke_session(conn, sess["id"])  # end this session
                return Response(status_code=401)
        except Exception:
            pass
        # Sliding touch (within cap)
        db.touch_session(conn, sess["id"])  # best-effort
        request.state.user = user_obj
    response = await call_next(request)
    return response


# -------------------- Email helpers --------------------

def _try_send_via_smtp(msg: MIMEText, *, host: str, port: int, user: str = "", password: str = "",
                        use_ssl: bool = False, use_tls: bool = False) -> bool:
    try:
        if use_ssl:
            smtp = smtplib.SMTP_SSL(host, port or 465, timeout=15)
        else:
            smtp = smtplib.SMTP(host, port or 25, timeout=15)
        with smtp as s:
            s.ehlo_or_helo_if_needed()
            if use_tls and not use_ssl:
                s.starttls()
                s.ehlo()
            if user:
                s.login(user, password)
            s.sendmail(msg["From"], [addr.strip() for addr in (msg["To"] or "").split(",") if addr.strip()], msg.as_string())
        return True
    except Exception as exc:  # pragma: no cover - best-effort
        print(f"[WARN] SMTP 发送失败: {exc}")
        return False


def _try_send_via_sendmail(msg: MIMEText) -> bool:
    sendmail = "/usr/sbin/sendmail"
    if not os.path.exists(sendmail):
        return False
    try:
        verbose = str(os.getenv("MAIL_VERBOSE", "")).strip().lower() in {"1", "true", "yes", "on"}
        # Derive recipients from headers to make envelope rcpt explicit
        rcpts: list[str] = []
        for key in ("To", "Cc", "Bcc"):
            raw = (msg.get(key) or "").strip()
            if raw:
                rcpts.extend([a.strip() for a in raw.split(",") if a.strip()])
        cmd = [sendmail, "-oi", "-t"]
        if verbose:
            cmd.append("-v")
        cmd += ["-f", msg["From"]]
        cmd += rcpts
        proc = subprocess.run(
            cmd,
            input=msg.as_string().encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=True,
        )
        if proc.stdout:
            try:
                print(proc.stdout.decode("utf-8", errors="ignore"))
            except Exception:
                pass
        return True
    except Exception as exc:  # pragma: no cover - best-effort
        print(f"[WARN] sendmail 调用失败: {exc}")
        return False


def _send_verification_email(to_email: str, code: str, purpose: str) -> bool:
    subject = f"{MAIL_SUBJECT_PREFIX} 验证码 {code}（10 分钟内有效）"
    purpose_text = "登录" if purpose == "login" else "注册"
    body = (
        f"您的{purpose_text}验证码为：{code}\n\n"
        "该验证码 10 分钟内有效，请尽快完成验证。\n"
        "如非本人操作，请忽略本邮件。\n"
        "—— 情报鸭团队"
    )
    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = MAIL_FROM
    msg["To"] = to_email
    msg["Subject"] = Header(subject, "utf-8")
    try:
        msg["Date"] = formatdate(localtime=True)
        domain = MAIL_FROM.split("@", 1)[1] if "@" in MAIL_FROM else None
        msg["Message-ID"] = make_msgid(domain=domain)
    except Exception:
        pass

    # Prefer local sendmail when available (works with tuned Postfix)
    if _try_send_via_sendmail(msg):
        return True

    # Next: explicit SMTP config
    if SMTP_HOST:
        if _try_send_via_smtp(
            msg,
            host=SMTP_HOST,
            port=SMTP_PORT,
            user=SMTP_USER,
            password=SMTP_PASS,
            use_ssl=SMTP_USE_SSL,
            use_tls=SMTP_USE_TLS,
        ):
            return True

    # Finally: local SMTP listener
    for host in ("127.0.0.1", "localhost"):
        if _try_send_via_smtp(msg, host=host, port=25):
            return True
    return False


def _send_verification_email_task(to_email: str, code: str, purpose: str) -> None:
    """Background task wrapper that logs result clearly.

    This makes it easier to diagnose why users don't receive codes
    (e.g., missing SMTP_HOST or no local MTA).
    """
    masked = _mask_email(to_email)
    method = (
        f"SMTP {SMTP_HOST}:{SMTP_PORT or ('465(ssl)' if SMTP_USE_SSL else '25/587')}"
        if SMTP_HOST
        else "local SMTP 127.0.0.1:25/sendmail"
    )
    ok = _send_verification_email(to_email, code, purpose)
    if ok:
        print(f"[auth] email sent to {masked} via {method}")
    else:
        print(
            f"[WARN] email NOT sent to {masked}. Configure SMTP_HOST/PORT/USER/PASS or local MTA."
        )


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/me")
def me(user: dict = Depends(_require_user)) -> MeResponse:
    return MeResponse(**user)


# -------------------- Admin: Users --------------------

class UserUpdatePayload(BaseModel):
    name: Optional[str] = None
    is_admin: Optional[int] = Field(None, ge=0, le=1)
    enabled: Optional[int] = Field(None, ge=0, le=1)


@app.get("/admin/users")
def admin_list_users(
    q: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user: dict = Depends(_require_user),
) -> dict:
    if int(user.get("is_admin", 0)) != 1:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    offset = (page - 1) * page_size
    with db.get_conn() as conn:
        items = db.list_users(conn, offset=offset, limit=page_size, q=q)
        total = db.count_users(conn, q=q)
        return {"items": items, "total": total}


@app.get("/admin/users/{uid}")
def admin_user_detail(uid: int, user: dict = Depends(_require_user)) -> dict:
    if int(user.get("is_admin", 0)) != 1:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    with db.get_conn() as conn:
        info = db.get_user_by_id(conn, uid)
        if not info:
            raise HTTPException(status_code=404, detail="用户不存在")
        pipelines = db.fetch_pipeline_list_by_owner(conn, uid)
        return {"user": info, "pipelines": pipelines}


@app.patch("/admin/users/{uid}")
def admin_update_user(uid: int, payload: UserUpdatePayload, user: dict = Depends(_require_user)) -> dict:
    if int(user.get("is_admin", 0)) != 1:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    with db.get_conn() as conn:
        existed = db.get_user_by_id(conn, uid)
        if not existed:
            raise HTTPException(status_code=404, detail="用户不存在")
        db.update_user(conn, uid, name=payload.name, is_admin=payload.is_admin, enabled=payload.enabled)
        # If disabled, revoke all active sessions immediately
        if payload.enabled is not None:
            try:
                if int(payload.enabled) != 1:
                    db.revoke_sessions_for_user(conn, uid)
            except Exception:
                pass
        updated = db.get_user_by_id(conn, uid)
        assert updated is not None
        return updated


@app.post("/auth/login/code")
def auth_login_code(payload: AuthEmailPayload, request: Request, background: BackgroundTasks) -> dict:
    email = (payload.email or "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="邮箱不能为空")
    with db.get_conn() as conn:
        existing = db.get_user_by_email(conn, email)
        if not existing:
            raise HTTPException(status_code=400, detail="邮箱不存在")
        # If user is disabled, block login attempts
        try:
            if int(existing.get("enabled", 0)) != 1:
                raise HTTPException(status_code=403, detail="此账号被禁用，请联系管理员")
        except Exception:
            pass
        # Rate limits
        ip = request.client.host if request.client else None
        if db.count_email_requests(conn, email=email, hours=1) >= AUTH_HOURLY_PER_EMAIL:
            raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")
        if db.count_email_requests(conn, email=email, hours=24) >= AUTH_DAILY_PER_EMAIL:
            raise HTTPException(status_code=429, detail="今日请求次数已达上限")
        if db.count_ip_requests(conn, ip=ip, hours=1) >= AUTH_HOURLY_PER_IP:
            raise HTTPException(status_code=429, detail="该 IP 请求过多")
        # Cooldown
        active = db.get_active_code(conn, email, "login")
        if active is not None:
            last_created = str(active["created_at"]) if active["created_at"] else None
            # Let SQLite compute diff
            if last_created:
                recent = int(
                    conn.execute(
                        "SELECT CASE WHEN datetime(?, ?||' seconds') > CURRENT_TIMESTAMP THEN 1 ELSE 0 END",
                        (last_created, AUTH_CODE_COOLDOWN_SECONDS),
                    ).fetchone()[0]
                )
                if recent:
                    raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")
        # Generate code and store hash
        code = "".join(secrets.choice("0123456789") for _ in range(max(4, AUTH_CODE_LENGTH)))
        code_hash = _sha256(code + AUTH_CODE_PEPPER)
        db.upsert_email_code(
            conn,
            email=email,
            purpose="login",
            code_hash=code_hash,
            ttl_seconds=AUTH_CODE_TTL_MINUTES * 60,
            max_attempts=AUTH_CODE_MAX_ATTEMPTS,
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            user_id=int(existing["id"]),
        )
    # Send email asynchronously (best effort)
    masked = _mask_email(email)
    # Send synchronously so we can surface failure to client
    method = (
        f"SMTP {SMTP_HOST}:{SMTP_PORT or ('465(ssl)' if SMTP_USE_SSL else '25/587')}" if SMTP_HOST else "local SMTP 127.0.0.1:25/sendmail"
    )
    if _send_verification_email(email, code, "login"):
        print(f"[auth] login code sent to {masked} via {method}")
        return {"ok": True}
    else:
        print(f"[WARN] login code NOT sent to {masked}. Check SMTP or MTA config.")
        raise HTTPException(status_code=502, detail="验证码发送失败，请稍后再试")


@app.post("/auth/signup/code")
def auth_signup_code(payload: AuthEmailPayload, request: Request, background: BackgroundTasks) -> dict:
    email = (payload.email or "").strip().lower()
    name = (payload.name or "").strip()
    if not email:
        raise HTTPException(status_code=400, detail="邮箱不能为空")
    if not name:
        raise HTTPException(status_code=400, detail="昵称不能为空")
    with db.get_conn() as conn:
        existing = db.get_user_by_email(conn, email)
        if existing:
            raise HTTPException(status_code=400, detail="邮箱已存在")
        ip = request.client.host if request.client else None
        if db.count_email_requests(conn, email=email, hours=1) >= AUTH_HOURLY_PER_EMAIL:
            raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")
        if db.count_email_requests(conn, email=email, hours=24) >= AUTH_DAILY_PER_EMAIL:
            raise HTTPException(status_code=429, detail="今日请求次数已达上限")
        if db.count_ip_requests(conn, ip=ip, hours=1) >= AUTH_HOURLY_PER_IP:
            raise HTTPException(status_code=429, detail="该 IP 请求过多")
        active = db.get_active_code(conn, email, "signup")
        if active is not None:
            last_created = str(active["created_at"]) if active["created_at"] else None
            if last_created:
                recent = int(
                    conn.execute(
                        "SELECT CASE WHEN datetime(?, ?||' seconds') > CURRENT_TIMESTAMP THEN 1 ELSE 0 END",
                        (last_created, AUTH_CODE_COOLDOWN_SECONDS),
                    ).fetchone()[0]
                )
                if recent:
                    raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")
        code = "".join(secrets.choice("0123456789") for _ in range(max(4, AUTH_CODE_LENGTH)))
        code_hash = _sha256(code + AUTH_CODE_PEPPER)
        db.upsert_email_code(
            conn,
            email=email,
            purpose="signup",
            code_hash=code_hash,
            ttl_seconds=AUTH_CODE_TTL_MINUTES * 60,
            max_attempts=AUTH_CODE_MAX_ATTEMPTS,
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            user_id=None,
        )
    masked = _mask_email(email)
    method = (
        f"SMTP {SMTP_HOST}:{SMTP_PORT or ('465(ssl)' if SMTP_USE_SSL else '25/587')}" if SMTP_HOST else "local SMTP 127.0.0.1:25/sendmail"
    )
    if _send_verification_email(email, code, "signup"):
        print(f"[auth] signup code sent to {masked} via {method}")
        return {"ok": True}
    else:
        print(f"[WARN] signup code NOT sent to {masked}. Check SMTP or MTA config.")
        raise HTTPException(status_code=502, detail="验证码发送失败，请稍后再试")


@app.post("/auth/login/verify")
def auth_login_verify(payload: AuthVerifyPayload, response: Response) -> dict:
    email = (payload.email or "").strip().lower()
    code = (payload.code or "").strip()
    if not email or not code:
        raise HTTPException(status_code=400, detail="参数错误")
    code_hash = _sha256(code + AUTH_CODE_PEPPER)
    with db.get_conn() as conn:
        ok, uid = db.verify_email_code(conn, email=email, purpose="login", input_hash=code_hash)
        if not ok:
            raise HTTPException(status_code=400, detail="验证码错误或已失效")
        user = db.get_user_by_email(conn, email)
        if not user:
            raise HTTPException(status_code=400, detail="邮箱不存在")
        # Check user enabled
        try:
            if int(user.get("enabled", 0)) != 1:
                raise HTTPException(status_code=403, detail="此账号被禁用，请联系管理员")
        except Exception:
            pass
        # Create session
        sid = secrets.token_hex(32)
        token_hash = _sha256(sid)
        db.create_session(
            conn,
            session_id=secrets.token_hex(16),
            user_id=int(user["id"]),
            token_hash=token_hash,
            expires_at=_fmt_expires(AUTH_SESSION_DAYS),
        )
        db.set_user_last_login(conn, int(user["id"]))
    _set_session_cookie(response, sid)
    return {"id": user["id"], "email": user["email"], "name": user["name"], "is_admin": user["is_admin"]}


@app.post("/auth/signup/verify")
def auth_signup_verify(payload: AuthVerifyPayload, response: Response) -> dict:
    email = (payload.email or "").strip().lower()
    code = (payload.code or "").strip()
    name = (payload.name or "").strip() or email
    if not email or not code:
        raise HTTPException(status_code=400, detail="参数错误")
    code_hash = _sha256(code + AUTH_CODE_PEPPER)
    with db.get_conn() as conn:
        ok, _ = db.verify_email_code(conn, email=email, purpose="signup", input_hash=code_hash)
        if not ok:
            raise HTTPException(status_code=400, detail="验证码错误或已失效")
        # Create user then session
        try:
            uid = db.create_user(conn, email=email, name=name, is_admin=0, verified=True)
        except sqlite3.IntegrityError:
            # Race-condition: user was created in-between
            existing = db.get_user_by_email(conn, email)
            uid = int(existing["id"]) if existing else None
        if not uid:
            raise HTTPException(status_code=500, detail="创建用户失败")
        user = db.get_user_by_id(conn, int(uid))
        sid = secrets.token_hex(32)
        token_hash = _sha256(sid)
        db.create_session(
            conn,
            session_id=secrets.token_hex(16),
            user_id=int(uid),
            token_hash=token_hash,
            expires_at=_fmt_expires(AUTH_SESSION_DAYS),
        )
    _set_session_cookie(response, sid)
    return {"id": user["id"], "email": user["email"], "name": user["name"], "is_admin": user["is_admin"]}


@app.post("/auth/logout")
def auth_logout(response: Response, sid: Optional[str] = Cookie(default=None)) -> dict:
    if sid:
        token_hash = _sha256(sid)
        with db.get_conn() as conn:
            sess = db.get_session_with_user(conn, token_hash)
            if sess:
                db.revoke_session(conn, sess["id"])
    _clear_session_cookie(response)
    return {"ok": True}


@app.get("/options")
def options(user: dict = Depends(_require_user)) -> dict:
    with db.get_conn() as conn:
        return db.fetch_options(conn)


@app.get("/categories")
def list_categories(user: dict = Depends(_require_user)) -> list[dict]:
    with db.get_conn() as conn:
        return db.fetch_categories(conn)


@app.post("/categories", status_code=201)
def create_category(payload: CategoryPayload, user: dict = Depends(_require_user)) -> dict:
    if int(user.get("is_admin", 0)) != 1:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    with db.get_conn() as conn:
        try:
            new_id = db.create_category(conn, payload.model_dump())
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=400, detail="类别 key 已存在")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"id": new_id}


@app.put("/categories/{cid}")
def update_category(cid: int, payload: CategoryUpdatePayload, user: dict = Depends(_require_user)) -> dict:
    if int(user.get("is_admin", 0)) != 1:
        raise HTTPException(status_code=403, detail="需要管理员权限")
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
def remove_category(cid: int, user: dict = Depends(_require_user)) -> dict:
    if int(user.get("is_admin", 0)) != 1:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    with db.get_conn() as conn:
        try:
            db.delete_category(conn, cid)
        except ValueError as exc:
            message = str(exc)
            status = 404 if "未找到类别" in message else 400
            raise HTTPException(status_code=status, detail=message)
        return {"ok": True}


@app.get("/sources")
def list_sources(user: dict = Depends(_require_user)) -> list[dict]:
    with db.get_conn() as conn:
        return db.fetch_sources(conn)


@app.post("/sources", status_code=201)
def create_source(payload: SourcePayload, user: dict = Depends(_require_user)) -> dict:
    if int(user.get("is_admin", 0)) != 1:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    with db.get_conn() as conn:
        try:
            new_id = db.create_source(conn, payload.model_dump())
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=400, detail="来源 key 已存在")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"id": new_id}


@app.put("/sources/{sid}")
def update_source(sid: int, payload: SourceUpdatePayload, user: dict = Depends(_require_user)) -> dict:
    if int(user.get("is_admin", 0)) != 1:
        raise HTTPException(status_code=403, detail="需要管理员权限")
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
def remove_source(sid: int, user: dict = Depends(_require_user)) -> dict:
    if int(user.get("is_admin", 0)) != 1:
        raise HTTPException(status_code=403, detail="需要管理员权限")
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
def get_info_detail(info_id: int, user: dict = Depends(_require_user)) -> dict:
    with db.get_conn() as conn:
        detail = db.fetch_info_detail(conn, info_id)
        if not detail:
            raise HTTPException(status_code=404, detail="资讯不存在")
        return detail


@app.get("/infos/{info_id}/ai_review")
def get_info_ai_review(info_id: int, user: dict = Depends(_require_user)) -> dict:
    with db.get_conn() as conn:
        exists = conn.execute("SELECT 1 FROM info WHERE id=?", (info_id,)).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="资讯不存在")
        review = db.fetch_info_ai_review(conn, info_id)
        review["has_review"] = review["final_score"] is not None or bool(review["scores"])
        return review


@app.get("/ai-metrics")
def list_ai_metrics(user: dict = Depends(_require_user)) -> list[dict]:
    with db.get_conn() as conn:
        return db.fetch_ai_metrics(conn)


@app.post("/ai-metrics", status_code=201)
def create_ai_metric(payload: AiMetricPayload, user: dict = Depends(_require_user)) -> dict:
    if int(user.get("is_admin", 0)) != 1:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    with db.get_conn() as conn:
        try:
            new_id = db.create_ai_metric(conn, payload.model_dump())
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=400, detail="指标 key 已存在")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"id": new_id}


@app.put("/ai-metrics/{metric_id}")
def update_ai_metric(metric_id: int, payload: AiMetricUpdatePayload, user: dict = Depends(_require_user)) -> dict:
    if int(user.get("is_admin", 0)) != 1:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    with db.get_conn() as conn:
        try:
            db.update_ai_metric(conn, metric_id, payload.model_dump(exclude_unset=True))
        except ValueError as exc:
            message = str(exc)
            status = 404 if "未找到指标" in message else 400
            raise HTTPException(status_code=status, detail=message)
        return {"id": metric_id}


@app.delete("/ai-metrics/{metric_id}")
def remove_ai_metric(metric_id: int, user: dict = Depends(_require_user)) -> dict:
    if int(user.get("is_admin", 0)) != 1:
        raise HTTPException(status_code=403, detail="需要管理员权限")
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

    chats: list[dict[str, Any]] = []
    page_token: str | None = None
    headers = {"Authorization": f"Bearer {access_token}"}
    while True:
        params = {"page_size": FEISHU_CHAT_MAX_PAGE_SIZE}
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
            description = item.get("description")
            member_count_raw = item.get("member_count")
            try:
                member_count: int | None = int(member_count_raw) if member_count_raw is not None else None
            except (TypeError, ValueError):
                member_count = None
            chats.append(
                {
                    "chat_id": chat_id,
                    "name": name,
                    "description": description if isinstance(description, str) else None,
                    "member_count": member_count,
                }
            )

        if not data.get("has_more"):
            break
        page_token = data.get("page_token")
        if not page_token:
            break

    unique: list[dict[str, Any]] = []
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
def list_pipelines(user: dict = Depends(_require_user)) -> list[dict]:
    with db.get_conn() as conn:
        items = db.fetch_pipeline_list(conn)
        if int(user.get("is_admin", 0)) == 1:
            return items
        uid = int(user["id"])
        return [it for it in items if (it.get("owner_user_id") == uid)]


@app.get("/pipelines/{pid}")
def get_pipeline(pid: int, user: dict = Depends(_require_user)) -> dict:
    with db.get_conn() as conn:
        result = db.fetch_pipeline(conn, pid)
        if not result:
            raise HTTPException(status_code=404, detail="Pipeline not found")
        if int(user.get("is_admin", 0)) != 1:
            owner_id = (result.get("pipeline") or {}).get("owner_user_id")
            if owner_id is None or int(owner_id) != int(user["id"]):
                raise HTTPException(status_code=403, detail="无权访问该投递")
        return result


@app.post("/pipelines", status_code=201)
def create_pipeline(payload: PipelinePayload, user: dict = Depends(_require_user)) -> dict:
    with db.get_conn() as conn:
        owner_id = int(user["id"]) if int(user.get("is_admin", 0)) != 1 else int(user["id"])  # default to self
        new_id = db.create_or_update_pipeline(conn, payload.model_dump(), owner_user_id=owner_id)
        return {"id": new_id}


@app.put("/pipelines/{pid}")
def update_pipeline(pid: int, payload: PipelinePayload, user: dict = Depends(_require_user)) -> dict:
    with db.get_conn() as conn:
        existed = db.fetch_pipeline(conn, pid)
        if not existed:
            raise HTTPException(status_code=404, detail="Pipeline not found")
        if int(user.get("is_admin", 0)) != 1:
            owner_id = (existed.get("pipeline") or {}).get("owner_user_id")
            if owner_id is None or int(owner_id) != int(user["id"]):
                raise HTTPException(status_code=403, detail="无权修改该投递")
        db.create_or_update_pipeline(conn, payload.model_dump(), pid=pid)
        return {"id": pid}


@app.delete("/pipelines/{pid}")
def remove_pipeline(pid: int, user: dict = Depends(_require_user)) -> dict:
    with db.get_conn() as conn:
        existed = db.fetch_pipeline(conn, pid)
        if not existed:
            raise HTTPException(status_code=404, detail="Pipeline not found")
        if int(user.get("is_admin", 0)) != 1:
            owner_id = (existed.get("pipeline") or {}).get("owner_user_id")
            if owner_id is None or int(owner_id) != int(user["id"]):
                raise HTTPException(status_code=403, detail="无权删除该投递")
        db.delete_pipeline(conn, pid)
        return {"ok": True}
