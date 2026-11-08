"""Finite-width octilinear search with native-checked coupled lanes.

A centerline is only a capacity proposal. Expanded tracks, vias and source
contacts must coexist before any proposal is committed.
"""
import heapq,math,time
from collections import Counter,defaultdict
from copy import deepcopy
from itertools import chain
from smilepcb.native_worker import p,snapshot
from smilepcb.copper_obstacles import own_clearance
from smilepcb.keyboard_routing.local_router import LocalRouter,vec
from smilepcb.keyboard_routing.port_policy import PortPolicy
from smilepcb.keyboard_routing.corridor_geometry import offset_path
from .generation import current_generation_profile

DIRS=((1,0),(1,1),(0,1),(-1,1),(-1,0),(-1,-1),(0,-1),(1,-1))

def unit(d):
    z=math.hypot(*d);return (d[0]/z,d[1]/z)

def clean(points):
    result=[]
    for q in points:
        q=tuple(q)
        if result and q==result[-1]:continue
        while len(result)>1:
            a,b=result[-2:];u=(b[0]-a[0],b[1]-a[1]);v=(q[0]-b[0],q[1]-b[1])
            if u[0]*v[1]!=u[1]*v[0] or sum(u[i]*v[i] for i in (0,1))<=0:break
            result.pop()
        result.append(q)
    return result

def polygon(points):
    result=p.SHAPE_POLY_SET();result.NewOutline()
    for q in points:result.Append(round(q[0]),round(q[1]))
    return result

def band(a,z,radius,cap):
    h=unit((z[0]-a[0],z[1]-a[1]));n=(-h[1],h[0])
    return polygon([tuple(q[i]+s*n[i]*radius+t*h[i]*cap for i in (0,1))
                    for q,s,t in ((a,-1,-1),(z,-1,1),(z,1,1),(a,1,-1))])

def shifted(points,offset):
    points=clean(points)
    return points if not offset else offset_path(points,offset)

