"""Host checks at the installed complete-generator boundary; never launches CAD."""
import json
from pathlib import Path
import sys

import pytest

from smilepcb.stm32 import generate

ROOT=Path(__file__).resolve().parents[1]


def inputs(tmp_path):
    kle=tmp_path/'layout.kle.json';config=tmp_path/'keyboard.json'
    fixture=ROOT/'recipes/stm32/inputs/st40-grid48'
    kle.write_bytes((fixture/'layout.kle.json').read_bytes())
    config.write_bytes((fixture/'keyboard.json').read_bytes())
    return kle,config


def test_invalid_layout_fails_before_creating_output(tmp_path):
    kle,config=inputs(tmp_path);kle.write_text('not KLE')
    with pytest.raises(ValueError):
        generate.generate_keyboard(kle,config,tmp_path/'tools.json',tmp_path/'output',controller_source=tmp_path/'source')
    assert not (tmp_path/'output').exists()


def test_unpinned_controller_fails_before_creating_output(tmp_path,monkeypatch):
    kle,config=inputs(tmp_path);source=tmp_path/'source';source.mkdir()
    (source/'stm32_hotswap_chiffre.kicad_pcb').write_text('untrusted PCB')
    monkeypatch.setattr(generate,'load_tools',lambda _: {})
    with pytest.raises(ValueError,match='controller source'):
        generate.generate_keyboard(kle,config,tmp_path/'tools.json',tmp_path/'output',controller_source=source)
    assert not (tmp_path/'output').exists()


def test_existing_output_is_never_overwritten(tmp_path):
    kle,config=inputs(tmp_path);out=tmp_path/'output';out.mkdir();(out/'keep').write_text('original')
    with pytest.raises(ValueError,match='new output'):
        generate.generate_keyboard(kle,config,tmp_path/'tools.json',out,controller_source=tmp_path/'source')
    assert (out/'keep').read_text()=='original'


def fake_pipeline(tmp_path,monkeypatch,*,complete=True):
    kle,config=inputs(tmp_path);source=tmp_path/'source';source.mkdir()
    donor=source/'stm32_hotswap_chiffre.kicad_pcb';donor.write_text('test stage input')
    paths={name:source/name for name in ('stm32_hotswap_chiffre.kicad_pcb','manifest.json','readme.md','COPYING')}
    for name,path in paths.items():
        if path!=donor:path.write_text('{}' if name=='manifest.json' else 'public test source')
    monkeypatch.setattr(generate,'validate_controller_source',lambda _:paths)
    monkeypatch.setattr(generate,'load_tools',lambda _: {'kicad_app':'not executed'})
    monkeypatch.setattr(generate,'configure_kicad',lambda *_:None)
    def libraries(project):
        (project/'KeyboardParts.pretty').mkdir()
        (project/'fp-lib-table').write_text('(fp_lib_table)')
    monkeypatch.setattr(generate,'create_libraries',libraries)
    monkeypatch.setattr(generate,'design',lambda layout,**_: {'layout':layout,'parts':[]})
    calls=[]
    def worker(script,request,log,**kwargs):
        project=Path(request['project'])
        if Path(script).name=='prepare_controller.py':
            calls.append(('prepare-controller',request,kwargs));core=project/'core.kicad_pcb';core.write_text('complete ten-part module boundary')
            descriptor=project/'core-module.json';descriptor.write_text(json.dumps({'schema':1,'source_sha256':generate.digest(core),'models':[]}))
            report=project/'preparation-report.json';report.write_text('{}')
            return dict(accepted=True,core_file=str(core),descriptor_file=str(descriptor),report_file=str(report))
        calls.append(('placement',request,kwargs))
        (project/'keyboard.kicad_pcb').write_text('native fixture boundary')
        return {'status':'placed','core_face':'B.Cu','core_pose':{'origin_nm':[0,0],'rotation_deg':0},'elapsed_s':0.1,'generation_profile':request.get('generation_profile','native')}
    monkeypatch.setattr(generate,'worker',worker)
    def stage(name,command,timeout,*,out,env,history,started):
        calls.append((name,command,timeout,env))
        if name=='extract':(out/'extracted').mkdir();(out/'extracted/core.kicad_pcb').write_text('(kicad_pcb (version 20260206) (layers (0 "F.Cu" signal) (2 "B.Cu" signal)))')
        if name=='normalize':(out/'normalized').mkdir();(out/'normalized/core.kicad_pcb').write_text('normalized')
        if name=='route':
            path=out/'routed/native-checks';path.mkdir(parents=True)
            (path/'summary.json').write_text(json.dumps({'erc':{'severity':{}},'drc':{'severity':{},'schematic_parity':0}}))
        if name=='complete':
            (out/'finished').mkdir();(out/'finished/result.json').write_text(json.dumps({'complete':complete,'generation_profile':command[command.index('--generation-profile')+1] if '--generation-profile' in command else 'native','independent':{'pad_group_gaps':0,'copper_layers':2}}))
    monkeypatch.setattr(generate,'run_stage',stage)
    return kle,config,source,calls


