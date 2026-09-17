// Business interpretation panels. Statistics come from the report generation;
// fixed-window diagnoses remain visibly separate from live visit filters.
const LABELS = {
  all: '全部需求', blur: '图片修复', product_video: '商品视频', watermark: '去水印',
  ppt: 'PPT', video_edit: '视频编辑', product_image: '商品图', model_explore: '模型探索',
  video_generation: '旧视频生成入口', studio_task: '工作台任务', other: '其他需求',
};
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const n = value => Number.isFinite(Number(value)) ? Number(value).toLocaleString('zh-CN') : '—';
const pct = value => value == null || !Number.isFinite(Number(value)) ? '—' : `${(Number(value)*100).toFixed(2)}%`;
const rate = (num, den) => den > 0 ? num / den : null;
const label = key => LABELS[key] || key;
const demandOf = filters => filters?.demand || 'all';
const scope = text => `<span class="scope-tag">${esc(text)}</span>`;
const empty = text => `<div class="empty">${esc(text)}</div>`;
const note = text => `<p class="details-note">${esc(text)}</p>`;
const insight = (title, text) => `<div class="insight-box"><strong>${esc(title)}</strong><p>${esc(text)}</p></div>`;
const metric = (title, value, detail='') => `<div class="metric-inline"><span class="muted">${esc(title)}</span><strong>${esc(value)}</strong>${detail ? `<small class="muted">${esc(detail)}</small>` : ''}</div>`;
const table = (heads, rows) => `<div class="table-wrap"><table class="data-table"><thead><tr>${heads.map(h=>`<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div>`;
const cell = value => `<td>${esc(value)}</td>`;
const card = (title, body, tag='') => `<article class="card"><div class="card-head"><h3>${esc(title)}</h3>${tag ? scope(tag) : ''}</div>${body}</article>`;
const filterNote = filters => (filters?.device && filters.device !== 'all') || (filters?.maturity && filters.maturity !== 'all') || filters?.start || filters?.end || (filters?.browser && filters.browser !== 'all') || (filters?.source && filters.source !== 'all') || (filters?.entry && filters.entry !== 'all') || (filters?.country && filters.country !== 'all') || (filters?.language && filters.language !== 'all')
  ? note('本专题按需求联动；其他访问筛选适用于流程图与按日统计。') : '';
const subGroup = (aggregate, demand) => demand === 'all' ? aggregate : aggregate?.by_demand?.find(row=>row.demand === demand);
const fraction = row => row?.denominator ? `${n(row.continued)} / ${n(row.denominator)}` : '暂无完整观察';

function bars(rows, ceiling=1) {
  return `<div class="bar-pair">${rows.map((row,index)=>{
    const width = row.value == null ? 0 : Math.min(100, Math.max(0, row.value / ceiling * 100));
    return `<div class="comparison-row"><div class="comparison-label"><span>${esc(row.label)}</span><strong>${esc(pct(row.value))}</strong></div><div class="comparison-track" style="height:9px;background:#f0f3f6;border-radius:3px;overflow:hidden;margin:7px 0"><span style="display:block;height:100%;width:${width}%;background:${index === 0 ? '#3b82f6' : '#aecbfa'}"></span></div><small class="muted">${esc(row.detail || '')}</small></div>`;
  }).join('')}</div>`;
}

function sameEntryDiagnosis(branches, demand) {
  const fixed = branches?.independent_diagnostics?.same_demand_entry_card || [];
  const selected = fixed.filter(row=>demand === 'all' ? ['blur','product_video'].includes(row.demand) : row.demand === demand);
  if (!selected.length) return card('9月10日，任务承接发生了什么变化', empty('该需求没有可比较的前后同素材样本。可切换图片修复或商品视频查看。'), '专题对照 · 固定观察窗');
  const panels = selected.map(row=>{
    const before = row.before_7d, after = row.after_6d;
    const rowSpec = [
      ['入口访问', n(before.n), n(after.n)],
      ['继续浏览 / 操作', `${pct(rate(before.entry_progress,before.n))} · ${n(before.entry_progress)}`, `${pct(rate(after.entry_progress,after.n))} · ${n(after.entry_progress)}`],
      ['30分钟内提交任务', `${pct(rate(before.use_within_entry_30m,before.n))} · ${n(before.use_within_entry_30m)}`, `${pct(rate(after.use_within_entry_30m,after.n))} · ${n(after.use_within_entry_30m)}`],
      ['认证后返回原工具', n(before.auth_return_paths?.[row.entry_path] || 0), n(after.auth_return_paths?.[row.entry_path] || 0)],
      ['认证后返回工作台', n((before.auth_return_paths?.['/studio'] || 0)+(before.auth_return_paths?.['/studio/task'] || 0)), n((after.auth_return_paths?.['/studio'] || 0)+(after.auth_return_paths?.['/studio/task'] || 0))],
    ];
    return `<div><h4>${esc(label(row.demand))}</h4><p class="muted">同入口、同素材 · ${esc(row.utm_content)}</p>${table(['观察项','9/3–9/9','9/10–9/15'], rowSpec.map(values=>`<tr>${values.map(cell).join('')}</tr>`))}</div>`;
  });
  return card('9月10日：先排查认证后的任务续接',
    `<div class="comparison-grid">${panels.join('')}</div>`+
    insight('变化落在任务承接环节', '同入口、同素材仍能看到浏览继续，但任务提交和认证返回路线发生变化。回查9月10日上午发布，重点复现素材 → 认证 → 回原页 → 恢复任务。')+
    note('前后分别为7个、6个完整日，比较比例；同素材控制了入口变化。需同时核查任务是否丢失，以及原页处理是否缺少使用埋点。'),
    '专题对照 · 固定观察窗');
}

function earlyExperience(branches, demand) {
  const window = branches?.early_repeat_to_intent?.windows?.find(row=>row.landmark_minutes === 5);
  const groups = window?.by_demand_repeat || [];
  const demands = demand === 'all' ? ['blur','product_video'] : [demand];
  const panels = demands.map(key=>{
    const repeated = groups.find(row=>row.demand === key && row.repeat === 'true');
    const once = groups.find(row=>row.demand === key && row.repeat === 'false');
    if (!repeated || !once) return `<div><h4>${esc(label(key))}</h4>${empty('尚无完整的两组体验对照。')}</div>`;
    const rows = [
      {label:'前5分钟重复使用',value:repeated.continue_rate,detail:`${fraction(repeated)} 出现后续付费意图`},
      {label:'前5分钟未重复',value:once.continue_rate,detail:`${fraction(once)} 出现后续付费意图`},
    ];
    const action = key === 'blur' ? '测试结果对比、调整强度和处理下一张，让继续操作围绕结果改善。'
      : key === 'product_video' ? '更多提交没有对应更高意图。优先测试预览、输出规格与导出权益说明。'
      : '先复现继续与中断的具体体验，再设计同需求实验。';
    return `<div><h4>${esc(label(key))}</h4>${bars(rows, Math.max(.1,...rows.map(x=>x.value || 0)))}<p>${esc(action)}</p></div>`;
  });
  return card('第一次使用之后，什么体验值得强化', `<p class="muted">先看前5分钟的体验，再看第5–30分钟新出现的付费意图。</p><div class="comparison-grid">${panels.join('')}</div>`+
    note('两组均有完整观察时间。重复操作可能来自继续修改，也可能是失败重试；实验同时记录结果质量、下载和重试原因。'), '同需求 · 前5分钟体验对照');
}

function authDiagnosis(branches, demand) {
  const registration = subGroup(branches?.axis?.find(row=>row.node === 'register'), demand);
  const source = branches?.auth_resume;
  let auth = source;
  if (demand !== 'all') {
    const rows = (source?.rows || []).filter(row=>row.demand === demand);
    auth = {
      denominator: rows.length,
      continued: rows.filter(row=>row.progressed).length,
      page_after_auth: rows.filter(row=>row.page_after_auth).length,
      same_path_return: rows.filter(row=>row.same_path_return).length,
    };
    auth.continue_rate = rate(auth.continued,auth.denominator);
  }
  const body = `<div class="comparison-grid"><div><h4>注册后续接任务</h4><div class="comparison-grid">${metric('30分钟内提交任务',pct(registration?.continue_rate),fraction(registration))}${metric('提交间隔中位数',registration?.median_seconds_to_target == null ? '—' : `${Number(registration.median_seconds_to_target).toFixed(1)}秒`)}</div><p class="muted">从已出现注册信号的访问出发。</p></div><div><h4>经过认证页面</h4><div class="comparison-grid">${metric('返回非认证页面',pct(rate(auth?.page_after_auth,auth?.denominator)),`${n(auth?.page_after_auth)} / ${n(auth?.denominator)}`)}${metric('随后提交任务',pct(auth?.continue_rate),fraction(auth))}</div><p class="muted">从打开认证页的访问出发，包含登录等目的。</p></div></div>`;
  return card('把顺畅的注册续接体验复制到认证路线', body+insight('减少重找入口和重复输入', '保留原页面、素材与输入，认证成功后回到原任务并给出清晰下一步。分别验证“成功恢复”和“恢复后开始处理”。'), `${label(demand)} · 完整30分钟观察`);
}

export function renderDiagnosis(report, filters={}) {
  const demand = demandOf(filters), branches = report?.data?.branches;
  if (!branches) return empty('诊断数据正在准备。');
  return filterNote(filters)+sameEntryDiagnosis(branches,demand)+earlyExperience(branches,demand)+authDiagnosis(branches,demand);
}

function aggregate(rows) {
  return rows.reduce((sum,row)=>{
    for (const key of ['n','returned','reused','expanded_choice','other_entry_exploration']) sum[key] += Number(row[key] || 0);
    return sum;
  },{n:0,returned:0,reused:0,expanded_choice:0,other_entry_exploration:0});
}

export function renderRetention(report, filters={}) {
  const demand = demandOf(filters), accounts = report?.data?.accounts;
  if (!accounts?.followup) return empty('回访数据正在准备。');
  const pick = days => demand === 'all' ? accounts.followup[days]?.total?.[0] : accounts.followup[days]?.demand?.find(row=>row.entry_family === demand);
  const seven = pick('7'), fourteen = pick('14');
  const cohortPanel = [7,14].map(days=>{
    const row = days === 7 ? seven : fourteen;
    return `<div><h4>首日之后的${days}日观察</h4><div class="comparison-grid">${metric('完整观察账户',n(row?.n || 0))}${metric('再次使用',pct(rate(row?.reused,row?.n)),`${n(row?.reused || 0)}个账户`)}</div><p class="muted">再次访问 ${n(row?.returned || 0)} · ${pct(rate(row?.returned,row?.n))}</p></div>`;
  }).join('');
  const cohorts = card('把一次完成延伸为下一次需求',`<div class="comparison-grid">${cohortPanel}</div>`+note('以可关联账户去重；首24小时定义首次体验，之后在新访问中再次提交任务才算再次使用。后续包含全部来源，7日与14日各自只纳入观察期完整的账户。'),`${label(demand)} · 账户观察窗`);
  const groupRows = demand === 'all' ? (accounts.followup['7']?.send_band || []) : ['1','2+'].map(band=>({send_band:band,...aggregate((accounts.followup['7']?.cells || []).filter(row=>row.entry_family === demand && row.send_band === band))}));
  const grouped = ['1','2+'].map(band=>groupRows.find(row=>row.send_band === band)).filter(Boolean);
  const rows = grouped.map(row=>({label:row.send_band === '1' ? '首24小时提交1次' : '首24小时提交2次及以上',value:rate(row.reused,row.n),detail:`后7日再次使用 ${n(row.reused)} / ${n(row.n)} 个账户`}));
  const standardized = accounts.comparisons?.find(row=>row.feature === 'send_band' && row.low === '1' && row.high === '2+')?.metrics?.reused;
  const standardizedNote = demand === 'all' && standardized ? note(`在相同需求与进入周内比较后，两组再次使用率为 ${pct(standardized.standardized_rates?.['1'])} 与 ${pct(standardized.standardized_rates?.['2+'])}，差 ${(standardized.standardized_difference_high_minus_low*100).toFixed(2)} 个百分点。`) : '';
  const comparison = card('首日有价值的第二次操作，是回访实验的候选',rows.length ? bars(rows,Math.max(.05,...rows.map(row=>row.value || 0)))+standardizedNote+insight('优先验证继续修改、下一张和保存任务', '让用户容易比较结果并保留下次入口。用成功结果、下载和7日再次使用判断效果，操作次数仅作为过程指标。') : empty('该需求暂无完整的首日体验对照。'),`${label(demand)} · 首日1次 vs 2+次`);
  const demandRows = (accounts.followup['7']?.demand || []).filter(row=>demand === 'all' || row.entry_family === demand).sort((a,b)=>b.n-a.n);
  const byDemand = card('不同需求的回访与复用', demandRows.length ? table(['入口需求','7日完整账户','再次访问','再次使用','14日再次使用'],demandRows.map(row=>{
    const longer = accounts.followup['14']?.demand?.find(x=>x.entry_family === row.entry_family);
    return `<tr>${cell(label(row.entry_family))}${cell(n(row.n))}${cell(`${n(row.returned)} · ${pct(rate(row.returned,row.n))}`)}${cell(`${n(row.reused)} · ${pct(rate(row.reused,row.n))}`)}${cell(longer ? `${n(longer.reused)} / ${n(longer.n)} · ${pct(rate(longer.reused,longer.n))}` : '暂无完整观察')}</tr>`;
  }))+note('先强化同需求复用。跨工具的明确选择样本仍少，暂不优先铺开大规模跨工具推荐。') : empty('该需求还没有完整的7日观察账户。'), '完整观察账户 · 按需求');
  return filterNote(filters)+cohorts+comparison+byDemand;
}

export function renderCommercial(report, filters={}) {
  const demand = demandOf(filters), branches = report?.data?.branches, accounts = report?.data?.accounts;
  if (!branches?.commercial_path) return empty('商业推进数据正在准备。');
  const commercial = branches.commercial_path;
  const paymentVisits = demand === 'all' ? report.data.segments.maturity.summary.paid : report.data.segments.maturity.matrix.find(row=>row.demand===demand)?.paid;
  const cohortPayments = accounts?.payments?.cohort_transactions_after_anchor || 0;
  const transitions = commercial.ordered_transitions || [];
  const stage = key => subGroup(transitions.find(row=>row.key === key),demand);
  const pricing = stage('pricing_to_selection'), selection = stage('selection_to_checkout'), checkout = stage('checkout_to_purchase');
  const labels = {'use_to_pricing':'提交任务 → 看价格','pricing_to_selection':'看价格 → 权益 / 套餐动作','selection_to_checkout':'权益 / 套餐动作 → 结账','checkout_to_use':'结账 → 继续使用'};
  const flow = card('让明确需求走向可完成的结账', `<div class="comparison-grid">${metric('看价格',n(pricing?.denominator || 0),'访问')}${metric('权益 / 套餐动作',n(selection?.denominator || 0),'访问')}${metric('开始结账',n(checkout?.denominator || 0),'访问')}${metric('当次支付记录',paymentVisits ? n(paymentVisits) : '未关联',paymentVisits ? '记录到purchase的访问' : '当前未见匹配的支付记录')}</div>`+
    table(['推进环节','起点访问','后续到达','30分钟推进率'],transitions.filter(row=>labels[row.key]).map(row=>{
      const x = subGroup(row,demand);
      return `<tr>${cell(labels[row.key])}${cell(n(x?.denominator || 0))}${cell(n(x?.continued || 0))}${cell(pct(x?.continue_rate))}</tr>`;
    }))+note('阶段到达允许跳步；表内只统计起点之后的新动作，因此不能直接用上方各阶段总量相除。权益/套餐动作比完整“主动付费意图”口径更窄。'),`${label(demand)} · 完整30分钟观察`);
  const relevantScenes = (branches.download_gate?.by_scene || []).filter(row=>row.scene === 'videoPreview' || row.scene === 'download' || row.reason === 'download' || row.reason === 'first_result');
  const sceneName = row => row.scene === 'videoPreview' ? '视频预览付费提示' : row.scene === 'download' ? '下载付费提示' : row.reason === 'download' ? '下载游客门槛' : '首次结果游客门槛';
  const scenes = card('在预览与下载位置承接拿走结果的意图',table(['价值场景','成熟曝光访问','后续注册','后续明确意图','后续结账'],relevantScenes.map(row=>`<tr>${cell(sceneName(row))}${cell(n(row.visits))}${cell(`${n(row.register_after)} · ${pct(row.register_rate)}`)}${cell(`${n(row.intent_after)} · ${pct(row.intent_rate)}`)}${cell(`${n(row.checkout_after)} · ${pct(row.checkout_rate)}`)}</tr>`))+
    insight('视频优先讲清输出权益，并保留结果', '在预览旁说明已生成什么、付款后得到什么，以及格式、清晰度和时长。结账返回后直接找到原结果，让付费意愿有明确的完成出口。')+
    note('场景表覆盖全部需求，不随页面筛选；同一访问可进入多个场景。下载提示样本较小，适合定向复现。门槛出现并不代表下载完成。'), '价值场景专题 · 全部需求');
  const actionLabels = {CHOOSE:'普通选购',UPGRADE:'升级',trial:'试用',TRIAL:'试用',exit_offer_half_off:'退出优惠',exit_offer_trial:'退出试用优惠'};
  const actions = commercial.checkout_action_type || [];
  const paymentBody = `<div class="comparison-grid"><div><h4>结账动作已能初步区分</h4>${table(['动作类型','访问'],actions.map(row=>`<tr>${cell(actionLabels[row.action_type] || row.action_type)}${cell(n(row.visits))}</tr>`))}<p class="muted">类型仅覆盖带有相应属性的结账信号。</p></div><div><h4>把“尝试”接到真正获得价值</h4><p>优先补齐生成完成、结果展示和下载成功；将注册后的实际下载作为首次价值指标。</p><p>支付成功与订阅创建、试用结束后的首次收费分别记录，才能拆开订阅与试用转付费。</p>${note(`全站可见 ${n(accounts?.payments?.unique_transaction_keys || 0)} 个交易键，${cohortPayments ? `其中${cohortPayments}个匹配本渠道账户` : '目前尚未关联到本渠道账户'}；真实付款还需结合采集覆盖判断。`)}</div></div>`;
  const payment = card('补齐下载与支付的最终结果',paymentBody+`<details class="details-note"><summary>查看建议补齐的业务信号</summary><p>任务：开始、生成完成、失败及原因。结果：展示、下载开始、下载成功。认证：原任务恢复成功。支付：确认到账、订阅创建、试用转首次收费与退款。通过任务、结果、账户和交易标识连接整个过程。</p></details>`, '转化闭环 · 全部需求');
  return filterNote(filters)+flow+scenes+payment;
}

const ACTIONS = [
  {priority:'P0',title:'修复认证后的任务续接',demand:['blur','product_video'],finding:'MGH-01',change:'核查9/10发布；保留原素材、输入和任务，认证后恢复到原位置。',metric:'认证 → 原任务恢复 → 任务开始 / 结果',guard:'重复输入、错误、等待时间与结果质量',experiment:'先复现同入口、同素材路径；确认断点后修复，并在同需求和相近时段比较恢复前后的有效任务。'},
  {priority:'P0',title:'补齐结果、下载与真实支付',demand:['all'],finding:'MGH-08',change:'将生成、下载、到账及试用转付费连接到同一任务与账户。',metric:'注册后下载率、成功交易关联率',guard:'重复交易、漏报与失败原因完整率',experiment:'对一批真实任务和订单逐条验收：结果出现、文件可取、支付到账能在分析中形成完整链路。'},
  {priority:'P1',title:'图片修复：结果对比与继续调整',demand:['blur'],finding:'MGH-05',change:'增加前后对比、调整强度、继续编辑和处理下一张。',metric:'结果后有效操作、下载、7日再次使用',guard:'失败重试率、平均成本、处理耗时',experiment:'对同一修复入口随机展示新旧结果页。以有效结果与下载评价，不以强制增加提交数评价。'},
  {priority:'P1',title:'视频：预览权益与保留结果结账',demand:['product_video'],finding:'MGH-06',change:'预览旁讲清格式、清晰度、时长及付款权益；结账后恢复原结果。',metric:'预览 → 结账 → 确认支付 → 下载',guard:'提示关闭率、退款、结果恢复错误',experiment:'在视频预览位置随机比较现有提示与具体输出权益说明，追踪到确认支付及下载完成。'},
  {priority:'P1',title:'回查并复制8/23有效放量',demand:['all'],finding:'MGH-02',change:'回查卡片排序、曝光与内容操作，在承接可测后做小规模重复投放。',metric:'每千入口注册后使用 / 下载',guard:'获客成本、流量增长与有效率同步看',experiment:'保留同期同入口参照，记录推广变更；确认有效使用增量后再扩大覆盖。'},
  {priority:'P2',title:'新卡扩量与公平版本对照',demand:['blur','watermark','video_edit','model_explore'],finding:'MGH-01',change:'先确保承接链路可测，再比较修复新旧卡及新需求入口。',metric:'同期有效任务 / 入口、下载 / 入口',guard:'需求和设备构成、观察时间完整度',experiment:'采用同期随机或公平曝光。旧卡同期样本不足时继续积累，避免直接比较新旧时期的全量比例。'},
];

export function renderOpportunities(report, filters={}) {
  const demand = demandOf(filters);
  const findings = report?.reviewed_analysis?.findings || [];
  const actions = ACTIONS.filter(row=>demand === 'all' || row.demand.includes('all') || row.demand.includes(demand));
  const roadmap = table(['优先级','产品 / 增长动作','主要验证指标','护栏'],actions.map(row=>`<tr><td><span class="scope-tag">${esc(row.priority)}</span></td><td><strong>${esc(row.title)}</strong><p class="muted">${esc(row.change)}</p></td>${cell(row.metric)}${cell(row.guard)}</tr>`));
  const details = actions.map(row=>{
    const finding = findings.find(item=>item.id === row.finding);
    return `<details class="action-card"><summary><span class="scope-tag">${esc(row.priority)}</span> ${esc(row.title)} <span class="muted">· 查看依据与实验</span></summary><div><h4>怎么验证</h4><p>${esc(row.experiment)}</p>${finding ? `<h4>为什么现在做</h4><p>${esc(finding.observation)}</p><p class="muted">${esc(finding.interpretation)}</p>` : ''}<h4>判断标准</h4><p>${esc(row.metric)}；同步检查${esc(row.guard)}。样本和周期按恢复后的基线与最小有业务价值差异确定。</p></div></details>`;
  }).join('');
  return filterNote(filters)+card('先恢复任务连续性，再放大有效流量',insight('执行顺序', '先让“注册 → 原任务 → 结果 → 下载 → 付款”可完成、可观察，再分别验证图片和视频的关键体验，最后扩大有效引流。')+roadmap, `${label(demand)} · 优先级路线图`)+card('实验卡与证据', details+note('以下依据保留已审观察窗口（截至2026/9/16 15:10，北京时间）。上方实时统计刷新后，产品机制仍通过复现和实验验证。'), '已审解读 · 固定证据窗');
}
