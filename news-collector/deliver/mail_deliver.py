from __future__ import annotations

import argparse
import os
import sqlite3
from email.header import Header
import re
import html as htmllib
import textwrap
import json
from typing import Optional, Tuple
from email.mime.text import MIMEText
from email import encoders as _encoders
from email.mime.multipart import MIMEMultipart
from email.utils import formatdate, make_msgid
from pathlib import Path
from datetime import datetime
from urllib.parse import urlencode


DATE_PLACEHOLDER_VARIANTS = ("${date_zh}", "$(date_zh)", "${data_zh}", "$(data_zh)")
TS_PLACEHOLDER_VARIANTS = ("${ts}", "$(ts)")


DEFAULT_SENDER = "news@news.pangruitao.com"
DEFAULT_RECEIVERS = ["306483372@qq.com"]


def _build_frontend_links(base: str, email: str | None, pipeline_id: int | None) -> tuple[str | None, str | None]:
    base_clean = (base or "").strip().rstrip("/")
    if not base_clean:
        return (None, None)
    manage_url = base_clean + "/"
    unsubscribe_url: str | None = None
    if email:
        qs = {"email": email.strip(), "reason": "email_footer"}
        if pipeline_id is not None:
            qs["pipeline_id"] = pipeline_id
        unsubscribe_url = f"{base_clean}/unsubscribe?{urlencode(qs)}"
    return (manage_url, unsubscribe_url)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Send an HTML file via Resend API")
    p.add_argument("--html", required=True, help="Path to the HTML file to send")
    p.add_argument("--subject", default="", help="Mail subject; default is 整合YYYY年MM月DD日")
    # Sender can be overridden by env MAIL_FROM after parsing
    p.add_argument("--sender", default=DEFAULT_SENDER, help="Sender email address")
    p.add_argument("--to", default=",".join(DEFAULT_RECEIVERS), help="Comma-separated recipient addresses")
    p.add_argument("--dump-msg", default="", help="Optional path to dump RFC822 message for debugging")

    p.add_argument("--dry-run", action="store_true", help="Print message metadata without sending")
    p.add_argument("--plain-only", action="store_true", help="Send only text/plain part (omit HTML)")
    return p.parse_args()


