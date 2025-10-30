# 用户与访问控制（SPEC）

本规范基于 docs/prompt/user-and-access-control.md 的需求，细化完整的“用户与访问控制”方案，覆盖数据结构、后端接口、前端交互、邮件验证码设计与安全策略，面向 FastAPI + SQLite + React(Ant Design) 的现有技术栈。

## 目标与非目标

- 目标
  - 支持“邮箱 + 验证码”（4 位数字）登录与注册，登录态有效期 30 天。
  - 实现管理员与普通用户两级权限；为 pipeline 增加“归属用户”。
  - 未登录访问受限：前端拦截并弹出登录对话框；后台 API 统一鉴权。
  - 提供基础的用户管理（管理员可见）：用户列表与详情（含其投递）。
  - 设计安全稳健的邮件验证码数据模型、速率限制与审计。
- 非目标
  - 暂不实现 OAuth/SSO、手机号登录、多因子认证（可留扩展位）。
  - 暂不实现密码体系（走无密码登录流）。

## 角色与权限

- 管理员（is_admin=1）
  - 访问与管理所有页面与数据。
  - 具有“用户管理”入口：用户列表与详情页。
  - 投递管理：可查看所有用户投递。
- 普通用户（is_admin=0）
  - 资讯管理：可查看全部资讯。
  - 来源管理/类别管理/AI 评估：仅查看，禁用“启用开关/编辑/删除”。
  - 投递管理：仅查看与编辑“归属于自己”的投递。

## 认证与会话

- 登录与注册均采用“邮箱 + 4 位数字验证码”。
- 会话采用“Opaque Session Token + Cookie”方案：
  - 服务端生成 256-bit 随机 token，客户端以 `HttpOnly; Secure; SameSite=Lax` Cookie `sid` 保存。
  - 服务端仅存储 token 的 `SHA-256` 哈希，避免泄漏风险。
  - 会话默认有效期 30 天，可采用“滑动过期（sliding）”策略：活跃访问刷新 `last_seen_at`，但 `expires_at` 不超过 30 天上限。

对比 JWT：当前单体架构 + 需要服务器侧强制登出与审计，DB 会话更适合，逻辑简单、撤销可控。

## 邮件验证码设计（重点）

用途与流程：
- 登录：已存在邮箱才允许请求验证码；校验通过则创建/续期会话。
- 注册：不存在的邮箱允许请求验证码；校验通过后创建用户并创建会话。

设计原则：
- 仅存储验证码哈希：`code_hash = SHA-256(code + pepper)`，`pepper` 为服务端私密常量（环境变量）。
- 单次有效、短期 TTL、错误次数上限、请求速率限制、目的隔离（purpose）。
- 支持一封邮件包含 4 位数字；长度默认 4，可参数化为 6 以增强强度。

字段与约束：见《数据库变更》中的 `auth_email_codes` 表。

安全与速率限制建议：
- 过期时间：10 分钟；最大尝试次数：5 次；重发冷却：60 秒。
- 每邮箱：每小时最多 5 次、每日最多 20 次请求（登录 + 注册合计）。
- 每 IP：每小时最多 30 次，超过则 429。
- 同一 email+purpose 同时仅允许 1 条“活动中的验证码”（未过期且未消费）。
- 验证成功后将该条标记 `consumed_at`，并使同一 purpose 其他未消费记录失效（软失效）。

可选实现：
- 若部署 Redis，可将速率限制计数放入 Redis；当前无 Redis 时，使用 SQLite 聚合 + 内存短缓存（LRU）即可。

## 后端 API 设计（FastAPI）

- Auth
  - POST `/auth/login/code`：请求登录验证码；请求体 `{ email }`。
    - 若邮箱不存在，返回 400 `邮箱不存在`（按原需求，不做枚举防护；可配置为“总是 200”以提升安全）。
  - POST `/auth/login/verify`：校验验证码；请求体 `{ email, code }`。
    - 成功：设置 `sid` Cookie，返回用户信息 `{ id, email, name, is_admin }`。
  - POST `/auth/signup/code`：请求注册验证码；请求体 `{ email, name }`。
    - 若邮箱已存在，返回 400 `邮箱已存在`。
  - POST `/auth/signup/verify`：校验并创建用户；请求体 `{ email, code, name }`。
    - 成功：创建用户、设置 `sid` Cookie，返回用户信息。
  - POST `/auth/logout`：注销当前会话，清除 Cookie 与会话记录。
  - GET `/me`：获取当前登录用户与能力边界（用于前端控制 UI）。

- 用户管理（管理员）
  - GET `/admin/users`：分页列表，支持 email/name 模糊查询、起止时间。
  - GET `/admin/users/{uid}`：用户详情与最近登录、投递统计。
  - PATCH `/admin/users/{uid}`：修改用户 `name`、`is_admin`（自我降权需二次确认）。

