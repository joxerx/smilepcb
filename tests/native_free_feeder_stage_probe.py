"""Tiny real fresh-field fallback through the conditional bank caller."""
from copy import deepcopy
import json,sys,time
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_routing.conditional_geometry import FrozenProofRouter
from types import SimpleNamespace
from smilepcb.keyboard_routing import conditional_banks as api
from smilepcb.keyboard_routing.controller_ports import route_controller_ports
from smilepcb.keyboard_routing.field import build_field
from smilepcb.keyboard_routing.generation import generation_profile
from smilepcb.keyboard_routing.interface_launches import reserve_launches,current_launch_inventory,publish_launches
from native_collector_flow_fixture import fixture

out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True);records={}
partial_seed='--partial-seed' in sys.argv
faces=[('front',p.F_Cu)] if '--red' in sys.argv or partial_seed else [('front',p.F_Cu),('back',p.B_Cu)]
with generation_profile('dsn-process-v2'):
    for name,face in faces:
        b,cfg=fixture(face)
        from smilepcb.keyboard_process import apply_native_rules
        apply_native_rules(b,'fr4-2l-1oz-0p5-0p2');before=snapshot(b)
        proof=route_controller_ports(b,controller_refs={'U_MCU','C_RETURN'},mcu_refs={'U_MCU'},plane_nets=cfg['plane_nets'],
            include_nets={'r0','r1','c0','c1'},access_mode='prefer_same_layer',continuation_nm=500000,clearance_nm=173000,deadline=time.monotonic()+10)
        assert proof['accepted'],proof
        token=api.certify_generated_heads(b,cfg,before,proof['ports'],deadline=time.monotonic()+5)
        field=build_field(b,cfg,row_layer=face,column_layer=p.B_Cu if face==p.F_Cu else p.F_Cu,deadline=time.monotonic()+20)
        assert field['complete'],field
        entry=snapshot(b);model=deepcopy(cfg);calls=[];normal=api.conditional_search
        def exhausted(order,attempt,**kwargs):
            calls.append(kwargs['deadline'])
            return {'accepted':False,'reason':'controlled_ordered_rejection','attempt_records':[]}
        skipped=[];scratch_head_check=[];normal_route=api.route_components
        from smilepcb.keyboard_routing import collectors as collector_api
        normal_collectors=collector_api.route_collectors
        normal_free=api.route_free_feeders
        def partial_search(order,attempt,**kwargs):
            calls.append(kwargs['deadline'])
            kwargs['max_attempts']=1
            return normal(order,attempt,**kwargs)
        def partial_collectors(board,cfg,ports,**collector_kwargs):
            skipped.append(ports[0]['net'])
            report=normal_collectors(board,cfg,ports[1:],**collector_kwargs)
            publish_launches(ports)
            return report
        def selective_route(board,router,nets,**route_kwargs):
            net=next(iter(nets))
            if skipped and net==skipped[0]:
                router.graph_diagnostic={'attempts':[],'skipped':True,'partial_seed_fixture':True}
                return 0
            return normal_route(board,router,nets,**route_kwargs)
        def inspect_fallback(board,*args,**free_kwargs):
            net=skipped[0]
            source_ids={u for q in token.ports if q['net']==net for u in q['added_ids']}
            scratch_head_check.append(not (source_ids & set(snapshot(board)['tracks'])))
            return normal_free(board,*args,**free_kwargs)
        deadline=time.monotonic()+70
        if partial_seed:
            collector_api.route_collectors=partial_collectors
            api.route_components=selective_route
            api.route_free_feeders=inspect_fallback
        api.conditional_search=partial_search if partial_seed else exhausted
        try:
            with reserve_launches(proof['ports']):
                scope=current_launch_inventory();prior=(deepcopy(scope.ports),set(scope.resolved),set(scope.repair_reserved))
                result=api.prepare_conditional_banks(b,cfg,field,proof['ports'],token,deadline=deadline)
                unchanged_scope=(scope.ports,scope.resolved,scope.repair_reserved)==prior
        finally:
            api.conditional_search=normal;api.route_components=normal_route
            collector_api.route_collectors=normal_collectors;api.route_free_feeders=normal_free
        records[name]={'result':result,'model_unchanged':cfg==model,'scope_unchanged':unchanged_scope,
            'ordered_cutoff_reserved':len(calls)==1 and calls[0]<deadline,'field_exact':all(snapshot(b)['tracks'].get(u)==entry['tracks'][u] for u in field['owned_ids'])}
        if partial_seed:
            attempt=result['selection']['attempt_records'][0]
            missing=attempt['result'].get('missing',{})
            closed=sorted(set(result['physical_banks'][attempt['bank']])-set(missing))
            fallback=result.get('free_feeders',{})
            records[name]['partial_seed_case']={'skipped_net':skipped[0] if skipped else None,
                'bank':attempt['bank'],'attempt_accepted':attempt['accepted'],'missing':missing,'closed_nets':closed,
                'partial_seed_nets':fallback.get('partial_seed_nets',[]),
                'seed_copper_count':fallback.get('seed_copper_count',0),
                'scratch_head_check':scratch_head_check}
        (out/'report.json').write_text(json.dumps(records,indent=2)+'\n')
        assert result['accepted'],result
        assert result.get('source_mode')=='native_pads',result
        assert result['free_feeders']['saved_reload_exact']
        assert records[name]['field_exact'] and records[name]['model_unchanged'] and records[name]['scope_unchanged'] and records[name]['ordered_cutoff_reserved']
        assert result['source_ids']==result['head_ids']==[]
        assert result['matrix_ports']==list(token.ports)
        if partial_seed:
            attempts=result['selection']['attempt_records']
            assert len(attempts)==1 and attempts[0]['option']=='original',attempts
            assert not attempts[0]['accepted'] and len(attempts[0]['result'].get('missing',{}))==1,attempts
            assert skipped and skipped[0] in result['physical_banks'][attempts[0]['bank']],(skipped,attempts)
            assert scratch_head_check==[True],scratch_head_check
            assert result['free_feeders']['native_proof']['accepted'] and result['free_feeders']['reload_proof']['accepted']
        assert p.SaveBoard(str(out/(name+'.kicad_pcb')),b)
        if name=='front':
            def fresh_case():
                candidate,model=fixture(face);apply_native_rules(candidate,'fr4-2l-1oz-0p5-0p2');entry=snapshot(candidate)
                ports_proof=route_controller_ports(candidate,controller_refs={'U_MCU','C_RETURN'},mcu_refs={'U_MCU'},plane_nets=model['plane_nets'],
                    include_nets={'r0','r1','c0','c1'},access_mode='prefer_same_layer',continuation_nm=500000,clearance_nm=173000,deadline=time.monotonic()+10)
                assert ports_proof['accepted'],ports_proof
                owned=api.certify_generated_heads(candidate,model,entry,ports_proof['ports'],deadline=time.monotonic()+5)
                return candidate,model,entry,ports_proof,owned
            def ordered_reject(order,attempt,**kwargs):
                return {'accepted':False,'reason':'controlled_ordered_rejection','attempt_records':[]}

            failed_board,failed_cfg,_cert_before,failed_ports,failed_token=fresh_case()
            failed_field=build_field(failed_board,failed_cfg,row_layer=face,column_layer=p.B_Cu if face==p.F_Cu else p.F_Cu,deadline=time.monotonic()+20)
            failed_before=snapshot(failed_board)
            failed_model=deepcopy(failed_cfg);raw_seal=FrozenProofRouter._physical_state(SimpleNamespace(board=failed_board));route_calls=[]
            normal_route=api.route_free_feeders
            normal_search=api.conditional_search
            def no_copper(board,*args,**kwargs):
                route_calls.append(True)
                return {'accepted':True,'routing_history':[]}
            api.route_free_feeders=no_copper
            api.conditional_search=ordered_reject
            try:
                with reserve_launches(failed_ports['ports']):
                    scope=current_launch_inventory();scope_before=(deepcopy(scope.ports),set(scope.resolved),set(scope.repair_reserved))
                    result_no_copper=api.prepare_conditional_banks(failed_board,failed_cfg,failed_field,failed_ports['ports'],failed_token,deadline=time.monotonic()+70)
                    scope_exact=(scope.ports,scope.resolved,scope.repair_reserved)==scope_before
            finally:api.route_free_feeders=normal_route;api.conditional_search=normal_search
            records['no_copper_rejected']={'rejected':not result_no_copper['accepted'],
                'exact':snapshot(failed_board)==failed_before and failed_cfg==failed_model,
                'raw_exact':FrozenProofRouter._physical_state(SimpleNamespace(board=failed_board))==raw_seal,'scope_exact':scope_exact,'called':bool(route_calls)}
            assert records['no_copper_rejected']=={'rejected':True,'exact':True,'raw_exact':True,'scope_exact':True,'called':True},records['no_copper_rejected']

            failed_board,failed_cfg,_cert_before,failed_ports,failed_token=fresh_case()
            failed_field=build_field(failed_board,failed_cfg,row_layer=face,column_layer=p.B_Cu if face==p.F_Cu else p.F_Cu,deadline=time.monotonic()+20)
            failed_before=snapshot(failed_board)
            failed_model=deepcopy(failed_cfg);raw_seal=FrozenProofRouter._physical_state(SimpleNamespace(board=failed_board));proof_calls=[];commit_scope_exact=False;commit_rejected=False
            normal_proof=api.prove_free_matrix
            normal_search=api.conditional_search
            def fail_commit(target,*args,**kwargs):
                proof_calls.append(target is failed_board)
                if target is failed_board:raise ValueError('controlled commit proof rejection')
                return normal_proof(target,*args,**kwargs)
            api.prove_free_matrix=fail_commit
            api.conditional_search=ordered_reject
            try:
                with reserve_launches(failed_ports['ports']):
                    scope=current_launch_inventory();scope_before=(deepcopy(scope.ports),set(scope.resolved),set(scope.repair_reserved))
                    try:api.prepare_conditional_banks(failed_board,failed_cfg,failed_field,failed_ports['ports'],failed_token,deadline=time.monotonic()+70)
                    except ValueError as error:
                        assert str(error)=='controlled commit proof rejection'
                        commit_rejected=True
                    else:raise AssertionError('Expected injected caller proof rejection')
                    commit_scope_exact=(scope.ports,scope.resolved,scope.repair_reserved)==scope_before
            finally:api.prove_free_matrix=normal_proof;api.conditional_search=normal_search
            records['commit_proof_rollback']={'rejected':commit_rejected,
                'exact':snapshot(failed_board)==failed_before and failed_cfg==failed_model,
                'raw_exact':FrozenProofRouter._physical_state(SimpleNamespace(board=failed_board))==raw_seal,'scope_exact':commit_scope_exact,'proof_calls':proof_calls}
            assert records['commit_proof_rollback']=={'rejected':True,'exact':True,'raw_exact':True,'scope_exact':True,'proof_calls':[False,False,True]}
print(json.dumps({'accepted':True,'cases':list(records)}))
