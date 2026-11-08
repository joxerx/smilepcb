"""Actual footprint flip/rotation and existing placement-oracle checks."""
import ast
from copy import deepcopy
import json
from pathlib import Path
import sys,time,io
from contextlib import redirect_stdout
from smilepcb import native_worker as w
from smilepcb.keyboard_routing.safety import require_via_land_clearance
from smilepcb.native_worker import p,SUPPORT,snapshot
from smilepcb.keyboard_placement import rgb_chain
from smilepcb.keyboard_process import apply_native_rules,COMPACT_VIAS
from smilepcb.routing_style import edge_shapes

root=Path(__file__).resolve().parents[1];out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
tree=ast.parse((root/'smilepcb/stm32/place.py').read_text())
functions={'vector','bbox','box_overlap','add_shape','silkscreen_shapes','add_obstacles','nearby','inside','clear','rebuild_obstacles','add_edge_obstacles','loaded','net','add_outlines'}
setup=compile(ast.fix_missing_locations(ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in functions],type_ignores=[])),'actual-placement-oracle','exec')
outer=next(n for n in tree.body if isinstance(n,ast.If) and ast.unparse(n.test)=='pose')
accepted=next(n for n in outer.body if isinstance(n,ast.If) and ast.unparse(n.test)=='not failed')
end=next(i for i,n in enumerate(accepted.body) if isinstance(n,ast.If) and ast.unparse(n.test)=="request.get('prepare_ordered_controller')")
final=compile(ast.fix_missing_locations(ast.Module(body=accepted.body[:end],type_ignores=[])),'actual-placement-final-block','exec')
folder=out/'KeyboardParts.pretty'

def fixture(side):
    board=p.BOARD();apply_native_rules(board,COMPACT_VIAS)
    def vector(x,y):return p.VECTOR2I(round(x),round(y))
    for a,z in zip(((-10000000,-10000000),(90000000,-10000000),(90000000,40000000),(-10000000,40000000)),((90000000,-10000000),(90000000,40000000),(-10000000,40000000),(-10000000,-10000000))):
        edge=p.PCB_SHAPE(board);edge.SetShape(p.SHAPE_T_SEGMENT);edge.SetStart(vector(*a));edge.SetEnd(vector(*z));edge.SetLayer(p.Edge_Cuts);edge.SetWidth(50000);board.Add(edge)
    nets={}
    def net(name):
        if name not in nets:nets[name]=p.NETINFO_ITEM(board,name);board.Add(nets[name])
        return nets[name]
    parts=[];aperture_owners={}
    def add(ref,fp,xy,names,recipe):
        fp.SetReference(ref);fp.SetPosition(vector(*xy));fp.SetUuid(p.KIID());board.Add(fp)
        for pad in fp.Pads():
            pad.SetUuid(p.KIID())
            if pad.GetNumber():pad.SetNet(net(names[pad.GetNumber()]))
        if side=='B.Cu' or ref=='U_MCU':fp.Flip(fp.GetPosition(),p.FLIP_DIRECTION_LEFT_RIGHT)
        if recipe:fp.Rotate(fp.GetPosition(),p.EDA_ANGLE(recipe,p.DEGREES_T))
        fp.Reference().SetVisible(False);fp.Value().SetVisible(False)
        parts.append({'ref':ref,'pcb':[v/1e6 for v in xy],'nets':names,'side':board.GetLayerName(fp.GetLayer()),'rotation':fp.GetOrientationDegrees()})
        return fp
    for i in range(1,4):
        led=add('LED'+str(i),p.FootprintLoad(str(folder),'MINI_E'),(i*20000000,15000000),{'1':'+5V_RGB','2':'old'+str(i),'3':'old'+str(i-1),'4':'GND'},180)
        add('SW'+str(i),p.FootprintLoad(str(folder),'MX_HS'),(i*20000000,9920000),{'1':'COL'+str(i),'2':'KEY'+str(i)},0)
        cut=p.FootprintLoad(str(folder),'MINI_E_CUTOUT')
        anchor=p.VECTOR2I(i*20000000,15000000)
        def transform(v):return p.VECTOR2I(-v.x+anchor.x,-v.y+anchor.y)
        for item in cut.GraphicalItems():
            if item.GetLayer()!=p.Edge_Cuts:continue
            edge=p.PCB_SHAPE(board);edge.SetShape(item.GetShape())
            if item.GetShape()==p.SHAPE_T_ARC:edge.SetArcGeometry(transform(item.GetStart()),transform(item.GetArcMid()),transform(item.GetEnd()))
            else:edge.SetStart(transform(item.GetStart()));edge.SetEnd(transform(item.GetEnd()))
            edge.SetLayer(p.Edge_Cuts);edge.SetWidth(item.GetWidth());board.Add(edge);aperture_owners[edge.m_Uuid.AsString()]=led.m_Uuid.AsString()
        add('C_LED'+str(i),p.FootprintLoad(str(SUPPORT/'footprints/Capacitor_SMD.pretty'),'C_0603_1608Metric'),(i*20000000+5800000,15000000),{'1':'+5V_RGB','2':'GND'},0)
    add('R_RGB',p.FootprintLoad(str(SUPPORT/'footprints/Resistor_SMD.pretty'),'R_0603_1608Metric'),(80000000,15000000),{'1':'RGB_BUFFER','2':'RGB_0'},0)
    # An actual B.Cu native angle180 MCU must not be used as LED pose context.
    add('U_MCU',p.FOOTPRINT(board),(80000000,30000000),{},0)
    cfg={'layout':{'keys':[{'id':i,'center_mm':[i*20,9.92],'matrix':[0,i-1]} for i in range(1,4)],'rgb':True,'rgb_order':[1,2,3],'size_mm':[80,30]},'parts':parts,'qmk':{'rgb_matrix':{'layout':[]}}}
    region=p.SHAPE_POLY_SET();assert board.GetBoardPolygonOutlines(region,False)
    env=dict(p=p,time=time,project=out,board=board,cfg=cfg,net=net,nets=nets,rgb_chain=rgb_chain,removed=set(),parts=[],width=80000000,height=30000000,
             stop=time.monotonic()+5,rules=board.GetDesignSettings(),board_region=region,outline=(-10000000,-10000000,90000000,40000000),cells={},cell=10000000,edge_shapes=edge_shapes,aperture_owners=aperture_owners)
    exec(setup,env)
    return env

