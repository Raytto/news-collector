# 飞书机器人发送群消息规范（spec）

## 目标
- 一个 `deliver` 脚本，可通过飞书自建应用（机器人）向其所在的指定群聊发送文本、Markdown 卡片或富文本 post。

## 环境与配置
- 在 `environment.yml` 的 `variables:` 节下声明以下环境变量（仅示例，不要提交真实密钥）：
  - `FEISHU_APP_ID`: 飞书自建应用的 App ID
  - `FEISHU_APP_SECRET`: 飞书自建应用的 App Secret
  - `FEISHU_DEFAULT_CHAT_ID`: 可选，默认消息接收的群 `chat_id`（机器人必须已在该群中）
  - 可选：`FEISHU_API_BASE=https://open.feishu.cn`（如需自定义域）

示例（片段）：

```
variables:
  FEISHU_APP_ID: "cli_xxx"
  FEISHU_APP_SECRET: "xxx"
  FEISHU_DEFAULT_CHAT_ID: "oc_xxx"
  FEISHU_API_BASE: "https://open.feishu.cn"
```

## 脚本位置与名称
- 文件：`news-collector/deliver/feishu_deliver.py`
- 作用：
  1) 读取环境变量，申请 `tenant_access_token`；
  2) 向指定 `chat_id` 发送文本/卡片/富文本；
  3) 支持按名称解析群、列出群；
  4) 返回并打印调用结果（成功/失败与错误信息）。

## API 说明（简化）
1) 获取租户访问令牌（Internal App）：
   - `POST /open-apis/auth/v3/tenant_access_token/internal`
   - 请求体：`{"app_id":"{FEISHU_APP_ID}","app_secret":"{FEISHU_APP_SECRET}"}`
   - 响应：`{"code":0,"tenant_access_token":"...","expire":7200}`

2) 发送消息到群（机器人已经在群内）：
   - `POST /open-apis/im/v1/messages?receive_id_type=chat_id`
   - Header：`Authorization: Bearer {tenant_access_token}`
   - 请求体：
     ```json
     {
       "receive_id": "{chat_id}",
       "msg_type": "text",
       "content": "{\"text\":\"test\"}"
     }
     ```
   - 成功：响应 `code=0`，并包含 message 对象。

参考文档：飞书开放平台「身份验证」与「消息与群组-发送消息」。

## CLI 交互与参数
- 主要参数：
  - `--chat-id`：显式指定群 `chat_id`；未提供则使用 `FEISHU_DEFAULT_CHAT_ID`。
  - `--chat-name`：按群名称解析 `chat_id`（需要 `im:chat:readonly` 权限）。
  - `--list-chats`：列出机器人可见的群（名称与 `chat_id`）。
  - `--text`：发送的文本内容（默认 `test`）。
  - `--file`：从文件读取要发送的文本（与 `--as-card/--as-post` 配合发送 Markdown）。
  - `--as-card`：以交互卡片发送（卡片 `markdown` 元素）。
  - `--as-post`：以富文本 post（聊天气泡）发送。
  - `--title`：卡片/富文本的标题。
  - `--to-all`：向所有机器人所在的群群发（需要 `im:chat:readonly`）。
  - `--dry-run`：仅打印待发送内容，不真正调用 API。

示例：
- 使用默认群并发送 `test`：
  `python news-collector/deliver/feishu_deliver.py`
- 指定群并自定义内容：
  `python news-collector/deliver/feishu_deliver.py --chat-id oc_123 --text "hello from bot"`
- 按名称解析并发送 Markdown 卡片：
  `python news-collector/deliver/feishu_deliver.py --chat-name "日报群" --file data/output/test.md --as-card --title "今日推荐"`

## 错误处理
- 缺少必要环境变量时退出并打印中英文可读提示。
- HTTP 调用失败（非 2xx 或 `code != 0`）时打印报错并返回非零退出码。
- 为防误发，`--dry-run` 模式不触发任何网络请求，仅展示将要发送的参数。

## 依赖与实现建议
- 依赖已有 `requests`（已在 `requirements.txt` 中）。
- 请求超时建议 `10s`，无需重试（首版简单实现）。
- 采用纯函数结构：`get_token() -> str`、`send_text(chat_id, text, token) -> dict`，便于后续复用和单测。

## 最小可用流程（MVP）
1) 管理员在飞书开放平台创建自建应用，启用机器人能力并将机器人拉进目标群；记录 `App ID / App Secret / chat_id`。
2) 在本仓库环境变量中设置上述值并激活环境。
3) 运行脚本，看到控制台输出成功信息并在群里收到 `test`。

## 后续扩展（非本次范围）
- 支持发送富文本/卡片消息；
- 支持从本地 HTML（日报）转为消息卡片摘要；
- 与现有自动任务脚本集成（收集 → 评估 → 生成 → 飞书通知）。
