"""Replace a via and all incident arms as one native-verified transaction."""
import math,time
from collections import defaultdict
from smilepcb.native_worker import p,snapshot
from smilepcb.routing_quality import copper_metrics
from .bundle_polish import extract_chains,retained_contacts_clear,_copy,_outside_caps,_joins_clear
from .bundle_geometry import paths_clear
from .local_router import LocalRouter,xy,vec
from .completion import pad_partitions
from .parallel_neighbors import ParallelNeighbors
from .polish_geometry import shape_score,octile
from .via_junction_geometry import junction_sites,arm_options
from .role_quality import projected_tracks


def _parts(board,deadline):return {frozenset(g) for g in pad_partitions(board,deadline)}


def _refill(board):
 board.BuildConnectivity()
 if board.Zones() and not p.ZONE_FILLER(board).Fill(board.Zones()):raise ValueError('Junction refill failed')


def via_stars(board,*,eligible_nets,protected_ids,deadline,deferred_zone_ids=()):
 protected=set(protected_ids);state=snapshot(board)
 if not protected<=state['tracks'].keys():raise ValueError('Missing protected source')
 work=extract_chains(board,eligible_nets=eligible_nets,protected_ids=protected,deadline=deadline,deferred_zone_ids=deferred_zone_ids,max_points=32,min_length_nm=0)
 endpoints=defaultdict(list)
 for row in work:
  for reverse in (False,True):
   path=list(reversed(row['path'])) if reverse else row['path']
   endpoints[row['net'],tuple(path[-1])].append(dict(row,path=path))
 oracle=LocalRouter(board,compact=True,deadline=deadline,deferred_zone_ids=deferred_zone_ids)
 out=[]
 for via in board.GetTracks():
  if time.monotonic()>=deadline:break
  if not isinstance(via,p.PCB_VIA) or via.GetViaType()!=p.VIATYPE_THROUGH:continue
  uid=via.m_Uuid.AsString();net=via.GetNetname();origin=xy(via.GetPosition())
  if uid in protected or via.IsLocked() or net not in eligible_nets:continue
  if via.TopLayer()!=p.F_Cu or via.BottomLayer()!=p.B_Cu or via.GetWidth(p.F_Cu)!=via.GetWidth(p.B_Cu):continue
  arms=endpoints.get((net,origin),[]);ids={u for arm in arms for u in arm['ids']}
  if not 2<=len(arms)<=6 or {a['layer'] for a in arms}!={p.F_Cu,p.B_Cu}:continue
  if sum(len(a['ids']) for a in arms)!=len(ids):continue
  modeled=True
  for layer in (p.F_Cu,p.B_Cu):
   shape=via.GetEffectiveShape(layer);seen={uid}
   for cell in oracle.cells(shape.BBox()):
    for other,s,name,_ in oracle.buckets[layer].get(cell,()):
     if other in seen or name!=net:continue
     seen.add(other)
     if p.SHAPE.Collide(shape,s,0) and other not in ids:modeled=False;break
    if not modeled:break
  if not modeled:continue
  star={'via_id':uid,'net':net,'origin':origin,'arms':arms,'track_rows':{u:state['tracks'][u] for u in ids|{uid}}}
  if not _old_contacts_clear(board,star,oracle):continue
  out.append(star)
 def potential(star):return sum(shape_score(a['path'],2000000)-min(shape_score(q,2000000) for q in octile(a['path'][0],a['path'][-1])) for a in star['arms'])
 return sorted(out,key=lambda s:(-potential(s),s['net'],s['origin']))


def _arms_separate(arms,paths,position,diameter):
 """Same-net arms may meet inside the via, never fold into an extra loop."""
 for i,(arm,path) in enumerate(zip(arms,paths)):
  if not paths_clear([path],[arm['width']],0):return False
  for j,other in enumerate(paths[:i]):
   if arms[j]['layer']!=arm['layer']:continue
   radius=diameter/2+max(arm['width'],arms[j]['width'])/2+2000
   caps=[(position,radius)]
   if path[0]==other[0]:caps.append((path[0],radius))
   for a,b in zip(path,path[1:]):
    for u,v in _outside_caps(a,b,caps):
     shape=p.SHAPE_SEGMENT(vec(u),vec(v),arm['width'])
     if any(p.SHAPE.Collide(shape,p.SHAPE_SEGMENT(vec(c),vec(d),arms[j]['width']),0) for c,d in zip(other,other[1:])):return False
 return True


