"""Atomic joint refinement of complete native copper, with contact-proof rollback."""
import math,time
from smilepcb.native_worker import p,snapshot
from smilepcb.routing_quality import copper_metrics
from .local_router import LocalRouter,vec
from .completion import pad_partitions
from .polish_geometry import chains
from .bundle_geometry import neighbor_groups,bundle_candidates,bundle_score,quality_improves,preserves_clean_columns


def _partitions(board,deadline):return {frozenset(g) for g in pad_partitions(board,deadline)}


def _copy(item):
 result=item.Duplicate();result.SetUuid(p.KIID(item.m_Uuid.AsString()));return result


def extract_chains(board,*,eligible_nets,protected_ids,deadline,deferred_zone_ids=(),max_points=24,min_length_nm=1000000):
 """Find real pad/via contacts through the spatial index, not all-pad scans."""
 state=snapshot(board);protected=set(protected_ids);eligible=set(eligible_nets)
 rows={u:t for u,t in state['tracks'].items() if u not in protected and not t['locked'] and t['class']=='PCB_TRACK' and t['net'] in eligible}
 if not rows:return []
 oracle=LocalRouter(board,ignored_copper=set(rows),compact=True,track_width_nm=board.GetDesignSettings().m_TrackMinWidth,deadline=deadline,deferred_zone_ids=deferred_zone_ids)
 anchors=set();sites={(r['net'],r['layer'],tuple(q)) for r in rows.values() for q in (r['start'],r['end'])}
 for net,face,q in sorted(sites):
  if oracle.expired():raise TimeoutError('Bundle terminal extraction deadline exhausted')
  probe=p.SHAPE_CIRCLE(vec(q),1);seen=set();connected=False
  for cell in oracle.cells(probe.BBox()):
   for uid,shape,name,gap in oracle.buckets[face].get(cell,()):
    if uid in seen or name!=net:continue
    seen.add(uid)
    if p.SHAPE.Collide(probe,shape,0):connected=True;break
   if connected:break
  if connected:anchors.add((net,face,q))
 return [q for q in chains(rows,anchors) if len(q['path'])<=max_points and sum(math.dist(a,b) for a,b in zip(q['path'],q['path'][1:]))>=min_length_nm]


def _joins_clear(board,path,face,net,ignored,items=None):
 for a,z in ((path[0],path[1]),(path[-1],path[-2])):
  ray=(z[0]-a[0],z[1]-a[1])
  for t in board.GetTracks() if items is None else items:
   if t.GetClass()!='PCB_TRACK' or t.GetLayer()!=face or t.GetNetname()!=net or t.m_Uuid.AsString() in ignored:continue
   u,v=(t.GetStart().x,t.GetStart().y),(t.GetEnd().x,t.GetEnd().y)
   if a not in (u,v):continue
   other=v if u==a else u
   if sum(ray[i]*(other[i]-a[i]) for i in (0,1))>0:return False
 return True


def _outside_caps(a,b,caps):
 """Clip centerline portions inside already intended endpoint junctions."""
 dx,dy=b[0]-a[0],b[1]-a[1];norm=dx*dx+dy*dy;intervals=[(0.0,1.0)]
 if not norm:return []
 for center,radius in caps:
  ax,ay=a[0]-center[0],a[1]-center[1];linear=2*(ax*dx+ay*dy);disc=linear*linear-4*norm*(ax*ax+ay*ay-radius*radius)
  if disc<=0:continue
  root=math.sqrt(disc);lo=max(0,(-linear-root)/(2*norm));hi=min(1,(-linear+root)/(2*norm))
  if hi<=lo:continue
  remaining=[]
  for left,right in intervals:
   if hi<=left or lo>=right:remaining.append((left,right));continue
   if left<lo:remaining.append((left,lo))
   if hi<right:remaining.append((hi,right))
  intervals=remaining
 return [((round(a[0]+lo*dx),round(a[1]+lo*dy)),(round(a[0]+hi*dx),round(a[1]+hi*dy))) for lo,hi in intervals if (hi-lo)*math.sqrt(norm)>1]


def retained_contacts_clear(board,path,face,net,width,*,ignored_ids=(),oracle=None,track_items=None):
 """A rewrite may retain terminal junctions, but not add same-net loops."""
 ignored=set(ignored_ids);items={t.m_Uuid.AsString():t for t in board.GetTracks()} if track_items is None else track_items
 if oracle is None:oracle=LocalRouter(board,ignored_copper=ignored,compact=True,track_width_nm=width)
 contacts={}
 for a,b in zip(path,path[1:]):
  probe=p.SHAPE_SEGMENT(vec(a),vec(b),width)
  for cell in oracle.cells(probe.BBox()):
   for uid,shape,name,gap in oracle.buckets[face].get(cell,()):
    if name==net and uid in items and uid not in ignored and uid not in contacts and p.SHAPE.Collide(probe,shape,0):contacts[uid]=shape
 for uid,shape in contacts.items():
  item=items[uid];diameter=item.GetWidth(face) if isinstance(item,p.PCB_VIA) else item.GetWidth()
  radius=(width+diameter)/2+2000
  caps=[(q,radius) for q in (path[0],path[-1]) if p.SHAPE.Collide(p.SHAPE_CIRCLE(vec(q),1),shape,0)]
  for a,b in zip(path,path[1:]):
   if any(p.SHAPE.Collide(p.SHAPE_SEGMENT(vec(u),vec(v),width),shape,0) for u,v in _outside_caps(a,b,caps)):return False
 return True


