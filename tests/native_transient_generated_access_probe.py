"""Fresh matrix capture must not freeze temporary access after net closure."""
import json,sys,time,subprocess
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_process import project_settings,COMPACT_VIAS
from smilepcb.keyboard_routing.ordered_controller import prepare_ordered_controller
from smilepcb.keyboard_routing.field_contract import verify_native
from smilepcb.keyboard_routing.generation import generation_profile
from smilepcb.keyboard_routing.cleanup import cleanup_generated
from smilepcb.keyboard_routing.completion import pad_partitions
from smilepcb.keyboard_routing.local_router import LocalRouter
from smilepcb.keyboard_native import pad_gaps
from native_collector_flow_fixture import fixture

out=Path(sys.argv[2]);out.mkdir(parents=True,exist_ok=True);host_python=sys.argv[3];records={}
def vec(x,y):return p.VECTOR2I(round(x*1e6),round(y*1e6))
def add_pad(board,fp,pin,net,xy,face):
    d=p.PAD(fp);d.SetNumber(pin);d.SetNet(board.FindNet(net));d.SetAttribute(p.PAD_ATTRIB_SMD);d.SetShape(p.PAD_SHAPE_RECT);d.SetSize(vec(.4,.4));ls=p.LSET();ls.AddLayer(face);d.SetLayerSet(ls);d.SetPosition(vec(*xy));fp.Add(d);return d
def prepare():
    for name,face in (("front",p.F_Cu),("back",p.B_Cu)):
        folder=out/name;folder.mkdir();path=folder/"keyboard.kicad_pcb";board,cfg=fixture(face)
        cfg['layout']['rgb']=True;cfg['layout']['rgb_order']=[k['id'] for k in cfg['layout']['keys']]
        for key in cfg['layout']['keys']:
            net='data'+str(key['id']);board.Add(p.NETINFO_ITEM(board,net));f=p.FOOTPRINT(board);f.SetReference('LED'+str(key['id']));f.SetLayer(face);board.Add(f)
            cfg['parts'].append({'ref':f.GetReference(),'region':'matrix','nets':{'2':net,'3':net}})
            for pin,y in (('2',37),('3',38.8)):add_pad(board,f,pin,net,(8+key['id']*5,y),face)
            for n in ('supply','GND'):
                if board.FindNet(n) is None:board.Add(p.NETINFO_ITEM(board,n))
            cap=p.FOOTPRINT(board);cap.SetReference('C_LED'+str(key['id']));cap.SetLayer(face);board.Add(cap)
            for dest,pin,net,x,y in ((f,'1','supply',6.5,37),(f,'4','GND',6.5,38.8),(cap,'1','supply',5,37),(cap,'2','GND',5,38.8)):
                add_pad(board,dest,pin,net,(x+key['id']*5,y),face)
            cfg['parts'][-1]['nets'].update({'1':'supply','4':'GND'});cfg['parts'].append({'ref':cap.GetReference(),'region':'matrix','nets':{'1':'supply','2':'GND'}})
        board.Add(p.NETINFO_ITEM(board,'control'));chip=next(f for f in board.GetFootprints() if f.GetReference()=='U_MCU')
        add_pad(board,chip,'6','control',(22,21),face);cfg['parts'][0]['nets']['6']='control'
        support=p.FOOTPRINT(board);support.SetReference('TP_CONTROL');support.SetLayer(face);board.Add(support)
        add_pad(board,support,'1','control',(22,26),face);cfg['parts'].append({'ref':'TP_CONTROL','region':'matrix','nets':{'1':'control'}})
        board.Add(p.NETINFO_ITEM(board,'USB_DP'));original=p.PCB_TRACK(board);original.SetStart(vec(36,25));original.SetEnd(vec(38,25));original.SetWidth(200000);original.SetLayer(face);original.SetNet(board.FindNet('USB_DP'));original.SetLocked(True);board.Add(original)
        (folder/'keyboard.kicad_pro').write_text(json.dumps(project_settings(COMPACT_VIAS)));assert p.SaveBoard(str(path),board)
        (folder/'cfg.json').write_text(json.dumps(cfg,indent=2)+'\n')


