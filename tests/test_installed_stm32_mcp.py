"""Host-only installed generation dispatch, containment and process lifecycle."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
from smilepcb import mcp_jobs
from smilepcb.stm32 import generate

ROOT=Path(__file__).resolve().parents[1]


def options(tmp_path,output='new-board'):
    for name in ('layout.json','config.json','tools.json'):(tmp_path/name).write_text('{}')
    (tmp_path/'source').mkdir(exist_ok=True)
    return {'kle':'layout.json','config':'config.json','tools':'tools.json','output':output,'controller_source':'source',
            'process_profile':'fr4-2l-1oz-0p5-0p2','routing_quality':'turns','completion_budget_s':1200,'placement_attempts':2}


def test_generation_dispatch_uses_shared_callable_for_new_output(tmp_path,monkeypatch):
    payload=options(tmp_path);seen={}
    def shared(*args,**kwargs):seen.update(args=args,kwargs=kwargs);return {'complete':False,'scope':'dispatch fixture'}
    monkeypatch.setattr(generate,'generate_keyboard',shared)
    result=mcp_jobs.dispatch({'workspace':str(tmp_path),'operation':'generate_keyboard','generation':payload})
    assert seen['args']==tuple(tmp_path/name for name in ('layout.json','config.json','tools.json','new-board'))
    assert seen['kwargs']['controller_source']==tmp_path/'source'
    assert seen['kwargs']['completion_budget_s']==1200 and seen['kwargs']['placement_attempts']==2
    assert result['scope']=='dispatch fixture' and not (tmp_path/'new-board').exists()


@pytest.mark.parametrize('field',['kle','config','tools','output','controller_source'])
def test_generation_rejects_workspace_escape_before_dispatch(tmp_path,monkeypatch,field):
    payload=options(tmp_path);payload[field]='../outside'
    monkeypatch.setattr(generate,'generate_keyboard',lambda *a,**k:pytest.fail('unsafe generation started'))
    with pytest.raises(ValueError,match='workspace'):
        mcp_jobs.dispatch({'workspace':str(tmp_path),'operation':'generate_keyboard','generation':payload})


def test_generation_rejects_symlink_escape_and_existing_output(tmp_path,monkeypatch):
    payload=options(tmp_path);outside=tmp_path.parent/'outside-input.json';outside.write_text('{}')
    (tmp_path/'linked.json').symlink_to(outside);payload['kle']='linked.json'
    with pytest.raises(ValueError,match='workspace'):
        mcp_jobs.dispatch({'workspace':str(tmp_path),'operation':'generate_keyboard','generation':payload})
    payload['kle']='layout.json';(tmp_path/'new-board').mkdir()
    with pytest.raises(ValueError,match='new output'):
        mcp_jobs.dispatch({'workspace':str(tmp_path),'operation':'generate_keyboard','generation':payload})


def test_generation_timeout_covers_declared_stage_budgets_and_rejects_inflation(tmp_path):
    payload=options(tmp_path)
    assert mcp_jobs.generation_timeout(payload)==4140
    with pytest.raises(ValueError):mcp_jobs.generation_timeout({**payload,'completion_budget_s':999999})
    with pytest.raises(ValueError):mcp_jobs.generation_timeout({**payload,'placement_attempts':True})


def test_extended_placement_allowance_is_forwarded_and_counted_per_attempt(tmp_path):
    payload={**options(tmp_path),'placement_budget_s':1200}
    assert mcp_jobs.generation_timeout(payload)==6100
    _,kwargs=mcp_jobs.generation_arguments(tmp_path,payload)
    assert kwargs['placement_budget_s']==1200
    assert mcp_jobs.generation_timeout({**payload,'placement_attempts':8,'completion_budget_s':3600})==15820


@pytest.mark.parametrize('budget',[True,None,'220',220.0,219,1201,float('nan'),float('inf')])
def test_mcp_placement_budget_validation_is_finite_and_strict(tmp_path,budget):
    with pytest.raises(ValueError,match='Placement budget'):
        mcp_jobs.generation_timeout({**options(tmp_path),'placement_budget_s':budget})


def replace_launch(monkeypatch,script):
    original=asyncio.create_subprocess_exec
    async def launch(*argv,**kwargs):
        assert argv[-2:]==('-m','smilepcb.mcp_jobs')
        env={**os.environ,**kwargs.pop('env',{}),'PYTHONPATH':str(ROOT)}
        return await original(sys.executable,str(script),env=env,**kwargs)
    monkeypatch.setattr(mcp_jobs.asyncio,'create_subprocess_exec',launch)


def test_generation_jobs_share_the_serial_native_lock(tmp_path,monkeypatch):
    payload=options(tmp_path);events=tmp_path/'events.jsonl';script=tmp_path/'harmless.py'
    script.write_text('''import json,sys,time
from pathlib import Path
request=json.load(sys.stdin);name=request['generation']['output']
path=Path('''+repr(str(events))+''')
with path.open('a') as f:f.write(json.dumps(['start',name])+'\\n')
time.sleep(.04)
with path.open('a') as f:f.write(json.dumps(['end',name])+'\\n')
print(json.dumps({'complete':False,'host_process':True}))
''')
    replace_launch(monkeypatch,script)
    async def run():
        jobs=mcp_jobs.JobRunner(tmp_path)
        return await asyncio.gather(jobs.generate(payload),jobs.generate({**payload,'output':'second-board'}))
    assert all(r['host_process'] for r in asyncio.run(run()))
    assert [json.loads(line)[0] for line in events.read_text().splitlines()]==['start','end','start','end']


def test_cancelled_generation_reaps_detached_child_and_releases_lock(tmp_path,monkeypatch):
    payload=options(tmp_path);pidfile=tmp_path/'detached.pid';script=tmp_path/'harmless.py'
    script.write_text('''import json,sys,time,subprocess,os,signal
from pathlib import Path
from smilepcb.stm32.context import install_cancellation_handler
install_cancellation_handler()
request=json.load(sys.stdin)
if request['generation']['output']=='after-cancel':
 print(json.dumps({'complete':False,'host_process':True}));raise SystemExit(0)
child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],start_new_session=True)
Path('''+repr(str(pidfile))+''').write_text(str(child.pid))
try:time.sleep(30)
finally:
 try:os.killpg(child.pid,signal.SIGKILL)
 except ProcessLookupError:pass
 child.wait()
''')
    replace_launch(monkeypatch,script)
    async def run():
        jobs=mcp_jobs.JobRunner(tmp_path);job=asyncio.create_task(jobs.generate(payload));deadline=time.monotonic()+5
        while not pidfile.exists():
            if job.done():await job
            assert time.monotonic()<deadline
            await asyncio.sleep(.01)
        pid=int(pidfile.read_text());job.cancel()
        with pytest.raises(asyncio.CancelledError):await job
        with pytest.raises(ProcessLookupError):os.kill(pid,0)
        return await jobs.generate({**payload,'output':'after-cancel'})
    assert asyncio.run(run())['host_process']


def test_mcp_import_does_not_select_native_runtime_before_generation_configuration():
    result=subprocess.run([sys.executable,'-c',"import sys;import smilepcb.mcp_jobs;assert 'smilepcb.runtime' not in sys.modules"],cwd=ROOT,env={**os.environ,'PYTHONPATH':str(ROOT),'PYTHONDONTWRITEBYTECODE':'1'},capture_output=True,text=True)
    assert result.returncode==0,result.stderr


def test_mcp_generation_tool_is_advertised_with_explicit_paths(tmp_path):
    from smilepcb.mcp_server import build_server
    tools=asyncio.run(build_server(tmp_path).list_tools());tool=next(t for t in tools if t.name=='generate_keyboard')
    assert set(tool.inputSchema['required'])=={'kle','config','tools','output','controller_source'}
    assert tool.annotations.readOnlyHint is False and tool.annotations.idempotentHint is False
    assert tool.inputSchema['properties']['placement_budget_s']['default']==220


def test_actual_completion_adapter_installs_cleanup_signal_handler_before_tools(tmp_path):
    script=ROOT/'smilepcb/stm32/complete.py'
    code="""import runpy,sys,signal,os
