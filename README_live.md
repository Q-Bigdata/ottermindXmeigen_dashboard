# 本机动态服务与接口

推荐在项目根目录运行 `python3 run.py --port 8878`。启动器校验运行数据，必要时恢复随仓库快照，提示输入Token，并验证MCP连接。

底层服务命令：`python3 live_server.py --port 8878`。真实刷新需要其环境中存在 `CHAT2DB_MCP_TOKEN`。服务只监听 `127.0.0.1` / `localhost`，不是公网部署配置。

| 接口 | 行为 |
|---|---|
| `GET /api/report` | 最近成功的完整报告；无报告503 |
| `GET /api/status` | 刷新状态、真实阶段、耗时、当前数据截止和共享设置 |
| `GET /api/settings` | 查询服务端共享自动开关 |
| `POST /api/settings` | `{ "auto_enabled": true/false }` |
| `POST /api/refresh` | 必须传 `{ "trigger": "manual" }` 或 `auto`；可选含时区的 `cutoff` |
| `POST /api/refresh/cancel` | 停止本服务启动的当前刷新进程组 |

自动更新由服务统一安排，默认关闭；开启后等5分钟开始，任务完成后再等5分钟。旧标签页空 `{}` 的自动请求会被拒绝。所有标签页共用设置，关闭开关只停止后续自动任务；当前任务需单独取消。

每批默认截至当前时间减120秒；起点保持全部历史。当前接口状态有idle/running/cancelling/cancelled/succeeded/failed。实际进度由pipeline各阶段写入，不使用虚构百分比。

新数据全批成功且截止一致后原子替换report.json。失败、超时、取消保留旧数据；前端显示旧截止与状态，不能将缓存显示为已更新。每次更新可能需要数分钟。

使用Ctrl+C停止服务。服务会结束自己启动的任务。凭证只在环境中，前端与HTTP状态均不包含Token或原始服务错误输出。
