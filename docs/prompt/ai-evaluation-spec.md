# AI 文章评估集成规范（中文）

## 1. 背景与目标
- 目标：为 `info` 表中的文章记录自动生成多维度评分、中文概要与评价，形成“是否值得推荐”的量化参考。
- 范围：本规范约定配置、数据结构、提示词与调度脚本的设计，作为实现蓝图。

## 2. 总体架构
1. 数据来源：`info` 表中的文章记录（可有 `detail` 正文）。
2. 评估服务：通过可配置的 HTTP API 调用大语言模型（LLM），端点/模型/key 从 Conda 环境变量读取。
3. 提示词层：`prompts/ai/` 下维护模板，脚本在运行时注入文章元数据与正文后发送至 LLM。
4. 结果存储：新表 `info_ai_review` 按 `info.id` 进行 1:1 关联，保存维度分数与加权总分、AI 概要与评价。
5. 调度脚本：遍历缺失评估的 `info` 行，批量请求并入库结果。

## 3. 配置与密钥
- 在 `environment.yml` 中声明：`AI_API_BASE_URL`、`AI_API_MODEL`、`AI_API_KEY`；可选：`AI_REQUEST_INTERVAL`、`AI_API_TIMEOUT`、`AI_SCORE_WEIGHTS`。
- 程序通过 `os.getenv` 读取；严禁提交真实密钥。后续在 `docs/prompt/ai/README.md` 汇总环境变量说明。

## 4. 提示词文件
- 目录：`prompts/ai/`；评估模板：`prompts/ai/article_evaluation_zh.prompt`。
- 模板要求模型：
  1) 阅读 `detail` 正文；2) 对各维度给出 1–5 分；3) 生成一句话中文概要；4) 给出一句话中文评价；5) 仅返回约定 JSON。
- 需包含占位符（如 `{{title}}`、`{{detail}}`），由脚本渲染；全部中文输出。

## 5. 评估维度与权重（更新版）
- 统一维度：`timeliness`（时效性）、`game_relevance`（游戏相关性）、`ai_relevance`（AI 相关性）、`tech_relevance`（科技相关性）、`quality`（文章质量）、`insight`（洞察力）。
- 分值范围：1（差）— 5（优）。
- 初始权重（Writer 端动态计算，可用 `AI_SCORE_WEIGHTS` 覆盖）：
  - `timeliness`: 0.18
  - `game_relevance`: 0.24
  - `ai_relevance`: 0.18
  - `tech_relevance`: 0.14
  - `quality`: 0.16
  - `insight`: 0.10

### 维度定义与打分参考
- 时效性 timeliness：内容与当前时间的相关程度；5=当天/最新事件；4=一周内热点；3=一月内一般新闻；2=旧闻但仍具参考；1=过时或无时间关联。
- 游戏相关性 game_relevance：与游戏产业/产品/市场/发行/买量/数据等贴合程度；5=深度聚焦核心议题/数据/案例，1=无关。
- AI 相关性 ai_relevance：与 AI 技术/模型/工具链/评测/应用相关程度；5=直指模型/算法/评测或标杆案例，1=无关。
- 科技相关性 tech_relevance：与芯片/云/硬件/互联网基础设施等关联性；5=面向科技产业核心构件或生态，1=无关。
- 文章质量 quality：结构清晰、论证完整、数据/引用可靠、信息密度高、废话少、可读性好；5=结构严谨数据充分，1=水文或缺乏依据。
 - 洞察力 insight：观点是否罕见/深刻、能否提出新的联系或因果解释；5=罕见且深刻的洞见，3=常见分析与观点，1=显而易见或无实质洞见。

## 6. 数据库（建议）
- 新建 `info_ai_review` 含以下列（与 `info(id)` 1:1）：
  | 列名 | 类型 | 约束 | 说明 |
  | --- | --- | --- | --- |
  | `info_id` | INTEGER | PRIMARY KEY, REFERENCES `info(id)` | 唯一关联 |
  | `final_score` | NUMERIC(3,2) | NOT NULL | 加权总分 |
  | `timeliness_score` | SMALLINT | NOT NULL | 1–5 |
  | `game_relevance_score` | SMALLINT | NOT NULL | 1–5 |
  | `ai_relevance_score` | SMALLINT | NOT NULL | 1–5 |
  | `tech_relevance_score` | SMALLINT | NOT NULL | 1–5 |
  | `quality_score` | SMALLINT | NOT NULL | 1–5 |
  | `ai_summary` | TEXT | NOT NULL | 一句话概要 |
  | `ai_comment` | TEXT | NOT NULL | 一句话评价 |
  | `raw_response` | TEXT |  | 原始响应 |
  | `created_at` | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP |  |
  | `updated_at` | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP |  |
- 为 `final_score` 与时间列增加索引，便于排序与筛选。

## 7. 管理脚本职责
- 位置：`news-collector/manager/ai_evaluate.py`。
- 核心流程：选择缺评估的 `info` 行 → 渲染提示词 → 调用 API（重试/限速）→ 校验字段 → 写入 `info_ai_review`。
- 注意：评估阶段不必计算“加权总分”，只需存储各维度评分、`comment` 与 `summary`。总分由展示层在渲染时按当前权重规则动态计算。
- CLI：支持 `--limit`、`--dry-run`、`--hours`（时间窗筛选）。

## 8. 展示层输出
- `manager/info_writer.py` 展示：总分（星级+数值）+ 各维度分 + 中文评价/概要；缺评估显示占位提示。

## 9. AI 客户端实现要点
- 从环境变量加载配置；处理网络错误/超时/重试；严格校验 JSON 字段与分值区间。

## 10. 错误处理与监控
- 指数退避重试；失败个例记录 `info_id` 并继续；可选审计/调试日志。

## 11. Security & Compliance
- Store secrets via environment variables managed by Conda (`environment.yml`); never log API keys.
- Redact article content in logs when unnecessary.
- Respect API usage policies and document request volumes.

## 12. 测试与验收
- 单元：提示词渲染、响应解析与校验、加权逻辑；集成：模拟 API 响应。
- 手动：配置测试 Key → 运行 `python manager/ai_evaluate.py --dry-run` → 检查 `info_ai_review`。

## 13. 发布与回填
- 回填历史记录；权重更新时可定期重跑；同步表结构与输出格式给下游。
