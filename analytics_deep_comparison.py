"""Recomputable, aggregate-only continuation/return/commercial diagnostics.

Reads a complete local generation; never queries a database. All explanatory
features precede the outcome window. Public entry points: analyze_generation(path)
and analyze_deep(visits, events, facts, history, cutoff, identity_context, bounds).
"""
from __future__ import annotations
import argparse
import json
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path
from statistics import median

from analytics_branches import (_dt, _normalize, _is_auth, _path, _is_page, _bool,
                                _features, _wilson, _branches, _record, _target_events, _quantile, ACTIVE, INTENT, CHECKOUT, REGISTER)
from analytics_accounts import link_info, CHOICES

ROOT = Path(__file__).resolve().parent
PRICE = {'pricing_opened', 'view_item_list'}
LABELS = {'send_message': '提交任务', 'sign_up': '注册信号', 'guest_signup': '注册信号',
          'login': '登录信号', 'ui_click': '选择功能', 'onboarding_pick': '引导选择',
          'paywall_shown': '付费提示', 'paywall_cta_click': '点击付费入口',
          'guest_gate_shown': '注册门槛', 'pricing_opened': '打开价格',
          'view_item_list': '查看套餐', 'pricing_closed': '关闭价格',
          'plan_clicked': '选择套餐', 'begin_checkout': '开始结账',
          'checkout_opened': '打开结账', 'purchase': '支付记录',
          'select_billing_cycle': '选择计费周期', 'offer_card_dismiss': '关闭优惠'}
DIM_LABELS = {'attachments': '首个任务携带素材', 'auth_before': '首次使用前经过认证页',
              'signup_before': '首次使用前已有注册信号', 'choice_before': '首次使用前主动选择功能',
              'prior_page_depth': '首次使用前页面深度', 'elapsed_before': '入口至首次使用耗时',
              'pricing_before': '首次使用前查看价格', 'device': '设备', 'utm_content': '引流素材',
              'week': '进入周', 'send_band': '首24小时提交次数', 'early_attachments': '首24小时携带素材',
              'early_download_gate': '首24小时遇到下载门槛', 'early_video_preview': '首24小时遇到视频预览付费提示',
              'early_auth': '首24小时经过认证页', 'early_choice': '首24小时主动选择功能',
              'early_pricing': '首24小时查看价格', 'early_visit_band': '首24小时访问次数',
              'early_depth_band': '首24小时页面深度', 'early_registered_use': '首24小时注册后提交', 'early_registration': '首24小时注册信号',
              'prior_use': 'MeiGen首次归因前已有使用', 'repeat_early': '固定前置窗内重复提交',
              'early_gate': '固定前置窗内遇到付费门槛'}
IMM_FEATURES = ['attachments', 'auth_before', 'signup_before', 'choice_before',
                'prior_page_depth', 'elapsed_before', 'pricing_before', 'device', 'utm_content', 'week']
RET_FEATURES = ['send_band', 'early_attachments', 'early_auth', 'early_choice',
                'early_download_gate', 'early_video_preview', 'early_pricing',
                'early_visit_band', 'early_depth_band', 'early_registered_use', 'early_registration', 'prior_use', 'device', 'week']


def ratio(n, d):
    return n / d if d else None


def band(n):
    return '0' if n == 0 else '1' if n == 1 else '2+'


def value(v):
    return 'true' if v is True else 'false' if v is False else str(v) if v is not None else 'unknown'


def normalized(raw):
    return {'at': _dt(raw['created_at']), 'name': str(raw.get('event_name') or ''),
            'path': _path(raw.get('url_path')), 'page': _is_page(raw),
            'props': raw.get('properties') or {}, 'visit_id': raw['visit_id'],
            'event_id': raw['event_id']}


def compact_path(events, start, end, limit=12):
    labels = []
    for e in events:
        if not start <= e['at'] <= end:
            continue
        if e['page']:
            p = e['path']
            label = '认证页' if _is_auth(p) else '任务页' if p.startswith('/studio/task') else 'Studio' if p.startswith('/studio') else '工具落地页' if p.startswith('/tools/') else '入口/浏览'
        elif e['name'] in LABELS:
            label = LABELS[e['name']]
            if e['name'] in {'paywall_shown', 'paywall_cta_click'}:
                scene = e['props'].get('scene')
                if scene in {'videoPreview', 'download', 'exitOfferStage1', 'exitOfferStage2', 'lowCredit', 'videoGen', 'concurrent'}:
                    label += '·' + str(scene)
        else:
            continue
        if not labels or labels[-1] != label:
            labels.append(label)
    if len(labels) > limit:
        labels = labels[:3]+['…']+labels[-(limit-4):]
    return tuple(labels)


def profile(rows):
    n = len(rows)
    booleans = ['attachments', 'auth_before', 'signup_before', 'choice_before', 'pricing_before',
                'early_attachments', 'early_auth', 'early_choice', 'early_download_gate',
                'early_video_preview', 'early_pricing', 'early_registered_use', 'early_registration', 'prior_use',
                'repeat_early', 'early_gate']
    metrics = {}
    for key in booleans:
        if not any(key in r for r in rows):
            continue
        known = [r for r in rows if value(r.get(key)) in {'true', 'false'}]
        count = sum(value(r.get(key)) == 'true' for r in known)
        metrics[key] = {'n': count, 'denominator': len(known), 'rate': ratio(count, len(known)), 'unknown': n-len(known)}
    for key in ['seconds_to_use', 'pre_page_depth', 'early_sends', 'early_page_depth', 'early_visits', 'pre_sends', 'pre_use_seconds', 'seconds_to_anchor', 'pre_active_actions', 'pre_backtracks', 'pre_distinct_pages']:
        values = [r[key] for r in rows if r.get(key) is not None]
        if values:
            metrics[key] = {'median': median(values), 'n': len(values)}
    return {'n': n, 'metrics': metrics}


