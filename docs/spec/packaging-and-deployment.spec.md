# 生产打包与部署方案（SPEC）

本规范为“News Collector / 情报鸭”提供一套可复现、可回滚的生产打包与部署方案，覆盖后端 API、前端管理界面以及每日自动管线任务，基于当前代码结构与现有文档（如 `README.md`、`docs/nginx-guide.md`、`docs/pipelines-guide.md`）。

## 1. 目标与非目标

### 1.1 目标
- 统一一套“打包 → 上线 → 回滚”流程，避免手工 ssh + tmux/screen 杂项操作。
- 清晰拆分三类组件：
  - Python 后端 API（FastAPI + Uvicorn）。
  - 管线 CLI/定时任务（collector/evaluator/pipeline_runner）。
  - 前端 SPA（Vite React，静态文件）。
- 在一台 Linux 服务器上，通过 systemd 管理所有长期进程，nginx 提供静态站点 + 反向代理。
- 配置/密钥统一使用环境变量和 `.env`/`EnvironmentFile` 管理，不再依赖 Conda `variables`。
- 部署可回滚：每次发布生成独立 release 目录，回滚仅需切换符号链接并重启服务。

### 1.2 非目标
- 不引入 Docker/Kubernetes 等容器编排（后续可在本方案基础上封装）。
- 不设计多机水平扩展与负载均衡，假设单机部署（可带公网 IP）。
- 不改变业务代码行为，只定义“如何上线现有系统”。

## 2. 运行环境与目录布局

### 2.1 运行环境假设
- OS：Ubuntu 22.04 LTS（或同级 Linux）。
- 系统用户：
  - 应用用户：`news`（无登录 shell），运行 backend 与 pipelines。
  - Web 用户：`www-data`（nginx 默认）。
- 运行时：
  - Python 3.11（系统包或 `pyenv`，不依赖 Conda）。
  - Node.js LTS 20.x（仅用于构建前端，运行时无需 Node）。
  - nginx ≥ 1.18（反向代理与静态资源）。
  - SQLite 3（随系统提供）。

### 2.2 目录布局（生产机）

约定统一的根目录前缀：

- 应用根：`/opt/news-collector`
  - 当前生效版本：`/opt/news-collector/current` → 指向某个 release（符号链接）。
  - 可回滚版本：`/opt/news-collector/releases/<yyyyMMdd-HHmmss>/`。
- 数据根（持久化）：`/var/lib/news-collector`
  - SQLite：`/var/lib/news-collector/data/info.db`
  - 输出：`/var/lib/news-collector/data/output/`（pipeline 输出 HTML/Markdown）。
- 日志根：
  - 应用日志：`/var/log/news-collector/*.log`
  - nginx 日志：`/var/log/nginx/*`
- 前端发布目录（静态）：`/var/www/agentduck`（参考 `docs/nginx-guide.md`）。
- 配置与密钥：
  - 系统级配置：`/etc/news-collector/news-collector.env`（systemd `EnvironmentFile=`）。
  - 临时/开发：repo 根 `.env`（仅在 `scripts/start-backend.sh` 开发环境中使用）。

### 2.3 release 目录结构

每次打包生成的 release（示例：`/opt/news-collector/releases/20251118-120000/`）包含：

- `backend/`：FastAPI 应用（`backend/main.py`、`backend/db.py`）。
- `news-collector/`：采集、评价、写作与投递脚本。
- `frontend/`：
  - `dist/`：经 `npm run build` 生成的静态 SPA。
- `scripts/`：运维脚本（`auto-pipelines-930.sh`、`start-backend.sh` 等）。
- `docs/`：文档及 SPEC（便于排障时查阅）。
- `requirements.txt`、`backend/requirements.txt`。
- `scripts/migrations/`：DB 迁移脚本。
- `build-info.json`：本次打包的元信息（版本号、git commit、构建时间）。

不包含：
- `.venv/`、`node_modules/`、`frontend/dev` 产物等（在目标机重新安装依赖）。
- 开发日志文件（如 `backend.dev.log`、`frontend.dev.log`）。

