"""Small read-only identity/history reads. No original business IDs are exported."""
import argparse,json,sys,hashlib
from pathlib import Path
from datetime import datetime,timedelta,timezone
from concurrent.futures import ThreadPoolExecutor,as_completed
ROOT=Path(__file__).resolve().parent;sys.path.insert(0,str(ROOT))
from mcp_client import MCPClient
SITE='f56e75d7-ed8c-4ef2-bb90-c1bdb5df035f'
DEFAULT=Path(__file__).resolve().parent
SALT='mg-analysis-20260916'

def parse_time(v):return datetime.fromisoformat(v.replace('Z','+00:00'))
def unique_link(ctx,end):
    ls=[x for x in ctx.get('links',[]) if parse_time(x['link_at'])<end]
    accounts={x['account_id'] for x in ls}
    if len(accounts)!=1:return None
    acct=next(iter(accounts))
    if acct!=ctx.get('current_account_id'):return None
    return acct,min(parse_time(x['link_at']) for x in ls)
def qstr(s):return "'"+s.replace("'","''")+"'"

def main():
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['context','identity','history','payments','all']);p.add_argument('--end');p.add_argument('--data-dir',type=Path,default=DEFAULT/'data');p.add_argument('--output-dir',type=Path,default=DEFAULT/'accounts');p.add_argument('--workers',type=int,default=2)
    a=p.parse_args();end=a.end or (datetime.now(timezone.utc)-timedelta(seconds=120)).isoformat();cutoff=parse_time(end);out=a.output_dir;out.mkdir(parents=True,exist_ok=True);tag=hashlib.sha256(end.encode()).hexdigest()[:8]
    def run(qid,sql):
        qid=f'{qid}-{tag}';dest=out/'extract'/qid;fp=dest/'results'/f'{qid}.decoded.json'
        if fp.exists():return json.loads(fp.read_text())
        with MCPClient(output_dir=dest,timeout=55,deadline_seconds=850,max_calls=120,max_pages=80,retries=0) as c:res=c.query_payload(qid,sql)
        print(qid,len(res) if isinstance(res,list) else 'object',flush=True);return res
    def parallel(tasks):
        results=[]
        with ThreadPoolExecutor(max_workers=a.workers) as pool:
            fs={pool.submit(run,q,s):q for q,s in tasks}
            for f in as_completed(fs):results.append((fs[f],f.result()))
        return results
    if a.mode in ('context','all'):
        events=json.loads((a.data_dir/'events.json').read_text());ids=sorted({e['session_id'] for e in events});tasks=[]
        for i in range(0,len(ids),800):
            sel=','.join(qstr(x) for x in ids[i:i+800])
            sql=f"""SELECT jsonb_build_object('sessions',(SELECT coalesce(jsonb_agg(jsonb_build_array(session_id,device,browser,os,country,language,CASE WHEN distinct_id IS NOT NULL AND distinct_id<>'' THEN md5('{SALT}'||distinct_id) END) ORDER BY session_id),'[]'::jsonb) FROM public.session WHERE website_id='{SITE}' AND session_id IN({sel})), 'links',(SELECT coalesce(jsonb_agg(jsonb_build_array(session_id,md5('{SALT}'||distinct_id),created_at) ORDER BY session_id,distinct_id,created_at),'[]'::jsonb) FROM public.session_link WHERE website_id='{SITE}' AND created_at<'{end}' AND session_id IN({sel}))) AS payload"""
            tasks.append((f'AC-CTX{i//800:03}',sql))
        contexts={s:{'session_id':s,'links':[]} for s in ids};cols=['session_id','device','browser','os','country','language','current_account_id']
        for q,res in parallel(tasks):
            for row in res['sessions']:contexts[row[0]].update(dict(zip(cols,row)))
            for sid,acct,at in res['links']:contexts[sid]['links'].append({'account_id':acct,'link_at':at})
        (a.data_dir/'session_context.json').write_text(json.dumps(list(contexts.values()),ensure_ascii=False,separators=(',',':')))
        print('session_context',len(contexts),flush=True)
    if a.mode in ('identity','all'):
        tasks=[]
        for shard in range(16):
            allowed=qstr('0123456789abcdef'[shard])
            sql=f"""SELECT jsonb_build_object('links',(SELECT coalesce(jsonb_agg(jsonb_build_array(session_id,md5('{SALT}'||distinct_id),created_at) ORDER BY session_id,distinct_id,created_at),'[]'::jsonb) FROM public.session_link WHERE website_id='{SITE}' AND created_at<'{end}' AND left(session_id::text,1) IN({allowed})), 'current',(SELECT coalesce(jsonb_agg(jsonb_build_array(session_id,md5('{SALT}'||distinct_id)) ORDER BY session_id),'[]'::jsonb) FROM public.session WHERE website_id='{SITE}' AND distinct_id IS NOT NULL AND distinct_id<>'' AND left(session_id::text,1) IN({allowed}))) AS payload"""
            tasks.append((f'AC-ID16-{shard}',sql))
        contexts={}
        for _,res in parallel(tasks):
            for sid,acct,at in res['links']:contexts.setdefault(sid,{'session_id':sid,'links':[]})['links'].append({'account_id':acct,'link_at':at})
            for sid,acct in res['current']:contexts.setdefault(sid,{'session_id':sid,'links':[]})['current_account_id']=acct
        (out/'identity_context.json').write_text(json.dumps(list(contexts.values()),separators=(',',':')))
    if a.mode in ('history','all'):
        from analytics_accounts import build_anchors
        anchors=build_anchors(a.data_dir,out,end);(out/'anchors.json').write_text(json.dumps(anchors,ensure_ascii=False,separators=(',',':')))
        accounts={x['account_id'] for x in anchors['accounts']};contexts=json.loads((out/'identity_context.json').read_text());ids=[]
        for ctx in contexts:
            link=unique_link(ctx,cutoff)
            if link and link[0] in accounts:ids.append(ctx['session_id'])
        tasks=[];cols=['event_id','visit_id','session_id','created_at','event_type','event_name','url_path','referrer_domain','utm_source','properties']
        for i in range(0,len(ids),300):
            sel=','.join(qstr(x) for x in sorted(ids)[i:i+300])
            sql=f"""WITH ev AS MATERIALIZED(SELECT event_id,visit_id,session_id,created_at,event_type,event_name,url_path,referrer_domain,utm_source FROM public.website_event WHERE website_id='{SITE}' AND hostname='ottermind.ai' AND created_at<'{end}' AND session_id IN({sel})), pairs AS (SELECT d.website_event_id,d.data_key,count(*) n,count(DISTINCT jsonb_build_array(d.data_type,d.string_value,d.number_value,d.date_value)) variants,min(coalesce(d.string_value,d.number_value::text,d.date_value::text)) val FROM public.event_data d JOIN ev e ON e.event_id=d.website_event_id WHERE d.website_id='{SITE}' AND d.data_key IN('ui_click_id','action_type','scene','surface','is_new_task','has_attachments','plan_name','billing_cycle') GROUP BY 1,2),p AS (SELECT website_event_id,coalesce(jsonb_object_agg(data_key,val) FILTER(WHERE variants=1),'{{}}'::jsonb)||jsonb_build_object('_property_conflicts',count(*) FILTER(WHERE variants>1),'_duplicate_keys',count(*) FILTER(WHERE n>1)) props FROM pairs GROUP BY 1) SELECT coalesce(jsonb_agg(jsonb_build_array(e.event_id,e.visit_id,e.session_id,e.created_at,e.event_type,e.event_name,e.url_path,e.referrer_domain,e.utm_source,coalesce(p.props,'{{}}'::jsonb)) ORDER BY e.created_at,e.event_id),'[]'::jsonb) AS payload FROM ev e LEFT JOIN p ON p.website_event_id=e.event_id"""
            tasks.append((f'AC-H{i//300:03}',sql))
        rows=[]
        for _,res in parallel(tasks):rows.extend(dict(zip(cols,r)) for r in res)
        (out/'history.json').write_text(json.dumps(rows,ensure_ascii=False,separators=(',',':')))
        print('history',len(rows),flush=True)
        # The first recovered identified event is not always a Visit's start.
        # Resolve complete Visit boundaries before deciding whether it is new.
        vids=sorted({r['visit_id'] for r in rows});tasks=[]
        for i in range(0,len(vids),700):
            sel=','.join(qstr(v) for v in vids[i:i+700])
            sql=f"SELECT coalesce(jsonb_agg(x ORDER BY visit_id),'[]'::jsonb) AS payload FROM (SELECT visit_id,min(created_at) AS visit_start,max(created_at) AS visit_end,count(DISTINCT session_id) AS session_count FROM public.website_event WHERE website_id='{SITE}' AND created_at<'{end}' AND visit_id IN({sel}) GROUP BY visit_id)x"
            tasks.append((f'AC-HB{i//700:03}',sql))
        bounds=[]
        for _,res in parallel(tasks):bounds.extend(res)
        (out/'history_visit_bounds.json').write_text(json.dumps(bounds,separators=(',',':')))
    if a.mode in ('payments','all'):
        sql=f"""WITH ev AS MATERIALIZED(SELECT event_id,visit_id,session_id,created_at FROM public.website_event WHERE website_id='{SITE}' AND hostname='ottermind.ai' AND event_name='purchase' AND created_at<'{end}'), pairs AS (SELECT d.website_event_id,d.data_key,count(DISTINCT jsonb_build_array(data_type,string_value,number_value,date_value)) variants,min(coalesce(string_value,number_value::text,date_value::text)) val FROM public.event_data d JOIN ev e ON e.event_id=d.website_event_id WHERE d.website_id='{SITE}' AND d.data_key IN('transaction_id','value','currency','plan_name','action_type','billing_cycle','plan_id') GROUP BY 1,2), props AS(SELECT website_event_id,jsonb_object_agg(data_key,CASE WHEN data_key='transaction_id' THEN md5('mg-payment-20260916'||val) ELSE val END) FILTER(WHERE variants=1) p,count(*) FILTER(WHERE variants>1) conflicts FROM pairs GROUP BY 1) SELECT coalesce(jsonb_agg(jsonb_build_object('event_id',e.event_id,'visit_id',e.visit_id,'session_id',e.session_id,'created_at',e.created_at,'properties',coalesce(p.p,'{{}}'::jsonb),'property_conflicts',coalesce(p.conflicts,0)) ORDER BY e.created_at,e.event_id),'[]'::jsonb) AS payload FROM ev e LEFT JOIN props p ON p.website_event_id=e.event_id"""
        data=run('AC-PAY',sql);(out/'payments.json').write_text(json.dumps(data,ensure_ascii=False,separators=(',',':')))
    (out/'extraction_meta.json').write_text(json.dumps({'cutoff':end,'timezone':'Asia/Shanghai','source':'umami.public','mode':a.mode,'identity_hash_salt':SALT},indent=2))
if __name__=='__main__':main()