def heading(port):
    a=port['heading_origin_nm'];z=port['exit_nm'];d=(z[0]-a[0],z[1]-a[1]);g=math.gcd(*d)
    if not g:raise ValueError('Missing physical port heading')
    h=(d[0]//g,d[1]//g)
    if h not in DIRS or h[0] and h[1]:raise ValueError('Collector source rails must be cardinal')
    return h

def groups_for(board,cfg,ports):
    chip=next(f for f in board.GetFootprints() if f.GetReference()=='U_MCU');center=chip.GetPosition()
    pads={d.m_Uuid.AsString():d for d in chip.Pads()};family={}
    mcu=next(q for q in cfg['parts'] if q['ref']=='U_MCU')
    for role in ('rows','cols'):
        for gpio in cfg['layout']['gpio'][role]:family[mcu['nets'][cfg['mcu_gpio_pads'][gpio]]]=role
    groups=defaultdict(list)
    for q in ports:
        h=heading(q);a=q['exit_nm'];normal=h[0]*a[0]+h[1]*a[1]
        source=pads[q['pad_ids'][0]].GetPosition();dx,dy=source.x-center.x,source.y-center.y
        physical=(0,1 if dx>0 else -1) if abs(dx)>abs(dy) else (1,1 if dy>0 else -1)
        groups[physical,q['exit_layer'],h,normal,tuple(q.get('collector_heading',()))].append(q['net'])
    lookup={q['net']:q for q in ports}
    values=[]
    for key,nets in groups.items():
        h=key[2];t=(-h[1],h[0]);nets.sort(key=lambda n:sum(lookup[n]['exit_nm'][i]*t[i] for i in (0,1)))
        values.append(nets)
    return sorted(values,key=lambda ns:(-len(ns),tuple(ns)))


def _reserved_width(span,width,gap):
    # Search floors the coordinate midpoint, then ceils radius+half-width.
    # Odd span and odd width must round separately, including mirrored rails.
    return 2*((span+1)//2+(width+1)//2+gap)


def _twice_safe_pitch(width,via_width,gap):
    nominal=((width+gap+20000+49999)//50000)*50000
    return max(2*nominal,via_width+width+2*gap+2)


def _partition_cut(positions,width,via_width,gaps):
    gap=max(gaps);safe=_twice_safe_pitch(width,via_width,gap)
    parent=_reserved_width(positions[-1]-positions[0],width,gap);best=None
    for cut in range(1,len(positions)):
        separation=positions[cut]-positions[cut-1]
        if separation<safe:continue
        child_gaps=[max(gaps[:cut]),max(gaps[cut:])]
        widths=[_reserved_width(positions[cut-1]-positions[0],width,child_gaps[0]),
                _reserved_width(positions[-1]-positions[cut],width,child_gaps[1])]
        if 2*max(widths)>parent:continue
        key=(max(widths),cut)
        if best is None or key<best[0]:
            best=(key,{'cut_index':cut,'cut_gap_nm':separation,'gap_nm':gap,
                       'twice_safe_pitch_nm':safe,'parent_reserved_width_nm':parent,
                       'child_reserved_widths_nm':widths,'child_gaps_nm':child_gaps})
    return best[1] if best is not None else None


def partition_initial(board,ports,groups,*,deadline):
    """Split at most one discontinuous source rail within the existing budget.

    groups_for has already certified cardinal, common-layer source rails and
    sorted their physical lane order. This changes only the initial queue;
    every sibling's actual copper and launch reservation remain live.
    """
    if time.monotonic()>=deadline or not any(len(ns)>1 for ns in groups):return groups,None
    rules=board.GetDesignSettings();width=rules.m_TrackMinWidth or 150000
    drill=max(p.FromMM(.2),rules.m_MinThroughDrill or p.FromMM(.3))
    via_width=max(rules.m_ViasMinSize,drill+2*rules.m_ViasMinAnnularWidth,p.FromMM(.5))
    floor=max(rules.m_MinClearance,current_generation_profile().foreign_copper_clearance_nm)
    minimum_separation=_twice_safe_pitch(width,via_width,floor)
    lookup={q['net']:q for q in ports}
    for index,nets in enumerate(groups):
        if time.monotonic()>=deadline:return groups,None
        if len(nets)<2:continue
        h=heading(lookup[nets[0]]);t=(-h[1],h[0])
        positions=[sum(lookup[n]['exit_nm'][i]*t[i] for i in (0,1)) for n in nets]
        # Necessary lower-bound test only. Compact cohorts never scan copper
        # metadata or construct a native shape index for this heuristic.
        if max(b-a for a,b in zip(positions,positions[1:]))<minimum_separation:continue
        owned={u for n in nets for u in lookup[n]['added_ids']};gaps={n:floor for n in nets}
        items=chain((pad for fp in board.GetFootprints() for pad in fp.Pads()),
                    (item for item in board.GetTracks() if item.m_Uuid.AsString() not in owned))
        for item in items:
            if time.monotonic()>=deadline:return groups,None
            net=item.GetNetname()
            if net not in gaps:continue
            for face in (p.F_Cu,p.B_Cu):
                if item.IsOnLayer(face):
                    # Match LocalRouter.index: own_clearance adds its margin
                    # to the raw native floor, before the generation floor.
                    gaps[net]=max(gaps[net],own_clearance(item,face,rules.m_MinClearance))
        if time.monotonic()>=deadline:return groups,None
        report=_partition_cut(positions,width,via_width,[gaps[n] for n in nets])
        if report is None:continue
        cut=report['cut_index'];children=[nets[:cut],nets[cut:]]
        report.update(initial_index=index,parent=nets[:],children=children,
                      track_width_nm=width,via_width_nm=via_width)
        return groups[:index]+children+groups[index+1:],report
    return groups,None

class GoalSchedule:
    """Share existing search budgets across first contact opportunities."""
    def __init__(self,count,*,deadline):
        self.count=count;self.deadline=deadline;self.first=0;self.retry=0

    def next_turn(self,*,now,expanded,rejections):
        if not self.count or now>=self.deadline or expanded>=18000 or rejections>=12:return None
        if self.first<self.count:
            index=self.first;remaining=self.count-self.first;self.first+=1
        else:
            index=self.retry%self.count;self.retry+=1;remaining=self.count
        return index,now+(self.deadline-now)/remaining,expanded+math.ceil((18000-expanded)/remaining)


class Search:
    def __init__(self,board,ports,nets,targets,*,deadline,reach_nm,grid_nm,allow_vias):
        self.board=board;self.ports=ports;self.lookup={q['net']:q for q in ports};self.nets=list(nets);self.deadline=deadline;self.reach=reach_nm;self.required_progress=reach_nm*.5;self.step=grid_nm;self.allow_vias=allow_vias
        self.h=heading(self.lookup[nets[0]]);self.face=self.lookup[nets[0]]['exit_layer'];self.heading_index=DIRS.index(self.h);t=(-self.h[1],self.h[0])
        starts=[tuple(self.lookup[n]['exit_nm']) for n in nets]
        self.origin=tuple((starts[0][i]+starts[-1][i])//2 for i in (0,1));self.offsets={n:sum((a[i]-self.origin[i])*t[i] for i in (0,1)) for n,a in zip(nets,starts)}
        if any(tuple(self.origin[i]+t[i]*self.offsets[n] for i in (0,1))!=a for n,a in zip(nets,starts)):raise ValueError('Group is not on one source rail')
        owned={u for n in nets for u in self.lookup[n]['added_ids']}
        self.world=LocalRouter(board,deadline=deadline,compact=True,ignored_copper=owned,track_width_nm=board.GetDesignSettings().m_TrackMinWidth or 150000)
        self.world.launch_policy=None
        from .interface_launches import current_launches,interface_identity
        interfaces={interface_identity(q):q for q in current_launches()}
        interfaces.update((interface_identity(q),q) for q in ports)
        cohort={interface_identity(self.lookup[n]) for n in nets}
        others=[q for key,q in interfaces.items() if key not in cohort]
        # An explicit empty policy excludes this cohort; assigning None would
        # restore the captured scope and block the cohort's own axis probe.
        self.world.port_policy=PortPolicy(board,others,track_width_nm=self.world.track_width)
        self.detail=LocalRouter(board,deadline=deadline,compact=True,track_width_nm=self.world.track_width)
        self.detail.port_policy=PortPolicy(board,ports,track_width_nm=self.world.track_width)
        self.width=self.world.track_width;self.radius=math.ceil(max(abs(q) for q in self.offsets.values())+self.width/2)
        self.gap=max(self.world.minimum_clearance,*[self.world.net_clearances.get(n,0) for n in nets]);self.reserved_width=2*(self.radius+self.gap)
        mean=tuple(sum(targets[n][i] for n in nets)/len(nets) for i in (0,1));delta=tuple(mean[i]-self.origin[i] for i in (0,1));self.toward=unit(delta) if any(delta) else self.h
        self.cache={};self.portal_cache={};self.blockers=Counter();self.stats={'expanded':0,'generated':0,'native_goal_rejections':0,'portal_proposals':0,'portal_legal':0}
        self.run_cap=max(3,math.ceil((2*self.radius*(math.sqrt(2)-1)+self.width)/self.step)+1)
        self.best_partial=None;self.best_partial_score=-math.inf

    def at(self,state):return tuple(self.origin[i]+state[i]*self.step for i in (0,1))
    def shape_clear(self,shape,face):
        collisions=list(self.world.collisions(shape,face,'__cohort_capacity_probe__'))
        if collisions:self.blockers.update(collisions[:3]);return False
        return True

    def edge_clear(self,state,new,turned):
        key=(state,new,turned)
        if key in self.cache:return self.cache[key]
        a,z=self.at(state),self.at(new);face=state[3];okay=self.shape_clear(band(a,z,self.radius,self.width/2+1),face)
        if okay and turned:
            h=DIRS[state[2]];back=tuple(a[i]-h[i]*self.step*state[4] for i in (0,1))
            try:
                spine=[back,a,z];outer=shifted(spine,self.radius);inner=shifted(spine,-self.radius)
                okay=self.shape_clear(polygon(outer+list(reversed(inner))),face)
            except ValueError:okay=False
        self.cache[key]=okay;return okay

    def portal(self,state,*,deadline=None):
        stop=self.deadline if deadline is None else min(self.deadline,deadline)
        if time.monotonic()>=stop:return None
        original=self.detail.deadline
        self.detail.deadline=stop if original is None else min(original,stop)
        try:return self._portal(state,deadline=stop)
        finally:
            self.detail.deadline=original
            if time.monotonic()>=stop and self.portal_cache.get(state[:4]) is None:
                self.portal_cache.pop(state[:4],None)

    def _portal(self,state,*,deadline):
        key=state[:4]
        def expired():
            if time.monotonic()<deadline:return False
            self.portal_cache.pop(key,None);return True
        if key in self.portal_cache:return self.portal_cache[key]
        self.stats['portal_proposals']+=1;self.portal_cache[key]=None
        if not self.allow_vias or len(self.nets)>1 and min(abs(a-b) for i,a in enumerate(self.offsets.values()) for j,b in enumerate(self.offsets.values()) if i<j)<self.world.via_width/2+self.width/2+self.gap+1:return None
        a=self.at(state);h=DIRS[state[2]];other=p.B_Cu if state[3]==p.F_Cu else p.F_Cu
        pitch=min((abs(a-b) for i,a in enumerate(self.offsets.values()) for j,b in enumerate(self.offsets.values()) if i<j),default=self.world.via_width+self.gap)
        required=max(self.world.via_width+self.gap,self.world.via_width/2+self.world.drill/2+self.world.generation.via_to_hole_clearance_nm)+1000
        stagger=math.sqrt(max(0,required*required-pitch*pitch));stagger_units=max(1,math.ceil(stagger/(self.step*math.hypot(*h))))
        length_units=2+stagger_units if len(self.nets)>1 else 2
        z=tuple(a[i]+h[i]*self.step*length_units for i in (0,1))
        envelope_radius=math.ceil(max(abs(q) for q in self.offsets.values())+self.world.via_width/2)
        # The both-face envelope includes empty space before/after each via.
        # Keep its blocker diagnostics; only actual copper can reject a portal.
        for face in (state[3],other):
            if expired():return None
            if not self.shape_clear(band(a,z,envelope_radius,self.width/2+1),face):
                self.stats['portal_envelope_blocked']=self.stats.get('portal_envelope_blocked',0)+1;break
        if expired():return None
        operations={};vias=[];shapes=[]
        for rank,n in enumerate(self.nets):
            ends=shifted([a,z],self.offsets[n]);entry,exit=ends;v=tuple(entry[i]+h[i]*self.step*(1+(rank%2)*stagger_units) for i in (0,1))
            if expired() or not self.detail.via_clear(v,n):return None
            if any(expired() or not self.detail.via_pair_clear(v,old) for old in vias):return None
            vias.append(v)
            ss=[(state[3],p.SHAPE_SEGMENT(vec(entry),vec(v),self.width)),(other,p.SHAPE_SEGMENT(vec(v),vec(exit),self.width))]
            circle=p.SHAPE_CIRCLE(vec(v),self.world.via_width//2)
            if not all(not expired() and self.detail.clear(s,face,n) for face,s in ss):return None
            ss.extend([(p.F_Cu,circle),(p.B_Cu,circle)]);shapes.append((n,ss))
            operations[n]=[('track',state[3],[entry,v]),('via',v),('track',other,[v,exit])]
        for i,(n,ss) in enumerate(shapes):
            for m,tt in shapes[i+1:]:
                gap=max(self.gap,self.world.net_clearances.get(n,0),self.world.net_clearances.get(m,0))
                if any(expired() or p.SHAPE.Collide(s,t,gap) for f,s in ss for g,t in tt if f==g):return None
        if expired():return None
        result={'axis_start':a,'axis_end':z,'layer':state[3],'end_layer':other,'heading':h,'units':length_units,'operations':operations}
        self.stats['portal_legal']+=1;self.portal_cache[key]=result;return result

    def physical_goal(self,state):
        point=self.at(state);h=DIRS[state[2]]
        probe=[tuple(point[i]-h[i]*self.step for i in (0,1)),point]
        for net in self.nets:
            end=shifted(probe,self.offsets[net])[-1];start=self.lookup[net]['exit_nm'];d=tuple(end[i]-start[i] for i in (0,1))
            if math.hypot(*d)<self.reach or sum(d[i]*self.toward[i] for i in (0,1))<self.reach*.5:return False
        return True

    def port_goal(self,net,q):
        start=self.lookup[net]['exit_nm'];d=tuple(q['exit_nm'][i]-start[i] for i in (0,1))
        return math.hypot(*d)>=self.reach and sum(d[i]*self.toward[i] for i in (0,1))>=self.required_progress

    def reconstruct(self,state,parents):
        edges=[]
        while state in parents:
            previous,kind,payload=parents[state];edges.append((previous,state,kind,payload));state=previous
        return list(reversed(edges))

    def expanded_lanes(self,edges):
        ops={n:[] for n in self.nets};run=[self.origin];face=self.face
        def flush():
            if len(clean(run))>1:
                for n in self.nets:ops[n].append(('track',face,shifted(run,self.offsets[n])))
        for before,after,kind,payload in edges:
            if kind=='track':run.append(self.at(after))
            else:
                flush()
                for n in self.nets:ops[n].extend(payload['operations'][n])
                run=[self.at(after)];face=after[3]
        flush();return ops

    def commit(self,edges):
        before={t.m_Uuid.AsString() for t in self.board.GetTracks()};updated={};added=[]
        try:
            lanes=self.expanded_lanes(edges)
            world=LocalRouter(self.board,deadline=self.deadline,compact=True,track_width_nm=self.width);world.port_policy=PortPolicy(self.board,self.ports,track_width_nm=self.width)
            for net in self.nets:
                q=deepcopy(self.lookup[net]);first=True;new_vias=[];last=None;layer=None
                for op in lanes[net]:
                    if time.monotonic()>=self.deadline:raise TimeoutError('Coupled lane commit deadline')
                    if op[0]=='track':
                        _,layer,points=op;points=clean(points)
                        if len(points)<2:continue
                        if first and tuple(points[0])!=tuple(q['exit_nm']):raise ValueError('Lane misses actual source port')
                        if not first and tuple(points[0])!=last:raise ValueError('Discontinuous native lane')
                        for a,z in zip(points,points[1:]):
                            if not world.port_policy.accepts_segment(a,z,layer,net,self.width):raise ValueError('Expanded lane violates source first-contact boundary')
                            if not world.clear(p.SHAPE_SEGMENT(vec(a),vec(z),self.width),layer,net):raise ValueError('Expanded lane violates native clearance')
                        mark=len(world.added);world.add_path(points,layer,net);ids=world.added[mark:];q['added_ids'].extend(ids);added.extend(ids)
                        q.update(terminal_id=ids[-1],exit_nm=points[-1],heading_origin_nm=points[-2],exit_layer=layer);last=tuple(points[-1]);first=False
                    else:
                        _,v=op
                        if tuple(v)!=last or not world.port_policy.accepts_via(v,net,world.via_width) or not world.via_clear(v,net):raise ValueError('Invalid coupled lane via')
                        mark=len(world.added);world.add_via(v,net);uid=world.added[mark];q['added_ids'].append(uid);added.append(uid);new_vias.append((uid,v))
                if new_vias and q['kind']=='same_layer':q.update(kind='via',via_id=new_vias[0][0],via_nm=new_vias[0][1])
                if first:raise ValueError('Empty cohort lane')
                tip=tuple(q['exit_nm']);a=tuple(q['heading_origin_nm']);d=(tip[0]-a[0],tip[1]-a[1]);g=math.gcd(*d);end=tuple(tip[i]+d[i]//g*500000 for i in (0,1))
                if not world.clear(p.SHAPE_SEGMENT(vec(tip),vec(end),self.width),q['exit_layer'],net):raise ValueError('Outlet has no checked final departure')
                start=self.lookup[net]['exit_nm'];delta=tuple(q['exit_nm'][i]-start[i] for i in (0,1));actual_reach=math.hypot(*delta);actual_progress=sum(delta[i]*self.toward[i] for i in (0,1))
                if not self.port_goal(net,q):raise ValueError('Physical lane does not reach its declared goal')
                q['corridor_displacement_nm']=actual_reach;q['corridor_progress_nm']=actual_progress
                updated[net]=q
            return updated,added,None
        except (ValueError,TimeoutError) as error:
            for t in list(self.board.GetTracks()):
                if t.m_Uuid.AsString() not in before:self.board.Delete(t)
            return None,[],str(error)

    def heuristic(self,state):
        point=self.at(state);delta=tuple(point[i]-self.origin[i] for i in (0,1))
        return max(0,self.reach-math.hypot(*delta),self.required_progress-sum(delta[i]*self.toward[i] for i in (0,1)))

    def goal_ready(self,state):
        point=self.at(state);delta=tuple(point[i]-self.origin[i] for i in (0,1))
        return (math.hypot(*delta)>=self.reach and sum(delta[i]*self.toward[i] for i in (0,1))>=self.required_progress
                and state[4]>=self.run_cap-1 and self.physical_goal(state))

    def solve(self,agenda=None):
        start=(0,0,self.heading_index,self.face,0);serial=0;queue=[(self.heuristic(start)*1.6,0,serial,start)];costs={start:0};parents={};reason='frontier exhausted';accepted=None
        extent=getattr(self,'search_extent',self.reach);common_deadline=self.deadline
        proof_stop=common_deadline
        if agenda:
            proof_stop=common_deadline-min(1,max(0,(common_deadline-time.monotonic())/2))
            agenda.deadline=min(agenda.deadline,proof_stop)
        reached={};attempted=set();offered=set();exhausted=set();cached=[];new_turn=agenda is not None
        turn_deadline=common_deadline;planning_stop=common_deadline;turn_limit=18000;goal_index=0
        while queue or agenda:
            now=time.monotonic()
            if now>=common_deadline:reason='search deadline';break
            if agenda and now>=proof_stop:reason='native proof budget';break
            if self.stats['expanded']>=18000:reason='state budget';break
            if self.stats['native_goal_rejections']>=12:reason='native goal rejection budget';break
            if agenda and new_turn:
                turn=agenda.next_turn(now=now,expanded=self.stats['expanded'],rejections=self.stats['native_goal_rejections'])
                if turn is None:break
                goal_index,turn_deadline,turn_limit=turn;self.select_goal(goal_index);offered.add(goal_index);new_turn=False
                planning_stop=turn_deadline
                # Changing a terminal never resets the physical search graph.
                unique={(state,cost) for _,cost,_,state in queue if costs.get(state)==cost}
                queue=[]
                for state,cost in sorted(unique):
                    serial+=1;heapq.heappush(queue,(cost+1.6*self.heuristic(state),cost,serial,state))
                cached=sorted((cost,state) for state,cost in reached.items() if costs.get(state)==cost and (goal_index,state) not in attempted and self.goal_ready(state))
            if agenda and (now>=planning_stop or self.stats['expanded']>=turn_limit):new_turn=True;continue
            from_cache=bool(cached)
            if from_cache:
                cost,state=cached.pop(0)
            elif queue:
                _,cost,_,state=heapq.heappop(queue)
                if cost!=costs.get(state):continue
                self.stats['expanded']+=1;reached[state]=cost;exhausted.clear()
            else:
                if not agenda:break
                exhausted.add(goal_index)
                if len(exhausted)==agenda.count:break
                new_turn=True;continue
            a=self.at(state);delta=tuple(a[i]-self.origin[i] for i in (0,1));radial=math.hypot(*delta);progress=sum(delta[i]*self.toward[i] for i in (0,1))
            merit=min(radial,self.reach)+progress
            if merit>self.best_partial_score:self.best_partial_score=merit;self.best_partial=state
            if (goal_index,state) not in attempted and self.goal_ready(state):
                if agenda and time.monotonic()>=planning_stop:
                    if not from_cache:
                        serial+=1;heapq.heappush(queue,(cost+1.6*self.heuristic(state),cost,serial,state))
                    new_turn=True;continue
                attempted.add((goal_index,state));edges=self.reconstruct(state,parents)
                self.deadline=common_deadline
                try:
                    try:updated,added,rejected=self.commit(edges)
                    except (ValueError,TimeoutError) as error:updated,added,rejected=None,[],str(error)
                finally:self.deadline=common_deadline
                if updated is not None:accepted=(state,edges,updated,added);break
                self.stats['native_goal_rejections']+=1;self.stats.setdefault('native_goal_rejection_reasons',{})[rejected]=self.stats.setdefault('native_goal_rejection_reasons',{}).get(rejected,0)+1;reason=rejected
                if self.stats['native_goal_rejections']>=12:reason='native goal rejection budget';break
                # A failed nearest witness cannot spend every native retry
                # before a farther contact gets its first scheduling turn.
                if agenda:new_turn=True
            if from_cache:continue
            if cost>extent*7:continue
            for turn in (0,-1,1):
                index=(state[2]+turn)%8;h=DIRS[index]
                needed=max(1,math.ceil((2*self.radius*(math.sqrt(2)-1)+self.width)/(self.step*math.hypot(*h))))
                incoming_needed=max(1,math.ceil((2*self.radius*(math.sqrt(2)-1)+self.width)/(self.step*math.hypot(*DIRS[state[2]]))))
                if turn and state[4]<incoming_needed:continue
                units=needed if turn else 1
                x,y=state[0]+h[0]*units,state[1]+h[1]*units
                if math.hypot(x,y)*self.step>extent*3:continue
                run=min(self.run_cap,units if turn else state[4]+1);new=(x,y,index,state[3],run)
                price=self.step*math.hypot(*h)*units+(1500000 if turn else 0)
                if cost+price>=costs.get(new,math.inf) or not self.edge_clear(state,new,bool(turn)):continue
                costs[new]=cost+price;parents[new]=(state,'track',None);serial+=1
                heapq.heappush(queue,(cost+price+1.6*self.heuristic(new),cost+price,serial,new));self.stats['generated']+=1
            portal=self.portal(state,deadline=min(common_deadline,proof_stop,planning_stop)) if self.allow_vias else None
            if agenda and self.allow_vias and planning_stop<=time.monotonic()<min(common_deadline,proof_stop):
                # A soft turn cutoff leaves this expansion unfinished. Keep
                # its portal opportunity in the shared frontier for the next turn.
                serial+=1;heapq.heappush(queue,(cost+1.6*self.heuristic(state),cost,serial,state))
                new_turn=True;continue
            if portal:
                h=DIRS[state[2]];u=portal['units'];new=(state[0]+h[0]*u,state[1]+h[1]*u,state[2],portal['end_layer'],0)
                price=u*self.step*math.hypot(*h)+len(self.nets)*1000000
                if math.hypot(new[0],new[1])*self.step<=extent*3 and cost+price<costs.get(new,math.inf):
                    costs[new]=cost+price;parents[new]=(state,'portal',portal);serial+=1
                    heapq.heappush(queue,(cost+price+1.6*self.heuristic(new),cost+price,serial,new));self.stats['generated']+=1
        if agenda:
            tried={i for i,_ in attempted};self.stats.update(goal_inventory=getattr(self,'goal_inventory_count',agenda.count),goals_admitted=agenda.count,goals_offered=len(offered),goals_attempted=len(tried),goals_untested=agenda.count-len(tried))
        state,edges,updated,added=accepted if accepted else (None,[],None,[])
        axis=[{'start_nm':self.at(a),'end_nm':self.at(z),'layer':a[3],'end_layer':z[3],'kind':kind} for a,z,kind,_ in edges]
        partial=[]
        if not accepted and self.best_partial:
            best=self.best_partial;previous=parents;partial=[{'start_nm':self.at(a),'end_nm':self.at(z),'layer':a[3],'end_layer':z[3],'kind':kind} for a,z,kind,_ in self.reconstruct(best,previous)]
        return {'accepted':bool(accepted),'reason':None if accepted else reason,'nets':self.nets,'lane_offsets_nm':self.offsets,'source_origin_nm':self.origin,'source_heading':self.h,'source_layer':self.face,'reserved_width_nm':self.reserved_width,'copper_band_width_nm':2*self.radius,'clearance_nm':self.gap,'track_width_nm':self.width,'reach_nm':self.reach,'search_extent_nm':extent,'reach_scope':'each actual lane from its original physical port tip','axis':axis,'failed_partial_axis':partial,'stats':self.stats,'blockers':self.blockers.most_common(8),'ports':updated,'added_ids':added}