def compare(rows, positive='continued', features=IMM_FEATURES, with_paths=True):
    yes = [r for r in rows if r[positive]]
    no = [r for r in rows if not r[positive]]
    rates = []
    for key in features:
        groups = defaultdict(list)
        for r in rows:
            groups[value(r.get(key))].append(r)
        # All small cohorts remain visible in counts; cap high-cardinality UTM.
        for val, rs in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:12]:
            y = sum(bool(r[positive]) for r in rs)
            rates.append({'feature': key, 'label': DIM_LABELS.get(key, key), 'value': val,
                          'n': len(rs), 'continued': y, 'stopped': len(rs)-y,
                          'rate': ratio(y, len(rs)), 'ci95': _wilson(y, len(rs)),
                          'continued_group_share': ratio(y, len(yes)),
                          'stopped_group_share': ratio(len(rs)-y, len(no))})
    result = {'n': len(rows), 'continued': len(yes), 'stopped': len(no),
              'rate': ratio(len(yes), len(rows)), 'ci95': _wilson(len(yes), len(rows)),
              'profiles': {'continued': profile(yes), 'stopped': profile(no)},
              'feature_rates': rates}
    if with_paths:
        result['paths'] = {}
        for label, rs in [('continued', yes), ('stopped', no)]:
            counts = Counter(r.get('path', ()) for r in rs)
            result['paths'][label] = [{'steps': list(p), 'n': n, 'group_share': ratio(n, len(rs))}
                                      for p, n in counts.most_common(3) if p]
    return result


def standardize(rows, feature, positive, strata=('demand', 'week', 'device'), low='false', high='true'):
    cells = defaultdict(lambda: defaultdict(list))
    for r in rows:
        v = value(r.get(feature))
        if v in (low, high):
            cells[tuple(r.get(k, 'unknown') for k in strata)][v].append(r)
    common = [c for c in cells.values() if len(c[low]) >= 5 and len(c[high]) >= 5]
    n = sum(len(c[low])+len(c[high]) for c in common)
    rates = {}
    for level in (low, high):
        rates[level] = sum((len(c[low])+len(c[high])) / n * sum(r[positive] for r in c[level])/len(c[level]) for c in common) if n else None
    return {'feature': feature, 'strata': list(strata), 'minimum_per_group_per_stratum': 5,
            'common_n': n, 'eligible_n': sum(value(r.get(feature)) in (low, high) for r in rows),
            'common_strata': len(common), 'low': low, 'high': high, 'standardized_rates': rates,
            'difference_high_minus_low': rates[high]-rates[low] if n else None,
            'interpretation': '同需求×周×设备共同覆盖分层后的描述性差异；不解释为因果。'}


def by_demand(rows, positive='continued', features=IMM_FEATURES, full=True):
    groups = defaultdict(list)
    for r in rows:
        groups[r['demand']].append(r)
    return [{'demand': d, **compare(rs, positive, features if full else [], with_paths=full)}
            for d, rs in sorted(groups.items(), key=lambda kv: -len(kv[1]))]


def _continuation(e, anchor_path):
    # Task submission itself creates task-route PVs. They are not another user action.
    return e['name'] in ACTIVE or (e['page'] and e['path'] != anchor_path and not e['path'].startswith('/studio/task'))


def immediate(items, cutoff):
    rows = []
    incomplete = 0
    for item in items:
        start = item['anchors']['use']
        if not start:
            continue
        if start+timedelta(minutes=30) > cutoff:
            incomplete += 1
            continue
        before = [e for e in item['events'] if e['at'] <= start]
        later = [e for e in item['events'] if start < e['at'] <= start+timedelta(minutes=30)]
        pages = [e for e in before if e['page']]
        anchor_path = pages[-1]['path'] if pages else item['entry_path']
        active = [e for e in later if _continuation(e, anchor_path)]
        row = {k: item[k] for k in ['demand', 'week', 'device', 'utm_content']}
        row.update(_features(item, start))
        row.update(continued=bool(active), seconds_to_use=(start-item['anchors']['entry']).total_seconds(),
                   pre_page_depth=len([e for e in before if e['page']]),
                   repeated=any(e['name']=='send_message' for e in later),
                   intent=any(e['name'] in INTENT for e in later),
                   only_route_or_passive=bool(later) and not active,
                   seconds_to_continue=(active[0]['at']-start).total_seconds() if active else None,
                   path=compact_path(item['events'], item['anchors']['entry'], start+timedelta(minutes=30)))
        rows.append(row)
    main = compare(rows)
    main.update({'title': '首次使用后：继续操作 vs 未再主动操作', 'window_minutes': 30,
                 'observation_incomplete': incomplete,
                 'repeated': sum(r['repeated'] for r in rows), 'intent': sum(r['intent'] for r in rows),
                 'route_or_passive_only': sum(r['only_route_or_passive'] for r in rows),
                 'median_seconds_to_continue': median([r['seconds_to_continue'] for r in rows if r['continued']]) if any(r['continued'] for r in rows) else None,
                 'by_demand': by_demand(rows),
                 'standardized': [standardize(rows, f, 'continued') for f in ['attachments', 'auth_before', 'choice_before']],
                 'definition': '首次send_message后的30分钟内出现再次提交、其他主动事件或不同的非task页面为继续；其余为未再观察到主动操作。自动task路由与付费提示曝光不算继续。',
                 'feature_time': '素材取首次提交参数，其他特征冻结在首次提交之前；停止仅描述本次30分钟，不表示永久流失。'})
    assert main['continued']+main['stopped'] == main['n']
    return main, rows


