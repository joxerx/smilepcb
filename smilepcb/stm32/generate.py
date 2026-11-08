"""One complete fresh STM32 pipeline for installed CLI and source adapters.

Routing stage implementations are package-owned executable resources. Paths are
explicit and every generated/cache/diagnostic file stays below the new output.
"""
import argparse
from copy import deepcopy
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from smilepcb.keyboard_tools import load_tools,configure_kicad
from smilepcb.keyboard_layout import parse_layout
from .context import run_context,STAGED_GENERATION_PROFILE
from .fixture_inputs import load_fixture
from smilepcb.usb_policy import resolve_usb_max_length_nm,parse_usb_max_length_mm

SCRIPTS=Path(__file__).parent
PACKAGE=SCRIPTS.parent



def design(*args,**kwargs):
    from smilepcb.keyboard_model import design as call
    return call(*args,**kwargs)


def create_libraries(*args,**kwargs):
    from smilepcb.keyboard_libraries import create_libraries as call
    return call(*args,**kwargs)


def worker(*args,**kwargs):
    from smilepcb.lab_tools import worker as call
    return call(*args,**kwargs)


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_controller_source(directory):
    directory=Path(directory)
    if directory.is_symlink() or not directory.is_dir():raise ValueError('Public controller source must be a regular directory')
    manifest=json.loads((SCRIPTS/'controller-manifest.json').read_text())
    names={'stm32_hotswap_chiffre.kicad_pcb','readme.md','COPYING','manifest.json'}
    paths={name:directory/name for name in names}
    if any(p.is_symlink() or not p.is_file() or p.stat().st_size>16*1024*1024 for p in paths.values()):
        raise ValueError('Public controller source requires the pinned PCB, README, COPYING and manifest')
    if json.loads(paths['manifest.json'].read_bytes())!=manifest:raise ValueError('Public controller source manifest differs from the pinned revision')
    for record in manifest['files']:
        name=record.get('local',Path(record['path']).name)
        if digest(paths[name])!=record['sha256']:raise ValueError('Public controller source checksum mismatch: '+name)
    return paths


STAGE_STOP_GRACE=3


def run_stage(name,command,timeout,*,out,env,history,started):
    from smilepcb.process_ownership import inherited_group_fds
    print(name,file=sys.stderr,flush=True);tick=time.monotonic();child=None;error=None
    (out/'progress.json').write_text(json.dumps({'stage':name,'elapsed_s':tick-started},indent=2)+'\n')
    try:
        with (out/(name+'.log')).open('w') as log:
            child=subprocess.Popen(command,cwd=out,env=env,stdout=log,stderr=subprocess.STDOUT,
                                   pass_fds=inherited_group_fds())
            try:
                status=child.wait(timeout=timeout)
            except BaseException:
                # subprocess.run would immediately SIGKILL the host stage here,
                # cutting off its detached compiler cleanup finally block.
                if child.poll() is None:
                    child.terminate()
                    try:child.wait(timeout=STAGE_STOP_GRACE)
                    except subprocess.TimeoutExpired:child.kill();child.wait()
                raise
            if status:raise subprocess.CalledProcessError(status,command)
    except BaseException as failure:
        error=failure;raise
    finally:
        status=('passed' if error is None else 'cancelled' if isinstance(error,KeyboardInterrupt)
                else 'timeout' if isinstance(error,subprocess.TimeoutExpired) else 'failed')
        attempt={'stage':name,'elapsed_s':time.monotonic()-tick,'status':status,
                 'exit_code':child.returncode if child is not None else None}
        history.append(attempt)
        (out/(name+'-attempt.json')).write_text(json.dumps(attempt,indent=2)+'\n')


