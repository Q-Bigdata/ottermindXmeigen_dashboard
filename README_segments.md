# MeiGen 全历史趋势、需求与画像模块

`analytics_segments.py` 是无数据库依赖、仅使用 Python 标准库的可复算模块。不读取凭证，不修改数据库。入口：

```python
from analytics_segments import analyze_segments
result = analyze_segments(visits, events,
    observation_start="2026-08-12T21:24:36.457+08:00",
    observation_end="2026-09-16T07:10:00Z")
```

也支持命令行，完整参数见 `python analytics_segments.py --help`。输入为 `data/visits.json` / `data/events.json` 两个对象列表。显式时区优先；无时区时间按提取契约解释为 UTC。显示与日汇总均用北京时间；截点为左闭右开。

## 输入字段

Visits：visit_id、start_at、entry_path、utm_source/referrer_domain。可选 initial_demand、utm_content/campaign/medium、device/browser/country/language、account_id、identity_conflict、source_evidence。明确的 `initial_demand` 优先于本模块路由规则，保留具体类别 `blur/product_image/product_video/video_edit/watermark/ppt/model_explore/studio_task/other`。

Events：visit_id、created_at、event_id、event_name、event_type、url_path、properties。按 event_id 去重；不凭时间相同自动去重。注册与使用先后要求严格大于，时间并列不强制补顺序。

## 输出

- `trends`：全历史自然日和有流量小时、首/末不完整日标记、日注册/使用/下载意图/价格/结账/支付到达、突增候选。无流量日保留。
- `maturity`：单次访问需求 × 最深状态矩阵、阶段到达、来源证据、卡片与落地映射、匹配异常。可关联账户只汇总 MeiGen 来源访问内累计；跨来源的后续行为由账户随访模块覆盖，明确分开。
- `cards`：只比较同一具体需求内不同卡片；列原始样本、从较晚首见卡片精确时间开始的共同日期 × 设备 × 归一入口子样本、共同覆盖率和对共同合并结构标准化后的差异。输出分母、Wilson区间、Newcombe原始差值区间和标准化近似区间。

## 行为定义

使用唯一代理为 `send_message`；`guest_started` 独立计数，不当作已使用。注册是 `sign_up` 或 `guest_signup`，`login` 不算注册。支付到达是 `purchase`，价格/结账另列，不混为充值。支付事件交易去重、账户归因由支付模块负责。

“当次重复使用”为至少两个不同时间戳的去重 `send_message`（同刻两条不算复用），不代表另一日回访或任务成功。“注册后使用”是在当次注册信号后严格更晚的 send_message；不覆盖此前已注册的账户。下载只记录 guest gate / guest signup / paywall 的 download 场景，命名为“下载意图”，不推断生成或下载完成。

`pending_30m` 仅由 `start_at + 30分钟 > 截点` 判定。旧的短访问有完整观察机会；没有后续事件不构成观察不足。模块只提供画像到达，不计算流程轴退出率；逐节点有序推进由分岔模块负责。

## 流量突增与对照

基线：前7个自然日中可完整观察日的访问量中位数与 MAD，至少3日。候选要求增量≥30、倍率≥1.5，且 MAD 非零时 robust z≥2。首/末不完整日不参与突增判定。贡献分解用前7日均量保证各维度增量可加，明确区别于用于筛选的中位数。

每个候选列卡片、具体需求、设备、国家、语言、来源证据的当前量、基线日均、净增量和正增量占比，并列下游注册/使用/价格/结账变化及当天小时分布，生成具体日期的运营回查线索。运营/产品动作是待回查机制，不由突增直接推出原因。

日行为按当日入组访问截至冻结时点的已观测结果汇总，不是固定30分钟转化。卡片对照排除冻结点前最后30分钟的新访问，按共同日期/设备/入口标准化；差异仍为观测关联，未主张随机试验。

## 本轮实际数据适配

矩阵添加“主动付费意图”“进入结账”：前者仅 plan_clicked / paywall_cta_click / begin_checkout / checkout_opened，后者仅 begin_checkout / checkout_opened；paywall_shown 不算主动意图。注册、使用、复用与付费意图仍保留独立 reached_flags，展示最深状态不代表真实固定时序。需求支持 video_generation=视频生成。

`quality_eligible=false` 的访问仍保留在量级及画像矩阵中，`_summary` 的行为分子与分母、卡片对照排除它们。`behavior_denominator`、`quality_excluded` 显式输出。矩阵状态总量使用所有访问，因而其最深状态分组可能与排除了4条的行为到达统计略有差异。

在 MAD 突增外增补 `traffic_jumps`（前后完整日增量≥150且≥15%的前5项）与 `usage_breaks`（相对前三完整日使用率下降至少一半、有效分母和使用次数满足阈值，3日内不重复报同一断点），避免仅看流量异常而漏掉承接变化。新卡首次出现只是数据首见，非已核实上线时间。
