在 `news-collector/collector` 目录下编写脚本，使其能运行一遍 `collector/scraping` 目录下的所有信息源，并把信息保存到 sqlite 中（在 `data` 目录下放一个 info.db）。
需要包含列：
- source:来源
- publish:发布时间,精确到秒
- title:标题文本
- link:链接
加入时需要注意去重（仅按 link 链接去重，link 相同视为重复）