def test_installed_pipeline_reaches_collectors_before_compose_and_keeps_explicit_workspace(tmp_path,monkeypatch):
    kle,config,source,calls=fake_pipeline(tmp_path,monkeypatch)
    out=tmp_path/'user output'
    result=generate.generate_keyboard(kle,config,tmp_path/'tools.json',out,controller_source=source,completion_budget_s=1200)
    assert [c[0] for c in calls]==['extract','prepare-controller','placement','schematic','route','complete']
    request,options=calls[2][1:]
    assert request['prepare_ordered_controller'] and request['complete_collector_matrix']
    assert request['optimize_matrix_gpio'] and request['fanout_rows']==3
    assert request['budget_s']==220 and options['timeout_s']==240
    assert request['routing_quality']=='turns' and request['process_profile']=='fr4-2l-1oz-0p5-0p2'
    assert [c[2] for c in calls if c[0] not in ('placement','prepare-controller')]==[90,240,1800,1380]
    assert all(Path(c[3]['SMILEPCB_STM32_WORKSPACE'])==out for c in calls if c[0] not in ('placement','prepare-controller'))
    assert result['complete'] and result['fresh_from_kle'] and result['input_key_count']==48
    assert (out/'inputs/layout.kle.json').read_bytes()==kle.read_bytes()
    assert not (tmp_path/'.lab').exists()
    assert donor_bytes(source)==b'test stage input'


def test_explicit_placement_budget_changes_only_staged_worker_allowance(tmp_path,monkeypatch):
    kle,config,source,calls=fake_pipeline(tmp_path,monkeypatch)
    generate.generate_keyboard(kle,config,tmp_path/'tools.json',tmp_path/'extended',
        controller_source=source,completion_budget_s=1200,placement_budget_s=1200)
    assert calls[2][1]['budget_s']==1200 and calls[2][2]['timeout_s']==1220
    assert calls[2][1]['access_budget_s']==70
    assert [c[2] for c in calls if c[0] not in ('placement','prepare-controller')]==[90,240,1800,1380]


@pytest.mark.parametrize('budget',[True,None,'220',220.0,0,219,1201,float('nan'),float('inf')])
def test_invalid_placement_budget_rejected_before_tools_or_output(tmp_path,monkeypatch,budget):
    kle,config=inputs(tmp_path)
    monkeypatch.setattr(generate,'load_tools',lambda _:pytest.fail('invalid budget reached tools'))
    out=tmp_path/'invalid'
    with pytest.raises(ValueError,match='Placement budget'):
        generate.generate_keyboard(kle,config,tmp_path/'tools.json',out,controller_source=tmp_path/'source',placement_budget_s=budget)
    assert not out.exists()


def donor_bytes(source):return (source/'stm32_hotswap_chiffre.kicad_pcb').read_bytes()


def test_incomplete_finished_stage_never_gets_an_accepted_result(tmp_path,monkeypatch):
    kle,config,source,_=fake_pipeline(tmp_path,monkeypatch,complete=False)
    out=tmp_path/'output'
    with pytest.raises(ValueError,match='complete'):
        generate.generate_keyboard(kle,config,tmp_path/'tools.json',out,controller_source=source)
    assert not (out/'result.json').exists()
    assert json.loads((out/'failure.json').read_text())['complete'] is False


@pytest.mark.parametrize('budget',[None,1200])
def test_normal_cli_selects_shared_generator_with_explicit_inputs(tmp_path,monkeypatch,capsys,budget):
    from smilepcb import cli
    seen={}
    def build(*args,**kwargs):seen.update(args=args,kwargs=kwargs);return {'complete':False,'stage':'test dispatch only'}
    monkeypatch.setattr(generate,'generate_keyboard',build)
    paths={name:tmp_path/name for name in ('kle','config','tools','output','donor')}
    argv=['smilepcb','build']
    for name in ('kle','config','tools','output'):argv+=['--'+name,str(paths[name])]
    argv+=['--controller-source',str(paths['donor']),'--routing-quality','turns']
    if budget is not None:argv+=['--placement-budget-s',str(budget)]
    monkeypatch.setattr(sys,'argv',argv);cli.main()
    assert seen['args']==tuple(paths[n] for n in ('kle','config','tools','output'))
    assert seen['kwargs']['controller_source']==paths['donor']
    assert seen['kwargs']['placement_budget_s']==(220 if budget is None else budget)
    assert json.loads(capsys.readouterr().out)['stage']=='test dispatch only'


