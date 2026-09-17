"""Read-only full-history MeiGen extraction; compact JSON with verified MCP wire."""
import sys,json,re,os
from pathlib import Path
from datetime import datetime,timedelta
from concurrent.futures import ThreadPoolExecutor,as_completed
ROOT=Path(__file__).resolve().parent; sys.path.insert(0,str(ROOT))
from mcp_client import MCPClient
OUT=Path(__file__).resolve().parent
SITE='f56e75d7-ed8c-4ef2-bb90-c1bdb5df035f'
END='2026-09-16T07:10:00Z'
START='2026-08-12T00:00:00+08:00'
SOURCE="(lower(e.referrer_domain) IN ('meigen.ai','www.meigen.ai') OR lower(e.utm_source) IN ('meigen','meigen.ai'))"
BASE=f"""WITH candidate AS MATERIALIZED (
 SELECT DISTINCT visit_id FROM public.website_event e WHERE website_id='{SITE}' AND created_at<'{END}' AND {SOURCE}
), first_pv AS MATERIALIZED (
 SELECT DISTINCT ON(e.visit_id) e.visit_id,e.session_id,e.event_id,e.created_at,e.url_path,e.referrer_domain,e.referrer_path,
 e.utm_source,e.utm_medium,e.utm_campaign,e.utm_content,e.hostname
 FROM public.website_event e JOIN candidate c USING(visit_id) WHERE e.website_id='{SITE}' AND e.event_type=1 AND e.created_at<'{END}'
 ORDER BY e.visit_id,e.created_at,e.event_id
), cohort AS MATERIALIZED (
 SELECT * FROM first_pv e WHERE hostname='ottermind.ai' AND {SOURCE}
)"""
KEYS="'is_new_task','ui_click_id','scene','surface','reason','has_attachments','attachment_count','has_agent','has_turn_setup','device_kind','action','action_type','plan_name','plan_id','billing_cycle','method','provider','outcome','via','currency','value','transaction_id','checkpoint','preview_slides','total_slides','route','source','mode','stage','tier'"
VCOLS=['visit_id','start_at','entry_path','session_id','referrer_domain','referrer_path','utm_source','utm_medium','utm_campaign','utm_content','device','browser','os','country','language','account_id','account_link_at','identity_count']
ECOLS=['event_id','visit_id','session_id','created_at','event_type','event_name','url_path','hostname','properties','property_conflicts','duplicate_keys','redirect_path','lcp','inp','fcp','ttfb','cls']
def run(qid,sql,kind='rows'):
 dest=OUT/'extract'/qid
 existing=dest/'results'/f'{qid}.decoded.json'
 if existing.exists(): return json.loads(existing.read_text())
 with MCPClient(output_dir=dest,timeout=50,deadline_seconds=700,max_calls=100,max_pages=64,retries=0) as c:
  val=c.query_payload(qid,sql)
 print(qid,len(val) if isinstance(val,list) else 'object',flush=True)
 return val

def cohort_sql(lo,hi):
 return BASE+f""" SELECT coalesce(jsonb_agg(jsonb_build_array(c.visit_id,c.created_at,c.url_path,c.session_id,c.referrer_domain,c.referrer_path,
 c.utm_source,c.utm_medium,c.utm_campaign,c.utm_content) ORDER BY c.created_at,c.visit_id),'[]'::jsonb) AS payload
 FROM cohort c WHERE c.created_at>='{lo}' AND c.created_at<'{hi}'"""

def events_sql(lo,hi):
 return BASE+f""", ev AS MATERIALIZED (SELECT e.* FROM public.website_event e JOIN cohort c USING(visit_id)
 WHERE e.website_id='{SITE}' AND e.created_at>='{lo}' AND e.created_at<'{hi}' AND e.created_at<'{END}'),
 pairs AS (SELECT d.website_event_id,d.data_key,count(*) AS n,count(DISTINCT jsonb_build_array(data_type,string_value,number_value,date_value)) AS variants,
 min(coalesce(string_value,number_value::text,date_value::text)) AS val FROM public.event_data d JOIN ev e ON e.event_id=d.website_event_id
 WHERE d.website_id='{SITE}' AND d.data_key IN ({KEYS}) GROUP BY 1,2),
 props AS (SELECT website_event_id,jsonb_object_agg(data_key,val) FILTER(WHERE variants=1) AS p,count(*) FILTER(WHERE variants>1) AS conflicts,count(*) FILTER(WHERE n>1) AS duplicate_keys FROM pairs GROUP BY 1)
 SELECT coalesce(jsonb_agg(jsonb_build_array(e.event_id,e.visit_id,e.session_id,e.created_at,e.event_type,e.event_name,e.url_path,e.hostname,
 coalesce(p.p,'{{}}'::jsonb),coalesce(p.conflicts,0),coalesce(p.duplicate_keys,0),
 CASE WHEN e.url_path LIKE '%auth%' THEN split_part(split_part((regexp_match(e.url_query,'(^|&)redirect=([^&]*)'))[2],'%3F',1),'?',1) ELSE NULL END,
 e.lcp,e.inp,e.fcp,e.ttfb,e.cls) ORDER BY e.created_at,e.event_id),'[]'::jsonb) AS payload
 FROM ev e LEFT JOIN props p ON p.website_event_id=e.event_id"""

def main():
 kind=sys.argv[1]
 if kind=='audit':
  sql=BASE+" SELECT jsonb_build_object('candidate',(SELECT count(*) FROM candidate),'first_pv',(SELECT count(*) FROM first_pv),'cohort',(SELECT count(*) FROM cohort),'source_groups',(SELECT jsonb_agg(x) FROM(SELECT referrer_domain,utm_source,count(*) n FROM cohort GROUP BY 1,2)x)) AS payload"
  print(json.dumps(run('FH-A001',sql),ensure_ascii=False));return
 tasks=[]
 if kind=='visits':
  bounds=['2026-08-12','2026-08-19','2026-08-26','2026-09-02','2026-09-09','2026-09-16','2026-09-17']
  for i,(a,b) in enumerate(zip(bounds,bounds[1:])): tasks.append((f'FH-VC{i:02}',cohort_sql(a+'T00:00:00+08:00',b+'T00:00:00+08:00')))
 elif kind=='events':
  a=datetime.fromisoformat(START);end=datetime.fromisoformat(END.replace('Z','+00:00'))
  i=0
  while a<end:
   b=min(a+timedelta(days=1),end);tasks.append((f'FH-E{i:02}',events_sql(a.isoformat(),b.isoformat())));a=b;i+=1
 else: raise ValueError(kind)
 rows=[]
 with ThreadPoolExecutor(max_workers=2) as pool:
  futs={pool.submit(run,q,sql):q for q,sql in tasks}
  for f in as_completed(futs): rows.extend(f.result())
 cols=VCOLS if kind=='visits' else ECOLS
 out=[dict(zip(cols,r)) for r in rows]
 (OUT/'data'/f'{kind}.json').write_text(json.dumps(out,ensure_ascii=False,separators=(',',':')))
 print(json.dumps({'kind':kind,'rows':len(out)}),flush=True)
if __name__=='__main__':main()