def _box(path):return min(q[0] for q in path),min(q[1] for q in path),max(q[0] for q in path),max(q[1] for q in path)


def refine_bundles(board,*,eligible_nets,protected_ids,deadline,deferred_zone_ids=(),max_groups=24,max_candidates=32,bend_price_nm=2000000,group_strength=1.0,focus_bounds=None,preserve_v45_nets=()):
 started=time.monotonic();before=snapshot(board);protected=set(protected_ids)|{u for u,t in before['tracks'].items() if t['locked']};report={'accepted':False,'changed_groups':0,'rewrites':[],'attempts':[],'before_metrics':copper_metrics(before['tracks'])}
 if not set(protected_ids)<=before['tracks'].keys():raise ValueError('Missing protected source')
 if type(max_groups) is not int or not 1<=max_groups<=128 or type(max_candidates) is not int or not 1<=max_candidates<=256:raise ValueError('Invalid bounded bundle worklist')
 if deadline-started<3:return dict(report,reason='Incumbent retained; no optimization budget',elapsed_s=time.monotonic()-started,after_metrics=report['before_metrics'])
 copies=[_copy(t) for t in board.GetTracks()];removed=set()
 def refill():
  board.BuildConnectivity()
  if board.Zones() and not p.ZONE_FILLER(board).Fill(board.Zones()):raise ValueError('Bundle plane refill failed')
 def rollback():
  for t in list(board.GetTracks()):board.Delete(t)
  for t in copies:board.Add(t)
  refill()
  if snapshot(board)!=before:raise ValueError('Bundle rollback changed original copper')
 try:
  original_parts=_partitions(board,deadline);previous_bends=report['before_metrics']['bends']
  work_stop=deadline-max(3,min(12,(deadline-started)/4))
  work=extract_chains(board,eligible_nets=eligible_nets,protected_ids=protected,deadline=work_stop,deferred_zone_ids=deferred_zone_ids)
  if focus_bounds is not None:
   if len(focus_bounds)!=4 or focus_bounds[0]>=focus_bounds[2] or focus_bounds[1]>=focus_bounds[3]:raise ValueError('Invalid physical grouping crop')
   def intersects(row):
    a=_box(row['path']);return a[0]<=focus_bounds[2] and a[2]>=focus_bounds[0] and a[1]<=focus_bounds[3] and a[3]>=focus_bounds[1]
   work=[q for q in work if intersects(q)]
  groups=neighbor_groups(work,limit=max_groups,deadline=work_stop);report.update(chain_count=len(work),group_count=len(groups))
  for group in groups:
   if time.monotonic()>=work_stop:break
   ids={u for q in group for u in q['ids']};items={t.m_Uuid.AsString():t for t in board.GetTracks()}
   if not ids<=items.keys():continue
   oracle=LocalRouter(board,ignored_copper=ids,compact=True,track_width_nm=min(q['width'] for q in group),deadline=min(work_stop,time.monotonic()+5),deferred_zone_ids=deferred_zone_ids)
   gap=max(oracle.minimum_clearance,*[oracle.net_clearances.get(q['net'],0) for q in group])
   cache={};path_counts={'checked':0,'foreign_rejected':0,'join_rejected':0,'own_contact_rejected':0,'pad_entry_rejected':0}
   kept={q['net']:[t for u,t in items.items() if u not in ids and t.GetNetname()==q['net']] for q in group}
   own_pads={q['net']:[d.GetEffectiveShape(q['layer']) for fp in board.GetFootprints() for d in fp.Pads() if d.GetNumber() and d.GetNetname()==q['net'] and d.IsOnLayer(q['layer'])] for q in group}
   def admissible(i,path):
    key=(i,tuple(path))
    if key in cache:return cache[key]
    if oracle.expired() or not preserves_clean_columns(group[i],path,set(preserve_v45_nets)):return False
    row=group[i];net=row['net'];face=row['layer'];width=row['width'];path_counts['checked']+=1;okay=True
    if not all(oracle.clear(p.SHAPE_SEGMENT(vec(a),vec(b),width),face,net) for a,b in zip(path,path[1:])):path_counts['foreign_rejected']+=1;okay=False
    elif not _joins_clear(board,path,face,net,ids,items=kept[net]):path_counts['join_rejected']+=1;okay=False
    elif not retained_contacts_clear(board,path,face,net,width,ignored_ids=ids,oracle=oracle,track_items=items):path_counts['own_contact_rejected']+=1;okay=False
    else:
     ends=[p.SHAPE_CIRCLE(vec(q),0) for q in (path[0],path[-1])];segments=[p.SHAPE_SEGMENT(vec(a),vec(b),0) for a,b in zip(path,path[1:])]
     for shape in own_pads[net]:
      if any(p.SHAPE.Collide(s,shape,0) for s in segments) and not any(p.SHAPE.Collide(e,shape,0) for e in ends):path_counts['pad_entry_rejected']+=1;okay=False;break
    cache[key]=okay;return okay
   proposals=bundle_candidates(group,clearance_nm=gap,limit=max_candidates,deadline=oracle.deadline,bend_price_nm=bend_price_nm,group_strength=group_strength,path_admissible=admissible)
   boxes=[_box(q['path']) for q in group];bounds=(min(b[0] for b in boxes)-3000000,min(b[1] for b in boxes)-3000000,max(b[2] for b in boxes)+3000000,max(b[3] for b in boxes)+3000000)
   context=[]
   for q in work:
    if q['layer']!=group[0]['layer'] or set(q['ids'])&ids or not set(q['ids'])<=items.keys():continue
    a=_box(q['path'])
    if a[0]<=bounds[2] and a[2]>=bounds[0] and a[1]<=bounds[3] and a[3]>=bounds[1]:context.append(q)
   widths=[q['width'] for q in group+context];names=[q['net'] for q in group+context]
   def score(paths):return bundle_score([*paths,*[q['path'] for q in context]],widths,clearance_nm=gap,bend_price_nm=bend_price_nm,group_strength=group_strength,nets=names)
   old_paths=[q['path'] for q in group];old_score=score(old_paths);old_length=sum(math.dist(a,b) for path in old_paths for a,b in zip(path,path[1:]));trial={'nets':[q['net'] for q in group],'old_score':old_score,'candidates':len(proposals),'context_chains':len(context),'clearance_rejections':0,'path_checks':path_counts,'accepted':False};report['attempts'].append(trial)
   ranked=[]
   for proposal in proposals:
    if oracle.expired():break
    paths=proposal['paths'];length=sum(math.dist(a,b) for path in paths for a,b in zip(path,path[1:]))
    if length>old_length*1.2+1:continue
    value=score(paths)
    if quality_improves(old_paths,paths,old_score,value):ranked.append((value,paths))
   selected=None
   for value,paths in sorted(ranked,key=lambda q:(sum(max(0,len(path)-2) for path in q[1]),q[0],q[1])):
    if oracle.expired():break
    okay=all(admissible(i,path) for i,path in enumerate(paths))
    if okay:selected=(value,paths);break
    trial['clearance_rejections']+=1
   if selected is None:continue
   value,paths=selected;restore=[_copy(items[u]) for u in sorted(ids)];added=[];new_rows=[]
   for u in ids:board.Delete(items[u])
   try:
    for row,path in zip(group,paths):
     oracle.track_width=row['width'];mark=len(oracle.added);oracle.add_path(path,row['layer'],row['net']);new_ids=oracle.added[mark:];added.extend(new_ids);new_rows.append(dict(row,path=path,ids=new_ids))
    refill()
    if _partitions(board,deadline)!=original_parts:raise ValueError('Joint group changed numbered-pad contacts')
   except ValueError as error:
    for t in list(board.GetTracks()):
     if t.m_Uuid.AsString() in set(added):board.Delete(t)
    for t in restore:board.Add(t)
    refill();trial['rejection_reason']=str(error)
    if _partitions(board,deadline)!=original_parts:raise ValueError('Rejected group rollback lost contacts')
    continue
   except BaseException:
    for t in list(board.GetTracks()):
     if t.m_Uuid.AsString() in set(added):board.Delete(t)
    for t in restore:board.Add(t)
    refill();raise
   current_bends=copper_metrics(snapshot(board)['tracks'])['bends']
   if current_bends>previous_bends:
    for t in list(board.GetTracks()):
     if t.m_Uuid.AsString() in set(added):board.Delete(t)
    for t in restore:board.Add(t)
    refill();trial['reason']='Reject added global bend';continue
   previous_bends=current_bends
   removed.update(ids);work.extend(new_rows);trial.update(accepted=True,new_score=value);report['changed_groups']+=1
   report['rewrites'].append({'nets':trial['nets'],'old':old_paths,'new':paths,'old_ids':sorted(ids),'new_ids':added,'old_score':old_score,'new_score':value})
  after=snapshot(board)
  if after['footprints']!=before['footprints'] or any(after['tracks'].get(u)!=t for u,t in before['tracks'].items() if u not in removed):raise ValueError('Bundle changed source outside admitted rewrites')
  if _partitions(board,deadline)!=original_parts:raise ValueError('Final bundle contact proof failed')
  if time.monotonic()>=deadline:raise TimeoutError('Bundle proof deadline exhausted')
  report.update(accepted=True,pad_partitions_exact=True,footprints_exact=True,source_outside_rewrites_exact=True,protected_source_exact=True,after_metrics=copper_metrics(after['tracks']))
 except (ValueError,TimeoutError) as error:
  rollback();report.update(reason=str(error),changed_groups=0,after_metrics=report['before_metrics']);report['rewrites']=[]
 except BaseException:
  rollback();raise
 report['elapsed_s']=time.monotonic()-started
 return report
