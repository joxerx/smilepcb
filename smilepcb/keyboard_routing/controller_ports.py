"""Joint short controller interfaces, using native shapes and bounded search.

Each selected physical pad group gets one via and an opposite-face outward
runway. Proposals must coexist before any copper is committed. This is a local
interface recipe; full native CAD/connectivity/firmware acceptance is separate.
"""
import math
import time
from smilepcb.native_worker import p,snapshot
from smilepcb.routing_style import octile_paths
from .local_router import LocalRouter,xy,vec,distance
from .native_graph import copper_graph
from .safety import require_via_land_clearance


def _select(domains,compatible,deadline,max_states):
    """Forward-check the remaining physical interfaces before selecting one."""
    cache={};states=0;stats={'max_assigned':0,'deepest_groups':[],'last_conflict':None,'backjumps':0}
    def allowed(i,a,j,mask):
        key=(i,a,j)
        if key not in cache:
            bits=0
            for k,b in enumerate(domains[j]):
                if time.monotonic()>=deadline:raise TimeoutError('Port compatibility budget exhausted')
                if compatible(domains[i][a],b):bits|=1<<k
            cache[key]=bits
        return cache[key]&mask
    def visit(masks,causes,chosen):
        nonlocal states
        if len(chosen)>stats['max_assigned']:
            stats.update(max_assigned=len(chosen),deepest_groups=sorted(chosen))
        if not masks:return chosen,set()
        if time.monotonic()>=deadline or states>=max_states:
            raise TimeoutError('Joint port search budget exhausted')
        i=min(masks,key=lambda k:(bin(masks[k]).count('1'),k));bits=masks[i]
        conflicts=set(causes[i])
        neighbours=sorted((j for j in masks if j!=i),key=lambda j:(bin(masks[j]).count('1'),j))[:8]
        values=[]
        while bits:
            bit=bits & -bits;bits^=bit;a=bit.bit_length()-1
            loss=sum(1-bin(allowed(i,a,j,masks[j])).count('1')/bin(masks[j]).count('1') for j in neighbours)
            values.append((loss,a))
        for _,a in sorted(values):
            if time.monotonic()>=deadline or states>=max_states:
                raise TimeoutError('Joint port search budget exhausted')
            states+=1
            remaining={};reasons={};valid=True
            for j,mask in masks.items():
                if j==i:continue
                filtered=allowed(i,a,j,mask)
                if not filtered:
                    stats['last_conflict']=[i,j]
                    conflicts.update(causes[j]);valid=False;break
                remaining[j]=filtered
                reasons[j]=causes[j]|({i} if filtered!=mask else set())
            if valid:
                result,failed=visit(remaining,reasons,{**chosen,i:a})
                if result is not None:return result,set()
                if i not in failed:
                    stats['backjumps']+=1
                    return None,failed
                conflicts.update(failed-{i})
        return None,conflicts
    try:
        masks={i:(1<<len(rows))-1 for i,rows in enumerate(domains)}
        result,_=visit(masks,{i:set() for i in masks},{})
    except TimeoutError as error:
        result=None;stats.update(budget_exhausted=True,budget_reason=str(error))
    stats.update(states=states,compatibility_masks=len(cache))
    return result,stats


def _compatible_ports(world,a,z,clearance_nm):
    """Check actual planned copper and drills before committing a bank."""
    gap=world.generation.via_to_hole_clearance_nm
    if p.SHAPE.Collide(a['circle'],z['hole'],gap) or p.SHAPE.Collide(z['circle'],a['hole'],gap):return False
    if a['net']==z['net']:return True
    clearance_nm=max(clearance_nm,world.generation.foreign_copper_clearance_nm)
    return not any(face==other and p.SHAPE.Collide(shape,b,clearance_nm)
                   for face,shape in a['shapes'] for other,b in z['shapes'])


