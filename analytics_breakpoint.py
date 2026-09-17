"""Recomputable, reviewed September 10 journey-change diagnostic.

Called by analytics_branches with its normalized Visit sequences. The named
historical windows are fixed evidence; a later refresh must not relabel this as a
newly discovered change. No database access and no shared-state mutation.
"""
from collections import Counter
from datetime import timedelta, timezone

CST = timezone(timedelta(hours=8))
WINDOW = timedelta(minutes=30)
INTENT = {'plan_clicked', 'paywall_cta_click', 'begin_checkout', 'checkout_opened'}
REG = {'sign_up', 'guest_signup'}


def _period(item):
    date = item['anchors']['entry'].astimezone(CST).date().isoformat()
    if '2026-09-03' <= date <= '2026-09-09': return 'before_7d'
    if '2026-09-10' <= date <= '2026-09-15': return 'after_6d'
    return None


def _auth(path):
    return '/auth' in path or '/login' in path or '/sign-in' in path


def _label(path):
    if not path: return '无后续PV'
    if _auth(path): return '认证页'
    if path.startswith('/studio'): return 'Studio'
    if path.startswith('/tools/'): return path.replace('/tools/', '工具:')
    return path


def _later(item, anchor, names):
    return next((e for e in item['events'] if e['name'] in names and anchor < e['at'] <= anchor+WINDOW), None)


def _summarize(items):
    registrations = [i for i in items if i['anchors']['register'] is not None]
    auths = [i for i in items if i['anchors']['auth'] is not None]
    post_reg = Counter()
    for item in registrations:
        at = item['anchors']['register']
        page = next((e for e in item['pages'] if at < e['at'] <= at+WINDOW), None)
        post_reg[_label(page['path'] if page else None)] += 1
    returned = same = identity = auth_use = 0
    paths = Counter()
    for item in auths:
        at = item['anchors']['auth']
        before = [e for e in item['pages'] if e['at'] < at and not _auth(e['path'])]
        previous = before[-1]['path'] if before else item['entry_path']
        page = next((e for e in item['pages'] if at < e['at'] <= at+WINDOW and not _auth(e['path'])), None)
        returned += page is not None
        same += page is not None and page['path'] == previous
        identity += _later(item, at, REG | {'login'}) is not None
        auth_use += _later(item, at, {'send_message'}) is not None
        path = page['path'] if page else '无后续PV'
        if path.startswith('/studio/task/'): path = '/studio/task/<id>'
        paths[path] += 1
    active = {'send_message', 'ui_click', 'onboarding_pick', 'sign_up', 'guest_signup', 'purchase',
              'skill_import_confirmed', 'skill_import_cancelled', 'deck_checkpoint_continue',
              'pricing_opened', 'plan_clicked', 'paywall_cta_click', 'begin_checkout', 'checkout_opened'}
    out = {'n':len(items), 'demand_counts':dict(Counter(i['demand'] for i in items)),
           'card_counts':dict(Counter(i['utm_content'] for i in items)),
           'path_counts':dict(Counter(i['entry_path'] for i in items)),
           'device_counts':dict(Counter(i['device'] for i in items)),
           'entry_progress':sum(any(i['anchors']['entry'] < e['at'] <= i['anchors']['entry']+WINDOW and
                                   (e['name'] in active or e['page'] and e['path'] != i['entry_path'])
                                   for e in i['events']) for i in items),
           'registration':len(registrations), 'use':sum(i['anchors']['use'] is not None for i in items),
           'register_then_use':sum(_later(i, i['anchors']['register'], {'send_message'}) is not None for i in registrations),
           'auth':len(auths), 'auth_then_identity':identity, 'auth_then_use':auth_use,
           'auth_returned':returned, 'auth_same_path_return':same,
           'auth_return_paths':dict(paths), 'after_registration_page':dict(post_reg),
           'event_counts':dict(Counter(e['name'] for i in items for e in i['events'] if e['name']))}
    for node in ('register','use','auth','pricing','intent','checkout','reuse'):
        out[node+'_within_entry_30m'] = sum(i['anchors'][node] is not None and
                                          i['anchors'][node] <= i['anchors']['entry']+WINDOW for i in items)
    return out


def analyze_breakpoint(items, observation_end):
    eligible = [i for i in items if i['anchors']['entry']+WINDOW <= observation_end]
    group_names = ['all'] + sorted({i['demand'] for i in eligible})
    periods = {}
    for group in group_names:
        periods[group] = {}
        for p in ('before_7d','after_6d'):
            selected = [i for i in eligible if _period(i)==p and (group=='all' or i['demand']==group)]
            periods[group][p] = dict(_summarize(selected), days=7 if p=='before_7d' else 6)
    cells = {}
    for i in eligible:
        p = _period(i)
        if p is None: continue
        key = (i['demand'],i['entry_path'],i['utm_content'])
        cells.setdefault(key,{'before_7d':[],'after_6d':[]})[p].append(i)
    matched = []
    for key, values in cells.items():
        if all(values.values()):
            matched.append({'demand':key[0],'entry_path':key[1],'utm_content':key[2],
                            **{p:_summarize(v) for p,v in values.items()}})
    matched.sort(key=lambda x: -min(x['before_7d']['n'],x['after_6d']['n']))
    repeat = []
    for d in ('blur','product_video'):
        for p in ('before0910','after0910'):
            bucket = []
            for i in eligible:
                at = i['anchors']['use']
                if at is None or at+WINDOW > observation_end or i['demand'] != d: continue
                date = i['anchors']['entry'].astimezone(CST).date().isoformat()
                if ('before0910' if date<'2026-09-10' else 'after0910') != p: continue
                mark = at+timedelta(minutes=5)
                if any(e['name'] in INTENT and e['at'] <= mark for e in i['events']): continue
                early_repeat = any(e['name']=='send_message' and at<e['at']<=mark for e in i['events'])
                future_intent = any(e['name'] in INTENT and mark<e['at']<=at+WINDOW for e in i['events'])
                bucket.append((early_repeat,future_intent))
            for rep in (True,False):
                chosen = [x for x in bucket if x[0] == rep]
                repeat.append({'demand':d,'period':p,'repeated5m':rep,'n':len(chosen),
                               'later_intent':sum(x[1] for x in chosen)})
    # Independent basic recounts; main branch logic is not reused here.
    audit = []
    for node,names in (('register',REG),('use',{'send_message'})):
        denominator=numerator=0
        for i in items:
            anchor=next((e['at'] for e in i['events'] if e['name'] in names),None)
            if anchor is None or anchor+WINDOW>observation_end: continue
            denominator += 1
            numerator += any(e['name']=='send_message' and anchor<e['at']<=anchor+WINDOW for e in i['events'])
        audit.append({'node':node,'denominator':denominator,'continued':numerator})
    return {'topic':'2026-09-10 historical journey change', 'reviewed_fixed_window':True,
            'windows':{'before':'2026-09-03 through 2026-09-09 CST, 7 complete days',
                       'after':'2026-09-10 through 2026-09-15 CST, 6 complete days'},
            'period_break':periods,'same_demand_entry_card':matched,
            'five_minute_repeat_by_demand_period':repeat,
            'independent_anchor_recounts':audit,
            'node_rates':'Rates use fixed 30-minute outcomes; arrival counts are per source Visit and have explicit within_entry_30m companions.',
            'interpretation':'Fixed reviewed history. A changed route can represent changed conversion, changed task location, or both; do not relabel the lower Studio send count as verified product failure.'}
