/** Lightweight, dependency-free charts for the MeiGen report.
 * All functions return HTML. Delegate clicks/keyboard events on [data-date] or
 * [data-chart-index] from the application; no script is interpolated in a chart.
 */

export const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export const number = value => Number(value || 0).toLocaleString('zh-CN');
export const percent = value => `${(Number(value || 0) * 100).toFixed(1)}%`;
export const shortNumber = value => Math.abs(value) >= 10000 ? `${(value / 10000).toFixed(1)}万` : number(value);

const BLUE = '#3b82f6';
const MUTED_BLUE = '#bfd6fc';
const finite = value => Number.isFinite(Number(value)) ? Math.max(0, Number(value)) : 0;
const attr = escapeHtml;
const valueText = (value, formatter, row) => attr(formatter ? formatter(value, row) : number(value));

function niceMax(value) {
  if (value <= 0) return 1;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  const leading = value / magnitude;
  const nice = [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].find(x => x >= leading) || 10;
  return nice * magnitude;
}

function emptyState(message = '当前筛选没有可展示的数据') {
  return `<div class="chart-empty" role="status" style="min-height:160px;display:grid;place-items:center;color:#8a9099;font-size:13px">${attr(message)}</div>`;
}

/** Vertical bars with undistorted HTML axes and native SVG tooltips.
 * selected is an index, a matching date, or a matching xKey value.
 * A fixed maxValue is useful when charts must share a common scale.
 */
