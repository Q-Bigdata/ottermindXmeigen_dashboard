"""Behavioral QA: temporal leakage, passive routes, maturity and identity boundaries."""
import unittest
from datetime import datetime, timezone, timedelta
from analytics_deep_comparison import immediate, commercial, account_events, node_comparisons
from analytics_branches import _normalize

T=datetime(2026,8,1,tzinfo=timezone.utc)

def event(visit,minutes,name=None,path='/tools/fix-blurry-pictures',props=None):
    return {'event_id':f'{visit}-{minutes}-{name}', 'visit_id':visit, 'session_id':visit,
            'created_at':(T+timedelta(minutes=minutes)).isoformat(),
            'event_name':name, 'event_type':2 if name else 1,
            'url_path':path,'properties':props or {}}

def data(specs,end_minutes=120):
    visits=[]; events=[]
    for vid,rows in specs.items():
        visits.append({'visit_id':vid,'start_at':T.isoformat(),'entry_path':'/tools/fix-blurry-pictures',
                       'initial_demand':'blur','utm_source':'meigen','device':'desktop'})
        events.append(event(vid,0))
        events.extend(event(vid,*r) for r in rows)
    cutoff=T+timedelta(minutes=end_minutes)
    items,_=_normalize(visits,events,T,cutoff)
    return items,cutoff