NODE_SPECS = {
    'entry_continue': ('入口后：继续操作 vs 未推进', '入口', '页面变化或主动操作', None),
    'browse_use': ('浏览后：提交任务 vs 未提交', '首次后续浏览/选择', '提交任务', None),
    'attempt_continue': ('首次操作后：再次使用/付费意图 vs 未达目标', '首次提交任务', '再次提交或进入付费意图', '没有结果完成信号；用首次尝试后的后续动作衡量推进，不称为首次成功。'),
    'use_register': ('未见注册的尝试后：注册 vs 未注册', '首次提交任务', '注册信号', '成功后注册暂无直接结果完成埋点；主比较以未见注册的首次提交为锚点，另列首次结果/下载注册门槛场景。'),
    'signup_use': ('注册后：使用 vs 未使用', '首次注册信号', '注册之后提交任务', None),
    'use_commercial': ('此前未见付费意图：使用后进入 vs 未进入', '首次提交任务且此前未见支付', '付费入口/套餐/结账动作', '观察到的是潜在付费路径，尚未关联真实付款；锚点前已有支付信号者剔除。')
}
STATE_LABELS = {
    'no_later_record':'锚点后无记录',
    'result_or_download_gate':'出现结果/下载相关门槛',
    'repeat_attempt':'出现重复提交，但未达本节点目标',
    'commercial_activity':'有价格/付费动作，但未达本节点目标',
    'auth_activity':'有认证/注册相关记录，但未达本节点目标',
    'other_active':'有其它主动操作',
    'route_or_passive_only':'仅路由/提示等被动记录',
    'last_submit':'最后记录：提交任务',
    'last_result_gate':'最后记录：结果/下载注册门槛',
    'last_signup_gate':'最后记录：其它注册门槛',
    'last_video_gate':'最后记录：视频预览付费提示',
    'last_download_gate':'最后记录：下载付费提示',
    'last_paywall':'最后记录：其它付费提示',
    'last_checkout':'最后记录：结账动作',
    'last_pricing':'最后记录：价格/套餐页动作',
    'last_registration':'最后记录：注册信号',
    'last_login':'最后记录：登录信号',
    'last_auth_page':'最后记录：认证页',
    'last_task_page':'最后记录：任务页',
    'last_studio_page':'最后记录：Studio',
    'last_other_page':'最后记录：其它页面',
    'last_other_action':'最后记录：其它主动动作',
    'last_passive':'最后记录：其它被动事件'
}


def terminal_state(last):
    if last is None:return 'no_later_record'
    name=last['name'];props=last['props']
    if name=='send_message':return 'last_submit'
    if name=='guest_gate_shown':return 'last_result_gate' if props.get('reason') in {'first_result','download'} else 'last_signup_gate'
    if name=='paywall_shown':
        return 'last_video_gate' if props.get('scene')=='videoPreview' else 'last_download_gate' if props.get('scene')=='download' else 'last_paywall'
    if name in CHECKOUT:return 'last_checkout'
    if name in PRICE|{'pricing_closed','select_billing_cycle','plan_clicked'}:return 'last_pricing'
    if name in REGISTER:return 'last_registration'
    if name=='login':return 'last_login'
    if last['page']:
        path=last['path']
        return 'last_auth_page' if _is_auth(path) else 'last_task_page' if path.startswith('/studio/task') else 'last_studio_page' if path.startswith('/studio') else 'last_other_page'
    return 'last_other_action' if name in ACTIVE else 'last_passive'


def make_node_row(item, source_row):
    anchor=_dt(source_row['anchor_at']);end=anchor+timedelta(minutes=30)
    before=[e for e in item['events'] if e['at']<anchor]
    later=[e for e in item['events'] if anchor<e['at']<=end]
    pages=[e for e in before if e['page']]
    distinct=[]
    for e in pages:
        path='/studio/task' if e['path'].startswith('/studio/task') else e['path']
        if not distinct or path!=distinct[-1]:distinct.append(path)
    seen=set();backtracks=0
    for path in distinct:
        if path in seen:backtracks+=1
        seen.add(path)
    # Task parameters at a submission anchor are already available at that instant.
    # For other anchors only earlier sends may contribute; unknown is not false.
    known_sends=[e for e in item['send_events'] if e['at']<=anchor]
    attachment_values=[_bool(e['props'].get('has_attachments')) for e in known_sends]
    attachment=True if True in attachment_values else False if attachment_values and all(v is False for v in attachment_values) else None
    result_gate=any(e['name']=='guest_gate_shown' and e['props'].get('reason') in {'first_result','download'} or e['name']=='paywall_shown' and e['props'].get('scene') in {'videoPreview','download'} for e in later)
    if not later:state='no_later_record'
    elif result_gate:state='result_or_download_gate'
    elif any(e['name']=='send_message' for e in later):state='repeat_attempt'
    elif any(e['name'] in PRICE|INTENT|CHECKOUT for e in later):state='commercial_activity'
    elif any(e['name'] in REGISTER|{'login'} or e['page'] and _is_auth(e['path']) for e in later):state='auth_activity'
    elif source_row['valid_later']:state='other_active'
    else:state='route_or_passive_only'
    row={k:item[k] for k in ['demand','week','device','utm_content']}
    row.update(source_row['features'])
    row.update({'attachments':value(attachment),'continued':source_row['progressed'],
                'seconds_to_anchor':(anchor-item['anchors']['entry']).total_seconds(),
                'pre_page_depth':len(pages),'pre_distinct_pages':len(set(distinct)),
                'pre_active_actions':sum(e['name'] in ACTIVE for e in before),
                'pre_backtracks':backtracks,'seconds_to_target':source_row['seconds_to_target'],
                'stop_state':state,'last_state':terminal_state(later[-1] if later else None),
                'target_before':source_row['target_before'],'target_same_time':source_row['target_same_time'],
                'path':compact_path(item['events'],item['anchors']['entry'],anchor,6)+compact_path(later,anchor+timedelta(microseconds=1),end,6)})
    return row


