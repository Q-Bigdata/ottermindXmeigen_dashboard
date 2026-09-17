"""Recomputable all-history MeiGen traffic, demand, and card comparisons.

Plain Python only; no DB calls and no credentials. Input timestamps with no
explicit zone are interpreted as UTC by default (the extraction contract is
UTC); output timestamps and natural dates use Asia/Shanghai.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from itertools import combinations
import json
import math
from statistics import median
from typing import Any, Mapping
from urllib.parse import unquote, urlparse
from zoneinfo import ZoneInfo

BJ = ZoneInfo("Asia/Shanghai")
UTC = timezone.utc
MATURITY = ("entry_only", "browsed", "registered_only", "used", "registered_used", "reused", "payment_intent", "checkout_started", "paid", "paid_used")
MATURITY_LABELS = {"entry_only": "仅入口记录", "browsed": "浏览或交互", "registered_only": "有注册信号，未见使用", "used": "尝试使用", "registered_used": "注册后使用", "reused": "当次重复使用", "payment_intent": "主动付费意图", "checkout_started": "进入结账", "paid": "有支付信号", "paid_used": "支付后使用"}
DEMAND_LABELS = {"image_repair": "图片修复", "product_image": "商品图生成", "video_ads": "商品视频广告", "video_editor": "视频编辑", "watermark_removal": "去水印", "presentation": "PPT制作", "model_exploration": "模型/案例探索", "studio_task": "Studio泛任务", "skill_mcp": "技能/MCP", "other": "其他已见入口", "unknown": "待识别"}
DEMAND_GROUPS = {"image_repair": "图片处理/创作", "product_image": "图片处理/创作", "video_ads": "视频制作/编辑", "video_editor": "视频制作/编辑", "watermark_removal": "去水印", "presentation": "演示内容", "model_exploration": "模型与案例探索", "studio_task": "其他实际任务", "skill_mcp": "其他实际任务", "other": "其他实际任务", "unknown": "待识别"}
_EXTRACT_DEMAND_ALIASES = {
    "blur": "image_repair", "fix_blurry": "image_repair", "product_image": "product_image",
    "product_video": "video_ads", "video_edit": "video_editor", "watermark": "watermark_removal",
    "ppt": "presentation", "model_explore": "model_exploration", "model_exploration": "model_exploration",
    "studio_task": "studio_task", "other": "other", "unknown": "unknown",
    "video_generation": "video_generation",
}
DEMAND_LABELS["video_generation"] = "视频生成"
DEMAND_GROUPS["video_generation"] = "视频制作/编辑"
for _alias, _canonical in _EXTRACT_DEMAND_ALIASES.items():
    DEMAND_LABELS.setdefault(_alias, DEMAND_LABELS.get(_canonical, _alias))
    DEMAND_GROUPS.setdefault(_alias, DEMAND_GROUPS.get(_canonical, "其他实际任务"))
REGISTER_EVENTS = {"sign_up", "guest_signup"}
PRICE_EVENTS = {"pricing_opened", "plan_clicked", "select_billing_cycle", "begin_checkout", "checkout_opened"}
CHECKOUT_EVENTS = {"begin_checkout", "checkout_opened"}
PAYMENT_INTENT_EVENTS = {"plan_clicked", "paywall_cta_click", "begin_checkout", "checkout_opened"}
PASSIVE_EVENTS = {"login", "sign_out", "view_item_list", "onboarding_shown", "guest_gate_shown", "paywall_shown", "ppt_landing_view", "deck_checkpoint_shown", "skill_import_prompted"}
METRICS = ("browsed", "registered", "used", "registered_use", "reused", "download_intent", "pricing", "payment_intent", "checkout", "paid")


def parse_dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        d = value
    elif isinstance(value, (int, float)):
        d = datetime.fromtimestamp(value / 1000 if value > 1e11 else value, UTC)
    else:
        s = str(value).strip().replace("Z", "+00:00")
        # PostgreSQL text includes a space between seconds and UTC offset.
        if len(s) > 6 and s[-6] in "+-":
            s = s[:-6].rstrip() + s[-6:]
        try:
            d = datetime.fromisoformat(s)
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=UTC)
    return d.astimezone(BJ)


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def rate(n: int | float, d: int | float) -> float | None:
    return round(n / d, 6) if d else None


def wilson(k: int, n: int) -> list[float] | None:
    if not n:
        return None
    z = 1.959963984540054
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return [round(max(0, center - half), 6), round(min(1, center + half), 6)]


def _domain(value: Any) -> str:
    s = str(value or "").strip().lower()
    return (urlparse(s if "://" in s else "//" + s).hostname or s).rstrip(".")


def is_meigen_entry(v: Mapping[str, Any]) -> bool:
    return str(v.get("utm_source") or "").strip().lower() in {"meigen", "meigen.ai", "www.meigen.ai"} or _domain(v.get("referrer_domain")) in {"meigen.ai", "www.meigen.ai"}


def normalize_path(value: Any) -> str:
    s = str(value or "").strip()
    if "://" in s:
        s = urlparse(s).path
    s = unquote(s).split("?", 1)[0].rstrip("/") or "/"
    parts = s.split("/")
    if len(parts) > 1 and parts[1].lower() in {"en", "zh", "zh-cn", "zh-tw", "ja", "ko", "fr", "de", "es", "it", "pt", "pt-br", "ru", "ar", "hi", "tr", "id", "vi", "th"}:
        s = "/" + "/".join(parts[2:])
    return s.lower().rstrip("/") or "/"


def _task(s: str) -> str | None:
    s = s.lower()
    for keywords, label in (
        (("watermark",), "watermark_removal"),
        (("fix-blurry", "blurry-picture", "image-enhance", "photo-enhance"), "image_repair"),
        (("product-image",), "product_image"),
        (("video-editor", "video-edit"), "video_editor"),
        (("/features/ai-video-generator",), "video_generation"),
        (("product-video", "video-ads", "video_en"), "video_ads"),
        (("ppt", "presentation", "/slide", "deck"), "presentation"),
        (("gpt-", "/explore", "/model"), "model_exploration"),
        (("/skill", "/mcp"), "skill_mcp"),
        (("/studio",), "studio_task"),
    ):
        if any(k in s for k in keywords):
            return label
    return None


def demand_details(path: Any, content: Any = None) -> dict[str, Any]:
    p = normalize_path(path)
    entry_task, card_task = _task(p), _task(str(content or ""))
    # Generic Studio/auth/home landings preserve an explicit task promise.
    initial = entry_task if entry_task and entry_task != "studio_task" else card_task or entry_task
    initial = initial or ("unknown" if p in {"/", "/auth", "/login", "/signup"} else "other")
    basis = "entry_path" if initial == entry_task else "utm_content" if initial == card_task else "unresolved"
    return {"initial_demand": initial, "demand_label": DEMAND_LABELS[initial], "demand_group": DEMAND_GROUPS[initial],
            "normalized_entry_path": p, "card_demand": card_task, "entry_demand": entry_task,
            "card_entry_mismatch": bool(card_task and entry_task and entry_task != "studio_task" and card_task != entry_task),
            "label_basis": basis, "label_confidence": "explicit_task_route" if basis == "entry_path" else "explicit_card_label" if basis == "utm_content" else "unresolved"}


def demand_label(path: Any, content: Any = None) -> str:
    return demand_details(path, content)["initial_demand"]


def _properties(e: Mapping[str, Any]) -> Mapping[str, Any]:
    p = e.get("properties") or {}
    if isinstance(p, str):
        try:
            p = json.loads(p)
        except (ValueError, TypeError):
            p = {}
    return p if isinstance(p, dict) else {}


def build_visit_features(visits: list[Mapping[str, Any]], events: list[Mapping[str, Any]], observation_end: Any = None,
                         observation_start: Any = None) -> list[dict[str, Any]]:
    cutoff, lower = parse_dt(observation_end), parse_dt(observation_start)
    by_visit = defaultdict(list)
    seen_ids = set()
    for e in events:
        t = parse_dt(e.get("created_at"))
        if not t or (cutoff and t >= cutoff):
            continue
        eid = e.get("event_id")
        if eid is not None and str(eid) in seen_ids:
            continue
        if eid is not None:
            seen_ids.add(str(eid))
        by_visit[str(e.get("visit_id"))].append((t, e))
    rows = []
    for v in visits:
        if not is_meigen_entry(v):
            continue
        start = parse_dt(v.get("start_at") or v.get("first_event_at"))
        if not start or (lower and start < lower) or (cutoff and start >= cutoff):
            continue
        vid = str(v.get("visit_id"))
        evs = sorted((x for x in by_visit.get(vid, []) if x[0] >= start),
                     key=lambda x: (x[0], str(x[1].get("event_id") or "")))
        # Preserve simultaneous timestamps as ties. Ordering-based proxies use >.
        named = [(t, str(e.get("event_name") or ""), e) for t, e in evs]
        sends = [(t, e) for t, n, e in named if n == "send_message"]
        registrations = [(t, e) for t, n, e in named if n in REGISTER_EVENTS]
        purchases = [(t, e) for t, n, e in named if n == "purchase"]
        reg_time = min((t for t, _ in registrations), default=None)
        paid_time = min((t for t, _ in purchases), default=None)
        pv_paths = {normalize_path(e.get("url_path")) for _, e in evs if e.get("event_type") in {1, "1"} or not e.get("event_name")}
        active = [n for _, n, _ in named if n and n not in PASSIVE_EVENTS]
        flags = {"browsed": len(pv_paths) > 1 or bool(active),
                 "registered": bool(registrations), "used": bool(sends),
                 "registered_use": bool(reg_time and any(t > reg_time for t, _ in sends)),
                 "reused": len({t for t, _ in sends}) >= 2,
                 "download_intent": any(n in {"guest_gate_shown", "guest_signup", "paywall_shown"} and str(_properties(e).get("reason") or _properties(e).get("scene") or "").lower() == "download" for _, n, e in named),
                 "pricing": any(n in PRICE_EVENTS for _, n, _ in named),
                 "payment_intent": any(n in PAYMENT_INTENT_EVENTS for _, n, _ in named),
                 "checkout": any(n in CHECKOUT_EVENTS for _, n, _ in named),
                 "paid": bool(purchases),
                 "paid_use": bool(paid_time and any(t > paid_time for t, _ in sends))}
        maturity = "entry_only"
        for condition, stage in ((flags["browsed"], "browsed"), (flags["registered"], "registered_only"),
                                 (flags["used"], "used"), (flags["registered_use"], "registered_used"),
                                 (flags["reused"], "reused"), (flags["payment_intent"], "payment_intent"),
                                 (flags["checkout"], "checkout_started"), (flags["paid"], "paid"), (flags["paid_use"], "paid_used")):
            if condition:
                maturity = stage
        account_id = v.get("account_id") if not v.get("identity_conflict") else None
        details = demand_details(v.get("entry_path"), v.get("utm_content"))
        # The extraction layer may have a richer route/card resolver. Prefer
        # its explicit label, while retaining our local route-derived fallback.
        supplied = str(v.get("initial_demand") or "").strip().lower()
        if supplied:
            canonical = _EXTRACT_DEMAND_ALIASES.get(supplied, supplied if supplied in DEMAND_LABELS else None)
            if canonical:
                # Keep the extractor's concrete category in the matrix (for
                # example product_video vs video_edit), while use the alias
                # map only for display/group labels.
                details["initial_demand"] = supplied
                details["demand_label"] = DEMAND_LABELS[supplied]
                details["demand_group"] = DEMAND_GROUPS[supplied]
                details["label_basis"] = "extracted_initial_demand"
                details["label_confidence"] = "extracted_rule"
        r = {"visit_id": vid, "raw_initial_demand": v.get("initial_demand"), "start_at": start.isoformat(), "day": start.strftime("%Y-%m-%d"),
             "hour": start.strftime("%Y-%m-%d %H:00"), "weekday": start.weekday(),
             **details,
             "utm_content": str(v.get("utm_content") or "(none)"), "utm_campaign": v.get("utm_campaign") or "(none)",
             "utm_medium": v.get("utm_medium") or "(none)", "device": v.get("device") or "unknown",
             "browser": v.get("browser") or "unknown", "country": v.get("country") or "unknown",
             "language": v.get("language") or "unknown", "source_evidence": v.get("source_evidence") or "unspecified",
             "account_id": account_id, "identity_conflict": bool(v.get("identity_conflict")),
             "quality_eligible": bool(v.get("quality_eligible", True)),
             "pending_30m": bool(cutoff and start + timedelta(minutes=30) > cutoff),
             "reached_flags": flags, "deepest_stage": maturity, "event_count": len(evs), "send_message_count": len(sends),
             "send_timestamps": sorted({t.isoformat() for t, _ in sends}),
             "sign_up_count": sum(n == "sign_up" for _, n, _ in named), "guest_signup_count": sum(n == "guest_signup" for _, n, _ in named),
             "login_count": sum(n == "login" for _, n, _ in named), "guest_started_count": sum(n == "guest_started" for _, n, _ in named),
             "first_send_at": _iso(sends[0][0]) if sends else None, "first_register_at": _iso(reg_time),
             "first_purchase_at": _iso(paid_time), "distinct_pv_paths": len(pv_paths)}
        rows.append(r)
    return rows


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = [r for r in rows if r.get("quality_eligible", True)]
    out: dict[str, Any] = {"visits": len(rows), "pending_30m": sum(r["pending_30m"] for r in rows),
                           "behavior_denominator": len(eligible), "quality_excluded": len(rows)-len(eligible),
                           "known_accounts": len({r["account_id"] for r in rows if r.get("account_id")})}
    for m in METRICS:
        k = sum(r["reached_flags"][m] for r in eligible)
        out[m] = k
        out[m + "_rate"] = rate(k, len(eligible))
        out[m + "_ci95"] = wilson(k, len(eligible))
    return out


def _group_summaries(rows: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    groups = defaultdict(list)
    for r in rows:
        groups[str(r.get(field) or "unknown")].append(r)
    return [{field: key, **_summary(rr)} for key, rr in sorted(groups.items(), key=lambda x: -len(x[1]))]


def demand_maturity(visits: list[Mapping[str, Any]], events: list[Mapping[str, Any]], observation_end: Any = None,
                    observation_start: Any = None, *, features: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = features if features is not None else build_visit_features(visits, events, observation_end, observation_start)
    groups = defaultdict(list)
    for r in rows:
        groups[r["initial_demand"]].append(r)
    matrix = []
    for d, rr in sorted(groups.items(), key=lambda x: -len(x[1])):
        cells = Counter(x["deepest_stage"] for x in rr)
        matrix.append({"demand": d, "label": DEMAND_LABELS[d], "group": DEMAND_GROUPS[d], **_summary(rr),
                       "states": [{"stage": s, "label": MATURITY_LABELS[s], "visits": cells[s], "row_share": rate(cells[s], len(rr))} for s in MATURITY]})
    # Only source-visit accumulation lives here. The separate account analysis
    # extends to other-source visits and reports the actual lifetime/follow-up.
    accounts = defaultdict(list)
    for r in rows:
        if r.get("account_id"):
            accounts[str(r["account_id"])].append(r)
    account_rows = []
    for aid, rr in accounts.items():
        first = min(rr, key=lambda x: x["start_at"])
        flags = {m: any(r["reached_flags"][m] for r in rr) for m in METRICS}
        first_reg = min((r["first_register_at"] for r in rr if r["first_register_at"]), default=None)
        first_pay = min((r["first_purchase_at"] for r in rr if r["first_purchase_at"]), default=None)
        flags["registered_use"] = flags["registered_use"] or bool(first_reg and any(r["first_send_at"] and r["first_send_at"] > first_reg for r in rr))
        flags["reused"] = len({t for r in rr for t in r["send_timestamps"]}) >= 2
        flags["paid_use"] = any(r["reached_flags"]["paid_use"] for r in rr) or bool(first_pay and any(r["first_send_at"] and r["first_send_at"] > first_pay for r in rr))
        deepest = "entry_only"
        for condition, stage in ((flags["browsed"], "browsed"), (flags["registered"], "registered_only"),
                                 (flags["used"], "used"), (flags["registered_use"], "registered_used"),
                                 (flags["reused"], "reused"), (flags["payment_intent"], "payment_intent"),
                                 (flags["checkout"], "checkout_started"), (flags["paid"], "paid"), (flags["paid_use"], "paid_used")):
            if condition:
                deepest = stage
        account_rows.append({"account_id": aid, "initial_demand": first["initial_demand"], "first_meigen_at": first["start_at"],
                             "source_visits": len(rr), "source_active_visits": sum(r["reached_flags"]["used"] for r in rr),
                             "cross_source_visit_reuse": sum(r["reached_flags"]["used"] for r in rr) >= 2,
                             "deepest_stage": deepest, "reached_flags": flags})
    account_cells = Counter((r["initial_demand"], r["deepest_stage"]) for r in account_rows)
    account_totals = Counter(r["initial_demand"] for r in account_rows)
    account_matrix = [{"demand": d, "label": DEMAND_LABELS[d], "accounts": n,
                       "states": [{"stage": s, "label": "来源访问内重复使用" if s == "reused" else MATURITY_LABELS[s], "accounts": account_cells[(d, s)], "row_share": rate(account_cells[(d, s)], n)} for s in MATURITY]} for d, n in account_totals.most_common()]
    return {"rows": rows, "matrix": matrix, "summary": _summary(rows), "stage_order": list(MATURITY),
            "account_source_visit_rows": account_rows, "account_source_visit_matrix": account_matrix,
            "account_scope": "仅 MeiGen 来源访问内累计；跨来源后续行为由账户随访模块补全",
            "source_evidence": _group_summaries(rows, "source_evidence"), "entry_mapping": _group_summaries(rows, "normalized_entry_path"),
            "card_mapping": _group_summaries(rows, "utm_content"), "mismatch_visits": sum(r["card_entry_mismatch"] for r in rows),
            "definitions": {"use": "send_message", "register": sorted(REGISTER_EVENTS), "reused": "当次至少2个不同时间戳的send_message；同刻两条不算复用，也不等于跨日回访", "registered_use": "本次注册信号后严格更晚的send_message", "payment_intent": sorted(PAYMENT_INTENT_EVENTS), "checkout": sorted(CHECKOUT_EVENTS), "paid": "purchase事件；交易核对由支付模块输出", "download_intent": "download场景门槛，仅意图线索", "missing_completion": "生成完成/下载完成未推断"}}


def _full_day(day: str, start: datetime | None, end: datetime | None) -> bool:
    midnight = datetime.fromisoformat(day).replace(tzinfo=BJ)
    return (start is None or midnight >= start) and (end is None or midnight + timedelta(days=1) <= end)


def _dimension_contribution(current: list[dict[str, Any]], baseline: list[dict[str, Any]], days: int, key: str) -> list[dict[str, Any]]:
    cur, old = Counter(r.get(key) or "unknown" for r in current), Counter(r.get(key) or "unknown" for r in baseline)
    raw = []
    for k in cur.keys() | old.keys():
        b = old[k] / days if days else 0
        raw.append({"dimension": str(k), "current": cur[k], "baseline_daily_avg": round(b, 3), "increment": round(cur[k] - b, 3)})
    positive = sum(max(0, r["increment"]) for r in raw)
    net = sum(r["increment"] for r in raw)
    for r in raw:
        r["positive_increment_share"] = rate(max(0, r["increment"]), positive)
        r["net_increment_share"] = rate(r["increment"], net) if net > 0 else None
    return sorted(raw, key=lambda r: -r["increment"])


def daily_hourly_trends(visits: list[Mapping[str, Any]], observation_start: Any = None, observation_end: Any = None,
                        *, features: list[dict[str, Any]] | None = None, events: list[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    rows = features if features is not None else build_visit_features(visits, events or [], observation_end, observation_start)
    start, end = parse_dt(observation_start), parse_dt(observation_end)
    byday, byhour = defaultdict(list), defaultdict(list)
    for r in rows:
        byday[r["day"]].append(r)
        byhour[r["hour"]].append(r)
    if not rows:
        return {"days": [], "hours": [], "spikes": [], "source_visits": 0}
    first = (start or min(parse_dt(r["start_at"]) for r in rows)).date()
    last = ((end - timedelta(microseconds=1)) if end else max(parse_dt(r["start_at"]) for r in rows)).date()
    dates = [(first + timedelta(days=i)).isoformat() for i in range((last-first).days+1)]
    daily = []
    for i, d in enumerate(dates):
        previous = [x for x in dates[max(0, i-7):i] if _full_day(x, start, end)]
        vals = [len(byday[x]) for x in previous]
        med = median(vals) if vals else None
        mad = median([abs(v-med) for v in vals]) if vals else None
        n = len(byday[d])
        complete = _full_day(d, start, end)
        robust_z = (n-med)/(1.4826*mad) if mad else None
        delta = n-med if med is not None else None
        # Require meaningful volume and >=3 observed full baseline days. A
        # large ratio alone must not promote 1 -> 2 visits to an anomaly.
        candidate = bool(complete and len(previous) >= 3 and delta is not None and delta >= 30 and n >= med * 1.5 and (robust_z is None or robust_z >= 2))
        prev_day = dates[i-1] if i else None
        daily.append({"date": d, **_summary(byday[d]), "partial_day": not complete,
                      "baseline_days": previous, "baseline_median_7d": med,
                      "baseline_mean_7d": round(sum(vals)/len(vals), 3) if vals else None,
                      "increment_vs_median": delta, "relative_change_vs_median": rate(delta, med) if med else None,
                      "robust_z_mad": round(robust_z, 4) if robust_z is not None else None,
                      "day_over_day_increment": n-len(byday[prev_day]) if prev_day and _full_day(prev_day, start, end) else None,
                      "spike_candidate": candidate})
    spikes = []
    for d in daily:
        if not d["spike_candidate"]:
            continue
        rr = byday[d["date"]]
        old = [r for x in d["baseline_days"] for r in byday[x]]
        old_summary, now_summary = _summary(old), _summary(rr)
        before_days = len(d["baseline_days"])
        downstream = {m: {"current": now_summary[m], "baseline_daily_avg": round(old_summary[m]/before_days, 3),
                           "increment": round(now_summary[m]-old_summary[m]/before_days, 3),
                           "current_rate": now_summary[m+"_rate"], "baseline_rate": old_summary[m+"_rate"],
                           "rate_difference_pp": round((now_summary[m+"_rate"]-old_summary[m+"_rate"])*100, 3) if now_summary[m+"_rate"] is not None and old_summary[m+"_rate"] is not None else None} for m in METRICS}
        spikes.append({**d, "downstream": downstream,
                       "contributions": {k: _dimension_contribution(rr, old, before_days, k) for k in ("utm_content", "initial_demand", "device", "country", "language", "source_evidence")},
                       "hours": [{"hour": h, **_summary(byhour[h])} for h in sorted(byhour) if h.startswith(d["date"])],
                       "operations_lookup": "回查当日MeiGen卡片排序/展示、链接或素材更改、联名推广及产品/埋点版本；这些是排查方向而非已确认原因。"})
    # The robust volume screen intentionally does not capture every business
    # change. Add auditable day-over-day increases and usage-signal breaks so a
    # lower-than-1.5x traffic rise cannot hide a much larger downstream drop.
    traffic_jumps = []
    for i, d in enumerate(daily):
        if i == 0 or d["partial_day"] or daily[i-1]["partial_day"]:
            continue
        old, cur = daily[i-1], d
        increase = cur["visits"] - old["visits"]
        if increase < 150 or not old["visits"] or increase/old["visits"] < .15:
            continue
        traffic_jumps.append({"date": cur["date"], "previous_date": old["date"], "visits": cur["visits"],
                              "previous_visits": old["visits"], "increment": increase,
                              "relative_change": rate(increase, old["visits"]),
                              "current": _summary(byday[cur["date"]]), "previous": _summary(byday[old["date"]]),
                              "contributions": {k: _dimension_contribution(byday[cur["date"]], byday[old["date"]], 1, k) for k in ("utm_content", "initial_demand")}})
    traffic_jumps = sorted(traffic_jumps, key=lambda x: -x["increment"])[:5]
    usage_breaks = []
    last_break_index = -100
    for i, d in enumerate(daily):
        if i < 3 or d["partial_day"] or i-last_break_index <= 3:
            continue
        baseline_days = [x["date"] for x in daily[i-3:i] if not x["partial_day"]]
        baseline_rows = [r for x in baseline_days for r in byday[x]]
        old = _summary(baseline_rows)
        if d["behavior_denominator"] < 200 or old["behavior_denominator"] < 500 or old["used"] < 20:
            continue
        if d["used_rate"] is not None and old["used_rate"] is not None and d["used_rate"] <= old["used_rate"]*.5:
            usage_breaks.append({"date": d["date"], "baseline_dates": baseline_days, "current": _summary(byday[d["date"]]),
                                 "baseline": old, "used_rate_ratio": rate(d["used_rate"], old["used_rate"]),
                                 "same_demand_before": _group_summaries(baseline_rows, "initial_demand"),
                                 "same_demand_after": _group_summaries(byday[d["date"]], "initial_demand"),
                                 "card_first_seen_today": sorted({r["utm_content"] for r in byday[d["date"]] if not any(x["utm_content"] == r["utm_content"] and x["day"] < d["date"] for x in rows)})})
            last_break_index = i
    return {"days": daily, "hours": [{"hour": h, **_summary(rr)} for h, rr in sorted(byhour.items())], "spikes": spikes,
            "traffic_jumps": traffic_jumps, "usage_breaks": usage_breaks,
            "source_visits": len(rows), "bounds": {"start": _iso(start), "end_exclusive": _iso(end)},
            "baseline_definition": "前7个自然日内完整日的中位数与MAD；零流量日保留；突增至少3个基线完整日、增量≥30、倍率≥1.5、非零MAD时robust_z≥2。贡献量以前7日均量分解。",
            "behavior_definition": "日行为指标为该日入组Visit截至冻结点的已观测到达；末日及末30分钟单列，非固定30分钟转化率。"}


def _difference(ka: int, na: int, kb: int, nb: int) -> dict[str, Any]:
    if not na or not nb:
        return {"difference_b_minus_a_pp": None, "ci95_pp": None}
    pa, pb = ka/na, kb/nb
    ia, ib = wilson(ka, na), wilson(kb, nb)
    delta = pb-pa
    # Newcombe interval from independent Wilson score intervals.
    lo = delta-math.sqrt((pb-ib[0])**2+(ia[1]-pa)**2)
    hi = delta+math.sqrt((ib[1]-pb)**2+(pa-ia[0])**2)
    return {"difference_b_minus_a_pp": round(delta*100, 3), "ci95_pp": [round(lo*100, 3), round(hi*100, 3)]}


def card_comparison(visits: list[Mapping[str, Any]], events: list[Mapping[str, Any]], observation_end: Any = None,
                    observation_start: Any = None, *, features: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = features if features is not None else build_visit_features(visits, events, observation_end, observation_start)
    comparisons = []
    for demand in sorted({r["initial_demand"] for r in rows}):
        rr = [r for r in rows if r["initial_demand"] == demand and not r["pending_30m"] and r.get("quality_eligible", True)]
        cards = sorted({r["utm_content"] for r in rr if r["utm_content"] != "(none)"})
        for a, b in combinations(cards, 2):
            aa, bb = [r for r in rr if r["utm_content"] == a], [r for r in rr if r["utm_content"] == b]
            overlap_start = max(min(r["start_at"] for r in aa), min(r["start_at"] for r in bb))
            # Do not count the older card's morning visits as contemporaneous
            # with a new card that first appears that evening.
            aa_overlap = [r for r in aa if r["start_at"] >= overlap_start]
            bb_overlap = [r for r in bb if r["start_at"] >= overlap_start]
            ga, gb = defaultdict(list), defaultdict(list)
            for r in aa_overlap: ga[(r["day"], r["device"], r["normalized_entry_path"] )].append(r)
            for r in bb_overlap: gb[(r["day"], r["device"], r["normalized_entry_path"] )].append(r)
            common = ga.keys() & gb.keys()
            ac = [r for k in common for r in ga[k]]
            bc = [r for k in common for r in gb[k]]
            raw_a, raw_b, overlap_a, overlap_b = _summary(aa), _summary(bb), _summary(ac), _summary(bc)
            denom = len(ac)+len(bc)
            metrics = {}
            for m in METRICS:
                weights = {k: (len(ga[k])+len(gb[k]))/denom for k in common} if denom else {}
                pa = sum(weights[k] * sum(r["reached_flags"][m] for r in ga[k])/len(ga[k]) for k in common) if common else None
                pb = sum(weights[k] * sum(r["reached_flags"][m] for r in gb[k])/len(gb[k]) for k in common) if common else None
                # A transparent descriptive delta-method uncertainty estimate.
                # Sparse strata remain visible; no causal claim follows.
                variance = 0.0
                for k in common:
                    for gg in (ga[k], gb[k]):
                        # Jeffreys smoothing is confined to uncertainty,
                        # leaving reported rates unmodified. It avoids a
                        # zero-width interval in all-zero tiny cells.
                        k_success = sum(r["reached_flags"][m] for r in gg)
                        p = (k_success + 0.5)/(len(gg) + 1)
                        variance += weights[k]**2 * p*(1-p)/len(gg)
                se = math.sqrt(variance)
                standardized = {"a_rate": round(pa, 6) if pa is not None else None, "b_rate": round(pb, 6) if pb is not None else None,
                                "difference_b_minus_a_pp": round((pb-pa)*100, 3) if pa is not None and pb is not None else None,
                                "ci95_approx_pp": [round(max(-1, pb-pa-1.96*se)*100, 3), round(min(1, pb-pa+1.96*se)*100, 3)] if pa is not None and pb is not None else None}
                metrics[m] = {"raw": _difference(raw_a[m], len(aa), raw_b[m], len(bb)),
                              "common_subset": _difference(overlap_a[m], len(ac), overlap_b[m], len(bc)), "standardized": standardized}
            comparisons.append({"demand": demand, "demand_label": DEMAND_LABELS[demand], "card_a": a, "card_b": b,
                                "temporal_overlap_start": overlap_start,
                                "temporal_overlap": {"a": _summary(aa_overlap), "b": _summary(bb_overlap)},
                                "raw": {"a": raw_a, "b": raw_b}, "common": {"a": overlap_a, "b": overlap_b},
                                "common_strata": len(common), "common_dates": sorted({k[0] for k in common}),
                                "sparse_strata_n_lt_5": sum(min(len(ga[k]), len(gb[k])) < 5 for k in common),
                                "overlap_coverage_a": rate(len(ac), len(aa)), "overlap_coverage_b": rate(len(bc), len(bb)),
                                "metrics": metrics,
                                "strata": [{"date": k[0], "device": k[1], "entry_path": k[2], "a": _summary(ga[k]), "b": _summary(gb[k])} for k in sorted(common)]})
    return {"comparisons": comparisons, "cards": _group_summaries(rows, "utm_content"),
            "definition": "仅同需求卡片对照；先以较晚首见卡片的精确时间为共同起点，再用共同日期×设备×归一入口分层，标准化到两组共同样本的合并结构；报告共同覆盖率。末30分钟访问从对照中排除。",
            "interpretation": "差异为观测关联；卡片未随机分配。共同覆盖不足时不能外推全历史。标准化区间为分层二项delta近似，小格以原始分母和Wilson区间复核。"}


def analyze_segments(visits: list[Mapping[str, Any]], events: list[Mapping[str, Any]], observation_start: Any = None,
                      observation_end: Any = None) -> dict[str, Any]:
    rows = build_visit_features(visits, events, observation_end, observation_start)
    return {"meta": {"observation_start": _iso(parse_dt(observation_start)), "observation_end_exclusive": _iso(parse_dt(observation_end)),
                     "timezone": "Asia/Shanghai", "naive_timestamp_timezone": "UTC", "visit_observation_cutoff_minutes": 30,
                     "input_visits": len(visits), "input_events": len(events), "cohort_visits": len(rows)},
            "trends": daily_hourly_trends(visits, observation_start, observation_end, features=rows),
            "maturity": demand_maturity(visits, events, observation_end, observation_start, features=rows),
            "cards": card_comparison(visits, events, observation_end, observation_start, features=rows)}


if __name__ == "__main__":
    import argparse
    from pathlib import Path
    parser = argparse.ArgumentParser()
    parser.add_argument("--visits", default="data/visits.json")
    parser.add_argument("--events", default="data/events.json")
    parser.add_argument("--start", default="2026-08-12T21:24:36.457+08:00")
    parser.add_argument("--end", default="2026-09-16T07:10:00Z")
    parser.add_argument("--output", default="data/segments.json")
    args = parser.parse_args()
    result = analyze_segments(json.loads(Path(args.visits).read_text()), json.loads(Path(args.events).read_text()), args.start, args.end)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({"output": args.output, "visits": result["meta"]["cohort_visits"], "days": len(result["trends"]["days"]), "spikes": len(result["trends"]["spikes"]), "card_comparisons": len(result["cards"]["comparisons"])}, ensure_ascii=False))
