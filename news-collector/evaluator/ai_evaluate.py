from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set

import requests

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT.parent / "data"
DB_PATH = DATA_DIR / "info.db"

_PROMPT_FILE = "article_evaluation_zh.prompt"
_PROMPT_ENV = os.getenv("AI_PROMPT_PATH")
_PROMPT_CANDIDATES = [
    Path(_PROMPT_ENV) if _PROMPT_ENV else None,
    ROOT / "prompts" / "ai" / _PROMPT_FILE,
    ROOT.parent / "prompts" / "ai" / _PROMPT_FILE,
]
PROMPT_PATH = next((p for p in _PROMPT_CANDIDATES if p and p.exists()), _PROMPT_CANDIDATES[1])

DEFAULT_METRIC_SEED: Sequence[Dict[str, object]] = [
    {
        "key": "timeliness",
        "label_zh": "时效性",
        "rate_guide_zh": "5-当天/最新；3-一月内或时间无关（长期有价值）；1-过时",
        "default_weight": 0.14,
        "sort_order": 10,
    },
    {
        "key": "game_relevance",
        "label_zh": "游戏相关性",
        "rate_guide_zh": "5-核心聚焦游戏议题/数据/案例；3-泛娱乐与游戏相关；1-无关",
        "default_weight": 0.20,
        "sort_order": 20,
    },
    {
        "key": "mobile_game_relevance",
        "label_zh": "手游相关性",
        "rate_guide_zh": "5-聚焦手游（产品/发行/买量/市场数据）；3-部分相关；1-无关",
        "default_weight": 0.09,
        "sort_order": 30,
    },
    {
        "key": "ai_relevance",
        "label_zh": "AI相关性",
        "rate_guide_zh": "5-模型/算法/评测/标杆案例；3-泛AI应用；1-无关",
        "default_weight": 0.14,
        "sort_order": 40,
    },
    {
        "key": "tech_relevance",
        "label_zh": "科技相关性",
        "rate_guide_zh": "5-芯片/云/硬件/基础设施；3-泛科技商业动态；1-无关",
        "default_weight": 0.11,
        "sort_order": 50,
    },
    {
        "key": "quality",
        "label_zh": "文章质量",
        "rate_guide_zh": "5-结构严谨数据充分；3-结构一般信息适中；1-水文/缺依据",
        "default_weight": 0.13,
        "sort_order": 60,
    },
    {
        "key": "insight",
        "label_zh": "洞察力",
        "rate_guide_zh": "5-罕见且深刻的观点/关联/因果；3-常见分析；1-无洞见",
        "default_weight": 0.08,
        "sort_order": 70,
    },
    {
        "key": "depth",
        "label_zh": "深度",
        "rate_guide_zh": "5-分层拆解背景充分逻辑完整；3-覆盖关键事实；1-浅尝辄止",
        "default_weight": 0.06,
        "sort_order": 80,
    },
    {
        "key": "novelty",
        "label_zh": "新颖度",
        "rate_guide_zh": "5-罕见消息或独到观点；3-常见进展/整合；1-无新意",
        "default_weight": 0.05,
        "sort_order": 90,
    },
]

LEGACY_BACKFILL_ENV = "AI_LEGACY_BACKFILL"


class AIClientError(RuntimeError):
    """Raised when the AI API cannot return a valid response."""


@dataclass
class Article:
    info_id: int
    title: str
    source: str
    publish: str
    detail: str


@dataclass
class MetricDefinition:
    id: int
    key: str
    label_zh: str
    rate_guide_zh: Optional[str]
    default_weight: Optional[float]
    sort_order: int


@dataclass
class EvaluationResult:
    info_id: int
    scores: Dict[str, int]
    comment: str
    summary: str
    key_concepts: list[str] = field(default_factory=list)
    summary_long: str = ""
    raw_response: str = ""
    final_score: float = 0.0