def node_summary(rows, features=IMM_FEATURES):
    result=compare(rows,features=features)
    positive=[r for r in rows if r['continued']]
    negative=[r for r in rows if not r['continued']]
    times=[r['seconds_to_target'] for r in positive if r['seconds_to_target'] is not None]
    result['advance_time']={'continued':{'n':len(times),'median_seconds':median(times) if times else None,'p90_seconds':_quantile(times,.9)},
                            'stopped':{'n':len(negative),'median_seconds':None,'reason':'未观察到下一步，不能给出到达下一步耗时'}}
    result['stopped_states']=[{'state':s,'label':STATE_LABELS[s],'n':n,'share':ratio(n,len(negative))} for s,n in Counter(r['stop_state'] for r in negative).most_common()]
    result['last_observed_states']=[{'state':s,'label':STATE_LABELS[s],'n':n,'share':ratio(n,len(negative))} for s,n in Counter(r['last_state'] for r in negative).most_common()]
    result['target_before_anchor']=sum(r['target_before'] for r in rows)
    result['target_same_timestamp']=sum(r['target_same_time'] for r in rows)
    assert sum(x['n'] for x in result['stopped_states'])==result['stopped']
    assert sum(x['n'] for x in result['last_observed_states'])==result['stopped']
    return result


def node_comparisons(items,cutoff):
    outputs,raw_rows=_branches(items,cutoff)
    by_id={i['visit_id']:i for i in items}
    nodes=[]
    for existing in outputs:
        key=existing['key'];title,anchor_label,target_label,proxy=NODE_SPECS[key]
        rows=[make_node_row(by_id[r['visit_id']],r) for r in raw_rows[key]]
        grouped=defaultdict(list)
        for r in rows:grouped[r['demand']].append(r)
        node={'key':key,'title':title,'anchor_label':anchor_label,'target_label':target_label,'proxy_note':proxy,
              **node_summary(rows),'observation_incomplete':existing['observation_incomplete'],
              'eligible_arrived':existing['denominator']+existing['observation_incomplete'],
              'prior_target_excluded':existing['prior_target_excluded'],
              'by_demand':[{'demand':d,**node_summary(rs,IMM_FEATURES[:6])} for d,rs in sorted(grouped.items(),key=lambda kv:-len(kv[1]))]}
        anchor_name=existing['anchor']
        arrived=defaultdict(int);unknown=defaultdict(int)
        for item in items:
            at=item['anchors'][anchor_name]
            if not at:continue
            if key=='browse_use' and item['anchors']['use'] is not None and item['anchors']['use']<=at:continue
            if key=='use_commercial' and item['anchors']['intent'] is not None and item['anchors']['intent']<=at:continue
            history_key='account_signup_at' if key=='use_register' else 'account_purchase_at'
            node_key='register' if key=='use_register' else 'payment'
            if key in {'use_register','use_commercial'}:
                known=[t for t in (item['anchors'][node_key],item.get(history_key)) if t]
                if known and min(known)<=at:continue
            arrived[item['demand']]+=1
            if at+timedelta(minutes=30)>cutoff:unknown[item['demand']]+=1
        existing_demands={d['demand'] for d in node['by_demand']}
        for demand in arrived:
            if demand not in existing_demands:node['by_demand'].append({'demand':demand,**node_summary([],IMM_FEATURES[:6])})
        for demand in node['by_demand']:
            demand['eligible_arrived']=arrived[demand['demand']]
            demand['observation_incomplete']=unknown[demand['demand']]
        assert node['n']==existing['denominator'] and node['continued']==existing['continued']
        assert sum(arrived.values())==node['eligible_arrived']
        assert sum(unknown.values())==node['observation_incomplete']
        nodes.append(node)
    # More direct result-related opportunity, where available: these events show a
    # registration gate at a first-result/download scene, not successful download.
    gate_rows=[];incomplete=0;prior_registered=0;gate_arrived=Counter();gate_unknown=Counter()
    for item in items:
        gate=next((e for e in item['events'] if e['name']=='guest_gate_shown' and e['props'].get('reason') in {'first_result','download'}),None)
        if not gate:continue
        known_registration=[t for t in [item['anchors']['register'],item.get('account_signup_at')] if t]
        if known_registration and min(known_registration)<=gate['at']:prior_registered+=1;continue
        gate_arrived[item['demand']]+=1
        if gate['at']+timedelta(minutes=30)>cutoff:
            incomplete+=1;gate_unknown[item['demand']]+=1;continue
        raw=_record(item,gate['at'],_target_events(item,'register'),'result_gate_register')
        row=make_node_row(item,raw);row['gate_reason']=str(gate['props'].get('reason'));gate_rows.append(row)
    gate_groups=defaultdict(list)
    for r in gate_rows:gate_groups[r['demand']].append(r)
    gate={'key':'result_gate_register','title':'首次结果/下载注册门槛后：注册 vs 未注册',
          'anchor_label':'first_result/download注册门槛','target_label':'之后注册信号',
          'proxy_note':'看到了结果相关注册门槛；并无生成成功或下载成功信号，不代表用户已经获得最终成果。',
          **node_summary(gate_rows),'observation_incomplete':incomplete,'prior_target_excluded':prior_registered,
          'eligible_arrived':len(gate_rows)+incomplete,
          'by_demand':[{'demand':d,**node_summary(rs)} for d,rs in sorted(gate_groups.items(),key=lambda kv:-len(kv[1]))]}
    for demand in gate_arrived:
        if not any(d['demand']==demand for d in gate['by_demand']):gate['by_demand'].append({'demand':demand,**node_summary([],IMM_FEATURES[:6])})
    for demand in gate['by_demand']:
        demand['eligible_arrived']=gate_arrived[demand['demand']]
        demand['observation_incomplete']=gate_unknown[demand['demand']]
    return {'window_minutes':30,'nodes':nodes,'result_gate_register':gate,
            'feature_time':'规模按30分钟成熟窗口；耗时/复杂度/认证/功能选择取锚点前，素材含锚点提交时已提供的参数。',
            'group_labels':{'continued':'到达本节点下一步','stopped':'30分钟未达目标'},
            'complexity_note':'页面数量为记录数；返回已见页面包含正常认证返回，不能直接等同操作障碍。',
            'stopped_state_note':'stopped_states是未达目标组在后续窗口的互斥观察分类；last_observed_states才是最后记录。均不是浏览器关闭或离开原因。',
            'duration_note':'两组可比耗时为入口至锚点的前置耗时；目标推进耗时只对已到达下一步者报告。'}


