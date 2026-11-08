"""Prepared native protocol: fresh-load both-face local closure and full matrix."""
from copy import deepcopy
import json,os,subprocess,sys,time
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_process import project_settings,COMPACT_VIAS
from smilepcb.keyboard_routing.generation import generation_profile
from native_collector_flow_fixture import fixture

out=Path(sys.argv[2]);out.mkdir(parents=True,exist_ok=True)
def vec(x,y):return p.VECTOR2I(round(x*1e6),round(y*1e6))
def pad(board,fp,number,net,point,face):
    d=p.PAD(fp);d.SetNumber(number);d.SetNet(board.FindNet(net));d.SetPosition(vec(*point));d.SetSize(vec(.4,.4));d.SetShape(p.PAD_SHAPE_RECT);d.SetAttribute(p.PAD_ATTRIB_SMD)
    layers=p.LSET();layers.AddLayer(face);d.SetLayerSet(layers);fp.Add(d);return d

def prepare():
    for name,face in (('front',p.F_Cu),('back',p.B_Cu)):
        folder=out/name;folder.mkdir();board,cfg=fixture(face)
        chip=next(f for f in board.GetFootprints() if f.GetReference()=='U_MCU')
        for net,number,y in (('local_function','6',16),('external_supply','7',20)):
            board.Add(p.NETINFO_ITEM(board,net));pad(board,chip,number,net,(14,y),face);cfg['parts'][0]['nets'][number]=net
            if net=='local_function':pad(board,chip,number,net,(14,y-.7),face)
            load=p.FOOTPRINT(board);load.SetReference('LOAD_'+number);load.SetLayer(face);board.Add(load);pad(board,load,'1',net,(10,y),face)
            cfg['parts'].append({'ref':load.GetReference(),'region':'controller','nets':{'1':net}})
            if net=='external_supply':
                remote=p.FOOTPRINT(board);remote.SetReference('REMOTE_LOAD');remote.SetLayer(face);board.Add(remote);pad(board,remote,'1',net,(35,y),face)
                cfg['parts'].append({'ref':'REMOTE_LOAD','region':'rgb','nets':{'1':net}})
        board.Add(p.NETINFO_ITEM(board,'incumbent'))
        source=p.FOOTPRINT(board);source.SetReference('ORIGINAL');source.SetLayer(face);board.Add(source);pad(board,source,'1','incumbent',(36,36),face)
        cfg['parts'].append({'ref':'ORIGINAL','region':'matrix','nets':{'1':'incumbent'}})
        track=p.PCB_TRACK(board);track.SetStart(vec(34,36));track.SetEnd(vec(36,36));track.SetWidth(200000);track.SetLayer(face);track.SetNet(board.FindNet('incumbent'));track.SetLocked(True);board.Add(track)
        (folder/'keyboard.kicad_pro').write_text(json.dumps(project_settings(COMPACT_VIAS)))
        assert p.SaveBoard(str(folder/'keyboard.kicad_pcb'),board)
        (folder/'cfg.json').write_text(json.dumps(cfg,indent=2)+'\n')
    (out/'prepared.json').write_text(json.dumps({'separate_exercise_process_required':True,'faces':['front','back'],'native_exercise_run':False}))


