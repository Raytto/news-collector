# Collector 并发设计与风险评估（线程化方案）

本文评估并设计一个“每个来源一个工作线程；该线程负责该来源首页与文章详情的拉取，详情默认串行（可按需小幅并行）”的并发模型；同时设置来源并发上限（可配置，默认 10）。目标是在保证合规与稳定性的前提下提升整体吞吐。

## 结论摘要
- 采用以线程为主的并发模型：顶层用 `ThreadPoolExecutor` 按“来源”为粒度调度；每个来源线程内详情抓取默认串行，后续可在指标允许时平滑升至小并发，始终受限流约束。
- 必须引入“全局 HTTP 并发上限 + 按域名限速”的双层限流，避免总并发膨胀与被站点封禁。
- `requests` 适合 IO 型任务并发；为每个来源线程维护独立 `Session`，并配置连接池大小与超时/重试。
- 初始参数建议：来源并发上限 10；单来源详情并发 1（默认串行）；全局 HTTP 并发上限 16；按域名间隔 ≥ 500ms（含抖动）；超时（connect=5s, read=10s）；重试 3 次带指数回退。
- 默认串行已显著降低“并发叠加”风险；剩余风险集中在重试突刺、限流封禁、连接池与写入竞争等，可通过限流、连接复用、幂等写入、结构化日志与指标监控缓解。

## 设计方案

### 并发拓扑
1) 顶层“来源并发”：
   - 使用 `ThreadPoolExecutor(max_workers=SOURCE_CONCURRENCY)` 提交每个来源的抓取任务（一个来源对应一个 worker 任务）。
   - SOURCE_CONCURRENCY 默认 10，可通过环境变量 `COLLECTOR_SOURCE_CONCURRENCY` 配置。

2) 单来源内“详情抓取”（默认串行）：
   - 默认：遍历该来源文章列表，详情逐个串行拉取与解析，避免来源内并发叠加。
   - 可选（小并发，二阶段）：当监控表明未触发限流且存在明显瓶颈时，将 `PER_SOURCE_DETAIL_CONCURRENCY` 从 1 平滑升至 2 或 4；建议继续依赖全局并发信号量，避免爆发。
   - PER_SOURCE_DETAIL_CONCURRENCY 默认 1，可通过 `COLLECTOR_PER_SOURCE_CONCURRENCY` 配置。

3) 全局限流与按域名限速：
   - 全局信号量 `GLOBAL_HTTP_CONCURRENCY`（默认 16，`COLLECTOR_GLOBAL_HTTP_CONCURRENCY` 可配）限制同一时刻的 HTTP 请求总数。
   - 按域名速率限制：为每个 `hostname` 维护最近一次请求时间，强制相邻请求最小间隔（默认 500ms）并叠加抖动（±100ms），必要时参考 robots.txt 指令。

4) 连接复用与超时/重试：
   - 每个来源线程维护独立 `requests.Session()`，挂载 `HTTPAdapter(pool_connections=16, pool_maxsize=16, max_retries=<带回退>)`；避免跨线程共享同一 `Session`。
   - 统一设置请求级超时（connect=5s, read=10s），失败按 429/5xx 触发指数回退重试并限制最大次数。

5) 写入与去重：
   - 将解析结果通过线程安全队列汇聚到单一“写入协程/线程”，集中做去重与持久化，保证幂等与一致性（避免并发写冲突）。

