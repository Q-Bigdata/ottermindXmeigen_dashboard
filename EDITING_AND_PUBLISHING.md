# 自己修改与发布 Dashboard

项目位置：``。这是普通 HTML、CSS、原生 JavaScript 前端，以及 Python 数据服务；没有前端打包步骤。

## 修改页面

| 想修改什么 | 文件 |
|---|---|
| 页面骨架、顶部控件、指标说明 | `report/index.html` |
| 配色、卡片、字体、导航、流程轴箭头 | `report/styles.css` |
| 概览、需求矩阵、流程轴、按日统计及筛选交互 | `report/app.js` |
| 前端统计与筛选口径 | `report/data.js` |
| 图表画法 | `report/charts.js` |
| 历史专题 | `report/insights.js` |
| 优化策略、证据与实验 | `report/strategy.js` |
| 新增的继续/停止、回访、复用到商业推进视图 | `report/deep_panels.js`、`report/deep_panels.css` |
| 自动更新、手动更新、取消和进度显示 | `report/refresh.js` |

用代码编辑器打开项目目录，保存文件后在浏览器刷新页面即可看到修改。修改 JS/CSS 后若页面未变化，使用浏览器的强制刷新。先从配色、文案和卡片布局修改开始；更改比例计算时，保留分母、观察窗和输入数据的一致性。

前端读取 `data/report.json` 作为当前报告。不要直接手填统计数字到页面，也不要通过修改 JSON 来“修正”分析结果；应修改统计逻辑，再重新生成。

## 修改分析逻辑

- `analytics_segments.py`：需求、成熟度、流量变化与素材对照。
- `analytics_branches.py`、`analytics_breakpoint.py`：流程与有序分岔、历史变化对照。
- `analytics_accounts.py`：账户回访与跨访问复用。
- `analytics_deep_comparison.py`：继续/停止画像、回访三组、复用到结账的前置体验。
- `refresh_pipeline.py`：读取、计算、组装一次完整更新。
- `public_payload.py`：把统计压缩为前端需要的公开数据。

分析数据以每个 `generations/<generation_id>/` 文件夹保存。同一批的访问、事件、账户、时间截止必须一起使用。服务只在整批完成后替换报告；失败或取消保留旧版。

## 本地启动

需要 Python 3.10+，只查看缓存报告时不需要数据库凭证。

```bash
python3 run.py --port 8878
```

在VS Code打开meigen-full-history根目录后运行。打开 `http://127.0.0.1:8878/`；启动器会提示输入 `CHAT2DB_MCP_TOKEN` 并检查当前 Chat2DB MCP。详细说明见START_DYNAMIC.md。凭证不要写入前端代码、Git或发布包。

自动更新现在是服务统一控制，所有标签页共享，默认关闭。开启后，每次任务结束5分钟再安排下一次；关闭只停止后续自动任务。正在运行的任务可点击“取消本次”，不会清空上次完整数据。

## 发布方式

### 1. 静态快照版

适合分享分析、评审与不需要实时查库的阅读。保留全部页面、筛选和交互，显示快照截止时间；没有数据库或刷新接口。

在项目根运行：

```bash
python3 export_static.py
```

输出 `dist/meigen-dashboard/` 及同名 ZIP。把目录里的内容上传到任意静态网站托管服务即可，以 `index.html` 为入口。测试本地发布包：

```bash
python3 -m http.server 8877 --directory dist/meigen-dashboard
```

打开 `http://127.0.0.1:8877/`。数据更新后重新导出、替换发布目录。

### 2. 动态版

可以部署，但当前服务专门绑定本机，并默认使用本机 Chat2DB 地址，因此直接上传 HTML 不会得到可用的动态网站。实际部署需完成：

1. 在服务器或受控网络内运行 Python 服务与数据更新任务。
2. 给服务提供能到达的只读数据源连接；服务器的 `127.0.0.1` 不会指向你的电脑。
3. 通过 HTTPS 网关代理页面和 API，给内部分析配置访问权限。
4. 将 MCP 凭证保存在服务器环境/密钥配置中；为更新任务设置并发、超时、取消与日志。
5. 持久保存当前完整报告与更新代次，重启后继续展示最近成功的数据。

目前没有执行公网部署。静态 ZIP 已准备成可检查的发布材料；动态上线需根据你选择的服务器和访问范围配置。

## 修改后的验证

先检查这几项：流程节点箭头、矩阵换行、日期/需求筛选、空样本、同比较组分母、移动端横向滚动、更新/取消状态。

代码检查与已有测试：

```bash
node --check report/app.js
python3 -m unittest discover -s analysis/meigen-full-history -p 'test_live_server.py'
python3 -m unittest discover -s analysis/meigen-full-history -p 'test_deep_comparison.py'
```

`qa/` 中保留实际浏览器测试脚本和截图。`qa/revision_checks.cjs` 覆盖本次修订交互；执行需要项目现有Playwright与本机Chrome。
