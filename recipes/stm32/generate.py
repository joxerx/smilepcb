"""Fresh KLE -> checked controller placement -> PCB routing -> QMK proof.

All candidate choices are geometry/connectivity driven. Existing partial PCB
artifacts are never inputs. Toolchain and public donor are explicit and pinned.
"""
import argparse,hashlib,json,subprocess,sys,time,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from smilepcb.keyboard_tools import load_tools,configure_kicad
from recipes.stm32.fixture_inputs import load_fixture
parser=argparse.ArgumentParser();parser.add_argument('tools');parser.add_argument('tag')
parser.add_argument('--fixture',default='ansi61',help='Input directory under recipes/stm32/inputs')
parser.add_argument('--stop-after-placement',action='store_true')
parser.add_argument('--native-staged',action='store_true',help='Checked generated MCU/key access, ordered native routing, bounded generated-copper recovery')
parser.add_argument('--process-profile',choices=('legacy-0p6-0p3','fr4-2l-1oz-0p5-0p2'))
parser.add_argument('--preserve-core-routing',action='store_true')
parser.add_argument('--placement-attempts',type=int,default=8,choices=range(1,9))
parser.add_argument('--joint-controller',action='store_true',help='Use pinned Freerouting for joint controller placement feasibility')
parser.add_argument('--joint-board',action='store_true',help='Joint full-board routing without freezing provisional controller escapes')
parser.add_argument('--with-fanout',action='store_true',help='Use the stock bounded fanout stage inside the same joint routing job')
parser.add_argument('--routing-candidates',type=int,default=1,choices=range(1,5),help='Compare bounded full-board routes before choosing the best diagnostic')
parser.add_argument('--optimize-matrix-gpio',action='store_true',help='Assign safe matrix GPIOs by actual native pad geometry')
parser.add_argument('--completion-budget-s',type=int,default=3600,choices=range(120,3601))
parser.add_argument('--routing-quality',choices=('legacy','turns'),default='legacy',help='Optional native path cost: length plus a positive bend penalty')
args=parser.parse_args()
if args.routing_quality!='legacy' and not args.native_staged:parser.error('Routing quality requires --native-staged')
if args.native_staged and args.process_profile is None:parser.error('Native staged routing requires an explicit process profile')
if args.native_staged and (args.joint_board or args.joint_controller or args.with_fanout or args.routing_candidates!=1):parser.error('Native staging cannot be combined with global routing modes')
if args.native_staged and args.stop_after_placement:parser.error('Native staging cannot stop before controller interface verification')
if args.process_profile and not args.native_staged:parser.error('A process profile is only supported for native staging')
if args.routing_candidates>1 and not args.joint_board:parser.error('Multiple routing candidates require --joint-board')
if args.optimize_matrix_gpio and args.preserve_core_routing:parser.error('Matrix reassignment requires releasing copied matrix copper')
if args.joint_controller:parser.error('Reduced controller exchange is quarantined: omitted-pin copper must be reconciled first; use --joint-board')
if args.joint_controller and args.joint_board:parser.error('Choose one joint routing scope')
if args.joint_board and args.stop_after_placement:parser.error('Full-board feasibility requires the routing stage')
if not args.tag.replace('-','').replace('_','').isalnum():raise ValueError('Invalid run tag')
fixture,inputs,validated_layout=load_fixture(Path(__file__).parent/'inputs',args.fixture)
tools=load_tools(args.tools);base=ROOT/'.lab/public-controller';out=base/args.tag
out.mkdir(exist_ok=False);configure_kicad(tools,out)
from smilepcb.runtime import KICAD_PYTHON,environment
from smilepcb.keyboard_layout import parse_layout
from smilepcb.keyboard_model import design
from smilepcb.keyboard_libraries import create_libraries
from smilepcb import schematic as s
from smilepcb.lab_tools import worker
scripts=Path(__file__).parent;started=time.monotonic();history=[]
donor=base/'sources/le-chiffre/stm32_hotswap_chiffre.kicad_pcb'
digest=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
if digest(donor)!='984473f03db99eda31c3b5f12a3bb2e3d07199ed8e2577d0f6cd78b123639b82':raise ValueError('Public donor changed')
(out/'inputs.json').write_text(json.dumps({str(p.relative_to(ROOT)):digest(p) for p in [donor,*inputs.glob('*.json')]},indent=2)+'\n')
(out/'code-hashes.json').write_text(json.dumps({str(p.relative_to(ROOT)):digest(p) for p in sorted([*scripts.glob('*.py'),*(ROOT/'smilepcb').rglob('*.py')])},indent=2)+'\n')
def stage(name,command,timeout):
    print(name,flush=True);tick=time.monotonic()
    (out/'progress.json').write_text(json.dumps({'stage':name,'elapsed_s':tick-started},indent=2)+'\n')
    with (out/(name+'.log')).open('w') as log:
        subprocess.run(command,cwd=ROOT,env=environment(),stdout=log,stderr=subprocess.STDOUT,timeout=timeout,check=True)
    history.append({'stage':name,'elapsed_s':time.monotonic()-tick})