## 3. 配置与密钥管理

### 3.1 配置来源优先级

生产环境配置统一以“环境变量”为真相，按优先级生效：

1. systemd `EnvironmentFile=/etc/news-collector/news-collector.env`。
2. systemd unit 中的 `Environment=KEY=VALUE`。
3. 进程启动前 shell 导出的环境变量。

开发环境可继续使用 `.env`，由 `scripts/start-backend.sh` 读取。

### 3.2 关键环境变量

与现有代码/文档对齐的关键变量：

- AI 评价：
  - `AI_API_BASE_URL`、`AI_API_MODEL`、`AI_API_KEY`
  - `AI_API_TIMEOUT`、`AI_REQUEST_INTERVAL`、`AI_SCORE_WEIGHTS`
- 飞书：
  - `FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`FEISHU_DEFAULT_CHAT_ID`、`FEISHU_API_BASE`
- 邮件投递（与 `backend/main.py` / `deliver/mail_deliver.py` 一致）：
  - `SMTP_HOST`、`SMTP_PORT`、`SMTP_USER`、`SMTP_PASS`
  - `SMTP_USE_SSL` / `SMTP_USE_TLS`（`1/true/yes/on` 为真）
  - `MAIL_FROM`、`MAIL_SUBJECT_PREFIX`
- Auth & 会话（见 `backend/main.py`）：
  - `AUTH_SESSION_DAYS`（默认 30）、`AUTH_COOKIE_SECURE`（生产必须为 `1`）
  - `AUTH_CODE_TTL_MINUTES`、`AUTH_CODE_LENGTH`、`AUTH_CODE_COOLDOWN_SECONDS`
  - `AUTH_CODE_MAX_ATTEMPTS`、`AUTH_HOURLY_PER_EMAIL`、`AUTH_DAILY_PER_EMAIL`、`AUTH_HOURLY_PER_IP`
  - `AUTH_CODE_PEPPER`（必须为充分随机的秘密字符串，不能使用默认值）
- 采集限速与并发（见 `README.md`）：
  - `COLLECTOR_SOURCE_CONCURRENCY`、`COLLECTOR_PER_SOURCE_CONCURRENCY`
  - `COLLECTOR_GLOBAL_HTTP_CONCURRENCY`
  - `COLLECTOR_PER_HOST_MIN_INTERVAL_MS`
  - `COLLECTOR_TIMEOUT_CONNECT`、`COLLECTOR_TIMEOUT_READ`
  - `COLLECTOR_RETRY_MAX`、`COLLECTOR_RETRY_BACKOFF_BASE`
  - `COLLECTOR_DISABLE_CONCURRENCY`
- 其他：
  - `NODE_OPTIONS="--max-old-space-size=512"`（前端构建时可用）。
  - `PIPELINE_TZ`（若未来按时区控制 Runner，可预留）。

敏感信息（API Key、SMTP 密码、pepper 等）只允许出现在：
- 本地 `.env`（开发机，不提交到 Git）。
- 生产机 `/etc/news-collector/news-collector.env`（严格权限 600，owner 为 `news` 或 root）。

## 4. 打包流程（构建产物）

打包既可以在 CI 上完成，也可以在目标机上执行。以下假设在构建机上操作且有 Git 仓库。

### 4.1 前置步骤

1. 检出目标版本：
   - `git checkout <tag-or-branch>`
2. 可选：运行基础检查：
   - `python -m venv .venv && source .venv/bin/activate`
   - `pip install -r requirements.txt`
   - （如需要）运行关键单测或集成测试。

### 4.2 构建前端 SPA

在 `frontend/` 下：

1. 安装依赖（首次或依赖变更）：
   - `npm ci`（优先）或 `npm install`
2. 设置 API 基础路径：
   - 默认：`VITE_API_BASE=/api`（由 nginx 反向代理到 backend）。
   - 如部署在前缀 `/agentduck` 下，设置：
     - `VITE_API_BASE=/agentduck/api`
3. 打包：
   - `npm run build` → 产出 `frontend/dist/`
4. 可选：本地预览：
   - `npm run preview -- --port 5173`

### 4.3 Python 依赖准备（用于验证）

在 repo 根：

- `python -m venv .venv && source .venv/bin/activate`
- `pip install -r requirements.txt`
- `pip install -r backend/requirements.txt`

（生产机会重新创建 venv，本步骤仅用于构建机验证代码可运行。）

### 4.4 生成 build-info.json

在 repo 根生成一份元信息文件（脚本可后续实现）：

```jsonc
{
  "version": "v0.3.0",
  "git_commit": "abcdef1234567890",
  "built_at": "2025-11-18T12:00:00+08:00",
  "builder": "user@host"
}
```

### 4.5 打包 tarball

在 repo 根执行（伪命令，实际可写为 `scripts/build-release.sh`）：

```bash
TS="$(date +%Y%m%d-%H%M%S)"
NAME="news-collector-$TS.tar.gz"
tar -czf "$NAME" \
  backend \
  news-collector \
  frontend/dist \
  scripts \
  docs \
  requirements.txt \
  backend/requirements.txt \
  scripts/migrations \
  build-info.json