def exercise():
    for name,face in (("front",p.F_Cu),("back",p.B_Cu)):
        folder=out/name;path=folder/"keyboard.kicad_pcb";cfg=json.loads((folder/"cfg.json").read_text());board=p.LoadBoard(str(path));before=snapshot(board)
        process_before={k:getattr(board.GetDesignSettings(),k) for k in ('m_MinClearance','m_TrackMinWidth','m_HoleClearance','m_MinThroughDrill','m_ViasMinSize')}
        expected={'m_MinClearance':150000,'m_TrackMinWidth':150000,'m_HoleClearance':250000,'m_MinThroughDrill':200000,'m_ViasMinSize':500000}
        assert process_before==expected,process_before
        with generation_profile('dsn-process-v2'):
            updated,result=prepare_ordered_controller(board,cfg,deadline=time.monotonic()+60,complete_matrix=True)
        assert result['accepted'],result
        contract=result['matrix_contract'];verify_native(board,contract,deadline=time.monotonic()+5)
        key_new=set(result['key_data_access']['added_ids']);fixed_new=set(result['fixed_interfaces']['added_ids']);permanent=set(contract['prerequisite_tracks']);field=set(contract['tracks'])
        excluded=not ((key_new|fixed_new)&permanent)
        external=next(part for part in updated['parts'] if part['ref']=='TP_CONTROL')['region']!='controller'
        real_fixed=[q for q in result['fixed_interfaces']['ports'] if q['net']=='control' and q['added_ids']]
        initial_control_gap=pad_gaps(board).get('control',0)
        assert external and real_fixed and fixed_new and initial_control_gap==1
        row={'accepted':True,'genuine_external_fixed_interface':bool(external and real_fixed),'fixed_control_initially_open':initial_control_gap==1,'new_access_is_transient':excluded,'key_owned':len(key_new),'fixed_owned':len(fixed_new),'originals_permanent':set(before['tracks'])<=permanent,'source_exact':all(snapshot(board)['tracks'].get(u)==v for u,v in before['tracks'].items())}
        if excluded:
            port_ids={q['via_id'] for q in result['key_data_access']['ports']}|fixed_new
            def groups():return {frozenset(g) for g in pad_partitions(board,time.monotonic()+10)}
            def retained():
                assert p.SaveBoard(str(path),board)
                code='import json,sys;from smilepcb.keyboard_recovery import pending_interface_ids;print(json.dumps(pending_interface_ids(sys.argv[1],json.loads(sys.argv[2]),json.loads(sys.argv[3]))))'
                raw=subprocess.check_output([host_python,'-c',code,str(path),json.dumps(sorted(port_ids)),json.dumps(pad_gaps(board))],text=True)
                return set(json.loads(raw))
            prior=snapshot(board);contacts=groups();active=retained()
            cleanup_generated(board,protected=field|permanent|active,deadline=time.monotonic()+15)
            row['open_access_retained']=all(snapshot(board)['tracks'].get(u)==prior['tracks'][u] for u in port_ids)
            row['open_contacts_exact']=groups()==contacts
            # Construct a legal native-closed state; this is a cleanup control,
            # not a claim that the routing algorithm chose this attachment.
            router=LocalRouter(board,compact=True,track_width_nm=max(150000,board.GetDesignSettings().m_TrackMinWidth))
            for net,refs in (('data1',('LED1','LED1')),('control',('U_MCU','TP_CONTROL'))):
                pads=[d for f in board.GetFootprints() if f.GetReference() in refs for d in f.Pads() if d.GetNumber() and d.GetNetname()==net]
                a,z=[(d.GetPosition().x,d.GetPosition().y) for d in pads]
                path_points=router.leg(a,z,face,net);assert path_points is not None
                router.add_path(path_points,face,net)
            closed_contacts=groups();active=retained();chosen_vias={q['via_id'] for q in result['key_data_access']['ports'] if q['net']=='data1'}
            closed_head={u for q in result['fixed_interfaces']['ports'] if q['net']=='control' for u in q['added_ids']}
            assert closed_head,'Unused fixed-head retirement must never be vacuous'
            cleanup_generated(board,protected=field|permanent|active,deadline=time.monotonic()+15)
            after=snapshot(board);verify_native(board,contract,deadline=time.monotonic()+5)
            row.update(closed_redundant_key_vias_retired=not chosen_vias&after['tracks'].keys(),closed_unused_head_retired=not closed_head&after['tracks'].keys(),
                closed_contacts_exact=groups()==closed_contacts,permanent_exact=all(after['tracks'].get(u)==prior['tracks'][u] for u in permanent),matrix_exact=all(after['tracks'].get(u)==v for u,v in contract['tracks'].items()))
            assert p.SaveBoard(str(path),board);saved=p.LoadBoard(str(path));verify_native(saved,contract,deadline=time.monotonic()+5);row['saved_exact']=snapshot(saved)==after
        row['native_process_before']=process_before;row['native_process_after']={k:getattr(board.GetDesignSettings(),k) for k in process_before}
        assert row['native_process_after']==expected
        records[name]=row
    (out/'report.json').write_text(json.dumps(records,indent=2)+'\n')

if sys.argv[1]=='prepare':prepare()
elif sys.argv[1]=='exercise':exercise()
else:raise ValueError('Unknown transient-access probe mode')
