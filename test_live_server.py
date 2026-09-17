"""Refresh lifecycle tests using isolated fake runners; never call Chat2DB."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('meigen_live_server', ROOT/'live_server.py')
server_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server_module)

FAKE_RUNNER = r'''
import argparse, datetime, json, os, pathlib, subprocess, sys, time
p=argparse.ArgumentParser();p.add_argument('--cutoff');p.add_argument('--output');a=p.parse_args()
root=pathlib.Path.cwd();mode=(root/'mode').read_text();output=pathlib.Path(a.output)
progress={'request_id':os.environ['MEIGEN_REFRESH_REQUEST_ID'],'phase':'extract_events','updated_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'completed':1,'total':3}
pathlib.Path(os.environ['MEIGEN_PROGRESS_PATH']).write_text(json.dumps(progress))
if mode=='fail':
    print('secret-must-never-be-in-an-http-error',file=sys.stderr);sys.exit(7)
report={'generation_id':'fake-'+str(time.time_ns()),'generated_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'window':{'start':'2026-08-01T00:00:00Z','end':a.cutoff,'timezone':'Asia/Shanghai'},'data':{'same_generation':True},'findings':[]}
if mode=='invalid':report['window']['end']='2026-08-02T00:00:00Z'
output.write_text(json.dumps(report))
if mode=='tree':
    subprocess.Popen([sys.executable,'-c',"import pathlib,time; time.sleep(2);pathlib.Path('child-survived').write_text('bad')"])
    time.sleep(10)
if mode in ('slow','timeout'):time.sleep(1)
if mode=='scheduled':time.sleep(.3)
'''


def sample_report():
    return {'generation_id':'previous-generation','generated_at':'2026-09-15T00:00:00Z',
            'window':{'start':'2026-08-01T00:00:00Z','end':'2026-09-15T00:00:00Z','timezone':'Asia/Shanghai'},
            'data':{'previous_generation':True},'findings':[]}


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        (self.root/'data').mkdir();(self.root/'data'/'report.json').write_text(json.dumps(sample_report()))
        self.before=(self.root/'data'/'report.json').read_bytes()
        (self.root/'fake.py').write_text(FAKE_RUNNER);(self.root/'mode').write_text('slow')
        self.manager=server_module.RefreshManager(self.root, runner=self.root/'fake.py', auto_interval_seconds=300)
    def tearDown(self):
        self.manager.close();self.temp.cleanup()
    def mode(self,value):
        (self.root/'mode').write_text(value)
    def wait_finished(self, timeout=4):
        deadline=time.monotonic()+timeout
        while self.manager.status()['refresh']['state'] in {'running','cancelling'} and time.monotonic()<deadline:
            time.sleep(.02)
        self.assertNotIn(self.manager.status()['refresh']['state'],{'running','cancelling'})
        return self.manager.status()
    def wait_phase(self):
        deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            s=self.manager.status()
            if s['refresh']['phase']=='extract_events':return s
            time.sleep(.02)
        self.fail('No actual runner progress was read')
    def test_shared_settings_persist_and_disabled_auto_does_not_start(self):
        self.assertFalse(self.manager.get_settings()['auto_enabled'])
        self.assertEqual(self.manager.start_refresh(trigger='auto')[0],409)
        self.assertEqual(self.manager.update_settings(True)[0],200)
        self.manager.close()
        self.manager=server_module.RefreshManager(self.root,runner=self.root/'fake.py')
        self.assertTrue(self.manager.get_settings()['auto_enabled'])
        self.manager.update_settings(False)
        self.assertIsNone(self.manager.get_settings()['next_auto_at'])
    def test_single_worker_progress_and_disable_keeps_current_manual(self):
        self.assertEqual(self.manager.start_refresh()[0],202)
        self.assertEqual(self.manager.start_refresh()[0],409)
        s=self.wait_phase();self.assertEqual(s['refresh']['units'],{'completed':1,'total':3})
        self.assertEqual(s['refresh']['trigger'],'manual')
        self.manager.update_settings(False)
        self.assertEqual(self.manager.status()['refresh']['state'],'running')
        s=self.wait_finished();self.assertEqual(s['refresh']['state'],'succeeded')
        self.assertNotEqual(s['report']['generation_id'],'previous-generation')
    def test_cancel_kills_owned_child_group_and_preserves_snapshot(self):
        self.mode('tree');self.manager.start_refresh();self.wait_phase()
        self.assertEqual(self.manager.cancel_refresh()[0],202)
        s=self.wait_finished();self.assertEqual(s['refresh']['state'],'cancelled')
        self.assertIsNone(s['refresh']['error']);self.assertTrue(s['serving_previous_report'])
        self.assertEqual(self.before,(self.root/'data'/'report.json').read_bytes())
        time.sleep(2.1);self.assertFalse((self.root/'child-survived').exists())
    def test_failure_hides_runner_output_and_keeps_previous_generation(self):
        self.mode('fail');self.manager.start_refresh();s=self.wait_finished()
        self.assertEqual(s['refresh']['error']['code'],'runner_failed')
        self.assertNotIn('secret-must',json.dumps(s))
        self.assertEqual(self.before,(self.root/'data'/'report.json').read_bytes())
        audit=(self.root/'data'/'refresh_history.jsonl').read_text()
        self.assertIn('runner_failed',audit);self.assertNotIn('secret-must',audit)
    def test_invalid_cutoff_and_timeout_keep_previous_generation(self):
        self.mode('invalid');self.manager.start_refresh();s=self.wait_finished()
        self.assertEqual(s['refresh']['error']['code'],'invalid_report')
        self.mode('timeout');self.manager.timeout_seconds=.2;self.manager.start_refresh();s=self.wait_finished()
        self.assertEqual(s['refresh']['error']['code'],'refresh_timeout')
        self.assertEqual(self.before,(self.root/'data'/'report.json').read_bytes())
    def test_auto_schedule_waits_from_completion_and_stops_globally(self):
        self.manager.close();self.mode('scheduled')
        self.manager=server_module.RefreshManager(self.root,runner=self.root/'fake.py',auto_interval_seconds=.4)
        self.manager.update_settings(True)
        deadline=time.monotonic()+2
        while self.manager.status()['refresh']['state']=='idle' and time.monotonic()<deadline:time.sleep(.02)
        self.assertEqual(self.manager.status()['refresh']['trigger'],'auto')
        self.wait_finished()
        self.assertGreater((self.manager.next_auto_at-server_module.utc_now()).total_seconds(),.25)
        self.manager.update_settings(False);last=self.manager.status()['refresh']['request_id']
        time.sleep(.6);self.assertEqual(last,self.manager.status()['refresh']['request_id'])


class HTTPTests(unittest.TestCase):
    def test_tabs_share_settings_and_legacy_posts_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'fake.py').write_text(FAKE_RUNNER);(root/'mode').write_text('slow')
            server=server_module.make_server(root,port=0,runner=root/'fake.py')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            def req(path,body=None):
                request=Request(f'http://127.0.0.1:{server.server_port}{path}',data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
                try:
                    with urlopen(request) as response:return response.status,json.load(response)
                except HTTPError as error:return error.code,json.load(error)
            try:
                self.assertEqual(req('/api/refresh',{})[1]['error']['code'],'require_refresh_trigger')
                self.assertFalse(req('/api/settings')[1]['settings']['auto_enabled'])
                req('/api/settings',{'auto_enabled':True})
                self.assertTrue(req('/api/status')[1]['settings']['auto_enabled'])
                req('/api/settings',{'auto_enabled':False})
                self.assertEqual(req('/api/refresh',{'trigger':'auto'})[1]['error']['code'],'auto_refresh_disabled')
                self.assertEqual(req('/api/refresh',{'trigger':'manual'})[0],202)
                self.assertEqual(req('/api/refresh/cancel',{})[0],202)
            finally:
                server.shutdown();server.manager.close();server.server_close();thread.join()


class SourceCacheTests(unittest.TestCase):
    def test_next_refresh_uses_published_generation_not_unpublished_work(self):
        spec=importlib.util.spec_from_file_location('meigen_refresh_pipeline',ROOT/'refresh_pipeline.py')
        pipeline=importlib.util.module_from_spec(spec);spec.loader.exec_module(pipeline)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);current=root/'data';current.mkdir()
            published=root/'generations'/'published'/'data';published.mkdir(parents=True)
            orphan=root/'generations'/'cancelled-work'/'data';orphan.mkdir(parents=True)
            old_cutoff='2026-09-14T00:00:00Z';published_cutoff='2026-09-15T00:00:00Z'
            (current/'quality.json').write_text(json.dumps({'cutoff':old_cutoff}))
            (published/'quality.json').write_text(json.dumps({'cutoff':published_cutoff}))
            (orphan/'quality.json').write_text(json.dumps({'cutoff':'2026-09-16T00:00:00Z'}))
            report=sample_report();report['generation_id']='published'
            (current/'report.json').write_text(json.dumps(report));pipeline.BASE=root
            self.assertEqual(pipeline.source_cache(),published)
            # Even a stray newer legacy cache marker must not switch generations.
            (current/'cache_generation.json').write_text(json.dumps({'path':str(orphan.parent),'cutoff':'2026-09-16T00:00:00Z'}))
            self.assertEqual(pipeline.source_cache(),published)

if __name__=='__main__':unittest.main()
