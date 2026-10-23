"""STM32 recipe support: route global. Native checks are required."""
import sys,json,shutil,subprocess,time,threading,os,platform
from copy import deepcopy
from pathlib import Path
import sexpdata as sx
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from smilepcb.keyboard_tools import load_tools,configure_kicad
tools=load_tools(sys.argv[1]);base=ROOT/'.lab/public-controller'
for tag in sys.argv[2:4]:
 if not tag or any(part in ('','..','.') for part in tag.split('/')) or Path(tag).is_absolute():
  raise ValueError('Invalid recipe path')
out=base/sys.argv[2];out.mkdir(exist_ok=False);configure_kicad(tools,out)
from smilepcb import schematic as s
from smilepcb.lab_tools import worker,rule_checks
from smilepcb.keyboard_build import BRIDGE
from smilepcb.routing_exchange import canonical_dsn,split_junctions,explicit_plane_routing,freerouting_route_modes
from smilepcb.keyboard_exchange import adapt_npth_keepouts,polygonize_circles,guard_milled_edges
from smilepcb.keyboard_routing.generated_seed import validate_manifest
source=(base/sys.argv[3]/'project').resolve()
if not source.is_relative_to(base.resolve()):raise ValueError('Seed path escapes the recipe workspace')
seed_receipt=validate_manifest(source)
project=out/'project';shutil.copytree(source,project);board=project/'keyboard.kicad_pcb'
(out/'source-seed.json').write_text(json.dumps(seed_receipt,indent=2)+'\n')
initial_tree=s.read(board)
net_names={n[1]:n[2] for n in initial_tree if s.tag(n)=='net' and len(n)>2}
core_ids={str(s.one(node,'uuid')[1]) for node in initial_tree if s.tag(node) in ('segment','via','arc') and (s.tag(node)=='via' or net_names.get(s.one(node,'net')[1],s.one(node,'net')[1]) in ('USB_DP','USB_DM'))}
from smilepcb.keyboard_recovery import controller_interface_ids
core_ids.update(controller_interface_ids(source))
from smilepcb.keyboard_routing.seed_junctions import ambiguous_junction_ids,endpoint_protection_closure
source_segments={}
for node in initial_tree:
 if s.tag(node)!='segment':continue
 source_segments[str(s.one(node,'uuid')[1])]={'class':'PCB_TRACK',
  'start':[round(float(v)*1e6) for v in s.one(node,'start')[1:]],
  'end':[round(float(v)*1e6) for v in s.one(node,'end')[1:]],
  'net':net_names.get(s.one(node,'net')[1],s.one(node,'net')[1]),'layer':str(s.one(node,'layer')[1]),
  'width':round(float(s.one(node,'width')[1])*1e6)}
ambiguous=ambiguous_junction_ids(source_segments,quantum_nm=100,deadline=time.monotonic()+5)
core_ids.update(ambiguous)
(out/'protected-junctions.json').write_text(json.dumps({'ids':sorted(ambiguous),'exchange_quantum_nm':100,
 'reason':'native same-net interior crossings outside the declared DSN integer grid; coordinates remain exact'},indent=2)+'\n')
for name in ('controller-ports.json','key-data-access.json','controller-return-tree.json'):
 if (source.parent/name).exists():shutil.copy2(source.parent/name,out/name)
def probe_script(name):return ROOT/'recipes/stm32'/name
def call(script,operation,tag,**kw):
 result=worker(script,dict(operation=operation,board=str(board),**kw),out/(tag+'.log'));(out/(tag+'.json')).write_text(json.dumps(result,indent=2)+'\n');return result
native=rule_checks(project,out/'pre-router-native','keyboard',timeout_s=120)
if native['erc']['severity'] or native['drc']['severity'].get('error') or native['drc']['schematic_parity']:raise ValueError('Local-block preparation fails native preflight')
tree=s.read(board);names={node[1]:node[2] for node in tree if s.tag(node)=='net' and len(node)>2}
copper=[node for node in tree if s.tag(node) in ('segment','via','arc')]
protected=[str(s.one(node,'uuid')[1]) for node in copper if
           str(s.one(node,'uuid')[1]) in core_ids or names.get(s.one(node,'net')[1],s.one(node,'net')[1]) in ('USB_DP','USB_DM')]
route=out/'route';route.mkdir();e=call(BRIDGE,'export_open','export',dsn=str(route/'raw.dsn'),protected_ids=protected)
# KiCad coalesces endpoint-contiguous equal-width traces into one DSN path.
# A single fixed member fixes the exported path. Retain every corresponding
# native object so a SYSTEM_FIXED omission from SES cannot delete its neighbor.
original_protected=set(e['protected_ids'])
source_copper={str(s.one(node,'uuid')[1]):{'class':{'segment':'PCB_TRACK','via':'PCB_VIA','arc':'PCB_ARC'}[s.tag(node)]}
               for node in initial_tree if s.tag(node) in ('segment','via','arc')}
