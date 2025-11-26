# 邮件退订与管理入口植入（SPEC）

为邮件投递内容增加“退订链接”和“管理链接”，两者均基于前端访问基础链接（`FRONTEND_BASE_URL`，目前为 `https://jp.pangruitao.com`）拼装。退订链接直达后端 `/unsubscribe` 接口，管理链接跳转前端首页便于用户登录后自助管理管线。此文档为实施前的设计说明，不包含代码改动。

## 1. 范围与非目标

- 范围：`news-collector/writer/email_writer.py` 生成的 HTML 邮件主体、`news-collector/deliver/mail_deliver.py` 邮件封装（含文本兜底与 `List-Unsubscribe` 头）。使用 `PIPELINE_ID` 和 DB 中的收件人信息拼接链接。
- 非目标：不改动退订 API 行为（`GET /unsubscribe` 已存在）；不改动前端路由与鉴权流程；不引入额外 DB 表。

## 2. 需求与规则

- 退订链接
  - 基于 `FRONTEND_BASE_URL` 拼接 `/unsubscribe`，附带 Query：`email=<收件人>`（URL 编码）、`pipeline_id=<PIPELINE_ID>`（如有）、`reason=email_footer`（标记来源）。
  - 如果缺少 `FRONTEND_BASE_URL` 或收件人邮箱无法确定，则不渲染退订链接，并记录 debug 日志。
  - 链接文字建议 “退订本邮件”/“取消本订阅”，样式低干扰（小号灰色）。
- 管理链接
  - 直接跳转 `FRONTEND_BASE_URL`（末尾无需额外路径），文本可用 “管理我的订阅/管线”，提示用户登录后自助调整。
  - 即便缺少收件人信息也可以渲染管理链接；若 `FRONTEND_BASE_URL` 缺失则整块不展示。
- 邮件位置与表现
  - 在邮件正文末尾追加一个信息块（如浅灰色分隔区域），包含两行：退订链接、管理链接。
  - 文本版（plain fallback 与 `--plain-only`）在尾部追加同样的两个 URL，采用一行一条。
- 邮件头
  - 若成功生成退订 URL，设置 `List-Unsubscribe: <{unsubscribe_url}>`；同时附加 `List-Unsubscribe-Post: List-Unsubscribe=One-Click` 以便客户端一键退订。
  - 若用户已通过 env 显式设置 `MAIL_LIST_UNSUBSCRIBE`，保持其优先级（即手工配置优先，自动填充值为后备）。

## 3. 方案草案

- 新增小型工具函数（可置于 `email_writer.py`），读取 `FRONTEND_BASE_URL`，去除尾部 `/`，并用 `urllib.parse.urljoin`/`urlencode` 拼装退订与管理 URL；返回 `None` 表示缺少必需条件。
- 生成邮件 HTML 时：
  - 保留现有主体不变，仅在尾部追加 `<hr/>` 之后的 footer（小字号、灰色文案），包含两个 `a` 标签。
  - `render_html(...)` 目前已有 `unsubscribe_url` 参数但未使用，可复用并新增 `manage_url` 参数；`main()` 内从 env/DB 组装并传入。
- 文本兜底：
  - `mail_deliver.py` 在构造 plain text 时，若有 URL 就在末尾追加：
    ```
    退订：<url>
    管理：<url>
    ```
- 邮件头填充：
  - 在 `mail_deliver.py` 设置 `List-Unsubscribe` 时，若 env 未指定且计算得到退订 URL，则自动填入。
- Nginx/路由假设：`FRONTEND_BASE_URL` 所指域名需将 `/unsubscribe` 反代到 FastAPI（当前 8000），否则退订页面会 404。若未来迁移路径，可通过更新前端基础链接或在代理层重写。

## 4. 兼容性与回滚

- 未配置 `FRONTEND_BASE_URL` 时，无退订/管理文案，邮件主体保持原状；不影响发送。
- 收件人/PIPELINE_ID 缺失时，仅隐藏退订链接，管理链接仍可显示。
- 如需回滚，移除 footer 渲染与自动头部填充即可；不涉及 DB 结构与 API。

## 5. 验收要点

- HTML 邮件尾部出现两条链接，URL 形如 `https://jp.pangruitao.com/unsubscribe?email=xxx%40yy.com&pipeline_id=12&reason=email_footer` 与 `https://jp.pangruitao.com/`。
- `--plain-only` 模式下文本尾部同样包含两条可点击/可复制的链接。
- 邮件原文头部包含 `List-Unsubscribe` 与 `List-Unsubscribe-Post`（当 env 未手动覆盖且 URL 可用）。
- 当 `FRONTEND_BASE_URL` 缺失时，邮件内容与头部不出现相关链接，发送流程不中断。
