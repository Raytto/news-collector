针对 scripts/auto-do-scripts.sh（已迁移为 DB 驱动）
职责：
1. conda activate `news-collector`
2. 运行 `collector/collect_to_sqlite.py` 拉取最新数据到 SQLite
3. 运行 `evaluator/ai_evaluate.py`（默认 40h）写入 AI 评估
4. 初始化并（幂等地）seed 管线：`write-deliver-pipeline/pipeline_admin.py init|seed`
5. 调用 `write-deliver-pipeline/pipeline_runner.py --all` 执行所有启用的管线（writer 与 deliver 均从 DB 自动读取配置）
6. 等到明天早上 10:30 再从 1 重新执行

说明：
- 旧流程（直接调用 `writer/info_writer.py`、`deliver/mail_today.py`、`deliver/feishu_bot_today.py`）已收敛为 DB 管线，便于按订阅人/场景集中配置。
- 具体的写作窗口、类别过滤、权重与投递目标等均维护在 `data/info.db` 的 pipeline 表中；runner 会为子进程设置 `PIPELINE_ID` 环境变量，writers/deliverers 会以此自取配置。