def test_import_does_not_bind_a_native_runtime_before_tools_are_configured():
    import os,subprocess
    probe="import sys; import smilepcb.stm32.generate; assert 'smilepcb.runtime' not in sys.modules"
    result=subprocess.run([sys.executable,'-c',probe],cwd=ROOT,env={**os.environ,'PYTHONPATH':str(ROOT),'PYTHONDONTWRITEBYTECODE':'1'},capture_output=True,text=True)
    assert result.returncode==0,result.stderr


def test_changed_staged_layout_cannot_publish_accepted_result(tmp_path,monkeypatch):
    kle,config,source,_=fake_pipeline(tmp_path,monkeypatch)
    stage=generate.run_stage
    def changed(name,*args,**kwargs):
        stage(name,*args,**kwargs)
        if name=='complete':
            path=kwargs['out']/'inputs/layout.kle.json'
            path.write_bytes(path.read_bytes()+b'\n')
    monkeypatch.setattr(generate,'run_stage',changed)
    with pytest.raises(ValueError,match='Saved layout inputs'):
        generate.generate_keyboard(kle,config,tmp_path/'tools.json',tmp_path/'output',controller_source=source)
    assert not (tmp_path/'output/result.json').exists()


def test_source_wrapper_preserves_selected_fixture_subdirectory(tmp_path,monkeypatch,capsys):
    _,_,source,_=fake_pipeline(tmp_path,monkeypatch)
    generate.main([str(tmp_path/'tools.json'),'legacy-run','--fixture','ansi61','--native-staged',
                   '--process-profile','fr4-2l-1oz-0p5-0p2','--controller-source',str(source)],
                  compatibility_workspace=tmp_path/'legacy-workspace')
    assert (tmp_path/'legacy-workspace/legacy-run/layouts/ansi61/project').is_dir()


def test_rejected_matrix_reports_placement_and_never_starts_composition(tmp_path,monkeypatch):
    kle,config,source,calls=fake_pipeline(tmp_path,monkeypatch)
    old_stage=generate.run_stage
    def stage(name,*args,**kwargs):
        result=old_stage(name,*args,**kwargs)
        (kwargs['out']/'progress.json').write_text(json.dumps({'stage':name}))
        return result
    monkeypatch.setattr(generate,'run_stage',stage)
    original_worker=generate.worker
    def rejected(script,request,log,**kwargs):
        if Path(script).name=='prepare_controller.py':return original_worker(script,request,log,**kwargs)
        return {'status':'access-rejected','core_face':'B.Cu','core_pose':None,'elapsed_s':0.1,'generation_profile':request.get('generation_profile','native')}
    monkeypatch.setattr(generate,'worker',rejected)
    out=tmp_path/'rejected'
    with pytest.raises(ValueError,match='No candidate'):
        generate.generate_keyboard(kle,config,tmp_path/'tools.json',out,controller_source=source,placement_attempts=1)
    assert [q[0] for q in calls]==['extract','prepare-controller']
    failure=json.loads((out/'failure.json').read_text())
    assert failure['stage']=='placement'
    progress=json.loads((out/'progress.json').read_text())
    assert progress['attempt']==0 and progress['face']=='B.Cu'


def test_process_v2_normal_coordinator_selects_matching_placement_and_completion(tmp_path,monkeypatch):
    kle,config,source,calls=fake_pipeline(tmp_path,monkeypatch)
    result=generate.generate_keyboard(kle,config,tmp_path/'tools.json',tmp_path/'output',controller_source=source)
    placement=next(c for c in calls if c[0]=='placement')[1]
    command=next(c for c in calls if c[0]=='complete')[1]
    assert placement['generation_profile']=='dsn-process-v2'
    assert command[command.index('--generation-profile')+1]=='dsn-process-v2'
    assert result['generation_profile']=='dsn-process-v2'


def test_process_v2_coordinator_rejects_mislabeled_placement_before_composition(tmp_path,monkeypatch):
    kle,config,source,calls=fake_pipeline(tmp_path,monkeypatch)
    worker=generate.worker
    def wrong(*args,**kwargs):return {**worker(*args,**kwargs),'generation_profile':'dsn-safe-v1'}
    monkeypatch.setattr(generate,'worker',wrong)
    with pytest.raises(ValueError,match='generation profile'):
        generate.generate_keyboard(kle,config,tmp_path/'tools.json',tmp_path/'output',controller_source=source)
    assert not any(c[0] in ('schematic','route','complete') for c in calls)


