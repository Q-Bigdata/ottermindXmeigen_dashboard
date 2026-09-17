"""Recompute short factual observations with each report generation."""
def pct(k,n):return f'{100*k/n:.2f}%' if n else '暂无可计算样本'
def build(quality,segments,branches,accounts):
 out=[];matrix=segments['maturity']['matrix'];names={x['demand']:x['label'] for x in matrix};top=sorted(matrix,key=lambda x:x['visits'],reverse=True)[:2]
 if top:
  n=sum(x['visits'] for x in top);out.append({'id':'LIVE-demand','type':'fact','title':'主要入口需求','text':f"{'、'.join(x['label'] for x in top)}合计{n:,}次入口，占{pct(n,quality['visits'])}。",'counts':{'numerator':n,'denominator':quality['visits']}})
 full=[x for x in segments['trends']['days'] if not x['partial_day']]
 if len(full)>1:
  before,after=full[-2:];out.append({'id':'LIVE-latest-day','type':'fact','title':'最近完整日','text':f"{after['date']}入口{after['visits']:,}次，前一天{before['visits']:,}次；可见任务提交{after['used']}次访问，前一天{before['used']}次。",'dates':[before['date'],after['date']]})
 for node in ('register','use'):
  x=next(x for x in branches['axis'] if x['node']==node)
  out.append({'id':'LIVE-node-'+node,'type':'fact','title':'注册后使用' if node=='register' else '首次使用后的继续',
   'text':f"{x['continued']:,}/{x['denominator']:,}个完整观察起点在30分钟内达到{x['target_label']}（{pct(x['continued'],x['denominator'])}）。",'counts':{'numerator':x['continued'],'denominator':x['denominator']},'metric':'same_visit_30m_ordered_progress'})
 bands={x['send_band']:x for x in accounts['followup']['7']['send_band']}
 if '1' in bands and '2+' in bands:
  a,b=bands['1'],bands['2+'];out.append({'id':'LIVE-repeat-return','type':'association','title':'首次体验与再次使用','text':f"首24小时1次提交者，随后7日再提交{a['reused']}/{a['n']}（{pct(a['reused'],a['n'])}）；2次及以上者{b['reused']}/{b['n']}（{pct(b['reused'],b['n'])}）。",'interpretation':'此处描述观测关联，继续编辑等具体改动通过实验验证。','sample_unit':'linked_account'})
 pay=accounts['payments'];out.append({'id':'LIVE-payment','type':'fact','title':'付款记录关联','text':f"当前关联到MeiGen锚点之后的purchase交易键{pay['cohort_transactions_after_anchor']}个；同次访问结账{branches['commercial_path']['visit_reach'].get('checkout',0)}次。",'interpretation':'purchase记录与真实付款的采集覆盖需分开，未关联到交易不代表真实付费率为零。'})
 return out