def edges(board):
    return sorted((e.m_Uuid.AsString(),e.GetShape(),(e.GetStart().x,e.GetStart().y),(e.GetEnd().x,e.GetEnd().y),
                   (e.GetArcMid().x,e.GetArcMid().y) if e.GetShape()==p.SHAPE_T_ARC else None,e.GetWidth(),e.GetLayer())
                  for e in board.GetDrawings() if isinstance(e,p.PCB_SHAPE) and e.GetLayer()==p.Edge_Cuts)

report={}
for side in ('F.Cu','B.Cu'):
    env=fixture(side);board=env['board'];before=snapshot(board);edges_before=edges(board)
    try:exec(final,env)
    except ValueError as error:
        leds=[f for f in board.GetFootprints() if f.GetReference().startswith('LED')]
        details=[]
        for f in leds:
            court=f.GetCourtyard(f.GetLayer())
            details.append({'ref':f.GetReference(),'body_inside':env['inside'](court),'body_edge_collision':any(p.SHAPE.Collide(court,e,0) for e in edge_shapes(board)),
                            'pads_inside':all(env['inside'](d.GetEffectiveShape(f.GetLayer())) for d in f.Pads() if d.GetNumber()),'current_clear':env['clear']([f]),
                            'courtyard_before':env['bbox'](env['led_mounts'][f.m_Uuid.AsString()][f.GetLayer()]['poses'][0]),'courtyard_after':env['bbox'](court),
                            'vertices_before':[(env['led_mounts'][f.m_Uuid.AsString()][f.GetLayer()]['poses'][0].COutline(0).CPoint(j).x,env['led_mounts'][f.m_Uuid.AsString()][f.GetLayer()]['poses'][0].COutline(0).CPoint(j).y) for j in range(env['led_mounts'][f.m_Uuid.AsString()][f.GetLayer()]['poses'][0].COutline(0).PointCount())],
                            'vertices_after':[(court.COutline(0).CPoint(j).x,court.COutline(0).CPoint(j).y) for j in range(court.COutline(0).PointCount())]})
        report[side]={'error':str(error),'native_mounting':details,'edge_shape_count':len(list(edge_shapes(board)))}
        assert p.SaveBoard(str(out/(side+'-rejected.kicad_pcb')),board)
        continue
    after=snapshot(board);parts={x['ref']:x for x in env['cfg']['parts']}
    leds=[f for f in board.GetFootprints() if f.GetReference().startswith('LED')]
    wanted_input_x=2725000  # DIN faces the upstream eastern neighbour/driver
    actual_inputs=[next(d for d in f.Pads() if d.GetNumber()=='3').GetPosition().x-f.GetPosition().x for f in leds]
    original_other={uid:x for uid,x in before['footprints'].items() if not x['ref'].startswith('LED')}
    resulting_other={uid:x for uid,x in after['footprints'].items() if not x['ref'].startswith('LED')}
    healthy=env['clear'](leds,led_mounts=env['led_mounts'])
    # Put a real foreign pad exactly on a rotated DIN. A rebuilt current index
    # must reject it, while never treating the LED's own pads as obstacles.
    target=next(d for d in leds[0].Pads() if d.GetNumber()=='3')
    foreign=p.FOOTPRINT(board);foreign.SetReference('FOREIGN');board.Add(foreign)
    pad=p.PAD(foreign);pad.SetNumber('1');pad.SetAttribute(p.PAD_ATTRIB_SMD);pad.SetShape(p.PAD_SHAPE_RECT);pad.SetSize(p.VECTOR2I(300000,300000));pad.SetPosition(target.GetPosition());layers=p.LSET();layers.AddLayer(p.B_Cu if side=='B.Cu' else p.F_Cu);pad.SetLayerSet(layers);pad.SetNet(env['net']('foreign'));foreign.Add(pad)
    env['rebuild_obstacles']();blocked=not env['clear'](leds,led_mounts=env['led_mounts'])
    board.Delete(foreign);env['rebuild_obstacles']()
    def rejects():
        env['rebuild_obstacles']()
        return not env['clear'](leds,led_mounts=env['led_mounts'])
    # A different aperture/foreign edge through the body is never exempt.
    extra=p.PCB_SHAPE(board);extra.SetShape(p.SHAPE_T_SEGMENT);centre=leds[0].GetPosition()
    extra.SetStart(p.VECTOR2I(centre.x-100000,centre.y));extra.SetEnd(p.VECTOR2I(centre.x+100000,centre.y));extra.SetLayer(p.Edge_Cuts);extra.SetWidth(50000);board.Add(extra)
    foreign_edge=rejects();env['aperture_owners'][extra.m_Uuid.AsString()]=leds[1].m_Uuid.AsString()
    other_aperture=rejects();env['aperture_owners'].pop(extra.m_Uuid.AsString());board.Delete(extra)
    # Even an own-tagged edge must retain the full 201um pad-edge test.
    face=p.B_Cu if side=='B.Cu' else p.F_Cu;box=target.GetEffectiveShape(face).BBox()
    near=p.PCB_SHAPE(board);near.SetShape(p.SHAPE_T_SEGMENT)
    near.SetStart(p.VECTOR2I(box.GetRight()+150000,target.GetPosition().y-100000));near.SetEnd(p.VECTOR2I(box.GetRight()+150000,target.GetPosition().y+100000));near.SetLayer(p.Edge_Cuts);near.SetWidth(50000);board.Add(near)
    env['aperture_owners'][near.m_Uuid.AsString()]=leds[0].m_Uuid.AsString();own_edge_pad=rejects()
    env['aperture_owners'].pop(near.m_Uuid.AsString());board.Delete(near)
    # Native drilled geometry remains strict even inside the owned aperture.
    holefp=p.FOOTPRINT(board);holefp.SetReference('FOREIGN_HOLE');board.Add(holefp)
    hole=p.PAD(holefp);hole.SetAttribute(p.PAD_ATTRIB_NPTH);hole.SetShape(p.PAD_SHAPE_CIRCLE);hole.SetSize(p.VECTOR2I(300000,300000));hole.SetDrillSize(p.VECTOR2I(300000,300000));hole.SetPosition(centre);holefp.Add(hole)
    foreign_hole=rejects();board.Delete(holefp)
    # Actual native body translation cannot borrow either captured pose.
    leds[0].Move(p.VECTOR2I(1,0))
    try:rejects()
    except ValueError as error:moved_body='supported native poses' in str(error)
    else:moved_body=False
    leds[0].Move(p.VECTOR2I(-1,0))
    env['rebuild_obstacles']();restored=env['clear'](leds,led_mounts=env['led_mounts'])
    path=out/(side+'.kicad_pcb');assert p.SaveBoard(str(path),board)
    saved=p.LoadBoard(str(path))
    saved_exact=snapshot(saved)==after
    from smilepcb.keyboard_routing.local_router import LocalRouter
    from smilepcb.keyboard_routing.native_graph import copper_graph
    local_before=snapshot(saved);router=LocalRouter(saved,compact=True,deadline=time.monotonic()+5)
    fps={f.GetReference():f for f in saved.GetFootprints()};connected=[]
    for i in range(1,4):
        led_pads={d.GetNumber():d for d in fps['LED'+str(i)].Pads()}
        cap_pads={d.GetNumber():d for d in fps['C_LED'+str(i)].Pads()}
        for a,b in (('1','1'),('4','2')):connected.append(router.connect(led_pads[a],cap_pads[b]))
    graph=copper_graph(saved,{'+5V_RGB','GND'},time.monotonic()+2)
    roots={n['item'].m_Uuid.AsString():graph['find'](j) for j,n in enumerate(graph['nodes'])}
    actual_power=[]
    for i in range(1,4):
        lp={d.GetNumber():d for d in fps['LED'+str(i)].Pads()};cp={d.GetNumber():d for d in fps['C_LED'+str(i)].Pads()}
        for a,b in (('1','1'),('4','2')):actual_power.append(roots[lp[a].m_Uuid.AsString()]==roots[cp[b].m_Uuid.AsString()])
    report[side]={'order':env['cfg']['layout']['rgb_order'],'data_inputs_face_upstream':all(x==wanted_input_x for x in actual_inputs),
                  'centres_exact':all(after['footprints'][uid]['position']==item['position'] for uid,item in before['footprints'].items()),
                  'other_parts_exact':original_other==resulting_other,'manufacturing_edges_exact':edges(board)==edges_before,
                  'native_clear':healthy,'foreign_overlap_rejected':blocked,'foreign_edge_rejected':foreign_edge,
                  'other_aperture_rejected':other_aperture,'own_aperture_pad_gap_rejected':own_edge_pad,
                  'foreign_hole_rejected':foreign_hole,'changed_native_body_rejected':moved_body,'negative_controls_restored':restored,
                  'native_cfg_angles_match':all(parts[f.GetReference()]['rotation']==f.GetOrientationDegrees() for f in leds),
                  'save_reload_exact':saved_exact,'track_count':len(list(board.GetTracks())),
                  'local_power_pairs_connected':all(connected) and all(actual_power),'local_power_footprints_exact':snapshot(saved)['footprints']==local_before['footprints']}
