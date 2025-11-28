# slg-scout-&-analyst 项目解析

## 1. 产品目标与定位
- 目标：监测指定 YouTube 频道在固定时间窗（昨日 09:31 - 今日 09:29）发布的 Android Gameplay 视频，识别可用于《万国觉醒》/《万龙觉醒》的 Minigame 副玩法，并生成可推送的情报报告。
- 形态：纯前端（React + Vite + Tailwind CDN），无自建后端；依赖 Gemini 模型和公共 CORS Proxy 访问外部接口（YouTube RSS、Feishu）。
- 结果：生成包含候选游戏列表、市场总结、来源 Grounding 的报告，可在页面查看并一键推送飞书群。

## 2. 核心模块与职责
- `App.tsx`：页面状态与交互入口，触发报告生成与飞书推送，管理加载/错误/日志状态，渲染报告组件。
- `services/geminiService.ts`：数据采集 + AI 生成。负责时间窗计算、RSS 抓取、Prompt 组装、Gemini 调用、结果解析与来源提取。
- `services/feishuService.ts`：飞书广播。获取 tenant token、列出机器人所在群、组装交互卡片并逐群发送（全部经 CORS Proxy）。
- 组件层：`AnalysisHeader`（报告头与总结）、`GameCard`（单游戏分析卡片）、`SourceList`（Grounding 来源）。
- 类型定义：`types.ts` 描述游戏、报告、Grounding 结构，便于跨模块传递。

## 3. 数据管线（生成报告）
1. **触发**：用户点击“生成昨日情报”→ `handleGenerate`。
2. **时间窗计算**：`generateMarketReport` 设定 start=昨天 09:31，end=今天 09:29，用于过滤 RSS 与 prompt 展示。
3. **采集（优先 RSS）**：`fetchRSSUpdates` 依次请求预设频道（`TARGET_CHANNELS`），经 `CORS_PROXY` 访问 `https://www.youtube.com/feeds/videos.xml?channel_id=...`，用 DOMParser 解析 XML；仅保留时间窗内的视频。
4. **策略选择**：
   - 若 RSS 有数据：策略 A，直接将抓取的视频列表写入 Prompt，要求 Gemini 做玩法分析、可行性评分并猜测 Play Store 链接。
   - 若 RSS 为空或失败：策略 B，改为搜索模式，提示 Gemini 用 googleSearch 工具搜索软启动视频，筛选时间窗后再分析。
5. **AI 调用**：`GoogleGenAI().models.generateContent`，模型 `gemini-2.5-flash`，工具 `googleSearch`，温度 0.2。Prompt 要求输出 JSON。
6. **结果解析**：尝试从 ```json fenced block 解析；否则直接 `JSON.parse` 全文。映射为内部 `ReportData`，并为缺失链接补充搜索 URL。
7. **Grounding 来源**：从 `response.candidates[0].groundingMetadata.groundingChunks` 收集 `title/uri`，用于前端来源列表。
8. **呈现**：页面展示分析头、候选游戏卡片、来源列表。若空列表提示“指定时间段无更新”。

## 4. 飞书推送流水
- **鉴权**：`getTenantAccessToken` 调用飞书 tenant token API。先尝试直连（通常被 CORS 拦截），失败后经公共 `CORS_PROXY` 代理。
- **群列表**: `getBotGroups` 用 token 通过代理获取机器人所在群（page_size=100）。若为空则直接报错。
- **卡片生成**：`createFeishuCard` 基于 `ReportData` 拼装交互卡片，包含总结、每个游戏的玩法/评分/按钮（YouTube/Play）。
- **广播发送**：`broadcastToFeishu` 遍历群聊，通过代理 POST 消息接口。结果以 `{name,status}` 返回，前端展示发送日志与成功/失败状态。
- **重要限制**：公共 proxy 不稳定且需用户提前访问 `https://cors-anywhere.herokuapp.com/demo` 开权限；未部署后端时易因 CORS 导致失败。

## 5. 前端交互与 UI
- 状态流：`loading`（骨架动画）→ `report`（结果态）/`error`（重试按钮）。推送过程有独立 `pushStatus` 与日志控制台。
- 视觉：暗色 Tailwind，渐变卡片与分栏卡片布局；移动端提供独立推送按钮。
- 入口提示：在结果为空之前提示用户必须先开启 CORS Proxy 权限，否则 RSS/推送都会失败。

## 6. 配置与运行要点
- 依赖：`react@19`、`@google/genai`、Vite；Tailwind 走 CDN，无本地构建样式。
- API Key：代码读取 `process.env.API_KEY`；README 指向 `.env.local` 的 `GEMINI_API_KEY`，需要统一（建议用 Vite 前缀 `VITE_GEMINI_API_KEY` 并在服务层读取）。
- 频道名单：`TARGET_CHANNELS` 目前为占位 ID/名称，需替换为真实监测目标。
- 本地运行：`npm install` → `.env.local` 写入 API Key → `npm run dev`。

## 7. 已知风险与改进方向
- CORS 依赖公共 Proxy：无 SLA，可能频繁失败；飞书/YouTube 请求在生产应改由后端代理。
- 数据可信度：RSS 失败时完全依赖 Gemini 搜索 + 自报时间，缺少额外校验；可考虑服务器侧抓取与时间戳验证。
- JSON 解析脆弱：模型若输出非规范 JSON 会被兜底为空；可加正则/JSON5 解析或重试逻辑。
- 安全性：飞书 `APP_SECRET` 明文写在前端代码，需迁移至安全配置或后端。
- 可观测性：当前仅前端日志，无持久化记录；可在后端增加运行日志和告警。
