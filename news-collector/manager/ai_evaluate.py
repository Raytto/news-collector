from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import requests

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT.parent / "data"
DB_PATH = DATA_DIR / "info.db"
# Resolve prompt path from multiple common locations
_PROMPT_FILE = "article_evaluation_zh.prompt"
_PROMPT_ENV = os.getenv("AI_PROMPT_PATH")
_PROMPT_CANDIDATES = [
    Path(_PROMPT_ENV) if _PROMPT_ENV else None,
    ROOT / "prompts" / "ai" / _PROMPT_FILE,
    ROOT.parent / "prompts" / "ai" / _PROMPT_FILE,
]
PROMPT_PATH = next((p for p in _PROMPT_CANDIDATES if p and p.exists()), _PROMPT_CANDIDATES[1])

DEFAULT_WEIGHTS: Dict[str, float] = {
    "timeliness": 0.18,
    "game_relevance": 0.24,
    "ai_relevance": 0.18,
    "tech_relevance": 0.14,
    "quality": 0.16,
    "insight": 0.10,
}
DIMENSION_ORDER: Tuple[str, ...] = tuple(DEFAULT_WEIGHTS.keys())


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
class EvaluationResult:
    info_id: int
    final_score: float
    timeliness: int
    game_relevance: int
    ai_relevance: int
    tech_relevance: int
    quality: int
    insight: int
    comment: str
    summary: str
    raw_response: str


@dataclass
class AIConfig:
    base_url: str
    api_path: str
    model: str
    api_key: str
    timeout: float
    interval: float
    max_retries: int
    weights: Dict[str, float]


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


