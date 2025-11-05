import os
import resend

# 设置 API Key（从环境变量读取）
resend.api_key = os.environ["RESEND_API_KEY"]

# 你的 HTML 内容
html_content = ""
with open("test-clean.html", "r", encoding="utf-8") as f:
    html_content = f.read()

try:
    email = resend.Emails.send({
        "from": "情报鸭 <news@news.pangruitao.com>",  # 必须是已验证域名下的地址
        "to": ["306483372@qq.com"],               # 替换为你的测试邮箱
        "subject": "最近 40 小时资讯简报",
        "html": html_content,
    })
    print("✅ 邮件已成功发送！ID:", email["id"])
except Exception as e:
    print("❌ 发送失败:", e)