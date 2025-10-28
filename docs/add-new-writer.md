# Writer 扩展实施指南

本文档约定在 `news-collector/manager/` 目录内新增“Writer”类脚本时需遵守的规范，确保输出样式、依赖和 CLI 行为与现有 `info_writer.py`、`feishu_writer.py` 一致。

## 1. 目录与命名
- 统一放在 `news-collector/manager/` 下，文件名使用 `snake_case`，并带有 `_writer` 后缀（例如 `newsletter_writer.py`）。
- 若 Writer 需要专属文档或示例，请在 `docs/prompt/` 子目录中新增对应说明；不要把说明散落在脚本文件里。

## 2. 数据来源与依赖
- 默认数据源为 SQLite `data/info.db`，Writer 应通过 SQL 一次性拉取所需字段（`info` 与 `info_ai_review`）。
- 尊重 `docs/prompt/ai-evaluation-spec.md` 中的维度命名与默认权重；如需新增维度，请先更新该规范以及相关脚本。
- 运行脚本不允许触发网络请求，也不要引入额外外部依赖；标准库和现有第三方库（requests、bs4 等）之外的依赖需在 `requirements.txt` 说明。

## 3. CLI 约定
- 使用 `argparse` 提供可选参数，常见参数包括：
  - 时间窗口（`--hours` 或等价选项）
  - 输出路径（`--output`）
  - 权重覆盖、来源加权、过滤阈值等可选参数
  - `--dry-run`：仅打印预览，不写文件
- CLI 错误与缺少资源时要提供友好消息（例如数据库缺失、未找到评估表等）。

## 4. 评分与排序
- Writer 必须在展示阶段重新计算加权总分，而非直接使用数据库中的 `final_score`。
- 默认维度顺序与标签应复用 `info_writer.py` 的常量，便于组合输出。
- 排序策略需在文档或脚本注释中说明（例如：先按总分，再按发布时间逆序）。

## 5. 输出与落盘
- 输出格式可以是 HTML、Markdown 或其他下游需要的形式，但必须：
  - 清晰注明生成时间、条目数、来源与标题
  - 在需要展示 AI 分维时使用统一的中文标签
- 若筛选后 **没有任何条目**，脚本必须打印提示并退出，不得写出空文件。
- Dry run 模式下应仍旧遵守“无条目时只输出提示”原则。

## 6. 测试与校验
- 至少确保 `python -m compileall news-collector` 通过。
- 推荐补充针对权重计算、过滤逻辑的单元测试，可放置在 `news-collector/manager/tests/`（若目录尚不存在需创建）。
- 新增 Writer 前请运行一次 `info_writer.py` 与 `feishu_writer.py` 验证共享常量未被破坏。

## 7. 与现有 Writer 对齐的要点
- `info_writer.py`：生成 HTML 卡片视图；按分类分组，展示评分星级、各维度分与评语。
- `feishu_writer.py`：输出 Lark Markdown，强调序号、评分与超链接；需兼容 `feishu_bot_today.py`。
- 新 Writer 若与上述脚本共享逻辑（例如 `compute_weighted_score`），优先复用或提取公共函数，避免复制粘贴后出现漂移。

遵循以上规范可以确保新增 Writer 在命名、数据处理与输出风格上保持一致，便于其他模块复用与维护。