def _env_pipeline_id() -> int | None:
    raw = (os.getenv("PIPELINE_ID") or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _load_email_delivery_from_db(db_path: Path, pipeline_id: int) -> tuple[str | None, str | None]:
    """Return (email, subject_tpl) for pipeline, or (None, None) when not found."""
    # DB lives at data/info.db relative to repo root by convention
    try:
        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute(
                "SELECT email, subject_tpl FROM pipeline_deliveries_email WHERE pipeline_id=?",
                (pipeline_id,),
            ).fetchone()
            if not row:
                return (None, None)
            email = str(row[0] or "").strip() or None
            subject_tpl = str(row[1] or "").strip() or None
            return (email, subject_tpl)
    except Exception:
        return (None, None)


def _render_subject_from_tpl(tpl: str | None) -> str:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    date_zh = datetime.now().strftime("%Y年%m月%d日")
    subject = str(tpl or "")
    for placeholder in TS_PLACEHOLDER_VARIANTS:
        subject = subject.replace(placeholder, ts)
    for placeholder in DATE_PLACEHOLDER_VARIANTS:
        subject = subject.replace(placeholder, "")
    subject = subject.strip()
    return f"{subject}{date_zh}" if subject else date_zh


def try_send_via_smtp(msg: MIMEText, sender: str, receivers: list[str], host: str, port: int,
                      user: str = "", password: str = "", use_ssl: bool = False, use_tls: bool = False) -> bool:
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
            # send_message handles bytes conversion with correct policies
            s.send_message(msg, from_addr=sender, to_addrs=receivers)
        return True
    except Exception as exc:
        print(f"[WARN] SMTP 发送失败: {exc}")
        return False


def try_send_via_sendmail(msg: MIMEText, sender: str, receivers: list[str]) -> bool:
    sendmail = "/usr/sbin/sendmail"
    if not Path(sendmail).exists():
        return False
    try:
        # Pass rcpt explicitly in addition to -t for clarity
        verbose = str(os.getenv("MAIL_VERBOSE", "")).strip().lower() in {"1", "true", "yes", "on"}
        cmd = [sendmail, "-oi", "-t"]
        if verbose:
            cmd.append("-v")
        cmd += ["-f", sender]
        cmd += receivers
        proc = subprocess.run(
            cmd,
            input=msg.as_string().encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=True,
        )
        if proc.stdout:
            print(proc.stdout.decode("utf-8", errors="ignore"))
        return True
    except Exception as exc:
        print(f"[WARN] sendmail 调用失败: {exc}")
        return False


def _try_import_resend():
    try:
        import resend  # type: ignore
        return resend
    except Exception:
        return None


def try_send_via_resend(
    *,
    api_key: str,
    sender: str,
    receivers: list[str],
    subject: str,
    html: Optional[str],
    text: Optional[str],
    headers: Optional[dict] = None,
) -> Tuple[bool, str]:
    """Send via Resend HTTP API. Returns (ok, message_id_or_error)."""
    headers = headers or {}
    resend_lib = _try_import_resend()
    try:
        if resend_lib is not None:
            resend_lib.api_key = api_key
            payload: dict = {
                "from": sender,
                "to": receivers,
                "subject": subject,
            }
            if html:
                payload["html"] = html
            if text:
                payload["text"] = text
            if headers:
                payload["headers"] = headers
            resp = resend_lib.Emails.send(payload)  # type: ignore[attr-defined]
            mid = str(resp.get("id") or "")
            return (bool(mid), mid or "no-id-returned")
        else:
            # Fallback to direct HTTP call
            import requests  # type: ignore
            url = "https://api.resend.com/emails"
            data: dict = {
                "from": sender,
                "to": receivers,
                "subject": subject,
            }
            if html:
                data["html"] = html
            if text:
                data["text"] = text
            if headers:
                data["headers"] = headers
            r = requests.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                data=json.dumps(data),
                timeout=20,
            )
            if r.status_code // 100 == 2:
                try:
                    mid = str((r.json() or {}).get("id") or "")
                except Exception:
                    mid = ""
                return (True, mid or f"http {r.status_code}")
            return (False, f"http {r.status_code}: {r.text[:200]}")
    except Exception as exc:
        return (False, str(exc))


def try_send_via_sendmail_raw(subject: str, text_body: str, sender: str, receivers: list[str]) -> bool:
    sendmail = "/usr/sbin/sendmail"
    if not Path(sendmail).exists():
        return False
    try:
        verbose = str(os.getenv("MAIL_VERBOSE", "")).strip().lower() in {"1", "true", "yes", "on"}
        # Build minimal headers similar to the manual heredoc example,
        # but declare UTF-8 to avoid garbled non-ASCII content.
        headers = [
            f"From: {sender}",
            f"To: {', '.join(receivers)}",
            str("Subject: " + str(Header(subject, "utf-8"))),
            "MIME-Version: 1.0",
            'Content-Type: text/plain; charset="utf-8"',
            "",
        ]
        raw = "\n".join(headers) + text_body
        cmd = [sendmail, "-oi", "-f", sender]
        if verbose:
            cmd.append("-v")
        cmd += receivers
        proc = subprocess.run(
            cmd,
            input=raw.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=True,
        )
        if proc.stdout:
            print(proc.stdout.decode("utf-8", errors="ignore"))
        return True
    except Exception as exc:
        print(f"[WARN] sendmail(raw) 发送失败: {exc}")
        return False


