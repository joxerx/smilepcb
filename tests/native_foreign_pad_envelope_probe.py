"""Prepared native PAD/contact coupons; exercise in a fresh project process."""
import json,math,sys,time
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_process import project_settings,apply_native_rules
from smilepcb.keyboard_routing.local_router import LocalRouter,vec,xy
from smilepcb.keyboard_routing.generation import generation_profile
from smilepcb.keyboard_routing.native_graph import copper_graph
from smilepcb.keyboard_routing.safety import require_via_land_clearance

OUT=Path(sys.argv[1]);OUT.mkdir(parents=True,exist_ok=True)
def own(board):
    return{i.m_Uuid.AsString()+'|'+str(face):[i.GetOwnClearance(face),i.GetLocalClearance()or 0]
        for i in[*board.GetTracks(),*(d for f in board.GetFootprints()for d in f.Pads())]
        for face in(p.F_Cu,p.B_Cu)if i.IsOnLayer(face)}
def groups(board):
    graph=copper_graph(board,{'fixed'},time.monotonic()+3);rows={}
    for i,node in enumerate(graph['nodes']):
        if isinstance(node['item'],p.PAD)and node['item'].GetNumber():rows.setdefault(graph['find'](i),set()).add(node['item'].m_Uuid.AsString())
    return{frozenset(q)for q in rows.values()}
def make(folder,face):
    folder.mkdir();path=folder/'keyboard.kicad_pcb';settings=project_settings('fr4-2l-1oz-0p5-0p2')
    settings['board']['design_settings']['rules'].update(min_hole_clearance=.25,min_hole_to_hole=.25)
    path.with_suffix('.kicad_pro').write_text(json.dumps(settings))
    board=p.BOARD();board.SetCopperLayerCount(2);apply_native_rules(board,'fr4-2l-1oz-0p5-0p2');board.GetDesignSettings().m_HoleClearance=250000
    for net in('fixed','foreign','obstacle','single','npth','high'):board.Add(p.NETINFO_ITEM(board,net))
    corners=[(0,0),(60000000,0),(60000000,50000000),(0,50000000)]
    for a,z in zip(corners,corners[1:]+corners[:1]):
        edge=p.PCB_SHAPE(board);edge.SetShape(p.SHAPE_T_SEGMENT);edge.SetStart(vec(a));edge.SetEnd(vec(z));edge.SetWidth(50000);edge.SetLayer(p.Edge_Cuts);board.Add(edge)
    fp=p.FOOTPRINT(board);fp.SetReference('COUPON');fp.SetFPID(p.LIB_ID('Fixture','PAD'));fp.SetLayer(face);board.Add(fp)
    def pad(number,net,at,kind,size,*,pth=False,attribute=None,angle=0):
        d=p.PAD(fp);d.SetNumber(number);d.SetNet(board.FindNet(net));d.SetPosition(vec(at));d.SetShape(kind);d.SetSize(vec(size));d.SetOrientationDegrees(angle)
        d.SetAttribute(p.PAD_ATTRIB_PTH if pth else p.PAD_ATTRIB_SMD if attribute is None else attribute)
        layers=p.LSET.AllCuMask()if pth or attribute==p.PAD_ATTRIB_NPTH else p.LSET()
        if not pth and attribute!=p.PAD_ATTRIB_NPTH:layers.AddLayer(face)
        d.SetLayerSet(layers)
        if pth or attribute==p.PAD_ATTRIB_NPTH:d.SetDrillSize(vec((600000,600000)))
        fp.Add(d);return d
    circle=pad('1','fixed',(10000000,10000000),p.PAD_SHAPE_CIRCLE,(3300000,3300000),pth=True)
    aliases=[circle,pad('1','fixed',(11800000,10000000),p.PAD_SHAPE_OVAL,(1650000,2500000)),pad('1','fixed',(13550000,10000000),p.PAD_SHAPE_ROUNDRECT,(2550000,2500000))]
    field=pad('2','fixed',(22000000,10000000),p.PAD_SHAPE_CIRCLE,(1000000,1000000),pth=True)
    probe=pad('9','fixed',(8360000,10680000),p.PAD_SHAPE_RECT,(20000,20000))
    single=pad('3','single',(40000000,10000000),p.PAD_SHAPE_CIRCLE,(500000,500000))
    pad('F1','foreign',(45000000,45000000),p.PAD_SHAPE_RECT,(300000,300000))
    pad('F2','foreign',(50000000,45000000),p.PAD_SHAPE_RECT,(300000,300000))
    high=pad('4','high',(50000000,40000000),p.PAD_SHAPE_RECT,(500000,500000));high.SetLocalClearance(250000)
    npth=pad('','npth',(30000000,23000000),p.PAD_SHAPE_CIRCLE,(1750000,1750000),attribute=p.PAD_ATTRIB_NPTH);npth.SetDrillSize(vec((1750000,1750000)))
    for i,kind in enumerate((p.PAD_SHAPE_RECT,p.PAD_SHAPE_OVAL,p.PAD_SHAPE_TRAPEZOID,p.PAD_SHAPE_ROUNDRECT,p.PAD_SHAPE_CHAMFERED_RECT,p.PAD_SHAPE_CUSTOM)):
        d=pad(str(10+i),'obstacle',(5000000+i*8000000,35000000),kind,(2000000,1000000),angle=17+i*11)
        if kind==p.PAD_SHAPE_TRAPEZOID:d.SetDelta(vec((200000,100000)))
        if kind in(p.PAD_SHAPE_ROUNDRECT,p.PAD_SHAPE_CHAMFERED_RECT):d.SetRoundRectRadiusRatio(.25)
        if kind==p.PAD_SHAPE_CHAMFERED_RECT:d.SetChamferRectRatio(.2);d.SetChamferPositions(15)
        if kind==p.PAD_SHAPE_CUSTOM:
            d.SetAnchorPadShape(p.PADSTACK.ALL_LAYERS,p.PAD_SHAPE_RECT);poly=p.SHAPE_POLY_SET();poly.NewOutline()
            for point in((-1200000,-300000),(0,-700000),(1200000,-300000),(900000,600000),(-900000,600000)):poly.Append(vec(point))
            d.AddPrimitivePoly(p.PADSTACK.ALL_LAYERS,poly,0,True)
    other=p.B_Cu if face==p.F_Cu else p.F_Cu
    for a,z,layer in(((10000000,10000000),(18000000,10000000),face),((18000000,10000000),(22000000,10000000),other)):
        t=p.PCB_TRACK(board);t.SetStart(vec(a));t.SetEnd(vec(z));t.SetLayer(layer);t.SetWidth(150000);t.SetNet(board.FindNet('fixed'));board.Add(t)
    via=p.PCB_VIA(board);via.SetPosition(vec((18000000,10000000)));via.SetWidth(500000);via.SetDrill(200000);via.SetLayerPair(p.F_Cu,p.B_Cu);via.SetNet(board.FindNet('fixed'));board.Add(via)
    board.BuildConnectivity();assert p.SaveBoard(str(path),board)
    (folder/'fixture.json').write_text(json.dumps({'face':face,'circle':circle.m_Uuid.AsString(),'single':single.m_Uuid.AsString(),'probe':probe.m_Uuid.AsString(),'alias_ids':[d.m_Uuid.AsString()for d in aliases]+[field.m_Uuid.AsString()]}))

