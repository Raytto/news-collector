针对 scripts/auto-do-scripts.sh
负责：
1. 先 conda activate news-collector
2. 用 collect_to_sqlite.py 拉取最新数据到 db
3. 用 info_writer.py 写最近24小时的消息摘要到 data/output 下的 YYMMDD-HHMMSS-24h-info.html 中 （此文件名在 sh 脚本中指定，只是如果没指定 python 再用当前的作为默认,可能需要修改 info_writer.py）
4. 参考 mail-today.md 发送刚才写的 YYMMDD-HHMMSS-24h-info.html 内容为邮件 给 306483372@qq.com (需要同时补全 mail_today.py)
5. 等到到明天的早上 10:30 再从1重新执行