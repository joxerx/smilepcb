"""Real cleanup/partition behavior; routing itself is outside this fixture."""
import hashlib,json,shutil,sys,time
from pathlib import Path
from collections import defaultdict
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_recovery import recover_generated
from smilepcb.keyboard_routing.cleanup import cleanup_generated
from smilepcb.keyboard_routing.native_graph import copper_graph

out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
board=p.BOARD()
def v(x,y):return p.VECTOR2I(p.FromMM(x),p.FromMM(y))
for name in ('READY','WAITING','BRIDGE'):board.Add(p.NETINFO_ITEM(board,name))
for a,b in (((0,0),(20,0)),((20,0),(20,20)),((20,20),(0,20)),((0,20),(0,0))):
    edge=p.PCB_SHAPE(board);edge.SetShape(p.SHAPE_T_SEGMENT);edge.SetStart(v(*a));edge.SetEnd(v(*b));edge.SetLayer(p.Edge_Cuts);edge.SetWidth(50000);board.Add(edge)
def pad(ref,net,x,y,layer):
    fp=p.FOOTPRINT(board);fp.SetReference(ref);board.Add(fp)
    d=p.PAD(fp);d.SetNumber('1');d.SetAttribute(p.PAD_ATTRIB_SMD);d.SetShape(p.PAD_SHAPE_RECT)
    layers=p.LSET();layers.AddLayer(layer);d.SetLayerSet(layers);d.SetSize(v(.5,.5));d.SetPosition(v(x,y));d.SetNet(board.FindNet(net));fp.Add(d)
def track(net,a,b,layer=p.F_Cu):
    t=p.PCB_TRACK(board);t.SetStart(v(*a));t.SetEnd(v(*b));t.SetWidth(150000);t.SetLayer(layer);t.SetNet(board.FindNet(net));board.Add(t);return t.m_Uuid.AsString()
def via(net,point):
    t=p.PCB_VIA(board);t.SetPosition(v(*point));t.SetWidth(600000);t.SetDrill(300000);t.SetLayerPair(p.F_Cu,p.B_Cu);t.SetNet(board.FindNet(net));board.Add(t);return t.m_Uuid.AsString()
pad('R1','READY',2,2,p.F_Cu);pad('R2','READY',8,2,p.F_Cu)
pad('W1','WAITING',2,8,p.F_Cu);pad('W2','WAITING',8,8,p.F_Cu)
pad('B1','BRIDGE',2,14,p.F_Cu);pad('B2','BRIDGE',8,14,p.B_Cu)
track('READY',(2,2),(8,2));ready_tail=track('READY',(5,2),(5,4));ready_via=via('READY',(5,4))
pending_track=track('WAITING',(2,8),(4,8));pending_via=via('WAITING',(4,8))
track('BRIDGE',(2,14),(5,14));bridge=via('BRIDGE',(5,14));track('BRIDGE',(5,14),(8,14),p.B_Cu)
source=out/'source.kicad_pcb';assert p.SaveBoard(str(source),board,True)
source_hash=hashlib.sha256(source.read_bytes()).hexdigest()
project=out/'project';project.mkdir();path=project/'keyboard.kicad_pcb';shutil.copy2(source,path)
(project/'.smilepcb-keyboard.json').write_text(json.dumps({'plane_nets':[]}))
owned=[ready_tail,ready_via,pending_track,pending_via,bridge]
def inspect():
    b=p.LoadBoard(str(path));g=copper_graph(b,{'READY','WAITING','BRIDGE'},time.monotonic()+15)
    groups=defaultdict(set);partition=defaultdict(set)
    for i,node in enumerate(g['nodes']):
        item=node['item']
        if isinstance(item,p.PAD):
            groups[node['net']].add(g['find'](i));partition[node['net'],g['find'](i)].add(item.GetParentFootprint().GetReference())
    missing={net:len(roots)-1 for net,roots in groups.items() if len(roots)>1}
    parts=sorted((net,sorted(refs)) for (net,_),refs in partition.items())
    return {'pad_group_gaps':sum(missing.values()),'pad_groups_missing':missing},parts,snapshot(b)
previous,original_partition,before=inspect()
class StopBeforeGeometricSearch(Exception):pass
def announce(stage):
    if stage.startswith('geometric completion'):raise StopBeforeGeometricSearch()
def call(operation,**kw):
    if operation=='verify':return inspect()[0]
    if operation=='cleanup':
        b=p.LoadBoard(str(path));result=cleanup_generated(b,protected=set(kw['protected_ids']));assert p.SaveBoard(str(path),b,True);return result
    if operation in ('components','stitch'):return {'connections':0}
    raise AssertionError('Unexpected operation: '+operation)
try:
    recover_generated(project,out,previous,[],call,announce,time.perf_counter()+120,port_ids=owned)
except StopBeforeGeometricSearch:pass
final,partition,after=inspect()
report={'completed_tail_removed':not({ready_tail,ready_via}&after['tracks'].keys()),
        'pending_escape_unchanged':all(after['tracks'].get(uid)==before['tracks'][uid] for uid in (pending_track,pending_via)),
        'necessary_two_face_via_unchanged':after['tracks'].get(bridge)==before['tracks'][bridge],
        'pad_partition_unchanged':partition==original_partition,
        'remaining_gaps':final['pad_groups_missing'],
        'source_unchanged':hashlib.sha256(source.read_bytes()).hexdigest()==source_hash}
(out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
