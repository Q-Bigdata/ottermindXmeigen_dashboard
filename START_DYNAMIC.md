# 在 VS Code 中启动完整动态项目

你应打开整个 `meigen-full-history` 文件夹。截图中的 `dist/meigen-dashboard/app.js` 属于静态发布包；日常修改与动态运行使用 `report/`。

## 第一次启动

1. 打开 Chat2DB，确保其 MCP 服务已启动（本项目默认 `http://127.0.0.1:11924/mcp`），并且 `umami` 数据源可连接。
2. 在 VS Code 菜单 **终端 → 新建终端**，确认当前目录为 `meigen-full-history`，可以看到 `run.py`、`live_server.py`、`report/`、`data/` 和 `generations/`。
3. 运行：

```bash
python3 run.py --port 8878
```

4. 按提示粘贴 **Chat2DB MCP Token** 并回车。输入时不会显示字符，Token仅在当前服务进程内使用，不写入代码或磁盘。
5. 出现 `MCP连接通过`、`MeiGen local report: http://127.0.0.1:8878` 后，用浏览器打开该地址。

使用8878是为了避开Codex先前启动的8876实例。若你已关闭旧服务，也可以指定 `--port 8876`。

项目仅依赖 Python 标准库，不需要 `npm install`、`npm run dev` 或 `pip install`。Python需要3.10+；启动器若发现macOS自带3.9，会优先寻找电脑中已有的较新Python，并自动切换。

## 怎样确认动态功能正在工作

- `http://127.0.0.1:8878/api/status` 应返回服务状态。
- 页面首先显示最近一次完整报告，这是动态服务的缓存，不是静态模式。
- 点击 **刷新数据**，服务从MCP查询、重算并更新“数据截至”；一次重算可能需要数分钟，期间显示真实阶段，保留上一批完整结果。
- 自动更新默认关闭；开启后所有标签页共用设置，每次完成后5分钟再更新。
- 点击 **取消本次** 可停止正在执行的更新；不会清空已有报告。
- 终端需要保持运行；`Ctrl+C`停止服务。关闭终端后网页API就无法访问。

只检查项目和数据库、不启动服务：

```bash
python3 run.py --check
```

## 修改代码

动态前端：`report/index.html`、`report/styles.css`、`report/app.js`；优化策略在 `report/strategy.js`，深入对照在 `report/deep_panels.js`。

保存HTML/CSS/JS后刷新浏览器即可；Python后端修改后，终端 `Ctrl+C`，再运行上面的启动命令。

不要只改 `dist/meigen-dashboard`，它是导出副本，动态服务不读取该目录。`report/config.js` 应保持 `mode:"live"`、`reportUrl:"/api/report"`。没有必要把静态包的mode改成live来运行——必须启动Python服务才能获得API与刷新能力。

## 复制到别处时

整个文件夹可以独立放在其他目录。连接模块 `mcp_client.py` 已包含在本项目内，不再依赖外层 `analysis/pipeline/`。

从GitHub克隆后，保留 `runtime/` 中的快照包；`run.py` 首次运行会自动校验并恢复 `data/report.json` 与完整generation。已有工作副本也可直接复制整个项目。源事件与账户历史是增量刷新的起点，不能只复制页面。

如果你此前复制了旧版本到别处，请同步更新本轮新增/修改的 `run.py`、`mcp_client.py`、`refresh_pipeline.py`、`extract_data.py`、`extract_accounts.py`、`extract_visit_context.py`，以及 `report/`。GitHub仓库已包含这些文件；旧工作副本可更新源码，保留自己已有的数据目录。

## 可选连接配置

默认MCP地址：`http://127.0.0.1:11924/mcp`；数据源ID：`202172`；数据库/schema：`umami.public`。

若你的Chat2DB使用不同地址或重新创建了数据源，可在同一个VS Code终端设置后启动：

```bash
export CHAT2DB_MCP_URL='http://127.0.0.1:11924/mcp'
export CHAT2DB_DATASOURCE_ID='202172'
python3 run.py --port 8878
```

也可在进程环境提前设置 `CHAT2DB_MCP_TOKEN`，启动器就不会再提示。不要把真实Token写入 `report/`、静态发布包或Git。远程MCP使用HTTPS，本机地址仍使用上方默认值。

## 常见问题

- **`run.py`找不到**：当前目录错误，或复制的是旧版本。先在终端 `pwd` / `ls` 查看，切换到含run.py的项目根目录。
- **端口已占用**：换成 `--port 8879`，或在原终端停止旧服务。不要反复启动同一端口。
- **MCP检查失败**：确认Chat2DB未退出、MCP已开启、端口正确、Token有效、umami数据源正常。
- **缺少当前完整数据版本**：把对应的generations目录一并复制。
- **仍显示“数据快照”**：打开的是dist或Live Server静态地址，改访问run.py打印的8878地址。
- **页面空白或旧脚本**：强制刷新浏览器；确认正在编辑report，不是dist。

也可以使用 **终端 → 运行任务 → 启动 MeiGen 动态服务**；项目的 `.vscode/tasks.json` 已配置相同启动命令。