def account_events(facts, history, events, contexts, bounds, cutoff):
    lookup = {r['account_id']: r for r in facts}
    context = {r['session_id']: r for r in contexts}
    grouped = defaultdict(dict)
    visit_bounds = {r['visit_id']: _dt(r['visit_start']) for r in bounds}
    for raw in history:
        li = link_info(context.get(raw['session_id'], {}), cutoff)
        e = normalized(raw)
        visit_bounds[e['visit_id']] = min(visit_bounds.get(e['visit_id'], e['at']), e['at'])
        if li and li[0] in lookup and li[1] <= e['at'] < cutoff:
            grouped[li[0]][e['event_id']] = e
    anchor = {r['anchor_visit']: r for r in facts}
    for raw in events:
        fact = anchor.get(raw['visit_id'])
        if not fact:
            continue
        e = normalized(raw)
        if _dt(fact['t0']) <= e['at'] < cutoff:
            grouped[fact['account_id']][e['event_id']] = e
    return {k: sorted(v.values(), key=lambda e:(e['at'], e['event_id'])) for k,v in grouped.items()}, visit_bounds


def early_features(fact, early):
    sends = [e for e in early if e['name']=='send_message']
    first = sends[0] if sends else None
    pages = [e for e in early if e['page']]
    attachment_vals = [_bool(e['props'].get('has_attachments')) for e in sends]
    attached = True if True in attachment_vals else False if attachment_vals and all(x is False for x in attachment_vals) else None
    return {'demand': fact['entry_family'], 'device': fact.get('device') or 'unknown', 'week': fact['week'],
            'send_band': band(len(sends)), 'early_sends': len(sends), 'early_visits': len({e['visit_id'] for e in early}),
            'early_visit_band': band(len({e['visit_id'] for e in early})), 'early_page_depth': len(pages),
            'early_depth_band': '0–2' if len(pages)<=2 else '3–5' if len(pages)<=5 else '6+',
            'early_attachments': attached, 'early_auth': any(_is_auth(e['path']) for e in pages),
            'early_choice': any(e['name'] in {'ui_click', 'onboarding_pick'} for e in early),
            'early_download_gate': any(e['name'] in {'paywall_shown', 'guest_gate_shown'} and (e['props'].get('scene')=='download' or e['props'].get('reason')=='download') for e in early),
            'early_video_preview': any(e['name']=='paywall_shown' and e['props'].get('scene')=='videoPreview' for e in early),
            'early_pricing': any(e['name'] in PRICE for e in early),
            'early_registered_use': fact['v1_24'], 'early_registration': fact['registered24'], 'prior_use': fact['prior_use'],
            'seconds_to_use': (first['at']-_dt(fact['t0'])).total_seconds() if first else None}


def return_groups(starters, days):
    groups=[]
    selectors=[('no_return',lambda r:not r[f'returned{days}']),
               ('browse_only',lambda r:r[f'returned{days}'] and not r[f'reused{days}']),
               ('reused',lambda r:r[f'reused{days}'])]
    for name,selector in selectors:
        rs=[r for r in starters if selector(r)]
        metrics={}
        for key in ['return_pricing','return_intent','return_checkout','return_video_gate','return_download_gate',
                    'return_auth','return_page','return_active','return_identity_only']:
            k=sum(r[f'{key}{days}'] for r in rs)
            metrics[key]={'n':k,'rate':ratio(k,len(rs))}
        landings=Counter(r[f'return_landing{days}'] for r in rs if r[f'returned{days}'])
        cleaned=Counter()
        for p,n in landings.items():
            cleaned['未见页面记录' if p=='未见页面' else '任务页' if p.startswith('/studio/task') else 'Studio' if p.startswith('/studio') else '认证页' if _is_auth(p) else p if p.startswith(('/tools/','/features/','/explore/')) else '其他页面']+=n
        groups.append({'group':name,**profile(rs),'share':ratio(len(rs),len(starters)),
                       'followup_actions':metrics,'return_landing':[{'label':p,'n':n} for p,n in cleaned.most_common(6)],
                       'paths':[{'steps':list(p),'n':n} for p,n in Counter(r[f'path{days}'] for r in rs).most_common(3) if p]})
    return groups