def _execute(kle_file,config_file,tools_file,output,controller_source,args):
    kle_file,config_file,tools_file=map(Path,(kle_file,config_file,tools_file))
    output=Path(output)
    if output.exists() or output.is_symlink():raise ValueError('Select a new output directory')
    out=output.resolve()
    if any(part in ('site-packages','dist-packages') for part in out.parts) or out.is_relative_to(PACKAGE):
        raise ValueError('Output must be outside the installed package')
    if any(p.is_symlink() or not p.is_file() or p.stat().st_size>65536 for p in (kle_file,config_file)):
        raise ValueError('Layout inputs must be bounded regular JSON files')
    input_bytes={'layout.kle.json':kle_file.read_bytes(),'keyboard.json':config_file.read_bytes()}
    layout=parse_layout(input_bytes['layout.kle.json'].decode('utf-8'),json.loads(input_bytes['keyboard.json']))
    from smilepcb.keyboard_process import process_rules
    from smilepcb.routing_quality import quality_profile
    process_rules(args.process_profile);quality_profile(args.routing_quality)
    if type(args.placement_attempts) is not int or not 1<=args.placement_attempts<=8:raise ValueError('Placement attempts must be 1..8')
    if type(args.completion_budget_s) is not int or not 120<=args.completion_budget_s<=3600:raise ValueError('Completion budget must be 120..3600 seconds')
    if type(args.placement_budget_s) is not int or not 220<=args.placement_budget_s<=1200:raise ValueError('Placement budget must be 220..1200 seconds')
    if not args.native_staged and args.placement_budget_s!=220:raise ValueError('Placement budget requires native staging')
    if type(getattr(args,'coherent_via_banks',False)) is not bool:raise ValueError('Coherent via banks must be boolean')
    if getattr(args,'coherent_via_banks',False) and not args.native_staged:raise ValueError('Coherent via banks require native staging')
    if type(getattr(args,'unified_controller_plan',False)) is not bool:raise ValueError('Unified controller plan must be boolean')
    if getattr(args,'unified_controller_plan',False) and not args.native_staged:raise ValueError('Unified controller plan requires native staging')
    args.usb_max_length_nm=resolve_usb_max_length_nm(getattr(args,'usb_max_length_nm',...))
    if type(getattr(args,'component_corridors',False)) is not bool:raise ValueError('Component corridors must be boolean')
    if getattr(args,'component_corridors',False) and not (args.native_staged and getattr(args,'unified_controller_plan',False)):raise ValueError('Component corridors require native staging and unified controller planning')
    tools=load_tools(tools_file)
    source_files=validate_controller_source(controller_source)
    if out.is_relative_to(Path(controller_source).resolve()):raise ValueError('Output must be outside the read-only controller source')
    args.tools=str(tools_file.resolve())
    if not hasattr(args,'fixture'):args.fixture='keyboard'
    source_hashes={name:digest(path) for name,path in source_files.items()}
    input_hashes={name:hashlib.sha256(value).hexdigest() for name,value in input_bytes.items()}
    out.mkdir(parents=True,exist_ok=False)
    inputs=out/'inputs';inputs.mkdir()
    for name,value in input_bytes.items():(inputs/name).write_bytes(value)
    source_copy=out/'sources/le-chiffre';source_copy.mkdir(parents=True)
    for name,path in source_files.items():shutil.copyfile(path,source_copy/name)
    try:
        with run_context(out,inputs):
            return _pipeline(args,layout,kle_file,config_file,out,inputs,source_files,source_hashes,input_hashes,tools)
    except BaseException as error:
        progress=json.loads((out/'progress.json').read_text()) if (out/'progress.json').exists() else {}
        (out/'failure.json').write_text(json.dumps({'complete':False,'stage':progress.get('stage','generation'),'error':str(error)},indent=2)+'\n')
        raise


