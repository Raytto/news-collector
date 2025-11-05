# send_test.py
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
import os

# 配置
smtp_host = os.getenv("SMTP_HOST", "smtpdm.aliyun.com")
smtp_port = int(os.getenv("SMTP_PORT", "465"))
smtp_user = os.getenv("SMTP_USER_NEWS")
smtp_pass = os.getenv("SMTP_PASS_NEWS")
mail_from = os.getenv("MAIL_FROM_NEWS")

to_addr = "306483372@qq.com"
subject = "测试发送"

# 读取 HTML
# with open("data/output/pipeline-1/20251104-174424.html", "r", encoding="utf-8") as f:
#     html = f.read()
with open("test-clean.html", "r", encoding="utf-8") as f:
    html = f.read()

# 构造邮件
msg = MIMEMultipart("alternative")
msg["Subject"] = Header(subject, "utf-8")
msg["From"] = mail_from
msg["To"] = to_addr
msg.attach(MIMEText(html, "html", "utf-8"))

# 发送
with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
    server.login(smtp_user, smtp_pass)
    server.sendmail(mail_from, [to_addr], msg.as_string())

print("✅ 邮件已通过阿里云 SMTP 发送！")