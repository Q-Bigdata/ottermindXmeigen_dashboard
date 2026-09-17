export const escapeHTML=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export const formatNumber=v=>v==null?'—':new Intl.NumberFormat('zh-CN',{maximumFractionDigits:0}).format(v);
export const formatRate=v=>v==null?'—':`${(v*100).toFixed(1)}%`;
export const formatSeconds=v=>v==null?'—':v<60?`${Math.round(v)} 秒`:`${Math.floor(v/60)} 分 ${Math.round(v%60)} 秒`;
export const NODE_NAMES={entry:'MeiGen 入口',landing:'落地',browse:'浏览',register:'注册',use:'使用',reuse:'复用',payment:'充值'};
export const DEVICES={mobile:'手机',laptop:'笔记本',desktop:'桌面',tablet:'平板',unknown:'未知设备'};
export const BROWSERS={chrome:'Chrome',ios:'Safari · iOS',crios:'Chrome · iOS','ios-webview':'iOS 内嵌浏览器','chromium-webview':'Chrome Webview',safari:'Safari',samsung:'Samsung',instagram:'Instagram',opera:'Opera',firefox:'Firefox',facebook:'Facebook','edge-chromium':'Edge','edge-ios':'Edge · iOS',fxios:'Firefox · iOS',yandexbrowser:'Yandex',miui:'MIUI',unknown:'未知浏览器'};
export const SOURCES={referrer_meigen:'MeiGen 直接引荐',utm_only:'仅 UTM 标记',utm_auth_return:'认证 / 付款回流',utm_other_referrer:'其他引荐 + MeiGen UTM'};
const METRICS=['registered','used','registered_use','reused','payment_intent','checkout','paid'];
const DEFAULT={demand:'all',maturity:'all',device:'all',browser:'all',source:'all',entry:'all',country:'all',language:'all',start:null,end:null};
const utcDate=d=>new Date(d+'T12:00:00Z');
export function createModel(report){
 const I=report.data.interactive,D=I.dictionaries;
 const idx=cols=>Object.fromEntries(cols.map((c,i)=>[c,i]));
 const V=idx(I.visit_columns),N=idx(I.node_columns),A=idx(I.arrival_columns);
 const names=Object.fromEntries(report.data.segments.maturity.matrix.map(r=>[r.demand,r.label]));
 const stages=Object.fromEntries(report.data.segments.maturity.matrix[0].states.map(s=>[s.stage,s.label]));
 const codeSets=(filters)=>Object.fromEntries(['demand','maturity','device','browser','source','entry','country','language'].filter(k=>D[k]).map(k=>[k,(filters[k]&&filters[k]!=='all')?D[k].indexOf(filters[k]):-1]));
 function matcher(filters,cols){const f={...DEFAULT,...filters},codes=codeSets(f);return r=>{
  for(const k in codes){if(f[k]!=='all'&&codes[k]!==r[cols[k]])return false;}
  const date=r[cols.date??cols.entry_date];return (!f.start||date>=f.start)&&(!f.end||date<=f.end);
 };}
 const cached=new Map();
 function selected(filters,type='visit'){
  const f={...DEFAULT,...filters};const key=type+JSON.stringify(f);if(cached.has(key))return cached.get(key);
  const [rows,cols]=type==='visit'?[I.visit_rows,V]:type==='node'?[I.node_rows,N]:[I.arrival_rows,A];
  const result=rows.filter(matcher(f,cols));if(cached.size>40)cached.clear();cached.set(key,result);return result;
 }
 function summarize(rows){const out={visits:rows.length,denominator:0,pending:0,qualityExcluded:0};METRICS.forEach(k=>out[k]=0);
  for(const r of rows){if(!r[V.quality_eligible]){out.qualityExcluded++;continue;}out.denominator++;out.pending+=r[V.pending_30m];METRICS.forEach(k=>out[k]+=r[V[k]]||0);}
  METRICS.forEach(k=>out[k+'_rate']=out.denominator?out[k]/out.denominator:null);return out;
 }
 function summary(f){return summarize(selected(f));}
 function daily(f){const buckets=new Map();for(const r of selected(f)){const d=r[V.date];if(!buckets.has(d))buckets.set(d,[]);buckets.get(d).push(r);}
  const byStage=new Map();
  const stage=(date,node)=>{if(!byStage.has(date))byStage.set(date,{});const group=byStage.get(date);return group[node]??=( {arrived:0,denominator:0,continued:0,exited:0,pending:0} );};
  for(const row of selected(f,'arrival')){const x=stage(row[A.entry_date],D.node[row[A.node]]);x.arrived++;x.pending+=!row[A.complete];}
  for(const row of selected(f,'node')){const x=stage(row[N.entry_date],D.node[row[N.node]]);x.denominator++;x.continued+=row[N.progressed];x.exited+=!row[N.valid_later];}
  const start=f.start||report.window.start.slice(0,10),end=f.end||new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(report.window.end));const out=[];
  for(let day=utcDate(start);day<=utcDate(end);day.setUTCDate(day.getUTCDate()+1)){
   const date=day.toISOString().slice(0,10),summary=summarize(buckets.get(date)||[]),journey=byStage.get(date)||{};
   for(const x of Object.values(journey)){x.rate=x.denominator?x.continued/x.denominator:null;x.exit_rate=x.denominator?x.exited/x.denominator:null;}
   out.push({date,...summary,browsed:journey.browse?.arrived||0,journey,behavior_observed:date>='2026-08-18',partial_day:report.data.segments.trends.days.find(x=>x.date===date)?.partial_day??false});
  }return out;
 }
 function matrix(f){const groups=new Map();for(const r of selected(f)){const d=D.demand[r[V.demand]],stage=D.maturity[r[V.maturity]];if(!groups.has(d))groups.set(d,{demand:d,label:names[d]||d,total:0,counts:{}});const x=groups.get(d);x.total++;x.counts[stage]=(x.counts[stage]||0)+1;}return [...groups.values()].sort((a,b)=>b.total-a.total);}
 function axis(f){const out=Object.fromEntries(report.data.branches.axis.map(x=>[x.node,{...x,arrived:0,denominator:0,continued:0,exit:0,other:0,pending:0,no_later_record:0,delays:[]}]));
  for(const r of selected(f,'arrival')){const key=D.node[r[A.node]];if(!out[key])continue;out[key].arrived++;out[key].pending+=!r[A.complete];}
  for(const r of selected(f,'node')){const x=out[D.node[r[N.node]]];if(!x)continue;x.denominator++;x.continued+=r[N.progressed];x.exit+=!r[N.valid_later];x.no_later_record+=!r[N.any_later];if(r[N.progressed]&&r[N.seconds_to_target]!=null)x.delays.push(r[N.seconds_to_target]);}
  return Object.values(out).map(x=>{const den=x.denominator;x.continue_rate=den?x.continued/den:null;x.exit_rate=den?x.exit/den:null;x.no_target=den-x.continued;x.no_target_rate=den?x.no_target/den:null;x.other=Math.max(0,den-x.continued-x.exit);x.delays.sort((a,b)=>a-b);const n=x.delays.length;x.median_seconds_to_target=n?(n%2?x.delays[(n-1)/2]:(x.delays[n/2-1]+x.delays[n/2])/2):null;delete x.delays;return x;});
 }
 function breakdown(f,key){if(V[key]===undefined)return [];const buckets=new Map();for(const r of selected(f)){const k=D[key]?D[key][r[V[key]]]:r[V[key]];if(!buckets.has(k))buckets.set(k,[]);buckets.get(k).push(r);}return [...buckets].map(([key,rows])=>({key,...summarize(rows)})).sort((a,b)=>b.visits-a.visits);}
 function breakpointGroups(f,node,dimension){
  const rows=selected(f,'node').filter(r=>D.node[r[N.node]]===node),groups=new Map();
  for(const row of rows){const key=D[dimension]?.[row[N[dimension]]]||'unknown';if(!groups.has(key))groups.set(key,[]);groups.get(key).push(row);}
  const sorted=[...groups.entries()].sort((a,b)=>b[1].length-a[1].length),reference=sorted[0];
  const rate=rows=>rows.length?rows.filter(r=>!r[N.valid_later]).length/rows.length:null;
  const strataKeys=['demand','entry','device','week'].filter(k=>k!==dimension&&N[k]!==undefined);
  const stratify=rs=>{const m=new Map();for(const row of rs){const key=strataKeys.map(k=>row[N[k]]).join('|');if(!m.has(key))m.set(key,[]);m.get(key).push(row);}return m;};
  const refCells=reference?stratify(reference[1]):new Map();
  return sorted.map(([key,rs])=>{
   const n=rs.length,stopped=rs.filter(r=>!r[N.valid_later]).length,next=rs.filter(r=>r[N.progressed]).length;
   let common=0,sameDirection=0,weight=0,targetSum=0,refSum=0,targetN=0,refN=0;
   if(key!==reference[0]){for(const [cell,group]of stratify(rs)){const ref=refCells.get(cell);if(!ref||group.length<5||ref.length<5)continue;const w=group.length+ref.length;common++;weight+=w;targetN+=group.length;refN+=ref.length;targetSum+=w*rate(group);refSum+=w*rate(ref);if((rate(group)-rate(ref))*(stopped/n-rate(reference[1]))>0)sameDirection++;}}
   return {key,n,stopped,continued:n-stopped,next,stop_rate:stopped/n,next_rate:next/n,reference:reference?.[0],is_reference:key===reference?.[0],common_strata:common,same_direction_strata:sameDirection,adjusted_difference:weight?(targetSum-refSum)/weight:null,common_target_n:targetN,common_reference_n:refN,coverage:targetN/n};
  });
 }
 return {report,dictionaries:D,names,stages,summary,daily,matrix,axis,breakdown,breakpointGroups,selected,visitIndex:V};
}
