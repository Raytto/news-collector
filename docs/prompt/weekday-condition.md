目标：为每个 pipeline 在 `enabled` 之外新增“按星期运行”条件；仅当“今天的星期”属于该 pipeline 配置的允许集合时才执行。

一、需求与语义
- 映射约定：使用 ISO 周编号，1=周一，2=周二，…，7=周日。
- 缺省行为（兼容旧数据）：
  - 字段缺失或为 NULL → 视为允许任意星期（即与当前行为一致）。
  - 字段存在但为空数组 `[]` → 表示所有星期均不运行（比 `enabled=0` 更细粒度的“停用”方式）。
- 时区基准：默认使用北京时区 `Asia/Shanghai` 判定“今天”。允许通过环境变量 `PIPELINE_TZ` 覆盖，或在后续扩展为每个 pipeline 独立设置。
- 覆盖运行：
  - 增加 CLI 开关 `--ignore-weekday` 或环境变量 `FORCE_RUN=1`，用于手动或紧急情况下强制忽略星期限制。
  - 覆盖仅影响当前进程，不改写数据库配置。

二、数据库变更（SQLite）
- 在 `pipelines` 表新增：
  - `weekdays_json` TEXT：JSON 数组，元素为整数 1–7，表示允许运行的星期集合；NULL 表示无限制。
- SQL（迁移脚本应幂等）：
  - `ALTER TABLE pipelines ADD COLUMN weekdays_json TEXT;`（如列已存在则跳过）
- 数据导出/导入：在 `pipeline_admin export/import` 的 JSON 结构中纳入 `weekdays_json` 字段。
- 兼容性：旧库与旧导出文件不包含此字段时，导入后视为 NULL（无限制）。

三、后端 Runner 变更
- 文件：`news-collector/write-deliver-pipeline/pipeline_runner.py`
  - 在装载 pipeline 后、执行前增加星期校验：
    1) 解析 `weekdays_json` 为集合 `S`；若为 NULL → 放行。
    2) 以 `tz = os.getenv("PIPELINE_TZ", "Asia/Shanghai")` 获取时区。
    3) `today = datetime.now(ZoneInfo(tz)).isoweekday()` 得到 1–7。
    4) 若 `S` 为空数组或 `today not in S` → 跳过并输出 `[SKIP] {name}: weekday not allowed (today={today})`。
  - 增加可选参数 `--ignore-weekday`：当传入时忽略上述校验。
  - 若已启用 `pipeline_runs`，可记录 `status='skipped:weekday'`。

四、管理 CLI 变更
- 文件：`news-collector/write-deliver-pipeline/pipeline_admin.py`
  - `ensure_db()`：在启动时检测并补加 `weekdays_json` 列。
  - `export`：输出 `pipelines.weekdays_json`（解析为数组或 NULL）。
  - `import`：接受 `weekdays_json` 为数组或 JSON 字符串；不合法值忽略并按 NULL 处理。

五、前端配置改动（投放设置页）
- 交互与展示：
  - 增加“按星期运行”区块，7 个多选项：周一…周日（数值 1–7）。
  - 快捷按钮：
    - “全选”（1–7）。
    - “仅工作日”（1–5）。
    - “仅周末”（6–7）。
  - 占位文案与提示：未选择任何项时将“永不按星期触发”，常用于临时停发；如需完全停用建议配合 `enabled=0`。
- 存储格式：以 JSON 数组持久化到 `pipelines.weekdays_json`；全选可存 `NULL` 以节省与表意（前端可选项：“不限制星期”）。
- 校验：
  - 允许空数组；显示明显提示。
  - 不允许出现 1–7 之外的值。

六、日志与可观测性
- 当被星期限制跳过时，Runner 标准输出打印：
  - `[SKIP] <pipeline_name>: weekday not allowed (today=7; allowed=[1,2,3,4,5])`
- 可选：在 `pipeline_runs` 记录一行，便于统计“被跳过次数”。

七、测试用例（建议）
- 单元测试：
  - 输入 `weekdays_json=NULL`：任意星期放行。
  - 输入 `[]`：任意星期拒绝。
  - 输入 `[1,2,3,4,5]`：周一–周五放行，周末拒绝。
  - `PIPELINE_TZ` 变更能影响 `today` 判定（如将 UTC 与 Asia/Shanghai 对比）。
  - `--ignore-weekday` 开关生效时强制放行。
- 集成测试：
  - 准备一条启用的 pipeline，分别设置不同 `weekdays_json`，运行 `pipeline_runner.py --name <name>` 验证行为与日志。

八、非目标（本次不做）
- 不引入“按小时/时间段”与“日期节假日表”配置；后续如需可扩展 `pipeline_schedules`。
- 不改变现有“每日 09:30（北京时区）”的自动调度脚本逻辑，只有 Runner 侧决策是否执行。

九、示例（导出 JSON 片段）
```json
{
  "name": "feishu_broadcast",
  "enabled": 1,
  "description": "飞书卡片群发（工作日）",
  "weekdays_json": [1, 2, 3, 4, 5],
  "filters": { "all_categories": 0, "categories_json": ["game", "tech"], "all_src": 1 },
  "writer": { "type": "feishu_md", "hours": 40 },
  "delivery": { "kind": "feishu", "to_all_chat": 1, "title_tpl": "24小时最新情报" }
}
```

十、实现清单（拆解）
1) DB 迁移：为 `pipelines` 增加 `weekdays_json`；`pipeline_admin.ensure_db()` 幂等补列。
2) Admin 导出/导入：透传并规范化 `weekdays_json`。
3) Runner：新增星期校验与 `--ignore-weekday`；可选写入 `pipeline_runs`。
4) 前端：UI 勾选、快捷按钮、校验与保存；显示当前配置摘要（如“仅工作日”）。
5) 测试：单测与集成验证；更新文档与示例导出文件。
