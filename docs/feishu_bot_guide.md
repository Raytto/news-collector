# 飞书机器人使用指南

本指南说明如何配置并使用仓库中的机器人脚本，将一条文本消息发送到机器人所在的群聊。

脚本位置：`news-collector/deliver/feishu_deliver.py`

## 一、准备工作

1) 飞书开放平台创建自建应用（Internal App）并启用机器人能力，将机器人拉入目标群。

2) 在应用权限中勾选（并发布/重新授权）：
- `im:message:send`（发送消息）
- `im:chat:readonly`（列出与查询群信息；用于按群名解析 chat_id 或列出群）

3) 获取应用凭据：`App ID` 与 `App Secret`。

## 二、环境变量

在 `environment.yml` 的 `variables:` 下配置（本地填入真实值）：

```
variables:
  FEISHU_APP_ID: "cli_xxx"
  FEISHU_APP_SECRET: "xxx"
  FEISHU_DEFAULT_CHAT_ID: "oc_xxx"   # 可选，默认发送目标群
  FEISHU_API_BASE: "https://open.feishu.cn" # 可选
```

加载/更新环境：

```
conda env update -n news-collector -f environment.yml
conda deactivate && conda activate news-collector
```

## 三、基础用法

- 指定 chat_id 发送文本：
```
python news-collector/deliver/feishu_deliver.py \
  --chat-id oc_xxx \
  --text "test"
```

- 使用默认 chat_id（来自 `FEISHU_DEFAULT_CHAT_ID`）发送：
```
python news-collector/deliver/feishu_deliver.py --text "test"
```

- 干跑（仅打印将要发送的内容，不发消息）：
```
python news-collector/deliver/feishu_deliver.py --chat-id oc_xxx --text "test" --dry-run
```

## 四、自动解析 chat_id

当不知道 `chat_id` 时，可通过脚本自动获取：

- 列出机器人可见的群（名称 与 chat_id）：
```
python news-collector/deliver/feishu_deliver.py --list-chats
```
  依赖权限：`im:chat:readonly`；且机器人必须已在目标群内。

- 根据群名称解析 chat_id 并发送（不区分大小写，支持包含匹配）：
```
python news-collector/deliver/feishu_deliver.py \
  --chat-name "日报群" \
  --text "test"
```

解析优先级：`--chat-id` > `--chat-name` > `FEISHU_DEFAULT_CHAT_ID`。

## 五、常见问题

- 报错“未提供 chat_id…”：
  - 需传入 `--chat-id` 或 `--chat-name`，或在环境中设置 `FEISHU_DEFAULT_CHAT_ID`。

- 列表为空或无法按名称解析：
  - 确认已勾选 `im:chat:readonly` 并发布；确认机器人已加入目标群。

- `code != 0` 或 HTTP 错误：
  - 通常为凭证/权限或网络问题；请检查 `FEISHU_APP_ID/FEISHU_APP_SECRET`、应用权限、网络出口与域名 `open.feishu.cn` 可达性。

## 六、安全与最佳实践

- 切勿将真实 App Secret 提交到仓库；仅在本地 `environment.yml`/CI 机密中配置。
- 脚本默认向群发送纯文本；后续可扩展为卡片消息或发送日报摘要链接。

## 七、与自动任务集成（可选）

- 可在 `scripts/auto-do-scripts.sh` 的流程末尾追加飞书通知步骤，用于发送“生成完成”的提示或链接。

示例（伪代码）：
```
$PYTHON news-collector/deliver/feishu_deliver.py \
  --chat-name "日报群" \
  --text "24小时汇总已生成：$out_file"
```

补充：
- 发送 Markdown 卡片：加 `--as-card --file data/output/test.md --title "今日推荐"`；
- 发送富文本 post：加 `--as-post`（对 Markdown 解析较宽松）。

---
如有需要，我可以在该脚本中加入“发送卡片消息/携带链接”的扩展能力。