sys.argv=[sys.argv[1],sys.argv[2],'routed','finished','keyboard']
try:runpy.run_path(sys.argv[0],run_name='__main__')
except ValueError:pass
try:os.kill(os.getpid(),signal.SIGTERM)
except KeyboardInterrupt:print('cleanup handler installed')
"""
    result=subprocess.run([sys.executable,'-c',code,str(script),str(tmp_path/'missing-tools.json')],cwd=tmp_path,env={**os.environ,'PYTHONPATH':str(ROOT),'PYTHONDONTWRITEBYTECODE':'1'},capture_output=True,text=True)
    assert result.returncode==0 and 'cleanup handler installed' in result.stdout,result.stderr

@pytest.mark.parametrize('policy',[None,35_000_000,20_000_000])
def test_mcp_dispatch_preserves_explicit_usb_policy_and_source_options(tmp_path,monkeypatch,policy):
    payload={**options(tmp_path),'usb_max_length_nm':policy,'coherent_via_banks':True,'unified_controller_plan':True,'component_corridors':True};seen=[]
    monkeypatch.setattr(generate,'_execute',lambda *a:seen.append(a[-1]) or {})
    mcp_jobs.dispatch({'workspace':str(tmp_path),'operation':'generate_keyboard','generation':payload})
    assert seen[-1].usb_max_length_nm==policy
    assert seen[-1].coherent_via_banks and seen[-1].unified_controller_plan and seen[-1].component_corridors


def test_mcp_advertises_legacy_usb_default_and_nullable_opt_out(tmp_path):
    from smilepcb.mcp_server import build_server
    tool=next(t for t in asyncio.run(build_server(tmp_path).list_tools()) if t.name=='generate_keyboard')
    props=tool.inputSchema['properties']
    assert props['usb_max_length_nm']['default']==20_000_000
    assert {'type':'null'} in props['usb_max_length_nm']['anyOf']
    assert props['coherent_via_banks']['default'] is False
    assert props['unified_controller_plan']['default'] is False
    assert props['component_corridors']['default'] is False