export function chartBars(data = [], options = {}) {
  if (!data.length) return emptyState(options.emptyMessage);
  const {
    xKey = 'label', yKey = 'value', height = 240, color = BLUE,
    label = '', selected = null, dateKey = 'date', formatValue,
    formatTick = formatValue, maxValue, showLabels = true, maxLabels = 8,
    highlightKey = 'spike_candidate', partialKey = 'partial_day',
  } = options;
  const safeHeight = Math.max(120, finite(height));
  const values = data.map(row => finite(row[yKey]));
  const maximum = Math.max(...values);
  const ceiling = Math.max(finite(maxValue), niceMax(maximum || finite(maxValue) || 4), 0.000001);
  const plotHeight = safeHeight - (showLabels ? 32 : 5);
  const width = 1000;
  const step = width / data.length;
  const gap = Math.max(1, Math.min(7, step * 0.22));
  const barWidth = Math.max(0.25, step - gap);
  const rows = 4;
  const selectedMatch = (row, i) => selected !== null && selected !== undefined && (selected === i || selected === row[dateKey] || selected === row[xKey]);

  const grid = Array.from({length: rows + 1}, (_, i) => {
    const y = i * plotHeight / rows;
    return `<line x1="0" x2="${width}" y1="${y}" y2="${y}" stroke="${i === rows ? '#d7dde5' : '#e8ebef'}" stroke-width="1" vector-effect="non-scaling-stroke"/>`;
  }).join('');
  const ticks = Array.from({length: rows + 1}, (_, i) => {
    const value = ceiling * (1 - i / rows);
    const defaultFormatter = v => ceiling < 10 ? Number(v.toFixed(2)).toString() : shortNumber(Math.round(v));
    return `<span style="position:absolute;right:10px;top:${i * plotHeight / rows}px;transform:translateY(-50%);white-space:nowrap">${valueText(value, formatTick || defaultFormatter)}</span>`;
  }).join('');
  const bars = data.map((row, i) => {
    const value = values[i];
    const active = selectedMatch(row, i);
    const barHeight = Math.max(value > 0 ? 1 : 0, value / ceiling * (plotHeight - 2));
    const x = step * i + gap / 2;
    const y = plotHeight - barHeight;
    const date = row[dateKey];
    const textLabel = row[xKey] ?? date ?? `${i + 1}`;
    const tooltip = row.unavailable ? `${textLabel}\n该日尚无完整的行为记录` : `${textLabel}\n${label ? `${label}：` : ''}${formatValue ? formatValue(value, row) : number(value)}${row[partialKey] ? '\n当日数据尚未完整' : ''}`;
    const selectedStyle = active ? 'outline:none;filter:brightness(.94)' : 'outline:none';
    return `<g role="button" tabindex="0" aria-label="${attr(tooltip)}" aria-pressed="${active}" data-chart-index="${i}"${date != null ? ` data-date="${attr(date)}"` : ''} style="cursor:pointer;${selectedStyle}"><title>${attr(tooltip)}</title><rect x="${step * i}" y="0" width="${step}" height="${plotHeight}" fill="${active ? '#eff6ff' : row.unavailable ? '#f3f4f6' : 'transparent'}"/><rect x="${x}" y="${y}" width="${barWidth}" height="${barHeight}" fill="${attr(color)}" fill-opacity="${active ? 0.95 : row[partialKey] ? 0.25 : 0.43}" stroke="${attr(color)}" stroke-opacity="${active || row[highlightKey] ? 1 : 0.5}" stroke-width="${active ? 1.5 : 1}"${row[partialKey] ? ' stroke-dasharray="3 2"' : ''} vector-effect="non-scaling-stroke"/>${active ? `<line x1="${x}" x2="${x + barWidth}" y1="${plotHeight + 2}" y2="${plotHeight + 2}" stroke="${attr(color)}" stroke-width="3" vector-effect="non-scaling-stroke"/>` : ''}</g>`;
  }).join('');

  const labelCount = Math.min(Math.max(2, maxLabels), data.length);
  const labelIndexes = new Set(Array.from({length: labelCount}, (_, i) => Math.round(i * (data.length - 1) / Math.max(1, labelCount - 1))));
  const labels = showLabels ? [...labelIndexes].map(i => {
    const row = data[i];
    const rawLabel = row[xKey] ?? row[dateKey] ?? `${i + 1}`;
    const textLabel = /^\d{4}-\d{2}-\d{2}$/.test(String(rawLabel)) ? String(rawLabel).slice(5).replace('-', '/') : rawLabel;
    const position = ((i + .5) / data.length) * 100;
    const transform = i === 0 ? '0' : i === data.length - 1 ? '-100%' : '-50%';
    return `<span style="position:absolute;top:${plotHeight + 12}px;left:${position}%;transform:translateX(${transform});white-space:nowrap;${selectedMatch(row, i) ? `color:${attr(color)};font-weight:600` : ''}">${attr(textLabel)}</span>`;
  }).join('') : '';

  return `<div class="viz-bars" role="group" aria-label="${attr(label || '数据趋势')}" style="height:${safeHeight}px;display:grid;grid-template-columns:52px minmax(0,1fr);width:100%;font-size:11px;line-height:1;color:#8a9099;padding-top:7px"><div class="viz-y-axis" aria-hidden="true" style="position:relative">${ticks}</div><div style="position:relative;min-width:0"><svg class="viz-bars-svg" xmlns="http://www.w3.org/2000/svg" width="100%" height="${plotHeight}" viewBox="0 0 ${width} ${plotHeight}" preserveAspectRatio="none" style="display:block;overflow:visible">${grid}${bars}</svg><div class="viz-x-axis" aria-hidden="true">${labels}</div></div></div>`;
}

/** Meigen daily series, one metric at a time to avoid mixed count/rate scales. */
export function trendChart(days = [], options = {}) {
  const {metric = 'visits', label, selectedDate, ...rest} = options;
  const labels = {
    visits: '入口访问', registered: '注册信号', used: '使用信号',
    registered_use: '注册后使用', registered_use_rate: '注册后使用率',
    used_rate: '使用率', download_intent: '下载意图', checkout: '进入结账',
    paid: '支付记录',
  };
  const rateMetric = metric.endsWith('_rate');
  return chartBars(days, {
    xKey: 'date', yKey: metric, dateKey: 'date', label: label || labels[metric] || metric,
    selected: selectedDate, formatValue: rateMetric ? percent : number,
    formatTick: rateMetric ? percent : shortNumber,
    maxValue: rateMetric && !days.some(day => finite(day[metric]) > 0) ? 1 : undefined,
    ...rest,
  });
}