- Pipeline 归属
  - 在现有 `pipelines` 上新增 `owner_user_id`；现有增删改查需在鉴权中校验归属。

- 统一鉴权中间件
  - 除 `/health` 与 `/auth/*` 外，其余 API 需有效会话。
  - 在 `request.state.user` 注入已登录用户信息（`id/email/name/is_admin`）。

## 数据库变更（SQLite）

以下 DDL 以 SQLite 为准，集成入口：`backend/db.py.ensure_db()` 迁移。

### 1) 新表：`users`

```sql
CREATE TABLE IF NOT EXISTS users (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  email          TEXT NOT NULL UNIQUE,              -- 存小写规范化邮箱
  name           TEXT NOT NULL,
  is_admin       INTEGER NOT NULL DEFAULT 0,
  avatar_url     TEXT,
  created_at     TEXT DEFAULT CURRENT_TIMESTAMP,
  verified_at    TEXT,
  last_login_at  TEXT
);
```

规范：
- 保存前将邮箱 `lower().strip()`，避免大小写重复。

### 2) 新表：`user_sessions`

```sql
CREATE TABLE IF NOT EXISTS user_sessions (
  id            TEXT PRIMARY KEY,                   -- UUIDv4
  user_id       INTEGER NOT NULL,
  token_hash    TEXT NOT NULL,                      -- SHA-256(sid)
  created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
  last_seen_at  TEXT,
  expires_at    TEXT NOT NULL,
  revoked_at    TEXT,
  ip            TEXT,
  user_agent    TEXT,
  FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_user_sessions_token_hash
  ON user_sessions (token_hash);

CREATE INDEX IF NOT EXISTS idx_user_sessions_user
  ON user_sessions (user_id, expires_at);
```

### 3) 新表：`auth_email_codes`（邮件验证码记录）

```sql
CREATE TABLE IF NOT EXISTS auth_email_codes (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  email         TEXT NOT NULL,                      -- 小写规范化
  user_id       INTEGER,                            -- 登录可回填用户；注册阶段为空
  purpose       TEXT NOT NULL,                      -- 'login' | 'signup'
  code_hash     TEXT NOT NULL,                      -- SHA-256(code + pepper)
  expires_at    TEXT NOT NULL,
  consumed_at   TEXT,
  attempt_count INTEGER NOT NULL DEFAULT 0,
  max_attempts  INTEGER NOT NULL DEFAULT 5,
  resent_count  INTEGER NOT NULL DEFAULT 0,
  created_ip    TEXT,
  user_agent    TEXT,
  created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id) REFERENCES users(id)
);

-- 仅允许同一 email+purpose 存在 1 条活动中的验证码（未消费且未过期）
CREATE UNIQUE INDEX IF NOT EXISTS idx_auth_codes_active_unique
ON auth_email_codes (email, purpose)
WHERE consumed_at IS NULL AND expires_at > CURRENT_TIMESTAMP;

CREATE INDEX IF NOT EXISTS idx_auth_codes_lookup
  ON auth_email_codes (email, purpose, expires_at);
```

说明：
- 登录流程可在生成记录时将 `user_id` 回填（若存在用户），便于审计。
- 失败尝试在验证接口中自增；达到 `max_attempts` 自动判为失效（与过期一致处理）。

### 4) 表变更：`pipelines` 增加归属

```sql
ALTER TABLE pipelines ADD COLUMN owner_user_id INTEGER; -- 可为空便于平滑迁移

CREATE INDEX IF NOT EXISTS idx_pipelines_owner
  ON pipelines (owner_user_id);
```

迁移策略：
- 初始将现有 pipelines 的 `owner_user_id` 设为某个管理员（或置空，首次编辑时绑定）。

## 邮件发送与模板

- SMTP 配置（环境变量）：
  - `SMTP_HOST`、`SMTP_PORT`、`SMTP_USER`、`SMTP_PASS`、`MAIL_FROM`、`MAIL_SUBJECT_PREFIX`（可选）。
- 信件内容：
  - 主题：`[情报鸭] 验证码 {1234}（10 分钟内有效）`
  - 正文：包含验证码、有效期、用途（登录/注册）、若非本人可忽略说明、团队签名。
- 发送实现：FastAPI `BackgroundTasks` 或线程池异步发送；失败重试 3 次，记录日志。

## 验证码生成与校验

- 生成：`code = random.choices('0123456789', k=4)`；可通过配置升级为 6 位。
- 存储：仅存 `SHA-256(code + pepper)`；`pepper` 来自 `AUTH_CODE_PEPPER` 环境变量。
- 冷却与限流：
  - 重发前检查 `resent_count` 与距离上次创建/重发时间 ≥ 60s。
  - 聚合统计（SQLite）：按 `email` 与 `created_at` 落在窗口内计数；超过阈值返回 429。
