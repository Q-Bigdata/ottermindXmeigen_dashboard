"""Construct locally reusable event/visit facts; no database access."""
import json,re,argparse
from pathlib import Path
from collections import defaultdict,Counter
from datetime import datetime
from urllib.parse import unquote,urlsplit
OUT=Path(__file__).resolve().parent;DATA=OUT/'data'
END='2026-09-16T07:10:00Z';START='2026-08-12T21:24:36.457+08:00'
def dt(s):return datetime.fromisoformat(s.replace('Z','+00:00'))
def norm(p):
 p=(p or '/').split('?')[0].split('#')[0];p=re.sub(r'^/[a-z]{2}-[A-Za-z]{2}(/|$)','/',p).rstrip('/') or '/'
 return p
DEMAND={'/features/ai-video-generator':'video_generation','/tools/fix-blurry-pictures':'blur','/tools/ai-product-image-generator':'product_image','/tools/ai-product-video-ads':'product_video','/tools/ai-video-editor':'video_edit','/tools/watermark-remover':'watermark','/features/ppt':'ppt'}
def demand(p):
 if p in DEMAND:return DEMAND[p]
 if p.startswith('/explore'):return 'model_explore'
 if 'mcp' in p or 'skill' in p:return 'skills_mcp'
 if p.startswith('/studio'):return 'studio_task'
 return 'other'
def main():
 global DATA,END,START
 ap=argparse.ArgumentParser();ap.add_argument('--data-dir',type=Path,default=DATA);ap.add_argument('--cutoff',default=END);args=ap.parse_args();DATA=args.data_dir;END=args.cutoff
 visits=json.loads((DATA/'visits.json').read_text());events=json.loads((DATA/'events.json').read_text())
 contexts=json.loads((DATA/'session_context.json').read_text()) if (DATA/'session_context.json').exists() else []
 contexts={x['session_id']:x for x in contexts}
 grouped=defaultdict(list);seen=set();dups=0
 for e in events:
  if e['event_id'] in seen:dups+=1;continue
  seen.add(e['event_id']);e['raw_url_path']=e.get('raw_url_path',e['url_path']);e['url_path']=norm(e['raw_url_path']);e.setdefault('properties',{})
  if e.get('redirect_path'):
   v=unquote(e['redirect_path']);v=urlsplit(v).path if '://' in v else v
   e['properties']['redirect_path']=norm(v)
  grouped[e['visit_id']].append(e)
 allvis=[];evs=[];reason=Counter()
 for v in visits:
  es=sorted(grouped[v['visit_id']],key=lambda e:(dt(e['created_at']),e['event_id']))
  v['raw_entry_path']=v.get('raw_entry_path',v['entry_path']);v['entry_path']=norm(v['raw_entry_path']);v['initial_demand']=demand(v['entry_path'])
  v['first_event_at']=es[0]['created_at'];v['end_at']=es[-1]['created_at'];at=dt(v['start_at'])
  v['entry_before_event']=dt(v['first_event_at'])<at
  v['entry_pv_tie']=sum(e['event_type']==1 and dt(e['created_at'])==at for e in es)>1
  v['quality_eligible']=not(v['entry_before_event'] or v['entry_pv_tie'])
  for k in ('entry_before_event','entry_pv_tie'):
   if v[k]:reason[k]+=1
  source=(v.get('referrer_domain') or '').lower()
  v['source_evidence']='referrer_meigen' if source in ('meigen.ai','www.meigen.ai') else ('utm_auth_return' if source in ('accounts.google.com','appleid.apple.com','checkout.stripe.com','api.ottermind.ai') or '/auth' in v['entry_path'] else ('utm_only' if not source else 'utm_other_referrer'))
  sc=contexts.get(v['session_id'],{})
  for k in ('device','browser','os','country','language'):v[k]=sc.get(k) or 'unknown'
  ids=set();linkdates=[];conflict=False
  for sid in {e['session_id'] for e in es}:
   c=contexts.get(sid,{})
   links=c.get('links',[])
   unique={r['account_id'] for r in links if r.get('account_id')}
   if len(unique)>1:conflict=True
   if len(unique)==1 and c.get('current_account_id') in unique:
    eligible=[r for r in links if dt(r['link_at'])<=dt(v['end_at'])]
    if eligible:
     ids.update(unique);linkdates += [r['link_at'] for r in eligible]
  v['identity_conflict']=conflict or len(ids)>1
  v['account_id']=next(iter(ids)) if len(ids)==1 and not conflict else None
  v['account_link_at']=min(linkdates,key=dt) if v['account_id'] else None
  v['identified_at_entry']=bool(v['account_link_at'] and dt(v['account_link_at'])<=at)
  v['event_count']=len(es);v['nonproduction_events']=sum(e['hostname']!='ottermind.ai' for e in es)
  evs.extend(e for e in es if e['hostname']=='ottermind.ai' and dt(e['created_at'])>=at)
  allvis.append(v)
 allvis.sort(key=lambda v:(dt(v['start_at']),v['visit_id']));evs.sort(key=lambda e:(dt(e['created_at']),e['event_id']))
 (DATA/'visits.json').write_text(json.dumps(allvis,ensure_ascii=False,separators=(',',':')))
 (DATA/'events_normalized.json').write_text(json.dumps(evs,ensure_ascii=False,separators=(',',':')))
 meta={'start':START,'cutoff':END,'timezone':'Asia/Shanghai','visits':len(allvis),'raw_events':len(events),'normalized_events':len(evs),'duplicate_event_ids':dups,'quality_exclusions':dict(reason),'source_groups':dict(Counter(v['source_evidence'] for v in allvis)),'identity_available':bool(contexts),'identified_visits':sum(bool(v['account_id']) for v in allvis),'source_rule':'earliest full Visit PV referrer meigen.ai/www.meigen.ai OR utm_source meigen/meigen.ai'}
 (DATA/'quality.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2));print(json.dumps(meta,ensure_ascii=False))
if __name__=='__main__':main()