def generate_keyboard(kle_file,config_file,tools_file,output,*,controller_source,
                      process_profile='fr4-2l-1oz-0p5-0p2',routing_quality='turns',
                      completion_budget_s=3600,placement_attempts=8,placement_budget_s=220,coherent_via_banks=False,unified_controller_plan=False,component_corridors=False,usb_max_length_nm=...):
    """Generate from supported user KLE/config; accept only final native/QMK proof."""
    if type(coherent_via_banks) is not bool:raise ValueError('Coherent via banks must be boolean')
    if type(unified_controller_plan) is not bool:raise ValueError('Unified controller plan must be boolean')
    if type(component_corridors) is not bool:raise ValueError('Component corridors must be boolean')
    if component_corridors and not unified_controller_plan:raise ValueError('Component corridors require unified controller planning')
    usb_max_length_nm=resolve_usb_max_length_nm(usb_max_length_nm)
    args=SimpleNamespace(component_corridors=component_corridors,usb_max_length_nm=usb_max_length_nm,unified_controller_plan=unified_controller_plan,coherent_via_banks=coherent_via_banks,native_staged=True,process_profile=process_profile,routing_quality=routing_quality,
        completion_budget_s=completion_budget_s,placement_attempts=placement_attempts,placement_budget_s=placement_budget_s,
        preserve_core_routing=False,optimize_matrix_gpio=True,stop_after_placement=False,
        joint_controller=False,joint_board=False,with_fanout=False,routing_candidates=1)
    return _execute(kle_file,config_file,tools_file,output,controller_source,args)