def exercise(folder):
    path=folder/'keyboard.kicad_pcb';original=path.read_bytes();board=p.LoadBoard(str(path));cfg=json.loads((folder/'fixture.json').read_text())
    foreign=board.FindNet('foreign');assert foreign is not None and foreign.GetNetname()=='foreign','Fixture foreign net must survive through genuine numbered pads'
    before=snapshot(board);clearances=own(board);partitions=groups(board);face=cfg['face'];other=p.B_Cu if face==p.F_Cu else p.F_Cu
    assert board.GetDesignSettings().m_TrackMinWidth==150000 and board.GetDesignSettings().m_HoleClearance==250000
    assert sorted({v[0]for v in clearances.values()})==[150000,250000]
    source=next(d for f in board.GetFootprints()for d in f.Pads()if d.m_Uuid.AsString()==cfg['circle'])
    witness=[(10491250,7855000),(10991250,8355000)];positive=[(x,y-300000)for x,y in witness]
    capsule=lambda points:p.SHAPE_SEGMENT(vec(points[0]),vec(points[1]),150000)
    report={'foreign_native_net_binding_verified':True,'fresh_rules_150_250':True,'native_alias_and_opposite_face_via_contact':any(set(cfg['alias_ids'])<=row for row in partitions),
            'virtual_probe_not_electrically_joined':all(not(set(cfg['alias_ids'])<=row and cfg['probe']in row)for row in partitions)}
    for profile in('native','dsn-safe-v1','dsn-process-v2'):
        with generation_profile(profile):router=LocalRouter(board,deadline=time.monotonic()+5,compact=True,track_width_nm=150000)
        clear=router.clear(capsule(witness),face,'foreign')
        if profile!='dsn-process-v2':
            report[profile+'_native_witness_clear']=clear
            if profile=='native':native_baseline=router
            continue
        report['v2_imported_foreign_rejected']=not clear
        report['same_net_uses_native_contacts']=router.clear(capsule(witness),face,'fixed')
        report['other_face_smd_remains_free']=router.clear(p.SHAPE_SEGMENT(vec((39500000,10000000)),vec((40500000,10000000)),150000),other,'foreign')
        report['one_bucket_entry_per_uid']=all(len([q for q in rows if q[0]==cfg['circle']])<=1 for rows in router.buckets[face].values())
        single=next(d for f in board.GetFootprints()for d in f.Pads()if d.m_Uuid.AsString()==cfg['single'])
        pad_radius=single.GetSize(face).x//2;via_radius=router.via_width//2;guard=router.generation.via_to_smd_clearance_nm
        distance=pad_radius+via_radius+guard+7000;theta=math.pi/8;center=xy(single.GetPosition())
        at=tuple(center[i]+round(distance*f)for i,f in enumerate((math.cos(theta),math.sin(theta))))
        gap=math.dist(center,at)-pad_radius-via_radius
        assert router.generation.name=='dsn-process-v2'and gap>guard
        report['solder_land_control_geometry']={'pad_radius_nm':pad_radius,'via_radius_nm':via_radius,'captured_guard_nm':guard,'nominal_distance_nm':distance,'actual_annulus_gap_nm':gap}
        report['native_baseline_solder_control_clear']=native_baseline.via_clear(at,'single')
        assert report['native_baseline_solder_control_clear'],'Positive control must first pass unchanged native physics'
        report['native_same_net_solder_land_retained']=router.via_clear(at,'single')
        shape=next(q[1]for rows in router.buckets[face].values()for q in rows if q[0]==cfg['circle'])
        report['virtual_envelope_does_not_close_native_probe']=(p.SHAPE.Collide(p.SHAPE_CIRCLE(vec((8360000,10680000)),0),shape,0)and report['virtual_probe_not_electrically_joined'])
        polygon=shape if isinstance(shape,p.SHAPE_POLY_SET)else None
        report['envelope_points_nm']=[xy(polygon.COutline(0).CPoint(i))for i in range(polygon.COutline(0).PointCount())]if polygon else []
        all_contained=True
        for d in(pad for f in board.GetFootprints()for pad in f.Pads()if pad.GetAttribute()!=p.PAD_ATTRIB_NPTH and pad.IsOnLayer(face)):
            indexed=next(q[1]for rows in router.buckets[face].values()for q in rows if q[0]==d.m_Uuid.AsString())
            outside=p.SHAPE_POLY_SET();d.TransformShapeToPolygon(outside,face,0,1000,p.ERROR_OUTSIDE)
            all_contained&=all(p.SHAPE.Collide(p.SHAPE_CIRCLE(outside.COutline(j).CPoint(k),0),indexed,0)
                for j in range(outside.OutlineCount())for k in range(outside.COutline(j).PointCount()))
        report['all_real_rotated_shape_outlines_contained']=all_contained
        snap=snapshot(board)
        unsafe_path=router.leg(*witness,face,'foreign')
        report['unsafe_candidate_path_nm']=unsafe_path
        report['planner_refuses_unsafe_endpoint']=(unsafe_path is None and snapshot(board)==snap and not router.added)
        with generation_profile('dsn-process-v2'):
            positive_router=LocalRouter(board,deadline=time.monotonic()+5,compact=True,track_width_nm=150000)
        positive_path=positive_router.leg(*positive,face,'foreign')
        report['positive_outward_route_allowed']=positive_router.clear(capsule(positive),face,'foreign')and positive_path is not None
        assert report['positive_outward_route_allowed'],'Native-clear translated positive must remain planner-admissible'
        positive_router.add_path(positive_path,face,'foreign');after=snapshot(board)
        emitted=[t for t in board.GetTracks()if t.m_Uuid.AsString()not in before['tracks']]
        report['actual_emitted_foreign_net_verified']=bool(emitted)and all(t.GetNetname()=='foreign'for t in emitted)
        assert report['actual_emitted_foreign_net_verified'],'Emitted positive copper must carry the actual foreign net'
        report['original_copper_pads_rules_contacts_exact']=(after['footprints']==before['footprints']and all(after['tracks'].get(u)==v for u,v in before['tracks'].items())
            and all(own(board)[u]==v for u,v in clearances.items())and groups(board)==partitions)
        candidate=folder/'accepted';candidate.mkdir();candidate.joinpath('keyboard.kicad_pro').write_bytes(path.with_suffix('.kicad_pro').read_bytes())
        assert p.SaveBoard(str(candidate/'keyboard.kicad_pcb'),board);saved=p.LoadBoard(str(candidate/'keyboard.kicad_pcb'))
        report['saved_native_geometry_exact']=snapshot(saved)==after;require_via_land_clearance(saved)
        report['native_solder_land_gate_passed']=True
    report['source_file_byte_exact']=path.read_bytes()==original
    report['source_center_nm']=list(xy(source.GetPosition()))
    assert p.ExportSpecctraDSN(board,str(folder/'raw.dsn'))
    return report

if len(sys.argv)>2 and sys.argv[2]=='--prepare':
    for name,face in(('front',p.F_Cu),('back',p.B_Cu)):make(OUT/name,face)
else:
    report={name:exercise(OUT/name)for name in('front','back')}
    (OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n')