def retention(facts, grouped, bounds, cutoff):
    base = []
    mismatches = Counter()
    for fact in facts:
        t0 = _dt(fact['t0']); mark = t0+timedelta(days=1)
        es = grouped.get(fact['account_id'], [])
        early = [e for e in es if t0 <= e['at'] < mark]
        row = early_features(fact, early)
        row['t0'] = t0
        row['early_path'] = compact_path(early, t0, mark)
        for days in (7,14):
            end = mark+timedelta(days=days)
            row[f'mature{days}'] = end < cutoff
            later = [e for e in es if mark < bounds.get(e['visit_id'], e['at']) <= end and e['at'] <= end]
            returned = bool(later); reused = any(e['name']=='send_message' for e in later)
            row[f'returned{days}'] = returned; row[f'reused{days}'] = reused
            row[f'path{days}'] = compact_path(later, mark, end)
            for key, actual in [('returned',returned), ('reused',reused)]:
                mismatches[f'{key}{days}'] += actual != bool(fact[f'{key}{days}'])
            # Describes the later window, not a pre-outcome cause.
            row[f'return_pricing{days}'] = any(e['name'] in PRICE for e in later)
            row[f'return_intent{days}'] = any(e['name'] in INTENT for e in later)
            row[f'return_checkout{days}'] = any(e['name'] in CHECKOUT for e in later)
            row[f'return_video_gate{days}'] = any(e['name']=='paywall_shown' and e['props'].get('scene')=='videoPreview' for e in later)
            row[f'return_download_gate{days}'] = any(e['name'] in {'paywall_shown','guest_gate_shown'} and (e['props'].get('scene')=='download' or e['props'].get('reason')=='download') for e in later)
            row[f'return_auth{days}'] = any(e['page'] and _is_auth(e['path']) for e in later)
            row[f'return_page{days}'] = any(e['page'] for e in later)
            row[f'return_active{days}'] = any(e['name'] in ACTIVE for e in later)
            row[f'return_identity_only{days}'] = bool(later) and all(e['name'] in {'login','paywall_shown','onboarding_shown','guest_started'} for e in later)
            row[f'return_landing{days}'] = next((e['path'] for e in later if e['page']), '未见页面')
            row[f'return_visits{days}'] = len({e['visit_id'] for e in later})
            row[f'return_sends{days}'] = sum(e['name']=='send_message' for e in later)
        base.append(row)
    assert not any(mismatches.values()), f'Account followup mismatch: {mismatches}'
    result = {'definition': '同一唯一关联账户，前24小时冻结体验特征；之后7/14天观察独立新访问。仅纳入完整观察窗。',
              'main_population': '首24小时已提交任务的账户；首次未用但之后激活另列。',
              'identity_note': '匿名入口仅在同次访问唯一关联账户后回补；后续访问必须在身份关联生效后。',
              'audit': {'facts': len(facts), 'followup_recount_matches': True}, 'windows': []}
    for days in (7,14):
        eligible = [r for r in base if r[f'mature{days}']]
        starters = [r for r in eligible if r['early_sends']>0]
        for r in eligible:
            r['continued'] = r[f'reused{days}']; r['path'] = r['early_path']
        ret = [r for r in starters if r[f'returned{days}']]
        reused = [r for r in ret if r[f'reused{days}']]
        browse = [r for r in ret if not r[f'reused{days}']]
        stopped = [r for r in starters if not r[f'returned{days}']]
        groups = return_groups(starters, days)
        counts = {'eligible_accounts': len(eligible), 'starters': len(starters), 'no_return': len(stopped),
                  'browse_only': len(browse), 'reused': len(reused), 'returned': len(ret),
                  'return_rate': ratio(len(ret),len(starters)), 'reuse_rate': ratio(len(reused),len(starters)),
                  'return_to_use_rate': ratio(len(reused),len(ret)),
                  'newly_activated': sum(r[f'reused{days}'] and not r['early_sends'] for r in eligible),
                  'initial_nonusers': sum(not r['early_sends'] for r in eligible)}
        assert counts['no_return']+counts['browse_only']+counts['reused']==counts['starters']
        comp = compare(starters, features=RET_FEATURES)
        comp['paths'] = {}  # First-day paths are labeled explicitly below, avoiding temporal ambiguity.
        comp['first24_paths'] = {g: [{'steps':list(p),'n':n} for p,n in Counter(r['early_path'] for r in starters if r['continued']==wanted).most_common(3) if p]
                                for g,wanted in [('continued',True),('stopped',False)]}
        demand_rows=by_demand(starters,features=RET_FEATURES[:10],full=True)
        for d in demand_rows:
            ds=[r for r in starters if r['demand']==d['demand']]
            d['groups']=return_groups(ds,days)
            d['starters']=len(ds)
            d['returned']=sum(r[f'returned{days}'] for r in ds)
            d['reused']=sum(r[f'reused{days}'] for r in ds)
            d['browse_only']=d['returned']-d['reused']
            d['no_return']=len(ds)-d['returned']
            d['return_rate']=ratio(d['returned'],len(ds))
            d['reuse_rate']=ratio(d['reused'],len(ds))
            d['return_to_use_rate']=ratio(d['reused'],d['returned'])
        window = {'days': days, **counts, 'groups':groups, 'comparison':comp,
                  'by_demand':demand_rows,
                  'standardized':[standardize(starters,f,'continued') for f in ['early_attachments','early_choice','early_pricing']],
                  'daily_experience_vs_followup': 'comparison内全部特征取前24小时；groups.followup_actions仅描述回访期间遇到的场景。'}
        result['windows'].append(window)
    return result