def load_prompt(path: Path) -> Tuple[str, str]:
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

    # Optional custom path (OpenAI compatible default)
    api_path = os.getenv("AI_API_PATH", "/v1/chat/completions").strip() or "/v1/chat/completions"

    # Normalize base_url: strip trailing slash and trailing "/v1" segment to avoid double /v1
    base = base_url.rstrip("/")
    if base.lower().endswith("/v1"):
        base = base.rsplit("/", 1)[0]

    timeout = float(os.getenv("AI_API_TIMEOUT", "30") or 30)
    interval = float(os.getenv("AI_REQUEST_INTERVAL", "0") or 0)
    max_retries = int(os.getenv("AI_MAX_RETRIES", "3") or 3)

    weight_override = os.getenv("AI_SCORE_WEIGHTS", "").strip()
    weights = DEFAULT_WEIGHTS.copy()
    if weight_override:
        try:
            overrides = json.loads(weight_override)
            if isinstance(overrides, dict):
                for key, value in overrides.items():
                    if key in weights and isinstance(value, (int, float)) and value >= 0:
                        weights[key] = float(value)
        except json.JSONDecodeError:
            raise SystemExit("AI_SCORE_WEIGHTS 必须是 JSON 对象，例如 {\"timeliness\":0.3,...}")

    return AIConfig(
        base_url=base,
        api_path=api_path,
        model=model,
        api_key=api_key,
        timeout=timeout,
        interval=interval,
        max_retries=max_retries,
        weights=weights,
    )


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS info_ai_review (
            info_id INTEGER PRIMARY KEY,
            final_score REAL NOT NULL,
            timeliness_score INTEGER NOT NULL,
            game_relevance_score INTEGER,
            ai_relevance_score INTEGER,
            tech_relevance_score INTEGER,
            quality_score INTEGER,
            insight_score INTEGER,
            ai_comment TEXT NOT NULL,
            ai_summary TEXT NOT NULL,
            raw_response TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (info_id) REFERENCES info(id)
        )
        """
    )
    # Backfill/migrate: add new columns if missing
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(info_ai_review)")}
        for col in ("game_relevance_score", "ai_relevance_score", "tech_relevance_score", "quality_score", "insight_score"):
            if col not in cols:
                conn.execute(f"ALTER TABLE info_ai_review ADD COLUMN {col} INTEGER")
        conn.commit()
    except Exception:
        pass


def fetch_candidates(
    conn: sqlite3.Connection,
    limit: int,
    overwrite: bool,
    hours: int,
    categories: List[str] | None = None,
    sources: List[str] | None = None,
) -> List[Article]:
    """Return info rows to evaluate.

    Logic: evaluate any `info` rows whose `id` is not present in `info_ai_review`
    (unless --overwrite is set, which ignores existing reviews). No requirement
    on `detail` being present; prompt will receive an empty detail string when
    unavailable.
    """
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
    cat_whitelist = set((categories or []))
    src_whitelist = set((sources or []))

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


def validate_scores(data: Dict[str, object]) -> EvaluationResult:
    if "dimension_scores" not in data or not isinstance(data["dimension_scores"], dict):
        raise AIClientError("响应缺少 dimension_scores 字段")
    scores_raw = data["dimension_scores"]
    scores: Dict[str, int] = {}
    for dim in DIMENSION_ORDER:
        value = scores_raw.get(dim)
        if not isinstance(value, (int, float)):
            raise AIClientError(f"维度 {dim} 的得分不是数字")
        score = int(round(float(value)))
        if score < 1 or score > 5:
            raise AIClientError(f"维度 {dim} 的得分超出 1-5 范围")
        scores[dim] = score

    comment = data.get("comment")
    summary = data.get("summary")
    if not isinstance(comment, str) or not comment.strip():
        raise AIClientError("comment 字段缺失或为空")
    if not isinstance(summary, str) or not summary.strip():
        raise AIClientError("summary 字段缺失或为空")

    return EvaluationResult(
        info_id=0,
        final_score=0.0,  # placeholder, 将在外部计算
        timeliness=scores["timeliness"],
        game_relevance=scores["game_relevance"],
        ai_relevance=scores["ai_relevance"],
        tech_relevance=scores["tech_relevance"],
        quality=scores["quality"],
        insight=scores["insight"],
        comment=comment.strip().replace("\n", " "),
        summary=summary.strip().replace("\n", " "),
        raw_response="",
    )


def compute_final_score(result: EvaluationResult, weights: Dict[str, float]) -> float:
    weighted = 0.0
    total_weight = 0.0
    for dim in DIMENSION_ORDER:
        score = getattr(result, dim)
        weight = weights.get(dim, 0.0)
        if weight <= 0:
            continue
        weighted += score * weight
        total_weight += weight
    if total_weight <= 0:
        total_weight = float(len(DIMENSION_ORDER))
        weighted = sum(getattr(result, dim) for dim in DIMENSION_ORDER)
    final_score = weighted / total_weight
    return round(max(1.0, min(5.0, final_score)), 2)


def store_evaluation(conn: sqlite3.Connection, evaluation: EvaluationResult) -> None:
    conn.execute(
        """
        INSERT INTO info_ai_review (
            info_id,
            final_score,
            timeliness_score,
            game_relevance_score,
            ai_relevance_score,
            tech_relevance_score,
            quality_score,
            insight_score,
            ai_comment,
            ai_summary,
            raw_response,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(info_id) DO UPDATE SET
            final_score=excluded.final_score,
            timeliness_score=excluded.timeliness_score,
            game_relevance_score=excluded.game_relevance_score,
            ai_relevance_score=excluded.ai_relevance_score,
            tech_relevance_score=excluded.tech_relevance_score,
            quality_score=excluded.quality_score,
            insight_score=excluded.insight_score,
            ai_comment=excluded.ai_comment,
            ai_summary=excluded.ai_summary,
            raw_response=excluded.raw_response,
            updated_at=CURRENT_TIMESTAMP
        """,
        (
            evaluation.info_id,
            evaluation.final_score,
            evaluation.timeliness,
            evaluation.game_relevance,
            evaluation.ai_relevance,
            evaluation.tech_relevance,
            evaluation.quality,
            evaluation.insight,
            evaluation.comment,
            evaluation.summary,
            evaluation.raw_response,
        ),
    )


def evaluate_articles(
    conn: sqlite3.Connection,
    config: AIConfig,
    articles: Iterable[Article],
    system_prompt: str,
    user_template: str,
    dry_run: bool,
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
            result = validate_scores(payload)
        except AIClientError as exc:
            print(f"[失败] {article.info_id} - {article.title}: {exc}")
            continue

        result.info_id = article.info_id
        result.raw_response = raw_text
        # 不在评估阶段计算加权总分，交由 Writer 按当前规则动态计算。
        result.final_score = 0.0

        if dry_run:
            print(
                f"[预览] {article.info_id} {article.title}\n"
                f"  总推荐度: {result.final_score}"
                f" | 时效性: {result.timeliness}"
                f" | 相关性: {result.relevance}"
                f" | 洞察力: {result.insightfulness}"
                f" | 可行动性: {result.actionability}\n"
                f"  评价: {result.comment}\n  概要: {result.summary}"
            )
        else:
            store_evaluation(conn, result)
            conn.commit()
            # 打印各维度分值，便于观察评分构成
            print(
                f"[完成] {article.info_id} - {article.title} -> "
                f"时效:{result.timeliness} / 游戏:{result.game_relevance} / "
                f"AI:{result.ai_relevance} / 科技:{result.tech_relevance} / 质量:{result.quality} / 洞察:{result.insight}"
            )
        if config.interval > 0:
            time.sleep(config.interval)


def main() -> None:
    args = parse_args()
    config = load_config()
    prompt_path = Path(args.prompt) if args.prompt else PROMPT_PATH
    system_prompt, user_template = load_prompt(prompt_path)
    limit = max(1, int(args.limit))

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"数据库不存在: {db_path}")

    with sqlite3.connect(str(db_path)) as conn:
        ensure_table(conn)
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
        evaluate_articles(
            conn=conn,
            config=config,
            articles=articles,
            system_prompt=system_prompt,
            user_template=user_template,
            dry_run=args.dry_run,
        )


if __name__ == "__main__":
    main()
