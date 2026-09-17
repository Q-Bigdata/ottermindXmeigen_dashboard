"""Ordered journeys and six branch diagnostics for the MeiGen entry cohort.

Pure Python, no database or network access. Public entry point:
    analyze_branches(visits, events, observation_start, observation_end) -> dict
Visit/event dictionaries follow the extraction pipeline's normalized schema.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from math import sqrt
from statistics import median
from typing import Any, Iterable, Mapping
from urllib.parse import unquote, urlsplit
import hashlib
import importlib.util
from pathlib import Path
import re

WINDOW = timedelta(minutes=30)
CST = timezone(timedelta(hours=8))
NODE_ORDER = ("entry", "landing", "browse", "register", "use", "reuse", "payment")
REGISTER = {"sign_up", "guest_signup"}
INTENT = {"plan_clicked", "paywall_cta_click", "begin_checkout", "checkout_opened"}
CHECKOUT = {"begin_checkout", "checkout_opened"}
ACTIVE = {"send_message", "ui_click", "onboarding_pick", "sign_up", "guest_signup", "purchase",
          "skill_import_confirmed", "skill_import_cancelled", "deck_checkpoint_continue",
          "pricing_opened", "plan_clicked", "paywall_cta_click", "begin_checkout", "checkout_opened"}


def _dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    value = str(value).strip().replace("Z", "+00:00")
    value = re.sub(r"\s+([+-]\d{2}:\d{2})$", r"\1", value)
    try:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _iso(at: datetime | None) -> str | None:
    return at.astimezone(CST).isoformat() if at else None


def _path(raw: Any) -> str:
    path = unquote(str(raw or "")).split("?", 1)[0]
    if "://" in path:
        path = urlsplit(path).path
    path = re.sub(r"^/(?:en|zh(?:-[a-zA-Z]+)?|ja|ko|de|fr|es|pt|ru|it|ar|id|th|vi)(?=/|$)", "", path)
    return (path.rstrip("/") or "/") if path else ""


def _is_auth(path: str) -> bool:
    return bool(re.search(r"/(?:auth|login|sign-in|signup|sign-up)(?:/|$)", path))


def _is_page(e: Mapping[str, Any]) -> bool:
    name = str(e.get("event_name") or "").lower()
    typ = str(e.get("event_type") or "").lower()
    return typ in {"1", "pageview", "page_view"} or name in {"pageview", "page_view"} or (not name and bool(e.get("url_path")))


def _props(e: Mapping[str, Any]) -> Mapping[str, Any]:
    p = e.get("properties", e.get("props", {}))
    return p if isinstance(p, Mapping) else {}


def _bool(value: Any) -> bool | None:
    if value is True or str(value).lower() in {"true", "1"}:
        return True
    if value is False or str(value).lower() in {"false", "0"}:
        return False
    return None


def _truth_label(value: Any) -> str:
    parsed = _bool(value)
    return "true" if parsed is True else "false" if parsed is False else "unknown"


def _source_ok(v: Mapping[str, Any]) -> bool:
    source = str(v.get("utm_source") or "").strip().lower()
    ref = str(v.get("referrer_domain") or "").strip().lower().split(":", 1)[0]
    return source in {"meigen", "meigen.ai"} or ref == "meigen.ai" or ref.endswith(".meigen.ai")


def _demand(v: Mapping[str, Any]) -> str:
    if v.get("initial_demand"):
        return str(v["initial_demand"])
    p = _path(v.get("entry_path"))
    for label, token in (("image_restore", "fix-blurry"), ("watermark_removal", "watermark"),
                         ("product_image", "product-image"), ("product_video", "product-video"),
                         ("video_edit", "video-editor"), ("presentation", "/ppt"),
                         ("model_exploration", "/explore"), ("studio_task", "/studio")):
        if token in p:
            return label
    return "unknown"


def _week(at: datetime) -> str:
    date = at.astimezone(CST).date()
    return (date - timedelta(days=date.weekday())).isoformat()


def _rate(y: int, n: int) -> float | None:
    return y / n if n else None


def _wilson(y: int, n: int) -> list[float] | None:
    if not n:
        return None
    z = 1.959963984540054
    p, den = y / n, 1 + z*z/n
    mid = (p + z*z/(2*n)) / den
    half = z * sqrt(p*(1-p)/n + z*z/(4*n*n)) / den
    return [max(0, mid-half), min(1, mid+half)]


def _quantile(vals: list[float], q: float) -> float | None:
    if not vals:
        return None
    vals = sorted(vals)
    index = (len(vals)-1)*q
    low = int(index)
    return vals[low] + (vals[min(low+1, len(vals)-1)]-vals[low])*(index-low)


def _first(events: list[dict[str, Any]], names: set[str] | None = None,
           after: datetime | None = None, until: datetime | None = None) -> dict[str, Any] | None:
    return next((e for e in events if (names is None or e["name"] in names)
                 and (after is None or e["at"] > after) and (until is None or e["at"] <= until)), None)


def _at(e: dict[str, Any] | None) -> datetime | None:
    return e["at"] if e else None


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    # Every row is an anchor with a full window. Outcome is mutually exclusive.
    n = len(rows)
    y = sum(r["progressed"] for r in rows)
    none = sum(not r["progressed"] and not r["any_later"] for r in rows)
    other = n-y-none
    no_valid = sum(not r["progressed"] and not r.get("valid_later", r["any_later"]) for r in rows)
    times = [r["seconds_to_target"] for r in rows if r["seconds_to_target"] is not None]
    return {"denominator": n, "continued": y, "stopped": n-y,
            "continue_rate": _rate(y, n), "continue_wilson95": _wilson(y, n),
            "no_later_record": none, "no_later_record_rate": _rate(none, n),
            "other_later_activity": other, "no_target_rate": _rate(n-y, n),
            "no_later_valid_action": no_valid, "exit_rate": _rate(no_valid, n),
            "target_observed_before_anchor": sum(r.get("target_before", False) for r in rows),
            "target_at_same_timestamp": sum(r.get("target_same_time", False) for r in rows),
            "median_seconds_to_target": median(times) if times else None,
            "p90_seconds_to_target": _quantile(times, .9)}


def _breakdowns(rows: list[dict[str, Any]], dims: tuple[str, ...]) -> list[dict[str, Any]]:
    cells: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        cells[tuple(r.get(d, "unknown") for d in dims)].append(r)
    return [{**dict(zip(dims, key)), **_summary(rs)} for key, rs in
            sorted(cells.items(), key=lambda x: tuple(str(v) for v in x[0]))]


def _normalize(visits: list[Mapping[str, Any]], events: Iterable[Mapping[str, Any]],
               start: datetime, end: datetime) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    selected = {str(v["visit_id"]): v for v in visits if v.get("visit_id") and _source_ok(v)}
    by_visit: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen = set()
    duplicate_events, invalid_times, skipped_outside = 0, 0, 0
    for raw in events:
        vid = str(raw.get("visit_id") or "")
        if vid not in selected:
            continue
        at = _dt(raw.get("created_at"))
        if not at:
            invalid_times += 1
            continue
        if not start <= at < end:
            skipped_outside += 1
            continue
        eid = str(raw.get("event_id") or "")
        if eid and eid in seen:
            duplicate_events += 1
            continue
        if eid:
            seen.add(eid)
        props = dict(_props(raw))
        # The extractor may keep redirect_path at the event top level. Preserve
        # it inside the normalized whitelist so auth continuation can use one path.
        if raw.get("redirect_path") is not None and "redirect_path" not in props:
            props["redirect_path"] = unquote(str(raw.get("redirect_path")))
        by_visit[vid].append({"event_id": eid, "at": at, "name": str(raw.get("event_name") or "").lower(),
                              "path": _path(raw.get("url_path")), "page": _is_page(raw), "props": props})
    prepared = []
    missing_events = 0
    for vid, v in selected.items():
        evs = sorted(by_visit[vid], key=lambda e: (e["at"], e["event_id"]))
        if not evs:
            missing_events += 1
            continue
        entry = _dt(v.get("start_at")) or evs[0]["at"]
        if not start <= entry < end:
            continue
        evs = [e for e in evs if e["at"] >= entry]
        if not evs:
            missing_events += 1
            continue
        entry_path = _path(v.get("entry_path") or v.get("raw_entry_path"))
        pages = [e for e in evs if e["page"]]
        nonauth = [e for e in pages if not _is_auth(e["path"])]
        browse_e = next((e for e in nonauth if e["at"] > entry and e["path"] != entry_path), None)
        if not browse_e:
            browse_e = next((e for e in evs if e["at"] > entry and e["name"] in {"ui_click", "onboarding_pick"}), None)
        sends = [e for e in evs if e["name"] == "send_message"]
        # Distinct timestamps avoid treating duplicate simultaneous emissions as a repeat.
        send = sends[0] if sends else None
        repeat = next((e for e in sends if e["at"] > send["at"]), None) if send else None
        anchors = {"entry": entry, "landing": _at(pages[0]) if pages else None,
                   "browse": _at(browse_e), "register": _at(_first(evs, REGISTER)),
                   "use": _at(send), "reuse": _at(repeat), "payment": _at(_first(evs, {"purchase"})),
                   "auth": _at(next((e for e in pages if _is_auth(e["path"])), None)),
                   "pricing": _at(_first(evs, {"pricing_opened", "view_item_list"})),
                   "checkout": _at(_first(evs, CHECKOUT)), "intent": _at(_first(evs, INTENT))}
        prepared.append({"visit_id": vid, "raw": v, "events": evs, "pages": pages, "send_events": sends,
                         "anchors": anchors, "demand": _demand(v), "entry_path": entry_path,
                         "device": str(v.get("device") or "unknown"), "browser": str(v.get("browser") or "unknown"),
                         "country": str(v.get("country") or "unknown"), "language": str(v.get("language") or "unknown"),
                         "utm_content": str(v.get("utm_content") or "untagged"), "week": _week(entry),
                         "same_time_first_sends": sum(e["at"] == send["at"] for e in sends) > 1 if send else False})
    # Exclude already-observed account registrations/payments from first-conversion
    # branches where a non-conflicting account link exists. Unknown history stays
    # explicit rather than being inferred from login (which can include guests).
    account_signup: dict[str, datetime] = {}
    account_purchase: dict[str, datetime] = {}
    for item in prepared:
        account = item["raw"].get("account_id")
        if not account or _bool(item["raw"].get("identity_conflict")) is True:
            continue
        for node, dest in (("register", account_signup), ("payment", account_purchase)):
            at = item["anchors"][node]
            if at and (str(account) not in dest or at < dest[str(account)]):
                dest[str(account)] = at
    for item in prepared:
        account = item["raw"].get("account_id")
        linked = bool(account and _bool(item["raw"].get("identity_conflict")) is not True)
        item["account_signup_at"] = account_signup.get(str(account)) if linked else None
        item["account_purchase_at"] = account_purchase.get(str(account)) if linked else None
    quality = {"input_visits": len(visits), "strict_source_visits": len(selected), "cohort_visits": len(prepared),
               "missing_events_visits": missing_events, "events": sum(len(i["events"]) for i in prepared),
               "duplicate_event_ids_removed": duplicate_events, "invalid_event_time": invalid_times,
               "outside_observation_events": skipped_outside,
               "first_send_timestamp_ambiguous_visits": sum(i["same_time_first_sends"] for i in prepared)}
    return prepared, quality


def _features(item: dict[str, Any], anchor: datetime) -> dict[str, Any]:
    before = [e for e in item["events"] if e["at"] < anchor]
    sends = [e for e in before if e["name"] == "send_message"]
    pages = [e for e in before if e["page"]]
    at_send = next((e for e in item["send_events"] if e["at"] == anchor), None)
    elapsed = (anchor-item["anchors"]["entry"]).total_seconds()
    known_registered = any(e["name"] in REGISTER for e in before) or bool(item.get("account_signup_at") and item["account_signup_at"] < anchor)
    return {"signup_before": str(known_registered).lower(),
            "auth_before": str(any(e["page"] and _is_auth(e["path"]) for e in before)).lower(),
            "studio_before": str(any(e["page"] and e["path"].startswith("/studio") and not _is_auth(e["path"]) for e in before)).lower(),
            "choice_before": str(any(e["name"] in {"onboarding_pick", "ui_click", "skill_import_confirmed"} for e in before)).lower(),
            "prior_use_count": "0" if not sends else "1" if len(sends) == 1 else "2_plus",
            "prior_page_depth": "0_1" if len(pages) <= 1 else "2_3" if len(pages) <= 3 else "4_plus",
            "elapsed_before": "under15s" if elapsed < 15 else "15_60s" if elapsed < 60 else "60s_plus",
            "attachments": (_truth_label(at_send["props"].get("has_attachments"))
                              if at_send and not item["same_time_first_sends"] else "unknown"),
            "intent_before": str(any(e["name"] in INTENT for e in before)).lower(),
            "pricing_before": str(any(e["name"] in {"pricing_opened", "view_item_list"} for e in before)).lower()}


def _record(item: dict[str, Any], anchor: datetime, targets: list[dict[str, Any]],
            branch: str, allow_same_time: bool = False) -> dict[str, Any]:
    later = [e for e in item["events"] if anchor < e["at"] <= anchor+WINDOW]
    outcome = next((e for e in targets if (e["at"] >= anchor if allow_same_time else e["at"] > anchor)
                    and e["at"] <= anchor+WINDOW), None)
    row = {d: item[d] for d in ("visit_id", "demand", "entry_path", "device", "browser", "country", "language", "utm_content", "week")}
    # A later page/action is a valid observed continuation. Passive duplicate
    # telemetry does not turn an exit into progress.
    prior_pages = [e for e in item["pages"] if e["at"] <= anchor]
    anchor_path = prior_pages[-1]["path"] if prior_pages else item["entry_path"]
    # Submitting a task itself creates /studio/task and /studio/task/<id> PVs.
    # These route updates are not independent post-submit actions. Keep the PVs
    # as raw records while excluding them from the effective-action exit metric.
    has_used_by_anchor = item["anchors"]["use"] is not None and item["anchors"]["use"] <= anchor
    valid_later = [e for e in later if e["name"] in ACTIVE or
                   (e["page"] and e["path"] != anchor_path and
                    not (has_used_by_anchor and e["path"].startswith("/studio/task")))]
    row.update({"branch": branch, "anchor_at": _iso(anchor), "outcome_at": _iso(_at(outcome)),
                "progressed": outcome is not None, "any_later": bool(later),
                "valid_later": bool(valid_later),
                "seconds_to_target": (outcome["at"]-anchor).total_seconds() if outcome else None,
                "target_before": any(e["at"] < anchor for e in targets),
                "target_same_time": any(e["at"] == anchor for e in targets),
                "features": _features(item, anchor), "next_observed": _event_label(later[0]) if later else None})
    return row


def _event_label(e: dict[str, Any]) -> str:
    if e["page"]:
        p = e["path"]
        if _is_auth(p): return "认证页"
        if p.startswith("/studio"): return "Studio"
        if p.startswith("/tools/"): return p.replace("/tools/", "工具:")
        if p.startswith("/explore/"): return "探索页"
        if p.startswith("/features/"): return p.replace("/features/", "功能:")
        return p
    return e["name"]


def _target_events(item: dict[str, Any], target: str) -> list[dict[str, Any]]:
    if target == "landing": return item["pages"]
    if target == "progress":
        return [e for e in item["events"] if e["name"] in ACTIVE or
                e["page"] and e["path"] != item["entry_path"]]
    if target == "start": return [e for e in item["events"] if e["name"] in REGISTER | {"send_message"}]
    mapping = {"register": REGISTER, "use": {"send_message"}, "reuse": {"send_message"},
               "pricing": {"pricing_opened", "view_item_list"}, "intent": INTENT,
               "checkout": CHECKOUT, "purchase": {"purchase"}, "repeat_or_intent": {"send_message"} | INTENT,
               "identity": REGISTER | {"login"}}
    return [e for e in item["events"] if e["name"] in mapping[target]]


def _node_outputs(items: list[dict[str, Any]], end: datetime) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    specs = {"entry": ("progress", "进入后出现页面/主动操作（无外链点击数据）"),
             "landing": ("progress", "浏览其他页面或主动操作"),
             "browse": ("start", "继续注册或提交任务"), "register": ("use", "注册后提交任务"),
             "use": ("reuse", "再次提交任务"), "reuse": ("intent", "进入付费意图"),
             "payment": ("use", "支付信号后继续提交任务")}
    out, all_rows = [], {}
    for node in NODE_ORDER:
        target, label = specs[node]
        reached = [i for i in items if i["anchors"][node] is not None]
        mature = [i for i in reached if i["anchors"][node]+WINDOW <= end]
        rows = [_record(i, i["anchors"][node], _target_events(i, target), node,
                        allow_same_time=False) for i in mature]
        # Entry and landing are the same measurable in-site observation start;
        # avoid presenting an unavailable external-click rate as 100%.
        if node == "entry":
            for r in rows:
                r["target_same_time"] = False
        stat = _summary(rows)
        stat.update({"node": node, "arrived": len(reached), "mature_arrived": len(rows),
                     "observation_incomplete": len(reached)-len(mature), "target": target, "target_label": label,
                     "definition": {"entry": "MeiGen来源的站内入口；不包含站外卡片曝光/点击量", "landing": "首次页面记录",
                                    "browse": "后续不同的非认证页面，或点击/选择动作", "register": "sign_up/guest_signup注册信号",
                                    "use": "send_message提交信号", "reuse": "严格更晚时间的再次send_message",
                                    "payment": "purchase支付记录信号，独立于结账意图"}[node],
                     "by_demand": _breakdowns(rows, ("demand",)),
                     "by_entry_device_week": _breakdowns(rows, ("entry_path", "device", "week")),
                     "next_observed": [{"label": label, "visits": count} for label, count in
                                       Counter(r["next_observed"] or "无后续记录" for r in rows).most_common(12)]})
        out.append(stat)
        all_rows[node] = rows
    return out, all_rows


def _contrast(rows: list[dict[str, Any]], feature: str, a: str, b: str,
              strata: tuple[str, ...] = ("demand", "entry_path", "week", "device")) -> dict[str, Any]:
    def val(r): return str(r.get(feature, r["features"].get(feature, "unknown")))
    groups = {a: [r for r in rows if val(r) == a], b: [r for r in rows if val(r) == b]}
    raw = {label: _summary(rs) for label, rs in groups.items()}
    cells: dict[tuple[Any, ...], dict[str, list[dict[str, Any]]]] = defaultdict(lambda: {a: [], b: []})
    for label, rs in groups.items():
        for r in rs: cells[tuple(r[d] for d in strata)][label].append(r)
    common = [c for c in cells.values() if c[a] and c[b]]
    total = sum(len(c[a])+len(c[b]) for c in common)
    adjusted = {a: None, b: None}
    if total:
        adjusted = {label: sum((len(c[a])+len(c[b]))/total *
                               sum(r["progressed"] for r in c[label])/len(c[label]) for c in common) for label in (a, b)}
    ra, rb = raw[a]["continue_rate"], raw[b]["continue_rate"]
    interval = None
    if ra is not None and rb is not None:
        ia, ib = raw[a]["continue_wilson95"], raw[b]["continue_wilson95"]
        interval = [max(-1, ra-rb-sqrt((ra-ia[0])**2+(ib[1]-rb)**2)),
                    min(1, ra-rb+sqrt((ia[1]-ra)**2+(rb-ib[0])**2))]
    return {"feature": feature, "group_a": a, "group_b": b, "raw": raw,
            "raw_difference": ra-rb if ra is not None and rb is not None else None,
            "raw_difference_newcombe95": interval, "strata": list(strata), "common_strata": len(common),
            "common_n": {label: sum(len(c[label]) for c in common) for label in (a, b)},
            "standardized_rate": adjusted,
            "standardized_difference": adjusted[a]-adjusted[b] if total else None,
            "method": "共同覆盖的需求×入口×周×设备单元，按两组总人数加权；描述性比较"}


def _branches(items: list[dict[str, Any]], end: datetime) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    specs = [("entry_continue", "entry", "progress", "入口后继续 vs 无进一步主动操作"),
             ("browse_use", "browse", "use", "浏览后提交任务 vs 未提交"),
             ("attempt_continue", "use", "repeat_or_intent", "首次尝试后再操作/进入付费路径 vs 停止"),
             ("use_register", "use", "register", "未见注册的尝试后注册 vs 未见注册"),
             ("signup_use", "register", "use", "注册后继续原任务 vs 未继续"),
             ("use_commercial", "use", "intent", "使用后进入付费路径 vs 未进入")]
    output, rows_by_branch = [], {}
    for key, anchor, target, title in specs:
        eligible = [i for i in items if i["anchors"][anchor] is not None]
        removed_prior = 0
        if key == "browse_use":
            def prior_use(i):
                return i["anchors"]["use"] is not None and i["anchors"]["use"] <= i["anchors"]["browse"]
            removed_prior = sum(prior_use(i) for i in eligible)
            eligible = [i for i in eligible if not prior_use(i)]
        if key == "use_register":
            def prior_registration(i):
                known = [at for at in (i["anchors"]["register"], i["account_signup_at"]) if at]
                return bool(known and min(known) <= i["anchors"]["use"])
            removed_prior = sum(prior_registration(i) for i in eligible)
            eligible = [i for i in eligible if not prior_registration(i)]
        if key == "use_commercial":
            # Prevent subsequent intent in an already purchasing Visit being counted as first conversion.
            def prior_purchase(i):
                known = [at for at in (i["anchors"]["payment"], i["account_purchase_at"]) if at]
                prior_intent = i["anchors"]["intent"] is not None and i["anchors"]["intent"] <= i["anchors"]["use"]
                return bool(known and min(known) <= i["anchors"]["use"]) or prior_intent
            removed_prior = sum(prior_purchase(i) for i in eligible)
            eligible = [i for i in eligible if not prior_purchase(i)]
        mature = [i for i in eligible if i["anchors"][anchor]+WINDOW <= end]
        rows = [_record(i, i["anchors"][anchor], _target_events(i, target), key) for i in mature]
        rows_by_branch[key] = rows
        features = ["device", "browser", "utm_content"] if key == "entry_continue" else ["auth_before", "signup_before", "choice_before", "attachments", "prior_use_count"]
        contrasts = []
        for feature in features:
            vals = Counter(str(r.get(feature, r["features"].get(feature, "unknown"))) for r in rows)
            compare = ("true", "false") if feature in {"auth_before", "signup_before", "choice_before", "attachments"} else tuple(k for k, _ in vals.most_common(2))
            if len(compare) != 2 or not all(vals[c] for c in compare): continue
            strata = ("demand", "entry_path", "week") if feature == "device" else ("demand", "entry_path", "week", "device")
            contrasts.append(_contrast(rows, feature, compare[0], compare[1], strata))
        # UTM same-demand variant contrast is explicit, not the two largest unrelated cards.
        if key == "entry_continue":
            cards = sorted({r["utm_content"] for r in rows if "fix-blurry" in r["utm_content"]})
            if len(cards) >= 2:
                contrasts.append(_contrast(rows, "utm_content", cards[0], cards[1]))
        profiles = []
        for feature in features:
            for val in sorted({str(r.get(feature, r["features"].get(feature, "unknown"))) for r in rows}):
                chosen = [r for r in rows if str(r.get(feature, r["features"].get(feature, "unknown"))) == val]
                profiles.append({"feature": feature, "value": val, **_summary(chosen)})
        output.append({"key": key, "title": title, "anchor": anchor, "target": target,
                       **_summary(rows), "observation_incomplete": len(eligible)-len(mature),
                       "prior_target_excluded": removed_prior,
                       "by_demand": _breakdowns(rows, ("demand",)),
                       "by_entry_device_week": _breakdowns(rows, ("demand", "entry_path", "device", "week")),
                       "pre_anchor_profiles": profiles, "contrasts": contrasts,
                       "meaning": ("未见结果完成；此处用首次任务尝试后的重复/付费意图作为替代" if key == "attempt_continue" else
                                   "注册前结果不可观测；用首次尝试后注册代理，下载门槛另作直接场景诊断" if key == "use_register" else None)})
    return output, rows_by_branch


def _auth(items: list[dict[str, Any]], end: datetime) -> dict[str, Any]:
    records = []
    immature = 0
    for item in items:
        anchor = item["anchors"]["auth"]
        if not anchor: continue
        if anchor+WINDOW > end:
            immature += 1
            continue
        ev = next(e for e in item["pages"] if e["at"] == anchor and _is_auth(e["path"]))
        before_pages = [e for e in item["pages"] if e["at"] < anchor and not _is_auth(e["path"])]
        previous = before_pages[-1]["path"] if before_pages else item["entry_path"]
        pages_after = [e for e in item["pages"] if anchor < e["at"] <= anchor+WINDOW and not _is_auth(e["path"])]
        return_page = pages_after[0] if pages_after else None
        row = _record(item, anchor, _target_events(item, "use"), "auth_use")
        identity = _first(item["events"], REGISTER | {"login"}, anchor, anchor+WINDOW)
        redirect = _path(ev["props"].get("redirect_path"))
        row.update({"previous_path": previous, "redirect_path": redirect or "unknown",
                    "returned_page": return_page["path"] if return_page else None,
                    "page_after_auth": bool(return_page), "identity_after_auth": bool(identity),
                    "same_path_return": bool(return_page and return_page["path"] == previous),
                    "studio_return": bool(return_page and return_page["path"].startswith("/studio")),
                    "return_then_use": bool(return_page and _first(item["events"], {"send_message"}, return_page["at"], anchor+WINDOW))})
        records.append(row)
    return {**_summary(records), "observation_incomplete": immature,
            "identity_after_auth": sum(r["identity_after_auth"] for r in records),
            "page_after_auth": sum(r["page_after_auth"] for r in records),
            "same_path_return": sum(r["same_path_return"] for r in records),
            "return_then_use": sum(r["return_then_use"] for r in records),
            "by_previous_redirect": _breakdowns(records, ("previous_path", "redirect_path")),
            "by_demand_browser": _breakdowns(records, ("demand", "browser")), "rows": records,
            "meaning": "认证页为起点；login仅为身份恢复；续接用严格更晚提交表示，不证明恢复了同一task_id"}


def _gates(items: list[dict[str, Any]], end: datetime) -> dict[str, Any]:
    rows = []
    immature = 0
    for item in items:
        seen = set()
        for ev in item["events"]:
            if ev["name"] not in {"guest_gate_shown", "paywall_shown"}: continue
            scene = str(ev["props"].get("scene") or "unknown")
            reason = str(ev["props"].get("reason") or "unknown")
            key = (ev["name"], scene, reason)
            if key in seen: continue
            seen.add(key)
            anchor = ev["at"]
            if anchor+WINDOW > end:
                immature += 1
                continue
            row = _record(item, anchor, _target_events(item, "intent"), "gate_intent")
            row.update({"gate_event": ev["name"], "scene": scene, "reason": reason,
                        "surface": str(ev["props"].get("surface") or "unknown"),
                        "is_download": scene.lower() == "download" or reason.lower() == "download",
                        "prior_registered": row["features"]["signup_before"], "prior_use_count": row["features"]["prior_use_count"]})
            for target in ("register", "use", "pricing", "intent", "checkout", "purchase"):
                at = _at(_first(_target_events(item, target), after=anchor, until=anchor+WINDOW))
                row[target+"_after"] = bool(at)
                row[target+"_seconds"] = (at-anchor).total_seconds() if at else None
            row["register_then_use"] = bool(row["register_after"] and
                _first(item["events"], {"send_message"}, anchor+timedelta(seconds=row["register_seconds"]), anchor+WINDOW))
            rows.append(row)
    def totals(selected):
        out = {"exposures": len(selected), "visits": len({r["visit_id"] for r in selected})}
        for target in ("register", "use", "pricing", "intent", "checkout", "purchase"):
            y = sum(r[target+"_after"] for r in selected)
            out[target+"_after"] = y
            out[target+"_rate"] = _rate(y, len(selected))
        out["no_later_record"] = sum(not r["any_later"] for r in selected)
        out["register_then_use"] = sum(r["register_then_use"] for r in selected)
        return out
    cells = defaultdict(list)
    for r in rows: cells[(r["gate_event"], r["scene"], r["reason"], r["surface"])].append(r)
    download = [r for r in rows if r["is_download"]]
    # Comparisons retain pre-exposure task depth and identity to reduce obvious mixing.
    contrasts = []
    for event in {r["gate_event"] for r in rows}:
        selected = [dict(r, gate_group="download" if r["is_download"] else "other") for r in rows if r["gate_event"] == event]
        for target in ("register", "intent", "checkout"):
            target_rows = [dict(r, progressed=r[target+"_after"], seconds_to_target=r[target+"_seconds"]) for r in selected]
            if {r["gate_group"] for r in selected} == {"download", "other"}:
                cmp = _contrast(target_rows, "gate_group", "download", "other", ("demand", "entry_path", "week", "device", "prior_registered", "prior_use_count"))
                cmp.update(gate_event=event, target=target)
                contrasts.append(cmp)
    direct_download_signup = [i for i in items if any(e["name"] == "guest_signup" and
                              str(e["props"].get("reason") or "").lower() == "download" for e in i["events"])]
    return {"download": totals(download), "all_scenes": totals(rows), "observation_incomplete": immature,
            "download_reason_signup_visits": len(direct_download_signup),
            "by_scene": [{"gate_event": k[0], "scene": k[1], "reason": k[2], "surface": k[3], **totals(v)} for k, v in sorted(cells.items())],
            "download_by_demand": [{"demand": d, **totals([r for r in download if r["demand"] == d])} for d in sorted({r["demand"] for r in download})],
            "contrasts": contrasts, "download_rows": download,
            "meaning": "每Visit×事件×场景×原因首次曝光；分母是曝光场景组，同Visit可跨组。下载门槛不等于下载成功。"}


def _landmarks(items: list[dict[str, Any]], end: datetime) -> dict[str, Any]:
    results = []
    for minutes in (2, 5):
        rows, early_intent, incomplete = [], 0, 0
        for item in items:
            first = item["anchors"]["use"]
            if not first: continue
            if first+WINDOW > end:
                incomplete += 1
                continue
            landmark = first+timedelta(minutes=minutes)
            if _first(_target_events(item, "intent"), until=landmark):
                early_intent += 1
                continue
            early_sends = [e for e in item["send_events"] if first < e["at"] <= landmark]
            row = _record(item, landmark, [e for e in _target_events(item, "intent") if e["at"] <= first+WINDOW], "landmark_intent")
            # _record usually uses +30m; use the original first-use 30m endpoint here.
            row["any_later"] = any(landmark < e["at"] <= first+WINDOW for e in item["events"])
            row["valid_later"] = any(landmark < e["at"] <= first+WINDOW and
                                     (e["name"] in ACTIVE or (e["page"] and not e["path"].startswith("/studio/task")))
                                     for e in item["events"])
            row["repeat"] = "true" if early_sends else "false"
            modes = {_bool(e["props"].get("is_new_task")) for e in early_sends}
            row["repeat_mode"] = ("ambiguous_first_send" if item["same_time_first_sends"] else "none" if not early_sends else
                                  "new_and_existing" if True in modes and False in modes else "new_task_style" if True in modes else
                                  "existing_task_style" if False in modes else "unknown")
            rows.append(row)
        clear = [r for r in rows if r["repeat_mode"] != "ambiguous_first_send"]
        cmp = _contrast(clear, "repeat", "true", "false")
        results.append({"landmark_minutes": minutes, "early_intent_excluded": early_intent,
                        "observation_incomplete": incomplete, **_summary(rows),
                        "by_repeat_mode": _breakdowns(rows, ("repeat_mode",)),
                        "by_demand_repeat": _breakdowns(rows, ("demand", "repeat")), "comparison": cmp})
    return {"windows": results, "meaning": "前2/5分钟冻结重复使用特征，之后至首次使用+30分钟观察新付费意图；不要求landmark后仍活跃"}


def _commercial(items: list[dict[str, Any]], end: datetime) -> dict[str, Any]:
    definitions = [
        ("use_to_pricing", {"send_message"}, {"pricing_opened", "view_item_list"}),
        ("pricing_to_selection", {"pricing_opened", "view_item_list"}, {"plan_clicked", "paywall_cta_click"}),
        ("selection_to_checkout", {"plan_clicked", "paywall_cta_click"}, CHECKOUT),
        ("checkout_to_purchase", CHECKOUT, {"purchase"}),
        ("checkout_to_use", CHECKOUT, {"send_message"}),
    ]
    paths = []
    for key, from_names, target_names in definitions:
        eligible = [(i, _first(i["events"], from_names)) for i in items]
        eligible = [(i, e) for i, e in eligible if e]
        mature = [(i, e) for i, e in eligible if e["at"]+WINDOW <= end]
        rows = [_record(i, e["at"], [x for x in i["events"] if x["name"] in target_names], key)
                for i, e in mature]
        paths.append({"key": key, **_summary(rows), "arrived": len(eligible),
                      "observation_incomplete": len(eligible)-len(mature),
                      "by_demand": _breakdowns(rows, ("demand",))})
    checkout_actions = Counter()
    return_outcomes = Counter()
    counts = {}
    for label, names in (("pricing", {"pricing_opened", "view_item_list"}),
                         ("selection", {"plan_clicked", "paywall_cta_click"}), ("checkout", CHECKOUT),
                         ("checkout_return", {"checkout_returned"}), ("purchase", {"purchase"})):
        counts[label] = sum(any(e["name"] in names for e in i["events"]) for i in items)
    for i in items:
        checkout = _first(i["events"], {"begin_checkout"})
        if checkout:
            checkout_actions[str(checkout["props"].get("action_type") or "unknown")] += 1
        returned = _first(i["events"], {"checkout_returned"})
        if returned:
            return_outcomes[(str(returned["props"].get("outcome") or "unknown"),
                             str(returned["props"].get("via") or "unknown"))] += 1
    return {"visit_reach": counts, "ordered_transitions": paths,
            "checkout_action_type": [{"action_type": a, "visits": n} for a, n in checkout_actions.most_common()],
            "checkout_returns": [{"outcome": o, "via": v, "visits": n} for (o, v), n in return_outcomes.most_common()],
            "meaning": "真实支付路径允许跳过价格/选套餐节点；TRIAL代表结账动作类型，非试用转付费；checkout_returned不等于purchase。"}


def _paths(items: list[dict[str, Any]], rows_by_branch: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    all_patterns = Counter()
    representatives = []
    by_id = {i["visit_id"]: i for i in items}
    edges = Counter()
    sequence_by_id = {}
    for item in items:
        labels = []
        for e in item["events"]:
            if e["page"] or e["name"] in ACTIVE | {"guest_gate_shown", "paywall_shown", "purchase", "skill_import_failed"}:
                label = _event_label(e)
                if not labels or labels[-1] != label: labels.append(label)
        labels = labels[:24]
        sequence_by_id[item["visit_id"]] = labels
        all_patterns[tuple(labels)] += 1
        for a, b in zip(labels, labels[1:]): edges[(a, b)] += 1
    for branch, rows in rows_by_branch.items():
        for progressed in (True, False):
            selected = [r for r in rows if r["progressed"] is progressed]
            # Modal path, deterministic selection; pseudonymized IDs only.
            freq = Counter(tuple(sequence_by_id[r["visit_id"]]) for r in selected)
            for path, n in freq.most_common(2):
                r = next(r for r in selected if tuple(sequence_by_id[r["visit_id"]]) == path)
                item = by_id[r["visit_id"]]
                anchor = _dt(r["anchor_at"])
                timeline = [{"seconds_from_anchor": round((e["at"]-anchor).total_seconds(), 3), "event": _event_label(e)}
                            for e in item["events"] if anchor-timedelta(minutes=3) <= e["at"] <= anchor+WINDOW]
                representatives.append({"branch": branch, "progressed": progressed, "demand": r["demand"],
                                        "example_id": hashlib.sha256(r["visit_id"].encode()).hexdigest()[:10],
                                        "pattern_visits": n, "timeline": timeline[:50]})
    return {"top_patterns": [{"path": list(p), "visits": n} for p, n in all_patterns.most_common(30)],
            "edges": [{"from": a, "to": b, "transitions": n} for (a, b), n in edges.most_common(100)],
            "representative_paths": representatives, "pattern_limit_steps": 24}


def analyze_branches(visits: Iterable[Mapping[str, Any]], events: Iterable[Mapping[str, Any]],
                     observation_start: Any, observation_end: Any) -> dict[str, Any]:
    input_visits = list(visits)
    visits = [v for v in input_visits if v.get("quality_eligible") is not False]
    start, end = _dt(observation_start), _dt(observation_end)
    if not start or not end or end <= start:
        raise ValueError("observation_start must precede observation_end")
    items, quality = _normalize(visits, events, start, end)
    axis, node_rows = _node_outputs(items, end)
    branches, branch_rows = _branches(items, end)
    quality.update({"node_partitions_conserved": all(s["denominator"] == s["continued"]+s["no_later_record"]+s["other_later_activity"] for s in axis),
                    "branch_partitions_conserved": all(s["denominator"] == s["continued"]+s["no_later_record"]+s["other_later_activity"] for s in branches),
                    "use_before_register_visits": sum(i["anchors"]["use"] is not None and i["anchors"]["register"] is not None and
                                                      i["anchors"]["use"] < i["anchors"]["register"] for i in items)})
    quality.update(input_visits_before_quality_filter=len(input_visits),
                   quality_ineligible_visits_excluded=len(input_visits)-len(visits),
                   quality_filter="quality_eligible is not false")
    spec = importlib.util.spec_from_file_location("meigen_analytics_breakpoint", Path(__file__).with_name("analytics_breakpoint.py"))
    breakpoint_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(breakpoint_module)
    historical_diagnostic = breakpoint_module.analyze_breakpoint(items, end)
    for check in historical_diagnostic["independent_anchor_recounts"]:
        node = next(x for x in axis if x["node"] == check["node"])
        check["matches_main"] = (check["denominator"], check["continued"]) == (node["denominator"], node["continued"])
    quality["independent_anchor_recounts_match"] = all(x["matches_main"] for x in historical_diagnostic["independent_anchor_recounts"])
    return {"meta": {"unit": "strict MeiGen entry Visit", "observation_start": _iso(start), "observation_end": _iso(end),
                      "node_window_minutes": 30, "timezone": "Asia/Shanghai", "compare_period": "calendar week beginning Monday",
                      "ordering": "strictly later timestamps; same timestamp targets reported separately; entry landing allows equality",
                      "rate_semantics": "no_target splits into no_later_record and other_later_activity; neither is a browser-close event",
                      "effective_action_rule": "Direct custom actions or changed page; after a use anchor /studio/task* route PVs are excluded as independent continuation; raw later-record and no-target metrics remain separate.",
                      "coverage_note": "历史全量按周拆分；事件首次出现之前不作为已完成覆盖的证据。单次访问分岔，账户后续另算。"},
            "quality": quality, "axis": axis,
            "arrival_rows": [{"visit_id": i["visit_id"], "node": node, "anchor_at": _iso(i["anchors"][node]),
                              "complete": i["anchors"][node]+WINDOW <= end, "demand":i["demand"],
                              "device":i["device"], "browser":i["browser"], "week":i["week"]}
                             for i in items for node in NODE_ORDER if i["anchors"][node] is not None],
            "node_rows": [r for rows in node_rows.values() for r in rows],
            "branches": branches, "auth_resume": _auth(items, end), "download_gate": _gates(items, end),
            "early_repeat_to_intent": _landmarks(items, end), "commercial_path": _commercial(items, end),
            "independent_diagnostics": historical_diagnostic,
            "paths": _paths(items, branch_rows),
            "notes": ["send_message表示任务/消息提交，不等于生成结果完成。", "purchase支付信号、结账意图和价格浏览分别统计。",
                      "无通用结果/下载完成事件时采用用户允许的使用、重复、注册及支付代理；不将缺失字段填成零。",
                      "同需求差异为描述性关联；共同覆盖权重不保证消除意愿、任务复杂度和历史埋点变化。"]}
