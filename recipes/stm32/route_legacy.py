"""STM32 recipe support: route legacy. Native checks are required."""
import sys,json,shutil,subprocess,time,threading,os,platform
from copy import deepcopy
from pathlib import Path
import sexpdata as sx
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from smilepcb.keyboard_tools import load_tools,configure_kicad
tools=load_tools(sys.argv[1]);base=ROOT/'.lab/public-controller';out=base/sys.argv[2];out.mkdir(exist_ok=False);configure_kicad(tools,out)
controller_only='--controller-only' in sys.argv[5:]
joint_board='--joint-board' in sys.argv[5:]
with_fanout='--with-fanout' in sys.argv[5:]
from smilepcb import schematic as s
from smilepcb.lab_tools import worker,rule_checks
from smilepcb.keyboard_build import BRIDGE
from smilepcb.routing_exchange import canonical_dsn,split_junctions,explicit_plane_routing,freerouting_route_modes
from smilepcb.keyboard_exchange import adapt_npth_keepouts,polygonize_circles,guard_milled_edges
source=base/sys.argv[3]/'project' if sys.argv[4]=='prepared' else base/sys.argv[3]/sys.argv[4]/'project';project=out/'project';shutil.copytree(source,project);board=project/'keyboard.kicad_pcb'
editable_routing='--editable-routing' in sys.argv[5:]
preflight_only='--preflight-only' in sys.argv[5:]
if editable_routing and sys.argv[4]=='prepared':raise ValueError('Editable probe requires placed source provenance, not a prepared board')
initial_tree=s.read(board)
core_ids={str(s.one(node,'uuid')[1]) for node in initial_tree if s.tag(node) in ('segment','via','arc')}
local_returns=set()
def call(script,operation,tag,**kw):
 result=worker(script,dict(operation=operation,board=str(board),**kw),out/(tag+'.log'));(out/(tag+'.json')).write_text(json.dumps(result,indent=2)+'\n');return result
if sys.argv[4]!='prepared':
 call(Path(__file__).with_name('route_native.py'),'usb','usb')
 if not controller_only:
  modules=call(BRIDGE,'local_modules','local-modules',via_to_smd_clearance_nm=152000)
  local_returns=set(modules['return_ids'])
  if not joint_board:call(BRIDGE,'components','controller-local',controller_only=True,grid_step_nm=250000,connection_budget_s=3)
native=rule_checks(project,out/'pre-router-native','keyboard',timeout_s=120)
if native['erc']['severity'] or native['drc']['severity'].get('error') or native['drc']['schematic_parity']:raise ValueError('Local-block preparation fails native preflight')
tree=s.read(board);names={node[1]:node[2] for node in tree if s.tag(node)=='net' and len(node)>2}
copper=[node for node in tree if s.tag(node) in ('segment','via','arc')]
if not local_returns<={str(s.one(node,'uuid')[1]) for node in copper}:raise ValueError('Missing local recipe return copper')
protected=[str(s.one(node,'uuid')[1]) for node in copper if not editable_routing or
           str(s.one(node,'uuid')[1]) in core_ids|local_returns or names.get(s.one(node,'net')[1],s.one(node,'net')[1]) in ('USB_DP','USB_DM')]
route=out/'route';route.mkdir();e=call(BRIDGE,'export_open','export',dsn=str(route/'raw.dsn'),protected_ids=protected)
protected=e['protected_ids']
if not local_returns<=set(protected):raise ValueError('Exchange dropped local return protection')
policy={'mode':'recipe/USB/local-returns/native-locks' if editable_routing else 'all-source-fixed',
        'source_objects':len(copper),'initial_recipe_objects':len(core_ids),
        'local_return_objects':len(local_returns),'local_return_ids':sorted(local_returns),
        'protected_ids':protected,'editable_ids':e['editable_ids']}
