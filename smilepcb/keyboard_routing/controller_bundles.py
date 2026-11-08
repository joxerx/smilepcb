"""Atomic native-checked continuation of whole ordered MCU banks."""
from collections import defaultdict
from copy import deepcopy
import itertools,math,time
from smilepcb.native_worker import p,snapshot
from .bank_geometry import terminal_origin
from .corridor_geometry import turn_bundle
from .local_router import LocalRouter,vec
from .native_graph import copper_graph


def extend_controller_bundles(board,ports,*,deadline,targets=None):
    """Keep lane identity and common turns, or leave the source candidate exact."""
    before=snapshot(board);result={'accepted':False,'ports':deepcopy(ports),'groups':[],'added_ids':[]}
    targets={} if targets is None else targets
    def world():return LocalRouter(board,deadline=deadline,compact=True,track_width_nm=board.GetDesignSettings().m_TrackMinWidth or 150000)
    def heading(port):
        a=terminal_origin(port);z=port['exit_nm'];d=(z[0]-a[0],z[1]-a[1]);g=math.gcd(*d)
        return (d[0]//g,d[1]//g) if g else (0,0)
    groups=defaultdict(list)
    for port in result['ports']:
        terminal_id=port.get('terminal_id') or port.get('via_id');terminal=before['tracks'].get(terminal_id,{})
        # A plated-via source can already end in a real track on its new face.
        # Its source kind/receipt stays unchanged when that track is extended.
        via_track=(port.get('kind')=='via' and terminal_id in port['added_ids']
                   and terminal.get('class')=='PCB_TRACK' and terminal.get('net')==port['net']
                   and terminal.get('layer')==port['exit_layer']
                   and {tuple(terminal['start']),tuple(terminal['end'])}=={tuple(port['exit_nm']),terminal_origin(port)})
        if port.get('kind')=='same_layer' or via_track:groups[(tuple(port['bank']),port['exit_layer'],heading(port))].append(port)
    try:
        if time.monotonic()>=deadline:raise TimeoutError('Controller bundle deadline exhausted')
        for (bank,face,h),group in sorted(groups.items()):
            if len(group)<2:continue
            if h not in ((1,0),(-1,0),(0,1),(0,-1)):raise ValueError('Unsupported physical bank departure')
            tangent=(-h[1],h[0]);group.sort(key=lambda q:sum(q['exit_nm'][i]*tangent[i] for i in (0,1)))
            starts=[tuple(q['exit_nm']) for q in group];normal=[sum(q[i]*tangent[i] for i in (0,1)) for q in starts];span=max(normal)-min(normal)
            center=tuple(sum(q[i] for q in starts)/len(starts) for i in (0,1));toward=[targets[q['net']] for q in group if q['net'] in targets]
            delta=tuple((sum(q[i] for q in toward)/len(toward)-center[i]) if toward else 0 for i in (0,1))
            forward=sum(h[i]*delta[i] for i in (0,1));side=sum(tangent[i]*delta[i] for i in (0,1))
            turns=sorted((-1,1),key=lambda t:(-t*side,t));run_min=math.ceil(span*(math.sqrt(2)-1))+400000;chamfer_min=math.ceil(span*(2-math.sqrt(2)))+350000
            straight=[('straight',[[a,(a[0]+h[0]*reach,a[1]+h[1]*reach)] for a in starts]) for reach in (8000000,6000000,4000000,2500000)]
            bends=[]
            for turn in turns:
                for reach in (8000000,6000000,4000000):
                    for run in (run_min,run_min+1000000,run_min+2000000):
                        try:paths=turn_bundle(starts,h,turn,run=run,chamfer=chamfer_min,reach=max(reach,chamfer_min+1000000))
                        except ValueError:continue
                        bends.append(('turn',paths))
            candidates=bends+straight if abs(side)>max(0,forward) else straight+bends
            selected=None;oracle=world();checked=0
            for kind,paths in candidates:
                if oracle.expired():raise TimeoutError('Controller bundle search deadline exhausted')
                checked+=1;shapes=[];valid=True
                for port,path in zip(group,paths):
                    own=[p.SHAPE_SEGMENT(vec(a),vec(z),oracle.track_width) for a,z in zip(path,path[1:])]
                    dx,dy=path[-1][0]-path[-2][0],path[-1][1]-path[-2][1];g=math.gcd(dx,dy)
                    future=(path[-1][0]+dx//g*500000,path[-1][1]+dy//g*500000)
                    own.append(p.SHAPE_SEGMENT(vec(path[-1]),vec(future),oracle.track_width))
                    if not all(oracle.clear(s,face,port['net']) for s in own):valid=False;break
                    shapes.append((port['net'],own))
                if not valid:continue
                for (a,ss),(b,tt) in itertools.combinations(shapes,2):
                    gap=max(oracle.minimum_clearance,oracle.net_clearances.get(a,0),oracle.net_clearances.get(b,0))
                    if a!=b and any(p.SHAPE.Collide(s,t,gap) for s in ss for t in tt):valid=False;break
                if valid:selected=(kind,paths);break
            if selected is None:
                result.update(reason='No coherent native-clear bank corridor',blocking_bank=bank);return result
            kind,paths=selected;row={'bank':bank,'nets':[q['net'] for q in group],'kind':kind,'paths':paths,'checked':checked};result['groups'].append(row)
            for port,path in zip(group,paths):
                if oracle.expired():raise TimeoutError('Controller bundle commit deadline exhausted')
                mark=len(oracle.added);oracle.add_path(path,face,port['net']);port['added_ids'].extend(oracle.added[mark:]);port.update(exit_nm=path[-1],heading_origin_nm=path[-2],terminal_id=oracle.added[-1])
        after=snapshot(board)
        if before['footprints']!=after['footprints'] or any(after['tracks'].get(u)!=t for u,t in before['tracks'].items()):
            raise ValueError('Controller bundle changed source geometry')
        graph=copper_graph(board,{q['net'] for q in result['ports']},deadline);ids={n['item'].m_Uuid.AsString():i for i,n in enumerate(graph['nodes'])}
        if any(graph['find'](ids[u])!=graph['find'](ids[q['terminal_id']]) for q in result['ports'] for u in q['pad_ids']):
            raise ValueError('Controller bundle lost its physical pad contact')
        result.update(accepted=True,added_ids=sorted(set(after['tracks'])-set(before['tracks'])))
    except TimeoutError as error:result['reason']=str(error)
    finally:
        if not result['accepted']:
            for t in list(board.GetTracks()):
                if t.m_Uuid.AsString() not in before['tracks']:board.Delete(t)
            result['ports']=deepcopy(ports);result['groups']=[];result['added_ids']=[]
    return result
