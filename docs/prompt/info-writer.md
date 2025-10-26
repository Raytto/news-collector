manager 下的 info_writer.py 脚本需要支持参数设置近X小时，比如近24小时。
根据近 X小时参数去 info.db 的 info 表中过滤信息（只找符合时间条件的信息）
把符合条件的信息（来源、发布时间、标题、链接）整理成一个 markdown 格式的方便阅读的形式到 data/output 目录下，文件名为 YYYMMDD-HHMMSS-info.md (其中YYMMDD那些替换为当前实际的)