(route/'copper-policy.json').write_text(json.dumps(policy,indent=2)+'\n')
t=explicit_plane_routing(canonical_dsn((route/'raw.dsn').read_text()));t,edge=guard_milled_edges(t,native_edge_um=200)
cfg=json.loads((project/'.smilepcb-keyboard.json').read_text())
plane_nets=set(cfg['plane_nets']);ignored_classes=[]
controller_job=None
if controller_only:
 from smilepcb.controller_exchange import controller_subset_dsn
 # Controller return pins need real local copper as well as the later plane.
 # Route one actual external return here; leave whole-board plane completion
 # to native refill/stitching rather than deferring MCU returns prematurely.
 t,controller_job=controller_subset_dsn(t,cfg['parts'])
 (route/'controller-job.json').write_text(json.dumps(controller_job,indent=2)+'\n')
if plane_nets or controller_only:
 from smilepcb.routing_policy import prioritize_dsn
 network=sx.loads(t[t.index('\n  (network'):t.index('\n  (wiring')],nil=None,true=None,false=None)
 routed_nets=set(controller_job['routed_nets']) if controller_only else {str(row[1]) for row in network[1:] if str(row[0])=='net'}-plane_nets
 t,ignored_classes=prioritize_dsn(t,routed_nets)
 (route/'deferred-planes.json').write_text(json.dumps({'native_plane_nets':sorted(plane_nets),'ignored_router_classes':ignored_classes,'acceptance':'native refill, stitching and pad-group verification remain mandatory'},indent=2)+'\n')
t,holes=adapt_npth_keepouts(t,holes_um=e['holes_um'],native_hole_clearance_um=e['hole_clearance_um'],router_clearance_um=150)
t,polys=polygonize_circles(t)
# Native KiCad also exports round copper dots as a zero-length path. They have
# no segment to split. Preserve those legal input declarations unchanged while
# the existing junction helper processes non-degenerate segments.
start=t.index('\n  (wiring');end=t.rfind('\n)');wiring=sx.loads(t[start:end],nil=None,true=None,false=None);points=[];ordinary=[wiring[0]]
for row in wiring[1:]:
 path=next((n for n in row[1:] if isinstance(n,list) and str(n[0])=='path'),None) if isinstance(row,list) and str(row[0])=='wire' else None
 coordinates=list(zip(path[3::2],path[4::2])) if path else []
 if coordinates and len(set(coordinates))==1:points.append(row)
 else:ordinary.append(row)
t=canonical_dsn(t[:start]+'\n  '+sx.dumps(ordinary)+t[end:]);t,junctions=split_junctions(t)
start=t.index('\n  (wiring');end=t.rfind('\n)');wiring=sx.loads(t[start:end],nil=None,true=None,false=None)
wiring.extend(points)
for area in e['intrinsic_regions']:
 wiring.append([sx.Symbol('wire'),[sx.Symbol('polygon'),sx.Symbol(area['layer']),0,*[v for point in area['points_um'] for v in point]],[sx.Symbol('net'),area['net']],[sx.Symbol('type'),sx.Symbol('fix')]])
t=t[:start]+'\n  '+sx.dumps(wiring)+t[end:]
# Reuse the earlier fixed-via attachment strategy; it changes exchange flags,
# not native coordinates, and never authorizes newly drilled SMD lands.
tree=sx.loads(t.replace('(string_quote ")','(string_quote __QUOTE__)'),nil=None,true=None,false=None)
section=lambda name:next(v for v in tree if isinstance(v,list) and str(v[0])==name)
lib=section('library');wiring=section('wiring');structure=section('structure');clones={}
for via in wiring[1:]:
 if not isinstance(via,list) or str(via[0])!='via':continue
 mode=next((str(v[1]) for v in via if isinstance(v,list) and str(v[0])=='type'),None)
 if mode not in ('fix','protect','shove_fixed'):continue
 original=str(via[1])
 if original not in clones:
  clone=deepcopy(next(v for v in lib[1:] if str(v[0])=='padstack' and str(v[1])==original));clone[1]='SmilePCBFixedAttach'+str(len(clones)+1);clone[2:]=[v for v in clone[2:] if not(isinstance(v,list) and str(v[0])=='attach')];clone.append([sx.Symbol('attach'),sx.Symbol('on')]);lib.append(clone);clones[original]=clone[1]
 via[1]=clones[original]