@dataclass
class AIConfig:
    base_url: str
    api_path: str
    model: str
    api_key: str
    timeout: float
    interval: float
    max_retries: int
    weight_overrides: Dict[str, float]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="使用大模型为资讯打分并写入 SQLite")
    parser.add_argument("--db", default=str(DB_PATH), help="SQLite 数据库路径 (默认: data/info.db)")
    parser.add_argument("--limit", type=int, default=50, help="本次最多处理的资讯条数 (默认: 50)")
    parser.add_argument("--overwrite", action="store_true", help="已存在评价时重新生成并覆盖")
    parser.add_argument("--dry-run", action="store_true", help="仅打印结果，不写入数据库")
    parser.add_argument("--prompt", default=str(PROMPT_PATH), help="提示词文件路径 (默认: 自动探测)")
    parser.add_argument("--hours", type=int, default=24, help="仅处理最近 N 小时内的资讯 (默认: 24)")
    parser.add_argument("--category", action="append", default=[], help="仅评估指定分类，可重复传入 (例如: --category game)")
    parser.add_argument("--source", action="append", default=[], help="仅评估指定来源标识，可重复传入 (例如: --source chuapp)")
    parser.add_argument(
        "--exportprompt",
        help="将填充后的提示词导出到指定文件并退出",
    )
    return parser.parse_args()


def _try_parse_dt(value: str) -> Optional[datetime]:
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d",
    ):
        try:
            dt = datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            continue
    return None


def load_prompt(path: Path) -> tuple[str, str]:
    if not path.exists():
        raise SystemExit(f"未找到提示词文件: {path}")
    text = path.read_text(encoding="utf-8")
    marker_sys = "<<SYS>>"
    marker_user = "<<USER>>"
    if marker_sys not in text or marker_user not in text:
        raise SystemExit("提示词文件需要包含 <<SYS>> 与 <<USER>> 标记")
    sys_part, user_part = text.split(marker_user, 1)
    system_prompt = sys_part.replace(marker_sys, "", 1).strip()
    user_prompt = user_part.strip()
    if not system_prompt or not user_prompt:
        raise SystemExit("提示词内容不能为空")
    return system_prompt, user_prompt


def fill_prompt(template: str, mapping: Dict[str, str]) -> str:
    result = template
    for key, value in mapping.items():
        result = result.replace(f"{{{{{key}}}}}", value)
    return result


def load_config() -> AIConfig:
    base_url = os.getenv("AI_API_BASE_URL", "").strip()
    model = os.getenv("AI_API_MODEL", "").strip()
    api_key = os.getenv("AI_API_KEY", "").strip()
    if not base_url or not model or not api_key:
        raise SystemExit("AI_API_BASE_URL、AI_API_MODEL、AI_API_KEY 必须通过环境变量提供")

    api_path = os.getenv("AI_API_PATH", "/v1/chat/completions").strip() or "/v1/chat/completions"

    base = base_url.rstrip("/")
    if base.lower().endswith("/v1"):
        base = base.rsplit("/", 1)[0]

    timeout = float(os.getenv("AI_API_TIMEOUT", "30") or 30)
    interval = float(os.getenv("AI_REQUEST_INTERVAL", "0") or 0)
    max_retries = int(os.getenv("AI_MAX_RETRIES", "3") or 3)

    weight_overrides_env = os.getenv("AI_SCORE_WEIGHTS", "").strip()
    weight_overrides: Dict[str, float] = {}
    if weight_overrides_env:
        try:
            parsed = json.loads(weight_overrides_env)
        except json.JSONDecodeError as exc:
            raise SystemExit("AI_SCORE_WEIGHTS 必须是 JSON 对象，例如 {\"timeliness\":0.3,...}") from exc
        if not isinstance(parsed, dict):
            raise SystemExit("AI_SCORE_WEIGHTS 需要是 JSON 对象")
        for key, value in parsed.items():
            if isinstance(value, (int, float)) and float(value) >= 0:
                weight_overrides[key] = float(value)

    return AIConfig(
        base_url=base,
        api_path=api_path,
        model=model,
        api_key=api_key,
        timeout=timeout,
        interval=interval,
        max_retries=max_retries,
        weight_overrides=weight_overrides,
    )


