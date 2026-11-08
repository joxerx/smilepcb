"""NPTH obstacle floors must survive native fresh-board/scratch reload context."""
import json,sys,tempfile,time
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_process import apply_native_rules,project_settings
from smilepcb.copper_obstacles import own_clearance
from smilepcb.keyboard_routing.generation import generation_profile
from smilepcb.keyboard_routing.local_router import LocalRouter,vec

out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True);records=[]
for hole_floor in (250000,350000):
    board=p.BOARD();board.SetCopperLayerCount(2);apply_native_rules(board,'fr4-2l-1oz-0p5-0p2')
    board.GetDesignSettings().m_HoleClearance=hole_floor
    board.Add(p.NETINFO_ITEM(board,'signal'))
    vertices=[(0,0),(100000000,0),(100000000,50000000),(0,50000000)]
    for a,z in zip(vertices,vertices[1:]+vertices[:1]):
        edge=p.PCB_SHAPE(board);edge.SetShape(p.SHAPE_T_SEGMENT);edge.SetStart(vec(a));edge.SetEnd(vec(z));edge.SetLayer(p.Edge_Cuts);edge.SetWidth(50000);board.Add(edge)
    cases=[('npth',p.PAD_ATTRIB_NPTH,None),('npth-low',p.PAD_ATTRIB_NPTH,100000),('npth-high',p.PAD_ATTRIB_NPTH,450000),
           ('pth',p.PAD_ATTRIB_PTH,None),('smd',p.PAD_ATTRIB_SMD,None),('smd-local',p.PAD_ATTRIB_SMD,450000)]
    for index,(name,kind,override) in enumerate(cases):
        f=p.FOOTPRINT(board);f.SetReference(name);board.Add(f)
        d=p.PAD(f);d.SetNumber('' if kind==p.PAD_ATTRIB_NPTH else '1');d.SetAttribute(kind)
        d.SetShape(p.PAD_SHAPE_CIRCLE);d.SetSize(vec((3000000,3000000)));d.SetPosition(vec((15000000+index*12000000,25000000)))
        if kind==p.PAD_ATTRIB_SMD:
            faces=p.LSET();faces.AddLayer(p.F_Cu);d.SetLayerSet(faces)
        else:d.SetLayerSet(p.LSET.AllCuMask());d.SetDrillSize(vec((3000000,3000000) if kind==p.PAD_ATTRIB_NPTH else (500000,500000)))
        if kind!=p.PAD_ATTRIB_NPTH:d.SetNet(board.FindNet('signal'))
        if override is not None:d.SetLocalClearance(override)
        f.Add(d)
    def values(target):
        return {f.GetReference():{'own_nm':d.GetOwnClearance(p.F_Cu),'local_nm':d.GetLocalClearance(),
            'effective_nm':own_clearance(d,p.F_Cu,target.GetDesignSettings().m_MinClearance),'attribute':d.GetAttribute()}
            for f in target.GetFootprints() for d in f.Pads()}
    before=snapshot(board);fresh=values(board)
    path=out/('floor-'+str(hole_floor)+'.kicad_pcb');assert p.SaveBoard(str(path),board)
    path.with_suffix('.kicad_pro').write_text(json.dumps(project_settings('fr4-2l-1oz-0p5-0p2')))
    clone=p.LoadBoard(str(path));project=board.GetProject()
    if project is not None:clone.SetProject(project,True)
    clone.SetDesignSettings(board.GetDesignSettings())
    loaded=values(clone)
    assert snapshot(clone)==before and snapshot(board)==before
    expected={name:max(150000,hole_floor if kind==p.PAD_ATTRIB_NPTH else 0,override or 0)+1000 for name,kind,override in cases}
    # The physical hole index already blocks both trace and transfer witnesses.
    witnesses=[]
    with generation_profile('dsn-process-v2'):
        for target in (board,clone):
            router=LocalRouter(target,deadline=time.monotonic()+5,compact=True,track_width_nm=150000)
            outcomes={'via_blocked':not router.via_clear((16900000,25000000),'signal'),
                'via_clear':router.via_clear((17400000,25000000),'signal'),
                'track_blocked':not router.clear(p.SHAPE_SEGMENT(vec((13000000,26700000)),vec((17000000,26700000)),150000),p.F_Cu,'signal'),
                'track_clear':router.clear(p.SHAPE_SEGMENT(vec((13000000,27300000)),vec((17000000,27300000)),150000),p.F_Cu,'signal')}
            witnesses.append(outcomes)
    records.append({'hole_floor_nm':hole_floor,'fresh':fresh,'reloaded':loaded,'expected_effective_nm':expected,'witnesses':witnesses})
(out/'report.json').write_text(json.dumps(records,indent=2)+'\n')
for row in records:
    for label in ('fresh','reloaded'):
        assert {name:q['effective_nm'] for name,q in row[label].items()}==row['expected_effective_nm'],row
    assert row['fresh']['npth']['own_nm']==0,row
    assert row['reloaded']['npth']['own_nm']>=250000,row
    assert row['fresh']['npth-low']['local_nm']==100000 and row['reloaded']['npth-low']['local_nm']==100000
    assert all(all(w.values()) for w in row['witnesses']),row
print(json.dumps({'stable_npth_pth_smd_and_local_overrides':True,'physical_hole_witnesses_preserved':True,'tested_hole_floors_nm':[250000,350000]}))
