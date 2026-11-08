"""Complete pre-compose admission, using real native field and MCU geometry."""
import json,sys,time
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_routing.ordered_controller import prepare_ordered_controller
from smilepcb.keyboard_routing.field_contract import capture,verify_native
from smilepcb.keyboard_routing.generation import generation_profile

from native_collector_flow_fixture import fixture

out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True);records={}
for tag,face in [('front',p.F_Cu),('back',p.B_Cu),('rejected',p.F_Cu),('retry',p.B_Cu),('local-proof-timeout',p.B_Cu),('rgb-prerequisites',p.B_Cu),('outlet-frame',p.B_Cu),('analysis-once',p.B_Cu)]:
    b,cfg=fixture(face)
    if tag=='rgb-prerequisites':
        cfg['layout']['rgb']=True;cfg['layout']['rgb_order']=[k['id'] for k in cfg['layout']['keys']]
        for key in cfg['layout']['keys']:
            n='rgb'+str(key['id']);b.Add(p.NETINFO_ITEM(b,n))
            f=p.FOOTPRINT(b);f.SetReference('LED'+str(key['id']));f.SetLayer(face);b.Add(f);cfg['parts'].append({'ref':f.GetReference(),'region':'matrix','nets':{'2':n,'3':n}})
            for pin,dy in [('2',0),('3',1800000)]:
                d=p.PAD(f);d.SetNumber(pin);d.SetNet(b.FindNet(n));d.SetAttribute(p.PAD_ATTRIB_SMD);d.SetShape(p.PAD_SHAPE_RECT);d.SetSize(p.VECTOR2I(400000,400000));ls=p.LSET();ls.AddLayer(face);d.SetLayerSet(ls);d.SetPosition(p.VECTOR2I(8000000+key['id']*5000000,37000000+dy));f.Add(d)
        for key in cfg['layout']['keys']:
            f=next(f for f in b.GetFootprints() if f.GetReference()=='LED'+str(key['id']))
            for n in ('supply','GND'):
                if b.FindNet(n) is None:b.Add(p.NETINFO_ITEM(b,n))
            cap=p.FOOTPRINT(b);cap.SetReference('C_LED'+str(key['id']));cap.SetLayer(face);b.Add(cap)
            for dest,pin,net,x,dy in [(f,'1','supply',6500000,0),(f,'4','GND',6500000,1800000),(cap,'1','supply',5000000,0),(cap,'2','GND',5000000,1800000)]:
                d=p.PAD(dest);d.SetNumber(pin);d.SetNet(b.FindNet(net));d.SetAttribute(p.PAD_ATTRIB_SMD);d.SetShape(p.PAD_SHAPE_RECT);d.SetSize(p.VECTOR2I(400000,400000));ls=p.LSET();ls.AddLayer(face);d.SetLayerSet(ls);d.SetPosition(p.VECTOR2I(x+key['id']*5000000,37000000+dy));dest.Add(d)
            next(q for q in cfg['parts'] if q['ref']==f.GetReference())['nets'].update({'1':'supply','4':'GND'})
            cfg['parts'].append({'ref':cap.GetReference(),'region':'matrix','nets':{'1':'supply','2':'GND'}})
        cfg['plane_nets']=['GND']
    before=snapshot(b)
    import smilepcb.keyboard_routing.matrix_stage as stage
    old=stage.prepare_matrix;calls=[];analysis_reads=[];final_reads=[]
    import smilepcb.keyboard_routing.native_graph as native_graph
    import smilepcb.keyboard_routing.matrix_polish as matrix_polish
    old_native_graph=native_graph.copper_graph;old_stage_graph=getattr(stage,'copper_graph',None);old_route=stage.route_components;old_polish=matrix_polish.polish_matrix
    if tag=='analysis-once':
        def counted_graph(*a,**kw):
            analysis_reads.append(snapshot(b))
            return old_native_graph(*a,**kw)
        def after_route(*a,**kw):
            value=old_route(*a,**kw);analysis_reads.clear();return value
        def before_polish(*a,**kw):
            final_reads.append(len(analysis_reads));return old_polish(*a,**kw)
        native_graph.copper_graph=counted_graph
        if old_stage_graph is not None:stage.copper_graph=counted_graph
        stage.route_components=after_route;matrix_polish.polish_matrix=before_polish
    from smilepcb.keyboard_routing import collectors as collector_module
    old_graph=collector_module.copper_graph
    if tag=='local-proof-timeout':collector_module.copper_graph=lambda *a,**k:(_ for _ in ()).throw(TimeoutError('Injected local collector proof expiration'))
    if tag=='rgb-prerequisites':
        def check_prerequisites(*a,**kw):
            from smilepcb.keyboard_routing.modules import seed_key_data_access
            prior=snapshot(b);reuse=seed_key_data_access(b,cfg,budget_s=5)
            assert reuse['accepted'] and reuse['required']==8,reuse
            assert not reuse['added_ids'] and snapshot(b)==prior,'Matrix started before LED access was reserved'
            from smilepcb.keyboard_routing.modules import connect_key_modules
            prior=snapshot(b);local=connect_key_modules(b,cfg,budget_s=5)
            assert local['connections']==12 and not local['added_ids'] and snapshot(b)==prior,'Matrix started before local key wiring was reserved'
            from smilepcb.keyboard_routing.power import connect_controller_return_tree
            prior=snapshot(b);returns=connect_controller_return_tree(b,cfg,budget_s=5)
            assert returns['accepted'] and returns['required']==2 and not returns['added_ids'] and snapshot(b)==prior,returns
            return old(*a,**kw)
        stage.prepare_matrix=check_prerequisites
    if tag=='rejected':stage.prepare_matrix=lambda *a,**k:{'complete':False,'controller_connected':False,'reason':'Injected incomplete full matrix'}
    if tag=='retry':
        def once_rejected(*a,**kw):
            calls.append(1);r=old(*a,**kw)
            if len(calls)==1:r.update(complete=False,controller_connected=False,reason='First native-complete candidate intentionally rejected')
            return r
        stage.prepare_matrix=once_rejected
    try:
        with generation_profile('dsn-safe-v1'):
            updated,result=prepare_ordered_controller(b,cfg,deadline=time.monotonic()+35,complete_matrix=True,exit_bounds_nm=(12500000,12500000,23500000,19500000) if tag=='outlet-frame' else None)
    finally:
        stage.prepare_matrix=old;collector_module.copper_graph=old_graph
        native_graph.copper_graph=old_native_graph
        if old_stage_graph is not None:stage.copper_graph=old_stage_graph
        stage.route_components=old_route;matrix_polish.polish_matrix=old_polish
    field=result.get('matrix',{});proof=False
    if result['accepted']:
        contract=result['matrix_contract'];verify_native(b,contract,deadline=time.monotonic()+5);proof=True
    if result['accepted']:
        from smilepcb.keyboard_routing.modules import connect_key_modules
        prior=snapshot(b);repeat=connect_key_modules(b,updated,budget_s=5)
        assert repeat['connections']==(12 if tag=='rgb-prerequisites' else 4),repeat
        assert snapshot(b)==prior and not repeat['added_ids'],'Repeated local module pass duplicated source copper'
        from smilepcb.keyboard_routing.power import connect_controller_return_tree
        prior=snapshot(b);returns=connect_controller_return_tree(b,updated,budget_s=5)
        assert returns['accepted'] and returns['required']==2 and not returns['added_ids'] and snapshot(b)==prior,returns
    records[tag]={'final_analysis_graph_reads':final_reads,'matrix_calls':len(calls),'accepted':result['accepted'],'matrix':field,'model_shared':updated['layout']['gpio']==updated['qmk']['matrix_pins'],'proof':proof,'source_preserved':all(snapshot(b)['tracks'].get(u)==t for u,t in before['tracks'].items()),'unchanged':snapshot(b)==before,'report':result}
(out/'report.json').write_text(json.dumps(records,indent=2)+'\n')
