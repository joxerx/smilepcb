"""Opt-in complete-scene core lookahead; Native work stays behind callbacks.

No full source preparation, field routing or generation is called per pose.
A selected scene is resumed from its saved PCB/model, never reconstructed.
"""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time


def _digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()
def _check(deadline):
    if time.monotonic()>=deadline:raise TimeoutError('Core lookahead caller deadline exhausted')


def validate_raw_model(cfg):
    refs=[q['ref'] for q in cfg.get('parts',())]
    if refs.count('U_MCU')!=1 or any(ref.startswith('C_CORE') for ref in refs):
        raise ValueError('Core lookahead requires the original raw assembly model, not an imported C_CORE model')


def assembly_controls(request):
    exact=request.get('_core_exact_pose');only=request.get('_core_assembly_only',False);fixed=request.get('_core_fixed_pose')
    if '_core_envelope_radius_nm' in request and (type(request['_core_envelope_radius_nm']) is not int or request['_core_envelope_radius_nm']<0):raise ValueError('Core envelope radius requires nonnegative integer nm')
    if type(only) is not bool:raise ValueError('Internal assembly-only control must be boolean')
    if exact is not None:
        point=exact.get('origin_nm',())
        if len(point)!=2 or any(type(x) is not int for x in point) or type(exact.get('rotation_deg')) is not int or exact['rotation_deg'] not in (0,90,180,270):
            raise ValueError('Exact core pose requires integer nm and a cardinal rotation')
    if fixed is not None:
        if (len(fixed.get('origin_nm',()))!=2 or any(type(x) is not int for x in fixed['origin_nm']) or
                type(fixed.get('rotation_deg')) not in (int,float) or not math.isfinite(fixed['rotation_deg']) or fixed['rotation_deg']%90 or fixed.get('side') not in ('F.Cu','B.Cu')):
            raise ValueError('Fixed connector pose requires a physical cardinal pose')
    return dict(exact_pose=deepcopy(exact),assembly_only=only,fixed_pose=deepcopy(fixed))


def _move_packed_core(objects,old,new):
    """Rigid cardinal transform only; source topology and identities stay intact."""
    from smilepcb.native_worker import p
    origin=p.VECTOR2I(0,0)
    for obj in objects:
        obj.Move(p.VECTOR2I(-old['origin_nm'][0],-old['origin_nm'][1]))
        obj.Rotate(origin,p.EDA_ANGLE(new['rotation_deg']-old['rotation_deg'],p.DEGREES_T))
        obj.Move(p.VECTOR2I(*new['origin_nm']))