```

产出的 `news-collector-<timestamp>.tar.gz` 即为单次发布的打包文件。

## 5. 部署流程（生产机）

### 5.1 一次性初始化

仅在首次部署时执行：

1. 创建系统用户与目录：

```bash
sudo useradd --system --home /opt/news-collector --shell /usr/sbin/nologin news || true
sudo mkdir -p /opt/news-collector/releases /var/lib/news-collector/data /var/log/news-collector
sudo chown -R news:news /opt/news-collector /var/lib/news-collector /var/log/news-collector
sudo mkdir -p /etc/news-collector
sudo touch /etc/news-collector/news-collector.env
sudo chown news:news /etc/news-collector/news-collector.env
sudo chmod 600 /etc/news-collector/news-collector.env
```

2. 安装基础软件：
   - `sudo apt install python3.11 python3.11-venv nginx`（Node 可用官方二进制或 nvm）。

3. 初始化 SQLite：
   - 若已有历史 `data/info.db`，迁移/拷贝到 `/var/lib/news-collector/data/info.db`。
   - 若从空库开始，可交由后端 `db.ensure_db()` 与管线初始化脚本创建。

### 5.2 解包到 release 目录

假设已将 tarball 上传到 `/opt/news-collector/`：

```bash
cd /opt/news-collector
TS=20251118-120000   # 与打包时保持一致
REL_DIR="/opt/news-collector/releases/$TS"
sudo mkdir -p "$REL_DIR"
sudo tar -xzf news-collector-$TS.tar.gz -C "$REL_DIR"
sudo chown -R news:news "$REL_DIR"
```

建立/更新 `current` 符号链接：

```bash
sudo ln -sfn "$REL_DIR" /opt/news-collector/current
sudo chown -h news:news /opt/news-collector/current
```

### 5.3 在生产机创建 venv 与安装依赖

以 `news` 用户运行：

```bash
sudo -u news bash -lc '
  cd /opt/news-collector/current
  python3.11 -m venv venv
  source venv/bin/activate
  pip install --upgrade pip
  pip install -r requirements.txt
  pip install -r backend/requirements.txt
'
```

venv 位置约定为：`/opt/news-collector/current/venv`。

### 5.4 配置环境变量文件

编辑 `/etc/news-collector/news-collector.env`，示例：

```dotenv
AI_API_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
AI_API_MODEL=qwen-flash
AI_API_KEY=***redacted***

FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=***redacted***
FEISHU_DEFAULT_CHAT_ID=oc_xxx

SMTP_HOST=smtp.xxx.com
SMTP_PORT=465
SMTP_USER=news@xxx.com
SMTP_PASS=***redacted***
SMTP_USE_SSL=1
MAIL_FROM="情报鸭 <news@xxx.com>"

