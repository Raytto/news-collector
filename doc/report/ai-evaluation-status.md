# AI 评估集成现状评估报告

## 概览
针对《AI Article Evaluation Integration Spec》中提出的评估流水线目标，对当前代码仓库的实现状态进行了核对，梳理出已落实的能力、仍待补齐的空白以及潜在的优化方向，便于后续规划迭代。【F:docs/prompt/ai-evaluation-spec.md†L1-L122】

## 已完成的需求
### 配置与环境准备
- `environment.yml` 已为评估服务声明基础地址、模型名、API Key、超时与请求间隔等环境变量，满足规范要求的可配置化入口。【F:docs/prompt/ai-evaluation-spec.md†L15-L21】【F:environment.yml†L1-L13】

### 提示词层
- 已在 `prompts/ai/article_evaluation_zh.prompt` 中按照规范提供系统与用户段落、插值占位符以及中文产出要求，覆盖评分、总结与评价字段的结构化响应指引。【F:docs/prompt/ai-evaluation-spec.md†L23-L33】【F:prompts/ai/article_evaluation_zh.prompt†L1-L26】

### 管理脚本职责
- `manager/ai_evaluate.py` 提供 CLI 入口、从 `info` 表筛选待评估文章、填充提示词、调用 AI、解析 JSON、校验分值区间、计算加权分并写回数据库，同时支持 `--limit`、`--dry-run` 与重试/间隔控制，覆盖规范列出的核心职责。【F:docs/prompt/ai-evaluation-spec.md†L67-L82】【F:news-collector/manager/ai_evaluate.py†L65-L399】

### 数据持久化
- 管理脚本会在运行时确保存在 `info_ai_review` 表，并落库四个维度分数、总分、中文评价/概要、时间戳及原始响应文本，基本对齐规范所述字段设计及 1:1 关联要求。【F:docs/prompt/ai-evaluation-spec.md†L50-L65】【F:news-collector/manager/ai_evaluate.py†L131-L325】

### Writer 展示层
- `manager/info_writer.py` 已将 AI 评分在资讯卡片中以星级、数值、各维度明细、中文评价与概要的形式展示，同时在缺失评估时提供明显占位，满足前端呈现要求。【F:docs/prompt/ai-evaluation-spec.md†L83-L88】【F:news-collector/manager/info_writer.py†L17-L257】

## 尚未完成或需要补充的部分
- 规范要求在 `docs/prompt/ai/README.md` 记录所需环境变量，但当前目录尚无该文档。【F:docs/prompt/ai-evaluation-spec.md†L15-L21】【5dff8e†L1-L2】
- 规范建议以迁移脚本形式新增 `info_ai_review` 表并考虑索引；现阶段仅在运行时通过 `CREATE TABLE IF NOT EXISTS` 建表，也未附带针对 `final_score` 的索引，仍需独立迁移与优化脚本。【F:docs/prompt/ai-evaluation-spec.md†L50-L66】【F:news-collector/manager/ai_evaluate.py†L131-L149】
- 计划中的通用 `services/ai_client.py` 尚未落地，当前调用逻辑直接实现在管理脚本内，缺少可复用的客户端封装。【F:docs/prompt/ai-evaluation-spec.md†L90-L95】【d94c72†L1-L2】
- 规范列出的单元/集成测试尚未补齐，仓库中没有 `test_*.py` 测试文件，可考虑为提示词渲染、响应校验与加权逻辑添加自动化覆盖。【F:docs/prompt/ai-evaluation-spec.md†L108-L117】【e3e1b1†L1-L2】
- 推广与回填步骤尚缺少脚本或文档支持，例如如何执行历史数据回填、定期重跑与向下游同步 schema 变更，可后续补充运维手册。【F:docs/prompt/ai-evaluation-spec.md†L119-L122】

