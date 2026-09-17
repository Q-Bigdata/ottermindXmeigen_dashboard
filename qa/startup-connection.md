# 独立动态启动验证

2026-09-16：run.py --check通过项目完整性、MCP握手和只读SELECT current_database()/TimeZone查询。确认umami、Asia/Shanghai，未执行正式分析全刷新。凭证仅来自进程环境，无Token写入证据。

/usr/bin/python3为3.9.6时，启动器成功切换本机已有3.13。复制项目到独立临时目录、清除PYTHONPATH、无Token重算同截止的保存数据成功：34508入口、3123账户；新端口API/页面正常。参见standalone-results.json。

项目目录和父目录均无Git仓库；本轮在原工作目录维护代码，未擅自初始化文档集合仓库、未创建本地提交或远端操作。