def commercial(items, cutoff, include_demands=True):
    transitions=[]
    specs=[('first_use', '首次提交'), ('repeat_use', '再次提交')]
    for anchor_key,label in specs:
        for target,names in [('pricing',PRICE), ('intent',INTENT), ('checkout',CHECKOUT)]:
            rows=[]; early_excluded=0; immature=0
            for item in items:
                sends=item['send_events']
                if not sends:continue
                first=sends[0]['at']; anchor=first if anchor_key=='first_use' else next((e['at'] for e in sends if e['at']>first),None)
                if not anchor:continue
                if anchor+timedelta(minutes=30)>cutoff:immature+=1;continue
                if any(e['at']<=anchor and e['name'] in names for e in item['events']):early_excluded+=1;continue
                later=[e for e in item['events'] if anchor<e['at']<=anchor+timedelta(minutes=30)]
                hit=next((e for e in later if e['name'] in names),None)
                rows.append({'demand':item['demand'],'continued':bool(hit),'seconds':(hit['at']-anchor).total_seconds() if hit else None})
            yes=[r for r in rows if r['continued']]
            transitions.append({'anchor':anchor_key,'anchor_label':label,'target':target,'n':len(rows),'reached':len(yes),
                                'rate':ratio(len(yes),len(rows)),'prior_target_excluded':early_excluded,'observation_incomplete':immature,
                                'median_seconds':median(r['seconds'] for r in yes) if yes else None,
                                'by_demand':by_demand(rows,features=[],full=False)})
    landmarks=[]
    for minutes in (2,5):
        for target,names in [('intent',INTENT),('checkout',CHECKOUT)]:
            rows=[]; excluded=0; immature=0
            for item in items:
                first=item['anchors']['use']
                if not first:continue
                mark=first+timedelta(minutes=minutes); end=first+timedelta(minutes=30)
                if end>cutoff:immature+=1;continue
                if any(e['at']<=mark and e['name'] in names for e in item['events']):excluded+=1;continue
                early=[e for e in item['events'] if first<e['at']<=mark]
                later=[e for e in item['events'] if mark<e['at']<=end]
                row={k:item[k] for k in ['demand','week','device']}
                row.update(_features(item,first))
                row.update(repeat_early=any(e['name']=='send_message' for e in early),
                           early_gate=any(e['name']=='paywall_shown' and e['props'].get('scene')!='sidebarUpgrade' for e in early),
                           continued=any(e['name'] in names for e in later))
                rows.append(row)
            landmarks.append({'minutes':minutes,'target':target,'early_target_excluded':excluded,'observation_incomplete':immature,
                              **compare(rows,features=['repeat_early','attachments','early_gate'],with_paths=False),
                              'by_demand':by_demand(rows,features=['repeat_early','early_gate'],full=True),
                              'standardized':standardize(rows,'repeat_early','continued')})
    # One first checkout per Visit; all features are strictly before the checkout.
    checkout_rows=[];scenes=Counter();actions=Counter();surfaces=Counter();source=Counter();no_preuse=0;incomplete=0
    for item in items:
        checkout=next((e for e in item['events'] if e['name'] in CHECKOUT),None)
        if not checkout:continue
        before=[e for e in item['events'] if e['at']<checkout['at']]
        pre_sends=[e for e in before if e['name']=='send_message']
        if not pre_sends:no_preuse+=1
        mature=checkout['at']+timedelta(minutes=30)<=cutoff
        if not mature:incomplete+=1
        after=[e for e in item['events'] if checkout['at']<e['at']<=checkout['at']+timedelta(minutes=30)] if mature else []
        gates=[e for e in before if e['name'] in {'paywall_shown','paywall_cta_click'}]
        non_sidebar=[e for e in gates if e['props'].get('scene')!='sidebarUpgrade']
        last_scene=(non_sidebar[-1]['props'].get('scene') if non_sidebar else gates[-1]['props'].get('scene') if gates else None) or 'unknown'
        scenes[last_scene]+=1
        begin=next((e for e in item['events'] if e['name']=='begin_checkout' and abs((e['at']-checkout['at']).total_seconds())<=10),None)
        action=(begin['props'].get('action_type') if begin else None) or checkout['props'].get('action') or 'unknown'
        actions[action]+=1
        surfaces[checkout['props'].get('surface') or 'unknown']+=1
        source[checkout['props'].get('source') or 'unknown']+=1
        row={'demand':item['demand'],'pre_sends':len(pre_sends),'pre_send_band':band(len(pre_sends)),
             'pre_use_seconds':(checkout['at']-pre_sends[0]['at']).total_seconds() if pre_sends else None,
             'scene':last_scene,'action_type':action,'mature':mature,
             'continued':any(e['name']=='send_message' for e in after),'pricing':any(e['name'] in PRICE for e in before),
             'post_pricing':any(e['name'] in PRICE for e in after),'purchase':any(e['name']=='purchase' for e in after),
             'no_active_after':not any(e['name'] in ACTIVE-CHECKOUT for e in after) if mature else None,
             'path':compact_path(item['events'],item['anchors']['entry'],checkout['at'],6)+compact_path(after,checkout['at']+timedelta(microseconds=1),checkout['at']+timedelta(minutes=30),6)}
        checkout_rows.append(row)
    mature_rows=[r for r in checkout_rows if r['mature']]
    scene_rows=[]
    for scene,n in scenes.most_common():
        rs=[r for r in checkout_rows if r['scene']==scene]; mature=[r for r in rs if r['mature']]
        scene_rows.append({'scene':scene,'n':n,'median_pre_sends':median(r['pre_sends'] for r in rs),
                           'after_denominator':len(mature),'used_again':sum(r['continued'] for r in mature),
                           'used_again_rate':ratio(sum(r['continued'] for r in mature),len(mature))})
    all_purchase=sum(bool(item['anchors']['payment']) for item in items)
    result = {'definition':'使用锚点之后30分钟观察首次价格/意图/结账；已在锚点前到达目标者剔除，三个目标独立计算，允许跳步。',
            'payment_proxy_note':f'本入口Visit中支付信号为{all_purchase}；当前深入分析止于结账行为，不能等同付款。账户支付另以账户汇总核对。',
            'ordered_transitions':transitions,'landmarks':landmarks,
            'landmark_definition':'先在首次使用后2/5分钟冻结重复提交和门槛特征，再观察到第30分钟；不要求landmark时仍活跃，剔除已达目标者。',
            'checkout_experience':{'n':len(checkout_rows),'without_preuse':no_preuse,'observation_incomplete':incomplete,
                'median_pre_sends':median(r['pre_sends'] for r in checkout_rows) if checkout_rows else None,
                'median_first_use_to_checkout_seconds':median(r['pre_use_seconds'] for r in checkout_rows if r['pre_use_seconds'] is not None) if any(r['pre_use_seconds'] is not None for r in checkout_rows) else None,
                'prior_pricing':sum(r['pricing'] for r in checkout_rows),
                'pre_send_bands':[{'band':b,'n':n} for b,n in Counter(r['pre_send_band'] for r in checkout_rows).items()],
                'scenes':scene_rows,'action_types':[{'type':k,'n':n} for k,n in actions.most_common()],
                'surfaces':[{'surface':k,'n':n} for k,n in surfaces.most_common()],
                'sources':[{'source':k,'n':n} for k,n in source.most_common()],
                'after30':{'n':len(mature_rows),'used_again':sum(r['continued'] for r in mature_rows),
                           'returned_pricing':sum(r['post_pricing'] for r in mature_rows),
                           'no_active_action':sum(r['no_active_after'] for r in mature_rows),
                           'purchase':sum(r['purchase'] for r in mature_rows)},
                'by_demand':by_demand(mature_rows,features=[],full=False),
                'paths':[{'steps':list(p),'n':n} for p,n in Counter(r['path'] for r in checkout_rows).most_common(6)],
                'scene_note':'最近一次非侧栏付费场景在checkout之前；action_type=trial是发起试用结账，并非试用转付费。',
                'after_note':'结账后30分钟站内行为只衡量继续操作，自动结账打开事件不算后续主动动作；离站支付可能未回传。'}}
    if include_demands:
        demands=Counter(i['demand'] for i in items)
        result['by_demand']=[{'demand':d,**commercial([i for i in items if i['demand']==d],cutoff,False)} for d,_ in demands.most_common()]
    return result


