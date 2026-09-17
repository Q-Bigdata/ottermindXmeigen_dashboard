import sys,json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
OUT=Path(__file__).resolve().parent;sys.path.insert(0,str(OUT))
from mcp_client import MCPClient
es=json.loads((OUT/'data/first_pvs.json').read_text())
cols=['visit_id','start_at','entry_path','session_id','referrer_domain','referrer_path','utm_source','utm_medium','utm_campaign','utm_content','event_id']
def batch(i,items):
 qid=f'FH-VKEY{i:03}'; dest=OUT/'extract'/qid;p=dest/'results'/f'{qid}.decoded.json'
 if p.exists():return json.loads(p.read_text())
 ids=','.join("'"+e['event_id']+"'" for e in items)
 sql=f"SELECT coalesce(jsonb_agg(jsonb_build_array(visit_id,created_at,url_path,session_id,referrer_domain,referrer_path,utm_source,utm_medium,utm_campaign,utm_content,event_id) ORDER BY event_id),'[]'::jsonb) AS payload FROM public.website_event WHERE website_id='f56e75d7-ed8c-4ef2-bb90-c1bdb5df035f' AND event_id IN ({ids})"
 with MCPClient(output_dir=dest,timeout=45,deadline_seconds=150,max_pages=10,max_calls=15) as c:v=c.query_payload(qid,sql)
 print(qid,len(v),flush=True);return v
rows=[]
with ThreadPoolExecutor(max_workers=2) as pool:
 fs=[pool.submit(batch,i//700,es[i:i+700]) for i in range(0,len(es),700)]
 for f in as_completed(fs):rows.extend(f.result())
out=[dict(zip(cols,r)) for r in rows]
assert len(out)==len(es)
(OUT/'data/visits.json').write_text(json.dumps(out,ensure_ascii=False,separators=(',',':')))
print('visits',len(out),flush=True)
