# Weekday Gating Refactor Spec（按星期运行重构方案）

本方案基于近期在“按星期运行”能力落地过程中的问题复盘，提出面向 AI 编程与多人协作更友好的工程化改进，目标是一次性把语义、契约、分层、测试与可观测性打通，降低往后类似需求的反复成本。

## 1. 背景与痛点

- 症状：前端勾选周二–周五保存后变为“不限制”，编辑页回显为空，列表摘要不准确。
- 根因：
  - 多源真相：Form 字段值、Checkbox.Group 内部状态与快捷按钮赋值未统一，提交时只传了 `'5'` 而非 `[2,3,4,5]`。
  - 契约不清：后端既使用 `PUT` 又 `exclude_unset`，导致“缺省/NULL/[]”语义混乱；Pydantic 校验与容错逻辑混杂。
  - 观测不足：前后端缺少提交载荷与原始 JSON 的调试日志，问题不可见。
  - 关注点交叉：领域语义（星期集合）散落在路由、DAO、前端组件各处，修一处会漏另一处。

## 2. 目标与非目标

### 2.1 目标
- 明确“按星期运行”的单一语义与三态表示；无二义性。
- 以“单一领域模块”承包所有解析、校验、序列化与摘要标签逻辑，前后端各一份、接口一致。
- 强契约（OpenAPI/JSON Schema），前端类型生成，禁止字符串等非规范输入进入业务层。
- 路由/Service/DAO 分层清晰，更新语义（PUT vs PATCH）一致且文档化。
- 受控表单 + 保存后回读，确保 UI 与 DB 一致。
- 提供覆盖关键路径的自动化测试与可观测日志（可开关）。

### 2.2 非目标
- 本次不改现有 DB 存储为 bitmask（保持 `TEXT JSON`），仅预留接口。
- 不实现“按小时/时间段”与“节假日表”，后续可在同一模式扩展。

## 3. 术语与语义

- 星期编号：ISO 周，`1=周一 … 7=周日`。
- 三态语义（字段：`pipelines.weekdays_json`）：
  - `NULL` → 不限制（与旧行为兼容）。
  - `[]` → 永不按星期触发（更细粒度停止）。
  - `[1..7]` → 仅当今天属于该集合时运行。
- 时区：默认 `Asia/Shanghai`；可由 `PIPELINE_TZ` 覆盖。

## 4. 数据层与迁移

- 继续使用 `pipelines.weekdays_json TEXT` 保存 JSON 数组或 `NULL`。
- 迁移（已存在）：`ALTER TABLE pipelines ADD COLUMN weekdays_json TEXT`（幂等）。
- 预留 bitmask 方案（可选）：新增 `weekdays_mask INTEGER`，`to_mask()/from_mask()` 映射；本次不落地。

## 5. 领域模块（后端 + 前端同构）

新增 WeekdaySet 模块，作为唯一的语义与算法来源。

后端（`backend/domain/weekday.py`）导出：
- `parse(value: Any) -> list[int] | None`：接受严格输入（数组 | null），便于路由层柯里化；保留一个 `coerce(...)`（仅兼容阶段，用于从字符串/CSV/数字宽入）。
- `normalize(days: list[int] | None) -> list[int] | None`：排序、去重、过滤非法。
- `is_allowed(days: list[int] | None, dt: datetime, tz: str) -> bool`：Runner 用。
- `to_tag(days) -> str`：`每天/工作日/周末/不限制/不按星期/自定义`。
- `to_mask(days) -> int`、`from_mask(mask: int) -> list[int]`：给未来存储方案预留。

前端（`frontend/src/lib/weekday.ts`）导出同名 API，保持一致。

使用点：
- 路由层仅调用 `parse/normalize`；DAO 层只收敛 `str <-> list[int]|null` 的序列化；列表摘要调用 `to_tag`；Runner 调 `is_allowed`。

## 6. API 契约（OpenAPI / JSON Schema）

采用 HTTP 方法与更新语义一致化：
- `POST /pipelines`（新建）必须给出完整字段（或具备默认）；
- `PATCH /pipelines/{id}`（推荐）仅修改出现的字段：
  - 缺省（missing）：不变；
  - `null`：不限制；
  - `[]`：永不按星期触发；
  - `[1..7]`：限制集合；
- 如保留 `PUT`，则定义为“完整覆盖”，但前端应改用 `PATCH`。

OpenAPI 片段（核心字段）：
```yaml
PipelineBase:
  type: object
  properties:
    name: { type: string, nullable: true }
    enabled: { type: integer, minimum: 0, maximum: 1, default: 1 }
    description: { type: string, nullable: true }
    weekdays_json:
      oneOf:
        - type: array
          items:
            type: integer
            minimum: 1
            maximum: 7
        - type: 'null'
```

错误码：
- 422：`weekdays_json` 非数组/越界；
- 400：语义冲突（例如 `PUT` 且缺字段，拒绝）。

