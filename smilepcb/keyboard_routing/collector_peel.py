"""Target-certified collector segment and exposed-edge branch peel.

Geometry derives from physical ports, native matrix pads and process rules.
No encoded matrix net names, GPIO pins or branch coordinates are used.
"""
import math,time
from dataclasses import dataclass
from copy import deepcopy
from smilepcb.native_worker import p
from smilepcb.keyboard_routing.local_router import LocalRouter,vec
from smilepcb.keyboard_routing.port_policy import PortPolicy
from smilepcb.keyboard_routing.bank_geometry import pitch_transition,clean_port_path,escape_path
from smilepcb.keyboard_routing.native_graph import copper_graph,nearest_to_node
from .collector_capacity import Search,GoalSchedule,heading

class Style:
    def accepts_path(self,points):return clean_port_path(points) is not None

def pocket_offset(via_diameter,neighbor_width,clearance,pitch,guard=1000):
    return max(0,math.ceil(via_diameter/2+neighbor_width/2+clearance+guard-pitch))

def compress(board,ports,nets,*,deadline,preserve_portal_pitch=False):
    from .interface_launches import current_launch_inventory
    before={t.m_Uuid.AsString() for t in board.GetTracks()}
    inventory=current_launch_inventory()
    launch_before=deepcopy((inventory.ports,inventory.resolved,inventory.repair_reserved)) if inventory is not None else None
    accepted=False;world=policy=next_policy=None
    def check_deadline():
        if time.monotonic()>=deadline:raise TimeoutError('Pitch transition deadline exhausted')
    def rollback():
        removed=False
        for item in list(board.GetTracks()):
            if item.m_Uuid.AsString() not in before:board.Delete(item);removed=True
        if removed:board.BuildConnectivity()
        if inventory is not None:inventory.ports,inventory.resolved,inventory.repair_reserved=deepcopy(launch_before)
    try:
        check_deadline()
        lookup={q['net']:q for q in ports};h=heading(lookup[nets[0]]);axis=0 if h[0] else 1;sign=h[axis]
        starts=[tuple(lookup[n]['exit_nm']) for n in nets];world=LocalRouter(board,deadline=deadline,compact=True,track_width_nm=board.GetDesignSettings().m_TrackMinWidth or 150000,path_policy=Style())
        policy=PortPolicy(board,ports,track_width_nm=world.track_width);world.port_policy=policy
        width=world.track_width;gap=max(world.minimum_clearance,*[world.net_clearances.get(n,0) for n in nets]);pitch=math.ceil((width+gap+20000)/50000)*50000
        # Keep a source rail that can already fit staggered legal vias. Shrinking
        # every bank first would irreversibly disable the coupled layer portals.
        tangent=1-axis
        source_pitch=min((abs(a[tangent]-z[tangent]) for i,a in enumerate(starts) for z in starts[i+1:]),default=0)
        portal_pitch=world.via_width/2+width/2+gap+1
        check_deadline()
        retained_pitch=preserve_portal_pitch and source_pitch>=portal_pitch
        if retained_pitch and len({a[axis] for a in starts})==1:
            span=max(a[tangent] for a in starts)-min(a[tangent] for a in starts)
            accepted=True
            return ports,{'accepted':True,'retained_portal_pitch':True,'pitch_nm':source_pitch,'old_span_nm':span,'new_span_nm':span,'paths':{}}
        if retained_pitch:pitch=source_pitch
        middle=(min(a[tangent] for a in starts)+max(a[tangent] for a in starts))/2;neck=max(width,gap+width//2)+1000
        order=sorted(range(len(nets)),key=lambda i:starts[i][tangent]);shift=0 if retained_pitch else max(abs(middle+(rank-(len(nets)-1)/2)*pitch-starts[i][tangent]) for rank,i in enumerate(order));minimum=math.ceil((shift+neck)/250000)*250000
        for extra in (0,250000,500000,1000000,2000000):
            check_deadline()
            rail=(max(a[axis] for a in starts) if sign>0 else min(a[axis] for a in starts))+sign*(minimum+extra)
            # Preserve every existing tangent position while aligning staggered
            # terminals; no narrower pitch is introduced into a usable bank.
            paths=([escape_path(a,axis,sign,rail,0,neck) for a in starts] if retained_pitch else pitch_transition(starts,axis,sign,rail,neck,pitch))
            if not paths or not all(paths):continue
            try:
                check_deadline()
                updated=deepcopy(lookup);world=LocalRouter(board,deadline=deadline,compact=True,track_width_nm=width,path_policy=Style())
                policy=PortPolicy(board,ports,track_width_nm=width);world.port_policy=policy
                for net,path in zip(nets,paths):
                    check_deadline()
                    if not all(policy.accepts_segment(a,z,lookup[net]['exit_layer'],net,width) and world.clear(p.SHAPE_SEGMENT(vec(a),vec(z),width),lookup[net]['exit_layer'],net) for a,z in zip(path,path[1:])):raise ValueError('Pitch transition is not native-clear')
                    check_deadline()
                    mark=len(world.added);world.add_path(path,lookup[net]['exit_layer'],net);ids=world.added[mark:];q=updated[net];q['added_ids'].extend(ids);q.update(terminal_id=ids[-1],exit_nm=path[-1],heading_origin_nm=path[-2])
                # A connected short tail is not a usable routing terminal. Check
                # the same first-contact boundary the following planner will use.
                check_deadline()
                live=[updated[q['net']] for q in ports];next_policy=PortPolicy(board,live,track_width_nm=width);world.port_policy=next_policy
                for net in nets:
                    check_deadline()
                    q=updated[net];a=tuple(q['exit_nm']);z=tuple(a[i]+h[i]*500000 for i in (0,1))
                    if not next_policy.accepts_segment(a,z,q['exit_layer'],net,width) or not world.clear(p.SHAPE_SEGMENT(vec(a),vec(z),width),q['exit_layer'],net):raise ValueError('Pitch transition leaves an unusable physical source departure')
                check_deadline()
                oldspan=max(a[tangent] for a in starts)-min(a[tangent] for a in starts)
                accepted=True
                newspan=oldspan if retained_pitch else pitch*(len(nets)-1)
                return [updated[q['net']] for q in ports],{'accepted':True,'retained_portal_pitch':bool(retained_pitch),'old_span_nm':oldspan,'new_span_nm':newspan,'pitch_nm':pitch,'clearance_nm':gap,'track_width_nm':width,'old_reserved_width_nm':oldspan+width+2*gap,'new_reserved_width_nm':newspan+width+2*gap,'paths':dict(zip(nets,paths))}
            except ValueError:
                check_deadline()
            finally:
                if not accepted:
                    # Native indexes and retired policies reference candidate
                    # copper. Dispose of both before deletion and the next try.
                    world=policy=next_policy=None
                    rollback()
        check_deadline()
        return ports,{'accepted':False,'reason':'No native-clear monotone pitch transition'}
    except TimeoutError as error:
        return ports,{'accepted':False,'budget_exhausted':True,'reason':'Pitch transition budget exhausted: '+str(error)}
    finally:
        if not accepted:
            world=policy=next_policy=None
            rollback()

@dataclass(frozen=True)
class FieldTarget:
    anchor_pad: object
    item: object
    point: tuple


@dataclass(frozen=True)
class BranchGoal:
    leaf_net: str
    witnesses: tuple
    side: int
    heading: tuple
    reach_nm: int

    @property
    def point(self):return self.witnesses[0].point


def field_targets(board,cfg,ports,owned_ids,deadline):
    """Keep an electrical pad witness separate from usable tree copper."""
    controller={q['ref'] for q in cfg['parts'] if q['region']=='controller'}
    nets={q['net'] for q in ports};result={n:[] for n in nets}
    pads=[d for f in board.GetFootprints() if f.GetReference() not in controller
          for d in f.Pads() if d.GetNumber() and d.GetNetname() in nets]
    for d in pads:result[d.GetNetname()].append((d,{'item':d,'points':[(d.GetPosition().x,d.GetPosition().y)],'zone':False}))
    owned=set(owned_ids)
    if owned:
        if not owned<={t.m_Uuid.AsString() for t in board.GetTracks()}:raise ValueError('Missing owned field copper')
        graph=copper_graph(board,nets,deadline);anchors={};external={d.m_Uuid.AsString() for d in pads}
        for i,node in enumerate(graph['nodes']):
            d=node['item']
            if d.m_Uuid.AsString() in external:anchors.setdefault(graph['find'](i),d)
        for i,node in enumerate(graph['nodes']):
            item=node['item'];anchor=anchors.get(graph['find'](i))
            # Arc nearest-point geometry is not a projection onto its chord.
            if item.m_Uuid.AsString() in owned and item.GetClass()=='PCB_TRACK' and anchor is not None:
                result[item.GetNetname()].append((anchor,node))
    return result


def witness_order(target):
    """Prefer real face access and physical geometry before arbitrary UUIDs."""
    item=target.item;faces=tuple(layer for layer in (p.F_Cu,p.B_Cu) if item.IsOnLayer(layer));kind=item.GetClass()
    if kind=='PAD':
        position=item.GetPosition();size=item.GetSize();drill=item.GetDrillSize()
        geometry=(position.x,position.y,size.x,size.y,drill.x,drill.y)
        terminal=(item.GetParentFootprint().GetReference(),item.GetNumber())
    else:
        a,z=item.GetStart(),item.GetEnd();ends=sorted(((a.x,a.y),(z.x,z.y)))
        geometry=(*ends[0],*ends[1],item.GetWidth());terminal=('', '')
    return -len(faces),faces,kind,geometry,terminal,item.m_Uuid.AsString()


def branch_goals(board,cfg,ports,nets,targets):
    """Inventory distinct exposed contacts, retaining actual face witnesses."""
    lookup={q['net']:q for q in ports};source=heading(lookup[nets[0]]);t=(-source[1],source[0])
    ordered=sorted(nets,key=lambda n:sum(lookup[n]['exit_nm'][i]*t[i] for i in (0,1)));options=[]
    planned={tuple(lookup[n]['collector_heading']) for n in nets if lookup[n].get('collector_heading')}
    if len(planned)>1:raise ValueError('A collector cohort has incompatible planned directions')
    headings=tuple(planned) if planned else (source,(-source[1],source[0]),(source[1],-source[0]))
    for h in headings:
        normal_axis=(-h[1],h[0])
        for net,side in ((ordered[0],-1),(ordered[-1],1)):
            origin=lookup[net]['exit_nm']
            for anchor,node in targets[net]:
                points=set(node['points'])
                if node['item'].GetClass()=='PCB_TRACK':points.add(nearest_to_node(node,origin))
                for point in sorted(points):
                    delta=tuple(point[i]-origin[i] for i in (0,1))
                    forward=sum(delta[i]*h[i] for i in (0,1));normal=sum(delta[i]*normal_axis[i] for i in (0,1))
                    if forward<=1000000 or len(nets)>1 and normal*side<0:continue
                    score=forward+abs(normal)+(1500000 if h!=source else 0)
                    target=FieldTarget(anchor,node['item'],point)
                    options.append((score,forward,abs(normal),net,anchor.GetParentFootprint().GetReference(),anchor.GetNumber(),point,target,side if len(nets)>1 else (1 if normal>0 else -1),h))
    if not options:raise ValueError('No useful external field contact at an exposed collector edge')
    groups={}
    for q in sorted(options,key=lambda q:(*q[:7],witness_order(q[7]))):
        key=(q[3],tuple(q[9]),q[8],q[6]);group=groups.setdefault(key,[])
        if not any(w.item.m_Uuid.AsString()==q[7].item.m_Uuid.AsString() for w in group):group.append(q[7])
    return [BranchGoal(net,tuple(witnesses),side,h,max(1000000,sum((point[i]-lookup[net]['exit_nm'][i])*h[i] for i in (0,1))-1000000))
            for (net,h,side,point),witnesses in groups.items()]


def pick_branch(board,cfg,ports,nets,targets):
    goal=branch_goals(board,cfg,ports,nets,targets)[0]
    return goal.leaf_net,goal.witnesses[0],goal.side,goal.heading

class CollectorSearch(Search):
    def __init__(self,board,ports,nets,targets,*,deadline,target=None,leaf_net=None,side=None,goal_heading=None,goals=None):
        if goals is None:
            h=goal_heading or heading(next(q for q in ports if q['net']==nets[0]));origin=next(q['exit_nm'] for q in ports if q['net']==leaf_net)
            forward=sum((target.point[i]-origin[i])*h[i] for i in (0,1))
            goals=[BranchGoal(leaf_net,(target,),side,tuple(h),max(1000000,forward-1000000))]
        if not goals:raise ValueError('Collector needs a real field contact goal')
        self.goal_inventory_count=len(goals);self.goals=tuple(goals);self.search_extent=max(g.reach_nm for g in self.goals)
        super().__init__(board,ports,nets,targets,deadline=deadline,reach_nm=self.goals[0].reach_nm,grid_nm=500000,allow_vias=True)
        self.branch=None;self.interface_graph=None;self.select_goal(0)

    def select_goal(self,index):
        self.goal_index=index;goal=self.goals[index];target=goal.witnesses[0]
        self.goal_heading=goal.heading;self.toward=goal.heading;self.reach=goal.reach_nm;self.required_progress=self.reach
        self.target_pad=target.anchor_pad;self.target_contact=target.item;self.target_point=goal.point;self.leaf_net=goal.leaf_net;self.side=goal.side

    def goal_ready(self,state):
        return state[4]>=self.run_cap-1 and self.physical_goal(state)

    def solve(self):
        return super().solve(GoalSchedule(len(self.goals),deadline=self.deadline))

    def physical_goal(self,state):
        from .collector_capacity import DIRS
        if DIRS[state[2]]!=self.goal_heading:return False
        for net in self.nets:
            center=self.at(state);normal=(-self.goal_heading[1],self.goal_heading[0]);end=tuple(center[i]+normal[i]*self.offsets[net] for i in (0,1));d=tuple(end[i]-self.lookup[net]['exit_nm'][i] for i in (0,1))
            if sum(d[i]*self.goal_heading[i] for i in (0,1))<self.reach:return False
        return True
    def port_goal(self,net,q):
        start=self.lookup[net]['exit_nm'];return sum((q['exit_nm'][i]-start[i])*self.goal_heading[i] for i in (0,1))>=self.reach
    def commit(self,edges):
        error='No real field contact witness accepted'
        self.branch=None;self.interface_graph=None
        for target in self.goals[self.goal_index].witnesses:
            if time.monotonic()>=self.deadline:break
            self.target_pad=target.anchor_pad;self.target_contact=target.item;self.target_point=target.point
            updated,added,error=self.commit_witness(edges)
            if updated is not None:return updated,added,None
        return None,[],error

    def commit_witness(self,edges):
        before={t.m_Uuid.AsString() for t in self.board.GetTracks()};updated,added,error=super().commit(edges)
        if updated is None:return updated,added,error
        try:
            q=updated[self.leaf_net];start=tuple(q['exit_nm']);h=self.goal_heading;t=(-h[1],h[0]);world=LocalRouter(self.board,deadline=min(self.deadline,time.monotonic()+3),compact=True,track_width_nm=self.width,path_policy=Style())
            live_ports=[updated.get(port['net'],port) for port in self.ports];policy=PortPolicy(self.board,live_ports,track_width_nm=self.width);world.port_policy=policy
            neighbors=[n for n in self.nets if n!=self.leaf_net];separation=min((abs(sum((updated[n]['exit_nm'][i]-start[i])*t[i] for i in (0,1))) for n in neighbors),default=world.via_width+self.gap)
            minimum=pocket_offset(world.via_width,self.width,self.gap,separation);base=math.ceil(minimum/25000)*25000
            success=False
            options=[]
            if self.target_contact.IsOnLayer(q['exit_layer']):
                direct=world.leg(start,self.target_point,q['exit_layer'],self.leaf_net)
                if direct is not None:options.append((None,0,[],direct,q['exit_layer']))
            other=p.B_Cu if q['exit_layer']==p.F_Cu else p.F_Cu
            if self.target_contact.IsOnLayer(other):
                for extra in (0,250000,500000,1000000,1500000):
                    if time.monotonic()>=self.deadline:break
                    offset=base+extra;advance=max(500000,offset);via=tuple(start[i]+h[i]*advance+t[i]*self.side*offset for i in (0,1))
                    if not policy.accepts_via(via,self.leaf_net,world.via_width) or not world.via_clear(via,self.leaf_net):continue
                    approach=world.leg(start,via,q['exit_layer'],self.leaf_net)
                    branch=world.leg(via,self.target_point,other,self.leaf_net)
                    if approach is not None and branch is not None:options.append((via,offset,approach,branch,other))
            for via,offset,approach,branch,face in options:
                if not all(policy.accepts_segment(a,z,q['exit_layer'],self.leaf_net,self.width) for a,z in zip(approach,approach[1:])) or not all(policy.accepts_segment(a,z,face,self.leaf_net,self.width) for a,z in zip(branch,branch[1:])):continue
                mark=len(world.added)
                if approach:world.add_path(approach,q['exit_layer'],self.leaf_net)
                if via is not None:world.add_via(via,self.leaf_net)
                world.add_path(branch,face,self.leaf_net);ids=world.added[mark:];q['added_ids'].extend(ids);added.extend(ids)
                graph=copper_graph(self.board,{self.leaf_net},self.deadline);nodes={node['item'].m_Uuid.AsString():i for i,node in enumerate(graph['nodes'])};target_id=self.target_pad.m_Uuid.AsString()
                if target_id not in nodes or any(graph['find'](nodes[u])!=graph['find'](nodes[target_id]) for u in q['pad_ids']):raise ValueError('Peel did not reach the real field component')
                if via is not None and q['kind']=='same_layer':
                    uid=next(uid for uid in ids if isinstance(next(z for z in self.board.GetTracks() if z.m_Uuid.AsString()==uid),p.PCB_VIA));q.update(kind='via',via_id=uid,via_nm=via)
                self.branch={'net':self.leaf_net,'collector_heading':self.goal_heading,'field_pad_id':target_id,'field_contact_id':self.target_contact.m_Uuid.AsString(),'field_contact_class':self.target_contact.GetClass(),'field_ref':self.target_pad.GetParentFootprint().GetReference(),'field_pad_number':self.target_pad.GetNumber(),'target_nm':self.target_point,'via_nm':via,'outward_offset_nm':offset,'adjacent_lane_pitch_nm':separation,'minimum_outward_offset_nm':minimum,'approach':approach,'branch':branch,'real_field_contact_verified':True}
                self.interface_graph=graph
                success=True;break
            if not success:raise ValueError('No native-clear exposed-edge branch and field contact')
            return updated,added,None
        except (ValueError,TimeoutError) as error:
            self.branch=None;self.interface_graph=None
            for z in list(self.board.GetTracks()):
                if z.m_Uuid.AsString() not in before:self.board.Delete(z)
            return None,[],str(error)