### 关键伪代码
```python
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
import threading, time, random
import requests

SOURCE_CONCURRENCY = int(os.getenv("COLLECTOR_SOURCE_CONCURRENCY", 10))
GLOBAL_HTTP_CONCURRENCY = int(os.getenv("COLLECTOR_GLOBAL_HTTP_CONCURRENCY", 16))
PER_SOURCE_DETAIL_CONCURRENCY = int(os.getenv("COLLECTOR_PER_SOURCE_CONCURRENCY", 1))
PER_HOST_MIN_INTERVAL_MS = int(os.getenv("COLLECTOR_PER_HOST_MIN_INTERVAL_MS", 500))

global_http_sem = threading.Semaphore(GLOBAL_HTTP_CONCURRENCY)
last_host_access = defaultdict(float)  # host -> last_ts
host_lock = defaultdict(threading.Lock)

@contextmanager
def acquire_http_slot(host: str):
    global_http_sem.acquire()
    try:
        lock = host_lock[host]
        with lock:
            now = time.time()
            delta = now - last_host_access[host]
            need = PER_HOST_MIN_INTERVAL_MS / 1000.0 - delta
            if need > 0:
                jitter = random.uniform(-0.1, 0.1)
                time.sleep(max(0, need + jitter))
            last_host_access[host] = time.time()
        yield
    finally:
        global_http_sem.release()

def fetch(session: requests.Session, url: str, timeout=(5, 10)) -> requests.Response:
    host = urllib.parse.urlparse(url).hostname or ""
    with acquire_http_slot(host):
        # 带重试/回退的请求，可用 urllib3 Retry 或自定义
        return session.get(url, timeout=timeout, headers={"User-Agent": UA})

def process_source(source: SourceConfig):
    session = new_configured_session()  # 独立 Session + Adapter + Retry
    index_html = fetch(session, source.index_url).text
    articles = extract_articles(index_html)
    # 默认：串行抓取详情
    if PER_SOURCE_DETAIL_CONCURRENCY <= 1:
        for a in articles:
            try:
                resp = fetch(session, a.url)
                yield parse_detail(a, resp.text)
            except Exception as e:
                log_error(source, a.url, e)
    else:
        # 可选：小并发（受全局信号量与按域名限速约束）
        with ThreadPoolExecutor(max_workers=PER_SOURCE_DETAIL_CONCURRENCY) as pool:
            futs = {pool.submit(fetch, session, a.url): a for a in articles}
            for fut in as_completed(futs):
                a = futs[fut]
                try:
                    resp = fut.result()
                    yield parse_detail(a, resp.text)
                except Exception as e:
                    log_error(source, a.url, e)

def run_all(sources: list[SourceConfig]):
    with ThreadPoolExecutor(max_workers=SOURCE_CONCURRENCY) as pool:
        futs = [pool.submit(process_source, s) for s in sources]
        for f in as_completed(futs):
            for item in f.result():
                writer_queue.put(item)
```

## 风险分析与缓解

- 并发爆炸与过载：
  - 风险（默认串行时）：总并发约等于来源并发（≈10），主要风险来自重试突刺或外部同时请求导致瞬时堆叠。
  - 缓解：保持“全局 HTTP 并发信号量”与指数回退重试；当开启来源内小并发（≥2）时，需重新评估全局并发与池容量。

- 被站点限流/封禁：
  - 风险：短时请求过密或同时抓取多域名导致 429/403/5xx。
  - 缓解：按域名最小间隔 + 抖动；尊重 robots.txt 的 crawl-delay；设置 UA、Referrer 基线并可配置代理；对 429/503 做冷却策略与熔断（临时跳过来源）。

- 连接池与端口耗尽：
  - 风险：短时并发过高导致本地 TIME_WAIT 积压、DNS/握手开销放大。
  - 缓解：复用 `Session` + `HTTPAdapter` 增大池容量；合理超时；避免无上限的并发层级；必要时调高内核端口区间并启用 keep-alive（由 `requests`/urllib3 默认支持）。

- GIL 与 CPU 负载：
  - 风险：解析/清洗为 CPU 密集时，线程并发受 GIL 影响；极端情况下降低吞吐。
  - 缓解：解析阶段尽量轻量；需要时对纯 CPU 步骤使用 `ProcessPoolExecutor` 或延后到离线批处理；避免在详情线程内进行重型 NLP。

- 数据写入竞争与一致性：
  - 风险：并发写数据库/文件可能产生死锁、重复或顺序不确定。
  - 缓解：集中写入（单写线程）或使用数据库原子/幂等约束（唯一键、UPSERT）；写入队列设置上限实现背压。