AUTH_COOKIE_SECURE=1
AUTH_CODE_PEPPER=***random-secret***

COLLECTOR_SOURCE_CONCURRENCY=10
COLLECTOR_PER_SOURCE_CONCURRENCY=1
COLLECTOR_GLOBAL_HTTP_CONCURRENCY=16
COLLECTOR_PER_HOST_MIN_INTERVAL_MS=500
```

确保文件权限为 600，避免其他用户读取。

### 5.5 数据库初始化与迁移

1. 初始化基础 schema（`backend/db.py.ensure_db()` 会在 Uvicorn 启动时自动调用）。
2. 对于 `scripts/migrations/` 中的幂等迁移脚本，可在发布后统一执行，例如：

```bash
sudo -u news bash -lc '
  cd /opt/news-collector/current
  source venv/bin/activate
  python scripts/migrations/202410_add_writer_limits.py --db /var/lib/news-collector/data/info.db || true
  python scripts/migrations/202510_ai_metrics_refactor.py --db /var/lib/news-collector/data/info.db || true
  python scripts/migrations/202511_ai_review_text_expansion.py --db /var/lib/news-collector/data/info.db || true
  python scripts/migrations/202511_fix_fk_pipelines_ref.py --db /var/lib/news-collector/data/info.db || true
  python scripts/migrations/202511_fix_pipeline_uniques.py --db /var/lib/news-collector/data/info.db || true
'
```

全部迁移脚本应设计为“可重复执行且幂等”，失败时记录日志并人工处理。

## 6. 后端与定时任务的 systemd 管理

### 6.1 Uvicorn 后端服务

unit 文件：`/etc/systemd/system/news-collector-backend.service`：

```ini
[Unit]
Description=News Collector Backend (FastAPI)
After=network.target

[Service]
User=news
Group=news
WorkingDirectory=/opt/news-collector/current
EnvironmentFile=/etc/news-collector/news-collector.env
ExecStart=/opt/news-collector/current/venv/bin/python -m uvicorn backend.main:app \
  --host 127.0.0.1 --port 8000 --workers 2
Restart=always
RestartSec=5
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
```

注意：
- 生产环境不使用 `--reload`，避免热重载影响稳定性。
- 仅监听 127.0.0.1（由 nginx 反向代理访问，不直接暴露公网）。

启用服务：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now news-collector-backend
```

### 6.2 每日自动管线任务

依托现有 `scripts/auto-pipelines-930.sh`（内部已经实现“每天 09:30 北京时间循环执行”），将其作为长期运行服务交给 systemd 管理。

unit 文件：`/etc/systemd/system/news-collector-pipelines.service`：

```ini
[Unit]
Description=News Collector Daily Pipelines (09:30 Beijing loop)
After=network.target

[Service]
User=news
Group=news
WorkingDirectory=/opt/news-collector/current
EnvironmentFile=/etc/news-collector/news-collector.env
ExecStart=/opt/news-collector/current/venv/bin/python -m bash -lc './scripts/auto-pipelines-930.sh'
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

（实现时应将 `ExecStart` 调整为直接调用 `bash`，例如 `ExecStart=/usr/bin/bash ./scripts/auto-pipelines-930.sh`。）

启用服务：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now news-collector-pipelines
```

简化版策略：
- 若后续不希望脚本内部 `while true` 轮询，也可引入 systemd timer 或 cron，每天 09:30 调用 `scripts/auto-pipelines-once.sh`；本 SPEC 以现有 `auto-pipelines-930.sh` 为基础。

## 7. 前端发布与 nginx 配置

### 7.1 前端静态文件发布

参考 `docs/nginx-guide.md`，将 `frontend/dist/` 发布到 `/var/www/agentduck`：

```bash
sudo mkdir -p /var/www/agentduck
sudo rsync -av --delete /opt/news-collector/current/frontend/dist/ /var/www/agentduck/
sudo chown -R www-data:www-data /var/www/agentduck
```

发布时仅需重新执行一次 `rsync`。

