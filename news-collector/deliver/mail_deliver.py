from __future__ import annotations

import argparse
import os
import smtplib
import sqlite3
import subprocess
from email.header import Header
import re
import html as htmllib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formatdate, make_msgid
from pathlib import Path
from datetime import datetime


DATE_PLACEHOLDER_VARIANTS = ("${date_zh}", "$(date_zh)", "${data_zh}", "$(data_zh)")
TS_PLACEHOLDER_VARIANTS = ("${ts}", "$(ts)")


DEFAULT_SENDER = "news@email.pangruitao.com"
DEFAULT_RECEIVERS = ["306483372@qq.com"]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Send an HTML file via SMTP or local sendmail")
    p.add_argument("--html", required=True, help="Path to the HTML file to send")
    p.add_argument("--subject", default="", help="Mail subject; default is 整合YYYY年MM月DD日")
    p.add_argument("--sender", default=DEFAULT_SENDER, help="Sender email address")
    p.add_argument("--to", default=",".join(DEFAULT_RECEIVERS), help="Comma-separated recipient addresses")
    p.add_argument("--dump-msg", default="", help="Optional path to dump RFC822 message for debugging")

    # Optional SMTP overrides (otherwise tries localhost:25, then sendmail)
    p.add_argument("--smtp-host", default=os.getenv("SMTP_HOST", ""))
    p.add_argument("--smtp-port", type=int, default=int(os.getenv("SMTP_PORT", "0") or 0))
    p.add_argument("--smtp-user", default=os.getenv("SMTP_USER", ""))
    p.add_argument("--smtp-pass", default=os.getenv("SMTP_PASS", ""))
    p.add_argument("--smtp-use-ssl", action="store_true", default=os.getenv("SMTP_USE_SSL", "false").lower() == "true")
    p.add_argument("--smtp-use-tls", action="store_true", default=os.getenv("SMTP_USE_TLS", "false").lower() == "true")
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
            s.sendmail(sender, receivers, msg.as_string())
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


def main() -> None:
    args = parse_args()
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

    # Build multipart/alternative to improve deliverability
    # Basic text fallback: strip scripts/styles/tags and condense whitespace
    try:
        tmp = re.sub(r"<script[\s\S]*?</script>", " ", body, flags=re.IGNORECASE)
        tmp = re.sub(r"<style[\s\S]*?</style>", " ", tmp, flags=re.IGNORECASE)
        tmp = re.sub(r"<[^>]+>", " ", tmp)
        tmp = htmllib.unescape(tmp)
        tmp = re.sub(r"\s+", " ", tmp).strip()
        # cap to a reasonable length to avoid huge plain text part
        text_fallback = tmp[:4000]
    except Exception:
        text_fallback = "(digest content)"

    # Build message according to mode
    if args.plain_only:
        msg = MIMEText(text_fallback, "plain", "utf-8")
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
    try:
        lu = (os.getenv("MAIL_LIST_UNSUBSCRIBE") or "").strip()
        if lu:
            msg["List-Unsubscribe"] = f"<{lu}>"
            if lu.startswith("http"):
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

    force_smtp = (os.getenv("MAIL_FORCE_SMTP") or "").strip().lower() in {"1", "true", "yes", "on"}
    # 1) Prefer local sendmail unless explicitly forcing SMTP
    if not force_smtp:
        if try_send_via_sendmail(msg, sender, receivers):
            print(f"邮件已发送: {html_path} -> {', '.join(receivers)} (sendmail)")
            return

    # 2) Next: explicit SMTP if provided via args/env
    if args.smtp_host:
        if try_send_via_smtp(
            msg,
            sender,
            receivers,
            host=args.smtp_host,
            port=args.smtp_port,
            user=args.smtp_user,
            password=args.smtp_pass,
            use_ssl=args.smtp_use_ssl,
            use_tls=args.smtp_use_tls,
        ):
            print(f"邮件已发送: {html_path} -> {', '.join(receivers)} (SMTP {args.smtp_host})")
            return

    # 3) Finally: localhost SMTP (try IPv4 then hostname)
    for host in ("127.0.0.1", "localhost"):
        if try_send_via_smtp(msg, sender, receivers, host=host, port=25):
            print(f"邮件已发送: {html_path} -> {', '.join(receivers)} (SMTP {host})")
            return

    # No method worked

    raise SystemExit("发送失败：SMTP 与 sendmail 均不可用")


if __name__ == "__main__":
    main()