class DeepAnalysisTests(unittest.TestCase):
    def test_new_use_and_intent_risk_sets_exclude_existing_targets(self):
        items,end=data({'already_used':[(1,'send_message'),(2,None,'/studio'),(3,'send_message')],
                        'new_use':[(1,None,'/studio'),(2,'send_message')],
                        'prior_intent':[(1,'plan_clicked'),(2,'send_message'),(3,'plan_clicked')]})
        out=node_comparisons(items,end)
        browse=next(x for x in out['nodes'] if x['key']=='browse_use')
        self.assertEqual((browse['n'],browse['continued'],browse['prior_target_excluded']),(1,1,1))
        intent=next(x for x in out['nodes'] if x['key']=='use_commercial')
        self.assertEqual(intent['prior_target_excluded'],1)

    def test_passive_task_routes_do_not_count_as_continuation(self):
        items,end=data({'stop':[(1,'send_message'),(2,None,'/studio/task/opaque'),(3,'paywall_shown')],
                        'continue':[(1,'send_message'),(2,None,'/studio/task/other'),(4,'send_message')]})
        out,rows=immediate(items,end)
        self.assertEqual((out['n'],out['continued'],out['stopped']),(2,1,1))
        self.assertEqual(out['route_or_passive_only'],1)
        self.assertEqual(out['repeated'],1)

    def test_pre_anchor_features_do_not_include_later_actions(self):
        items,end=data({'v':[(1,'send_message','/studio',{'has_attachments':'false'}),
                            (2,None,'/auth'),(3,'ui_click'),
                            (4,'send_message','/studio',{'has_attachments':'true'})]})
        out,rows=immediate(items,end)
        self.assertEqual(rows[0]['attachments'],'false')
        self.assertEqual(rows[0]['auth_before'],'false')
        self.assertEqual(rows[0]['choice_before'],'false')
        self.assertTrue(rows[0]['continued'])

    def test_unfinished_window_and_late_event_excluded(self):
        items,end=data({'late':[(1,'send_message'),(31.1,'send_message')],
                        'immature':[(100,'send_message'),(101,'send_message')]})
        out,_=immediate(items,end)
        self.assertEqual((out['n'],out['continued'],out['observation_incomplete']),(1,0,1))

    def test_landmark_does_not_require_surviving_or_include_early_conversion(self):
        items,end=data({'repeat':[(1,'send_message'),(3,'send_message'),(8,'plan_clicked')],
                        'one':[(1,'send_message')],
                        'early_intent':[(1,'send_message'),(2,'plan_clicked'),(7,'send_message')],
                        'too_late':[(1,'send_message'),(8,'send_message'),(9,'plan_clicked')]})
        out=commercial(items,end,False)
        mark=next(m for m in out['landmarks'] if m['minutes']==5 and m['target']=='intent')
        self.assertEqual(mark['early_target_excluded'],1)
        self.assertEqual((mark['n'],mark['continued']),(3,2))
        feature={f['value']:f for f in mark['feature_rates'] if f['feature']=='repeat_early'}
        self.assertEqual((feature['true']['n'],feature['true']['continued']),(1,1))
        self.assertEqual((feature['false']['n'],feature['false']['continued']),(2,1))

    def test_followup_identity_only_after_effective_link_with_anchor_restore(self):
        facts=[{'account_id':'account','anchor_visit':'anchor','t0':T.isoformat()}]
        contexts=[{'session_id':'follow','current_account_id':'account','links':[{'account_id':'account','link_at':(T+timedelta(minutes=20)).isoformat()}]}]
        history=[event('follow',10,'send_message'),event('follow',21,'send_message')]
        events=[event('anchor',1,'send_message')]
        grouped,_=account_events(facts,history,events,contexts,[],T+timedelta(days=1))
        self.assertEqual([e['visit_id'] for e in grouped['account']],['anchor','follow'])
        self.assertEqual(grouped['account'][-1]['at'],T+timedelta(minutes=21))

    def test_checkout_opened_does_not_turn_stopped_user_into_continued(self):
        items,end=data({'v':[(1,'send_message'),(4,'begin_checkout'),(4.01,'checkout_opened')]})
        out=commercial(items,end,False)['checkout_experience']
        self.assertEqual(out['n'],1)
        self.assertEqual(out['after30']['no_active_action'],1)
        self.assertEqual(out['after30']['used_again'],0)

    def test_six_node_registration_order_is_not_collapsed_into_use(self):
        items,end=data({'reg_first':[(1,'sign_up'),(2,'send_message'),(3,'ui_click')],
                        'use_first':[(1,'send_message'),(2,'sign_up'),(4,'send_message')]})
        nodes={n['key']:n for n in node_comparisons(items,end)['nodes']}
        self.assertEqual((nodes['signup_use']['n'],nodes['signup_use']['continued']),(2,2))
        self.assertEqual((nodes['use_register']['n'],nodes['use_register']['continued']),(1,1))
        # A click is other active behavior; the attempt branch requires another
        # submit or a commercial intent and keeps that difference explicit.
        self.assertEqual((nodes['attempt_continue']['n'],nodes['attempt_continue']['continued']),(2,1))
        self.assertIsNone(nodes['attempt_continue']['advance_time']['stopped']['median_seconds'])

    def test_prefix_complexity_and_last_state_do_not_leak_future(self):
        items,end=data({'v':[(1,None,'/auth'),(2,None,'/tools/fix-blurry-pictures'),
                            (3,None,'/studio'),(4,'send_message'),
                            (5,'guest_gate_shown','/studio',{'reason':'first_result'}),
                            (6,None,'/auth')]})
        output=node_comparisons(items,end)
        node=next(n for n in output['nodes'] if n['key']=='use_register')
        metrics=node['profiles']['stopped']['metrics']
        self.assertEqual(metrics['pre_backtracks']['median'],1)
        self.assertEqual(metrics['pre_page_depth']['median'],4)
        self.assertEqual(metrics['pre_active_actions']['median'],0)
        self.assertEqual(metrics['seconds_to_anchor']['median'],240)
        self.assertEqual(node['stopped_states'][0]['state'],'result_or_download_gate')
        self.assertEqual(node['last_observed_states'][0]['state'],'last_auth_page')
        gate=output['result_gate_register']
        self.assertEqual((gate['n'],gate['continued'],gate['stopped']),(1,0,1))

    def test_node_unknown_window_is_reported_in_its_demand(self):
        items,end=data({'v':[(1,'send_message')]},end_minutes=20)
        nodes=node_comparisons(items,end)['nodes']
        node=next(n for n in nodes if n['key']=='attempt_continue')
        self.assertEqual((node['n'],node['observation_incomplete'],node['eligible_arrived']),(0,1,1))
        self.assertEqual(node['by_demand'][0]['observation_incomplete'],1)

if __name__=='__main__':unittest.main()
