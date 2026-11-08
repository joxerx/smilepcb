"""Pure source-head equivalence, bounded selection, and unary constraints.

Geometry signatures identify alternatives only. Native geometry, full physical
terminal coverage, and immutable frame proofs remain the adapter's obligation.
"""
from collections import defaultdict
from hashlib import sha256
import json
import math
import time


class SourceSearchBudget:
    """A finite allowance shared by trials of the same certified interfaces."""
    def __init__(self,pad_groups,*,max_candidates=96,max_states=12000):
        groups=[tuple(sorted(ids)) for ids in pad_groups]
        if (any(not ids or len(ids)!=len(set(ids)) for ids in groups) or
                len(set().union(*(set(ids) for ids in groups)))!=sum(map(len,groups))):
            raise ValueError('Source budget requires disjoint complete physical interfaces')
        if any(type(n) is not int or n<1 for n in (max_candidates,max_states)):
            raise ValueError('Source budget requires positive finite limits')
        self.max_candidates=max_candidates;self.max_states=max_states
        self.candidates={ids:0 for ids in groups};self.states_spent=0

    def candidate_limit(self,pad_ids,requested):
        key=tuple(sorted(pad_ids))
        if key not in self.candidates:raise ValueError('Source budget interface differs from certified aliases')
        return min(requested,self.max_candidates-self.candidates[key])

    def charge_candidates(self,pad_ids,count):
        if type(count) is not int or count<0 or count>self.candidate_limit(pad_ids,self.max_candidates):
            raise ValueError('Source candidate allowance exceeded')
        self.candidates[tuple(sorted(pad_ids))]+=count

    @property
    def states_left(self):
        return self.max_states-self.states_spent

    def selector(self,select):
        def charged(domains,compatible,deadline,max_states):
            limit=min(max_states,self.states_left)
            if limit<=0:
                return None,{'states':0,'budget_exhausted':True,'budget_reason':'Shared source selection budget exhausted'}
            result,stats=select(domains,compatible,deadline,limit)
            used=stats['states']
            if type(used) is not int or not 0<=used<=limit:raise ValueError('Source selection allowance exceeded')
            self.states_spent+=used
            return result,stats
        return charged

    def receipt(self):
        return {'max_candidates_per_interface':self.max_candidates,'max_states':self.max_states,
                'states_spent':self.states_spent,'states_remaining':self.states_left,
                'candidate_domains':[{'pad_ids':list(ids),'spent':spent,'remaining':self.max_candidates-spent}
                                     for ids,spent in sorted(self.candidates.items())]}


def _point(value):
    if not isinstance(value,(tuple,list)) or len(value)!=2 or any(type(v) is not int for v in value):
        raise ValueError('Expected integer native coordinates')
    return tuple(value)