structure.append([sx.Symbol('control'),[sx.Symbol('via_at_smd'),sx.Symbol('off' if controller_only or joint_board else 'on')]])
prepared='(pcb '+sx.dumps(tree[1])+'\n'+''.join('  '+sx.dumps(node)+'\n' for node in tree[2:])+'\n)'
prepared=canonical_dsn(prepared.replace('(string_quote __QUOTE__)','(string_quote ")'))
prepared,modes=freerouting_route_modes(prepared)
(route/'board.dsn').write_text(prepared)
(route/'copper-modes.json').write_text(json.dumps(modes,indent=2)+'\n')
(route/'exchange.json').write_text(json.dumps(dict(edge=edge,holes=holes,circles=polys,junctions=junctions,unchanged_native_point_wires=len(points),protected_objects=len(protected),intrinsic_regions=len(e['intrinsic_regions']),fixed_via_clones=clones,extra_clearance_margin_um=0),indent=2)+'\n')
from smilepcb.fixed_copper_exchange import fixed_copper_exchange,restore_fixed_vias,diagnosed_fixed_objects
cmd=[tools['java'],'-Djava.awt.headless=true','-Xmx512m','-XX:ActiveProcessorCount=2','-jar',tools['freerouting_jar'],'-da','--gui.enabled=false','--api_server.enabled=false','--mcp_server.enabled=false','--router.autorouter.max_threads=2','--router.optimizer.max_threads=2','--router.autorouter.max_passes=20','--router.optimizer.enabled=false','--router.optimizer.max_passes=0','--router.automatic_neckdown=false','--router.fanout.enabled=false','--logging.file.enabled=false','--router.job_timeout=00:10:00','--router.result_json='+str(route/'result.json'),'--user_data_path='+str(route/'config'),'-de',str(route/'board.dsn'),'-do',str(route/'board.ses')]
cmd += ['--usage_and_diagnostic_data.disable_analytics=true','--profile.allow_telemetry=false','--profile.allow_contact=false']
cmd += ['--router.plane_as_obstacle=true']
if with_fanout:
 cmd=[v.replace('--router.fanout.enabled=false','--router.fanout.enabled=true') for v in cmd]
 cmd += ['--router.fanout.max_passes=3','--router.fanout.timeout=45s','--router.fanout.max_milliseconds_per_pin=250',
         '--router.fanout.ripup_allowed=true','--router.fanout.start_via_diameter_mm=0.6','--router.fanout.end_via_diameter_mm=0.6']
if ignored_classes:cmd.append('--router.autorouter.ignore_net_classes='+','.join(ignored_classes))
if controller_only:cmd=[v.replace('--router.job_timeout=00:10:00','--router.job_timeout=00:02:00') for v in cmd]
(route/'command.json').write_text(json.dumps(cmd,indent=2)+'\n')
original_text=(route/'board.dsn').read_text()
(route/'prepared-input.dsn').write_text(original_text)
original_drc=cmd[:cmd.index('-do')]+['-drc',str(route/'input-drc.json')]
with (route/'input-drc.log').open('w') as log:subprocess.run(original_drc,stdout=log,stderr=subprocess.STDOUT,timeout=30,check=True)
indices=diagnosed_fixed_objects(original_text,json.loads((route/'input-drc.json').read_text()))
if indices:raise ValueError('Fixed copper is incompatible with the router; no surrogate copper or routing attempt')
fixed_text,fixed_manifest=fixed_copper_exchange(original_text,indices=indices)
(route/'board.dsn').write_text(fixed_text)
fixed_manifest['diagnosed_indices']=sorted(indices)
(route/'fixed-copper.json').write_text(json.dumps(fixed_manifest,indent=2)+'\n')
print('exact fixed objects',len(indices),flush=True)
pre=[v.replace(str(route/'result.json'),str(route/'preflight-result.json')) for v in cmd]+['--router.autorouter.enabled=false']
with (route/'preflight.log').open('w') as log:subprocess.run(pre,stdout=log,stderr=subprocess.STDOUT,timeout=30,check=True)
stats=json.loads((route/'preflight-result.json').read_text())['board_statistics'];(route/'preflight-summary.json').write_text(json.dumps(stats,indent=2)+'\n');print('router preflight',stats.get('clearance_violations'),flush=True)
conflicts=stats.get('clearance_violations',{})
if any(value for key,value in conflicts.items() if key.endswith('count')):raise ValueError('Freerouting input is not clean; no routing attempt')
if editable_routing or preflight_only:
 from recipes.stm32.verify_exchange import prove_roundtrip
 shutil.copy2(route/'board.ses',route/'preflight.ses')
 proof=prove_roundtrip(project,out/'roundtrip',route/'preflight.ses',protected)
 print('native roundtrip',proof['accepted'],'fixed',proof['protected_objects'],'editable',len(e['editable_ids']),flush=True)
 if preflight_only:raise SystemExit(0)