### 7.2 nginx 静态站点 + API 反向代理

在现有 TLS server block（如 `/etc/nginx/sites-available/ganghaofan`）中追加：

```nginx
    # AgentDuck frontend (所有前端路径前缀 /agentduck)
    location = /agentduck {
        return 301 /agentduck/;
    }

    # 前端静态资源
    location ^~ /agentduck/assets/ {
        alias /var/www/agentduck/assets/;
        try_files $uri $uri/ =404;
        access_log off;
        add_header Cache-Control "public, max-age=31536000, immutable";
    }

    # SPA HTML + 路由回退
    location ^~ /agentduck/ {
        alias /var/www/agentduck/;
        index index.html;
        try_files $uri $uri/ /agentduck/index.html;
        add_header Cache-Control "no-store";
    }

    # Backend API 反向代理（前端 VITE_API_BASE=/agentduck/api）
    location ^~ /agentduck/api/ {
        proxy_pass http://127.0.0.1:8000/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 600;
        proxy_send_timeout 600;
    }

    # 健康检查
    location = /agentduck/healthz {
        return 200 "ok\n";
        add_header Content-Type text/plain;
    }
```

验证并重载：

```bash
sudo nginx -t
sudo systemctl reload nginx
```

若希望完全不暴露后台 API 到公网，可：
- 把上述 `/agentduck/api/` 段移除，仅保留静态前端（需要前端使用内网地址访问 API）。
- 或在 nginx 上增加 IP 白名单 / Basic Auth / WAF。

## 8. 升级与回滚流程

### 8.1 升级步骤（重复执行）

1. 打包新版本 tarball 并上传到 `/opt/news-collector/`。
2. 解包到新的 release 目录（参见 5.2）。
3. 创建/更新 venv 并安装依赖（参见 5.3）。
4. 更新 `current` 符号链接到新 release。
5. 执行 DB 迁移脚本（参见 5.5）。
6. 执行前端 `rsync` 发布（参见 7.1）。
7. 依次重启服务：

```bash
sudo systemctl restart news-collector-backend
sudo systemctl restart news-collector-pipelines
```

8. 访问 `https://<domain>/agentduck/` 与 `/agentduck/healthz` 做烟囱测试。

### 8.2 回滚策略

若新版本出现严重问题：

1. 找到上一个 release 目录（例如 `/opt/news-collector/releases/20251117-230000`）。
2. 切换 `current` 符号链接：

```bash
sudo ln -sfn /opt/news-collector/releases/20251117-230000 /opt/news-collector/current
sudo systemctl restart news-collector-backend
sudo systemctl restart news-collector-pipelines
sudo rsync -av --delete /opt/news-collector/current/frontend/dist/ /var/www/agentduck/
sudo systemctl reload nginx
```

3. 如回滚涉及 DB 结构变更，需提前设计迁移脚本的“向后兼容”或“只追加不删除”策略；否则回滚必须在数据库备份基础上进行。

## 9. 监控、日志与运维建议

- systemd 日志：
  - `journalctl -u news-collector-backend -f`
  - `journalctl -u news-collector-pipelines -f`
- 应用级日志：
  - 可在后续迭代中，将关键运行日志（采集、AI 调用、管线执行）统一写入 `/var/log/news-collector/app.log`，并通过 logrotate 轮转。
- 健康检查：
  - `GET /health`（由后端 FastAPI 提供）用于 L7 健康探测。
  - `GET /agentduck/healthz`（由 nginx 直接返回）用于简单外部探测。
- 备份：
  - 重点定期备份 `/var/lib/news-collector/data/info.db` 与管线 JSON 配置。
  - 备份前可短暂暂停 `news-collector-pipelines` 服务，避免写入冲突。

---

本 SPEC 专注于“单机 + systemd + nginx”的生产部署路径；如需演进到容器化/多机部署，可在此基础上将 release 目录结构映射到镜像构建上下文，并保留同样的配置/日志/数据目录约定，降低迁移成本。