## 可优化的方向
- `info_ai_review` 的 `final_score` 当前使用 `REAL` 类型，若需贴合 `NUMERIC(3,2)` 精度要求，可在迁移中改为定点数并统一各维度分数类型，提升数据一致性。【F:docs/prompt/ai-evaluation-spec.md†L47-L56】【F:news-collector/manager/ai_evaluate.py†L131-L147】
- 权重覆盖通过单个 `AI_SCORE_WEIGHTS` JSON 变量实现，后续若需更精细的配置或热更新，可按规范建议拆分为独立环境变量或集中配置模块以便运营调参。【F:docs/prompt/ai-evaluation-spec.md†L35-L48】【F:news-collector/manager/ai_evaluate.py†L108-L128】
- 当前仅打印日志并在失败时跳过，若需更完善的监控，可按规范延伸实现重试次数告警、失败明细持久化或审计日志，以支撑稳定运行与后续排障。【F:docs/prompt/ai-evaluation-spec.md†L97-L103】【F:news-collector/manager/ai_evaluate.py†L195-L373】

---

## 评估维度调整提案（中文）

为更好地区分资讯在不同读者群（游戏/AI/科技）中的价值，建议将原有维度进行如下调整，并补充每项评分定义与参考档位。后续会在提示词与权重中落地这些变更。

### 新/改维度列表
- 时效性 timeliness（保留）
- 游戏相关性 game_relevance（将原“相关性”细化为游戏向）
- AI 相关性 ai_relevance（新增）
- 科技相关性 tech_relevance（新增）
- 文章质量 quality（新增，结构清晰、信息密度、数据支撑等）

### 评分口径与示例（1–5 分）
- 时效性 timeliness：文章内容与当前时间的相关程度，是否为最新动态、突发新闻或当下热门话题。
  - 5 = 当天/最新事件；4 = 一周内热点；3 = 一月内一般新闻；2 = 旧闻但仍具参考；1 = 过时或无时间关联。

- 游戏相关性 game_relevance：与游戏产业、产品、市场、发行、买量、数据等的贴合程度。
  - 5 = 高度聚焦游戏产业核心议题/数据/案例；4 = 与游戏强相关的行业动态或深度评论；3 = 泛娱乐/内容但与游戏有一定关联；2 = 提及游戏但联系较弱；1 = 基本无关。

- AI 相关性 ai_relevance：与 AI 技术、模型、工具链、评测、应用案例的相关程度。
  - 5 = 直指模型/算法/评测或标杆案例；4 = 与 AI 方向密切相关；3 = 泛 AI/自动化应用；2 = 偶有提及；1 = 无关。

- 科技相关性 tech_relevance：与更广义的科技行业（芯片、云、硬件、互联网基础设施等）的相关程度。
  - 5 = 直接面向科技产业核心构件或生态；4 = 与科技行业密切相关；3 = 泛科技商业动态；2 = 关系较弱；1 = 无关。

- 文章质量 quality：结构清晰、论证完整、引用规范、数据或一手素材支撑、信息密度、可读性（废话少）。
  - 5 = 结构严谨、结论明确、数据/图表完备、信息密度高；
  - 4 = 结构清楚、信息较实，偶有主观但总体可靠；
  - 3 = 结构一般、信息密度中等、论证略松散；
  - 2 = 结构混乱/信息冗余，支撑不足；
  - 1 = 明显水文/拼接文或缺乏基本事实依据。

### 权重建议（初稿，可在 `AI_SCORE_WEIGHTS` 中覆盖）
- timeliness: 0.20
- game_relevance: 0.25
- ai_relevance: 0.20
- tech_relevance: 0.15
- quality: 0.20

### 提示词需要的同步修改
- 调整返回 JSON 结构：
  - `dimension_scores` 内字段改为：`timeliness`, `game_relevance`, `ai_relevance`, `tech_relevance`, `quality`（均为 1–5 整数）。
  - 继续返回 `comment` 与 `summary`（中文）。
- 在系统提示中加入各维度定义与 1–5 档位说明；用户提示保持“标题/来源/时间/正文”占位。

### 数据库与展示的后续影响（仅规划，未改动代码）
- DB：`info_ai_review` 新增 3 个相关性字段与 `quality_score` 列；同时更新计算总分的权重逻辑。
- 展示：`info_writer.py` 仍显示“总分 + 各维度明细 + 评价/概要”；维度标签替换为上述新维度。
- 迁移：提供 SQL 迁移脚本为现有表添加新列，并为常用查询（如总分倒序）补充索引。