- 异常处理与可观测性不足：
  - 风险：子线程异常被吞，导致“沉默失败”；日志互相覆盖。
  - 缓解：统一捕获并打点异常；结构化日志包含 `source`, `thread`, `url`, `elapsed`; 指标记录成功率/耗时/队列深度；必要时启用采样级别的请求追踪 ID。

- 死锁/饥饿：
  - 风险：嵌套线程池互相 `result()` 阻塞或信号量获取次序不当。
  - 缓解：使用 `as_completed` 消费；严格避免在持有全局锁时进行阻塞 IO；信号量粒度只覆盖 HTTP 发送前后窗口。

- 复杂度与调试成本：
  - 风险：混用线程/异步导致心智负担上升。
  - 缓解：阶段一仅使用线程 + 信号量；如需更高并发，再评估迁移“详情拉取”至 `aiohttp` 的异步模型，但仍受全局限流约束。

## 配置项（建议）

- `COLLECTOR_SOURCE_CONCURRENCY`：来源并发上限（默认 10）。
- `COLLECTOR_PER_SOURCE_CONCURRENCY`：单来源详情并发（默认 1）。
- `COLLECTOR_GLOBAL_HTTP_CONCURRENCY`：全局 HTTP 并发上限（默认 16）。
- `COLLECTOR_PER_HOST_MIN_INTERVAL_MS`：同一域名的最小请求间隔（默认 500ms）。
- `COLLECTOR_TIMEOUT_CONNECT`/`COLLECTOR_TIMEOUT_READ`：超时配置（默认 5s/10s）。
- `COLLECTOR_RETRY_MAX`/`COLLECTOR_RETRY_BACKOFF_BASE`：重试次数与回退基数。
- `COLLECTOR_DISABLE_CONCURRENCY`：调试开关，强制串行以复现问题。

## 指标与监控

- 关键指标：
  - per-source 吞吐（articles/min）、成功率、P50/P95/P99 耗时；
  - 全局与每域名的并发度/队列深度；
  - HTTP 状态分布（2xx/3xx/4xx/5xx，特别是 429/403/503）。
- 日志字段：`source`, `url`, `status`, `elapsed_ms`, `retries`, `thread`, `host`, `queue_len`。
- 报警建议：近 5 分钟 429 比例超阈、某来源连续失败、全局并发长期打满、写入滞后持续扩大。

## 测试与演练

- 单元测试：
  - 使用 `responses`/`httpretty` 或本地 stub server 注入延迟、返回 429/5xx、超时，验证重试与回退策略；
  - 模拟 N 个来源 × M 篇详情，验证默认串行下总并发≈来源数；当详情并发升至 2/4 时，并发与吞吐按预期变化且不突破限流；
  - 验证去重与幂等写入；
  - Freshness 过滤在并发下保持正确性（顺序无关）。
- 压测回归：
  - 进行阶梯并发试验（如 SOURCE_CONCURRENCY 5→10，PER_SOURCE_DETAIL_CONCURRENCY 1→2），记录吞吐、错误率与被限流频度，调整参数至平衡点。

## 渐进式落地计划

1) 加入全局限流与来源并发参数，详情默认串行（PER_SOURCE_DETAIL_CONCURRENCY=1）；
2) 接入结构化日志与基础指标，建立基线；
3) 小流量放量（仅 2–3 个来源），观察 48 小时；
4) 扩至默认 10 个来源并发，评估被限流比与耗时分布；
5) 瓶颈明显且未触发限流时，将详情并发升至 2 并持续观测；如仍需提升，再评估“详情拉取”迁移 `aiohttp` 的收益与改造成本。

## 备选：异步模型简述

- 使用 `aiohttp` + `asyncio.Semaphore` 实现全局与按域名限流，通常能以更少线程获取更高并发密度；
- 复杂度上升：与现有 `requests`/阻塞解析混用需引入线程池桥接；
- 建议作为二阶段优化选项，不阻塞线程化方案的上线。

---

附录：安全与合规注意
- 不在代码中硬编码凭据；UA/代理从环境变量读取；
- 遵守 robots.txt 与站点 ToS，必要时设置 crawl-delay；
- 日志脱敏（移除 query 中的 token、cookie 等）。
