"""Actual MCP root -> run_stage -> detached harmless child cancellation."""
import asyncio
import json
import os
from pathlib import Path
import signal
import sys
import time

import pytest
from smilepcb import mcp_jobs

ROOT=Path(__file__).resolve().parents[1]


def make_tree(tmp_path,monkeypatch,*,cleanup_delay=.35):
    for name in ('layout.json','config.json','tools.json'):(tmp_path/name).write_text('{}')
    (tmp_path/'source').mkdir()
    payload=dict(kle='layout.json',config='config.json',tools='tools.json',controller_source='source',output='cancelled',completion_budget_s=1200,placement_attempts=1)
    marker=tmp_path/'tree.json';stage=tmp_path/'completion.py';root=tmp_path/'root.py'
    stage.write_text('''import json,os,signal,subprocess,sys,time
from pathlib import Path
from smilepcb.stm32.context import install_cancellation_handler
install_cancellation_handler()
out=Path(sys.argv[1]);child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],start_new_session=True)
marker=Path('''+repr(str(marker))+''');temporary=marker.with_suffix('.tmp')
temporary.write_text(json.dumps({'stage':os.getpid(),'detached':child.pid}));temporary.replace(marker)
try:time.sleep(30)
finally:
 time.sleep('''+repr(cleanup_delay)+''')
 try:os.killpg(child.pid,signal.SIGKILL)
 except ProcessLookupError:pass
 child.wait();(out/'stage-cleanup.json').write_text(json.dumps({'detached_reaped':True}))
''')
    root.write_text('''import json,os,sys,time
from pathlib import Path
from smilepcb import mcp_jobs
from smilepcb.stm32.generate import run_stage
marker=Path('''+repr(str(marker))+''')
def dispatch(request):
 if request['generation']['output']=='after-cancel':
  alive=[]
  for name,pid in json.loads(marker.read_text()).items():
   try:os.kill(pid,0);alive.append(name)
   except ProcessLookupError:pass
  return {'owned_alive_at_next_start':alive}
 out=Path(request['workspace'])/request['generation']['output'];out.mkdir();history=[]
 try:run_stage('complete',[sys.executable,'''+repr(str(stage))+''',str(out)],20,out=out,env=os.environ.copy(),history=history,started=time.monotonic())
 except BaseException as error:
  (out/'failure.json').write_text(json.dumps({'complete':False,'error':type(error).__name__}));raise
 finally:(out/'history.json').write_text(json.dumps(history))
mcp_jobs.dispatch=dispatch
mcp_jobs.main()
''')
    original=asyncio.create_subprocess_exec
    async def launch(*argv,**kwargs):
        assert argv[-2:]==('-m','smilepcb.mcp_jobs')
        env={**os.environ,**kwargs.pop('env',{}),'PYTHONPATH':str(ROOT)}
        return await original(sys.executable,str(root),env=env,**kwargs)
    monkeypatch.setattr(mcp_jobs.asyncio,'create_subprocess_exec',launch)
    return payload,marker


@pytest.mark.parametrize('trigger',['cancel','timeout','cancel_twice'])
def test_real_root_stage_tree_is_drained_before_next_job(tmp_path,monkeypatch,trigger):
    payload,marker=make_tree(tmp_path,monkeypatch)
    if trigger=='timeout':monkeypatch.setattr(mcp_jobs,'generation_timeout',lambda _:1.0)
    async def run():
        jobs=mcp_jobs.JobRunner(tmp_path);first=asyncio.create_task(jobs.generate(payload));deadline=time.monotonic()+5
        while not marker.exists():
            if first.done():await first
            assert time.monotonic()<deadline
            await asyncio.sleep(.01)
        second=asyncio.create_task(jobs.generate({**payload,'output':'after-cancel'}))
        if trigger in ('cancel','cancel_twice'):
            first.cancel()
            if trigger=='cancel_twice':
                await asyncio.sleep(.05);first.cancel()
            with pytest.raises(asyncio.CancelledError):await first
        else:
            with pytest.raises(TimeoutError):await first
        return await second
    try:
        result=asyncio.run(run())
        assert result['owned_alive_at_next_start']==[]
        assert json.loads((tmp_path/'cancelled/stage-cleanup.json').read_text())['detached_reaped']
        history=json.loads((tmp_path/'cancelled/history.json').read_text())
        assert len(history)==1 and history[0]['status']=='cancelled'
        assert json.loads((tmp_path/'cancelled/failure.json').read_text())['complete'] is False
        receipt=json.loads((tmp_path/'cancelled/process-cleanup.json').read_text())
        assert receipt['all_owned_groups_gone'] and receipt['root_reaped']
    finally:
        # These PIDs were created by this test; never scan or kill unrelated groups.
        if marker.exists():
            for pid in json.loads(marker.read_text()).values():
                try:os.kill(pid,signal.SIGKILL)
                except ProcessLookupError:pass


def test_registered_detached_group_is_closed_before_next_job_when_stage_cannot_cleanup(tmp_path,monkeypatch):
    payload,marker=make_tree(tmp_path,monkeypatch)
    stage=tmp_path/'completion.py';root=tmp_path/'root.py'
    code=stage.read_text()
    code=code.replace('install_cancellation_handler()', 'signal.signal(signal.SIGTERM,signal.SIG_IGN)')
    code=code.replace("out=Path(sys.argv[1]);child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],start_new_session=True)",
        "from smilepcb.process_ownership import register_detached_group,detached_environment\nout=Path(sys.argv[1]);child=subprocess.Popen([sys.executable,'-c','import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(30)'],start_new_session=True,env=detached_environment(os.environ))\nregister_detached_group(child.pid)")
    stage.write_text(code)
    root.write_text(root.read_text().replace('from smilepcb.stm32.generate import run_stage',
        'from smilepcb.stm32 import generate\ngenerate.STAGE_STOP_GRACE=.15\nrun_stage=generate.run_stage'))
    async def run():
        jobs=mcp_jobs.JobRunner(tmp_path);first=asyncio.create_task(jobs.generate(payload));deadline=time.monotonic()+5
        while not marker.exists():
            if first.done():await first
            assert time.monotonic()<deadline
            await asyncio.sleep(.01)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):await first
        result=await jobs.generate({**payload,'output':'after-cancel'})
        assert result['owned_alive_at_next_start']==[]
    try:
        asyncio.run(run())
        receipt=json.loads((tmp_path/'cancelled/process-cleanup.json').read_text())
        assert receipt['all_owned_groups_gone'] and receipt['registered_detached_events']>=1
        assert not (tmp_path/'cancelled/stage-cleanup.json').exists()
    finally:
        if marker.exists():
            for pid in json.loads(marker.read_text()).values():
                try:os.kill(pid,signal.SIGKILL)
                except ProcessLookupError:pass
