"""Local account analytics from a frozen, read-only MeiGen snapshot.
Entry demand is the primary classification. Events, repeated visits, registration
signals and payment signals remain separate. Only salted account hashes are used.
"""
import argparse,json,re,math
from collections import defaultdict,Counter
from datetime import datetime,timedelta,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parent
DEMANDS={'/tools/fix-blurry-pictures':'blur','/tools/ai-product-video-ads':'product_video','/tools/watermark-remover':'watermark','/tools/ai-video-editor':'video_edit','/features/ppt':'ppt','/tools/ai-product-image-generator':'product_image','/features/ai-video-generator':'video_generation'}
INTENTS={'blur':'image','watermark':'image','product_image':'image','product_video':'video','video_edit':'video','video_generation':'video','ppt':'slides'}
CHOICES={'studio_composer_suggestion_generate_image':'image','studio_composer_suggestion_create_video':'video','studio_composer_suggestion_create_slides':'slides','studio_composer_suggestion_build_website':'website','studio_composer_suggestion_summarize_file':'file_summary','studio_composer_suggestion_analyze_spreadsheet':'spreadsheet'}
INTENT_EVENTS={'plan_clicked','paywall_cta_click','begin_checkout','checkout_opened'}
PRICE_EVENTS={'pricing_opened','view_item_list'}
REG_EVENTS={'sign_up','guest_signup'}

def dt(v):return datetime.fromisoformat(v.replace('Z','+00:00'))
def norm(path):return re.sub(r'^/[a-z]{2}-[A-Za-z]{2}(?=/|$)','',str(path or '/').split('?')[0].split('#')[0]).rstrip('/') or '/'
def demand(path):
    p=norm(path);return DEMANDS.get(p,'model_explore' if p.startswith('/explore/') else 'other')
def read(p):return json.loads(p.read_text())
def dump(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2))
def link_info(ctx,end):
    ls=[x for x in ctx.get('links',[]) if dt(x['link_at'])<end];ids={x['account_id'] for x in ls}
    if len(ids)!=1:return None
    aid=next(iter(ids))
    if aid!=ctx.get('current_account_id'):return None
    return aid,min(dt(x['link_at']) for x in ls)

