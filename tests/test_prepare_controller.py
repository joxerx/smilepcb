"""Host contracts for package-owned once-per-run controller preparation."""
from copy import deepcopy
import importlib
import importlib.util
import json
from pathlib import Path
import pytest
from test_core_module_import import fixture


def package():
    assert importlib.util.find_spec('smilepcb.stm32.prepare_controller') is not None,'package-owned preparation worker is missing'
    return importlib.import_module('smilepcb.stm32.prepare_controller')


def raw():
    cfg,descriptor,source=fixture();cfg.update(layout=dict(keys=[{'id':1}],rgb=True,rgb_order=[1],size_mm=[200,100]),outline=[[0,0],[200,0],[200,100],[0,100]])
    return cfg,descriptor,source


def test_preparation_model_keeps_exact_target_supports_and_raw_exclusions():
    cfg,_,_=raw();next(p for p in cfg['parts'] if p['ref']=='U_MCU')['no_connect']=['2'];before=deepcopy(cfg)
    model=package().preparation_model(cfg)
    assert {p['ref'] for p in model['parts']}==package().COMPILED_REPLACEMENTS
    assert model['layout']['keys']==[] and not model['layout']['rgb']
    for ref in ('U_MCU','R_BOOT','R_NRST'):
        assert next(p for p in model['parts'] if p['ref']==ref)==next(p for p in cfg['parts'] if p['ref']==ref)
    assert cfg==before


@pytest.mark.parametrize('change',[lambda cfg:cfg['parts'].append(deepcopy(next(p for p in cfg['parts'] if p['ref']=='R_BOOT'))),lambda cfg:cfg['parts'].__setitem__(slice(None),[p for p in cfg['parts'] if p['ref']!='R_NRST']),lambda cfg:cfg['parts'].append(dict(ref='C_CORE1'))])
def test_missing_or_duplicate_support_fails_before_native_preparation(change):
    cfg,_,_=raw();change(cfg)
    with pytest.raises(ValueError):package().preparation_model(cfg)


def test_compiler_descriptor_reuses_strict_raw_import_binding():
    cfg,descriptor,source=raw();models=[r['model'] for r in descriptor['models']]
    result=package().controller_descriptor(cfg,models,source,'a'*64,{'accepted':True})
    assert result['source_sha256']=='a'*64 and len(result['models'])==10
    next(p for p in models if p['ref']=='U_MCU')['no_connect']=['1']
    with pytest.raises(ValueError):package().controller_descriptor(cfg,models,source,'a'*64,{'accepted':True})


def test_source_branch_preservation_allows_merges_and_fresh_pad_mapping():
    before={'+3V3':[['a','b'],['c']],'GND':[['d','e']],'NRST':[['f','g']]}
    mapping={x:'new-'+x for x in 'abcdefg'}
    after={'+3V3':[['new-a','new-b','new-c']],'GND':[['new-d','new-e']],'NRST':[['new-f','new-g']]}
    assert package().preserved_branches(before,after,mapping)['accepted']
    after['+3V3']=[['new-a','new-c'],['new-b']]
    with pytest.raises(ValueError):package().preserved_branches(before,after,mapping)


def test_saved_empty_seed_has_only_source_headers_and_leaves_source_bytes_exact(tmp_path):
    source=tmp_path/'source.kicad_pcb';source.write_text('(kicad_pcb (version 20260206) (generator "pcbnew") (layers (0 "F.Cu" signal) (2 "B.Cu" signal) (25 "Edge.Cuts" user)) (setup (stackup ignored)) (footprint "part") (segment (net "GND")) (zone (net "GND")))')
    before=source.read_bytes();seed=tmp_path/'empty.kicad_pcb';package().write_empty_seed(source,seed)
    import sexpdata
    tree=sexpdata.loads(seed.read_text());tags={str(q[0]) for q in tree[1:]}
    assert 'layers' in tags and not tags&{'footprint','segment','zone','setup'} and source.read_bytes()==before


def test_worker_is_package_owned_without_research_or_runpy_dependency():
    path=Path(__file__).resolve().parents[1]/'smilepcb/stm32/prepare_controller.py'
    assert path.is_file(),'package worker is missing'
    text=path.read_text();assert '.research' not in text and 'runpy' not in text and 'reroute-core' not in text