core=args.tag+'/extracted';normalized=args.tag+'/normalized'
stage('extract',[KICAD_PYTHON,str(scripts/'extract_controller.py'),str(base),core],90)
stage('normalize',[KICAD_PYTHON,str(scripts/'normalize_core.py'),str(base),normalized,core]+([] if args.preserve_core_routing else ['--reroute-core']),90)
core_file=base/normalized/'core.kicad_pcb';batch=out/'layouts';batch.mkdir()
accepted=None;rejected={False:[],True:[]};attempts=[];routed=[];base_angle=None
for index in range(args.placement_attempts):
    mirror=bool(index//4) if args.routing_candidates>1 else bool(index%2);attempt=out/('placement-'+str(index));project=attempt/'project';project.mkdir(parents=True)
    cfg=design(parse_layout((inputs/'layout.kle.json').read_text(),json.loads((inputs/'keyboard.json').read_text())),controller_origin_mm=(0,0))
    cfg['controller_recipe']={'name':'Le Chiffre STM32F072 local core','donor_commit':'57f64495c4b6dca1566b8ddbd464849c4023a56f','tile_sha256':digest(core_file),'tile_variant':normalized,'status':'experimental'}
    create_libraries(project);(project/'model-before-placement.json').write_text(json.dumps(cfg,indent=2)+'\n')
    for filename in ('layout.kle.json','keyboard.json'):(project/filename).write_bytes((inputs/filename).read_bytes())
    print('placement',index,'face','F.Cu' if mirror else 'B.Cu',flush=True)
    result=worker(scripts/'place.py',{'project':str(project),'core':str(core_file),'budget_s':80,
        'access_budget_s':70,'require_controller_access':not (args.joint_controller or args.joint_board or args.native_staged),'fanout_rows':2 if args.native_staged else 0,'mirror_core':mirror,'excluded_core_poses':rejected[mirror],
        'core_rotation_deg':((base_angle+90*(index%4))%360 if args.routing_candidates>1 and base_angle is not None else None),
        'optimize_matrix_gpio':args.optimize_matrix_gpio},attempt/'placement.log',timeout_s=90)
    (attempt/'placement.json').write_text(json.dumps(result,indent=2)+'\n')
    attempts.append({'index':index,'face':result['core_face'],'pose':result['core_pose'],'status':result['status'],
                     'controller_access':result.get('controller_access'),'matrix_gpio':result.get('matrix_gpio'),'elapsed_s':result['elapsed_s']})
    (out/'placement-attempts.json').write_text(json.dumps(attempts,indent=2)+'\n')
    if result['status']=='placed':
        accepted=attempt
        if args.routing_candidates==1:break
        if base_angle is None:base_angle=result['core_pose']['rotation_deg']
        candidate_tag=args.tag+'/candidate-'+str(index)
        selected=out/('candidate-'+str(index))/fixture;selected.parent.mkdir();shutil.copytree(attempt,selected)
        stage('schematic-'+str(index),[sys.executable,str(scripts/'compose.py'),args.tools,candidate_tag],240)
        route_tag=args.tag+'/route-'+str(index)
        from recipes.stm32.candidate_selection import probe_candidate,candidate_score
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
        print('candidate result',json.dumps(routed[-1]),flush=True)
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
    stage('schematic',[sys.executable,str(scripts/'compose.py'),args.tools,args.tag+'/layouts']+([args.process_profile] if args.native_staged else []),240)
    if args.joint_controller:
        stage('controller-joint',[sys.executable,str(scripts/'route_legacy.py'),args.tools,args.tag+'/controller-joint',args.tag+'/layouts',fixture,'--controller-only'],480)
        proof=json.loads((out/'controller-joint/controller-result.json').read_text())
        if not proof['accepted']:
            (out/'failure.json').write_text(json.dumps({'complete':False,'stage':'joint controller placement feasibility','proof':proof},indent=2)+'\n')
            raise ValueError('Joint controller routing did not pass independent native placement feasibility')
        shutil.copytree(out/'controller-joint/project',selected/'project',dirs_exist_ok=True)
    if args.stop_after_placement:
        print('Controller access accepted; full routing not requested in this diagnostic invocation',flush=True)
        raise SystemExit(0)
    stage('route',[sys.executable,str(scripts/('route.py' if args.native_staged else 'route_legacy.py')),args.tools,args.tag+'/routed',args.tag+'/layouts',fixture]+(['--routing-quality',args.routing_quality] if args.native_staged else [])+(['--joint-board','--editable-routing'] if args.joint_board else [])+(['--with-fanout'] if args.with_fanout else []),1800 if args.native_staged else 1000)
checks=json.loads((out/'routed/native-checks/summary.json').read_text())
if checks['erc']['severity'] or checks['drc']['severity'].get('error') or checks['drc']['schematic_parity']:
    (out/'failure.json').write_text(json.dumps({'complete':False,'stage':'post-router native rules','checks':checks},indent=2)+'\n')
    raise ValueError('Native post-router errors must be resolved before completion/firmware work')
stage('complete',[sys.executable,str(scripts/'complete.py'),args.tools,args.tag+'/routed',args.tag+'/finished',fixture,'--budget-s',str(args.completion_budget_s),'--routing-quality',args.routing_quality],args.completion_budget_s+180)
result=json.loads((out/'finished/result.json').read_text())
assert result['complete'] and result['independent']['pad_group_gaps']==0
assert result['independent']['copper_layers']==2
assert digest(donor)=='984473f03db99eda31c3b5f12a3bb2e3d07199ed8e2577d0f6cd78b123639b82'
result.update(entrypoint='recipes/stm32/generate.py',fresh_from_kle=True,fixture=fixture,input_key_count=len(validated_layout['keys']),placement_attempts=attempts,
              cold_history=history,cold_elapsed_s=time.monotonic()-started,donor_unchanged=True,routing_quality=args.routing_quality)
(out/'result.json').write_text(json.dumps(result,indent=2)+'\n');print('Fresh KLE -> PCB -> QMK accepted',flush=True)