def build_anchors(data_dir,out,end):
    cutoff=dt(end);contexts={x['session_id']:x for x in read(data_dir/'session_context.json')}
    groups=defaultdict(list)
    for e in read(data_dir/'events.json'):
        if e['hostname']=='ottermind.ai' and dt(e['created_at'])<cutoff:groups[e['visit_id']].append(e)
    visitpath=data_dir/'visits.json';visits={x['visit_id']:x for x in read(visitpath)} if visitpath.exists() else {}
    linked=[];audit=Counter()
    for vid,events in groups.items():
        events.sort(key=lambda e:(e['created_at'],e['event_id']));pv=next((e for e in events if e['event_type']==1),None)
        if not pv: audit['no_initial_pageview']+=1;continue
        meta=visits.get(vid,{});start=dt(meta.get('start_at',pv['created_at']));last=max(dt(e['created_at']) for e in events)
        if meta.get('quality_eligible') is False:
            audit['quality_excluded_visits']+=1;continue
        eligible=[];all_ids=set();conflict=False;unknown=0
        for sid in {e['session_id'] for e in events}:
            ctx=contexts.get(sid,{});ids={x['account_id'] for x in ctx.get('links',[]) if dt(x['link_at'])<cutoff}
            if len(ids)>1:conflict=True
            li=link_info(ctx,cutoff)
            if li:
                all_ids.add(li[0])
                if li[1]<=last:eligible.append((li[0],li[1],sid))
            elif ids:conflict=True
            else:unknown+=1
        audit['cohort_visits']+=1
        if conflict or len(all_ids)>1:audit['identity_conflict_visits']+=1;continue
        if not eligible:audit['no_account_during_visit']+=1;continue
        aid=eligible[0][0];at=min(t for _,t,_ in eligible);path=meta.get('entry_path',pv['url_path']);family=meta.get('initial_demand') or demand(path)
        entryli=link_info(contexts.get(pv['session_id'],{}),cutoff)
        known=bool(entryli and entryli[0]==aid and entryli[1]<=start)
        linked.append({'account_id':aid,'anchor_visit':vid,'t0':start.isoformat(),'entry_path':norm(path),'entry_family':family,'entry_intent':INTENTS.get(family,'unknown'),'linkage':'known_at_entry' if known else 'identified_during_visit','link_at':at.isoformat(),'link_delay_seconds':max(0,(at-start).total_seconds()),'unknown_sessions_in_visit':unknown,'device':contexts.get(pv['session_id'],{}).get('device'),'country':contexts.get(pv['session_id'],{}).get('country'),'utm_content':meta.get('utm_content'),'source_evidence':meta.get('source_evidence','unknown'),'entry_referrer':meta.get('referrer_domain'),'entry_utm_source':meta.get('utm_source')})
    linked.sort(key=lambda x:(dt(x['t0']),x['anchor_visit']));anchors={}
    for r in linked:anchors.setdefault(r['account_id'],r)
    audit['linked_visits']=len(linked);audit['accounts']=len(anchors)
    return {'meta':{'cutoff':end,'source_cohort':'first PV referrer meigen.ai/www.meigen.ai OR UTM meigen/meigen.ai; same cohort as parent events','identity_rule':'one consistent account across Visit; link must occur by Visit end; current identity and unique mapping agree','anchor_attribution':'anchor Visit may be associated retrospectively; entry logged-in status is not backfilled'},'audit':dict(audit),'accounts':list(anchors.values())}

def wilson(k,n):
    if not n:return [None,None]
    z=1.95996398454;p=k/n;den=1+z*z/n;c=(p+z*z/(2*n))/den;h=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    return [max(0,c-h),min(1,c+h)]
def aggregate(rows,keys,days):
    groups=defaultdict(list)
    for r in rows:
        if r[f'mature{days}']:groups[tuple(r[k] for k in keys)].append(r)
    out=[]
    for key,rs in sorted(groups.items(),key=lambda x:str(x[0])):
        d=dict(zip(keys,key));n=len(rs);d['n']=n
        for metric in ('returned','reused','expanded_choice','other_entry_exploration'):
            k=sum(bool(r[f'{metric}{days}']) for r in rs);d[metric]=k;d[metric+'_rate']=k/n;d[metric+'_ci95']=wilson(k,n)
        out.append(d)
    return out

