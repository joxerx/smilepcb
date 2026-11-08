"""Tiny actual-copper controls for the optional two-pad face proposal."""
import hashlib,json,shutil,sys,time
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_process import COMPACT_VIAS,apply_native_rules,project_settings
from smilepcb.keyboard_routing.generation import generation_profile
from smilepcb.keyboard_routing.interface_launches import reserve_launches
from smilepcb.keyboard_routing.local_router import vec
from smilepcb.keyboard_routing.matrix import KeyboardRouter
from smilepcb.keyboard_routing.native_graph import copper_graph,route_components
from smilepcb.keyboard_routing.cleanup import cleanup_generated
from smilepcb.routing_quality import using_quality_profile

out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True);probe_stop=time.monotonic()+10
def v(x,y):return p.VECTOR2I(p.FromMM(x),p.FromMM(y))
def track(board,a,b,face):
    t=p.PCB_TRACK(board);t.SetStart(v(*a));t.SetEnd(v(*b));t.SetWidth(150000);t.SetLayer(face);t.SetNet(board.FindNet('wire'));board.Add(t);return t

def make(face,blocked,source_guard,tag,*,different=False,compound=False):
    b=p.BOARD();apply_native_rules(b,COMPACT_VIAS)
    for net in ('wire','barrier'):b.Add(p.NETINFO_ITEM(b,net))
    for a,z in (((0,0),(30,0)),((30,0),(30,30)),((30,30),(0,30)),((0,30),(0,0))):
        edge=p.PCB_SHAPE(b);edge.SetShape(p.SHAPE_T_SEGMENT);edge.SetStart(v(*a));edge.SetEnd(v(*z));edge.SetLayer(p.Edge_Cuts);edge.SetWidth(50000);b.Add(edge)
    pads=[]
    for ref,x in (('A',10),('B',20)):
        f=p.FOOTPRINT(b);f.SetReference(ref);b.Add(f);d=p.PAD(f);d.SetNumber('1');d.SetAttribute(p.PAD_ATTRIB_SMD);d.SetShape(p.PAD_SHAPE_RECT);d.SetSize(v(.6,.6));d.SetPosition(v(x,15));d.SetNet(b.FindNet('wire'));layers=p.LSET();layers.AddLayer((p.B_Cu if face==p.F_Cu else p.F_Cu) if different and ref=='B' else face);d.SetLayerSet(layers);f.Add(d);pads.append(d)
    access=[]
    for x,z in ((10,11),(20,19)):
        t=track(b,(x,15),(z,15),(p.B_Cu if face==p.F_Cu else p.F_Cu) if different and x==20 else face);via=p.PCB_VIA(b);via.SetPosition(v(z,15));via.SetWidth(500000);via.SetDrill(200000);via.SetViaType(p.VIATYPE_THROUGH);via.SetLayerPair(p.F_Cu,p.B_Cu);via.SetNet(b.FindNet('wire'));b.Add(via);access.extend((t.m_Uuid.AsString(),via.m_Uuid.AsString()))
    if compound:
        f=pads[0].GetParentFootprint();d=p.PAD(f);d.SetNumber('1');d.SetAttribute(p.PAD_ATTRIB_SMD);d.SetShape(p.PAD_SHAPE_RECT);d.SetSize(v(.6,.6));d.SetPosition(v(10,14));d.SetNet(b.FindNet('wire'));layers=p.LSET();layers.AddLayer(face);d.SetLayerSet(layers);f.Add(d);track(b,(10,14),(10,15),face)
    ports=[]
    if source_guard:
        terminal=track(b,(10,15),(12,15),face)
        ports=[{'net':'wire','pad_ids':[pads[0].m_Uuid.AsString()],'added_ids':access[:2]+[terminal.m_Uuid.AsString()],
                'terminal_id':terminal.m_Uuid.AsString(),'exit_nm':[12000000,15000000],'heading_origin_nm':[10000000,15000000],
                'exit_layer':face,'axis':0,'sign':1,'kind':'same_layer','via_id':None,'via_nm':None}]
    if blocked:
        f=p.FOOTPRINT(b);f.SetReference('OBSTACLE');b.Add(f);d=p.PAD(f);d.SetNumber('1');d.SetAttribute(p.PAD_ATTRIB_SMD);d.SetShape(p.PAD_SHAPE_RECT);d.SetSize(v(1,28));d.SetPosition(v(15,15));d.SetNet(b.FindNet('barrier'));layers=p.LSET();layers.AddLayer(face);d.SetLayerSet(layers);f.Add(d)
    source=out/(tag+'-source.kicad_pcb');source.with_suffix('.kicad_pro').write_text(json.dumps(project_settings(COMPACT_VIAS)))
    assert p.SaveBoard(str(source),b,True);source_hash=hashlib.sha256(source.read_bytes()).hexdigest();b=p.LoadBoard(str(source));return b,source,source_hash,access,ports

