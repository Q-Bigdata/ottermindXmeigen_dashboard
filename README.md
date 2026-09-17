# MeiGen 行为与增长分析 Dashboard

面向 Ottermind 的 MeiGen 入口专项分析：从来源需求、任务推进、路径断点到回访、复用和商业转化。原生 HTML/CSS/JavaScript 前端，Python 动态数据服务，通过 Chat2DB MCP 只读查询 Umami。

本仓库为**私有项目**。随仓库的 `runtime/seed-data.zip` 包含恢复动态运行所需的当前数据与已审历史证据，包含访问和账户关联数据；请保持仓库及其派生数据包的访问范围。

## 启动动态项目

```bash
git clone <本仓库地址>
cd meigen-full-history
python3 run.py --port 8878
```

首次启动会自动校验并解压 `runtime/seed-data.zip`，已有数据不会覆盖。随后在终端提示输入 Chat2DB MCP Token，Token不会写入文件。打开终端打印的 `http://127.0.0.1:8878/`。

- Python 3.10+，业务运行只使用标准库，无前端构建步骤。
- Chat2DB 需保持运行；默认 MCP 为 `http://127.0.0.1:11924/mcp`，数据源 ID `202172`，数据库 `umami.public`。
- `CHAT2DB_MCP_URL`、`CHAT2DB_DATASOURCE_ID` 可以在启动终端环境中覆盖。也可提前设置 `CHAT2DB_MCP_TOKEN`。
- 页面首先显示最近成功的报告；点击“刷新数据”后重新查询与计算。每批截止为当前时刻减2分钟。
- 自动更新由服务统一调度、所有标签页共享，默认关闭；取消或失败保留上次完整数据。
- macOS 的系统 Python 较旧时，启动器会寻找本机已安装的 Python 3.10+。Windows 缺时区数据库时可安装 `tzdata`。

仅检查连接：`python3 run.py --check`。只看已有报告：`python3 run.py --cached-only --port 8878`。

详细步骤见 [START_DYNAMIC.md](START_DYNAMIC.md)。

## 功能

- 需求 × 行为成熟度矩阵，区分访问画像与账户累计状态。
- 可点击流程轴与路径断点总览：到达、继续、其他去向、未继续和耗时。
- 按需求、入口、设备、浏览器、地区、语言和日期筛选。
- 六类同起点分岔：前置体验、复杂度、耗时、停止状态与代表路径。
- 首24小时体验与随后7/14日的回访、再使用和功能扩展。
- 首次/重复使用到价格、权益、结账的有序比较。
- 有具体证据、实验、主指标与护栏的优化策略。

使用记录是任务提交代理；生成完成、下载完成和支付仅在可关联证据支持时使用。停止不直接等于产品问题，也不等于永久流失。

## 代码结构

| 位置 | 内容 |
|---|---|
| `report/` | 动态页面源码；保存后刷新浏览器即可 |
| `live_server.py`, `refresh_pipeline.py` | API、共享刷新、取消与批次发布 |
| `mcp_client.py`, `extract_*.py` | 只读MCP连接与数据提取 |
| `analytics_*.py` | 流量、需求、路径、回访与深度对照 |
| `public_payload.py` | 前端聚合与无身份筛选数据 |
| `runtime/` | 已校验的当前运行快照与历史证据包 |
| `data/`, `generations/` | 首次启动恢复、以后更新产生的本地数据，不追踪变更 |
| `qa/`, `test_*.py` | 浏览器与数据/服务检查 |
| `dist/` | 本地生成的静态快照，不是动态开发源码 |

修改指南：[EDITING_AND_PUBLISHING.md](EDITING_AND_PUBLISHING.md)。
策略依据：[STRATEGY_UPDATE.md](STRATEGY_UPDATE.md)、[deep_comparison_findings.md](deep_comparison_findings.md)。

## 检查与导出

```bash
python3 -m unittest discover -s . -p 'test_*.py'
node --check report/app.js
python3 validate_results.py data/report.json
python3 export_static.py
```

最后一条生成 `dist/meigen-dashboard/` 与 ZIP，保留交互但不查数据库。动态运行请访问 Python 服务地址，不要用静态 Live Server 打开 `dist/`。

浏览器QA额外需要 Node.js 与 Playwright；可执行 `npm install`、`npx playwright install chromium` 后运行 `npm run test:ui:strategy`。环境变量 `CHROME_PATH` 可指定已安装Chrome，默认使用Playwright自带Chromium。QA默认连接 `http://127.0.0.1:8876`。

## 数据快照与复现

代码只追踪一次精简运行包；没有把数GB的重复运行日志、失败提取和历史缓存加入Git。`runtime/manifest.json`列出每个文件的校验和。

```bash
python3 restore_data.py
```

恢复后可运行当前报告，以及已审阅的历史基线脚本。新的刷新结果保存在 `generations/<generation_id>/`；当前有效版本由 `data/report.json` 指定。运行时凭证、`.env`、机器路径、依赖目录和更新生成的数据均不提交。

如需更新仓库随附快照，确认新批次完整后执行 `python3 pack_runtime.py`，审查数据内容，再提交更新后的压缩包和manifest。不要把Token、用户输入内容或新增敏感字段加入仓库。
