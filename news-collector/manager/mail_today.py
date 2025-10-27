from __future__ import annotations

import argparse
import os
import smtplib
import subprocess
from email.header import Header
from email.mime.text import MIMEText
from pathlib import Path
from datetime import datetime


DEFAULT_SENDER = "pangruitaosite@gmail.com"
DEFAULT_RECEIVERS = ["306483372@qq.com"]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Send an HTML file via SMTP or local sendmail")
    p.add_argument("--html", required=True, help="Path to the HTML file to send")
    p.add_argument("--subject", default="", help="Mail subject; default is YYYY年MM月DD日整合")
    p.add_argument("--sender", default=DEFAULT_SENDER, help="Sender email address")
    p.add_argument("--to", default=",".join(DEFAULT_RECEIVERS), help="Comma-separated recipient addresses")

    # Optional SMTP overrides (otherwise tries localhost:25, then sendmail)
    p.add_argument("--smtp-host", default=os.getenv("SMTP_HOST", ""))
    p.add_argument("--smtp-port", type=int, default=int(os.getenv("SMTP_PORT", "0") or 0))
    p.add_argument("--smtp-user", default=os.getenv("SMTP_USER", ""))
    p.add_argument("--smtp-pass", default=os.getenv("SMTP_PASS", ""))
    p.add_argument("--smtp-use-ssl", action="store_true", default=os.getenv("SMTP_USE_SSL", "false").lower() == "true")
    p.add_argument("--smtp-use-tls", action="store_true", default=os.getenv("SMTP_USE_TLS", "false").lower() == "true")
    p.add_argument("--dry-run", action="store_true", help="Print message metadata without sending")
    return p.parse_args()


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


def try_send_via_sendmail(msg: MIMEText, sender: str) -> bool:
    sendmail = "/usr/sbin/sendmail"
    if not Path(sendmail).exists():
        return False
    try:
        proc = subprocess.run(
            [sendmail, "-oi", "-t", "-f", sender],
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
    html_path = Path(args.html)
    if not html_path.exists():
        raise SystemExit(f"HTML 文件不存在: {html_path}")

    subject = args.subject.strip() or datetime.now().strftime("%Y年%m月%d日整合")
    sender = args.sender.strip()
    receivers = [addr.strip() for addr in args.to.split(",") if addr.strip()]

    body = html_path.read_text(encoding="utf-8")

    msg = MIMEText(body, "html", "utf-8")
    msg["From"] = sender
    msg["To"] = ", ".join(receivers)
    msg["Subject"] = Header(subject, "utf-8")

    if args.dry_run:
        print(f"[DRY-RUN] subject={subject} from={sender} to={receivers} bytes={len(body)}")
        return

    # 1) Preferred SMTP if provided via args/env
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

    # 2) Fallback to localhost SMTP (try IPv4 then hostname)
    for host in ("127.0.0.1", "localhost"):
        if try_send_via_smtp(msg, sender, receivers, host=host, port=25):
            print(f"邮件已发送: {html_path} -> {', '.join(receivers)} (SMTP {host})")
            return

    # 3) Final fallback: use local sendmail binary if available
    if try_send_via_sendmail(msg, sender):
        print(f"邮件已发送: {html_path} -> {', '.join(receivers)} (sendmail)")
        return

    raise SystemExit("发送失败：SMTP 与 sendmail 均不可用")


if __name__ == "__main__":
    main()