/** Two series of horizontal bars; each row is a direct side-by-side comparison.
 * Rows: {label, a, b}. Pass alternate key names for existing report fields.
 */
export function compareBars(rows = [], options = {}) {
  if (!rows.length) return emptyState(options.emptyMessage);
  const {
    labelKey = 'label', aKey = 'a', bKey = 'b', aLabel = '继续', bLabel = '中断',
    aColor = BLUE, bColor = MUTED_BLUE, formatValue = number, maxValue,
    showLegend = true,
  } = options;
  const ceiling = Math.max(finite(maxValue), ...rows.flatMap(row => [finite(row[aKey]), finite(row[bKey])]), 0.000001);
  const legend = showLegend ? `<div class="viz-legend" style="display:flex;align-items:center;gap:20px;margin:0 0 22px;font-size:12px;color:#727a85"><span style="display:flex;align-items:center;gap:7px"><i style="width:9px;height:9px;border-radius:2px;background:${attr(aColor)}"></i>${attr(aLabel)}</span><span style="display:flex;align-items:center;gap:7px"><i style="width:9px;height:9px;border-radius:2px;background:${attr(bColor)}"></i>${attr(bLabel)}</span></div>` : '';
  const body = rows.map((row, i) => {
    const a = finite(row[aKey]), b = finite(row[bKey]);
    const series = [[a, aColor, aLabel], [b, bColor, bLabel]].map(([value, color, seriesLabel]) => `<div style="display:grid;grid-template-columns:minmax(0,1fr) 58px;gap:12px;align-items:center;min-height:17px"><div style="height:9px;border-radius:2px;background:#f5f6f8;overflow:hidden" title="${attr(seriesLabel)}：${valueText(value, formatValue, row)}"><div style="width:${value / ceiling * 100}%;height:100%;background:${attr(color)};border-radius:2px"></div></div><span style="font-size:12px;color:#4b5563;text-align:right;font-variant-numeric:tabular-nums">${valueText(value, formatValue, row)}</span></div>`).join('');
    return `<div class="viz-compare-row" data-compare-index="${i}" style="margin-bottom:20px"><div style="font-size:12px;color:#434a53;margin-bottom:8px;line-height:1.45;overflow-wrap:anywhere">${attr(row[labelKey])}</div><div>${series}</div></div>`;
  }).join('');
  return `<div class="viz-compare" role="img" aria-label="${attr(aLabel)}与${attr(bLabel)}对照">${legend}${body}</div>`;
}

/** Decorative inline trend; use adjacent visible text for exact values. */
export function sparkline(values = [], {color = BLUE, width = 100, height = 30, fill = false} = {}) {
  if (!values.length) return '';
  const points = values.map(finite);
  const w = Math.max(10, finite(width)), h = Math.max(10, finite(height));
  const lo = Math.min(...points), hi = Math.max(...points);
  const range = hi - lo;
  const coords = points.map((value, i) => [points.length === 1 ? w / 2 : 2 + i / (points.length - 1) * (w - 4), range ? h - 3 - (value - lo) / range * (h - 6) : h / 2]);
  const line = coords.map(([x,y], i) => `${i ? 'L' : 'M'}${x.toFixed(2)},${y.toFixed(2)}`).join(' ');
  const area = fill && coords.length > 1 ? `<path d="${line} L${coords.at(-1)[0]},${h} L${coords[0][0]},${h} Z" fill="${attr(color)}" fill-opacity=".08"/>` : '';
  const dot = points.length === 1 ? `<circle cx="${w/2}" cy="${h/2}" r="2" fill="${attr(color)}"/>` : '';
  return `<svg class="viz-sparkline" xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true" style="display:block;max-width:100%;overflow:visible">${area}<path d="${line}" fill="none" stroke="${attr(color)}" stroke-width="1.7" vector-effect="non-scaling-stroke" stroke-linecap="round" stroke-linejoin="round"/>${dot}</svg>`;
}

export const lineOrBars = chartBars;
