# 邮件验证码 SMTP 配置

后台通过 SMTP 发送登录/注册验证码邮件。若未配置可用的 SMTP，前端会显示“验证码已发送”，但实际邮件发不出去（发送在后台异步进行）。

## 需要的环境变量

- `SMTP_HOST`：SMTP 服务器主机名（如 `smtp.qq.com`、`smtp.gmail.com`、`email-smtp.us-east-1.amazonaws.com`）
- `SMTP_PORT`：端口（SSL 常用 465；TLS 常用 587）
- `SMTP_USER`：登录用户名（通常为邮箱地址）
- `SMTP_PASS`：登录密码（或服务提供商的授权码 / API Key）
- `SMTP_USE_SSL`：`true/false`（465 走 SSL）
- `SMTP_USE_TLS`：`true/false`（587 走 STARTTLS）
- `MAIL_FROM`：发件人邮箱（需与服务商配置匹配/已验证）
- `MAIL_SUBJECT_PREFIX`：主题前缀（可选，默认 `[情报鸭]`）

任一成功配置的 SMTP 会被优先使用；否则后备尝试本地 MTA（`127.0.0.1:25` 或 `sendmail`），大多服务器默认没有配置，本步骤往往失败。

## 快速配置（.env）

将以下内容写入仓库根目录的 `.env`（`scripts/start-backend.sh` 会自动加载）：

```
# 任选一种协议，QQ 邮箱示例（使用授权码）
SMTP_HOST=smtp.qq.com
SMTP_PORT=465
SMTP_USE_SSL=true
SMTP_USE_TLS=false
SMTP_USER=your_account@qq.com
SMTP_PASS=your_smtp_auth_code
MAIL_FROM=your_account@qq.com

# 可选
MAIL_SUBJECT_PREFIX=[情报鸭]
AUTH_CODE_TTL_MINUTES=10
AUTH_CODE_COOLDOWN_SECONDS=60
```

Gmail 示例（需 App Password，开启 2FA）：

```
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USE_SSL=false
SMTP_USE_TLS=true
SMTP_USER=your_account@gmail.com
SMTP_PASS=your_app_password
MAIL_FROM=your_account@gmail.com
```

AWS SES/SendGrid 等同理，按服务商给出的主机、端口、凭证填入即可。

## 诊断与验证

1) 启动后台（自动加载 `.env`）：

```
scripts/start-backend.sh
```

2) 触发一次验证码发送（登录邮箱需已存在，注册需不存在）：

```
curl -sS -X POST http://127.0.0.1:8000/auth/login/code \
  -H 'Content-Type: application/json' \
  -d '{"email":"test@example.com"}'
```

3) 查看后台输出（新增了发送结果日志）：

- 成功：`[auth] email sent to t***@example.com via SMTP ...`
- 失败：`[WARN] email NOT sent to t***@example.com ...`（请检查 `.env` 与网络）

4) 若仍未收到邮件：

- 检查垃圾邮箱/隔离。
- 确认 `MAIL_FROM` 在服务商侧已验证或域名已配置 SPF/DKIM。
- 服务器是否能连通 SMTP：`telnet smtp.qq.com 465` 或 `openssl s_client -connect smtp.gmail.com:587 -starttls smtp`。
- 避免频繁点击：接口有限流与 60 秒冷却；过快会报 429。

## 安全提示

- 不要将 `.env` 提交到版本库；仅在服务器上配置。
- 授权码/密码仅供后台使用；如需共享配置，使用变量占位并在部署环境填充。

