manager 下的 info_writer.py 脚本需要支持参数设置近 X 小时（默认 24 小时）。

根据近 X 小时参数，从 info.db 的 info 表中过滤信息（仅保留“发布时间 publish 能被解析为时间且位于窗口内”的记录）。

将符合条件的信息（来源、发布时间、标题、链接）输出为便于阅读的 HTML 文件，保存到 data/output 目录下，文件名格式：

- YYYMMDD-HHMMSS-info.html（例如 20251026-142355-info.html）

HTML 输出规范：
- 文档结构：
  - <!doctype html>
  - <html lang="zh-CN">，<meta charset="utf-8">，<title>最近 X 小时资讯汇总</title>
- 头部信息：页面顶部展示“生成时间（UTC）”与“合计条数”。
- 分组展示：按来源 source 分组，每组使用 <h2>source</h2> 作为小节标题。
- 条目格式：按发布时间倒序列出为无序列表 <ul>；单条为：
  - <li><time datetime="ISO8601">YYYY-MM-DD HH:MM UTC</time> — <a href="URL" target="_blank" rel="noopener noreferrer">标题</a></li>
  - time 元素的 datetime 使用 ISO-8601（如 2025-10-24T14:27:00+00:00）
- 字符集：UTF-8；可在 <head> 中内联一小段 CSS 以提升可读性（如字号、行距、颜色）。

排序与筛选：
- 仅包含能够解析为时间的 publish 值；其余记录忽略。
- 各来源分组内按发布时间（降序）排序；分组按来源名（字母序）排序。

可选增强（非强制）：
- 在页面顶部加入来源统计（每个来源的条目数）。
- 支持 --since 绝对时间参数（如 2025-10-24T00:00:00Z）作为 --hours 的替代。