fanout_only='--fanout-only' in sys.argv[5:]
if fanout_only:
 # Isolate the stock SMD-escape stage; do not add another global rerouting
 # loop. Returned copper still goes through native geometry/land checks.
 cmd=[v.replace('--router.fanout.enabled=false','--router.fanout.enabled=true').replace('--router.job_timeout=00:10:00','--router.job_timeout=00:01:30') for v in cmd]
 cmd += ['--router.autorouter.enabled=false','--router.fanout.max_passes=2','--router.fanout.timeout=60s',
         '--router.fanout.max_milliseconds_per_pin=200','--router.fanout.ripup_allowed=false',
         '--router.fanout.start_via_diameter_mm=0.6','--router.fanout.end_via_diameter_mm=0.6']
 (route/'command.json').write_text(json.dumps(cmd,indent=2)+'\n')
if (route/'board.ses').exists():(route/'board.ses').unlink()
with (route/'router.log').open('w') as log:
 child=subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT);timer=threading.Timer(110 if fanout_only else 150 if controller_only else 630,child.kill);timer.start();tick=time.monotonic()
 try:_,status,usage=os.wait4(child.pid,0)
 finally:timer.cancel()
 resource={'exit_code':os.waitstatus_to_exitcode(status),'elapsed_s':time.monotonic()-tick,'peak_rss_bytes':usage.ru_maxrss*(1 if platform.system()=='Darwin' else 1024),'cpu_limit':2,'heap_mib':512}
(route/'resources.json').write_text(json.dumps(resource,indent=2)+'\n');print(resource,flush=True)
if not (route/'board.ses').exists():raise RuntimeError('Bounded router comparison produced no session')
shutil.copy2(route/'board.ses',route/'router-original.ses')
(route/'board.ses').write_text(restore_fixed_vias((route/'board.ses').read_text(),fixed_manifest))
call(Path(__file__).with_name('import_session.py'),'import_open','import',ses=str(route/'board.ses'),protected_ids=protected,replace_editable=editable_routing)
call(BRIDGE,'planes','planes')
if controller_only:
 call(BRIDGE,'stitch','controller-plane-stitch')
 proof=call(Path(__file__).with_name('check_access.py'),'check','controller-access')
try:call(Path(__file__).with_name('route_native.py'),'inspect','independent')
finally:
 checks=rule_checks(project,out/'native-checks','keyboard',timeout_s=120);print('native checks',json.dumps(checks),flush=True)
if controller_only:
 accepted=proof['accepted'] and not checks['erc']['severity'] and not checks['drc']['severity'].get('error') and not checks['drc']['schematic_parity']
 (out/'controller-result.json').write_text(json.dumps({'accepted':accepted,'scope':'controller placement feasibility only; full keyboard still requires routing and acceptance','proof':proof,'native_checks':checks,'resources':resource},indent=2)+'\n')
 print('controller placement feasibility',accepted,proof['missing'],flush=True)