def connected(b,deadline):
    graph=copper_graph(b,{'wire'},deadline);roots={graph['find'](i) for i,n in enumerate(graph['nodes']) if isinstance(n['item'],p.PAD) and n['item'].GetNumber()};return len(roots)==1

def run(face,blocked=False,source_guard=False,*,different=False,compound=False):
    tag=('front' if face==p.F_Cu else 'back')+('-different' if different else '-compound' if compound else '-blocked' if blocked else '-source' if source_guard else '-simple')
    b,source,digest,access,ports=make(face,blocked,source_guard,tag,different=different,compound=compound);before=snapshot(b);stop=min(probe_stop,time.monotonic()+5)
    def own_clearances():return {item.m_Uuid.AsString()+'|'+str(f):[item.GetOwnClearance(f),item.GetLocalClearance() or 0] for item in [*b.GetTracks(),*(d for fp in b.GetFootprints() for d in fp.Pads())] for f in (p.F_Cu,p.B_Cu) if item.IsOnLayer(f)}
    clearances=own_clearances()
    with generation_profile('dsn-process-v2'),using_quality_profile('turns'),reserve_launches(ports):
        router=KeyboardRouter(b,deadline=stop,compact=True,track_width_nm=150000);router.grid_step=250000;router.connection_budget_s=2;router.search_budget_s=2
        joined=route_components(b,router,{'wire'});after=snapshot(b);added=[t for t in b.GetTracks() if t.m_Uuid.AsString() in router.added]
        row={'joined':joined,'native_connected':connected(b,stop),'attempts':router.graph_diagnostic['attempts'],
             'added_layers':sorted({t.GetLayer() for t in added}),'new_vias':sum(isinstance(t,p.PCB_VIA) for t in added),
             'original_copper_exact':all(after['tracks'].get(uid)==shape for uid,shape in before['tracks'].items()),'footprints_exact':after['footprints']==before['footprints'],
             'original_clearances_exact':all(own_clearances().get(uid)==value for uid,value in clearances.items()),
             'all_new_paths_physically_clear':all(router.clear(t.GetEffectiveShape(t.GetLayer()),t.GetLayer(),'wire') for t in added),
             'source_file_exact':hashlib.sha256(source.read_bytes()).hexdigest()==digest}
        if ports:
            # Read-only entry-live admission proof even after native closure.
            router.port_policy.set_resolved(set())
            row['entry_live_admission_exact']=all(router.port_policy.accepts_segment((t.GetStart().x,t.GetStart().y),(t.GetEnd().x,t.GetEnd().y),t.GetLayer(),'wire',t.GetWidth()) for t in added)
            row['raw_pad_shortcut_rejected']=not router.port_policy.accepts_segment((10000000,15000000),(20000000,15000000),face,'wire',150000)
        else:
            # Existing production cleanup may retire explicitly fixture-owned
            # access only when a real alternative native contact exists.
            protected=set(before['tracks'])-set(access);clean=cleanup_generated(b,protected=protected,deadline=stop)
            row.update(cleanup=clean,native_connected_after_cleanup=connected(b,stop),remaining_access_vias=sum(isinstance(t,p.PCB_VIA) and t.m_Uuid.AsString() in access for t in b.GetTracks()))
    saved=out/(tag+'-after.kicad_pcb');shutil.copyfile(source.with_suffix('.kicad_pro'),saved.with_suffix('.kicad_pro'));assert p.SaveBoard(str(saved),b,True)
    row['saved_native_connected']=connected(p.LoadBoard(str(saved)),min(probe_stop,time.monotonic()+2))
    return tag,row

report=dict(run(face,**mode) for face in (p.F_Cu,p.B_Cu) for mode in ({},{'blocked':True},{'source_guard':True},{'different':True},{'compound':True}))
(out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:{q:v[q] for q in ('joined','native_connected','added_layers','new_vias','original_copper_exact')} for k,v in report.items()}))