def route_controller_ports(board,*,controller_refs,mcu_refs,plane_nets=(),usb_nets=(),
                           deadline,clearance_nm=200000,max_distance_nm=4500000,
                           runway_nm=1000000,max_candidates=96,max_states=12000,
                           include_auxiliary_returns=False):
    started=time.monotonic();before=snapshot(board);added=[]
    refs=set(controller_refs);mcus=set(mcu_refs);fps={f.GetReference():f for f in board.GetFootprints()}
    if not mcus or not mcus<=refs or not refs<=set(fps):raise ValueError('Controller interfaces require existing MCU/controller references')
    if type(include_auxiliary_returns) is not bool:raise ValueError('Auxiliary return selection must be boolean')
    if deadline<=started:raise ValueError('Controller interfaces require a positive deadline')
    if (any(type(value) is not int for value in (clearance_nm,max_distance_nm,runway_nm,max_candidates,max_states)) or
        clearance_nm<board.GetDesignSettings().m_MinClearance or
        not 150000<=clearance_nm<=500000 or not 800000<=max_distance_nm<=6000000 or
        not 500000<=runway_nm<=2000000 or not 1<=max_candidates<=256 or not 1<=max_states<=100000):
        raise ValueError('Unsupported bounded controller interface dimensions/search limits')
    if any(not z.GetIsRuleArea() for z in board.Zones()):raise ValueError('Controller interfaces require a fresh board without plane fills')
    report={'accepted':False,'ports':[],'added_ids':[],'already_accessible':[],'groups':0}
    try:
        nets={d.GetNetname() for name in mcus for d in fps[name].Pads()
              if d.GetNumber() and d.GetNetname() and not d.GetNetname().startswith(('unconnected-','NC_'))}
        nets=(nets|set(plane_nets))-set(usb_nets)
        graph=copper_graph(board,nets,deadline);groups=[]
        for indices in graph['groups'].values():
            pads=[graph['nodes'][i]['item'] for i in indices if isinstance(graph['nodes'][i]['item'],p.PAD)
                  and graph['nodes'][i]['item'].GetNumber() and graph['nodes'][i]['item'].GetParentFootprint().GetReference() in refs]
            if not pads:continue
            net=pads[0].GetNetname()
            # This stage reserves MCU interfaces. Auxiliary SMD returns can
            # contact their native plane directly; forcing a through-via on
            # every such pad needlessly consumes the GPIO escape area.
            if not any(d.GetParentFootprint().GetReference() in mcus for d in pads):
                if not include_auxiliary_returns or net not in plane_nets:continue
            # An old via alone may still be trapped inside the chip's local
            # copper. Only an actual external pad already reached by the
            # physical group substitutes for a new outward interface.
            if any(isinstance(graph['nodes'][i]['item'],p.PAD) and graph['nodes'][i]['item'].GetNumber() and
                   graph['nodes'][i]['item'].GetParentFootprint().GetReference() not in refs for i in indices):
                report['already_accessible'].append({'net':net,'pad_ids':[d.m_Uuid.AsString() for d in pads]});continue
            groups.append((net,pads))
        groups.sort(key=lambda row:(row[0],sorted((d.GetParentFootprint().GetReference(),d.GetNumber()) for d in row[1])))
        report['groups']=len(groups)
        world=LocalRouter(board,deadline=deadline,compact=True,track_width_nm=board.GetDesignSettings().m_TrackMinWidth or 150000)
        world.minimum_clearance=max(world.minimum_clearance,clearance_nm)
        world.net_clearances={key:max(value,clearance_nm) for key,value in world.net_clearances.items()}
        bounds={}
        for name in mcus:
            boxes=[d.GetEffectiveShape(face).BBox() for d in fps[name].Pads()
                   for face in (p.F_Cu,p.B_Cu) if d.IsOnLayer(face)]
            bounds[name]=(min(b.GetLeft() for b in boxes),min(b.GetTop() for b in boxes),
                          max(b.GetRight() for b in boxes),max(b.GetBottom() for b in boxes))
        lands=[d.GetEffectiveShape(face) for f in board.GetFootprints() for d in f.Pads()
               if d.GetAttribute()==p.PAD_ATTRIB_SMD for face in (p.F_Cu,p.B_Cu) if d.IsOnLayer(face)]
        shifts=sorted(((normal,tangent) for normal in [sign*v for v in range(800000,max_distance_nm+1,400000) for sign in (-1,1)]
                       for tangent in [0]+[sign*v for v in range(300000,2100001,300000) for sign in (-1,1)]
                       if math.hypot(normal,tangent)<=max_distance_nm),
                      key=lambda row:(abs(row[0])*(2 if row[0]<0 else 1)+(math.sqrt(2)-1)*abs(row[1]),row))
        domains=[]
        for net,pads in groups:
            choices=[];seen=set();diagnostic={'via_clear':0,'solder_land_clear':0,'runway_clear':0,'lead_clear':0}
            anchors=sorted(pads,key=lambda d:(d.GetParentFootprint().GetReference() in mcus,
                            len(list(d.GetParentFootprint().Pads())),d.GetParentFootprint().GetReference(),d.GetNumber()))
            for pad in anchors:
                origin=xy(pad.GetPosition());nearest=min(mcus,key=lambda name:distance(origin,xy(fps[name].GetPosition())))
                center=xy(fps[nearest].GetPosition())
                delta=(origin[0]-center[0],origin[1]-center[1]);axis=0 if abs(delta[0])>abs(delta[1]) else 1;sign=1 if delta[axis]>=0 else -1
                faces=[face for face in (p.F_Cu,p.B_Cu) if pad.IsOnLayer(face)]
                if len(faces)!=1:continue
                face=faces[0];other=p.B_Cu if face==p.F_Cu else p.F_Cu
                for normal,tangent in shifts:
                    if time.monotonic()>=deadline:raise TimeoutError('Port candidate generation budget exhausted')
                    end=list(origin);end[axis]+=sign*normal;end[1-axis]+=tangent;end=tuple(end)
                    if not world.via_clear(end,net):continue
                    diagnostic['via_clear']+=1
                    circle=p.SHAPE_CIRCLE(vec(end),world.via_width//2)
                    if any(p.SHAPE.Collide(circle,land,clearance_nm) for land in lands):continue
                    diagnostic['solder_land_clear']+=1
                    # A via may sit under a leaded package: escape on its
                    # opposite face all the way outside the pad envelope.
                    # Ground-plane access needs a real via, not an artificial
                    # outward signal trace through the plane.
                    exit_point=list(end);plane=net in plane_nets
                    if not plane:
                        edge=bounds[nearest][axis+(2 if sign>0 else 0)]
                        exit_point[axis]=(max(end[axis]+runway_nm,edge+runway_nm) if sign>0 else
                                          min(end[axis]-runway_nm,edge-runway_nm))
                    exit_point=tuple(exit_point)
                    runway=None if plane else p.SHAPE_SEGMENT(vec(end),vec(exit_point),world.track_width)
                    if runway is not None and not world.clear(runway,other,net):continue
                    diagnostic['runway_clear']+=1
                    for path in octile_paths(origin,end):
                        path=[q for i,q in enumerate(path) if not i or q!=path[i-1]];key=(tuple(path),face,end,exit_point)
                        if key in seen:continue
                        segments=[p.SHAPE_SEGMENT(vec(a),vec(z),world.track_width) for a,z in zip(path,path[1:])]
                        if not all(world.clear(shape,face,net) for shape in segments):continue
                        diagnostic['lead_clear']+=1
                        seen.add(key);choices.append({'net':net,'pad_ids':[d.m_Uuid.AsString() for d in pads],
                            'path':path,'layer':face,'via_nm':end,'exit_nm':exit_point,'exit_layer':other,'axis':axis,'sign':sign,
                            'circle':circle,'hole':p.SHAPE_CIRCLE(vec(end),world.drill//2),
                            'shapes':[(face,shape) for shape in segments]+[(p.F_Cu,circle),(p.B_Cu,circle)]+([] if plane else [(other,runway)]),
                            'cost':sum(distance(a,z) for a,z in zip(path,path[1:]))+distance(end,exit_point)})
                    if len(choices)>=max_candidates:break
                if len(choices)>=max_candidates:break
            if not choices:
                report.update(reason='No legal outward interface candidates',missing_net=net,candidate_diagnostic=diagnostic);return report
            domains.append(sorted(choices,key=lambda c:(c['cost'],c['via_nm'],c['path']))[:max_candidates])
        report['candidate_counts']=[len(rows) for rows in domains]
        report['group_labels']=[{'net':net,'pads':sorted(d.GetParentFootprint().GetReference()+':'+d.GetNumber() for d in pads)} for net,pads in groups]
        def compatible(a,z):
            return _compatible_ports(world,a,z,clearance_nm)
        selected,stats=_select(domains,compatible,deadline,max_states);report.update(stats)
        if selected is None:
            report['reason']=stats.get('budget_reason','No mutually clear short interfaces within the candidate set');return report
        for i in sorted(selected):
            if time.monotonic()>=deadline:raise TimeoutError('Port commit budget exhausted')
            row=domains[i][selected[i]];mark=len(world.added)
            world.add_path(row['path'],row['layer'],row['net']);world.add_via(row['via_nm'],row['net']);via_id=world.added[-1]
            if row['via_nm']!=row['exit_nm']:world.add_path([row['via_nm'],row['exit_nm']],row['exit_layer'],row['net'])
            added=world.added[:]
            report['ports'].append({key:row[key] for key in ('net','pad_ids','via_nm','exit_nm','exit_layer','axis','sign')})
            report['ports'][-1].update(via_id=via_id,added_ids=world.added[mark:])
        require_via_land_clearance(board,deadline=deadline)
        after=snapshot(board)
        if after['footprints']!=before['footprints'] or any(after['tracks'].get(uid)!=shape for uid,shape in before['tracks'].items()):
            raise ValueError('Controller interfaces changed original components/copper')
        actual=copper_graph(board,nets,deadline);ids={node['item'].m_Uuid.AsString():i for i,node in enumerate(actual['nodes'])}
        for port in report['ports']:
            if any(actual['find'](ids[uid])!=actual['find'](ids[port['via_id']]) for uid in port['pad_ids']):
                raise ValueError('Saved native pad group does not reach its access via')
        report.update(accepted=True,added_ids=added)
    except TimeoutError as error:
        report['reason']=str(error)
    finally:
        if not report['accepted']:
            for track in list(board.GetTracks()):
                if track.m_Uuid.AsString() not in before['tracks']:board.Delete(track)
            report['ports']=[];report['added_ids']=[]
        report['elapsed_s']=time.monotonic()-started
    return report
