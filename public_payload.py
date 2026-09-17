"""Export aggregate results and compact filterable facts; keep raw evidence local."""
from collections import defaultdict
from copy import deepcopy
from datetime import datetime
import re

def _strip_internal(value):
 if isinstance(value,dict):return {k:_strip_internal(v) for k,v in value.items() if k not in {'visit_id','event_id','session_id','account_id','transaction_id'}}
 if isinstance(value,list):return [_strip_internal(v) for v in value]
 return value

def export_data(quality,segments,branches,accounts):
 s=deepcopy(segments);b=deepcopy(branches);a=deepcopy(accounts)
 features=s['maturity'].pop('rows',[])
 s['maturity'].pop('account_source_visit_rows',None)
 # The comprehensive account matrix uses cross-source history; don't ship the
 # narrower source-only matrix as a competing definition.
 s['maturity'].pop('account_source_visit_matrix',None)
 facts={r['visit_id']:r for r in features}
 rows=b.pop('node_rows',[])
 arrivals=b.pop('arrival_rows',[])
 dims=['demand','maturity','device','browser','source','week','node','entry','country','language']
 dictionaries={k:[] for k in dims};codes={k:{} for k in dims}
 def code(k,v):
  v=str(v or 'unknown')
  if v not in codes[k]:codes[k][v]=len(dictionaries[k]);dictionaries[k].append(v)
  return codes[k][v]
 node_rows=[]
 for r in rows:
  f=facts.get(r['visit_id'],{})
  node_rows.append([code('node',r['branch']),code('demand',r['demand']),code('maturity',f.get('deepest_stage')),
    code('device',r['device']),code('browser',r['browser']),code('source',f.get('source_evidence')),code('week',r['week']),
    int(r['progressed']),int(r['any_later']),int(r['valid_later']),r.get('seconds_to_target'),int(r.get('target_before',False)),f.get('day'),code('entry',re.sub(r'/studio/task/[^/]+','/studio/task/:task',f.get('normalized_entry_path') or '/')),code('country',f.get('country')),code('language',f.get('language'))])
 arrival_rows=[]
 for r in arrivals:
  f=facts.get(r['visit_id'],{})
  arrival_rows.append([code('node',r['node']),code('demand',r['demand']),code('maturity',f.get('deepest_stage')),
    code('device',r['device']),code('browser',r['browser']),code('source',f.get('source_evidence')),code('week',r['week']),
    int(r['complete']),f.get('day'),code('entry',re.sub(r'/studio/task/[^/]+','/studio/task/:task',f.get('normalized_entry_path') or '/')),code('country',f.get('country')),code('language',f.get('language'))])
 profile_rows=[]
 for f in features:
  flags=f['reached_flags']
  profile_rows.append([f['day'],code('demand',f['initial_demand']),code('maturity',f['deepest_stage']),code('device',f['device']),
    code('browser',f['browser']),code('source',f['source_evidence']),int(f['quality_eligible']),int(f['pending_30m']),
    int(flags['registered']),int(flags['used']),int(flags['registered_use']),int(flags['reused']),int(flags['checkout']),int(flags['paid']),int(flags.get('payment_intent',False)),code('entry',re.sub(r'/studio/task/[^/]+','/studio/task/:task',f.get('normalized_entry_path') or '/')),code('country',f.get('country')),code('language',f.get('language'))])
 # No internal account identifiers or filesystem evidence directories in browser.
 a.pop('expansion_examples',None)
 compact={'dictionaries':dictionaries,
  'node_columns':['node','demand','maturity','device','browser','source','week','progressed','any_later','valid_later','seconds_to_target','target_before','entry_date','entry','country','language'],
  'node_rows':node_rows,
  'arrival_columns':['node','demand','maturity','device','browser','source','week','complete','entry_date','entry','country','language'],
  'arrival_rows':arrival_rows,
  'visit_columns':['date','demand','maturity','device','browser','source','quality_eligible','pending_30m','registered','used','registered_use','reused','checkout','paid','payment_intent','entry','country','language'],
  'visit_rows':profile_rows}
 return {'quality':quality,'segments':_strip_internal(s),'branches':_strip_internal(b),'accounts':_strip_internal(a),'interactive':compact}
