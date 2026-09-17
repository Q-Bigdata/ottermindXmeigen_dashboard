"""Independent count and packaging checks on a saved report generation."""
import json,sys,math
from pathlib import Path
from collections import Counter,defaultdict
ROOT=Path(__file__).resolve().parent
p=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'snapshots/reviewed-20260916-1510/report.json'
r=json.loads(p.read_text());d=r['data'];q=d['quality'];s=d['segments'];b=d['branches'];a=d['accounts'];c=d['interactive'];checks={}
checks['daily_visits']=sum(x['visits'] for x in s['trends']['days'])==q['visits']
checks['matrix_visits']=sum(x['visits'] for x in s['maturity']['matrix'])==q['visits']
checks['exclusive_matrix']=sum(z['visits'] for x in s['maturity']['matrix'] for z in x['states'])==q['visits']
checks['account_matrix']=sum(x['n'] for x in a['matrix'])==a['cumulative_totals']['accounts']
checks['event_ids_unique']=q['duplicate_event_ids']==0
checks['account_events_unique']=a['audit']['history_rows']==a['audit']['history_event_ids']
for horizon,payload in a['followup'].items():
 n=payload['total'][0]['n']
 for field,rows in payload.items():checks[f'account_{horizon}_{field}']=sum(x['n'] for x in rows)==n
 for row in payload['total']:checks[f'account_{horizon}_subset']=row['reused']<=row['returned']<=row['n']
idx={n:i for i,n in enumerate(c['node_columns'])};counts=defaultdict(Counter)
for row in c['node_rows']:
 name=c['dictionaries']['node'][row[idx['node']]];x=counts[name];x['n']+=1;x['y']+=row[idx['progressed']];x['exit']+=not row[idx['valid_later']]
for row in b['axis']:
 x=counts[row['node']];checks['node_'+row['node']]=x['n']==row['denominator'] and x['y']==row['continued'] and x['exit']==row['no_later_valid_action']
 checks['node_partition_'+row['node']]=row['continued']+row['no_later_record']+row['other_later_activity']==row['denominator']
 for f in ('continue_rate','exit_rate','no_target_rate'):
  checks['node_rate_'+row['node']+'_'+f]=row[f] is None or 0<=row[f]<=1
checks['profile_rows']=len(c['visit_rows'])==q['visits']
if 'arrival_rows' in c:
 ai={n:i for i,n in enumerate(c['arrival_columns'])};arr=defaultdict(Counter)
 for row in c['arrival_rows']:
  name=c['dictionaries']['node'][row[ai['node']]];arr[name]['arrived']+=1;arr[name]['complete']+=row[ai['complete']]
 for x in b['axis']:
  checks['arrival_'+x['node']]=arr[x['node']]['arrived']==x['arrived'] and arr[x['node']]['complete']==x['denominator']
checks['coherent_cutoff']=s['meta']['observation_end_exclusive']==b['meta']['observation_end'] or __import__('datetime').datetime.fromisoformat(s['meta']['observation_end_exclusive'].replace('Z','+00:00'))==__import__('datetime').datetime.fromisoformat(b['meta']['observation_end'].replace('Z','+00:00'))
checks['account_cutoff']=__import__('datetime').datetime.fromisoformat(a['meta']['cutoff'].replace('Z','+00:00'))==__import__('datetime').datetime.fromisoformat(r['window']['end'].replace('Z','+00:00'))
failed=[k for k,v in checks.items() if not v]
result={'report':str(p),'generation_id':r['generation_id'],'cutoff':r['window']['end'],'passed':not failed,'checks':checks,'failed':failed}
(ROOT/'validation-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps({'passed':not failed,'checks':len(checks),'failed':failed}))
if failed:raise SystemExit(1)