def _via_own_clear(oracle,position,net,diameter):
 shape=p.SHAPE_CIRCLE(vec(position),diameter//2);seen=set()
 for face in (p.F_Cu,p.B_Cu):
  for cell in oracle.cells(shape.BBox()):
   for uid,other,name,_ in oracle.buckets[face].get(cell,()):
    if (uid,face) in seen or name!=net:continue
    seen.add((uid,face))
    if p.SHAPE.Collide(shape,other,0):return False
 return True


def _pad_contacts_clear(board,path,face,net,width,oracle):
 """Use physical copper capsules, including grazing contacts outside centerline."""
 if not hasattr(oracle,'_junction_pad_ids'):
  oracle._junction_pad_ids={d.m_Uuid.AsString() for f in board.GetFootprints() for d in f.Pads()}
 contacts={}
 for a,b in zip(path,path[1:]):
  probe=p.SHAPE_SEGMENT(vec(a),vec(b),width)
  for cell in oracle.cells(probe.BBox()):
   for uid,shape,name,_ in oracle.buckets[face].get(cell,()):
    if name==net and uid in oracle._junction_pad_ids and uid not in contacts and p.SHAPE.Collide(probe,shape,0):contacts[uid]=shape
 for shape in contacts.values():
  box=shape.BBox();radius=math.hypot(box.GetWidth(),box.GetHeight())/2+width/2+2000
  caps=[(q,radius) for q in (path[0],path[-1]) if p.SHAPE.Collide(p.SHAPE_CIRCLE(vec(q),1),shape,0)]
  for a,b in zip(path,path[1:]):
   if any(p.SHAPE.Collide(p.SHAPE_SEGMENT(vec(u),vec(v),width),shape,0) for u,v in _outside_caps(a,b,caps)):return False
 return True


def _old_contacts_clear(board,star,oracle):
 items={t.m_Uuid.AsString():t for t in board.GetTracks()}
 return all(_pad_contacts_clear(board,a['path'],a['layer'],star['net'],a['width'],oracle) and retained_contacts_clear(board,a['path'],a['layer'],star['net'],a['width'],ignored_ids=star['track_rows'],oracle=oracle,track_items=items) for a in star['arms'])


def _oracle(board,star,deadline,deferred):
 oracle=LocalRouter(board,ignored_copper=set(star['track_rows']),compact=True,track_width_nm=min(a['width'] for a in star['arms']),deadline=deadline,deferred_zone_ids=deferred)
 row=star['track_rows'][star['via_id']];oracle.via_width=row['width'];oracle.drill=row['drill'];return oracle


def _admissible(board,star,position,paths,oracle):
 arms=star['arms'];net=star['net'];ids=set(star['track_rows']);diameter=star['track_rows'][star['via_id']]['width']
 if len(paths)!=len(arms) or any(tuple(q[0])!=tuple(a['path'][0]) or tuple(q[-1])!=tuple(position) for a,q in zip(arms,paths)):return False
 if not oracle.via_clear(position,net) or not _via_own_clear(oracle,position,net,diameter):return False
 if not _arms_separate(arms,paths,position,diameter):return False
 items={t.m_Uuid.AsString():t for t in board.GetTracks()};kept=[t for u,t in items.items() if u not in ids]
 for arm,path in zip(arms,paths):
  width=arm['width'];face=arm['layer']
  if not _joins_clear(board,path,face,net,ids,items=kept):return False
  if not _pad_contacts_clear(board,path,face,net,width,oracle):return False
  if not retained_contacts_clear(board,path,face,net,width,ignored_ids=ids,oracle=oracle,track_items=items):return False
  if not all(oracle.clear(p.SHAPE_SEGMENT(vec(a),vec(b),width),face,net) for a,b in zip(path,path[1:])):return False
 return not oracle.expired()


def replace_junction(board,star,position,paths,*,protected_ids,deadline,deferred_zone_ids=(),expected_partitions=None,quality_gate=None):
 """Apply a complete native-checked star or restore its exact prior objects."""
 before=snapshot(board);ids=set(star['track_rows']);protected=set(protected_ids);items={t.m_Uuid.AsString():t for t in board.GetTracks()}
 if time.monotonic()>=deadline:return {'accepted':False,'reason':'Deadline; incumbent retained'}
 if not ids<=items.keys() or any(before['tracks'][u]!=row for u,row in star['track_rows'].items()):return {'accepted':False,'reason':'Stale junction inventory'}
 if any(u in protected or items[u].IsLocked() for u in ids):return {'accepted':False,'reason':'Protected junction source'}
 oracle=_oracle(board,star,deadline,deferred_zone_ids)
 if not _old_contacts_clear(board,star,oracle):return {'accepted':False,'reason':'Unsupported old-arm contact'}
 if not _admissible(board,star,position,paths,oracle):return {'accepted':False,'reason':'Physical or same-net contact conflict'}
 if quality_gate is not None and not quality_gate(before['tracks'],projected_tracks(before['tracks'],ids,[(arm['layer'],star['net'],arm['width'],path) for arm,path in zip(star['arms'],paths)])):return {'accepted':False,'reason':'Whole MCU area grouping would degrade'}
 parts=expected_partitions if expected_partitions is not None else _parts(board,deadline)
 copies=[_copy(items[u]) for u in sorted(ids)];added=[]
 try:
  via=items[star['via_id']]
  for u in ids-{star['via_id']}:board.Delete(items[u])
  via.SetPosition(vec(position))
  for arm,path in zip(star['arms'],paths):
   oracle.track_width=arm['width'];mark=len(oracle.added);oracle.add_path(path,arm['layer'],star['net']);added.extend(oracle.added[mark:])
  _refill(board)
  if _parts(board,deadline)!=parts:raise ValueError('Junction changed numbered-pad contacts')
  after=snapshot(board)
  if quality_gate is not None and not quality_gate(before['tracks'],after['tracks']):raise ValueError('Whole MCU area grouping would degrade')
  if after['footprints']!=before['footprints'] or any(after['tracks'].get(u)!=row for u,row in before['tracks'].items() if u not in ids):raise ValueError('Junction changed retained source')
  if copper_metrics(after['tracks'])['bends']>copper_metrics(before['tracks'])['bends']:raise ValueError('Junction added global bends')
  if time.monotonic()>=deadline:raise TimeoutError('Junction proof deadline exhausted')
  return {'accepted':True,'via_id':star['via_id'],'net':star['net'],'old_via_nm':star['origin'],'new_via_nm':position,'old_ids':sorted(ids),'new_ids':added,'arms':star['arms'],'old':[a['path'] for a in star['arms']],'new':paths,'moved_source_ids':[star['via_id']]}
 except BaseException as error:
  for t in list(board.GetTracks()):
   if t.m_Uuid.AsString() in ids or t.m_Uuid.AsString() not in before['tracks']:board.Delete(t)
  for item in copies:board.Add(item)
  _refill(board)
  if snapshot(board)!=before or _parts(board,max(deadline,time.monotonic()+5))!=parts:raise ValueError('Junction rollback did not restore exact contacts') from error
  if isinstance(error,(ValueError,TimeoutError)):return {'accepted':False,'reason':str(error)}
  raise


def polish_via_junctions(board,*,eligible_nets,protected_ids,deadline,deferred_zone_ids=(),bend_price_nm=2000000,group_strength=1.0,max_sites=96,uniform_spacing=False,focus_bounds=None,quality_gate=None):
 started=time.monotonic();before=snapshot(board);report={'accepted':True,'moved_vias':0,'rewrites':[],'attempts':[],'before_metrics':copper_metrics(before['tracks'])}
 if board.GetCopperLayerCount()!=2:raise ValueError('Expected two copper layers')
 if type(bend_price_nm) is not int or not 0<=bend_price_nm<=10000000:raise ValueError('Invalid bend cost')
 if not math.isfinite(group_strength) or not 0<=group_strength<=4:raise ValueError('Invalid grouping strength')
 if deadline-started<3:return dict(report,reason='No optimization budget; incumbent retained',after_metrics=report['before_metrics'],elapsed_s=time.monotonic()-started)
 protected=set(protected_ids)|{u for u,t in before['tracks'].items() if t['locked']};copies=[_copy(t) for t in board.GetTracks()];removed=set();moved=set()
 try:
  original_parts=_parts(board,deadline);stop=deadline-max(4,min(12,(deadline-started)/4));stars=via_stars(board,eligible_nets=eligible_nets,protected_ids=protected,deadline=stop,deferred_zone_ids=deferred_zone_ids);report['star_count']=len(stars)
  for star in stars:
   if time.monotonic()>=stop:break
   if focus_bounds is not None and not (focus_bounds[0]<=star['origin'][0]<=focus_bounds[2] and focus_bounds[1]<=star['origin'][1]<=focus_bounds[3]):continue
   current=snapshot(board)
   if any(current['tracks'].get(u)!=v for u,v in star['track_rows'].items()):continue
   local_stop=min(stop,time.monotonic()+3);oracle=_oracle(board,star,local_stop,deferred_zone_ids)
   live=[dict(t) for u,t in current['tracks'].items() if u not in star['track_rows'] and t['class']=='PCB_TRACK']
   from .visual_geometry import UniformNeighbors,foreign_guides
   neighbor_type=UniformNeighbors if uniform_spacing else ParallelNeighbors
   neighbors=neighbor_type(live,eligible_nets=set(eligible_nets),min_edge_gap=max(oracle.minimum_clearance,173000)+(25000 if uniform_spacing else 0),min_run_nm=2000000 if uniform_spacing else 0)
   guides=foreign_guides(star['origin'],[t for t in live if t['net'] in eligible_nets],net=star['net'],width=max(a['width'] for a in star['arms']),gap_nm=neighbors.minimum) if uniform_spacing else []
   def score(arm,path):
    length=sum(math.dist(a,b) for a,b in zip(path,path[1:]));covered=sum(neighbors.coverage(a,b,arm['layer'],star['net'],arm['width']) for a,b in zip(path,path[1:]))
    return shape_score(path,bend_price_nm)+group_strength*max(0,length-covered)
   old_paths=[a['path'] for a in star['arms']];old_score=sum(score(a,q) for a,q in zip(star['arms'],old_paths));old_bends=sum(max(0,len(q)-2) for q in old_paths);old_length=sum(math.dist(a,b) for q in old_paths for a,b in zip(q,q[1:]));trial={'via_id':star['via_id'],'sites':0,'physically_feasible':0,'contact_rejections':0,'accepted':False};report['attempts'].append(trial)
   ranked=[]
   own_sites=junction_sites(old_paths,star['origin'],limit=max_sites,deadline=local_stop,guide_points=guides)
   sites=list(dict.fromkeys([*guides,*own_sites]))[:max_sites]
   for site in sites:
    if oracle.expired():break
    trial['sites']+=1
    if not oracle.via_clear(site,star['net']) or not _via_own_clear(oracle,site,star['net'],oracle.via_width):continue
    options=[]
    for arm in star['arms']:
     paths=arm_options(arm['path'],site);valid=[]
     for path in sorted(paths,key=lambda q:(score(arm,q),q)):
      if oracle.expired():break
      if not _pad_contacts_clear(board,path,arm['layer'],star['net'],arm['width'],oracle):continue
      if not retained_contacts_clear(board,path,arm['layer'],star['net'],arm['width'],ignored_ids=star['track_rows'],oracle=oracle):continue
      if all(oracle.clear(p.SHAPE_SEGMENT(vec(a),vec(b),arm['width']),arm['layer'],star['net']) for a,b in zip(path,path[1:])):valid.append(path)
      if len(valid)>=4:break
     if not valid:options=[];break
     options.append(valid)
    if not options:continue
    beam=[[]]
    for i,paths in enumerate(options):
     joined=[prev+[q] for prev in beam for q in paths if _arms_separate(star['arms'][:i+1],prev+[q],site,oracle.via_width)]
     beam=sorted(joined,key=lambda q:(sum(score(a,path) for a,path in zip(star['arms'],q)),q))[:4]
     if not beam:break
    for paths in beam:
     bends=sum(max(0,len(q)-2) for q in paths);length=sum(math.dist(a,b) for q in paths for a,b in zip(q,q[1:]));value=sum(score(a,q) for a,q in zip(star['arms'],paths))
     if bends>old_bends or length>old_length*1.2+1 or value>old_score-100000:continue
     if quality_gate is not None and not quality_gate(current['tracks'],projected_tracks(current['tracks'],star['track_rows'],[(arm['layer'],star['net'],arm['width'],path) for arm,path in zip(star['arms'],paths)])):continue
     trial['physically_feasible']+=1;ranked.append((bends,value,site,paths))
   for _,value,site,paths in sorted(ranked,key=lambda q:(q[0],q[1],q[2],q[3]))[:8]:
    if time.monotonic()>=stop:break
    result=replace_junction(board,star,site,paths,protected_ids=protected,deadline=deadline,deferred_zone_ids=deferred_zone_ids,expected_partitions=original_parts,quality_gate=quality_gate)
    if result['accepted']:
     report['rewrites'].append(result);report['moved_vias']+=1;trial.update(accepted=True,old_score=old_score,new_score=value);removed.update(star['track_rows']);moved.add(star['via_id']);break
    trial['contact_rejections']+=1;trial['last_rejection']=result['reason']
  after=snapshot(board)
  if after['footprints']!=before['footprints'] or any(after['tracks'].get(u)!=v for u,v in before['tracks'].items() if u not in removed):raise ValueError('Final via pass changed retained source')
  if _parts(board,deadline)!=original_parts:raise ValueError('Final via pass lost contacts')
  if time.monotonic()>=deadline:raise TimeoutError('Final via proof deadline exhausted')
  report.update(after_metrics=copper_metrics(after['tracks']),all_pad_partitions_exact=True,protected_source_exact=True)
 except (ValueError,TimeoutError) as error:
  for t in list(board.GetTracks()):board.Delete(t)
  for item in copies:board.Add(item)
  _refill(board)
  if snapshot(board)!=before:raise ValueError('Via pass rollback changed original geometry') from error
  report.update(accepted=False,moved_vias=0,rewrites=[],reason=str(error),after_metrics=report['before_metrics'])
 except BaseException:
  for t in list(board.GetTracks()):board.Delete(t)
  for item in copies:board.Add(item)
  _refill(board);raise
 report['elapsed_s']=time.monotonic()-started;return report