def comparison(rows,key,low,high,days=7):
    eligible=[r for r in rows if r[f'mature{days}'] and r[key] in (low,high)];cells=defaultdict(lambda:defaultdict(list))
    for r in eligible:cells[(r['entry_family'],r['week'])][r[key]].append(r)
    common=[c for c in cells.values() if len(c[low])>=5 and len(c[high])>=5];weight=sum(len(c[low])+len(c[high]) for c in common)
    metrics={}
    for metric in ('returned','reused'):
        raw=[]
        for level in (low,high):
            rs=[r for r in eligible if r[key]==level];k=sum(r[f'{metric}{days}'] for r in rs);raw.append({'level':level,'n':len(rs),'k':k,'rate':k/len(rs) if rs else None,'ci95':wilson(k,len(rs))})
        rates={}
        for level in (low,high):rates[str(level)]=sum((len(c[low])+len(c[high]))/weight*sum(r[f'{metric}{days}'] for r in c[level])/len(c[level]) for c in common) if weight else None
        diff=rates[str(high)]-rates[str(low)] if weight else None
        variance=0
        if weight:
            for c in common:
                w=(len(c[low])+len(c[high]))/weight
                for level in (low,high):
                    n=len(c[level]);pr=sum(r[f'{metric}{days}'] for r in c[level])/n;variance+=w*w*pr*(1-pr)/n
        se=math.sqrt(variance)
        raw_diff=raw[1]['rate']-raw[0]['rate'] if all(x['n'] for x in raw) else None
        raw_ci=None
        if raw_diff is not None:
            lp,hp=raw[0]['rate'],raw[1]['rate'];lc,hc=raw[0]['ci95'],raw[1]['ci95']
            raw_ci=[raw_diff-math.sqrt((hp-hc[0])**2+(lc[1]-lp)**2),raw_diff+math.sqrt((hc[1]-hp)**2+(lp-lc[0])**2)]
        metrics[metric]={'raw':raw,'raw_difference_high_minus_low':raw_diff,'raw_difference_ci95':raw_ci,'standardized_rates':rates,'standardized_difference_high_minus_low':diff,'standardized_difference_ci95':[max(-1,diff-1.95996398454*se),min(1,diff+1.95996398454*se)] if weight else None,'standardized_interval_method':'normal approximation from independent within-stratum proportions; exploratory with sparse outcomes'}
    return {'feature':key,'low':low,'high':high,'window_days':days,'strata':['entry_family','week'],'common_strata':len(common),'common_accounts':weight,'eligible_accounts':len(eligible),'minimum_each_group_in_stratum':5,'interpretation':'observational association; common support only','metrics':metrics}