def test_process_v2_coordinator_never_relabels_wrong_finished_stage(tmp_path,monkeypatch):
    kle,config,source,calls=fake_pipeline(tmp_path,monkeypatch);original=generate.run_stage
    def wrong(name,*args,**kwargs):
        original(name,*args,**kwargs)
        if name=='complete':
            path=kwargs['out']/'finished/result.json';data=json.loads(path.read_text());data['generation_profile']='native';path.write_text(json.dumps(data))
    monkeypatch.setattr(generate,'run_stage',wrong)
    with pytest.raises(ValueError,match='generation profile'):
        generate.generate_keyboard(kle,config,tmp_path/'tools.json',tmp_path/'output',controller_source=source)
    assert not (tmp_path/'output/result.json').exists()


def test_nonstaged_compatibility_keeps_eighty_second_placement(tmp_path,monkeypatch):
    kle,config,source,calls=fake_pipeline(tmp_path,monkeypatch)
    out=tmp_path/'compat'
    with pytest.raises(SystemExit) as error:
        generate.main([str(tmp_path/'tools.json'),'compat','--kle',str(kle),'--config',str(config),
            '--controller-source',str(source),'--output',str(out),'--stop-after-placement'])
    assert error.value.code==0
    assert calls[2][1]['budget_s']==80 and calls[2][2]['timeout_s']==90
    assert not calls[2][1]['prepare_ordered_controller']


def test_nonstaged_compatibility_rejects_ignored_budget_option(tmp_path,monkeypatch):
    monkeypatch.setattr(generate,'_execute',lambda *a:pytest.fail('unsupported option reached work'))
    with pytest.raises(SystemExit) as error:
        generate.main(['tools','tag','--placement-budget-s','1200'])
    assert error.value.code==2


def test_native_stage_prepares_once_before_repeated_poses_and_reuses_same_module(tmp_path,monkeypatch):
    kle,config,source,calls=fake_pipeline(tmp_path,monkeypatch);original=generate.worker;poses=[];design=generate.design;models=[]
    def capture_design(*a,**kw):models.append(1);return design(*a,**kw)
    monkeypatch.setattr(generate,'design',capture_design)
    def trial(script,payload,*a,**kw):
        result=original(script,payload,*a,**kw)
        if Path(script).name=='place.py':
            poses.append(payload)
            if len(poses)==1:result['status']='access-rejected'
        return result
    monkeypatch.setattr(generate,'worker',trial)
    generate.generate_keyboard(kle,config,tmp_path/'tools.json',tmp_path/'once',controller_source=source,placement_attempts=2)
    assert [c[0] for c in calls].count('prepare-controller')==1 and len(poses)==2
    assert len(models)==1 and not any(c[0]=='normalize' for c in calls)
    assert poses[0]['core']==poses[1]['core'] and poses[0]['core_module']==poses[1]['core_module']


def test_preparation_failure_stops_before_any_placement_and_keeps_report(tmp_path,monkeypatch):
    kle,config,source,calls=fake_pipeline(tmp_path,monkeypatch);original=generate.worker
    def failed(script,*a,**kw):
        result=original(script,*a,**kw)
        return dict(result,accepted=False,reason='required local pad remains open') if Path(script).name=='prepare_controller.py' else result
    monkeypatch.setattr(generate,'worker',failed)
    with pytest.raises(ValueError,match='Controller module preparation'):generate.generate_keyboard(kle,config,tmp_path/'tools.json',tmp_path/'failed-module',controller_source=source)
    assert not any(c[0]=='placement' for c in calls)
    assert (tmp_path/'failed-module/controller-module/project/preparation-report.json').is_file()


def test_controller_preparation_seed_is_paired_before_native_worker_entry(tmp_path,monkeypatch):
    kle,config,source,calls=fake_pipeline(tmp_path,monkeypatch);original=generate.worker;seen=[]
    def checked(script,request,*a,**kw):
        if Path(script).name=='prepare_controller.py':
            project=Path(request['project']);seed=project/'keyboard.kicad_pcb'
            assert seed.is_file(),'Saved empty seed must use the existing target project basename'
            assert seed.with_suffix('.kicad_pro').is_file()
            assert not (project/'empty.kicad_pcb').exists()
            seen.append(seed)
        return original(script,request,*a,**kw)
    monkeypatch.setattr(generate,'worker',checked)
    generate.generate_keyboard(kle,config,tmp_path/'tools.json',tmp_path/'paired-seed',controller_source=source)
    assert len(seen)==1 and donor_bytes(source)==b'test stage input'
