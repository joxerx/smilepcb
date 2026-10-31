"""Exercise actual routing scheduling across host and native completion stages."""
import ast,json,os,subprocess,time
from pathlib import Path
import pytest
import sexpdata

ROOT=Path(__file__).resolve().parents[1]

def test_host_completion_does_not_retry_the_same_net_after_recording_its_attempt(tmp_path):
    source=ROOT/'recipes/stm32/complete.py'
    tree=ast.parse(source.read_text());call=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='call')
    def worker(_bridge,request,_log,**kwargs):
        counts=dict(request.get('attempt_counts',{}));net=min(('hard','other'),key=lambda n:(counts.get(n,0),n));counts[net]=counts.get(net,0)+1
        return {'accepted':False,'selected':net,'connections':0,'pad_group_gaps':2,'graph':{'attempt_counts':counts}}
    env={'worker':worker,'BRIDGE':Path('native'),'board':tmp_path/'keyboard.kicad_pcb','quality':'turns','counts':{},'history':[],'out':tmp_path,'deadline':time.perf_counter()+120,'time':time,'json':json}
    exec(compile(ast.Module(body=[call],type_ignores=[]),str(source),'exec'),env)
    selected=[env['call']('complete',tag='trial-'+str(i))['selected'] for i in range(2)]
    assert selected==['hard','other']

@pytest.fixture(scope='module')
def native_report(tmp_path_factory):
    interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not interpreter:pytest.skip('Set SMILEPCB_KICAD_PYTHON for native completion regression')
    out=tmp_path_factory.mktemp('completion-native')
    run=subprocess.run([interpreter,str(Path(__file__).with_name('native_completion_fairness_probe.py')),str(out)],cwd=ROOT,env={**os.environ,'PYTHONPATH':os.pathsep.join((str(ROOT),str(Path(sexpdata.__file__).parent)))},capture_output=True,text=True,timeout=90)
    assert run.returncode==0,run.stdout+'\n'+run.stderr
    return json.loads((out/'report.json').read_text())

def test_native_repair_spends_its_first_attempts_on_less_tried_nets(native_report):
    assert native_report['fair']['order'][-1]=='C'
    assert set(native_report['fair']['order'][:2])=={'A','B'}
    assert native_report['fair']['counts']=={'A':1,'B':1,'C':3}
    assert native_report['fair']['after']=={}

def test_native_repair_prefers_available_layer_bypass_over_cutting_completed_net(native_report):
    assert native_report['weight']['after']=={}
    assert native_report['weight']['connected']==1
    assert native_report['weight']['removed']==0


def test_legacy_retains_its_previous_negotiation_preference(native_report):
    """Characterize the prior policy separately from the opt-in improvement."""
    assert native_report['weight_legacy']['after']=={}
    assert native_report['weight_legacy']['connected']==1
    assert native_report['weight_legacy']['removed']==1


def test_disposable_exploration_can_reconnect_displaced_copper(native_report):
    result=native_report['weight_exploratory']
    assert result.get('supported'),result
    assert result['connected']==1 and result['after']=={}
    assert result['removed']==1
    assert native_report['weight']['removed']==0


def test_exploratory_mode_rejects_non_boolean_inputs(native_report):
    assert native_report['invalid_mode_rejected']