def _pipeline(args,validated_layout,kle_file,config_file,out,inputs,source_files,source_hashes,input_hashes,tools):
    base=out;fixture=args.fixture
    configure_kicad(tools,out)
    from smilepcb.runtime import KICAD_PYTHON,environment
    from smilepcb import schematic as s
    scripts=SCRIPTS;started=time.monotonic();history=[]
    donor=base/'sources/le-chiffre/stm32_hotswap_chiffre.kicad_pcb'
    digest=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
    if digest(donor)!=source_hashes['stm32_hotswap_chiffre.kicad_pcb']:raise ValueError('Public donor changed')
    (out/'inputs.json').write_text(json.dumps({**{name:digest(inputs/name) for name in ('layout.kle.json','keyboard.json')},**{'controller/'+name:digest(path) for name,path in source_files.items()}},indent=2)+'\n')
    (out/'code-hashes.json').write_text(json.dumps({str(p.relative_to(PACKAGE.parent)):digest(p) for p in sorted([*scripts.glob('*.py'),*PACKAGE.rglob('*.py')])},indent=2)+'\n')
    def stage(name,command,timeout):
        return run_stage(name,command,timeout,out=out,env=environment(),history=history,started=started)
    core='extracted';normalized='normalized'
    stage('extract',[KICAD_PYTHON,str(scripts/'extract_controller.py'),str(base),core],90)
    prepared_core=None;raw_cfg=None;module_project=None
    if args.native_staged:
        from smilepcb.keyboard_process import project_settings
        from smilepcb.stm32.prepare_controller import write_empty_seed
        module_project=out/'controller-module/project';module_project.mkdir(parents=True)
        raw_cfg=design(validated_layout,controller_origin_mm=(0,0))
        create_libraries(module_project)
        (module_project/'model-before-placement.json').write_text(json.dumps(raw_cfg,indent=2)+'\n')
        (module_project/'keyboard.kicad_pro').write_text(json.dumps(project_settings(args.process_profile),indent=2)+'\n')
        # LoadBoard compiles rules from the PCB's same-basename project.
        write_empty_seed(base/core/'core.kicad_pcb',module_project/'keyboard.kicad_pcb')
        (out/'progress.json').write_text(json.dumps({'stage':'controller-module','elapsed_s':time.monotonic()-started})+'\n')
        prepared=worker(scripts/'prepare_controller.py',{'project':str(module_project),'source_core':str(base/core/'core.kicad_pcb'),
            'process_profile':args.process_profile,'generation_profile':STAGED_GENERATION_PROFILE,'budget_s':90},out/'controller-module/preparation.log',timeout_s=110)
        (out/'controller-module/preparation.json').write_text(json.dumps(prepared,indent=2)+'\n')
        if prepared.get('accepted') is not True:raise ValueError('Controller module preparation rejected: '+prepared.get('error',{}).get('message',prepared.get('reason','required local obligations unproved')))
        artifacts={key:Path(prepared[key]).resolve() for key in ('core_file','descriptor_file','report_file')}
        if any(not path.is_relative_to(module_project.resolve()) or not path.is_file() for path in artifacts.values()):raise ValueError('Controller module preparation returned an invalid output artifact')
        core_file=artifacts['core_file'];prepared_core=json.loads(artifacts['descriptor_file'].read_text())
        if prepared_core.get('source_sha256')!=digest(core_file):raise ValueError('Controller module descriptor source changed')
        normalized='prepared-controller'
    else:
        stage('normalize',[KICAD_PYTHON,str(scripts/'normalize_core.py'),str(base),normalized,core]+([] if args.preserve_core_routing else ['--reroute-core']),90)
        core_file=base/normalized/'core.kicad_pcb'
    batch=out/'layouts';batch.mkdir()
    accepted=None;rejected={False:[],True:[]};attempts=[];routed=[];base_angle=None
    for index in range(args.placement_attempts):
        mirror=bool(index//4) if args.routing_candidates>1 else bool(index%2);attempt=out/('placement-'+str(index));project=attempt/'project';project.mkdir(parents=True)
        cfg=deepcopy(raw_cfg) if raw_cfg is not None else design(parse_layout((inputs/'layout.kle.json').read_text(),json.loads((inputs/'keyboard.json').read_text())),controller_origin_mm=(0,0))
        cfg['controller_recipe']={'name':'Le Chiffre STM32F072 local core','donor_commit':'57f64495c4b6dca1566b8ddbd464849c4023a56f','tile_sha256':digest(core_file),'tile_variant':normalized,'status':'experimental'}
        if module_project is not None:
            shutil.copytree(module_project/'KeyboardParts.pretty',project/'KeyboardParts.pretty')
            for name in ('fp-lib-table','sym-lib-table','KeyboardParts.kicad_sym','component-sources.json','LICENSE-marbastlib','keyboard.kicad_pro'):
                if (module_project/name).is_file():shutil.copyfile(module_project/name,project/name)
        else:create_libraries(project)
        (project/'model-before-placement.json').write_text(json.dumps(cfg,indent=2)+'\n')
        for filename in ('layout.kle.json','keyboard.json'):(project/filename).write_bytes((inputs/filename).read_bytes())
        print('placement',index,'face','F.Cu' if mirror else 'B.Cu',file=sys.stderr,flush=True)
        (out/'progress.json').write_text(json.dumps({'stage':'placement','attempt':index,
            'face':'F.Cu' if mirror else 'B.Cu','elapsed_s':time.monotonic()-started},indent=2)+'\n')
        result=worker(scripts/'place.py',{'project':str(project),'core':str(core_file),'budget_s':args.placement_budget_s if args.native_staged else 80,
            'access_budget_s':70,'require_controller_access':not (args.joint_controller or args.joint_board or args.native_staged),'fanout_rows':3 if args.native_staged else 0,'mirror_core':mirror,'excluded_core_poses':rejected[mirror],
            'core_rotation_deg':((base_angle+90*(index%4))%360 if args.routing_candidates>1 and base_angle is not None else None),
            'optimize_matrix_gpio':args.optimize_matrix_gpio,'prepare_ordered_controller':args.native_staged,'complete_collector_matrix':args.native_staged,'process_profile':args.process_profile,'routing_quality':args.routing_quality,'generation_profile':STAGED_GENERATION_PROFILE if args.native_staged else 'native',
            **({'usb_max_length_nm':args.usb_max_length_nm} if hasattr(args,'usb_max_length_nm') else {}),
            **({'core_module':deepcopy(prepared_core)} if prepared_core is not None else {}),
            **({'coherent_via_banks':True} if getattr(args,'coherent_via_banks',False) else {}),
            **({'component_corridors':True} if getattr(args,'component_corridors',False) else {}),
            **({'unified_controller_plan':True} if getattr(args,'unified_controller_plan',False) else {})},attempt/'placement.log',timeout_s=args.placement_budget_s+20 if args.native_staged else 90)
        (attempt/'placement.json').write_text(json.dumps(result,indent=2)+'\n')
        if args.native_staged and result.get('generation_profile')!=STAGED_GENERATION_PROFILE:
            raise ValueError('Placement generation profile differs from requested policy')
        attempts.append({'index':index,'face':result['core_face'],'pose':result['core_pose'],'status':result['status'],
                         'controller_access':result.get('controller_access'),'matrix_gpio':result.get('matrix_gpio'),'elapsed_s':result['elapsed_s']})
        (out/'placement-attempts.json').write_text(json.dumps(attempts,indent=2)+'\n')
        if result['status']=='placed':
            accepted=attempt
            if args.routing_candidates==1:break
            if base_angle is None:base_angle=result['core_pose']['rotation_deg']
            candidate_tag='candidate-'+str(index)
            selected=out/('candidate-'+str(index))/fixture;selected.parent.mkdir();shutil.copytree(attempt,selected)
            stage('schematic-'+str(index),[sys.executable,str(scripts/'compose.py'),args.tools,candidate_tag],240)
            route_tag='route-'+str(index)
            from smilepcb.stm32.candidate_selection import probe_candidate,candidate_score
            def inspect_candidate():
                route_dir=base/route_tag
                access=worker(scripts/'check_access.py',{'board':str(route_dir/'project/keyboard.kicad_pcb')},route_dir/'controller-access.log',timeout_s=90)
                (route_dir/'controller-access.json').write_text(json.dumps(access,indent=2)+'\n')
                checks=json.loads((route_dir/'native-checks/summary.json').read_text())
                graph=json.loads((route_dir/'independent.json').read_text())
                score=candidate_score(checks,graph,access)
                return {'eligible':score is not None,'score':score,'native_checks':checks,
                    'controller_access':access,'pad_group_gaps':graph['pad_group_gaps']}
            probe=probe_candidate(lambda:stage('route-'+str(index),
                [sys.executable,str(scripts/'route_legacy.py'),args.tools,route_tag,candidate_tag,fixture,'--joint-board','--editable-routing']+
                (['--with-fanout'] if args.with_fanout else []),1000),inspect_candidate)
            routed.append(dict(probe,index=index,route_tag=route_tag,pose=result['core_pose'],face=result['core_face'],matrix_gpio=result.get('matrix_gpio')))
            (out/'routing-candidates.json').write_text(json.dumps(routed,indent=2)+'\n')
            print('candidate result',json.dumps(routed[-1]),file=sys.stderr,flush=True)
            if len(routed)>=args.routing_candidates:break
        if result['core_pose']:
            pose=result['core_pose'];rejected[mirror].append([*pose['origin_nm'],pose['rotation_deg']])
    if accepted is None:raise ValueError('No candidate passed actual GPIO/power/protocol access within the declared pose budget')
    if args.routing_candidates>1:
        valid=[candidate for candidate in routed if candidate['eligible']]
        if not valid:raise ValueError('No routing candidate passed native physical rules and pin/layer guards')
        best=min(valid,key=lambda candidate:(candidate['score'],candidate['index']))
        (out/'selected-route.json').write_text(json.dumps(dict(best,complete=False,selection_scope='best incomplete diagnostic; full native and firmware acceptance still required'),indent=2)+'\n')
        shutil.copytree(base/best['route_tag'],out/'routed')
    else:
        selected=batch/fixture;shutil.copytree(accepted,selected)
        stage('schematic',[sys.executable,str(scripts/'compose.py'),args.tools,'layouts']+([args.process_profile] if args.native_staged else []),240)
        if args.joint_controller:
            stage('controller-joint',[sys.executable,str(scripts/'route_legacy.py'),args.tools,'controller-joint','layouts',fixture,'--controller-only'],480)
            proof=json.loads((out/'controller-joint/controller-result.json').read_text())
            if not proof['accepted']:
                (out/'failure.json').write_text(json.dumps({'complete':False,'stage':'joint controller placement feasibility','proof':proof},indent=2)+'\n')
                raise ValueError('Joint controller routing did not pass independent native placement feasibility')
            shutil.copytree(out/'controller-joint/project',selected/'project',dirs_exist_ok=True)
        if args.stop_after_placement:
            print('Controller access accepted; full routing not requested in this diagnostic invocation',file=sys.stderr,flush=True)
            raise SystemExit(0)
        stage('route',[sys.executable,str(scripts/('route.py' if args.native_staged else 'route_legacy.py')),args.tools,'routed','layouts',fixture]+(['--routing-quality',args.routing_quality] if args.native_staged else [])+(['--joint-board','--editable-routing'] if args.joint_board else [])+(['--with-fanout'] if args.with_fanout else []),1800 if args.native_staged else 1000)
    checks=json.loads((out/'routed/native-checks/summary.json').read_text())
    if checks['erc']['severity'] or checks['drc']['severity'].get('error') or checks['drc']['schematic_parity']:
        (out/'failure.json').write_text(json.dumps({'complete':False,'stage':'post-router native rules','checks':checks},indent=2)+'\n')
        raise ValueError('Native post-router errors must be resolved before completion/firmware work')
    stage('complete',[sys.executable,str(scripts/'complete.py'),args.tools,'routed','finished',fixture,'--budget-s',str(args.completion_budget_s),'--routing-quality',args.routing_quality]+(['--generation-profile',STAGED_GENERATION_PROFILE] if args.native_staged else []),args.completion_budget_s+180)
    result=json.loads((out/'finished/result.json').read_text())
    if args.native_staged and result.get('generation_profile')!=STAGED_GENERATION_PROFILE:
        raise ValueError('Completion generation profile differs from requested policy')
    if (result.get('complete') is not True or result['independent']['pad_group_gaps']!=0
            or result['independent']['copper_layers']!=2):
        raise ValueError('Finished stage did not prove complete two-layer connectivity')
    if digest(donor)!=source_hashes['stm32_hotswap_chiffre.kicad_pcb']:raise ValueError('Controller source changed during generation')
    result.update(entrypoint='smilepcb build',fresh_from_kle=True,fixture=fixture,input_key_count=len(validated_layout['keys']),placement_attempts=attempts,
                  cold_history=history,cold_elapsed_s=time.monotonic()-started,donor_unchanged=True,routing_quality=args.routing_quality,
                  generation_profile=STAGED_GENERATION_PROFILE if args.native_staged else result.get('generation_profile','native'))
    if any(digest(path)!=source_hashes[name] for name,path in source_files.items()):raise ValueError('Public controller source changed')
    if digest(kle_file)!=input_hashes['layout.kle.json'] or digest(config_file)!=input_hashes['keyboard.json']:raise ValueError('Layout inputs changed during generation')
    if any(digest(inputs/name)!=sha for name,sha in input_hashes.items()):raise ValueError('Saved layout inputs changed during generation')
    (out/'result.json').write_text(json.dumps(result,indent=2)+'\n');print('Fresh KLE -> PCB -> QMK accepted',file=sys.stderr,flush=True)

    return result


def main(argv=None,*,compatibility_workspace=None):
    parser=argparse.ArgumentParser();parser.add_argument('tools');parser.add_argument('tag')
    parser.add_argument('--fixture',default='ansi61',help='Input directory under recipes/stm32/inputs')
    parser.add_argument('--stop-after-placement',action='store_true')
    parser.add_argument('--component-corridors',action='store_true',help='Experimental controller-local corridors; requires unified controller planning')
    parser.add_argument('--unified-controller-plan',action='store_true',help='Experimental joint all-function MCU source plan with required controller-local closure')
    parser.add_argument('--coherent-via-banks',action='store_true',help='Experimental best-effort coherent source preference; full-board proof still required')
    parser.add_argument('--native-staged',action='store_true',help='Checked generated MCU/key access, ordered native routing, bounded generated-copper recovery')
    parser.add_argument('--process-profile',choices=('legacy-0p6-0p3','fr4-2l-1oz-0p5-0p2'))
    parser.add_argument('--preserve-core-routing',action='store_true')
    parser.add_argument('--placement-attempts',type=int,default=8,choices=range(1,9))
    parser.add_argument('--usb-max-length-mm',type=parse_usb_max_length_mm,default=...,dest='usb_max_length_nm',help='Absolute USB cap in mm; none removes only this geometry cap')
    parser.add_argument('--placement-budget-s',type=int,default=220,help='Staged placement seconds per attempt (220..1200)')
    parser.add_argument('--joint-controller',action='store_true',help='Use pinned Freerouting for joint controller placement feasibility')
    parser.add_argument('--joint-board',action='store_true',help='Joint full-board routing without freezing provisional controller escapes')
    parser.add_argument('--with-fanout',action='store_true',help='Use the stock bounded fanout stage inside the same joint routing job')
    parser.add_argument('--routing-candidates',type=int,default=1,choices=range(1,5),help='Compare bounded full-board routes before choosing the best diagnostic')
    parser.add_argument('--optimize-matrix-gpio',action='store_true',help='Assign safe matrix GPIOs by actual native pad geometry')
    parser.add_argument('--completion-budget-s',type=int,default=3600,choices=range(120,3601))
    parser.add_argument('--routing-quality',choices=('legacy','turns'),default='legacy',help='Optional native path cost: length plus a positive bend penalty')
    parser.add_argument('--output',type=Path)
    parser.add_argument('--kle',type=Path)
    parser.add_argument('--config',type=Path)
    parser.add_argument('--controller-source',type=Path)
    args=parser.parse_args(argv)
    if not 220<=args.placement_budget_s<=1200:parser.error('Placement budget must be 220..1200 seconds')
    if not args.native_staged and args.placement_budget_s!=220:parser.error('Placement budget requires --native-staged')
    if args.unified_controller_plan and not args.native_staged:parser.error('Unified controller plan requires --native-staged')
    if args.coherent_via_banks and not args.native_staged:parser.error('Coherent via banks require --native-staged')
    if args.routing_quality!='legacy' and not args.native_staged:parser.error('Routing quality requires --native-staged')
    if args.native_staged and args.process_profile is None:parser.error('Native staged routing requires an explicit process profile')
    if args.native_staged and (args.joint_board or args.joint_controller or args.with_fanout or args.routing_candidates!=1):parser.error('Native staging cannot be combined with global routing modes')
    if args.native_staged and args.stop_after_placement:parser.error('Native staging cannot stop before controller interface verification')
    if args.process_profile and not args.native_staged:parser.error('A process profile is only supported for native staging')
    if args.routing_candidates>1 and not args.joint_board:parser.error('Multiple routing candidates require --joint-board')
    # Native GPIO allocation rejects any existing assignable-pin copper; a
    # preserved supply/reset/USB module is compatible and must remain intact.
    if args.joint_controller:parser.error('Reduced controller exchange is quarantined: omitted-pin copper must be reconciled first; use --joint-board')
    if args.joint_controller and args.joint_board:parser.error('Choose one joint routing scope')
    if args.joint_board and args.stop_after_placement:parser.error('Full-board feasibility requires the routing stage')

    if args.kle is not None or args.config is not None:
        if args.kle is None or args.config is None:parser.error('Provide both --kle and --config')
        kle,config=args.kle,args.config
    else:
        fixture,inputs,_=load_fixture(SCRIPTS/'inputs',args.fixture)
        kle,config=inputs/'layout.kle.json',inputs/'keyboard.json'
    if args.output is None:
        if compatibility_workspace is None:parser.error('Installed generation requires an explicit --output')
        if not args.tag.replace('-','').replace('_','').isalnum():parser.error('Invalid run tag')
        output=Path(compatibility_workspace)/args.tag
    else:output=args.output
    source=args.controller_source
    if source is None:
        if compatibility_workspace is None:parser.error('Select an explicit --controller-source')
        source=Path(compatibility_workspace)/'sources/le-chiffre'
    result=_execute(kle,config,args.tools,output,source,args)
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