def test_input_rejection_retains_package_report_and_leaves_source_bytes(tmp_path,monkeypatch,capsys):
    import io,sys
    cfg,_,_=raw();cfg['parts']=[p for p in cfg['parts'] if p['ref']!='R_BOOT']
    (tmp_path/'model-before-placement.json').write_text(json.dumps(cfg));(tmp_path/'keyboard.kicad_pro').write_text('{}')
    source=tmp_path/'source.kicad_pcb';source.write_text('original read-only source');before=source.read_bytes()
    monkeypatch.setattr(sys,'stdin',io.StringIO(json.dumps(dict(project=str(tmp_path),source_core=str(source),process_profile='fr4-2l-1oz-0p5-0p2',generation_profile='dsn-process-v2',budget_s=90))))
    package().main();result=json.loads(capsys.readouterr().out)
    assert result['accepted'] is False and Path(result['report_file']).is_file() and source.read_bytes()==before


def test_native_preparation_uses_host_saved_seed_and_no_host_worker_dependencies():
    import ast
    path=Path(__file__).resolve().parents[1]/'smilepcb/stm32/prepare_controller.py';tree=ast.parse(path.read_text())
    native=next(q for q in tree.body if isinstance(q,ast.FunctionDef) and q.name=='prepare')
    imported=[q.module for q in ast.walk(native) if isinstance(q,ast.ImportFrom)]
    assert 'smilepcb.lab_tools' not in imported
    assert not any(isinstance(q,ast.Call) and isinstance(q.func,ast.Name) and q.func.id=='write_empty_seed' for q in ast.walk(native))
    assert any(isinstance(q,ast.Call) and isinstance(q.func,ast.Attribute) and q.func.attr=='run' and isinstance(q.func.value,ast.Name) and q.func.value.id=='subprocess' for q in ast.walk(native))


def test_native_seed_load_has_the_target_project_at_its_own_basename(tmp_path):
    """Execute the actual preparation prefix through its first Native load."""
    import ast
    module=package();cfg,_,_=raw()
    project=tmp_path/'module';project.mkdir()
    (project/'model-before-placement.json').write_text(json.dumps(cfg))
    pro=project/'keyboard.kicad_pro';pro.write_text('{"target-process":"exact"}')
    # Leave the former unpaired seed present to expose a stale loader choice.
    (project/'empty.kicad_pcb').write_text('empty saved seed')
    (project/'keyboard.kicad_pcb').write_text('empty saved seed')
    source=tmp_path/'donor.kicad_pcb';source.write_text('read-only donor')
    seen=[]
    class Manager:
        def LoadProject(self,path):seen.append(Path(path));return True
        def GetProject(self,path):assert Path(path)==pro;return object()
    class Native:
        def GetSettingsManager(self):return Manager()
        def LoadBoard(self,path):
            path=Path(path)
            assert path.with_suffix('.kicad_pro').is_file(),'Native cold load has no same-basename target project'
            assert path.with_suffix('.kicad_pro')==seen[-1]
            assert path.with_suffix('.kicad_pro').read_bytes()==pro.read_bytes()
            return object()
    tree=ast.parse(Path(module.__file__).read_text());fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='prepare')
    prefix=fn.body[:next(i for i,n in enumerate(fn.body) if isinstance(n,ast.ImportFrom))]
    check=next(n for n in fn.body if isinstance(n,ast.FunctionDef) and n.name=='check')
    attempt=next(n for n in fn.body if isinstance(n,ast.Try))
    first_load=next(i for i,n in enumerate(attempt.body) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='empty' for t in n.targets))
    fn.body=prefix+[check]+attempt.body[:first_load+1]+[ast.Return(value=ast.Name(id='seed',ctx=ast.Load()))]
    namespace={**module.__dict__,'p':Native()};exec(compile(ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[])),module.__file__,'exec'),namespace)
    assert namespace['prepare'](dict(project=str(project),source_core=str(source),budget_s=90))==project/'keyboard.kicad_pcb'
    assert source.read_text()=='read-only donor' and pro.read_text()=='{"target-process":"exact"}'


def test_missing_paired_seed_project_rejects_before_importing_native(tmp_path):
    cfg,_,_=raw();(tmp_path/'model-before-placement.json').write_text(json.dumps(cfg))
    (tmp_path/'keyboard.kicad_pcb').write_text('empty saved seed')
    source=tmp_path/'donor.kicad_pcb';source.write_text('read-only donor')
    with pytest.raises(ValueError,match='paired target project'):
        package().prepare(dict(project=str(tmp_path),source_core=str(source),budget_s=90))
    assert source.read_text()=='read-only donor'