def main():
    p=argparse.ArgumentParser();p.add_argument('--data-dir',type=Path,default=ROOT/'data');p.add_argument('--output-dir',type=Path,default=ROOT/'accounts');p.add_argument('--end');a=p.parse_args();out=a.output_dir;end=a.end or read(out/'extraction_meta.json')['cutoff'];cutoff=dt(end)
    anchor_data=build_anchors(a.data_dir,out,end);anchors=anchor_data['accounts'];lookup={r['account_id']:r for r in anchors};contexts={x['session_id']:x for x in read(out/'identity_context.json')};event_groups=defaultdict(dict);all_history=read(out/'history.json');audit=Counter();visit_bounds={}
    bounds_path=out/'history_visit_bounds.json'
    if bounds_path.exists():visit_bounds={r['visit_id']:dt(r['visit_start']) for r in read(bounds_path)}
    # Follow-up and prior behavior are assigned only once mapping is effective.
    for e in all_history:
        t=dt(e['created_at']);li=link_info(contexts.get(e['session_id'],{}),cutoff)
        if not li or li[0] not in lookup:continue
        visit_bounds[e['visit_id']]=min(visit_bounds.get(e['visit_id'],t),t)
        if t<li[1]:audit['history_events_before_identity_link']+=1;continue
        e=dict(e);e['_t']=t;e['identity_basis']='link_effective';event_groups[li[0]][e['event_id']]=e
    # Restore the initial anonymous part of a uniquely identified anchor Visit.
    # It is a retrospective Visit association, never evidence of entry login.
    anchor_visit_to_id={r['anchor_visit']:r['account_id'] for r in anchors}
    for e in read(a.data_dir/'events.json'):
        aid=anchor_visit_to_id.get(e['visit_id']);t=dt(e['created_at'])
        if aid and e['hostname']=='ottermind.ai' and dt(lookup[aid]['t0'])<=t<cutoff:
            if e['event_id'] not in event_groups[aid]:
                e=dict(e);e['_t']=t;e['identity_basis']='retrospective_anchor_visit';event_groups[aid][e['event_id']]=e;audit['restored_anchor_prelink_events']+=1
    facts=[];expansion_examples=[]
    for anchor in anchors:
        aid=anchor['account_id'];t0=dt(anchor['t0']);index=t0+timedelta(days=1);es=sorted(event_groups[aid].values(),key=lambda e:(e['_t'],e['event_id']));post=[e for e in es if e['_t']>=t0];early=[e for e in post if e['_t']<index];prior=[e for e in es if e['_t']<t0]
        signup_times=[e['_t'] for e in es if e['event_name'] in REG_EVENTS];signup=min(signup_times,default=None);sends=[e for e in early if e['event_name']=='send_message'];post_sends=[e for e in post if e['event_name']=='send_message'];early_choices={CHOICES.get(e.get('properties',{}).get('ui_click_id')) for e in early};early_choices.discard(None)
        baseline_dirs=early_choices|({anchor['entry_intent']} if anchor['entry_intent']!='unknown' else set());paid=[e for e in post if e['event_name']=='purchase'];intent=[e for e in post if e['event_name'] in INTENT_EVENTS];postreg=[e for e in post_sends if signup and e['_t']>signup]
        week=(t0.astimezone(timezone(timedelta(hours=8))).date()-timedelta(days=t0.astimezone(timezone(timedelta(hours=8))).weekday())).isoformat()
        f={**anchor,'week':week,'exposure_mature':index<cutoff,'prior_use':any(e['event_name']=='send_message' for e in prior),'prior_registration':bool(signup and signup<t0),'first24_sends':len(sends),'send_band':'0' if not sends else '1' if len(sends)==1 else '2+','registered24':any(e['event_name'] in REG_EVENTS for e in early),'v1_24':any(signup and e['_t']>signup for e in sends),'first24_payment_intent':any(e['event_name'] in INTENT_EVENTS for e in early),'first24_directions':sorted(early_choices),'cumulative_sends':len(post_sends),'cumulative_registration_signal':any(e['event_name'] in REG_EVENTS for e in post),'cumulative_v1':bool(postreg),'cumulative_intent':bool(intent),'cumulative_purchase_signal':bool(paid),'observed_visits':len({e['visit_id'] for e in post}),'cumulative_reuse':len({e['visit_id'] for e in post_sends})>1,'first_registration_at':signup.isoformat() if signup else None,'first_post_signup_send_at':postreg[0]['_t'].isoformat() if postreg else None}
        for days in (7,14):
            limit=index+timedelta(days=days);f[f'mature{days}']=limit<cutoff
            ret=[e for e in post if index<visit_bounds.get(e['visit_id'],e['_t'])<=limit and e['_t']<=limit];ret_visits={e['visit_id'] for e in ret};rs=[e for e in ret if e['event_name']=='send_message'];dirs={CHOICES.get(e.get('properties',{}).get('ui_click_id')) for e in ret};dirs.discard(None);newdirs=dirs-baseline_dirs
            other={demand(e['url_path']) for e in ret if e['event_type']==1 and demand(e['url_path']) not in ('other',anchor['entry_family'])}
            f.update({f'returned{days}':bool(ret_visits),f'reused{days}':bool(rs),f'return_visits{days}':len(ret_visits),f'return_sends{days}':len(rs),f'expanded_choice{days}':bool(newdirs),f'new_directions{days}':sorted(newdirs),f'other_entry_exploration{days}':bool(other),f'later_entry_families{days}':sorted(other)})
        strict_sends=[e for e in sends if e['identity_basis']=='link_effective']
        f['first24_sends_after_effective_link']=len(strict_sends)
        f['strict_send_band']='0' if not strict_sends else '1' if len(strict_sends)==1 else '2+'
        f['restored_anchor_events']=sum(e['identity_basis']=='retrospective_anchor_visit' for e in early)
        if f['cumulative_purchase_signal']:stage='payment_signal'
        elif f['cumulative_intent']:stage='payment_intent'
        elif f['cumulative_reuse']:stage='repeat_use'
        elif f['cumulative_v1']:stage='registered_use'
        elif f['cumulative_sends']:stage='use_without_observed_signup'
        elif f['cumulative_registration_signal']:stage='registered_no_observed_use'
        elif any(e['event_name'] in ('ui_click','onboarding_pick','skill_import_confirmed') for e in post):stage='active_exploration'
        elif sum(e['event_type']==1 for e in post)>1:stage='browse'
        else:stage='entry_only'
        f['display_stage']=stage;facts.append(f)
        if f['expanded_choice7'] and f['mature7']:expansion_examples.append({'account_id':aid,'entry_family':f['entry_family'],'initial_directions':sorted(baseline_dirs),'later_directions':f['new_directions7']})
    payments=read(out/'payments.json');transactions={};unkeyed=[];payment_link_quality=Counter();any_link_cohort_events=0
    for pay in payments:
        pctx=contexts.get(pay['session_id'],{});pids={x['account_id'] for x in pctx.get('links',[]) if dt(x['link_at'])<cutoff}
        payment_link_quality['no_link' if not pids else 'multi_link' if len(pids)>1 else 'current_mismatch' if next(iter(pids))!=pctx.get('current_account_id') else 'unique_consistent']+=1
        any_link_cohort_events+=bool(pids & lookup.keys())
        li=link_info(contexts.get(pay['session_id'],{}),cutoff);pay['account_id']=li[0] if li else anchor_visit_to_id.get(pay['visit_id']);pay['identity_effective_at_payment']=bool(li and dt(pay['created_at'])>=li[1]);tx=pay['properties'].get('transaction_id')
        if tx:transactions.setdefault(tx,[]).append(pay)
        else:unkeyed.append(pay)
    dedup=[];conflicts=[]
    for tx,rows in transactions.items():
        attrs={tuple(r['properties'].get(k) for k in ('value','currency','plan_name')) for r in rows};ids={r['account_id'] for r in rows if r['account_id']}
        if len(attrs)>1 or len(ids)>1:conflicts.append(tx)
        r=min(rows,key=lambda r:dt(r['created_at']));dedup.append(r)
    cohort_pay=[r for r in dedup if r['account_id'] in lookup and dt(r['created_at'])>=dt(lookup[r['account_id']]['t0'])];late_assoc=[r for r in cohort_pay if not r['identity_effective_at_payment']]
    # Any matched payments get a bounded, strictly prepayment feature summary.
    paypaths=[]
    for pay in cohort_pay:
        aid=pay['account_id'];tp=dt(pay['created_at']);pre=[e for e in event_groups[aid].values() if dt(lookup[aid]['t0'])<=e['_t']<tp];after=[e for e in event_groups[aid].values() if e['_t']>tp]
        paypaths.append({'account_id':aid,'transaction_id':pay['properties'].get('transaction_id'),'paid_at':pay['created_at'],'pre_sends':sum(e['event_name']=='send_message' for e in pre),'pre_visits':len({e['visit_id'] for e in pre}),'pre_intent':sum(e['event_name'] in INTENT_EVENTS for e in pre),'post_sends':sum(e['event_name']=='send_message' for e in after),'plan_name':pay['properties'].get('plan_name'),'currency':pay['properties'].get('currency')})
    matrix=[];mg=Counter((f['entry_family'],f['display_stage']) for f in facts);row_n=Counter(f['entry_family'] for f in facts)
    for (family,stage),n in sorted(mg.items()):matrix.append({'entry_family':family,'stage':stage,'n':n,'row_n':row_n[family],'row_share':n/row_n[family]})
    snapshot={'meta':{'cutoff':end,'timezone':'Asia/Shanghai','grain':'uniquely_linked_account','first_experience':'[MeiGen anchor, anchor+24h)','followup':'new Visit starts in (anchor+24h,anchor+24h+7/14d]; events must occur within same followup end','identity_note':'initial Visit uniquely linked later is restored retrospectively; future/prior visits only use effective unique identity','value':'V1 = observed registration signal followed by send_message; generation/download unavailable','reuse':'new independent Visit with send_message; message submission proxy, not completed task','expansion':'explicit choice of another composer direction, separate from another landing-page visit; not successful task adoption','payment_note':'purchase signal grouped by transaction_id; trial checkout is not trial conversion; missing transaction keys kept separate'},'audit':{**anchor_data['audit'],**dict(audit),'history_rows':len(all_history),'history_event_ids':len({e['event_id'] for e in all_history}),'accounts_known_at_entry':sum(f['linkage']=='known_at_entry' for f in facts),'accounts_with_full_first24h':sum(f['exposure_mature'] for f in facts)},'cumulative_totals':{'accounts':len(facts),'registration_signal':sum(f['cumulative_registration_signal'] for f in facts),'send_signal':sum(bool(f['cumulative_sends']) for f in facts),'registered_use_v1':sum(f['cumulative_v1'] for f in facts),'repeat_use':sum(f['cumulative_reuse'] for f in facts),'payment_intent':sum(f['cumulative_intent'] for f in facts),'purchase_signal':sum(f['cumulative_purchase_signal'] for f in facts)},'matrix':matrix,'followup':{},'comparisons':[comparison(facts,'send_band','0','2+'),comparison(facts,'send_band','1','2+'),comparison(facts,'registered24',False,True),comparison(facts,'v1_24',False,True)],'payments':{'site_purchase_events':len(payments),'unique_transaction_keys':len(transactions),'duplicate_events':sum(len(rs)-1 for rs in transactions.values()),'missing_transaction_key_events':len(unkeyed),'conflicting_transaction_keys':len(conflicts),'site_payment_accounts':len({r['account_id'] for r in dedup if r['account_id']}),'cohort_transactions_after_anchor':len(cohort_pay),'cohort_payment_accounts':len({r['account_id'] for r in cohort_pay}),'cohort_late_identity_transactions':len(late_assoc),'paths':paypaths},'expansion_examples':expansion_examples[:20]}
    for days in (7,14):
        snapshot['followup'][str(days)]={name:aggregate(facts,keys,days) for name,keys in {'total':[],'send_band':['send_band'],'strict_send_band':['strict_send_band'],'registration':['registered24'],'v1':['v1_24'],'demand':['entry_family'],'week':['week'],'cells':['entry_family','week','send_band','registered24','prior_use'],'linkage':['linkage'],'source_evidence':['source_evidence']}.items()}
    snapshot['audit']['history_property_conflicts']=sum(e.get('properties',{}).get('_property_conflicts',0) for e in all_history)
    snapshot['audit']['history_duplicate_property_keys']=sum(e.get('properties',{}).get('_duplicate_keys',0) for e in all_history)
    snapshot['payments']['identity_quality_event_counts']=dict(payment_link_quality)
    snapshot['payments']['any_candidate_link_cohort_event_overlap']=any_link_cohort_events
    snapshot['payments']['current_identity_cohort_event_overlap']=sum(contexts.get(p['session_id'],{}).get('current_account_id') in lookup for p in payments)
    snapshot['payments']['site_current_identity_accounts']=len({contexts.get(p['session_id'],{}).get('current_account_id') for p in payments if contexts.get(p['session_id'],{}).get('current_account_id')})
    assert len(facts)==len({f['account_id'] for f in facts})
    assert sum(r['n'] for r in matrix)==len(facts)
    assert len(all_history)==len({e['event_id'] for e in all_history})
    assert all(not f['reused7'] or f['returned7'] for f in facts)
    assert all(not f['reused14'] or f['returned14'] for f in facts)
    dump(out/'account_facts.json',facts);dump(out/'anchors.json',anchor_data);dump(out/'account_analysis.json',snapshot);dump(out/'summary.json',snapshot)
    print(json.dumps({'audit':snapshot['audit'],'cumulative':snapshot['cumulative_totals'],'followup7':snapshot['followup']['7']['total'],'followup14':snapshot['followup']['14']['total'],'payments':snapshot['payments']},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