def geometry_signature(shapes):
    """Ignore UUIDs, segment direction, collinear splitting, and duplicates.

    Net bindings, copper faces, widths and every via's diameter/drill remain
    significant. Equivalence is within a fixed logical binding/frame.
    """
    lines=defaultdict(list);vias=set()
    for shape in shapes.values():
        net=shape['net'];width=shape['width'];start=_point(shape['start']);end=_point(shape['end'])
        if not isinstance(net,str) or not net or type(width) is not int or width<=0:raise ValueError('Invalid copper identity/width')
        if shape['class']=='PCB_TRACK':
            delta=tuple(end[i]-start[i] for i in (0,1));scale=math.gcd(*delta)
            if not scale:raise ValueError('Zero-length bank track')
            d=tuple(v//scale for v in delta)
            if d[0]<0 or d[0]==0 and d[1]<0:d=tuple(-v for v in d)
            key=(net,shape['layer'],width,*d,d[0]*start[1]-d[1]*start[0])
            lines[key].append(tuple(sorted(sum(d[i]*q[i] for i in (0,1)) for q in (start,end))))
        elif shape['class']=='PCB_VIA':
            drill=shape['drill']
            if start!=end or type(drill) is not int or not 0<drill<width or shape['top']==shape['bottom']:
                raise ValueError('Invalid through-via geometry')
            vias.add((net,start,width,drill,min(shape['top'],shape['bottom']),max(shape['top'],shape['bottom']),shape['via_type']))
        else:raise ValueError('Bank operations must be tracks or through-vias')
    canonical=[]
    for key,ranges in sorted(lines.items()):
        merged=[]
        for lo,hi in sorted(ranges):
            if merged and lo<=merged[-1][1]:merged[-1][1]=max(merged[-1][1],hi)
            else:merged.append([lo,hi])
        canonical.append([list(key),merged])
    return sha256(json.dumps({'lines':canonical,'vias':sorted(vias)},sort_keys=True,separators=(',',':')).encode()).hexdigest()



def select_head_set(domains,compatible,deadline,max_states,accept_complete=lambda rows:True):
    """Score and forward-check every remaining physical interface."""
    cache={};states=0;stats={'max_assigned':0,'deepest_groups':[],'last_conflict':None,'backjumps':0,'zero_support_values':0,'rejected_complete_sets':0}
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
        if not masks:
            if accept_complete([domains[i][chosen[i]] for i in sorted(chosen)]):return chosen,set()
            stats['rejected_complete_sets']+=1
            return None,set(chosen)
        if time.monotonic()>=deadline or states>=max_states:
            raise TimeoutError('Joint port search budget exhausted')
        i=min(masks,key=lambda k:(bin(masks[k]).count('1'),k));bits=masks[i]
        conflicts=set(causes[i])
        neighbours=sorted((j for j in masks if j!=i),key=lambda j:(bin(masks[j]).count('1'),j))
        values=[]
        while bits:
            bit=bits & -bits;bits^=bit;a=bit.bit_length()-1
            supports={j:allowed(i,a,j,masks[j]) for j in neighbours}
            blocked=next((j for j in masks if j!=i and not supports[j]),None)
            if blocked is not None:
                stats['zero_support_values']+=1
                stats['last_conflict']=[i,blocked]
                conflicts.update(causes[blocked]);continue
            loss=sum(1-bin(supports[j]).count('1')/bin(masks[j]).count('1') for j in neighbours)
            values.append((loss,a))
        if time.monotonic()>=deadline:raise TimeoutError('Joint port search budget exhausted')
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


def _identity(row):
    values=row.get('pad_ids')
    if (not isinstance(values,(tuple,list)) or not values or
            any(not isinstance(q,str) or not q for q in values) or len(set(values))!=len(values)):
        return None
    return tuple(sorted(values))


def select_constrained_head_set(domains,compatible,deadline,max_states,*,required_heads,signature,accept_complete):
    """Propagate only a provably local complete physical-interface constraint.

    Split aliases, mixed groups and a shared logical net remain subject to the
    existing aggregate leaf predicate. Candidate generation/order/objects and
    compatibility are unchanged. Returned indices address THIS call's domains,
    including when the caller supplies its preferred same-layer sublists.
    """
    original=[list(rows) for rows in domains];filtered=[list(rows) for rows in original]
    indices=[list(range(len(rows))) for rows in original];constraints=[]
    groups=[{_identity(row) for row in rows} for rows in original]
    normalized={}
    for pads,old in required_heads.items():
        key=_identity({'pad_ids':pads})
        if key is None:raise ValueError('Required head must name a complete physical source-pad set')
        if key in normalized and normalized[key]!=old:raise ValueError('Conflicting original head signatures')
        normalized[key]=old
    used=set()
    for key in normalized:
        if used.intersection(key):raise ValueError('Certified physical interfaces overlap')
        used.update(key)
    def decorate(stats,complete=True):
        stats.update(head_constraints=constraints,candidate_domains_before=[len(q) for q in original],
            candidate_domains_after=[len(q) for q in filtered],head_filter_complete=complete)
        return stats
    def no_selection(**extra):
        return None,decorate(dict(states=0,max_assigned=0,deepest_groups=[],last_conflict=None,
            backjumps=0,rejected_complete_sets=0,compatibility_masks=0,**extra),not extra.get('budget_exhausted',False))
    for pads,old in normalized.items():
        if time.monotonic()>=deadline:return no_selection(budget_exhausted=True,budget_reason='Head constraint filtering deadline')
        exact=[i for i,keys in enumerate(groups) if keys=={pads}]
        overlapping=[i for i,keys in enumerate(groups) if any(q is not None and set(q).intersection(pads) for q in keys)]
        record={'pad_ids':list(pads),'group_indices':overlapping,'before':sum(len(original[i]) for i in overlapping),
                'after':None,'status':'deferred_non_exact_group'}
        constraints.append(record)
        if len(exact)!=1 or overlapping!=exact:continue
        i=exact[0];nets={row.get('net') for row in original[i]}
        if len(nets)!=1 or None in nets:continue
        net=next(iter(nets));record.update(net=net,group_index=i,before=len(original[i]))
        if any(row.get('net')==net for j,rows in enumerate(original) if j!=i for row in rows):
            record['status']='deferred_shared_net';continue
        keep=[]
        for k,row in enumerate(original[i]):
            if time.monotonic()>=deadline:return no_selection(budget_exhausted=True,budget_reason='Head constraint filtering deadline')
            if signature(row)!=old:keep.append(k)
        indices[i]=keep;filtered[i]=[original[i][k] for k in keep]
        record.update(status='filtered_exact_interface',after=len(keep),removed=len(original[i])-len(keep))
    empty=[i for i,rows in enumerate(filtered) if not rows]
    if empty:return no_selection(empty_filtered_groups=empty)
    chosen,stats=select_head_set(filtered,compatible,deadline,max_states,accept_complete=accept_complete)
    if chosen is not None:chosen={i:indices[i][k] for i,k in chosen.items()}
    return chosen,decorate(stats)