- 校验流程：
  1) 查找 `email+purpose` 活动记录；若无或已过期/已消费 → 400。
  2) 对比 `SHA-256(input + pepper)` 与 `code_hash`；不一致 → `attempt_count++`，若达到上限 → 400（已失效）。
  3) 一致 → 标记 `consumed_at=now()`；对于同一 email+purpose 的其他活动记录做软失效处理。
  4) 登录：创建/续期会话；注册：创建用户后创建会话。

## 前端交互（Ant Design）

- 全局登录要求
  - 未登录访问任何业务页，统一跳出登录 Modal（支持注册 Tab）。
  - 会话过期（401）时，前端拦截器自动弹出 Modal 并引导重新登录。
- 登录/注册 UI（建议组件）
  - LoginModal：输入邮箱 → 请求验证码 → 验证码输入框（4 格/6 格分段）、倒计时、重发按钮、错误提示。
  - SignupModal：邮箱 + 用户名 → 请求验证码 → 验证 → 自动完成注册并进入系统。
- 右上角头像下拉
  - 显示昵称、“个人设置”、“退出登录”。
  - 个人设置页：仅支持“修改名字”。
- 管理入口
  - “用户管理”并列展示；列表页（表格）展示 `email/name/is_admin/created_at/last_login_at`；
  - 详情页：基本信息 + 其投递列表（可点击跳转投递详情）。
- 权限前端控制
  - 读取 `/me` 返回的 `is_admin`；在 UI 层 disable/隐藏不可用操作，后端仍做强校验。

## 安全与合规

- Cookie：`HttpOnly; Secure; SameSite=Lax; Path=/`；仅通过 HTTPS 部署。
- CSRF：对修改类接口（非 GET）要求同站请求；若未来跨站嵌入需求，可引入 CSRF Token。
- 日志与审计：
  - 记录验证码请求/验证成功/失败、登录/登出、权限拒绝（403）。
  - 脱敏：日志中不记录明文验证码或 token，仅记录哈希与 email 局部（如 `a***@b.com`）。
- 枚举风险：按需求登录请求在邮箱不存在时提示“不存在”；可通过配置开关改为“泛化提示”。
- 数据保留：
  - `auth_email_codes` 建议保留 30 天用于审计，定期清理过期与已消费的旧记录。
  - `user_sessions` 清理已过期或已撤销的会话。

## 配置项一览（env）

- `AUTH_SESSION_DAYS=30`
- `AUTH_CODE_TTL_MINUTES=10`
- `AUTH_CODE_LENGTH=4`（可改 6）
- `AUTH_CODE_COOLDOWN_SECONDS=60`
- `AUTH_CODE_MAX_ATTEMPTS=5`
- `AUTH_HOURLY_PER_EMAIL=5`
- `AUTH_DAILY_PER_EMAIL=20`
- `AUTH_HOURLY_PER_IP=30`
- `AUTH_CODE_PEPPER=...`（强随机）
- SMTP 相关：`SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASS/MAIL_FROM/MAIL_SUBJECT_PREFIX`

## 迁移与落地步骤

1) DB 迁移：新增 `users`、`user_sessions`、`auth_email_codes`，为 `pipelines` 增 `owner_user_id`。
2) 邮件发送器：基于 SMTP 实现与模板；引入 `BackgroundTasks` 异步发送。
3) Auth API：按规范添加 6 个接口；统一中间件加载用户并拦截未登录请求。
4) 权限网关：对现有资源接口按 is_admin 与 owner 校验；返回 403。
5) 前端改造：
   - 全局拦截器处理 401；登录/注册 Modal；个人设置；头像下拉；“用户管理”入口（管理员）。
   - 非管理员禁用/隐藏受限交互（来源/类别/AI 指标）。
6) 填充归属：为历史 pipelines 批量指定 `owner_user_id`（或首次编辑绑定）。
7) 清理与监控：定时任务清理过期验证码/会话；日志与告警接入。

## 开放问题与决策

- 验证码长度是否直接定为 6 位？当前按需求默认 4 位，但建议上线后改为 6 位并放宽重试策略。
- 登录邮箱不存在时的提示策略：目前按需求直返 400；若要降低枚举风险，可改为“总是 200 + 泛化提示”。
- 是否需要多端会话并存/单点登录？当前允许多端并存；如需限制，可在创建新会话时撤销旧会话。
- 是否需要管理员手动“设为管理员”的二次确认流程？建议需要（前端二次确认 + 后端强校验）。

---

以上方案可在不引入额外基础设施的前提下，满足“邮箱验证码登录/注册 + 角色权限 + 归属绑定”的核心需求，并为后续扩展（JWT、MFA、OAuth、Redis 限流）保留空间。