def exercise():
    from smilepcb.keyboard_routing.ordered_controller import prepare_ordered_controller
    from smilepcb.keyboard_routing.interface_contract import verify_interfaces
    from smilepcb.keyboard_routing.controller_local import verify_local_closures
    from smilepcb.keyboard_routing.field_contract import verify_native
    from smilepcb.keyboard_routing.native_graph import copper_graph
    records={}
    for name in ('front','back'):
        folder=out/name;path=folder/'keyboard.kicad_pcb';cfg=json.loads((folder/'cfg.json').read_text())
        board=p.LoadBoard(str(path));before=snapshot(board);settings=board.GetDesignSettings()
        actual={k:getattr(settings,k) for k in ('m_MinClearance','m_TrackMinWidth','m_HoleClearance')}
        assert actual=={'m_MinClearance':150000,'m_TrackMinWidth':150000,'m_HoleClearance':250000},actual
        assert {d.GetOwnClearance(layer) for f in board.GetFootprints() for d in f.Pads() if d.GetNumber() for layer in (p.F_Cu,p.B_Cu) if d.IsOnLayer(layer)}=={150000}
        with generation_profile('dsn-process-v2'):
            _,expired=prepare_ordered_controller(board,cfg,deadline=time.monotonic()-1,complete_matrix=True)
            assert not expired['accepted'] and snapshot(board)==before and 'interface' not in expired
            updated,report=prepare_ordered_controller(board,cfg,deadline=time.monotonic()+60,complete_matrix=True)
            assert report['accepted'],report
            proof=report['controller_local']['local_closed'];interfaces=report['interface'];matrix=report['matrix_contract']
            graph=copper_graph(board,{'local_function','external_supply'},time.monotonic()+5,include_zones=False)
            groups={net:{graph['find'](i) for i,n in enumerate(graph['nodes']) if isinstance(n['item'],p.PAD) and n['item'].GetNumber() and n['net']==net} for net in ('local_function','external_supply')}
            assert len(groups['local_function'])==1 and len(groups['external_supply'])>1
            assert set(proof['nets'])=={'local_function'}
            assert not any(q['net']=='local_function' for q in report['fixed_interfaces']['ports'])
            assert any(q['net']=='external_supply' for q in report['fixed_interfaces']['ports'])
            assert verify_interfaces(board,updated,interfaces,deadline=time.monotonic()+5)['accepted']
            verify_native(board,matrix,deadline=time.monotonic()+5)
            early=set(report['controller_local']['added_ids']);assert early and early<=set(matrix['prerequisite_tracks'])
            assert all(snapshot(board)['tracks'].get(u)==s for u,s in before['tracks'].items())
            assert updated['layout']['gpio']==updated['qmk']['matrix_pins']
            def physical_frame(state):
                result=deepcopy(state['footprints'])
                for f in result.values():
                    for d in f['pads']:d.pop('net')
                return result
            assert physical_frame(snapshot(board))==physical_frame(before)
            controls={}
            for case in ('alias','region','binding'):
                trial=deepcopy(interfaces);model=deepcopy(updated)
                if case=='alias':
                    row=trial['local_closed']['nets']['local_function']['pads'];uid=next(u for u,d in row.items() if d['ref']=='U_MCU');row.pop(uid)
                elif case=='region':next(q for q in model['parts'] if q['ref']=='LOAD_6')['region']='rgb'
                else:next(q for q in model['parts'] if q['ref']=='LOAD_6')['nets']['1']='external_supply'
                try:verify_interfaces(board,model,trial,deadline=time.monotonic()+5)
                except ValueError:controls[case]=True
                else:controls[case]=False
            assert all(controls.values()),controls
            assert p.SaveBoard(str(path),board);saved=p.LoadBoard(str(path));assert snapshot(saved)==snapshot(board)
            verify_native(saved,matrix,deadline=time.monotonic()+5);assert verify_interfaces(saved,updated,json.loads(json.dumps(interfaces)),deadline=time.monotonic()+5)['accepted']
            # Exercise the actual compose finalizer and ordinary controller_ports boundary.
            from smilepcb.keyboard_routing.interface_contract import MARKER as INTERFACES
            from smilepcb.keyboard_routing.field_contract import MARKER as FIELD
            (folder/INTERFACES).write_text(json.dumps(interfaces));(folder/FIELD).write_text(json.dumps(matrix))
            model=deepcopy(updated);mapping={d.GetNetname():'/module/'+d.GetNetname() for f in board.GetFootprints() for d in f.Pads() if d.GetNumber() and d.GetNetname()}
            footprints={f.GetReference():f for f in board.GetFootprints()}
            for part in model['parts']:
                part['footprint']=footprints[part['ref']].GetFPID().GetUniStringLibId()
                part['nets']={number:mapping.get(net,net) for number,net in part['nets'].items()}
            model['plane_nets']=[mapping.get(net,net) for net in model['plane_nets']]
            (folder/'.smilepcb-keyboard.json').write_text(json.dumps(model))
            request={'board':str(path),'paths':{ref:'/'+p.KIID().AsString() for ref in footprints},'fields':{}}
            finalizer=Path(__file__).resolve().parents[1]/'smilepcb/stm32/finalize_native.py'
            call=subprocess.run([sys.executable,'-B',str(finalizer)],input=json.dumps(request),text=True,capture_output=True,env=os.environ.copy(),timeout=20)
            (folder/'finalize.log').write_text(call.stdout+call.stderr);assert call.returncode==0,call.stdout+call.stderr
            from smilepcb.keyboard_native import run
            ordinary=run({'operation':'controller_ports','board':str(path),'generation_profile':'dsn-process-v2','routing_quality':'turns','budget_s':15})
            assert ordinary['accepted'] and not any(q['net']==mapping['local_function'] for q in ordinary['ports']['ports'])
            board=p.LoadBoard(str(path));interfaces=json.loads((folder/INTERFACES).read_text());matrix=json.loads((folder/FIELD).read_text());proof=interfaces['local_closed'];updated=model
            assert set(proof['nets'])=={mapping['local_function']}
            assert verify_interfaces(board,updated,interfaces,deadline=time.monotonic()+5)['local_closed_nets']==[mapping['local_function']]
            verify_native(board,matrix,deadline=time.monotonic()+5)
            for uid,shape in before['tracks'].items():
                expected=deepcopy(shape);expected['net']=mapping.get(shape['net'],shape['net']);assert snapshot(board)['tracks'][uid]==expected
            saved=board
            # Destructive negative on this in-memory disposable copy only.
            for t in list(saved.GetTracks()):
                if t.m_Uuid.AsString() in early and t.GetNetname()==mapping['local_function']:saved.Delete(t)
            try:verify_local_closures(saved,updated,proof,deadline=time.monotonic()+5)
            except ValueError:controls['broken_closure']=True
            else:controls['broken_closure']=False
            assert controls['broken_closure']
            records[name]={'accepted':True,'native_rules_nm':actual,'local_closed_without_head':True,'external_partial_keeps_head':True,'all_physical_aliases_joined':True,'joint_matrix_native_proved':True,'early_functional_copper_permanent':True,'source_exact':True,'saved_contacts_exact':True,'actual_finalize_rebind_proved':True,'normal_controller_ports_keeps_local_proof':True,'expired_source_rollback':True,'negative_controls':controls,'early_added':len(early),'matrix_track_count':len(matrix['tracks'])}
    (out/'report.json').write_text(json.dumps(records,indent=2)+'\n')

if sys.argv[1]=='prepare':prepare()
elif sys.argv[1]=='exercise':exercise()
else:raise ValueError('Unknown native probe mode')
