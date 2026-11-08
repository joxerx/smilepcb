"""Prepare the complete controller once, using the existing Native recipe.

JSON worker: project, source_core, process_profile, generation_profile, budget_s.
All outputs live below project/prepared-controller; source inputs are read-only.
"""
from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))

from smilepcb.stm32.core_module_import import (COMPILED_REPLACEMENTS,COMPILED_TARGETS,
    core_import_spec,_no_connect)

SOURCE_REFS={'U1',*('C'+str(i) for i in range(1,8))}
SOURCE_MAP={'U1':'U_MCU',**{'C'+str(i):'C_CORE'+str(i) for i in range(1,8)}}
LOCAL={'+3V3','GND','NRST','BOOT0'}
PROTOCOL={'D_N','D_P'}


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def preparation_model(raw):
    parts=raw['parts'];refs=[q['ref'] for q in parts]
    if len(refs)!=len(set(refs)) or any(r.startswith('C_CORE') for r in refs) or not COMPILED_REPLACEMENTS<=set(refs):
        raise ValueError('Original raw model requires unique MCU, seven capacitors and both target resistors')
    for q in parts:
        if q['ref'] in COMPILED_REPLACEMENTS:
            if q.get('region')!='controller':raise ValueError('Required support has wrong model region')
            _no_connect(q)
    result=deepcopy(raw);result['parts']=[deepcopy(q) for q in parts if q['ref'] in COMPILED_REPLACEMENTS]
    # Existing bounded local-module assembly bench, independent of keyboard pose.
    result['layout'].update(keys=[],rgb=False,rgb_order=[],size_mm=[32,32])
    result['outline']=[[-2,-2],[34,-2],[34,34],[-2,34]];result['controller_bounds']=[0,0,32,32]
    return result


def preserved_branches(before,after,pad_mapping=None):
    mapping=pad_mapping or {};checked=0
    for net,groups in before.items():
        current=[set(q) for q in after.get(net,[])]
        for group in groups:
            required={mapping.get(u,u) for u in group};checked+=1
            if not required or not any(required<=row for row in current):raise ValueError('Required original local branch split or lost pads: '+net)
    return dict(accepted=True,original_groups=checked,merges_allowed=True)


def controller_descriptor(raw,models,inventory,source_sha256,proof):
    descriptor=dict(schema=1,source_sha256=source_sha256,models=[dict(source_ref=q['ref'],target_ref=q['ref'],model=deepcopy(q)) for q in sorted(models,key=lambda q:q['ref'])],replace_refs=sorted(COMPILED_REPLACEMENTS),proof=proof)
    core_import_spec(raw,descriptor,inventory,source_sha256=source_sha256)
    return descriptor


def write_empty_seed(source,path):
    import sexpdata as sx
    tree=sx.loads(Path(source).read_text());tag=lambda q:str(q[0]) if isinstance(q,list) and q else ''
    if tag(tree)!='kicad_pcb':raise ValueError('Expected a saved source PCB')
    headers={'version','generator','generator_version','general','paper','layers'}
    seed=[tree[0],*(deepcopy(q) for q in tree[1:] if tag(q) in headers)]
    if not any(tag(q)=='layers' for q in seed):raise ValueError('Saved board layer inventory is missing')
    Path(path).write_text(sx.dumps(seed)+'\n')


