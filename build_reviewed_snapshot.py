"""Recompute the first evidence snapshot using archived same-cutoff inputs."""
import json,sys
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parent;sys.path.insert(0,str(ROOT))
from analytics_segments import analyze_segments
from analytics_branches import analyze_branches
from public_payload import export_data
from live_findings import build
SOURCE=ROOT/'generations/20260916T071000Z-d19bc8ca'
load=lambda p:json.loads(p.read_text())
q=load(SOURCE/'data/quality.json');vs=load(SOURCE/'data/visits.json');es=load(SOURCE/'data/events_normalized.json');a=load(SOURCE/'accounts/summary.json')
s=analyze_segments(vs,es,q['start'],q['cutoff']);b=analyze_branches(vs,es,q['start'],q['cutoff'])
out={'generation_id':'reviewed-20260916-1510-v2','generated_at':datetime.now(timezone.utc).isoformat(),'window':{'start':q['start'],'end':q['cutoff'],'timezone':'Asia/Shanghai'},'data':export_data(q,s,b,a),'findings':build(q,s,b,a),'evidence_generation':SOURCE.name}
p=ROOT/'snapshots/reviewed-20260916-1510/report.json';p.write_text(json.dumps(out,ensure_ascii=False,separators=(',',':'),allow_nan=False));print('reviewed snapshot recomputed',q['visits'])