def analyze_deep(visits, events, account_facts, account_history, cutoff,
                 identity_context, history_visit_bounds):
    end=_dt(cutoff)
    eligible=[v for v in visits if v.get('quality_eligible',True)]
    start=min(_dt(v['start_at']) for v in eligible)
    items,quality=_normalize(eligible,events,start,end)
    imm,_=immediate(items,end)
    grouped,bounds=account_events(account_facts,account_history,events,identity_context,history_visit_bounds,end)
    ret=retention(account_facts,grouped,bounds,end)
    comm=commercial(items,end)
    result={'meta':{'cutoff':end.isoformat(),'timezone':'Asia/Shanghai','version':1,
                    'unit_immediate_commercial':'MeiGen来源Visit','unit_retention':'唯一关联账户',
                    'value_proxy':'send_message代表提交尝试；未见生成完成和下载完成信号。',
                    'privacy':'聚合输出不含账户、会话、访问、任务或事件ID。'},
            'audit':{'quality_eligible_visits':len(eligible),'normalized_visits':len(items),
                     'first_use_visits':sum(i['anchors']['use'] is not None for i in items),
                     'immediate_partition_conserved':imm['continued']+imm['stopped']==imm['n'],
                     'account_followup_matches_saved_facts':ret['audit']['followup_recount_matches'],
                     'retention_partitions_conserved':all(w['no_return']+w['browse_only']+w['reused']==w['starters'] for w in ret['windows']),
                     'checkout_scene_counts_conserved':sum(s['n'] for s in comm['checkout_experience']['scenes'])==comm['checkout_experience']['n']},
            'feature_labels':DIM_LABELS,'immediate':imm,'retention':ret,'commercial':comm,
            'node_comparisons':node_comparisons(items,end)}
    nodes=result['node_comparisons']['nodes']+[result['node_comparisons']['result_gate_register']]
    result['audit']['node_partitions_conserved']=all(n['continued']+n['stopped']==n['n'] and sum(d['n'] for d in n['by_demand'])==n['n'] for n in nodes)
    result['audit']['node_unknown_windows_conserved']=all(sum(d['observation_incomplete'] for d in n['by_demand'])==n['observation_incomplete'] for n in nodes)
    assert all(v for k,v in result['audit'].items() if k.endswith(('conserved','facts')))
    json.dumps(result,allow_nan=False)
    return result


def analyze_generation(generation):
    p=Path(generation)
    def read(name):return json.loads((p/name).read_text())
    # account_facts may be stale if the pipeline has not completed account analytics.
    meta=read('accounts/account_analysis.json')['meta']
    q=read('data/quality.json')
    if _dt(meta['cutoff'])!=_dt(q['cutoff']):raise ValueError('deep_analysis_generation_cutoff_mismatch')
    result = analyze_deep(read('data/visits.json'),read('data/events_normalized.json'),
                        read('accounts/account_facts.json'),read('accounts/history.json'),q['cutoff'],
                        read('accounts/identity_context.json'),read('accounts/history_visit_bounds.json'))
    result['meta']['generation_id']=p.name
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--generation',type=Path);parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if args.generation:
        generation=args.generation
    else:
        report=json.loads((ROOT/'data/report.json').read_text())
        generation_id=report['generation_id']
        if Path(generation_id).name!=generation_id:raise ValueError('invalid_generation_id')
        generation=ROOT/'generations'/generation_id
    result=analyze_generation(generation)
    output=args.output or ROOT/'data/deep_comparison.json'
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,ensure_ascii=False,separators=(',',':'),allow_nan=False))
    print(json.dumps({'output':str(output),'bytes':output.stat().st_size,'audit':result['audit']},ensure_ascii=False))

if __name__=='__main__':main()
