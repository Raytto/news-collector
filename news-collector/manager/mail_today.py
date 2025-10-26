from __future__ import annotations

import argparse
import smtplib
from email.header import Header
from email.mime.text import MIMEText
from pathlib import Path
from datetime import datetime


DEFAULT_SENDER = "pangruitaosite@gmail.com"
DEFAULT_RECEIVERS = ["306483372@qq.com"]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Send an HTML file via local Postfix (SMTP localhost:25)")
    p.add_argument("--html", required=True, help="Path to the HTML file to send")
    p.add_argument("--subject", default="", help="Mail subject; default is YYYY年MM月DD日整合")
    p.add_argument("--sender", default=DEFAULT_SENDER, help="Sender email address")
    p.add_argument(
        "--to",
        default=",".join(DEFAULT_RECEIVERS),
        help="Comma-separated recipient addresses",
    )
    return p.parse_args()


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

    with smtplib.SMTP("localhost", 25) as smtp:
        smtp.sendmail(sender, receivers, msg.as_string())

    print(f"邮件已发送: {html_path} -> {', '.join(receivers)}")


if __name__ == "__main__":
    main()