def balance_packed_core(board,cfg,core_objects,baseline_pose,*,deadline,legal,excluded=(),allowed_rotations=(0,90,180,270)):
    """Consume the existing four-side search at the actual pre-aux boundary.

    Estimates rank proposals. The caller's unchanged Native geometry checker
    admits each pose; rejected trials restore the exact entry snapshot.
    """
    from smilepcb.native_worker import snapshot
    from .core_pose_geometry import balanced_pose_search,_balanced_metrics
    _check(deadline);entry=snapshot(board)
    if not allowed_rotations or any(type(a) is not int or a not in (0,90,180,270) for a in allowed_rotations):raise ValueError('Balanced placement requires cardinal rotations')
    baseline={k:deepcopy(baseline_pose[k]) for k in ('origin_nm','rotation_deg')}
    core_ids={q.m_Uuid.AsString() for q in core_objects}
    oracle=_NativePoseOracle(board,cfg,core_ids,baseline)
    centres=[q['center_mm'] for q in cfg['layout']['keys']]
    pitch=round(min((math.dist(a,b)*1e6 for i,a in enumerate(centres) for b in centres[i+1:] if a!=b),default=10_000_000))
    rules=board.GetDesignSettings()
    process_pitch=max(rules.m_TrackMinWidth+rules.m_MinClearance,(rules.m_ViasMinSize+rules.m_TrackMinWidth+1)//2+rules.m_MinClearance)
    def measure(pose,*,deadline=None):
        if pose['rotation_deg'] not in allowed_rotations:return {'legal':False,'status':'rotation_constraint'}
        return oracle(pose,deadline=deadline)
    admission_reserve=min(5,max(.001,(deadline-time.monotonic())*.2))
    search=balanced_pose_search(oracle.bounds,baseline,coarse_step_nm=max(1,pitch//4),min_step_nm=max(1,process_pitch//4),oracle=measure,deadline=deadline-admission_reserve,max_evaluations=8192,limit=6)
    report={k:deepcopy(v) for k,v in search.items() if k!='samples'}
    report.update(strategy='balanced-four-side-v1',extraction=oracle.extraction,sample_count=len(search['samples']),sample_sha256=_digest(search['samples']),native_rejected=0,native_legal=False)
    report['baseline_metrics']=deepcopy(search['poses'][0]['diagnostics'])
    excluded={tuple(q) for q in excluded}
    from .core_pose_geometry import expanded_pose_box,boxes_overlap
    failed_regions=[];region_cache={}
    if excluded:
        core_bounds=[min(q['box'][0] for q in oracle.core),min(q['box'][1] for q in oracle.core),
            max(q['box'][2] for q in oracle.core),max(q['box'][3] for q in oracle.core)]
        failed_regions=[expanded_pose_box(core_bounds,baseline,dict(origin_nm=list(q[:2]),rotation_deg=q[2]),
            margin_nm=oracle.depth) for q in sorted(excluded)]
    def same_region(pose):
        if not failed_regions:return False
        key=(*pose['origin_nm'],pose['rotation_deg'])
        if key not in region_cache:
            region=expanded_pose_box(core_bounds,baseline,pose,margin_nm=oracle.depth)
            region_cache[key]=any(boxes_overlap(region,old) for old in failed_regions)
        return region_cache[key]
    report['failed_neighborhoods_nm']=failed_regions
    # The coarse search already measured every cardinal angle. Its small global
    # shortlist can contain one angle only; preserve one measured leader per
    # angle so retries explore genuinely different bank exits without new search.
    candidates=list(search['poses'])
    for angle in allowed_rotations:
        rows=[q for q in search['samples'] if q.get('legal') and q['pose']['rotation_deg']==angle]
        if rows:
            row=min(rows,key=lambda q:(_balanced_metrics(q),sum((q['pose']['origin_nm'][i]-baseline['origin_nm'][i])**2 for i in (0,1)),q['pose']['origin_nm']))
            candidates.append(dict(row['pose'],diagnostics={k:v for k,v in row.items() if k not in ('pose','phase','step_nm')}))
            if failed_regions:
                spatial=min(rows,key=lambda q:(same_region(q['pose']),_balanced_metrics(q),
                    sum((q['pose']['origin_nm'][i]-baseline['origin_nm'][i])**2 for i in (0,1)),q['pose']['origin_nm']))
                candidates.append(dict(spatial['pose'],diagnostics={k:v for k,v in spatial.items() if k not in ('pose','phase','step_nm')}))
    unique={(*q['origin_nm'],q['rotation_deg']):q for q in candidates}
    ranked=[q for key,q in unique.items() if q.get('diagnostics',{}).get('legal') and key not in excluded and q['rotation_deg'] in allowed_rotations]
    tried_angles={q[2] for q in excluded}
    def ordinary(q):
        return (q['rotation_deg'] in tried_angles,_balanced_metrics(q['diagnostics']),
            sum((q['origin_nm'][i]-baseline['origin_nm'][i])**2 for i in (0,1)),q['rotation_deg'],q['origin_nm'])
    ordinary_leader=min(ranked,key=ordinary) if ranked else None
    ranked.sort(key=lambda q:(same_region(q),*ordinary(q)))
    # New regions are a preference, never a prohibition. Retain one ordinary
    # fallback within the existing six Native admissions if new regions fail.
    if ordinary_leader is not None and ordinary_leader not in ranked[:6]:
        ranked=ranked[:5]+[ordinary_leader]
    else:ranked=ranked[:6]
    report['candidate_overlaps_failed_neighborhood']=[same_region(q) for q in ranked]
    report.update(tried_rotations=sorted(tried_angles),candidate_rotations=[q['rotation_deg'] for q in ranked])
    for candidate in ranked:
        if time.monotonic()>=deadline:
            report.update(failure='Balanced placement admission budget exhausted',budget_exhausted=True)
            return None,report
        pose={k:deepcopy(candidate[k]) for k in ('origin_nm','rotation_deg')}
        _move_packed_core(core_objects,baseline,pose)
        try:
            if legal(core_objects) and time.monotonic()<deadline:
                report.update(native_legal=True,selected_metrics=deepcopy(candidate['diagnostics']))
                return pose,report
        except BaseException:
            _move_packed_core(core_objects,pose,baseline)
            if snapshot(board)!=entry:raise ValueError('Balanced placement rejection changed source geometry')
            raise
        _move_packed_core(core_objects,pose,baseline)
        if snapshot(board)!=entry:raise ValueError('Balanced placement rejection changed source geometry')
        report['native_rejected']+=1
    report['failure']='No Native-admitted balanced candidate in the bounded search'
    return None,report


def _select(*args,**kwargs):
    from .core_pose_selection import select_core_pose
    return select_core_pose(*args,**kwargs)


def lookahead_core_pose(board,cfg,request,*,core_objects,baseline_pose,fixed_pose,deadline):
    runtime=_runtime(board,cfg,request,core_objects=core_objects,baseline_pose=baseline_pose,fixed_pose=fixed_pose,deadline=deadline)
    try:
        result=_select(runtime.poses(),assemble=runtime.assemble,evaluate=runtime.evaluate,dispose=runtime.dispose,
            deadline=deadline,max_candidates=min(3,request.get('lookahead_native_candidates',3)))
        if result['selected_scene'] is None:
            return dict(accepted=False,result={k:v for k,v in result.items() if k!='selected_scene'})
        return runtime.resume(result['selected_scene'],result)
    finally:runtime.verify_baseline()


def _rules(board):
    settings=board.GetDesignSettings();values={}
    for key in dir(settings):
        if not key.startswith('m_'):continue
        try:value=getattr(settings,key)
        except Exception:continue
        if value is None or isinstance(value,(int,float,str,bool)):values[key]=value
    return dict(native_scalar_attributes=values,default_netclass_clearance_nm=settings.m_NetSettings.GetDefaultNetclass().GetClearance())


def _mandatory(cfg):
    part=next(q for q in cfg['parts'] if q['ref']=='U_MCU')
    return sorted('U_MCU:'+number for number,net in part['nets'].items() if number not in part.get('no_connect',()) and net and not net.startswith(('NC_','unconnected-')))


def _manifest(board,cfg,trusted,deadline):
    from smilepcb.native_worker import p
    from smilepcb.keyboard_routing.native_graph import copper_graph
    fps={f.GetReference():f for f in board.GetFootprints()};pads={}
    for pad in fps['U_MCU'].Pads():
        key='U_MCU:'+pad.GetNumber()
        if key in trusted:
            if key in pads:raise ValueError('Mandatory MCU pad canonical identity aliases physical pads')
            pads[key]=pad
    if set(pads)!=set(trusted):raise ValueError('Assembly omitted a trusted mandatory MCU pad')
    graph=copper_graph(board,{d.GetNetname() for d in pads.values()},deadline,include_zones=False)
    ids={q['item'].m_Uuid.AsString():i for i,q in enumerate(graph['nodes'])};roots={}
    for key,pad in pads.items():roots.setdefault(graph['find'](ids[pad.m_Uuid.AsString()]),[]).append(key)
    return dict(mandatory_pad_keys=list(trusted),pads=[dict(pad_key=k,pad_id=pads[k].m_Uuid.AsString()) for k in sorted(pads)],
        roots=[dict(source_key=sorted(keys),pad_ids=sorted(pads[k].m_Uuid.AsString() for k in keys)) for keys in sorted(roots.values())])


def _mechanics(board,cfg):
    from smilepcb.native_worker import snapshot
    parts={q['ref']:q for q in cfg['parts']};result={}
    for fp in snapshot(board)['footprints'].values():
        ref=fp['ref']
        if parts.get(ref,{}).get('region')=='controller' and ref!='J_UDB':continue
        item=deepcopy(fp);item.pop('path',None)
        for pad in item['pads']:
            pad.pop('uuid',None);pad.pop('net',None)
        # Snapshot order uses UUID to break duplicate pad-number ties. Trial
        # assembly regenerates UUIDs; compare the complete physical pad records.
        item['pads'].sort(key=lambda pad:json.dumps(pad,sort_keys=True,separators=(',',':')))
        if ref.startswith('LED'):
            # The existing final-LED gate admits only supported 0/180 mounts.
            item.pop('rotation',None);item.pop('pads',None)
        result[ref]=item
    return result


def _rotate(point,angle):
    x,y=point
    return ((x,y),(-y,x),(-x,-y),(y,-x))[(angle%360)//90]


def _bounded_pose_events(baseline,events,*,pitch_nm,limit=48):
    """Share the finite estimate pool across cardinal poses at real events."""
    origin=tuple(baseline['origin_nm']);angle=baseline['rotation_deg']
    rotations=(angle,*[v for v in (0,90,180,270) if v!=angle])
    spacing=max(1,math.ceil(pitch_nm/8));points=[origin];result=[]
    for point in sorted(set(map(tuple,events)),key=lambda p:(abs(p[0]-origin[0])+abs(p[1]-origin[1]),p)):
        if all(max(abs(point[i]-other[i]) for i in (0,1))>=spacing for other in points):points.append(point)
        if len(points)*len(rotations)>=limit:break
    for point in points:
        for rotation in rotations:
            if len(result)>=limit:return result
            key=(point,rotation)
            result.append(deepcopy(baseline) if key==(origin,angle) else dict(origin_nm=list(point),rotation_deg=rotation,pose_key='pose-'+_digest(key)[:12]))
    return result


def _transform_features(features,old_origin,old_angle,new_origin,new_angle):
    result=[]
    for feature in features:
        a,b,c,d=feature['box'];corners=[]
        for point in ((a,b),(a,d),(c,b),(c,d)):
            local=_rotate((point[0]-old_origin[0],point[1]-old_origin[1]),-old_angle)
            turned=_rotate(local,new_angle);corners.append((turned[0]+new_origin[0],turned[1]+new_origin[1]))
        result.append(dict(feature,box=[min(p[0] for p in corners),min(p[1] for p in corners),max(p[0] for p in corners),max(p[1] for p in corners)]))
    return result



def _feature_margin(core,obstacle):
    """Match placement's body guards separately from conductor rules."""
    a,b=core.get('kind'),obstacle.get('kind')
    if core.get('layer')!=obstacle.get('layer') and a not in ('hole','edge') and b not in ('hole','edge'):return None
    if 'body' in (a,b):
        return 0 if a in ('body','hole','edge') and b in ('body','hole','edge') else None
    return max(core.get('gap_nm',0),obstacle.get('gap_nm',0))


def _space_metrics(core,fixed,bounds,*,access_depth_nm,contains=None):
    """Conservative connected empty-slab bounds around the complete rigid core.

    Bounding boxes may reject feasible geometry; these estimates cannot admit
    Native routing. Remote disjoint free intervals never increase headroom.
    """
    if not core:raise ValueError('Positive owned rigid-core geometry required')
    box=[min(q['box'][0] for q in core),min(q['box'][1] for q in core),max(q['box'][2] for q in core),max(q['box'][3] for q in core)]
    left,top,right,bottom=box
    spaces=dict(left=max(0,left-bounds[0]),right=max(0,bounds[2]-right),top=max(0,top-bounds[1]),bottom=max(0,bounds[3]-bottom))
    witnesses={side:dict(kind='board-material-bound',box=list(bounds)) for side in spaces};legal=True;hits=[];internal=[]
    groups={}
    for feature in core:groups.setdefault((feature.get('layer'),feature.get('kind')),[]).append(feature)
    grouped=[]
    for (layer,kind),features in groups.items():
        selected=[q['box'] for q in features]
        grouped.append(dict(layer=layer,kind=kind,gap_nm=max(q.get('gap_nm',0) for q in features),box=[min(b[0] for b in selected),min(b[1] for b in selected),max(b[2] for b in selected),max(b[3] for b in selected)]))
    if left<bounds[0] or top<bounds[1] or right>bounds[2] or bottom>bounds[3] or contains is not None and not all(contains(x,y) for x,y in ((left,top),(left,bottom),(right,top),(right,bottom),((left+right)//2,(top+bottom)//2))):legal=False;hits.append(dict(kind='board-material',box=box))
    core_gap=max(q.get('gap_nm',0) for q in core)
    for obstacle in fixed:
        internal_recorded=False
        a,b,c,d=obstacle['box'];broad_gap=max(obstacle.get('gap_nm',0),core_gap)
        for feature in core if a-broad_gap<=right and c+broad_gap>=left and b-broad_gap<=bottom and d+broad_gap>=top else ():
            gap=_feature_margin(feature,obstacle)
            if gap is None:continue
            u,v,w,z=feature['box']
            if a-gap<=w and c+gap>=u and b-gap<=z and d+gap>=v:
                legal=False;hits.append(dict(obstacle,applied_margin_nm=gap,core_kind=feature.get('kind')));break
        for feature in grouped:
            gap=_feature_margin(feature,obstacle)
            if gap is None:continue
            u,v,w,z=feature['box'];aa,bb,cc,dd=a-gap,b-gap,c+gap,d+gap;found={}
            if bb<=z and dd>=v:
                if cc<=u:found['left']=u-cc
                elif aa<=u<=cc:found['left']=0
                if aa>=w:found['right']=aa-w
                elif aa<=w<=cc:found['right']=0
            if aa<=w and cc>=u:
                if dd<=v:found['top']=v-dd
                elif bb<=v<=dd:found['top']=0
                if bb>=z:found['bottom']=bb-z
                elif bb<=z<=dd:found['bottom']=0
            if u<aa and cc<w and v<bb and dd<z and not internal_recorded:
                internal.append(dict(obstacle,applied_margin_nm=gap,core_kind=feature.get('kind'),envelope_nm=feature['box']));internal_recorded=True
            for side,distance in found.items():
                if distance<spaces[side]:
                    spaces[side]=max(0,distance);witnesses[side]=dict(obstacle,applied_margin_nm=gap,core_kind=feature.get('kind'))
    return dict(legal=legal,side_space_nm=spaces,side_witnesses=witnesses,whole_core_bounds_nm=box,
        bank_headroom_nm=min(spaces.values())-access_depth_nm,adverse_departures=0,collision_witnesses=hits[:8],internal_obstruction_witnesses=internal[:8],
        estimate_kind='conservative connected whole-rigid-block side-slab bounds; internal bank access requires Native proof',access_depth_nm=access_depth_nm)


def _bank_access_halos(pads,*,depth_nm,margin_nm):
    """One bounded straight access strip per actual cardinal source bank."""
    banks={}
    for pad in pads:banks.setdefault(tuple(pad['heading']),[]).append(pad['box'])
    result=[]
    for heading,boxes in sorted(banks.items()):
        left=min(b[0] for b in boxes);top=min(b[1] for b in boxes);right=max(b[2] for b in boxes);bottom=max(b[3] for b in boxes)
        if heading==(1,0):box=[right,top-margin_nm,right+depth_nm,bottom+margin_nm]
        elif heading==(-1,0):box=[left-depth_nm,top-margin_nm,left,bottom+margin_nm]
        elif heading==(0,1):box=[left-margin_nm,bottom,right+margin_nm,bottom+depth_nm]
        elif heading==(0,-1):box=[left-margin_nm,top-depth_nm,right+margin_nm,top]
        else:raise ValueError('Cardinal source-bank heading required')
        result.append(dict(heading=list(heading),box=box,depth_nm=depth_nm,margin_nm=margin_nm))
    return result


def _available_halo_radius(pose):
    spaces=pose.get('diagnostics',{}).get('side_space_nm')
    if spaces is None:return None
    if not isinstance(spaces,dict) or set(spaces)!={'left','right','top','bottom'} or any(type(value) is not int or value<0 for value in spaces.values()):
        raise ValueError('Available side space requires four nonnegative integer nm dimensions')
    return min(spaces.values())


def _core_envelope_halos(features,*,depth_nm):
    """Bounded whole-positive-core envelope, on every actually occupied face."""
    if not features or type(depth_nm) is not int or depth_nm<0:raise ValueError('Positive core geometry and nonnegative integer access depth required')
    boxes=[q['box'] for q in features]
    bounds=[min(b[0] for b in boxes),min(b[1] for b in boxes),max(b[2] for b in boxes),max(b[3] for b in boxes)]
    expanded=[bounds[0]-depth_nm,bounds[1]-depth_nm,bounds[2]+depth_nm,bounds[3]+depth_nm]
    return [dict(layer=layer,box=list(expanded),core_bounds_nm=list(bounds),depth_nm=depth_nm) for layer in sorted({q['layer'] for q in features})]


def _courtyard_boxes(shape):
    # Independent actual islands must not become one solid bounding obstacle.
    # Each island's nonrectangular interior remains conservatively occupied.
    result=[]
    for i in range(shape.OutlineCount()):
        b=shape.COutline(i).BBox();result.append([b.GetLeft(),b.GetTop(),b.GetRight(),b.GetBottom()])
    return result


class _NativePoseOracle:
    """Extract once; whole-board cells use cardinal box arithmetic only."""
    def __init__(self,board,cfg,core_ids,baseline,*,ignored_tracks=(),assembled=False):
        from smilepcb.native_worker import p
        from smilepcb.copper_obstacles import own_clearance
        from smilepcb.keyboard_routing.generation import current_generation_profile,uses_exchange_geometry
        from smilepcb.keyboard_exchange import npth_exchange_clearance_nm
        generation=current_generation_profile()
        self.p=p;self.baseline=baseline;self.core=[];self.fixed=[];self.inventory=[]
        started=time.monotonic();rules=board.GetDesignSettings();gap=max(rules.m_MinClearance,rules.m_NetSettings.GetDefaultNetclass().GetClearance(),generation.foreign_copper_clearance_nm)
        hole_gap=max(250000,rules.m_HoleClearance)+1000;edge_gap=max(rules.m_CopperEdgeClearance,generation.edge_clearance_nm)+1000
        via=max(rules.m_ViasMinSize,rules.m_MinThroughDrill+2*rules.m_ViasMinAnnularWidth)
        self.depth=via+2*gap+rules.m_TrackMinWidth
        parts={q['ref']:q for q in cfg['parts']};ignored=set(ignored_tracks)
        def add(target,shape,layer,kind,owner,ref='',pad='',padding=0):
            b=shape.BBox();target.append(dict(box=[b.GetLeft(),b.GetTop(),b.GetRight(),b.GetBottom()],layer=layer,kind=kind,owner=owner,ref=ref,pad=pad,gap_nm=int(padding)))
        for f in board.GetFootprints():
            uid=f.m_Uuid.AsString();ref=f.GetReference();owned=uid in core_ids
            if not owned and not assembled and parts.get(ref,{}).get('region')=='controller' and ref!='J_UDB':continue
            target=self.core if owned else self.fixed
            for layer in (p.F_Cu,p.B_Cu):
                court=f.GetCourtyard(layer)
                for i,box in enumerate(_courtyard_boxes(court)):
                    target.append(dict(box=box,layer=layer,kind='body',owner=uid,ref=ref,pad='',gap_nm=0,courtyard_island=i,interior_holes= court.HoleCount(i)))
            for graphic in [*f.GraphicalItems(),*f.GetFields()]:
                if graphic.GetLayer() in (p.F_Cu,p.B_Cu):add(target,graphic.GetEffectiveShape(graphic.GetLayer()),graphic.GetLayer(),'copper-artwork',uid,ref,padding=gap+1000)
            for pad in f.Pads():
                drill=pad.GetDrillSize();pad_hole_gap=hole_gap
                if uses_exchange_geometry(generation) and pad.GetAttribute()==p.PAD_ATTRIB_NPTH and drill.x==drill.y and drill.x:pad_hole_gap=max(pad_hole_gap,npth_exchange_clearance_nm(hole_gap-1000,drill.x))
                for layer in (p.F_Cu,p.B_Cu):
                    if pad.GetDrillSize().x:add(target,pad.GetEffectiveHoleShape(),layer,'hole',uid,ref,pad.GetNumber(),pad_hole_gap)
                    if pad.IsOnLayer(layer):add(target,pad.GetEffectiveShape(layer),layer,'pad',uid,ref,pad.GetNumber(),own_clearance(pad,layer,gap))
        for track in board.GetTracks():
            uid=track.m_Uuid.AsString()
            if uid in ignored:continue
            target=self.core if uid in core_ids else self.fixed
            for layer in (p.F_Cu,p.B_Cu):
                if track.IsOnLayer(layer):add(target,track.GetEffectiveShape(layer),layer,'copper',uid,padding=own_clearance(track,layer,gap))
                if isinstance(track,p.PCB_VIA):add(target,track.GetEffectiveHoleShape(),layer,'hole',uid,padding=hole_gap)
        for graphic in board.GetDrawings():
            if graphic.GetLayer() in (p.F_Cu,p.B_Cu):add(self.fixed,graphic.GetEffectiveShape(graphic.GetLayer()),graphic.GetLayer(),'copper-artwork',graphic.m_Uuid.AsString(),padding=gap+1000)
        edges=[]
        for item in [*board.GetDrawings(),*(g for f in board.GetFootprints() for g in f.GraphicalItems())]:
            if not isinstance(item,p.PCB_SHAPE) or item.GetLayer()!=p.Edge_Cuts:continue
            copy=item.Duplicate();copy.SetWidth(0);shape=copy.GetEffectiveShape();b=shape.BBox();edges.append([b.GetLeft(),b.GetTop(),b.GetRight(),b.GetBottom()])
            for layer in (p.F_Cu,p.B_Cu):add(self.fixed,shape,layer,'edge',item.m_Uuid.AsString(),padding=edge_gap)
        if not edges:raise ValueError('Actual board material edges required')
        self.bounds=[min(b[0] for b in edges),min(b[1] for b in edges),max(b[2] for b in edges),max(b[3] for b in edges)]
        self.rows={};self.columns={};self.cell_nm=10000000
        core_gap=max(q.get('gap_nm',0) for q in self.core)
        for i,feature in enumerate(self.fixed):
            a,b,c,d=feature['box'];g=max(feature.get('gap_nm',0),core_gap)
            for row in range((b-g)//self.cell_nm,(d+g)//self.cell_nm+1):self.rows.setdefault(row,set()).add(i)
            for col in range((a-g)//self.cell_nm,(c+g)//self.cell_nm+1):self.columns.setdefault(col,set()).add(i)
        self.extraction=dict(core_features=len(self.core),fixed_features=len(self.fixed),row_buckets=len(self.rows),column_buckets=len(self.columns),elapsed_s=time.monotonic()-started,foreign_gap_nm=gap,hole_gap_nm=hole_gap,edge_gap_nm=edge_gap,core_required_gap_nm=core_gap)
        self.material=p.SHAPE_POLY_SET()
        if not board.GetBoardPolygonOutlines(self.material,False):raise ValueError('Invalid actual board material')

    def __call__(self,pose,*,deadline=None):
        if deadline is not None:_check(deadline)
        features=_transform_features(self.core,self.baseline['origin_nm'],self.baseline['rotation_deg'],pose['origin_nm'],pose['rotation_deg'])
        a=min(q['box'][0] for q in features);b=min(q['box'][1] for q in features);c=max(q['box'][2] for q in features);d=max(q['box'][3] for q in features);indices=set()
        for row in range(b//self.cell_nm,d//self.cell_nm+1):indices.update(self.rows.get(row,()))
        for col in range(a//self.cell_nm,c//self.cell_nm+1):indices.update(self.columns.get(col,()))
        result=_space_metrics(features,[self.fixed[i] for i in indices],self.bounds,access_depth_nm=self.depth,contains=lambda x,y:self.material.Contains(self.p.VECTOR2I(x,y)))
        result['queried_fixed_features']=len(indices);return result


class _Runtime:
    def __init__(self,board,cfg,request,*,core_objects,baseline_pose,fixed_pose,deadline):
        from smilepcb.native_worker import p,snapshot
        self.p=p;self.board=board;self.cfg=cfg;self.request=deepcopy(request);self.deadline=deadline
        self.baseline_pose=deepcopy(baseline_pose);self.fixed_pose=deepcopy(fixed_pose);self.entry=snapshot(board);self.model=deepcopy(cfg);self.context=_rules(board)
        self.raw=Path(request.get('lookahead_raw_project',request['project'])).resolve();self.raw_cfg=json.loads((self.raw/'model-before-placement.json').read_text());validate_raw_model(self.raw_cfg)
        self.trusted=_mandatory(self.raw_cfg);self.mechanics=_mechanics(board,cfg);self.core_ids={q.m_Uuid.AsString() for q in core_objects}
        self.directory=Path(tempfile.mkdtemp(prefix='.core-lookahead-',dir=Path(request['project']).resolve()))
        self.manager=p.GetSettingsManager();self.owners=[self.manager];self._poses=None

    def poses(self):
        from .core_pose_geometry import balanced_pose_search
        from smilepcb.keyboard_routing.generated_provenance import inventory
        _check(self.deadline);ignored={};ledger_path=self.request.get('lookahead_generated_ledger')
        if ledger_path:
            ledger=json.loads(Path(ledger_path).read_text());actual=inventory(self.board.GetFileName())
            for track in self.board.GetTracks():
                uid=track.m_Uuid.AsString();record=ledger.get('generated',{}).get(uid)
                if uid not in self.core_ids and record and record.get('binding')==actual.get(uid) and record.get('creator'):
                    ignored[uid]=dict(record,reason='positively generated copper absent from exact raw assembly recipe')
        self.geometry_oracle=_NativePoseOracle(self.board,self.cfg,self.core_ids,self.baseline_pose,ignored_tracks=ignored)
        centres=[q['center_mm'] for q in self.cfg['layout']['keys']]
        distances=[math.hypot(a[0]-b[0],a[1]-b[1])*1000000 for i,a in enumerate(centres) for b in centres[i+1:] if a!=b]
        pitch=round(min(distances,default=10000000));rules=self.board.GetDesignSettings()
        process_pitch=max(rules.m_TrackMinWidth+rules.m_MinClearance,(rules.m_ViasMinSize+rules.m_TrackMinWidth+1)//2+rules.m_MinClearance)
        self.geometry_search=balanced_pose_search(self.geometry_oracle.bounds,self.baseline_pose,coarse_step_nm=max(1,pitch//4),min_step_nm=max(1,process_pitch//4),oracle=self.geometry_oracle,deadline=self.deadline,max_evaluations=8192,limit=6)
        self.geometry_search['extraction']=dict(self.geometry_oracle.extraction,ignored_generated_tracks=ignored,fixed_track_ids=sorted(q.m_Uuid.AsString() for q in self.board.GetTracks() if q.m_Uuid.AsString() not in self.core_ids and q.m_Uuid.AsString() not in ignored))
        self._poses=self.geometry_search['poses'];return self._poses

    def _load(self,directory,stem='assembly'):
        from smilepcb.native_worker import snapshot
        from smilepcb.keyboard_process import apply_native_rules
        pro=directory/'keyboard.kicad_pro'
        if not self.manager.LoadProject(str(pro)):raise ValueError('Cannot load candidate Native project')
        project=self.manager.GetProject(str(pro));board=self.p.LoadBoard(str(directory/(stem+'.kicad_pcb')));board.SetProject(project)
        owners=[project,board.GetDesignSettings()];apply_native_rules(board,self.request['process_profile'])
        if _rules(board)!=self.context:raise ValueError('Candidate Native project/process context changed')
        return board,owners

    def assemble(self,pose,*,deadline):
        from smilepcb.native_worker import snapshot
        _check(deadline);directory=Path(tempfile.mkdtemp(prefix='trial-',dir=self.directory))
        for name in ('KeyboardParts.pretty','PublicController.pretty'):
            if (self.raw/name).exists():shutil.copytree(self.raw/name,directory/name)
        for name in ('model-before-placement.json','fp-lib-table'):
            if not (self.raw/name).exists():raise ValueError('Missing pinned raw assembly input '+name)
            shutil.copyfile(self.raw/name,directory/name)
        reference=Path(self.request['lookahead_reference_project']).resolve()
        shutil.copyfile(reference,directory/'keyboard.kicad_pro')
        request=deepcopy(self.request);request.update(project=str(directory),budget_s=max(.001,deadline-time.monotonic()),
            core_pose_lookahead=False,_core_exact_pose={k:pose[k] for k in ('origin_nm','rotation_deg')},_core_assembly_only=True,
            _core_fixed_pose=self.fixed_pose,_core_native_rules=self.context,_core_bank_halo=True,prepare_ordered_controller=False,require_controller_access=False,optimize_matrix_gpio=False)
        radius=_available_halo_radius(pose)
        request.pop('_core_envelope_radius_nm',None)
        if radius is not None:request['_core_envelope_radius_nm']=radius
        command=[sys.executable,'-X','faulthandler','-B',str(Path(__file__).with_name('place.py'))]
        (directory/'request.json').write_text(json.dumps(request,indent=2))
        run=subprocess.run(command,input=json.dumps(request),text=True,capture_output=True,timeout=max(.001,deadline-time.monotonic()))
        (directory/'assembly.log').write_text(run.stdout+run.stderr)
        (directory/'worker-exit.json').write_text(json.dumps(dict(command=command,returncode=run.returncode,stdout=run.stdout,stderr=run.stderr),indent=2))
        if run.returncode:raise ValueError('Isolated exact-pose assembly exited '+str(run.returncode)+'; command='+repr(command)+'; '+run.stderr[-1000:])
        result=json.JSONDecoder().raw_decode(run.stdout)[0]
        if not (directory/'assembly.kicad_pcb').exists():raise ValueError('Exact-pose worker did not save its actual geometry')
        board,owners=self._load(directory);cfg=json.loads((directory/'assembly.model.json').read_text())
        manifest=_manifest(board,cfg,self.trusted,deadline)
        scene=dict(pose=deepcopy(pose),expected_source_keys=[q['source_key'] for q in manifest['roots']],native_source_manifest=manifest,
            resource=dict(directory=directory,board=board,cfg=cfg,owners=owners,assembly=result,entry=snapshot(board)))
        actual_core=set(result['core_object_ids'])
        measured=_NativePoseOracle(board,cfg,actual_core,pose,assembled=True)(pose,deadline=deadline)
        scene['resource']['four_side_metrics']=measured
        (directory/'assembled-space.json').write_text(json.dumps(measured,indent=2))
        return scene

    def evaluate(self,scene,*,deadline):
        from smilepcb.native_worker import snapshot
        from smilepcb.keyboard_routing.controller_local import prepare_controller_local
        from smilepcb.keyboard_routing.power import seed_controller_returns
        from smilepcb.keyboard_routing.controller_plan import _local_subsets,verify_controller_local_subsets
        from smilepcb.keyboard_routing.interface_contract import mcu_signal_nets
        from .usb_placement import select_usb_placement,verify_prepared_usb
        r=scene['resource'];board=r['board'];cfg=r['cfg'];refs={q['ref'] for q in cfg['parts'] if q['region']=='controller' and not q['ref'].startswith('#')}
        assembly_checks=r['assembly'].get('assembly_checks',{})
        checks=dict(assembly=bool(assembly_checks) and all(value is True for value in assembly_checks.values()),mechanics=_mechanics(board,cfg)==self.mechanics,local_pads=False,returns=False,usb=False,context=_rules(board)==self.context,probe_cleanup=False)
        proof=dict(schema='native-core-local-v1',mandatory_source_keys=deepcopy(scene['expected_source_keys']),proved_source_keys=[],checks=checks,
            worst_bank_deficit=scene['pose'].get('diagnostics',{}).get('worst_bank_deficit',0),worst_bank_slack_nm=scene['pose'].get('diagnostics',{}).get('worst_bank_slack_nm',0),via_count=0,length_nm=0)
        measured=r.get('four_side_metrics')
        if measured is not None:
            proof.update({k:deepcopy(measured[k]) for k in ('side_space_nm','bank_headroom_nm','adverse_departures')});proof['space_witnesses']=deepcopy(measured);checks['four_side_metrics']=True
        if not all(checks[k] for k in ('assembly','mechanics','context')):return proof
        from smilepcb.usb_policy import resolve_usb_max_length_nm
        usb_max_length_nm=resolve_usb_max_length_nm(self.request.get('usb_max_length_nm',...))
        before=snapshot(board);local=prepare_controller_local(board,cfg,deadline=deadline)
        from smilepcb.keyboard_phases import ordered_nets
        phases=dict(ordered_nets(cfg));matrix=set(phases['rows']+phases['columns'])
        subsets=_local_subsets(board,cfg,mcu_signal_nets(cfg)-matrix);verify_controller_local_subsets(board,cfg,dict(pad_subsets=subsets),deadline=deadline)
        checks['local_pads']=True;r['local']=dict(local,actual_local_subsets=subsets)
        returns=seed_controller_returns(board,controller_refs=refs,plane_nets=cfg['plane_nets'],deadline=deadline,via_to_smd_clearance_nm=200000)
        checks['returns']=returns.get('accepted') is True;r['returns']=returns
        if not checks['returns']:return proof
        connector=next(f for f in board.GetFootprints() if f.GetReference()=='J_UDB')
        usb=select_usb_placement(board,connector,cfg,preferred_nm=self.fixed_pose['origin_nm'],legal=lambda fp:True,deadline=deadline,fixed_pose=self.fixed_pose,max_length_nm=usb_max_length_nm)
        checks['usb']=usb.get('accepted') is True;r['usb']=usb
        if not checks['usb']:return proof
        r['retained_snapshot']=snapshot(board);r['model_snapshot']=deepcopy(cfg)
        self._save(scene)
        proved,receipt=self._probe(scene,deadline);proof['proved_source_keys']=proved;proof['probe']=receipt
        checks['probe_cleanup']=snapshot(board)==r['retained_snapshot'] and cfg==r['model_snapshot']
        verify_prepared_usb(board,usb['usb'],deadline=deadline,max_length_nm=usb_max_length_nm)
        checks['context']=_rules(board)==self.context
        delta=set(r['retained_snapshot']['tracks'])-set(before['tracks'])
        proof['via_count']=sum(r['retained_snapshot']['tracks'][u]['class']=='PCB_VIA' for u in delta)+receipt.get('via_count',0)
        proof['length_nm']=round(sum(math.dist(r['retained_snapshot']['tracks'][u]['start'],r['retained_snapshot']['tracks'][u]['end']) for u in delta))+receipt.get('length_nm',0)
        r['proof']=deepcopy(proof);return proof

    def _save(self,scene):
        r=scene['resource'];directory=r['directory']
        if not self.p.SaveBoard(str(directory/'selected.kicad_pcb'),r['board']):raise ValueError('Cannot save owned candidate')
        (directory/'selected.model.json').write_text(json.dumps(r['cfg'],indent=2))

    def _probe(self,scene,deadline):
        from smilepcb.native_worker import snapshot
        from smilepcb.keyboard_routing.native_graph import copper_graph
        from smilepcb.keyboard_routing.controller_ports import route_controller_ports
        from smilepcb.keyboard_routing.controller_plan import certify_source_batch
        from smilepcb.keyboard_routing.interface_contract import mcu_signal_nets
        from smilepcb.keyboard_phases import ordered_nets
        r=scene['resource'];board,owners=self._load(r['directory'],'selected');cfg=deepcopy(r['cfg']);before=snapshot(board)
        names=mcu_signal_nets(cfg);parts={q['ref']:q for q in cfg['parts']};manifest=scene['native_source_manifest'];all_pads={d.m_Uuid.AsString():(f.GetReference(),d) for f in board.GetFootprints() for d in f.Pads() if d.GetNumber()}
        graph=copper_graph(board,{d.GetNetname() for _,d in all_pads.values()},deadline,include_zones=False);ids={q['item'].m_Uuid.AsString():i for i,q in enumerate(graph['nodes'])}
        discharged=[];external=set()
        for root in manifest['roots']:
            uid=root['pad_ids'][0];net=all_pads[uid][1].GetNetname();component=graph['find'](ids[uid]);members={q['item'].m_Uuid.AsString() for i,q in enumerate(graph['nodes']) if graph['find'](i)==component}
            outside={u for u,(ref,d) in all_pads.items() if d.GetNetname()==net and parts[ref]['region']!='controller'}
            if net in cfg['plane_nets'] or net in ('USB_DP','USB_DM') or not outside or members&outside:discharged.append(root['source_key'])
            else:external.add(net)
        receipt=dict(accepted=True,ports=[],added_ids=[],already_accessible=[])
        if external:
            refs={q['ref'] for q in cfg['parts'] if q['region']=='controller' and not q['ref'].startswith('#')}
            receipt=route_controller_ports(board,controller_refs=refs,mcu_refs={'U_MCU'},plane_nets=cfg['plane_nets'],usb_nets={'USB_DP','USB_DM'},include_nets=external,
                deadline=deadline,clearance_nm=max(board.GetDesignSettings().m_MinClearance,173000),access_mode='prefer_same_layer',continuation_nm=500000,max_candidates=24,max_states=1500,coherent_via_banks=False)
            if not receipt['accepted']:return discharged,dict(accepted=False,reason=receipt.get('reason','Bounded short exits unproved'))
            phases=dict(ordered_nets(cfg));matrix=set(phases['rows']+phases['columns'])&external
            certify_source_batch(board,cfg,before,receipt,matrix_nets=matrix,requested_nets=external,deadline=deadline)
        covered={u for port in receipt['ports'] for u in port['pad_ids']}|{u for port in receipt.get('already_accessible',[]) for u in port['pad_ids']}
        proved=[root['source_key'] for root in manifest['roots'] if root['source_key'] in discharged or set(root['pad_ids'])<=covered]
        tracks=snapshot(board)['tracks'];added=receipt['added_ids']
        return proved,dict(accepted=len(proved)==len(manifest['roots']),source=receipt,via_count=sum(tracks[u]['class']=='PCB_VIA' for u in added),length_nm=round(sum(math.dist(tracks[u]['start'],tracks[u]['end']) for u in added)),scope='joint short exits only; no field routing')

    def resume(self,scene,result):
        from smilepcb.native_worker import snapshot
        from .usb_placement import verify_prepared_usb
        r=scene['resource'];board,owners=self._load(r['directory'],'selected');cfg=json.loads((r['directory']/'selected.model.json').read_text())
        if snapshot(board)!=r['retained_snapshot'] or cfg!=r['cfg']:raise ValueError('Saved selected scene differs from its admission frame')
        verify_prepared_usb(board,r['usb']['usb'],deadline=self.deadline,max_length_nm=self.request.get('usb_max_length_nm',...))
        lookup={f.GetReference():f for f in board.GetFootprints()};core_ids=set(r['assembly']['core_object_ids'])
        core=[q for q in [*board.GetFootprints(),*board.GetTracks()] if q.m_Uuid.AsString() in core_ids]
        updates={k:v for k,v in result.items() if k!='selected_scene'}
        return dict(accepted=True,scene=scene,board=board,model=cfg,owners=owners,lookup=lookup,core_objects=core,
            prepared_usb=r['usb']['usb'],usb_placement=r['usb'],core_pose=scene['pose'],supports=r['assembly']['supports'],
            imported_copper=r['assembly']['imported_copper'],creator_receipts=dict(local=r['local'],returns=r['returns'],usb=r['usb']),result=updates)

    def dispose(self,scene):
        r=scene.get('resource',{});r.pop('board',None);r.pop('owners',None)
        # Preserve every geometric/admission case for the practical comparison.
        r['disposed']=True

    def verify_baseline(self):
        from smilepcb.native_worker import snapshot
        if snapshot(self.board)!=self.entry or self.cfg!=self.model or _rules(self.board)!=self.context:
            raise ValueError('Core lookahead changed baseline board/model/context')


_runtime=_Runtime
