import {escapeHTML as e,formatNumber as n,formatRate as pct,formatSeconds as seconds} from './data.js';
const DEMANDS={all:'全部需求',blur:'图片修复',product_video:'商品视频',product_image:'商品图',watermark:'去水印',video_edit:'视频编辑',video_generation:'视频生成',ppt:'PPT',model_explore:'模型探索',studio_task:'工作台任务',other:'其他'};
const card=(title,body,tag='全部历史 · 同需求对照')=>`<article class="card deep-card"><div class="card-head"><h2>${e(title)}</h2><span class="scope-tag">${e(tag)}</span></div>${body}</article>`;
const hint=t=>`<div class="note-row">${e(t)}</div>`;
const empty=t=>`<div class="empty"><p>${e(t)}</p></div>`;
const action=(title,body)=>`<div class="insight-box"><strong>${e(title)}</strong><p>${e(body)}</p></div>`;
const table=(heads,rows)=>`<div class="table-wrap"><table class="data-table"><thead><tr>${heads.map(x=>`<th>${e(x)}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${row.map(x=>`<td>${x}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
function scopeNote(filters){return Object.entries(filters).some(([k,v])=>k!=='demand'&&v&&v!=='all')?hint('深度比较按入口需求联动，使用全部历史及统一观察窗；其他访问筛选用于流程图与按日统计。'):'';}
function compare(root,demand){return demand==='all'?root:root?.by_demand?.find(x=>x.demand===demand);}
function hero(groups,total){return `<div class="deep-cohorts">${groups.map((g,i)=>`<div class="deep-cohort ${i===0?'emphasis':''}"><div class="deep-group-label"><i style="background:${g.color||['#3b82f6','#a3afc0','#7aa88c'][i]}"></i>${e(g.label)}</div><div class="deep-group-value">${n(g.n)}<span>${pct(total?g.n/total:null)}</span></div><p>${e(g.note||'')}</p></div>`).join('')}</div>`;}
function profileComparison(a,b,labels,aName,bName){const rows=Object.keys(labels).filter(k=>a?.metrics?.[k]||b?.metrics?.[k]);
 return `<div class="profile-comparison"><div class="legend"><span><i class="legend-dot"></i>${e(aName)}</span><span><i class="legend-dot gray"></i>${e(bName)}</span><span>分岔之前的体验</span></div>${rows.map(key=>{const x=a?.metrics?.[key],y=b?.metrics?.[key];if(x?.rate!==undefined||y?.rate!==undefined){return `<div class="profile-row"><div class="profile-label">${e(labels[key])}</div><div class="profile-pairs">${[x,y].map((v,i)=>`<div class="profile-bar"><div><span style="width:${v?.rate==null?0:Math.min(100,v.rate*100)}%;background:${i?'#b8c3d3':'#5a94ed'}"></span></div><strong>${pct(v?.rate)}</strong><small>${v?.denominator?`${n(v.n)} / ${n(v.denominator)}`:'暂无样本'}</small></div>`).join('')}</div></div>`;}
 const isTime=key.includes('seconds');return `<div class="profile-row"><div class="profile-label">${e(labels[key])}<small>中位数</small></div><div class="profile-medians"><span>${isTime?seconds(x?.median):n(x?.median)}</span><span>${isTime?seconds(y?.median):n(y?.median)}</span></div></div>`;}).join('')}</div>`;
}
function pathList(paths,label){return `<div class="deep-path"><h3>${e(label)}</h3>${(paths||[]).slice(0,3).map(p=>`<div class="deep-path-row"><div>${p.steps.map(s=>`<span>${e(s)}</span>`).join('<b>→</b>')}</div><small>${n(p.n)} 个同模式样本</small></div>`).join('')||'<p class="muted">该组尚无可展示路径。</p>'}</div>`;}
export function renderDeepDiagnosis(report,filters={},branch='immediate'){
 const controls=nodeControls(branch);if(branch!=='immediate')return controls+renderNodeComparison(report,filters,branch);
 const deep=report.data.deep_comparison;if(!deep)return '';const demand=filters.demand||'all',x=compare(deep.immediate,demand);if(!x||!x.n)return card('使用之后：继续 vs 停止',empty('该需求还没有足够的首次使用记录。'));
 const groups=[{label:'继续主动操作',n:x.continued,note:'首次提交后30分钟内再次提交、选择功能、进入价格/结账，或主动改变页面。'},{label:'停止主动操作',n:x.stopped,note:'同样30分钟观察中，未见独立后续动作；自动路由与被动曝光不算继续。'}];
 const overview=card('第一次使用后，哪些人继续，哪些人停下',hero(groups,x.n)+hint(`共同起点：${n(x.n)} 个首次提交访问。${demand==='all'?`${n(x.route_or_passive_only)} 个停止组访问仍留下自动路由或被动记录。`:''}未再操作不等于结果失败，也不等于永不回来。`),`${DEMANDS[demand]||demand} · 首次使用后30分钟`);
 const profiles=card('两组在开始使用之前，有哪些不同',profileComparison(x.profiles.continued,x.profiles.stopped,{choice_before:'先主动选择过功能',auth_before:'先经过认证页面',attachments:'首个任务携带素材',signup_before:'已出现注册信号',seconds_to_use:'入口到首次使用',pre_page_depth:'首次使用前页面数'},'继续组','停止组')+action('把差异转成具体体验验证','优先检查开始任务前的功能选择、素材准备和认证续接。相同需求下比较两组，复现是否需要重新定位入口或重复输入；不能仅凭差异认定原因。'));
 const paths=card('继续与停止的典型路径',`<div class="deep-paths">${pathList(x.paths?.continued,'继续组')}${pathList(x.paths?.stopped,'停止组')}</div>`+hint('路径是各组实际出现的模式，保留起点和主要动作；出现次数不是独立的转化率。'));
 const split=card('不同需求的分岔强度',table(['入口需求','相同使用起点','继续主动操作','停止主动操作'],(deep.immediate.by_demand||[]).filter(r=>demand==='all'||r.demand===demand).map(r=>[e(DEMANDS[r.demand]||r.demand),n(r.n),`${n(r.continued)} · ${pct(r.rate)}`,`${n(r.stopped)} · ${pct(r.n?r.stopped/r.n:null)}`])),`${DEMANDS[demand]||demand} · 同一观察窗口`);
 return controls+scopeNote(filters)+overview+profiles+paths+split;
}
export function renderDeepRetention(report,filters={},days=7){
 const deep=report.data.deep_comparison;if(!deep)return '';const demand=filters.demand||'all';const whole=deep.retention.windows.find(w=>w.days===Number(days));const x=compare(whole,demand);if(!x)return card('回访后的真实使用',empty('该需求尚无完整观察账户。'));
 const groups=x.groups||[];const get=k=>groups.find(g=>g.group===k)||{n:0,metrics:{},followup_actions:{}};
 const no=get('no_return'),browse=get('browse_only'),reuse=get('reused');
 const overview=card('首次使用之后：没回来、回来了、再次使用',`<div class="deep-control"><span>先固定首24小时体验，再观察新访问</span><div class="tabs"><button data-deep-days="7" class="${Number(days)===7?'active':''}">随后7日</button><button data-deep-days="14" class="${Number(days)===14?'active':''}">随后14日</button></div></div>`+hero([{label:'未回访',n:no.n,note:'观察期内未见新访问。'},{label:'回访但未再使用',n:browse.n,note:'有新访问，但未见再次提交。'},{label:'回访并再次使用',n:reuse.n,note:'新访问中再次提交任务。'}],x.starters)+hint(`分母为首24小时已经尝试使用、且之后${days}日观察完整的 ${n(x.starters)} 个账户。回访后再次使用率 ${pct(x.return_to_use_rate)}。`),`${DEMANDS[demand]||demand} · 已开始使用的账户`);
 const profiles=card('什么首次体验与后续再次使用相伴',profileComparison(reuse,no,{early_registration:'首日出现注册',early_registered_use:'首日注册后继续使用',early_choice:'首日主动选择功能',early_video_preview:'首日触及视频预览',early_pricing:'首日进入价格',early_sends:'首日提交次数',seconds_to_use:'入口到首次使用'},'回访并再用','未回访')+action('强化有价值的继续操作和任务保存','优先验证结果对比、继续修改、处理下一张和保留原任务；用之后的新访问与实际再用检查是否形成持续需求。先看相同入口、相近进入周，不把所有深度用户混为一类。'));
 const fields={return_page:'出现页面记录',return_active:'有主动操作',return_identity_only:'仅身份恢复 / 被动记录',return_auth:'经过认证',return_pricing:'查看价格',return_intent:'主动付费意图',return_checkout:'进入结账'};
 const after=card('回来了，为什么仍然没有再次使用',table(['回访中的行为','回访但未再用','回访并再用'],Object.entries(fields).map(([key,label])=>[e(label),`${n(browse.followup_actions?.[key]?.n||0)} / ${n(browse.n)} · ${pct(browse.followup_actions?.[key]?.rate)}`,`${n(reuse.followup_actions?.[key]?.n||0)} / ${n(reuse.n)} · ${pct(reuse.followup_actions?.[key]?.rate)}`]))+`<div class="deep-paths">${pathList(browse.paths,'未再用的回访路径')}${pathList(reuse.paths,'再次使用的回访路径')}</div>`+action('把“返回网站”接回具体任务','如果回访停在任务页、价格或身份恢复，检查是否能恢复上次结果、继续编辑或创建同类任务。回访记录本身不能作为产品留存成功。'));
 const demandRows=whole.by_demand||[];const byDemand=card('各需求的持续使用差距',table(['需求','首日使用账户','未回访','回访未再用','回访并再用','回访→再用'],demandRows.filter(r=>demand==='all'||r.demand===demand).map(r=>[e(DEMANDS[r.demand]||r.demand),n(r.starters),n(r.no_return),n(r.browse_only),n(r.reused),pct(r.return_to_use_rate)])));
 return scopeNote(filters)+overview+profiles+after+byDemand;
}
export function renderDeepCommercial(report,filters={},minutes=5){
 const deep=report.data.deep_comparison;if(!deep)return '';const demand=filters.demand||'all',c=compare(deep.commercial,demand);if(!c)return card('复用怎样走向付款',empty('该需求暂无完整的使用到商业推进样本。'));
 const targets={pricing:'看价格',intent:'产生付费意图',checkout:'发起结账',purchase:'记录到支付'};
 const ordered=card('首次使用、复用之后，分别走向哪里',table(['起点','后续目标','可比较起点','到达 / 未到达','30分钟推进率','耗时中位数'],c.ordered_transitions.map(r=>[e(r.anchor_label),e(targets[r.target]||r.target),n(r.n),`${n(r.reached)} / ${n(r.n-r.reached)}`,pct(r.rate),seconds(r.median_seconds)]))+hint('各目标使用自己的合格起点：排除起点前已达到目标者，保留完整30分钟观察。不同目标行的分母不能直接拼成单线漏斗。'),`${DEMANDS[demand]||demand} · 使用后有序推进`);
 const rows=c.landmarks.filter(x=>x.minutes===Number(minutes));
 const comparison=card('多次尝试是否真的对应更强的商业推进',`<div class="deep-control"><span>先观察固定前置体验，再看新的商业动作</span><div class="tabs"><button data-deep-minutes="2" class="${Number(minutes)===2?'active':''}">前2分钟</button><button data-deep-minutes="5" class="${Number(minutes)===5?'active':''}">前5分钟</button></div></div>`+table(['后续目标',`前${minutes}分钟重复使用`,`前${minutes}分钟未重复`,'样本范围'],rows.map(r=>{const yes=r.feature_rates.find(f=>f.feature==='repeat_early'&&f.value==='true'),no=r.feature_rates.find(f=>f.feature==='repeat_early'&&f.value==='false');return [e(targets[r.target]||r.target),`${n(yes?.continued||0)} / ${n(yes?.n||0)} · ${pct(yes?.rate)}`,`${n(no?.continued||0)} / ${n(no?.n||0)} · ${pct(no?.rate)}`,`排除前置窗已到目标 ${n(r.early_target_excluded||0)} 次`];}))+action('把重复使用、付费意图和结账分开判断','重复尝试可能与后续付费意图相关，却未必更容易走到结账。按需求验证结果质量和权益承接，而不是把更多提交次数当作商业成功。'));
 const check=c.checkout_experience;
 const checkout=card('进入结账的人，在此之前体验了什么',hero((check.pre_send_bands||[]).map((x,i)=>({label:x.band==='0'?'结账前未见使用':x.band==='1'?'结账前使用1次':'结账前多次使用',n:x.n,note:'只统计发起结账之前的提交记录。'})),check.n)+`<div class="deep-summary"><span>从首次使用到结账，中位数 <strong>${seconds(check.median_first_use_to_checkout_seconds)}</strong></span><span>结账后再次使用 <strong>${n(check.after30?.used_again||0)} / ${n(check.after30?.n||0)}</strong></span><span>结账后无主动动作 <strong>${n(check.after30?.no_active_action||0)}</strong></span></div>`+table(['结账前最后可见权益场景','访问','先前提交中位数','结账后继续使用'],(check.scenes||[]).map(r=>[e(({videoPreview:'视频预览',download:'下载',exitOfferStage1:'退出优惠 · 阶段1',exitOfferStage2:'退出优惠 · 阶段2',sidebarUpgrade:'侧栏升级',paidModel:'付费模型',unknown:'未记录场景'})[r.scene]||r.scene),n(r.n),n(r.median_pre_sends),`${n(r.used_again)} / ${n(r.after_denominator)} · ${pct(r.used_again_rate)}`]))+hint('这里记录结账之前最后一个可见场景，不据此断言该提示促成了付费。结账动作含订阅、升级、试用和优惠路径。'));
 const paths=card('从价值体验到结账，再返回任务',`<div class="deep-paths">${pathList(check.paths,'实际结账路径')}<div class="deep-path"><h3>优化重点</h3><p>清楚说明已经生成什么、付款后可以下载什么；保持当前结果和素材，在结账结束后恢复原任务。</p><p>当前未观测到可关联的真实付款，不能直接做付费用户与未付费用户对照。先将结账意图接到支付成功、订阅创建和试用结束后的首次收费，再补齐支付后下载。</p></div></div>`);
 return scopeNote(filters)+ordered+comparison+checkout+paths;
}

function nodeControls(selected){const choices={immediate:'首次使用：继续主动操作 / 停止',entry_continue:'进入网站：离开 / 继续浏览',browse_use:'浏览内容：停留浏览 / 开始使用',attempt_continue:'开始操作：未推进 / 进一步使用',use_register:'先使用后注册：未注册 / 注册',signup_use:'完成注册：未继续 / 继续使用',use_commercial:'使用产品：未出现 / 出现付费意图'};return `<div class="deep-control card"><label>选择同一起点的对照 <select id="deepBranchSelect">${Object.entries(choices).map(([key,label])=>`<option value="${key}" ${selected===key?'selected':''}>${label}</option>`).join('')}</select></label><span>同起点 · 同观察机会 · 只比较分岔前体验</span></div>`;}
function renderNodeComparison(report,filters,branch){
 const demand=filters.demand||'all',parent=report.data.deep_comparison?.node_comparisons?.nodes?.find(x=>x.key===branch),x=compare(parent,demand);
 if(!parent||!x)return card('该节点的继续与未继续对照',empty('该需求在此节点暂无合格对照样本。'));
 const target=parent.target_label;
 const groups=[{label:'达到下一目标',n:x.continued,note:target},{label:'观察窗内未达到目标',n:x.stopped,note:'包括停止主动操作，以及继续其他操作但未到达目标。'}];
 const cohort=card(parent.title,hero(groups,x.n)+`<div class="deep-summary"><span>推进中位数 <strong>${seconds(x.advance_time?.continued?.median_seconds)}</strong></span><span>推进P90 <strong>${seconds(x.advance_time?.continued?.p90_seconds)}</strong></span><span>观察不足 <strong>${n(x.observation_incomplete||0)}</strong></span></div>`+hint(parent.proxy_note||`从${parent.anchor_label}出发，严格观察其后30分钟。`),`${DEMANDS[demand]||demand} · 30分钟观察`);
 const profiles=card('结果发生之前，两组经历了什么',profileComparison(x.profiles.continued,x.profiles.stopped,{auth_before:'此前经过认证',signup_before:'此前已有注册信号',choice_before:'此前主动选择功能',attachments:'提交时携带素材',seconds_to_anchor:'入口到当前节点',pre_page_depth:'此前页面记录数',pre_distinct_pages:'此前不同页面数',pre_active_actions:'此前主动操作数',pre_backtracks:'此前页面往返次数'},'达到目标','未达到目标')+hint('特征截在起点之前或当前提交时。未达标者使用相同的后续观察时长，避免因观察更久产生差异；时长较长不直接等于体验差。'));
 const states=card('没有达到下一步的人，处于什么状态',table(['观察窗内的状态','访问','未达标组占比'],(x.stopped_states||[]).map(r=>[e(r.label),n(r.n),pct(r.share)]))+table(['最后一条可见记录','访问','占比'],(x.last_observed_states||[]).map(r=>[e(r.label),n(r.n),pct(r.share)]))+hint('状态是观测线索：曾触及结果场景不等于已拿到可用结果，重复后离开也可能有多种原因。先结合任务性质、同类差异和实际反馈判断。'));
 const paths=card('两组的常见过程',`<div class="deep-paths">${pathList(x.paths?.continued,'达到目标')}${pathList(x.paths?.stopped,'未达到目标')}</div>`);
 let resultProxy='';
 if(branch==='use_register'){
  const p=compare(report.data.deep_comparison.node_comparisons.result_gate_register,demand);
  if(p)resultProxy=card('补充：结果 / 下载注册门槛之后',hero([{label:'随后注册',n:p.continued,note:'真实记录到结果或下载注册门槛之后的注册信号。'},{label:'窗内未见注册',n:p.stopped,note:'仍需区分任务已完成、放弃或追踪不足。'}],p.n)+hint('该场景是当前最接近“首次结果后注册”的代理，尚不等于生成成功或下载成功。已注册者不纳入此注册风险人群。'));
 }
 return scopeNote(filters)+cohort+profiles+states+paths+resultProxy;
}
