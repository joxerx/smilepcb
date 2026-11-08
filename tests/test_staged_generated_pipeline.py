"""Exercise rollback/seed provenance at the staged-driver boundary, without CAD."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from smilepcb.keyboard_process import project_settings, COMPACT_VIAS

ROOT = Path(__file__).resolve().parents[1]
PLACED = "(kicad_pcb (version 20241229)) ;placed only"


def setup_driver(tmp_path, monkeypatch, *, native_gaps=0, reject_seed=False):
    spec = importlib.util.spec_from_file_location("native_staged_driver", ROOT / "smilepcb/stm32/route.py")
    driver = importlib.util.module_from_spec(spec); spec.loader.exec_module(driver)
    base = tmp_path / ".lab/public-controller"; source = base / "input/fixture/project"
    monkeypatch.setenv('SMILEPCB_STM32_WORKSPACE',str(base))
    source.mkdir(parents=True)
    cfg = {"layout": {"rgb_order": [2, 1]}, "plane_nets": [],"routing_preparation":{"mode":"ordered-banks-v2","generation_profile":"dsn-process-v2"}}
    for name, value in {"keyboard.kicad_pcb": PLACED, "keyboard.kicad_pro": json.dumps(project_settings(COMPACT_VIAS)),
                        "layout.kle.json": "[]", ".smilepcb-keyboard.json": json.dumps(cfg)}.items():
        (source / name).write_text(value)
    monkeypatch.setattr(driver, "load_tools", lambda _: {})
    monkeypatch.setattr(driver, "configure_kicad", lambda *_: None)
    monkeypatch.setattr(driver, "ordered_nets", lambda _: [("control", ["ctl"]), ("rows", ["r"]), ("columns", ["c"]), ("rgb", ["b", "a"])])
    monkeypatch.setattr(sys, "argv", ["script", "tools.json", "output", "input", "fixture"])
    (source/".smilepcb-interfaces.json").write_text('{"fixture":"ordered source boundary"}')
    calls = []
    checks = {"erc": {"severity": {}}, "drc": {"severity": {}, "schematic_parity": 0}}

    def worker(script, request, log, **kwargs):
        op = request["operation"]; calls.append((op, request))
        board = Path(request["board"])
        if op == "inspect":
            return {"pad_partitions":[["p","q","r"]] if "|global" in board.read_text() else [["p","q"],["r"]],"generation_profile":request.get('generation_profile','native')}
        if op in ("complete", "joint_repair"):
            return {"accepted":False,"pad_group_gaps":native_gaps,"pad_groups_missing":{"ctl":native_gaps},"generation_profile":request.get("generation_profile","native")}
        if op not in ("verify",) and not reject_seed:
            board.write_text(board.read_text() + "|" + op)
        if op == 'matrix_field':
            (board.parent/'.smilepcb-field.json').write_text(json.dumps({'fixture':'matrix boundary','scope':'matrix-with-controller','generation_profile':request.get('generation_profile','native')}))
        if op == "verify":
            return {"pad_group_gaps": native_gaps,"pad_groups_missing":{"ctl":native_gaps} if native_gaps else {},"generation_profile":request.get("generation_profile","native")}
        result={"accepted": not reject_seed, "controller_connected": True, "connections": 1,
                "generation_profile":request.get('generation_profile','native')}
        if op=='field_verify':
            field=json.loads((board.parent/'.smilepcb-field.json').read_text())
            result.update(scope=field.get('scope'),field_generation_profile=field.get('generation_profile'))
        return result

    def run(command, **kwargs):
        calls.append(("global", command))
        seed = base / "output/prepared-seed/project/keyboard.kicad_pcb"
        assert "|components" not in seed.read_text()
        target = base / "output/generated-global"
        import shutil
        shutil.copytree(seed.parent, target / "project")
        (target / "project/keyboard.kicad_pcb").write_text(seed.read_text() + "|global")
        (target / "native-checks").mkdir()
        (target / "native-checks/summary.json").write_text(json.dumps(checks))
        (target / "independent.json").write_text(json.dumps({"pad_group_gaps": 1}))

    from smilepcb import keyboard_build
    monkeypatch.setattr(keyboard_build,"BRIDGE",Path("native"))
    lab = SimpleNamespace(worker=worker, rule_checks=lambda *_a, **_k: checks)
    monkeypatch.setitem(sys.modules, "smilepcb.lab_tools", lab)
    import smilepcb
    monkeypatch.setattr(smilepcb, "lab_tools", lab, raising=False)
    monkeypatch.setitem(sys.modules, "smilepcb.runtime", SimpleNamespace(environment=lambda: {}))
    monkeypatch.setattr(driver.subprocess, "run", run)
    return driver, base, source, calls


def test_successful_native_path_retains_receipted_seed_and_never_claims_complete(tmp_path, monkeypatch):
    driver, base, source, calls = setup_driver(tmp_path, monkeypatch)
    driver.main()
    result = json.loads((base / "output/result.json").read_text())
    assert result["complete"] is False
    assert result["selected_path"] == "ordered-native"
    assert source.joinpath("keyboard.kicad_pcb").read_text() == PLACED
    assert not any(name == "global" for name, _ in calls)
    assert [req["include_nets"] for op, req in calls if op == "components"] == [["ctl"], ["ctl"], ["b", "a"], ["b", "a"]]
    from smilepcb.keyboard_routing.generated_seed import validate_manifest
    assert validate_manifest(base / "output/prepared-seed/project")["completed_keyboard"] is False


def test_stalled_native_path_uses_only_immutable_seed_and_archives_attempt(tmp_path, monkeypatch):
    driver, base, source, calls = setup_driver(tmp_path, monkeypatch, native_gaps=7)
    driver.main()
    out = base / "output"
    assert "|components" in (out / "native-attempt/project/keyboard.kicad_pcb").read_text()
    assert (out / "project/keyboard.kicad_pcb").read_text().endswith("|global")
    assert json.loads((out / "result.json").read_text())["complete"] is False
    assert len([op for op, _ in calls if op == "global"]) == 1
    assert source.joinpath("keyboard.kicad_pcb").read_text() == PLACED


def test_failed_joint_seed_does_not_start_signals_or_global_router(tmp_path, monkeypatch):
    driver, base, source, calls = setup_driver(tmp_path, monkeypatch, reject_seed=True)
    with pytest.raises(ValueError, match="Joint seed preparation"):
        driver.main()
    assert not any(op in ("components", "global") for op, _ in calls)
    assert not (base / "output/prepared-seed").exists()


@pytest.mark.parametrize('gaps',[0,7])
def test_selected_quality_reaches_every_native_call_and_global_recovery(tmp_path,monkeypatch,gaps):
    driver,base,source,calls=setup_driver(tmp_path,monkeypatch,native_gaps=gaps)
    monkeypatch.setattr(sys,'argv',[*sys.argv,'--routing-quality','turns'])
    driver.main()
    native=[request for op,request in calls if op!='global']
    assert native and all(request['routing_quality']=='turns' for request in native)
    assert all(request.get('generation_profile')=='dsn-process-v2' for request in native)
    for op,command in calls:
        if op=='global':assert command[-2:]==['--routing-quality','turns']
    assert json.loads((base/'output/result.json').read_text())['routing_quality']=='turns'


def test_invalid_quality_is_rejected_before_starting_native_work(tmp_path,monkeypatch):
    driver,base,source,calls=setup_driver(tmp_path,monkeypatch)
    monkeypatch.setattr(sys,'argv',[*sys.argv,'--routing-quality','bogus'])
    with pytest.raises(ValueError,match='routing quality'):driver.main()
    assert calls==[] and not (base/'output').exists()


@pytest.mark.parametrize('failure',['timeout','nonzero'])
def test_failed_global_retains_physically_checked_native_progress(tmp_path,monkeypatch,failure):
    """A bounded external failure must not discard the usable native board."""
    import subprocess
    driver,base,source,calls=setup_driver(tmp_path,monkeypatch,native_gaps=7)
    def fail(command,**kwargs):
        if failure=='timeout':raise subprocess.TimeoutExpired(command,1000)
        raise subprocess.CalledProcessError(1,command)
    monkeypatch.setattr(driver.subprocess,'run',fail)
    driver.main()
    out=base/'output'
    actual=(out/'project/keyboard.kicad_pcb').read_bytes()
    assert actual==(out/'native-attempt/project/keyboard.kicad_pcb').read_bytes()
    assert b'|components' in actual and b'|global' not in actual
    result=json.loads((out/'result.json').read_text())
    assert result['complete'] is False
    assert result['independent']['pad_group_gaps']==7
    assert result['selected_path']=='ordered-native-after-global-failure'
    assert result['global_attempt_error']
    assert (out/'native-checks/summary.json').is_file()
    assert (source/'keyboard.kicad_pcb').read_text()==PLACED


def test_failed_global_does_not_restore_physically_invalid_native(tmp_path,monkeypatch):
    """An archived snapshot is not eligible merely because its file exists."""
    import subprocess
    driver,base,source,calls=setup_driver(tmp_path,monkeypatch,native_gaps=7)
    import smilepcb.lab_tools as lab_tools
    monkeypatch.setattr(lab_tools,'rule_checks',lambda *_a,**_k:{'erc':{'severity':{}},'drc':{'severity':{'error':1},'schematic_parity':0}})
    def fail(command,**kwargs):raise subprocess.CalledProcessError(1,command)
    monkeypatch.setattr(driver.subprocess,'run',fail)
    with pytest.raises(subprocess.CalledProcessError):driver.main()
    assert not (base/'output/result.json').exists()
    assert not (base/'output/project').exists()
    assert (source/'keyboard.kicad_pcb').read_text()==PLACED


def test_matrix_is_built_before_seed_and_survives_global_fallback(tmp_path,monkeypatch):
    driver,base,source,calls=setup_driver(tmp_path,monkeypatch,native_gaps=7)
    driver.main()
    ops=[op for op,_ in calls]
    assert ops.count('matrix_field')==1
    assert next(req for op,req in calls if op=='matrix_field')['include_controller'] is True
    assert ops.index('matrix_field')<ops.index('components')
    assert '|matrix_field' in (base/'output/prepared-seed/project/keyboard.kicad_pcb').read_text()
    assert '|matrix_field' in (base/'output/project/keyboard.kicad_pcb').read_text()
    assert (base/'output/project/.smilepcb-field.json').is_file()


def test_incomplete_matrix_does_not_start_signals_or_fallback(tmp_path,monkeypatch):
    driver,base,source,calls=setup_driver(tmp_path,monkeypatch)
    import smilepcb.lab_tools as lab
    native=lab.worker
    def reject(script,request,log,**kwargs):
        result=native(script,request,log,**kwargs)
        if request['operation']=='matrix_field':result['accepted']=False
        return result
    monkeypatch.setattr(lab,'worker',reject)
    with pytest.raises(ValueError,match='matrix'):driver.main()
    assert not any(op in ('components','global') for op,_ in calls)


def test_new_seed_receipt_binds_the_matrix_contract(tmp_path,monkeypatch):
    driver,base,source,calls=setup_driver(tmp_path,monkeypatch)
    driver.main()
    from smilepcb.keyboard_routing.generated_seed import write_manifest,validate_manifest
    project=base/'output/prepared-seed/project';marker=project/'.smilepcb-field.json'
    marker.write_text('{"proof": "first"}')
    write_manifest(project,generation_profile='dsn-safe-v1')
    marker.write_text('{"proof": "changed"}')
    with pytest.raises(ValueError):validate_manifest(project)


def test_global_fallback_cannot_split_existing_native_pad_contacts(tmp_path,monkeypatch):
    driver,base,source,calls=setup_driver(tmp_path,monkeypatch,native_gaps=7)
    import smilepcb.lab_tools as lab
    native=lab.worker
    def inspect(script,request,log,**kwargs):
        if request['operation']=='inspect':
            global_board='|global' in Path(request['board']).read_text()
            return {'pad_partitions':[['p'],['q','r']] if global_board else [['p','q'],['r']],'generation_profile':request.get('generation_profile','native')}
        return native(script,request,log,**kwargs)
    monkeypatch.setattr(lab,'worker',inspect)
    driver.main()
    out=base/'output';result=json.loads((out/'result.json').read_text())
    assert result['selected_path']=='ordered-native-after-global-regression'
    assert (out/'project/keyboard.kicad_pcb').read_bytes()==(out/'native-attempt/project/keyboard.kicad_pcb').read_bytes()


@pytest.mark.parametrize("proof", [False, None])
def test_accepted_field_without_mcu_proof_cannot_become_prepared_seed(tmp_path, monkeypatch, proof):
    driver, base, source, calls = setup_driver(tmp_path, monkeypatch)
    import smilepcb.lab_tools as lab
    native = lab.worker
    def field_only(script, request, log, **kwargs):
        result = native(script, request, log, **kwargs)
        if request["operation"] == "matrix_field":
            if proof is None: result.pop("controller_connected", None)
            else: result["controller_connected"] = proof
        return result
    monkeypatch.setattr(lab, "worker", field_only)
    with pytest.raises(ValueError, match="matrix"): driver.main()
    assert not any(op in ("components", "global") for op, _ in calls)
    assert not (base / "output/prepared-seed").exists()


@pytest.mark.parametrize('change',['missing','changed'])
def test_host_seed_inventory_binds_required_ordered_interfaces_without_loading_kicad(tmp_path,monkeypatch,change):
    driver,base,source,calls=setup_driver(tmp_path,monkeypatch)
    driver.main()
    from smilepcb.keyboard_routing.generated_seed import write_manifest,validate_manifest
    project=base/'output/prepared-seed/project';model=project/'.smilepcb-keyboard.json';cfg=json.loads(model.read_text());cfg['routing_preparation']={'mode':'ordered-banks-v1'};model.write_text(json.dumps(cfg))
    marker=project/'.smilepcb-interfaces.json';marker.write_text('{"schema":1,"native_proof":"first"}')
    write_manifest(project,generation_profile='dsn-safe-v1')
    if change=='missing':marker.unlink()
    else:marker.write_text('{"schema":1,"native_proof":"changed"}')
    with pytest.raises(ValueError):validate_manifest(project)


def test_precomposed_full_matrix_is_reverified_and_reused_by_every_later_phase(tmp_path,monkeypatch):
    driver,base,source,calls=setup_driver(tmp_path,monkeypatch)
    cfg=json.loads((source/'.smilepcb-keyboard.json').read_text());cfg['routing_preparation']={'mode':'ordered-banks-v2','matrix':'collectors-v1','generation_profile':'dsn-process-v2'}
    (source/'.smilepcb-keyboard.json').write_text(json.dumps(cfg))
    marker='{"scope":"matrix-with-controller","generation_profile":"dsn-process-v2","fixture":"precompose"}'
    (source/'.smilepcb-field.json').write_text(marker)
    (source/'.smilepcb-interfaces.json').write_text('{"fixture":"source ports"}')
    import smilepcb.lab_tools as lab
    original=lab.worker
    def verified(script,request,log,**kwargs):
        result=original(script,request,log,**kwargs)
        if request['operation']=='field_verify':result.update(scope='matrix-with-controller',controller_connected=True,field_generation_profile=json.loads((Path(request['board']).parent/'.smilepcb-field.json').read_text()).get('generation_profile'))
        return result
    monkeypatch.setattr(lab,'worker',verified)
    driver.main()
    assert not any(op=='matrix_field' for op,_ in calls)
    assert sum(op=='field_verify' for op,_ in calls)==1
    assert (base/'output/prepared-seed/project/.smilepcb-field.json').read_text()==marker
    assert (base/'output/project/.smilepcb-field.json').read_text()==marker


def test_process_v2_route_workers_and_seed_report_the_actual_policy(tmp_path,monkeypatch):
    driver,base,source,calls=setup_driver(tmp_path,monkeypatch)
    driver.main()
    native=[request for op,request in calls if op!='global']
    assert native and all(request['generation_profile']=='dsn-process-v2' for request in native)
    from smilepcb.keyboard_routing.generated_seed import validate_manifest
    assert validate_manifest(base/'output/prepared-seed/project')['generation_profile']=='dsn-process-v2'
    assert json.loads((base/'output/result.json').read_text())['generation_profile']=='dsn-process-v2'


def test_process_v2_seed_cannot_relabel_real_v1_proofs(tmp_path,monkeypatch):
    driver,base,source,calls=setup_driver(tmp_path,monkeypatch)
    driver.PROFILE='dsn-safe-v1';driver.main()
    from smilepcb.keyboard_routing.generated_seed import write_manifest
    with pytest.raises(ValueError,match='generation profile'):
        write_manifest(base/'output/prepared-seed/project',generation_profile='dsn-process-v2')


def test_process_v2_rejects_an_existing_v1_field_before_seed_or_signals(tmp_path,monkeypatch):
    driver,base,source,calls=setup_driver(tmp_path,monkeypatch)
    (source/'.smilepcb-field.json').write_text(json.dumps({'scope':'matrix-with-controller','generation_profile':'dsn-safe-v1'}))
    with pytest.raises(ValueError,match='generation profile'):driver.main()
    assert not any(op in ('components','global') for op,_ in calls)
    assert not (base/'output/prepared-seed').exists()


def test_process_v2_mismatched_worker_is_rejected_before_seed_construction(tmp_path,monkeypatch):
    driver,base,source,calls=setup_driver(tmp_path,monkeypatch)
    import smilepcb.lab_tools as lab
    original=lab.worker
    def wrong(*args,**kwargs):return {**original(*args,**kwargs),'generation_profile':'native'}
    monkeypatch.setattr(lab,'worker',wrong)
    with pytest.raises(ValueError,match='generation profile'):driver.main()
    assert len(calls)==1 and calls[0][0]=='usb'
    assert not (base/'output/prepared-seed').exists()


@pytest.mark.parametrize('change',['preparation_missing','mode_missing','unknown_mode','profile_missing','wrong_profile'])
def test_process_v2_seed_requires_actual_supported_ordered_model(tmp_path,monkeypatch,change):
    driver,base,source,calls=setup_driver(tmp_path,monkeypatch);driver.main()
    project=base/'output/prepared-seed/project';path=project/'.smilepcb-keyboard.json';model=json.loads(path.read_text())
    if change=='preparation_missing':model.pop('routing_preparation')
    elif change=='mode_missing':model['routing_preparation'].pop('mode')
    elif change=='unknown_mode':model['routing_preparation']['mode']='unsupported'
    elif change=='profile_missing':model['routing_preparation'].pop('generation_profile')
    else:model['routing_preparation']['generation_profile']='dsn-safe-v1'
    path.write_text(json.dumps(model))
    from smilepcb.keyboard_routing.generated_seed import write_manifest
    with pytest.raises(ValueError,match='generation profile'):
        write_manifest(project,generation_profile='dsn-process-v2')


def test_complete_precompose_field_is_verified_before_any_mutation(tmp_path,monkeypatch):
    driver,base,source,calls=setup_driver(tmp_path,monkeypatch)
    (source/'.smilepcb-field.json').write_text(json.dumps({'scope':'matrix-with-controller','generation_profile':'dsn-process-v2'}))
    driver.main()
    operations=[op for op,_ in calls]
    assert operations[0]=='field_verify'
    assert not set(operations)&{'usb','key_data_access','controller_ports','local_modules','controller_return_tree','matrix_field'}
    from smilepcb.keyboard_routing.generated_seed import validate_manifest
    manifest=validate_manifest(base/'output/prepared-seed/project')
    assert manifest['proof_mode']=='controller-field'
    assert 'matrix-field.json' in manifest['files']
    assert 'controller-ports.json' not in manifest['files']


@pytest.mark.parametrize('defect',['scope','generation_profile'])
def test_invalid_existing_field_never_falls_back_to_mutating_producers(tmp_path,monkeypatch,defect):
    driver,base,source,calls=setup_driver(tmp_path,monkeypatch)
    proof={'scope':'matrix-with-controller','generation_profile':'dsn-process-v2'};proof[defect]='invalid'
    (source/'.smilepcb-field.json').write_text(json.dumps(proof))
    with pytest.raises(ValueError,match='Existing field'):driver.main()
    assert [op for op,_ in calls]==['field_verify']
    assert not (base/'output/prepared-seed').exists()