# Real materialized non-RGB ST40 model through actual outline + complete
# accepted-placement save block. Packing/ordered routing remain separate gates.
full=compile(ast.fix_missing_locations(ast.Module(body=[*accepted.body,*tree.body[-2:]],type_ignores=[])),'actual-no-rgb-accepted-placement','exec')
cfg=json.loads((out/'st40-model.json').read_text());original_order=list(cfg['layout']['rgb_order']);original_qmk=deepcopy(cfg['qmk'])
board=p.BOARD();apply_native_rules(board,COMPACT_VIAS)
env=dict(p=p,w=w,board=board,project=out,cfg=cfg,nets={},aperture_owners={},time=time,json=json,
         removed=set(),parts=[],width=round(cfg['layout']['size_mm'][0]*1e6),height=round(cfg['layout']['size_mm'][1]*1e6),
         stop=time.monotonic()+5,started=time.monotonic(),request={},result={'stage':'placement','status':'incomplete'},
         source=board,require_via_land_clearance=require_via_land_clearance)
exec(setup,env)
for part in cfg['parts']:
    if not part['ref'].startswith('#'):env['loaded'](part)
env['add_outlines']();region=p.SHAPE_POLY_SET();valid=board.GetBoardPolygonOutlines(region,False)
before=snapshot(board);drawings=edges(board)
with redirect_stdout(io.StringIO()) as printed:exec(full,env)
result=json.loads(printed.getvalue());saved=p.LoadBoard(str(out/'keyboard.kicad_pcb'))
report['no_rgb']={'placed':result['status']=='placed','valid_board_region':valid,
    'four_outer_edges_only':len(edges(board))==4,'no_aperture_owners':not env['aperture_owners'],
    'no_led_or_driver':not any(f.GetReference().startswith('LED') or f.GetReference()=='R_RGB' for f in board.GetFootprints()),
    'dormant_order_preserved':cfg['layout']['rgb_order']==original_order and len(original_order)==48,
    'qmk_exact':cfg['qmk']==original_qmk and 'rgb_matrix' not in cfg['qmk'],
    'native_geometry_exact':snapshot(board)==before and edges(board)==drawings,'save_reload_exact':snapshot(saved)==before}
(out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
