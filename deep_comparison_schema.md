# 深度比较数据接口

调用方式：

```python
from analytics_deep_comparison import analyze_generation
# 在 analytics_accounts.py 完成后运行；要求账户与访问cutoff一致。
deep = analyze_generation(work)
save(work / 'data' / 'deep_comparison.json', deep)
payload['data']['deep'] = deep
```

`immediate` 为首次使用后的 30 分钟继续/未继续比较。通用比较结构：

```text
n, continued, stopped, rate, ci95
profiles.continued / profiles.stopped:
  n
  metrics.<boolean feature>: {n, denominator, rate, unknown}
  metrics.<numeric feature>: {median, n}
feature_rates[]:
  feature, label, value, n, continued, stopped, rate, ci95,
  continued_group_share, stopped_group_share
paths.continued / paths.stopped: [{steps: string[], n, group_share}]
by_demand[]: {demand, ...相同通用比较结构}
```

`retention.windows[]`：`days` 为 7 或 14；顶层人数含 `eligible_accounts`、`starters`（首24小时已使用）、`no_return`、`browse_only`（回访未再使用，不一定有页面）、`reused`、`returned`、`newly_activated`、`initial_nonusers`。率为 `return_rate`、`reuse_rate`、`return_to_use_rate`。`groups[]` 为三组，包含 `group,n,share,metrics`（首24h前置画像）、`followup_actions`（后续窗口内，仅描述场景）、`return_landing`、`paths`。`comparison` 为再次使用 vs 未再次使用的通用比较结构。`by_demand[]` 同时包含通用比较、三组画像和人数/率，便于需求筛选。

`commercial.ordered_transitions[]`：`anchor` 为 `first_use/repeat_use`，`target` 为 `pricing/intent/checkout`；含 `n,reached,rate,prior_target_excluded,observation_incomplete,median_seconds,by_demand`。各目标独立分母，不能当作同一线性漏斗相减。

`commercial.landmarks[]`：`minutes` 为 2 或 5，`target` 为 `intent/checkout`；通用比较结构加 `early_target_excluded`。`feature_rates` 中 `repeat_early`=true/false 为先冻结前置特征、再观察后续结果的分组，适合展示重复与未重复的意图/结账率。

`commercial.checkout_experience`：`n,without_preuse,median_pre_sends,median_first_use_to_checkout_seconds,prior_pricing,pre_send_bands,scenes,action_types,surfaces,sources,after30,paths`。`after30` 为 `n,used_again,returned_pricing,no_active_action,purchase`，并非互斥划分。

`commercial.by_demand[]`：`{demand,...完整商业模块}`，可用当前选择需求替换 commercial 根对象。

所有 rate 为 0–1，空分母为 null；`n` 分母应始终同步呈现。2/5 分钟标准化字段为共同需求×周×设备分层加权描述性比较，最少每组每层 5 个样本。聚合仅承诺全部历史与需求筛选，日期或设备选择若未重算应显示明确的范围说明。

## 六个节点 × 四维对照

新增 `node_comparisons`：

```text
window_minutes = 30
nodes[]:
  key, title, anchor_label, target_label, proxy_note
  n, continued, stopped, rate, ci95
  eligible_arrived, observation_incomplete, prior_target_excluded
  profiles.continued / profiles.stopped: {n, metrics}
  advance_time:
    continued: {n, median_seconds, p90_seconds}
    stopped: {n, median_seconds: null, reason}
  stopped_states[]: {state, label, n, share}
  last_observed_states[]: {state, label, n, share}
  feature_rates[], paths
  by_demand[]: {demand, ...以上通用比较+四维字段}
result_gate_register: { ...独立的结果/下载注册门槛代理节点 }
```

六个 `key`：`entry_continue`、`browse_use`、`attempt_continue`、`use_register`、`signup_use`、`use_commercial`。可用数组顺序构建切换器；目标含义取 `target_label`，不要把六种不同目标全部写成“继续使用”。

四维对应：

1. 规模：`n/continued/stopped/rate`；尚无完整30分钟观察窗的 `observation_incomplete` 单列，不能混入停止组。
2. 推进时间：两组前置时长在 `profiles.*.metrics.seconds_to_anchor.median`；到达下一步耗时只展示 `advance_time.continued`，未达目标组无可观测完成时长。
3. 过程复杂度：`metrics.pre_page_depth`、`pre_distinct_pages`、`pre_active_actions`、`pre_backtracks` 的 `median`。全部取锚点前。返回已见页面也可能是正常认证返回，不等同无效绕路。
4. 停止时状态：`stopped_states` 是未达目标组在窗口内互斥的观察类型；`last_observed_states` 是窗口中真正最后记录的状态。某人曾遇结果门槛后又进入认证页时，前者是“出现结果门槛”，后者是“最后记录认证页”，两者不能混称停在门槛。

二元前置特征仍为 `attachments/auth_before/signup_before/choice_before`，每项有 `n/denominator/rate/unknown`。没有提交参数时“有素材”是未知，不自动归类“没有素材”。素材使用锚点提交时已经提供的参数，其余特征全部早于锚点。

`result_gate_register` 使用 `guest_gate_shown.reason in (first_result, download)` 为更直接的场景代理，排除此前已有注册信号者。当前没有生成完成/下载完成信号，必须保留“结果相关门槛”的表述，不能命名为真实首次成功。
