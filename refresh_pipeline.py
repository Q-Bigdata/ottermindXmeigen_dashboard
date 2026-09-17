"""Incremental full-history refresh from Chat2DB; one cutoff per generation."""
from __future__ import annotations
import argparse,json,sys,subprocess,shutil,uuid,importlib.util
from pathlib import Path
from datetime import datetime,timedelta,timezone
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor,as_completed
from urllib.parse import unquote
BASE=Path(__file__).resolve().parent;sys.path.insert(0,str(BASE))
from mcp_client import MCPClient
import extract_data
from analytics_segments import analyze_segments
from analytics_branches import analyze_branches
from public_payload import export_data
from analytics_deep_comparison import analyze_generation as analyze_deep_generation
from live_findings import build as build_live_findings
from refresh_progress import progress
START='2026-08-12T21:24:36.457+08:00';SITE=extract_data.SITE

def dt(t):return datetime.fromisoformat(t.replace('Z','+00:00'))
def save(p,obj):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(obj,ensure_ascii=False,separators=(',',':'),allow_nan=False))
def run_sql(dest,qid,sql):
 with MCPClient(output_dir=dest/qid,timeout=50,deadline_seconds=400,max_calls=100,max_pages=64,retries=0) as c:return c.query_payload(qid,sql)
def shell(script,*args):
 subprocess.run([sys.executable,str(BASE/script),*map(str,args)],check=True,stdout=sys.stderr)
def source_cache():
 """Select the same complete generation that readers currently see.

 A cancelled runner never advances this pointer: only live_server's atomic
 report publication makes a newly built generation eligible for reuse.
 """
 current=BASE/'data';published=current/'report.json'
 if published.exists():
  report=json.loads(published.read_text());generation=report.get('generation_id','')
  if generation and Path(generation).name==generation:
   candidate=BASE/'generations'/generation/'data'
   if (candidate/'quality.json').exists():
    quality=json.loads((candidate/'quality.json').read_text())
    if dt(quality['cutoff'])==dt(report['window']['end']):return candidate
 return current