source_copper.update(source_segments)
closed=endpoint_protection_closure(source_copper,original_protected,deadline=time.monotonic()+5)
continuations=closed-original_protected
if continuations:
 e=call(BRIDGE,'export_open','export-closed',dsn=str(route/'raw.dsn'),protected_ids=sorted(closed))
protected=e['protected_ids']
(route/'native-endpoint-closure.json').write_text(json.dumps({'added_ids':sorted(continuations),
 'reason':'native equal-width same-net same-face shared-endpoint protection closure; source coordinates unchanged',
 'before':len(original_protected),'after':len(protected)},indent=2)+'\n')
policy={'mode':'fresh-generated/USB/vias/interfaces/exact-junctions/native-locks',
        'source_objects':len(copper),'initial_recipe_objects':len(core_ids),
        'protected_ids':protected,'editable_ids':e['editable_ids']}
(route/'copper-policy.json').write_text(json.dumps(policy,indent=2)+'\n')
raw=(route/'raw.dsn').read_text()
if '(unit um)' not in raw or '(resolution um 10)' not in raw:raise ValueError('Junction guard requires the declared native 100nm DSN grid')
t=explicit_plane_routing(canonical_dsn(raw));t,edge=guard_milled_edges(t,native_edge_um=200)
cfg=json.loads((project/'.smilepcb-keyboard.json').read_text())
plane_nets=set(cfg['plane_nets']);ignored_classes=[]
if plane_nets:
 from smilepcb.routing_policy import prioritize_dsn
 network=sx.loads(t[t.index('\n  (network'):t.index('\n  (wiring')],nil=None,true=None,false=None)
 routed_nets={str(row[1]) for row in network[1:] if str(row[0])=='net'}-plane_nets
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
structure.append([sx.Symbol('control'),[sx.Symbol('via_at_smd'),sx.Symbol('off')]])
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
if ignored_classes:cmd.append('--router.autorouter.ignore_net_classes='+','.join(ignored_classes))
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
from recipes.stm32.verify_exchange import prove_roundtrip
shutil.copy2(route/'board.ses',route/'preflight.ses')
proof=prove_roundtrip(project,out/'roundtrip',route/'preflight.ses',protected)
if proof['accepted'] is not True:raise ValueError('Exact no-routing roundtrip is required')
print('native roundtrip',proof['accepted'],'fixed',proof['protected_objects'],'editable',len(e['editable_ids']),flush=True)
if '--preflight-only' in sys.argv[4:]:
 (out/'result.json').write_text(json.dumps({'complete':False,'scope':'exchange boundary diagnostic only',
  'source_unchanged':validate_manifest(source)==seed_receipt,'roundtrip_accepted':True},indent=2)+'\n')
 raise SystemExit(0)
if (route/'board.ses').exists():(route/'board.ses').unlink()
with (route/'router.log').open('w') as log:
 child=subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT);timer=threading.Timer(630,child.kill);timer.start();tick=time.monotonic()
 try:_,status,usage=os.wait4(child.pid,0)
 finally:timer.cancel()
 resource={'exit_code':os.waitstatus_to_exitcode(status),'elapsed_s':time.monotonic()-tick,'peak_rss_bytes':usage.ru_maxrss*(1 if platform.system()=='Darwin' else 1024),'cpu_limit':2,'heap_mib':512}
(route/'resources.json').write_text(json.dumps(resource,indent=2)+'\n');print(resource,flush=True)
if not (route/'board.ses').exists():raise RuntimeError('Bounded router comparison produced no session')
shutil.copy2(route/'board.ses',route/'router-original.ses')
(route/'board.ses').write_text(restore_fixed_vias((route/'board.ses').read_text(),fixed_manifest))
call(probe_script('import_session.py'),'import_open','import',ses=str(route/'board.ses'),protected_ids=protected,replace_editable=True)
call(BRIDGE,'planes','planes')
try:
 graph=call(probe_script('route_native.py'),'inspect','independent')
finally:
 checks=rule_checks(project,out/'native-checks','keyboard',timeout_s=120)
if checks['erc']['severity'] or checks['drc']['severity'].get('error') or checks['drc']['schematic_parity']:
 raise ValueError('Global generated route introduced native physical/ERC/parity errors')
if resource['exit_code']!=0:raise RuntimeError('Bounded global router exited unsuccessfully')
validate_manifest(source)
(out/'result.json').write_text(json.dumps({'complete':False,'scope':'generated routing diagnostic; completion and firmware pending',
 'source_unchanged':True,'generation_profile':seed_receipt['generation_profile'],'resources':resource,
 'independent':graph,'native_checks':checks,'roundtrip_accepted':proof['accepted']},indent=2)+'\n')
print('global generated route gaps',graph['pad_group_gaps'],flush=True)