过渡期兼容：
- 路由层可使用 `coerce(...)` 宽入字符串（`"2,3,4,5"`/`"[2,3]"`/`"5"`），解析后打 `WARN` 日志；在 2 个小版本后移除宽入逻辑。

## 7. 分层与职责

- 路由层（FastAPI）：鉴权、解析 body → DTO、容错（兼容期）、调用 Service。
- Service 层（应用服务）：DTO → 领域对象（`WeekdaySet.normalize`）、业务校验、调用 DAO。
- DAO 层：SQL + JSON 序列化/反序列化；无业务判断。

日志（dev-only，可由 env 控制）：
- `DEBUG_PAYLOAD=1` 时：打印原始 JSON 与 DTO（已打码敏感字段）。
- `DEBUG_WEEKDAY=1` 时：打印解析后的 `weekdays_json` 与最终写库值。

## 8. 前端表单与交互

- Checkbox 周选择为“受控组件”：
  - 本地 `weekdaySelection: number[] | null` 为单一真相；
  - onChange 与快捷按钮都更新该 state 与 form-field 值；
  - 保存时仅从 state 生成 payload，保证数组形态一致。
- 保存成功后 re-fetch 回灌表单（当前已实现）。
- 列表“星期”列调用与后端同语义的 `toTag`（或后端返回摘要），可选 tooltip 展示明细（如 `2–5`）。

## 9. Runner 与 Admin CLI

- Runner gating：统一调用 `WeekdaySet.is_allowed(today, tz)`；当被限制跳过时标准输出 `[SKIP] name: weekday not allowed (today=..., allowed=[...])`。
- Admin CLI 导出/导入：透传 `weekdays_json`，严格数组/`null`，拒绝字符串；兼容期支持宽入并警告。

## 10. 自动化测试

后端（pytest + TestClient）：
- `PATCH /pipelines/{id}`：
  - `null` → DB NULL；
  - `[]` → DB "[]"；
  - `[2,3,4,5]` → DB "[2,3,4,5]"；
  - 缺省 → DB 不变；
  - 宽入字符串（兼容期）→ 解析后写入并记 `WARN`。
- `GET /pipelines/{id}` 回显与 DB 一致；列表摘要 `weekday_tag` 正确。
- WeekdaySet 单元测试：`parse/normalize/is_allowed/to_tag` 覆盖。

前端（Playwright/Cypress）：
- 勾选 2–5 → 保存 → 列表显示“自定义” → 编辑页回显勾选；
- 点击“不限制” → 保存 → 列表“不限制” → 编辑页空勾选；
- 点击“清空” → 保存 → 列表“不按星期” → 编辑页空勾选。

## 11. 迁移与发布

1) 保持 DB 列不变（已存在）。
2) 落地 WeekdaySet 模块（后端/前端）。
3) 路由改造：新增 `PATCH`；保留 `PUT` 一段时间但禁用 `exclude_unset` 或拒绝不完整 PUT。
4) 前端切换为受控组件 + 使用 `PATCH`。
5) 打开 `DEBUG_*` 在开发环境观测 1–2 天；回收日志。
6) 加入 CI：后端集成测试 + 前端 E2E 最小集。

风险与回滚：
- 若上线后出现保存异常，可临时打开宽入解析与 DEBUG 日志快速定位；必要时回退到旧路由但保留受控表单。

## 12. 验收标准（DoD）

- 编辑页勾选 2–5 保存后，立刻回显一致；刷新仍一致。
- 列表“星期”列与 DB 一致；
- API 文档中 `weekdays_json` 仅为数组或 `null`；
- 关键测试（后端 + 前端）通过；
- Runner 在周末对“工作日”管线输出 `[SKIP]` 日志并不执行。

## 13. 附录

### 13.1 WeekdaySet 伪代码

```python
def normalize(days: list[int] | None) -> list[int] | None:
    if days is None:
        return None
    xs = sorted({int(x) for x in days if 1 <= int(x) <= 7})
    return xs

def is_allowed(days: list[int] | None, now: datetime, tz: str = 'Asia/Shanghai') -> bool:
    if days is None:
        return True
    if not days:
        return False
    today = datetime.now(ZoneInfo(tz)).isoweekday()
    return today in days

def to_tag(days: list[int] | None) -> str:
    if days is None:
        return '不限制'
    if not days:
        return '不按星期'
    if days == [1,2,3,4,5,6,7]:
        return '每天'
    if days == [1,2,3,4,5]:
        return '工作日'
    if days == [6,7]:
        return '周末'
    return '自定义'
```

### 13.2 示例 Payload

```json
// PATCH /pipelines/9
{
  "pipeline": {
    "enabled": 1,
    "weekdays_json": [2,3,4,5]
  }
}
```

```json
// 不限制
{
  "pipeline": {
    "weekdays_json": null
  }
}
```

```json
// 永不按星期触发
{
  "pipeline": {
    "weekdays_json": []
  }
}
```