def main():
 p=argparse.ArgumentParser();p.add_argument('--cutoff');p.add_argument('--output',type=Path,required=True);p.add_argument('--full',action='store_true');p.add_argument('--local-only',action='store_true');a=p.parse_args()
 end=dt(a.cutoff) if a.cutoff else datetime.now(timezone.utc)-timedelta(seconds=120)
 cutoff=end.astimezone(timezone.utc).isoformat();gen=end.strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
 work=BASE/'generations'/gen;data=work/'data';data.mkdir(parents=True)
 progress('load_cache')
 current=source_cache();quality=json.loads((current/'quality.json').read_text());old_end=dt(quality['cutoff'])
 if end==old_end and not a.full:a.local_only=True
 if a.local_only and end!=old_end:raise ValueError('local_only_requires_cached_cutoff')
 if end<old_end and not a.local_only:raise ValueError('cutoff_before_cache')
 raw=json.loads((current/'events.json').read_text());visits=json.loads((current/'visits.json').read_text())
 if not a.local_only:
  # Re-read a 48h overlap; first arrivals of newly seen Visits get full context below.
  lo=dt(START) if a.full else max(dt(START),old_end-timedelta(days=2))
  lo=lo.replace(hour=0,minute=0,second=0,microsecond=0)
  if a.full:raw=[];visits=[]
  else:raw=[e for e in raw if dt(e['created_at'])<lo]
  extract_data.BASE=extract_data.BASE.replace(extract_data.END,cutoff);extract_data.END=cutoff
  windows=[];t=lo;i=0
  while t<end:
   hi=min(t+timedelta(days=1),end);windows.append((f'LIVE-E{i:03}',extract_data.events_sql(t.isoformat(),hi.isoformat())));t=hi;i+=1
  progress('extract_events',0,len(windows))
  with ThreadPoolExecutor(max_workers=2) as pool:
   futures=[pool.submit(run_sql,work/'extract',q,sql) for q,sql in windows]
   for completed,f in enumerate(as_completed(futures),1):
    raw.extend(dict(zip(extract_data.ECOLS,r)) for r in f.result());progress('extract_events',completed,len(windows))
  raw=list({e['event_id']:e for e in raw}.values());known={v['visit_id'] for v in visits};newids=sorted({e['visit_id'] for e in raw}-known)
  # Restore complete new Visit context, including pre-overlap events.
  progress('restore_visits',0,(len(newids)+499)//500)
  for i in range(0,len(newids),500):
   selected=','.join("'"+x+"'" for x in newids[i:i+500])
   sql=extract_data.events_sql(START,cutoff)
   sql=sql.replace('JOIN cohort c USING(visit_id)',f'JOIN cohort c USING(visit_id)').replace("e.created_at>=", "e.visit_id IN ("+selected+") AND e.created_at>=",1)
   rows=run_sql(work/'extract',f'LIVE-N{i//500:03}',sql);raw.extend(dict(zip(extract_data.ECOLS,r)) for r in rows)
   progress('restore_visits',i//500+1,(len(newids)+499)//500)
  raw=list({e['event_id']:e for e in raw}.values());pvs=defaultdict(list)
  for e in raw:
   if e['event_type']==1:pvs[e['visit_id']].append(e)
  first=[min(es,key=lambda e:(dt(e['created_at']),e['event_id'])) for vid,es in pvs.items() if vid not in known]
  cols=['visit_id','start_at','entry_path','session_id','referrer_domain','referrer_path','utm_source','utm_medium','utm_campaign','utm_content','event_id']
  progress('extract_entries',0,(len(first)+599)//600)
  for i in range(0,len(first),600):
   ids=','.join("'"+e['event_id']+"'" for e in first[i:i+600])
   sql=f"SELECT coalesce(jsonb_agg(jsonb_build_array(visit_id,created_at,url_path,session_id,referrer_domain,referrer_path,utm_source,utm_medium,utm_campaign,utm_content,event_id) ORDER BY event_id),'[]'::jsonb) AS payload FROM public.website_event WHERE website_id='{SITE}' AND event_id IN ({ids})"
   visits.extend(dict(zip(cols,r)) for r in run_sql(work/'extract',f'LIVE-V{i//600:03}',sql))
   progress('extract_entries',i//600+1,(len(first)+599)//600)
 save(data/'events.json',raw);save(data/'visits.json',visits)
 accounts_dir=work/'accounts'
 progress('account_context')
 if a.local_only:
  shutil.copy2(current/'session_context.json',data/'session_context.json')
  pointer=current/'cache_generation.json'
  cached_generation=json.loads(pointer.read_text()) if pointer.exists() else None
  source_accounts=current.parent/'accounts' if current.parent.parent.name=='generations' else Path(cached_generation['path'])/'accounts' if cached_generation and dt(cached_generation['cutoff'])==end else BASE/'accounts'
  account_meta=json.loads((source_accounts/'extraction_meta.json').read_text())
  if dt(account_meta['cutoff'])!=end:raise ValueError('local_account_cutoff_mismatch')
  shutil.copytree(source_accounts,accounts_dir,ignore=shutil.ignore_patterns('extract','ACC-*'),dirs_exist_ok=True)
 else:
  shell('extract_accounts.py','context','--end',cutoff,'--data-dir',data,'--output-dir',accounts_dir)
 progress('normalize');shell('normalize_data.py','--data-dir',data,'--cutoff',cutoff)
 visits=json.loads((data/'visits.json').read_text());events=json.loads((data/'events_normalized.json').read_text());q=json.loads((data/'quality.json').read_text())
 eligible=[v for v in visits if v.get('quality_eligible',True)]
 progress('segments');segments=analyze_segments(visits,events,START,cutoff)
 progress('branches');branches=analyze_branches(visits,events,START,cutoff)
 save(data/'segments.json',segments);save(data/'branches.json',branches)
 if not a.local_only:
  for mode in ['identity','history','payments']:
   progress('account_'+mode);shell('extract_accounts.py',mode,'--end',cutoff,'--data-dir',data,'--output-dir',accounts_dir)
 progress('account_analysis');shell('analytics_accounts.py','--end',cutoff,'--data-dir',data,'--output-dir',accounts_dir)
 account_paths=[accounts_dir/'summary.json',accounts_dir/'accounts_summary.json',accounts_dir/'analysis.json']
 account_path=next((p for p in account_paths if p.exists()),None)
 if account_path is None:raise RuntimeError('account_summary_unavailable')
 accounts=json.loads(account_path.read_text())
 deep_comparison=analyze_deep_generation(work)
 save(data/'deep_comparison.json',deep_comparison)
 progress('assemble');findings=build_live_findings(q,segments,branches,accounts)
 reviewed_path=BASE/'findings.json'
 reviewed=json.loads(reviewed_path.read_text()) if reviewed_path.exists() else None
 payload={'generation_id':gen,'generated_at':datetime.now(timezone.utc).isoformat(),'window':{'start':START,'end':cutoff,'timezone':'Asia/Shanghai'},'data':export_data(q,segments,branches,accounts),'findings':findings,'reviewed_analysis':reviewed,'refresh':{'method':'48h_overlap_plus_complete_new_visits','full_refresh':a.full,'delay_seconds':120,'interpretation':'mechanism narratives retain their reviewed evidence window'}}
 payload['data']['deep_comparison']=deep_comparison
 save(a.output,payload);save(work/'report.json',payload)
 # Keep every source file isolated in this generation. The HTTP server alone
 # publishes report.json; that atomic file also selects the next source cache.
 print(json.dumps({'status':'ready','generation_id':gen,'cutoff':cutoff,'visits':q['visits']}))
if __name__=='__main__':main()
