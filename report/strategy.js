import {escapeHTML as e,formatNumber as n,formatRate as pct,formatSeconds as sec} from './data.js';

const LABEL={all:'全部需求',blur:'图片修复',product_video:'商品视频',product_image:'商品图',watermark:'去水印',video_edit:'视频编辑',video_generation:'视频生成',ppt:'PPT',model_explore:'模型探索',studio_task:'工作台任务',other:'其他'};
const card=(title,body,tag)=>`<article class="card strategy-card"><div class="card-head"><h3>${e(title)}</h3><span class="scope-tag">${e(tag)}</span></div>${body}</article>`;
const note=t=>`<div class="note-row">${e(t)}</div>`;
const action=(title,text)=>`<div class="insight-box"><strong>${e(title)}</strong><p>${e(text)}</p></div>`;
const table=(heads,rows)=>`<div class="table-wrap"><table class="data-table strategy-table"><thead><tr>${heads.map(x=>`<th>${e(x)}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div>`;
const scope=(root,demand)=>demand==='all'?root:root?.by_demand?.find(x=>x.demand===demand);
const ratio=(count,total)=>`${n(count)} / ${n(total)}（${pct(total?count/total:null)}）`;
const date=value=>value?new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'未记录';
const row=x=>`<tr><td><span class="scope-tag">${e(x.p)}</span></td><td><strong>${e(x.title)}</strong><p>${e(x.action)}</p></td><td>${e(x.metric)}</td><td>${e(x.guard)}</td></tr>`;
const detail=x=>`<details class="action-card" data-strategy="${e(x.id)}"><summary><span class="scope-tag">${e(x.p)}</span> ${e(x.title)}<span class="muted"> · 查看证据与实验</span></summary><div><p><strong>目标人群：</strong>${e(x.audience)}</p><p><strong>数据依据：</strong>${e(x.finding)}</p><p><strong>证据范围：</strong>${e(x.source)}</p><p><strong>具体改动：</strong>${e(x.action)}</p><p><strong>验证实验：</strong>${e(x.experiment)}</p><p><strong>主指标：</strong>${e(x.metric)}</p><p><strong>护栏：</strong>${e(x.guard)}</p><p><strong>解释边界：</strong>${e(x.limit)}</p></div></details>`;
function repeatEvidence(com,target){
 const group=com?.landmarks?.find(x=>x.minutes===5&&x.target===target);
 const yes=group?.feature_rates?.find(x=>x.feature==='repeat_early'&&x.value==='true');
 const no=group?.feature_rates?.find(x=>x.feature==='repeat_early'&&x.value==='false');
 return yes&&no?`前5分钟重复提交组 ${ratio(yes.continued,yes.n)}，未重复组 ${ratio(no.continued,no.n)}`:'该需求暂无两组可比样本';
}
function stateCount(node,key){return node?.stopped_states?.find(x=>x.state===key)?.n||0;}
function strategyActions(report,demand){
 const deep=report.data.deep_comparison;
 const currentSource=`${LABEL[demand]||demand} · 深度对照截至 ${date(deep.meta?.cutoff)} 北京时间`;
 const historySource=`已审阅历史快照截至 ${date(report.reviewed_analysis?.snapshot?.window?.end)} 北京时间；用于定位历史变化，不随本次筛选重算`;
 const historical=id=>report.reviewed_analysis?.findings?.find(x=>x.id===id)?.observation;
 const node=key=>scope(deep.node_comparisons?.nodes?.find(x=>x.key===key),demand);
 const imm=scope(deep.immediate,demand),attempt=node('attempt_continue'),signup=node('signup_use');
 const ret=scope(deep.retention?.windows?.find(x=>x.days===7),demand);
 const com=scope(deep.commercial,demand),check=com?.checkout_experience;
 const gate=scope(deep.node_comparisons?.result_gate_register,demand);
 const payments=report.data.accounts?.payments;
 const paymentEvidence=payments?`全入口账户汇总关联 ${n(payments.cohort_payment_accounts)} 个付款账户、${n(payments.cohort_transactions_after_anchor)} 笔交易（此项为全入口口径）`:'账户支付关联尚未提供';
 const retFeatures=ret?.feature_rates||ret?.comparison?.feature_rates||[];
 const once=retFeatures.find(x=>x.feature==='send_band'&&x.value==='1');
 const multiple=retFeatures.find(x=>x.feature==='send_band'&&x.value==='2+');
 const returnedNoUse=ret?.groups?.find(x=>x.group==='browse_only');
 const blur=scope(deep.commercial,'blur'),video=scope(deep.commercial,'product_video');
 const blurImmediate=scope(deep.immediate,'blur');
 const result=[];
 const add=x=>result.push(x);
 if(signup?.n)add({id:'authentication',p:'P0',title:'恢复认证前的素材、输入与原任务',audience:`${LABEL[demand]||demand}中到达注册节点、拥有完整30分钟后续观察的访问`,
 finding:`注册后使用 ${ratio(signup.continued,signup.n)}，推进中位数 ${sec(signup.advance_time?.continued?.median_seconds)}；${n(signup.stopped)} 个未使用访问中，${n(stateCount(signup,'other_active'))} 个仍有其他主动操作。${demand==='all'&&historical('MGH-01')?' 历史线索：'+historical('MGH-01'):''}`,
 source:currentSource+(demand==='all'?`；9/10变化另取 ${historySource}`:''),
 action:'认证成功后恢复原页面、素材与输入，把开始或继续任务作为明确下一步；逐条复现9/10前后原工具页与工作台的处理路径。',
 experiment:'在同需求、入口、设备和产品时期内，对任务恢复方案做账户级随机对照；先确认两条路径都能记录任务开始和结果。',
 metric:'注册后30分钟有效任务开始率；补齐事件后看结果与下载完成率',guard:'重复输入、恢复失败、等待时间、重复任务',limit:'未提交者仍可能在执行原页工具；提交事件覆盖变化与真实续接问题需要分别复现。'});
 if(imm?.n&&attempt?.n)add({id:'stop-states',p:'P0',title:'按停止时的状态定位具体阻力',audience:`${LABEL[demand]||demand}的首次任务提交访问`,
 finding:`首次提交 ${n(imm.n)} 次访问中，${n(imm.continued)} 个继续主动操作、${n(imm.stopped)} 个30分钟内未再主动操作。另一个更严格目标“再次使用或付费意图”有 ${n(attempt.stopped)} 个未达标，其中 ${n(stateCount(attempt,'other_active'))} 个仍有其他主动操作、${n(stateCount(attempt,'result_or_download_gate'))} 个触及结果/下载门槛。`,source:currentSource,
 action:'在断点图优先查看未继续规模大的节点，再拆无后续记录、自动路由、其他主动操作、结果门槛和权益场景；对等待、输入失败和结果已完成分别处理。',
 experiment:'按需求×入口×设备抽取继续与未继续的可比路径；再按地区、语言和进入周复核是否重复出现。录屏可用时成对核查，只对已复现的阻力做单点实验。',
 metric:'所选节点30分钟推进率 + 未继续状态构成；结果完成后离开单列',guard:'任务完成率、重复尝试、错误、等待；不以多停留或多点击为目标',limit:'未再主动操作不等于未到某一目标，更不等于永久流失。完成任务、查阅信息和等待下一次需求都可能带来停止；地区/语言差异仍需复核。'});
 if((demand==='all'||demand==='blur')&&blurImmediate?.n)add({id:'image-value',p:'P1',title:'图片修复：让结果对比和继续精修更容易',audience:'图片修复首次使用者；继续操作与停止操作分别观察',
 finding:`图片修复首次使用后继续 ${ratio(blurImmediate.continued,blurImmediate.n)}。第5–30分钟新付费意图：${repeatEvidence(blur,'intent')}；对应结账：${repeatEvidence(blur,'checkout')}。`,
 source:`图片修复 · 深度对照截至 ${date(deep.meta?.cutoff)} 北京时间`,
 action:'在结果旁提供原图对比、调整强度、继续编辑、处理下一张及保存任务；将失败重试单独标记。',
 experiment:'按账户随机展示简化结果操作区；先比较结果后有效继续与下载，再观察完整7日的新访问使用。',
 metric:'结果→下载完成率、结果→有效继续率；7日再次使用为后续指标',guard:'失败重试、处理成本、等待时间、结果质量',limit:'较多提交可能反映更强需求或失败重试。结账样本少，不能声称多次提交会带来付款。'});
 if((demand==='all'||demand==='product_video')&&video?.checkout_experience?.n)add({id:'video-value',p:'P1',title:'商品视频：优先解释预览与导出权益',audience:'商品视频已提交任务、触及预览或导出权益的访问',
 finding:`商品视频共有 ${n(video.checkout_experience.n)} 个结账访问，结账后30分钟再提交 ${ratio(video.checkout_experience.after30?.used_again,video.checkout_experience.after30?.n)}。第5–30分钟新结账：${repeatEvidence(video,'checkout')}。`,source:`商品视频 · 深度对照截至 ${date(deep.meta?.cutoff)} 北京时间`,
 action:'预览处明确格式、清晰度、时长和可下载内容；保留当前结果与素材，结账结束后返回同一任务。',
 experiment:'在同入口、设备、时期内随机比较权益说明版本，跟踪预览→主动意图→结账→服务端支付→下载；同时检查结账返回和关闭后的状态。',
 metric:'预览到结账率；接通支付与下载事件后看付款到下载完成率',guard:'提示关闭、结果丢失、退款、重复付费、生成成本',limit:'重复使用组的结账率未表现出稳定优势；不能把增加消息次数当作视频商业目标。结账后无站内动作可能是在外部支付。'});
 if(ret?.starters)add({id:'return-recovery',p:'P1',title:'把回站接回上次任务与结果',audience:`${LABEL[demand]||demand}首24小时已提交、之后7日观察完整的关联账户`,
 finding:`${n(ret.starters)} 个成熟账户中，未回访 ${n(ret.no_return)}、回访未再用 ${n(ret.browse_only)}、回访并再用 ${n(ret.reused)}；回访到再用 ${ratio(ret.reused,ret.returned)}。未再用的回访中 ${n(returnedNoUse?.followup_actions?.return_identity_only?.n||0)} 个仅留下身份恢复/被动记录。`,source:currentSource,
 action:'回站首页展示最近任务、素材和结果，提供继续编辑及创建同类任务入口；身份恢复记录与真实回访操作分开统计。',
 experiment:'对有历史任务的回访账户随机展示任务恢复区；比较同需求的回访后24小时有效使用，并观察7日是否持续。',
 metric:'真实回访→任务恢复→再次使用→下载；同时报告未回访、回访未再用、再用三组',guard:'失效任务链接、结果保留、误计被动回访、重复任务',limit:'回访只是有新访问，不自动代表价值或留存成功；匿名与跨设备未关联行为仍可能漏记。'});
 if(once&&multiple)add({id:'second-value',p:'P1',title:'以有价值的第二次操作验证持续需求',audience:`${LABEL[demand]||demand}首24小时有使用、后续7日观察完整的账户`,
 finding:`首24小时1次提交者后续7日再次使用 ${ratio(once.continued,once.n)}；2次及以上者 ${ratio(multiple.continued,multiple.n)}。这两组的需求强度和重试动机仍可能不同。`,source:currentSource,
 action:'在完成首个结果后提供继续修改、比较版本、下一张与保存任务；结果未完成时优先解释进度和失败原因。',
 experiment:'账户级随机对照，先固定首24小时体验，再等满后续7日；在同需求、入口、设备、地区/语言及进入周复核方向，稀疏分组保留样本数。',
 metric:'首日结果/下载完成率 + 后续7日新访问有效使用率',guard:'失败重试、处理成本、等待、任务质量；不鼓励无意义提交',limit:'当前是描述性关联，不能把更高提交数解释成留存原因；地区与语言的一致性尚待验证。'});
 if(gate?.n)add({id:'result-registration',p:'P2',title:'结果与下载注册门槛保留任务上下文',audience:`${LABEL[demand]||demand}能够先使用、在结果/下载门槛前尚未出现注册信号的访问`,
 finding:`结果/下载注册门槛后的成熟样本 ${n(gate.n)} 个，之后30分钟注册 ${ratio(gate.continued,gate.n)}；未见注册 ${n(gate.stopped)} 个。`,source:currentSource,
 action:'保留预览和输入；完成注册后直达同一结果与下载入口，避免要求重新生成。',
 experiment:'先逐条复现游客→门槛→注册→原结果恢复，再小流量验证清晰权益提示和恢复入口；完整注册者放在注册后使用组。',
 metric:'门槛→注册→原结果恢复；补事件后统计下载完成',guard:'结果丢失、重复生成、注册耗时、门槛关闭',limit:'当前是结果/下载门槛代理且样本小，不能命名为生成成功转注册率，也不外推到必须先注册的路径。'});
 if(demand==='all'&&historical('MGH-02'))add({id:'growth',p:'P2',title:'复盘有效放量，再决定扩大入口投入',audience:'8/23与9/14的推广批次，以及相同需求、入口、设备和时段的后续小流量',finding:historical('MGH-02'),source:historySource,
 action:'回查8/23素材曝光、排序与链接位置；复制到小流量同期对照。9/14放量先确认9/10之后任务承接与事件覆盖。',
 experiment:'同一时期保留未调整入口作对照；同步看地区、语言、周末与设备构成，确认有效使用增加后再扩量。',
 metric:'新增有效使用量、每千入口注册后使用；接通后加结果与下载',guard:'获客成本、需求构成、任务完成率、异常流量',limit:'流量增长和日期差异不直接等于运营动作生效；该历史快照不会因点击刷新而自动变成新的已验证结论。'});
 add({id:'value-payment',p:'P0',title:'接通结果、下载与真实支付的闭环',audience:`${LABEL[demand]||demand}的任务与商业路径`,
 finding:check?`当前范围记录 ${n(check.n)} 个结账访问；结账后30分钟观察完整 ${n(check.after30?.n||0)} 个，其中再次提交 ${n(check.after30?.used_again||0)} 个。数据能力核查未找到通用结果成功与下载完成信号；${paymentEvidence}。`:`当前需求没有合格结账样本；全站数据能力核查尚缺通用结果成功与下载完成；${paymentEvidence}。`,
 source:check?currentSource:'当前需求暂无结账样本；事件能力范围为整个项目',
 action:'统一任务、结果、账户和交易关联键；记录任务成功/失败、结果可见、下载开始/完成、结账取消/失败、服务端支付成功、订阅创建与试用后首次收费。',
 experiment:'用测试任务和测试交易逐条核对页面→任务→结果→下载及结账→支付回传→返回结果；去重并核对漏记，完成后再做付费/未付费对照。',
 metric:'注册后结果与下载完成率、支付交易匹配率、试用到首次收费率',guard:'交易唯一性、金额/币种一致、失败理由完整率、事件重复率',limit:'结账和试用结账不能等同支付或试用转付费；未关联支付不表示渠道真实支付率为零。'});
 return {actions:result.sort((a,b)=>a.p.localeCompare(b.p)),imm,ret,currentSource};
}
export function renderStrategy(report,filters={}){
 const deep=report.data?.deep_comparison;
 if(!deep)return card('优化策略',note('本批数据尚未生成深度对照，暂不展示基于深度样本的策略。'),'等待深度分析');
 const demand=filters.demand||'all';
 const {actions,imm,ret,currentSource}=strategyActions(report,demand);
 const hasOtherFilter=Object.entries(filters).some(([key,value])=>key!=='demand'&&value&&value!=='all');
 const scopeNote=note(`${currentSource}。${hasOtherFilter?'当前日期、设备、地区、语言和行为筛选不重算本模块，':'本模块按需求联动，'}使用全部历史与统一成熟观察窗口；每项历史证据单独标明截止时间。`);
 const stats=imm?`<div class="stat-strip"><div><span class="label">首次使用后继续主动操作</span><div class="number">${n(imm.continued)}</div><span class="label">${ratio(imm.continued,imm.n)} · 30分钟</span></div><div><span class="label">首次使用后未再主动操作</span><div class="number">${n(imm.stopped)}</div><span class="label">30分钟内 · 不等于永久流失</span></div><div><span class="label">回访后再次使用</span><div class="number">${ret?n(ret.reused):'—'}</div><span class="label">${ret?`${ratio(ret.reused,ret.returned)} · 7日成熟账户`:'该需求暂无完整7日样本'}</span></div></div>`:note('当前需求暂无合格首次使用对照样本；不借用全部需求统计。下方仅保留适用的核查与埋点动作。');
 const overview=card('优化顺序：恢复任务、验证体验、扩大有效流量',action('把断点分析转为具体实验','先找到未继续规模集中的节点，拆开停止时的状态，再对相近需求与入口验证差异。每项动作同时回答改哪里、为谁改、用什么指标判断，完成任务后的离开单独观察。')+scopeNote+stats+table(['优先级','产品 / 增长动作','主指标','护栏'],actions.map(row)),`${LABEL[demand]||demand} · ${actions.length}项行动`);
 return `<div class="section-stack">${overview}${card('策略详情与验证实验',actions.map(detail).join('')+note('先用任务结果和下载判断价值，再看回访与商业推进；结账对照与真实支付分别解释，支付关联变化后需重审商业结论。'),`深度证据动态更新 · 历史结论保留原窗口`)}</div>`;
}
