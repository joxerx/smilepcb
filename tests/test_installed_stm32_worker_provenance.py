"""Real worker launch must use the server package, even from a shadow checkout."""
import json,os,subprocess,sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_mcp_worker_uses_server_package_from_shadowed_cwd(tmp_path):
    package_root=Path(os.environ.get('SMILEPCB_INSTALLED_TEST_ROOT',ROOT)).resolve()
    shadow=tmp_path/'shadow';(shadow/'smilepcb').mkdir(parents=True)
    (shadow/'smilepcb/__init__.py').write_text('')
    (shadow/'smilepcb/mcp_jobs.py').write_text("import sys,json;json.load(sys.stdin);print(json.dumps({'shadow_worker':True}))")
    (shadow/'relative-dependency').mkdir()
    workspace=tmp_path/'workspace';workspace.mkdir()
    for name in ('layout.json','config.json','tools.json'):(workspace/name).write_text('{}')
    (workspace/'source').mkdir()
    code="""import asyncio,json,sys,pathlib
from smilepcb import mcp_jobs
package=pathlib.Path(sys.argv[1]).resolve()
assert pathlib.Path(mcp_jobs.__file__).resolve().is_relative_to(package)
launch=[];original=asyncio.create_subprocess_exec
async def observed(*argv,**kwargs):
 launch.append({'argv':list(argv),'cwd':str(kwargs.get('cwd')),'pythonpath':kwargs.get('env',{}).get('PYTHONPATH')})
 return await original(*argv,**kwargs)
mcp_jobs.asyncio.create_subprocess_exec=observed
async def run():
 jobs=mcp_jobs.JobRunner(pathlib.Path(sys.argv[2]))
 try:
  result=await jobs.generate(dict(kle='layout.json',config='config.json',tools='tools.json',controller_source='source',output='new-board'))
 except ValueError as error:return {'expected_input_rejection':str(error),'server_module':str(pathlib.Path(mcp_jobs.__file__).resolve()),'launch':launch}
 raise AssertionError('Shadow worker executed: '+repr(result))
print(json.dumps(asyncio.run(run())))
"""
    result=subprocess.run([sys.executable,'-P','-c',code,str(package_root),str(workspace)],cwd=shadow,
        env={**os.environ,'PYTHONPATH':str(package_root)+os.pathsep+'relative-dependency','PYTHONDONTWRITEBYTECODE':'1'},capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
    receipt=json.loads(result.stdout)
    assert 'Configuration requires' in receipt['expected_input_rejection']
    assert Path(receipt['server_module']).is_relative_to(package_root)
    assert not (workspace/'new-board').exists()
    assert '-P' in receipt['launch'][0]['argv']
    assert Path(receipt['launch'][0]['cwd'])==package_root
    paths=receipt['launch'][0]['pythonpath'].split(os.pathsep)
    assert Path(paths[0])==package_root and str(shadow/'relative-dependency') in paths