def main() -> None:
    args = parse_args()
    # Allow MAIL_FROM to override sender if provided (aligns with backend config docs)
    env_mail_from = (os.getenv("MAIL_FROM") or "").strip()
    if env_mail_from:
        args.sender = env_mail_from  # type: ignore[attr-defined]
    # Allow env to force plain-only and dump path without changing DB/CLI
    if (os.getenv("MAIL_PLAIN_ONLY") or "").strip().lower() in {"1", "true", "yes", "on"}:
        args.plain_only = True  # type: ignore[attr-defined]
    env_dump = (os.getenv("MAIL_DUMP_MSG") or "").strip()
    if env_dump and not getattr(args, "dump_msg", ""):
        args.dump_msg = env_dump  # type: ignore[attr-defined]
    html_path = Path(args.html)
    if not html_path.exists():
        raise SystemExit(f"HTML 文件不存在: {html_path}")

    # Default subject/receivers; allow DB to override when PIPELINE_ID present
    subject = args.subject.strip()
    sender = args.sender.strip()
    receivers = [addr.strip() for addr in args.to.split(",") if addr.strip()]
    frontend_base = (os.getenv("FRONTEND_BASE_URL") or "").strip()

    pid = _env_pipeline_id()
    # Locate db at repo data/info.db by walking relative paths (this script lives in news-collector/deliver)
    repo_root = Path(__file__).resolve().parents[2]
    db_path = repo_root / "data" / "info.db"
    if pid is not None and db_path.exists():
        email_addr, subject_tpl = _load_email_delivery_from_db(db_path, pid)
        if email_addr:
            receivers = [email_addr]
        # When DB present, always use its subject template if available
        if subject_tpl:
            subject = _render_subject_from_tpl(subject_tpl)
    if not subject:
        date_zh = datetime.now().strftime("%Y年%m月%d日")
        subject = f"整合{date_zh}"

    body = html_path.read_text(encoding="utf-8")
    manage_url: str | None = None
    unsubscribe_url: str | None = None
    if receivers:
        manage_url, unsubscribe_url = _build_frontend_links(frontend_base, receivers[0], pid)

    # Build multipart/alternative to improve deliverability
    # Convert HTML to readable wrapped text (lines <= 78 chars, paragraphs preserved)
    def _html_to_wrapped_text(html: str, width: int = 78, cap: int = 8000) -> str:
        try:
            # Minimal list mode: extract titles + links for readability in plain text
            if (os.getenv("MAIL_PLAIN_MINI") or os.getenv("MAIL_PLAIN_STYLE") == "list"):
                items = re.findall(r"(?is)<a[^>]*class=\"[^\"]*article-title[^\"]*\"[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>", html)
                lines = []
                for href, raw_title in items:
                    title = re.sub(r"<[^>]+>", "", raw_title)
                    title = htmllib.unescape(title).strip()
                    href = htmllib.unescape(href).strip()
                    if not title:
                        continue
                    if len(title) > width:
                        title = title[: width - 1] + "…"
                    lines.append(f"• {title}\n  {href}")
                out = "\n".join(lines).strip()
                if cap > 0 and len(out) > cap:
                    out = out[:cap] + "\n..."
                return out or "(digest content)"

            x = html
            # Normalize line breaks for common block elements
            x = re.sub(r"(?i)<br\s*/?>", "\n", x)
            x = re.sub(r"(?i)</(p|div|section|article|h[1-6]|tr)>", "\n", x)
            # Bullet points
            x = re.sub(r"(?i)<li[^>]*>", "\n- ", x)
            x = re.sub(r"(?i)</li>", "\n", x)
            # Remove scripts/styles
            x = re.sub(r"(?is)<script.*?</script>", " ", x)
            x = re.sub(r"(?is)<style.*?</style>", " ", x)
            # Strip remaining tags
            x = re.sub(r"<[^>]+>", " ", x)
            x = htmllib.unescape(x)
            # Collapse spaces but keep newlines
            x = re.sub(r"[\t\x0b\x0c\r ]+", " ", x)
            # Normalize multiple blank lines
            x = re.sub(r"\n{3,}", "\n\n", x)
            # Split paragraphs by blank lines, wrap each
            parts = [p.strip() for p in x.split("\n\n")]
            wrapped = []
            for p in parts:
                if not p:
                    continue
                wrapped.append(textwrap.fill(p, width=width, break_long_words=False, replace_whitespace=False))
            out = "\n\n".join(wrapped).strip()
            # Cap to avoid oversized plain body
            if cap > 0 and len(out) > cap:
                out = out[:cap] + "\n..."
            return out or "(digest content)"
        except Exception:
            return "(digest content)"

    text_fallback = _html_to_wrapped_text(body)
    footer_lines: list[str] = []
    if unsubscribe_url:
        footer_lines.append(f"退订：{unsubscribe_url}")
    if manage_url:
        footer_lines.append(f"管理：{manage_url}")
    if footer_lines:
        text_fallback = f"{text_fallback}\n\n" + "\n".join(footer_lines)

    # Build message according to mode
    if args.plain_only:
        msg = MIMEText(text_fallback, "plain", "utf-8")
        # Optionally prefer quoted-printable for plain text to avoid base64-only bodies
        # Some providers score large base64 text bodies more harshly.
        if (os.getenv("MAIL_PLAIN_QP") or "").strip().lower() in {"1", "true", "yes", "on"}:
            try:
                del msg["Content-Transfer-Encoding"]
            except Exception:
                pass
            _encoders.encode_quopri(msg)
    else:
        msg = MIMEMultipart("alternative")
        # Attach text then HTML
        msg.attach(MIMEText(text_fallback, "plain", "utf-8"))
        msg.attach(MIMEText(body, "html", "utf-8"))

    # Common headers
    msg["From"] = sender
    msg["To"] = ", ".join(receivers)
    msg["Subject"] = Header(subject, "utf-8")
    try:
        msg["Date"] = formatdate(localtime=True)
        domain = sender.split("@", 1)[1] if "@" in sender else None
        msg["Message-ID"] = make_msgid(domain=domain)
    except Exception:
        pass
    list_unsub_url = ""
    try:
        lu_env = (os.getenv("MAIL_LIST_UNSUBSCRIBE") or "").strip()
        list_unsub_url = lu_env or unsubscribe_url or ""
        if list_unsub_url:
            msg["List-Unsubscribe"] = f"<{list_unsub_url}>"
            if list_unsub_url.startswith("http"):
                msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    except Exception:
        pass

    dump_path = (args.dump_msg or "").strip()
    if dump_path:
        Path(dump_path).write_text(msg.as_string(), encoding="utf-8")
        print(f"[DEBUG] dump message to: {dump_path}")

    if args.dry_run:
        print(f"[DRY-RUN] subject={subject} from={sender} to={receivers} bytes={len(body)}")
        return

    # Allow env to toggle raw sendmail mode without changing CLI
    if (os.getenv("MAIL_SENDMAIL_RAW") or "").strip().lower() in {"1", "true", "yes", "on"}:
        args.sendmail_raw = True  # type: ignore[attr-defined]

    # Resend-only: ignore legacy transport toggles; keep verbose for debug prints
    verbose = str(os.getenv("MAIL_VERBOSE", "")).strip().lower() in {"1", "true", "yes", "on"}

    if verbose:
        print(f"[DEBUG] preparing mail: from={sender} to={receivers} subject={subject}")

    # Resend as the sole transport (required)
    resend_api = (os.getenv("RESEND_API_KEY") or "").strip()
    if not resend_api:
        raise SystemExit("发送失败：缺少 RESEND_API_KEY 环境变量")

    extra_headers: dict = {}
    if list_unsub_url:
        extra_headers["List-Unsubscribe"] = f"<{list_unsub_url}>"
        if list_unsub_url.startswith("http"):
            extra_headers["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

    html_payload: Optional[str] = None if args.plain_only else body
    text_payload: Optional[str] = text_fallback
    sender_for_resend = (os.getenv("RESEND_FROM") or sender).strip()
    ok, info = try_send_via_resend(
        api_key=resend_api,
        sender=sender_for_resend,
        receivers=receivers,
        subject=subject,
        html=html_payload,
        text=text_payload,
        headers=extra_headers or None,
    )
    if ok:
        print(f"邮件已发送: {html_path} -> {', '.join(receivers)} (Resend id={info})")
        return
    raise SystemExit(f"发送失败：Resend 错误 {info}")


if __name__ == "__main__":
    main()
