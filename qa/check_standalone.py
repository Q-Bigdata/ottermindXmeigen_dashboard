"""Run a copied project with no parent workspace imports or credentials."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parents[1]
report=json.loads((ROOT/'data/report.json').read_text())
source=ROOT/'generations'/report['generation_id']
with tempfile.TemporaryDirectory(prefix='meigen-standalone-') as directory:
    dest=Path(directory)/'meigen-full-history';dest.mkdir()
    for p in ROOT.glob('*.py'):shutil.copy2(p,dest/p.name)
    shutil.copytree(ROOT/'report',dest/'report')
    (dest/'data').mkdir();shutil.copy2(ROOT/'data/report.json',dest/'data/report.json')
    for name in ('data','accounts'):
        target=dest/'generations'/report['generation_id']/name;target.mkdir(parents=True)
        for p in (source/name).glob('*.json'):shutil.copy2(p,target/p.name)
    env=dict(os.environ);env.pop('CHAT2DB_MCP_TOKEN',None);env['PYTHONPATH']=''
    output=dest/'rebuilt.json'
    result=subprocess.run([sys.executable,str(dest/'refresh_pipeline.py'),'--local-only','--cutoff',report['window']['end'],'--output',str(output)],cwd=directory,env=env,capture_output=True,text=True,timeout=120)
    if result.returncode:raise RuntimeError(result.stderr[-1500:])
    rebuilt=json.loads(output.read_text())
    assert rebuilt['data']['quality']['visits']==report['data']['quality']['visits']
    assert rebuilt['data']['accounts']['audit']['accounts']==report['data']['accounts']['audit']['accounts']
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    process=subprocess.Popen([sys.executable,str(dest/'run.py'),'--cached-only','--port',str(port)],cwd=directory,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        status=None
        for _ in range(50):
            try:
                with urlopen(f'http://127.0.0.1:{port}/api/status',timeout=1) as response:status=json.load(response)
                break
            except OSError:time.sleep(.1)
        assert status and status['report']['available']
        with urlopen(f'http://127.0.0.1:{port}/') as response:assert b'./app.js' in response.read()
    finally:
        process.terminate();process.wait(timeout=10)
    verification={'passed':True,'standalone_copy':True,'no_parent_imports':True,'no_credentials_required_for_saved_recompute':True,'recomputed_visits':rebuilt['data']['quality']['visits'],'recomputed_accounts':rebuilt['data']['accounts']['audit']['accounts'],'launcher_served_api':True,'input_generation':report['generation_id']}
    (ROOT/'qa/standalone-results.json').write_text(json.dumps(verification,ensure_ascii=False,indent=2))
    print(json.dumps(verification,ensure_ascii=False))