def prepare(request):
    project=Path(request['project']).resolve();source=Path(request['source_core']).resolve()
    budget=request['budget_s']
    if type(budget) not in (int,float) or not math.isfinite(budget) or not 0<budget<=90:raise ValueError('Controller preparation budget must be positive and at most90seconds')
    raw_path=project/'model-before-placement.json';pro=project/'keyboard.kicad_pro'
    raw=json.loads(raw_path.read_text());bench=preparation_model(raw)
    # The cold load must see the target project before any later SetProject.
    seed=pro.with_suffix('.kicad_pcb')
    if not seed.is_file() or not pro.is_file():raise ValueError('Host-prepared saved empty seed or paired target project is missing')
    protected={name:path for name,path in [('source_core',source),('raw_model',raw_path),('project_settings',pro),('empty_seed',seed)]}
    input_hashes={name:sha(path) for name,path in protected.items()}
    out=project/'prepared-controller';out.mkdir(exist_ok=False)
    report=dict(schema='prepared-controller-v1',accepted=False,stage='source_preservation',input_sha256=input_hashes,budget_s=budget)
    started=time.monotonic();deadline=started+budget;board=None;owners=[]
    from smilepcb.native_worker import p,snapshot
    from smilepcb.keyboard_process import apply_native_rules
    from smilepcb.stm32.core_pose_native import _rules
    from smilepcb.keyboard_routing.generation import generation_profile
    from smilepcb.keyboard_routing.native_graph import copper_graph,route_components
    from smilepcb.keyboard_routing.local_router import LocalRouter
    from smilepcb.keyboard_routing.controller_access import prune_single_terminal_stubs
    from smilepcb.keyboard_routing.cleanup import remove_tails
    from smilepcb.keyboard_routing.safety import require_via_land_clearance
    from smilepcb.runtime import KICAD_PYTHON,environment
    def check():
        if time.monotonic()>=deadline:raise TimeoutError('Controller module shared deadline exhausted')
    def pads(candidate):
        return {d.m_Uuid.AsString():dict(ref=f.GetReference(),number=d.GetNumber(),net=d.GetNetname(),position_nm=[d.GetPosition().x,d.GetPosition().y]) for f in candidate.GetFootprints() for d in f.Pads() if d.GetNumber()}
    def inventory(candidate):
        return [dict(ref=f.GetReference(),uuid=f.m_Uuid.AsString(),value=f.GetValue(),pads=[dict(number=d.GetNumber(),uuid=d.m_Uuid.AsString(),net=d.GetNetname()) for d in f.Pads()]) for f in candidate.GetFootprints()]
    def partition(candidate,records,nets=LOCAL):
        graph=copper_graph(candidate,set(nets),deadline,include_zones=False);indices={q['item'].m_Uuid.AsString():i for i,q in enumerate(graph['nodes'])};groups={net:{} for net in nets}
        for uid,row in records.items():
            if row['net'] not in groups:continue
            if uid not in indices:raise ValueError('Required physical pad omitted from local graph')
            groups[row['net']].setdefault(graph['find'](indices[uid]),[]).append(uid)
        return {net:sorted(sorted(ids) for ids in rows.values()) for net,rows in groups.items()}
    def process_context(candidate):
        context=_rules(candidate);settings=context['native_scalar_attributes']
        return dict(copper_layers=candidate.GetCopperLayerCount(),default_netclass_clearance_nm=context['default_netclass_clearance_nm'],rules={key:settings[key] for key in ('m_MinClearance','m_HoleClearance','m_TrackMinWidth','m_CopperEdgeClearance','m_MinThroughDrill','m_ViasMinSize','m_ViasMinAnnularWidth')})
    try:
        check();manager=p.GetSettingsManager();owners.append(manager)
        if not manager.LoadProject(str(pro)):raise ValueError('Cannot load actual target project')
        native_project=manager.GetProject(str(pro))
        if native_project is None:raise ValueError('Missing Native target project owner')
        owners.append(native_project)
        empty=p.LoadBoard(str(seed))
        if empty is None or list(empty.GetFootprints()) or list(empty.GetTracks()):raise ValueError('Local saved seed is not empty')
        empty.SetProject(native_project);owners.extend([empty,empty.GetDesignSettings()]);numeric=apply_native_rules(empty,request['process_profile']);expected_context=_rules(empty);expected_process=process_context(empty)
        board=p.LoadBoard(str(source));board.SetProject(native_project);owners.extend([board,board.GetDesignSettings()]);apply_native_rules(board,request['process_profile'])
        counts=Counter(f.GetReference() for f in board.GetFootprints())
        if set(counts)!=SOURCE_REFS or any(n!=1 for n in counts.values()) or list(board.Zones()):raise ValueError('Source must be the exact extracted eight-part, zone-free core')
        source_pads=pads(board);source_state=snapshot(board);original_groups=partition(board,source_pads)
        chip=next(f for f in board.GetFootprints() if f.GetReference()=='U1');source_origin=[chip.GetPosition().x,chip.GetPosition().y]
        target=next(q for q in raw['parts'] if q['ref']=='U_MCU');net_map={}
        for d in chip.Pads():
            old=d.GetNetname();new=target['nets'][d.GetNumber()]
            if old in net_map and net_map[old]!=new:raise ValueError('Target model splits an internally shared donor net')
            net_map[old]=new
        with generation_profile(request['generation_profile']):
            report['source_pruning']=prune_single_terminal_stubs(board,protected_nets=PROTOCOL)
            for t in board.GetTracks():
                if t.GetNetname() in PROTOCOL and not isinstance(t,p.PCB_VIA):t.SetWidth(max(200000,board.GetDesignSettings().m_TrackMinWidth))
            # Preserve the existing local normalization workbench expansion.
            points=[q for edge in board.GetDrawings() if edge.GetLayer()==p.Edge_Cuts for q in (edge.GetStart(),edge.GetEnd())]
            centre=[round((min(getattr(q,axis) for q in points)+max(getattr(q,axis) for q in points))/2) for axis in ('x','y')]
            for edge in board.GetDrawings():
                if edge.GetLayer()!=p.Edge_Cuts:continue
                for getter,setter in (('GetStart','SetStart'),('GetEnd','SetEnd')):
                    point=getattr(edge,getter)();getattr(edge,setter)(p.VECTOR2I(point.x+(-2000000 if point.x<centre[0] else 2000000),point.y+(-2000000 if point.y<centre[1] else 2000000)))
            retained=snapshot(board);source_context=_rules(board)
            router=LocalRouter(board,deadline=min(deadline-20,time.monotonic()+55),compact=True);router.grid_step=250000;router.connection_budget_s=5;router.search_budget_s=5
            report['source_joins']=route_components(board,router,{'+3V3','GND'},refresh_components=True,prefer_small_goal=True)
            after=snapshot(board)
            if after['footprints']!=source_state['footprints'] or any(after['tracks'].get(u)!=v for u,v in retained['tracks'].items()) or _rules(board)!=source_context:raise ValueError('Source preservation changed retained geometry or actual rules')
            report['source_branches']=preserved_branches(original_groups,partition(board,source_pads))
            normalized=out/'preserved';normalized.mkdir();preserved=normalized/'core.kicad_pcb'
            if not p.SaveBoard(str(preserved),board):raise ValueError('Cannot save preserved local source')
            protocol={u:v for u,v in snapshot(board)['tracks'].items() if v['net'] in PROTOCOL}
            report.update(target_process=expected_process,numeric_rules=numeric,source_native_context=source_context)
            assembly=out/'assembly';assembly.mkdir()
            for name in ('keyboard.kicad_pro','fp-lib-table'):shutil.copyfile(project/name,assembly/name)
            shutil.copytree(project/'KeyboardParts.pretty',assembly/'KeyboardParts.pretty')
            (assembly/'model-before-placement.json').write_text(json.dumps(bench,indent=2)+'\n')
            child=dict(project=str(assembly),core=str(preserved),budget_s=min(10,max(.001,deadline-time.monotonic()-12)),process_profile=request['process_profile'],generation_profile=request['generation_profile'],require_controller_access=False,optimize_matrix_gpio=False,mirror_core=False,_core_exact_pose=dict(origin_nm=[16000000,16000000],rotation_deg=0),_core_assembly_only=True,_core_empty_seed=str(seed),_core_native_rules=expected_context)
            report['stage']='assembly'
            run=subprocess.run([KICAD_PYTHON,'-X','faulthandler','-B',str(Path(__file__).with_name('place.py'))],input=json.dumps(child),text=True,capture_output=True,env=environment(),timeout=max(.001,deadline-time.monotonic()-10))
            (out/'assembly.log').write_text(run.stdout+run.stderr)
            if run.returncode:raise ValueError('Existing assembly worker failed; see assembly.log')
            result=json.JSONDecoder().raw_decode(run.stdout)[0]
            if result.get('status')!='assembled' or not result.get('assembly_checks') or not all(v is True for v in result['assembly_checks'].values()):raise ValueError('Complete target-support assembly rejected')
            model=json.loads((assembly/'assembly.model.json').read_text());board=p.LoadBoard(str(assembly/'assembly.kicad_pcb'));board.SetProject(native_project);owners.extend([board,board.GetDesignSettings()]);apply_native_rules(board,request['process_profile'])
            if process_context(board)!=expected_process:raise ValueError('Module actual target process changed')
            state=snapshot(board);module_pads=pads(board);models={q['ref']:q for q in model['parts']}
            if set(models)!=COMPILED_TARGETS or Counter(f.GetReference() for f in board.GetFootprints())!=Counter({r:1 for r in COMPILED_TARGETS}) or len(module_pads)!=66:raise ValueError('Required ten-part/66-pad module inventory changed')
            mapping={}
            for uid,row in source_pads.items():
                matches=[u for u,r in module_pads.items() if r['ref']==SOURCE_MAP[row['ref']] and r['number']==row['number']]
                if len(matches)!=1 or module_pads[matches[0]]['net']!=net_map[row['net']]:raise ValueError('Source-pad import binding is ambiguous')
                mapping[uid]=matches[0]
            if len(mapping)!=62 or len(set(mapping.values()))!=62:raise ValueError('Source-pad identity map is incomplete')
            protected_protocol={};delta=[16000000-source_origin[i] for i in (0,1)]
            for uid,shape in protocol.items():
                expected=deepcopy(shape);expected['net']=net_map[expected['net']]
                for field in ('start','end','mid'):
                    if field in expected:expected[field]=[expected[field][i]+delta[i] for i in (0,1)]
                matches=[u for u,row in state['tracks'].items() if row==expected]
                if len(matches)!=1:raise ValueError('Protected source USB tail changed in assembly')
                protected_protocol[matches[0]]=deepcopy(expected)
            report['stage']='local_connections';context=_rules(board);router=LocalRouter(board,deadline=deadline-4,compact=True);router.grid_step=250000;router.connection_budget_s=5;router.search_budget_s=5
            report['local_joins']=route_components(board,router,LOCAL,refresh_components=True,prefer_small_goal=True)
            joined=snapshot(board)
            if joined['footprints']!=state['footprints'] or any(joined['tracks'].get(u)!=v for u,v in state['tracks'].items()):raise ValueError('Local joins changed imported source geometry')
            report['stage']='cleanup';cleanup=remove_tails(board,protected=set(protected_protocol),budget_s=max(.001,deadline-time.monotonic()-2));final=snapshot(board)
            changed=set(cleanup['removed_ids'])|set(cleanup['trimmed_ids'])
            if final['footprints']!=state['footprints'] or any(final['tracks'].get(u)!=v for u,v in joined['tracks'].items() if u not in changed):raise ValueError('Cleanup changed undeclared module items')
            groups=partition(board,pads(board))
            if any(len(groups.get(net,[]))!=1 for net in LOCAL):raise ValueError('Required local module net remains disconnected')
            mapped_groups={net_map[net]:rows for net,rows in original_groups.items()}
            branch_proof=preserved_branches(mapped_groups,groups,mapping)
            if module_pads!=pads(board) or _rules(board)!=context or process_context(board)!=expected_process or any(final['tracks'].get(u)!=v for u,v in protected_protocol.items()):raise ValueError('Final module pads, USB tails or target process changed')
            report['via_land']=require_via_land_clearance(board,deadline=deadline)
            all_nets={row['net'] for row in pads(board).values()}|{t.GetNetname() for t in board.GetTracks()};graph=copper_graph(board,all_nets,deadline,include_zones=False);ports=[]
            for rows in graph['groups'].values():
                actual=[graph['nodes'][i] for i in rows if graph['nodes'][i]['item'].m_Uuid.AsString() in module_pads]
                if not actual:raise ValueError('Prepared module contains undeclared pad-free copper')
                net=actual[0]['net']
                if net.startswith(('NC_','unconnected-')):continue
                ports.append(dict(net=net,pad_ids=sorted(q['item'].m_Uuid.AsString() for q in actual),copper_ids=sorted(graph['nodes'][i]['item'].m_Uuid.AsString() for i in rows if not isinstance(graph['nodes'][i]['item'],p.PAD))))
            check();core_file=out/'core.kicad_pcb'
            if not p.SaveBoard(str(core_file),board):raise ValueError('Cannot save prepared controller')
            shutil.copyfile(pro,out/'core.kicad_pro')
            proof=dict(schema='prepared-controller-proof-v1',native_context=context,target_process=expected_process,generation_profile=request['generation_profile'],process_profile=request['process_profile'],source_pad_to_module_pad=mapping,source_branches=branch_proof,local_pad_groups=groups,cleanup=cleanup,protected_usb_ids=sorted(protected_protocol),physical_ports=ports,input_sha256=input_hashes)
            descriptor=controller_descriptor(raw,list(models.values()),inventory(board),sha(core_file),proof)
            descriptor_file=out/'core-module.json';descriptor_file.write_text(json.dumps(descriptor,indent=2)+'\n')
            report.update(accepted=True,stage='prepared',proof=proof,core_file=str(core_file),descriptor_file=str(descriptor_file))
    except Exception as error:report['error']=dict(type=type(error).__name__,message=str(error),traceback=traceback.format_exc(limit=8))
    finally:
        if board is not None and not report['accepted']:
            try:p.SaveBoard(str(out/'attempt.kicad_pcb'),board)
            except Exception as error:report['save_error']=str(error)
        report['inputs_unchanged']=all(sha(path)==input_hashes[name] for name,path in protected.items());report['accepted']=report['accepted'] and report['inputs_unchanged'];report['elapsed_s']=time.monotonic()-started
        report_file=out/'report.json';report['report_file']=str(report_file);report_file.write_text(json.dumps(report,indent=2)+'\n')
    return report


def main():
    request=json.load(sys.stdin)
    try:result=prepare(request)
    except Exception as error:
        result=dict(accepted=False,stage='input_or_runtime_validation',error=dict(type=type(error).__name__,message=str(error)))
        project=Path(request['project']).resolve()
        if project.is_dir():
            report_file=project/'controller-preparation-error.json';result['report_file']=str(report_file)
            report_file.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))


if __name__=='__main__':
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
    main()