def ensure_ai_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_metrics (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            key            TEXT NOT NULL UNIQUE,
            label_zh       TEXT NOT NULL,
            rate_guide_zh  TEXT,
            default_weight REAL,
            active         INTEGER NOT NULL DEFAULT 1,
            sort_order     INTEGER NOT NULL DEFAULT 0,
            created_at     TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at     TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ai_metrics_active
        ON ai_metrics (active, sort_order)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS info_ai_scores (
            info_id    INTEGER NOT NULL,
            metric_id  INTEGER NOT NULL,
            score      INTEGER NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (info_id, metric_id),
            FOREIGN KEY (info_id) REFERENCES info(id),
            FOREIGN KEY (metric_id) REFERENCES ai_metrics(id)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_info_ai_scores_info
        ON info_ai_scores (info_id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_info_ai_scores_metric
        ON info_ai_scores (metric_id)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS info_ai_review (
            info_id     INTEGER PRIMARY KEY,
            final_score REAL    NOT NULL DEFAULT 0.0,
            ai_comment  TEXT    NOT NULL,
            ai_summary  TEXT    NOT NULL,
            raw_response TEXT,
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at  TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (info_id) REFERENCES info(id)
        )
        """
    )
    review_columns = {row[1] for row in conn.execute("PRAGMA table_info(info_ai_review)").fetchall()}
    if "ai_key_concepts" not in review_columns:
        conn.execute("ALTER TABLE info_ai_review ADD COLUMN ai_key_concepts TEXT")
    if "ai_summary_long" not in review_columns:
        conn.execute("ALTER TABLE info_ai_review ADD COLUMN ai_summary_long TEXT")
    seed_default_metrics(conn)
    conn.commit()


def seed_default_metrics(conn: sqlite3.Connection) -> None:
    existing_keys = {
        row[0] for row in conn.execute("SELECT key FROM ai_metrics")
    }
    to_insert = [
        (
            seed["key"],
            seed["label_zh"],
            seed["rate_guide_zh"],
            seed["default_weight"],
            seed["sort_order"],
        )
        for seed in DEFAULT_METRIC_SEED
        if seed["key"] not in existing_keys
    ]
    if to_insert:
        conn.executemany(
            """
            INSERT INTO ai_metrics (key, label_zh, rate_guide_zh, default_weight, sort_order)
            VALUES (?, ?, ?, ?, ?)
            """,
            to_insert,
        )


def load_active_metrics(conn: sqlite3.Connection) -> List[MetricDefinition]:
    rows = conn.execute(
        """
        SELECT id, key, label_zh, rate_guide_zh, default_weight, sort_order
        FROM ai_metrics
        WHERE active = 1
        ORDER BY sort_order ASC, id ASC
        """
    ).fetchall()
    metrics = [
        MetricDefinition(
            id=row[0],
            key=row[1],
            label_zh=row[2],
            rate_guide_zh=row[3],
            default_weight=row[4],
            sort_order=row[5],
        )
        for row in rows
    ]
    if not metrics:
        raise SystemExit("ai_metrics 表为空，无法继续评估")
    return metrics


def build_metrics_block(metrics: Sequence[MetricDefinition]) -> str:
    lines: List[str] = []
    for metric in metrics:
        lines.append(f"- {metric.key}（{metric.label_zh}）")
    return "\n".join(lines)


def build_schema_example(metrics: Sequence[MetricDefinition]) -> str:
    score_lines: List[str] = []
    for index, metric in enumerate(metrics):
        desc_parts: List[str] = [metric.label_zh]
        if metric.rate_guide_zh:
            desc_parts.append(metric.rate_guide_zh)
        desc = "：".join(desc_parts)
        trailing = "," if index < len(metrics) - 1 else ""
        score_lines.append(f'    "{metric.key}": <1-5整数>{trailing}  --{desc}')

    example_lines = [
        "{",
        '  "dimension_scores": {',
        *score_lines,
        "  },",
        '  "comment": "<一句话中文评价>",  --整体评价，需说明理由',
        '  "summary": "<一句话介绍文章内容>",  --简要概括文章要点',
        '  "key_concepts": ["<按重要性列出0-5个核心名词>"],  --无法提炼时使用空数组 []',
        '  "summary_long": "<约50字的中文扩展摘要>"  --若缺资料可复用 summary',
        "}",
    ]
    return "\n".join(example_lines)


def fetch_candidates(
    conn: sqlite3.Connection,
    limit: int,
    overwrite: bool,
    hours: int,
    categories: Optional[List[str]] = None,
    sources: Optional[List[str]] = None,
) -> List[Article]:
    rows = conn.execute(
        """
        SELECT i.id, i.title, i.source, i.publish, i.detail, i.category,
               r.info_id IS NOT NULL AS has_review
        FROM info AS i
        LEFT JOIN info_ai_review AS r ON r.info_id = i.id
        ORDER BY i.id DESC
        """
    ).fetchall()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, int(hours)))
    articles: List[Article] = []
    cat_whitelist = set(categories or [])
    src_whitelist = set(sources or [])

    for row in rows:
        info_id, title, source, publish, detail, category, has_review = row
        if cat_whitelist and str(category or "") not in cat_whitelist:
            continue
        if src_whitelist and str(source or "") not in src_whitelist:
            continue
        if not overwrite and has_review:
            continue
        dt = _try_parse_dt(str(publish or ""))
        if not dt or dt < cutoff:
            continue
        articles.append(
            Article(
                info_id=int(info_id),
                title=str(title or ""),
                source=str(source or ""),
                publish=str(publish or ""),
                detail=str(detail or ""),
            )
        )
        if len(articles) >= int(limit):
            break
    return articles


def request_ai(config: AIConfig, system_prompt: str, user_prompt: str) -> str:
    url = f"{config.base_url.rstrip('/')}/{config.api_path.lstrip('/')}"
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": config.model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }

    for attempt in range(1, config.max_retries + 1):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=config.timeout)
            resp.raise_for_status()
            data = resp.json()
            choices = data.get("choices")
            if not choices:
                raise AIClientError("响应中缺少 choices 字段")
            message = choices[0].get("message") or {}
            content = message.get("content")
            if not content:
                raise AIClientError("响应中缺少 content 内容")
            return content
        except (requests.RequestException, ValueError) as exc:
            if attempt == config.max_retries:
                raise AIClientError(f"调用 AI 接口失败: {exc}") from exc
            wait = min(2 ** (attempt - 1), 10)
            time.sleep(wait)
    raise AIClientError("无法从 AI 获取有效响应")


def _strip_json_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped[3:]
        if stripped.lower().startswith("json"):
            stripped = stripped[4:]
        stripped = stripped.strip()
        if stripped.endswith("```"):
            stripped = stripped[:-3]
        return stripped.strip()
    return stripped


def parse_ai_payload(raw_text: str) -> Dict[str, object]:
    cleaned = _strip_json_fence(raw_text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise AIClientError(f"AI 返回内容不是合法 JSON: {exc}") from exc


def validate_scores(data: Dict[str, object], metrics: Sequence[MetricDefinition]) -> EvaluationResult:
    if "dimension_scores" not in data or not isinstance(data["dimension_scores"], dict):
        raise AIClientError("响应缺少 dimension_scores 字段")
    scores_raw = data["dimension_scores"]
    required_keys = [metric.key for metric in metrics]

    unexpected = {key for key in scores_raw.keys() if key not in required_keys}
    if unexpected:
        raise AIClientError(f"返回包含未知指标: {', '.join(sorted(unexpected))}")

    missing = [key for key in required_keys if key not in scores_raw]
    if missing:
        raise AIClientError(f"返回缺少指标: {', '.join(missing)}")

    scores: Dict[str, int] = {}
    for metric in metrics:
        value = scores_raw.get(metric.key)
        if not isinstance(value, (int, float)):
            raise AIClientError(f"维度 {metric.key} 的得分不是数字")
        score = int(round(float(value)))
        if score < 1 or score > 5:
            raise AIClientError(f"维度 {metric.key} 的得分超出 1-5 范围")
        scores[metric.key] = score

    comment = data.get("comment")
    summary = data.get("summary")
    if not isinstance(comment, str) or not comment.strip():
        raise AIClientError("comment 字段缺失或为空")
    if not isinstance(summary, str) or not summary.strip():
        raise AIClientError("summary 字段缺失或为空")

    raw_concepts = data.get("key_concepts", [])
    concepts: list[str] = []
    if raw_concepts is None:
        concepts = []
    elif isinstance(raw_concepts, str):
        normalized = raw_concepts.replace("，", ",").replace("、", ",").replace(";", ",")
        parts = [item.strip() for item in normalized.split(",")]
        concepts = [item for item in parts if item]
    elif isinstance(raw_concepts, (list, tuple)):
        for item in raw_concepts:
            if item is None:
                continue
            text = str(item).strip()
            if text:
                concepts.append(text)
    else:
        raise AIClientError("key_concepts 字段格式无效")
    if len(concepts) > 5:
        concepts = concepts[:5]

    summary_long_raw = data.get("summary_long")
    if summary_long_raw is None:
        summary_long = ""
    elif isinstance(summary_long_raw, str):
        summary_long = summary_long_raw.strip().replace("\n", " ")
    else:
        raise AIClientError("summary_long 字段必须为字符串")
    if not summary_long:
        summary_long = summary.strip().replace("\n", " ")

    return EvaluationResult(
        info_id=0,
        scores=scores,
        comment=comment.strip().replace("\n", " "),
        summary=summary.strip().replace("\n", " "),
        key_concepts=concepts,
        summary_long=summary_long,
    )


def compute_final_score(
    scores: Dict[str, int],
    metrics: Sequence[MetricDefinition],
    weight_overrides: Dict[str, float],
) -> float:
    weighted = 0.0
    total_weight = 0.0
    for metric in metrics:
        weight = weight_overrides.get(metric.key)
        if weight is None:
            weight = metric.default_weight or 0.0
        if weight <= 0:
            continue
        weighted += scores[metric.key] * weight
        total_weight += weight
    if total_weight <= 0:
        total_weight = float(len(scores) or 1)
        weighted = float(sum(scores.values()))
    final_score = weighted / total_weight
    return round(max(1.0, min(5.0, final_score)), 2)


def get_info_ai_review_columns(conn: sqlite3.Connection) -> Set[str]:
    rows = conn.execute("PRAGMA table_info(info_ai_review)").fetchall()
    return {row[1] for row in rows}


def store_evaluation(
    conn: sqlite3.Connection,
    evaluation: EvaluationResult,
    metrics: Sequence[MetricDefinition],
    review_columns: Set[str],
    enable_legacy_backfill: bool,
) -> None:
    score_rows = [
        (evaluation.info_id, metric.id, evaluation.scores[metric.key])
        for metric in metrics
    ]
    conn.executemany(
        """
        INSERT INTO info_ai_scores (info_id, metric_id, score, updated_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(info_id, metric_id) DO UPDATE SET
            score=excluded.score,
            updated_at=CURRENT_TIMESTAMP
        """,
        score_rows,
    )

    key_concepts_value = (
        json.dumps(evaluation.key_concepts, ensure_ascii=False) if evaluation.key_concepts else None
    )
    columns = [
        "info_id",
        "final_score",
        "ai_comment",
        "ai_summary",
        "ai_key_concepts",
        "ai_summary_long",
        "raw_response",
    ]
    values: List[object] = [
        evaluation.info_id,
        evaluation.final_score,
        evaluation.comment,
        evaluation.summary,
        key_concepts_value,
        evaluation.summary_long,
        evaluation.raw_response,
    ]
    updates = [
        "final_score=excluded.final_score",
        "ai_comment=excluded.ai_comment",
        "ai_summary=excluded.ai_summary",
        "ai_key_concepts=excluded.ai_key_concepts",
        "ai_summary_long=excluded.ai_summary_long",
        "raw_response=excluded.raw_response",
    ]

    if enable_legacy_backfill:
        for metric in metrics:
            column = f"{metric.key}_score"
            if column in review_columns:
                columns.append(column)
                values.append(evaluation.scores[metric.key])
                updates.append(f"{column}=excluded.{column}")

    placeholders = ", ".join(["?"] * len(values))
    conn.execute(
        f"""
        INSERT INTO info_ai_review ({', '.join(columns)}, updated_at)
        VALUES ({placeholders}, CURRENT_TIMESTAMP)
        ON CONFLICT(info_id) DO UPDATE SET
            {', '.join(updates)},
            updated_at=CURRENT_TIMESTAMP
        """,
        values,
    )


def evaluate_articles(
    conn: sqlite3.Connection,
    config: AIConfig,
    articles: Iterable[Article],
    metrics: Sequence[MetricDefinition],
    system_prompt: str,
    user_template: str,
    dry_run: bool,
    review_columns: Set[str],
    enable_legacy_backfill: bool,
) -> None:
    for article in articles:
        mapping = {
            "title": article.title,
            "source": article.source,
            "publish": article.publish,
            "detail": article.detail,
        }
        user_prompt = fill_prompt(user_template, mapping)
        try:
            raw_text = request_ai(config, system_prompt, user_prompt)
            payload = parse_ai_payload(raw_text)
            result = validate_scores(payload, metrics)
        except AIClientError as exc:
            print(f"[失败] {article.info_id} - {article.title}: {exc}")
            continue

        result.info_id = article.info_id
        result.raw_response = raw_text
        result.final_score = compute_final_score(result.scores, metrics, config.weight_overrides)

        if dry_run:
            dims = " / ".join(
                [
                    f"{metric.label_zh}:{result.scores[metric.key]}"
                    for metric in metrics
                ]
            )
            print(
                f"[预览] {article.info_id} {article.title}\n"
                f"  {dims}\n"
                f"  评价: {result.comment}\n"
                f"  概要: {result.summary}\n"
                f"  概念: {', '.join(result.key_concepts) or 'N/A'}\n"
                f"  摘要: {result.summary_long}"
            )
        else:
            store_evaluation(conn, result, metrics, review_columns, enable_legacy_backfill)
            conn.commit()
            dim_str = " / ".join(
                f"{metric.key}:{result.scores[metric.key]}" for metric in metrics
            )
            print(f"[完成] {article.info_id} - {article.title} -> {dim_str}")
        if config.interval > 0:
            time.sleep(config.interval)


def main() -> None:
    args = parse_args()
    prompt_path = Path(args.prompt) if args.prompt else PROMPT_PATH
    system_prompt, user_template = load_prompt(prompt_path)
    limit = max(1, int(args.limit))

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"数据库不存在: {db_path}")

    with sqlite3.connect(str(db_path)) as conn:
        ensure_ai_tables(conn)
        metrics = load_active_metrics(conn)
        metrics_block = build_metrics_block(metrics)
        schema_example = build_schema_example(metrics)
        enriched_template = fill_prompt(
            user_template,
            {
                "metrics_block": metrics_block,
                "schema_example": schema_example,
            },
        )
        if args.exportprompt:
            export_path = Path(args.exportprompt)
            export_path.parent.mkdir(parents=True, exist_ok=True)
            content = "[SYSTEM]\n"
            content += f"{system_prompt}\n\n"
            content += "[USER]\n"
            content += enriched_template
            export_path.write_text(content, encoding="utf-8")
            print(f"提示词已导出到 {export_path}")
            return
        config = load_config()
        review_columns = get_info_ai_review_columns(conn)
        articles = fetch_candidates(
            conn,
            limit,
            args.overwrite,
            args.hours,
            categories=args.category,
            sources=args.source,
        )
        if not articles:
            print("没有待处理的资讯")
            return
        legacy_backfill = os.getenv(LEGACY_BACKFILL_ENV, "0").strip().lower() in {"1", "true", "yes"}
        evaluate_articles(
            conn=conn,
            config=config,
            articles=articles,
            metrics=metrics,
            system_prompt=system_prompt,
            user_template=enriched_template,
            dry_run=args.dry_run,
            review_columns=review_columns,
            enable_legacy_backfill=legacy_backfill,
        )


if __name__ == "__main__":
    main()
