用 Postfix (实际用的 Gmail)发邮件，发某指定的 html 文件内容
sender = "pangruitaosite@gmail.com"
receivers = ["306483372@qq.com"]


# 示例代码：
import smtplib
from email.mime.text import MIMEText
from email.header import Header

# 发件人、收件人、主题、内容
sender = "pangruitaosite@gmail.com"
receivers = ["306483372@qq.com"]
subject = "XXXX年XX月XX日整合"
body = "html的内容"

# 构造 MIME
msg = MIMEText(body, "plain", "utf-8")
msg["From"] = sender
msg["To"] = ", ".join(receivers)
msg["Subject"] = Header(subject, "utf-8")

# 连接本地 Postfix（端口 25）
with smtplib.SMTP("localhost", 25) as smtp:
    smtp.sendmail(sender, receivers, msg.as_string())

print("邮件已发送")
