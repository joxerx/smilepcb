"""Finite, fixture-independent proposals for jointly moving neighboring chains."""
from collections import defaultdict
import math,time
from .follow_geometry import offset_path,follow_spans,simplify
from .polish_geometry import octile,shortcut_candidates,valid_shape,shape_score
from .parallel_neighbors import ParallelNeighbors,frame


def _point_distance(q,a,b):
 d=(b[0]-a[0],b[1]-a[1]);norm=sum(x*x for x in d)
 t=max(0,min(1,sum((q[i]-a[i])*d[i] for i in (0,1))/norm)) if norm else 0
 return math.dist(q,(a[0]+t*d[0],a[1]+t*d[1]))


def _distance(a,b,c,d):
 cross=lambda u,v,w:(v[0]-u[0])*(w[1]-u[1])-(v[1]-u[1])*(w[0]-u[0])
 if max(min(a[0],b[0]),min(c[0],d[0]))<=min(max(a[0],b[0]),max(c[0],d[0])) and max(min(a[1],b[1]),min(c[1],d[1]))<=min(max(a[1],b[1]),max(c[1],d[1])):
  if cross(a,b,c)*cross(a,b,d)<=0 and cross(c,d,a)*cross(c,d,b)<=0:return 0.0
 return min(_point_distance(a,c,d),_point_distance(b,c,d),_point_distance(c,a,b),_point_distance(d,a,b))


def paths_clear(paths,widths,clearance_nm):
 if len(paths)!=len(widths):raise ValueError('One width per path required')
 for i,path in enumerate(paths):
  if not valid_shape(path):return False
  edges=list(zip(path,path[1:]))
  if any(_distance(*a,*b)<1 for j,a in enumerate(edges) for b in edges[j+2:]):return False
  # A two-step chamfer can legitimately merge at its corner. More distant
  # parts must not fold back into their own copper capsule.
  if any(_distance(*a,*b)+1e-6<widths[i] for j,a in enumerate(edges) for b in edges[j+3:]):return False
  for j in range(i):
   required=(widths[i]+widths[j])/2+clearance_nm
   if any(_distance(a,b,c,d)+1e-6<required for a,b in edges for c,d in zip(paths[j],paths[j][1:])):return False
 return True


def _parallel_intervals(a,b):
 for x,y in zip(a,a[1:]):
  first=frame(x,y)
  if first is None:continue
  for u,v in zip(b,b[1:]):
   second=frame(u,v)
   if second is None or first[0]!=second[0]:continue
   overlap=min(first[3],second[3])-max(first[2],second[2])
   if overlap>0:yield overlap,abs(first[1]-second[1])


def bundle_score(paths,widths,*,clearance_nm,bend_price_nm=2000000,group_strength=1.0,nets=None):
 names=[str(i) for i in range(len(paths))] if nets is None else list(nets)
 if len(names)!=len(paths):raise ValueError('One net identity per context path required')
 rows=[dict(start=a,end=b,net=names[i],layer=0,width=widths[i]) for i,path in enumerate(paths) for a,b in zip(path,path[1:])]
 index=ParallelNeighbors(rows,eligible_nets=set(names),min_edge_gap=clearance_nm,max_edge_gap=max(3000000,clearance_nm+1))
 length=sum(math.dist(a,b) for path in paths for a,b in zip(path,path[1:]));coverage=sum(index.coverage(a,b,0,names[i],widths[i]) for i,path in enumerate(paths) for a,b in zip(path,path[1:]))
 roughness=0.0
 for i in range(len(paths)):
  for j in range(i):
   if names[i]==names[j]:continue
   intervals=[(run,gap-(widths[i]+widths[j])/2) for run,gap in _parallel_intervals(paths[i],paths[j]) if gap<3000000+(widths[i]+widths[j])/2]
   total=sum(run for run,gap in intervals)
   if not total:continue
   mean=sum(run*gap for run,gap in intervals)/total
   roughness+=sum(run*abs(gap-mean)/max(clearance_nm,abs(mean)) for run,gap in intervals)
 return length+bend_price_nm*sum(max(0,len(simplify(path))-2) for path in paths)+group_strength*(max(0,length-coverage)+.4*roughness)


def quality_improves(old_paths,new_paths,old_score,new_score,*,minimum_gain_nm=100000):
 """A scalar affinity gain cannot buy an extra dogleg in simplification mode."""
 bends=lambda paths:sum(max(0,len(simplify(q))-2) for q in paths)
 if not math.isfinite(old_score) or not math.isfinite(new_score):return False
 if [simplify(q) for q in old_paths]==[simplify(q) for q in new_paths]:return False
 return bends(new_paths)<=bends(old_paths) and old_score-new_score>=minimum_gain_nm


def neighbor_groups(chains,*,max_gap_nm=3000000,min_overlap_nm=2000000,limit=32,deadline=None):
 """Spatially index parallel strips and respect the caller's proof reserve."""
 if max_gap_nm<=0 or type(limit) is not int or limit<1:raise ValueError('Positive discovery limits required')
 expired=lambda:deadline is not None and time.monotonic()>=deadline
 if expired():return []
 buckets=defaultdict(list);entries=[];max_width=max((q['width'] for q in chains),default=0);opportunity={}
 for i,q in enumerate(chains):
  if expired():return []
  if len(q['path'])>24:continue
  best=min(shape_score(path,2000000) for path in octile(q['path'][0],q['path'][-1]))
  opportunity[i]=max(0,shape_score(q['path'],2000000)-best)
  for a,b in zip(q['path'],q['path'][1:]):
   f=frame(a,b)
   if f is None:continue
   direction,c,lo,hi=f;row=(i,c,lo,hi,q['width']);entries.append((q['layer'],direction,row))
   buckets[q['layer'],direction,math.floor(c/max_gap_nm)].append(row)
 shared=defaultdict(float)
 for layer,direction,(i,c,lo,hi,width) in entries:
  if expired():break
  radius=max_gap_nm+(width+max_width)/2
  for cell in range(math.floor((c-radius)/max_gap_nm),math.floor((c+radius)/max_gap_nm)+1):
   for k,(j,nc,nlo,nhi,nwidth) in enumerate(buckets.get((layer,direction,cell),())):
    if k%64==0 and expired():break
    if j>=i or chains[i]['net']==chains[j]['net']:continue
    distance=abs(c-nc);half=(width+nwidth)/2
    overlap=min(hi,nhi)-max(lo,nlo)
    if half<=distance<=max_gap_nm+half and overlap>0:shared[i,j]+=overlap
 neighbors=defaultdict(list);pairs=[]
 for (i,j),run in shared.items():
  if run<min_overlap_nm:continue
  neighbors[i].append((run,j));neighbors[j].append((run,i));pairs.append((run,i,j))
 result=[];seen=set()
 for run,i,j in sorted(pairs,key=lambda row:(opportunity[row[1]]+opportunity[row[2]],row),reverse=True):
  if expired():break
  selected=[i,j];nets={chains[i]['net'],chains[j]['net']}
  for _,k in sorted(neighbors[i]+neighbors[j],key=lambda row:(opportunity[row[1]],row),reverse=True):
   if len(selected)>=6:break
   if chains[k]['net'] not in nets:selected.append(k);nets.add(chains[k]['net'])
  for size in sorted({2,len(selected)}):
   key=tuple(sorted(selected[:size]))
   if key not in seen:seen.add(key);result.append([chains[k] for k in key])
   if len(result)>=limit:return result
 return result


def bundle_candidates(group,*,clearance_nm,margin_nm=25000,limit=48,deadline=None,bend_price_nm=2000000,group_strength=1.0,path_admissible=None,allow_layer_bridges=False):
 if not 2<=len(group)<=6 or len({q['net'] for q in group})!=len(group):raise ValueError('Two to six distinct nets required')
 if len({q['layer'] for q in group})!=1:raise ValueError('Bundle needs a common physical face')
 if type(limit) is not int or not 1<=limit<=256:raise ValueError('Candidate limit must be 1..256')
 if type(clearance_nm) is not int or clearance_nm<=0 or type(margin_nm) is not int or margin_nm<0:raise ValueError('Positive physical clearance and nonnegative margin required')
 if any(type(q['width']) is not int or q['width']<=0 for q in group):raise ValueError('Invalid physical width')
 original=[simplify(q['path']) for q in group];widths=[q['width'] for q in group]
 score=lambda paths:bundle_score(paths,widths[:len(paths)],clearance_nm=clearance_nm,bend_price_nm=bend_price_nm,group_strength=group_strength)
 expired=lambda:deadline is not None and time.monotonic()>=deadline
 results={};beam_width=4
 for reference in range(len(group)):
  if expired():break
  path=original[reference];dx,dy=path[-1][0]-path[0][0],path[-1][1]-path[0][1];norm=math.hypot(dx,dy)
  if not norm:continue
  normal=lambda point:(-dy*point[0]+dx*point[1])/norm
  positions=[(normal(q[0])+normal(q[-1]))/2 for q in original];order=sorted(range(len(group)),key=lambda k:(positions[k],k))
  gaps=[positions[j]-positions[i]-(widths[i]+widths[j])/2 for i,j in zip(order,order[1:])]
  observed=round(sum(gaps)/len(gaps));minimum=clearance_nm+margin_nm
  references=sorted(shortcut_candidates(path),key=lambda q:(shape_score(q,bend_price_nm),q))[:4]
  for gap in sorted({minimum,max(minimum,observed)}):
   offsets={order[0]:0}
   for a,b in zip(order,order[1:]):offsets[b]=offsets[a]+math.ceil((widths[a]+widths[b])/2+gap)
   base=offsets[reference];offsets={i:value-base for i,value in offsets.items()}
   center=round(sum(positions)/len(positions)-positions[reference]-sum(offsets.values())/len(offsets))
   for ref in references:
    for shift in sorted({0,center,round(center/2)}):
     if expired():break
     beam=[[]]
     for i in range(len(group)):
      delta=offsets[i]+shift;lane=ref if not delta else offset_path(ref,delta)
      if lane is None:beam=[];break
      a,z=original[i][0],original[i][-1];options={tuple(q) for q in [*octile(a,z),original[i]] if valid_shape(q)}
      for middle in follow_spans(lane,a,z):
       for left in octile(a,middle[0]):
        for right in octile(middle[-1],z):
         q=simplify([*left,*middle[1:],*right[1:]])
         if valid_shape(q):options.add(tuple(q))
      if path_admissible is not None:options={q for q in options if path_admissible(i,list(q))}
      joined=[]
      for previous in beam:
       for q in options:
        candidate=[*previous,list(q)]
        # Physical two-layer admission can bridge different nets crossing
        # in XY. Self contacts remain invalid on every projection.
        clear=(all(paths_clear([path],[width],0) for path,width in zip(candidate,widths)) if allow_layer_bridges else paths_clear(candidate,widths[:i+1],clearance_nm+margin_nm))
        if clear:joined.append(candidate)
      beam=sorted(joined,key=lambda q:(score(q),q))[:beam_width]
      if not beam:break
     for paths in beam:
      key=tuple(tuple(q) for q in paths);results[key]={'paths':paths,'score':score(paths),'reference':reference,'gap_nm':gap}
     if len(results)>limit*4:
      results={tuple(tuple(q) for q in row['paths']):row for row in sorted(results.values(),key=lambda q:(q['score'],q['paths']))[:limit*2]}
 return sorted(results.values(),key=lambda q:(q['score'],q['paths']))[:limit]


def preserves_clean_columns(row,path,column_nets):
 """Joint affinity cannot undo a vertical/45-degree column backbone."""
 if row['net'] not in column_nets:return True
 old=row['path'];dx=old[-1][0]-old[0][0];dy=old[-1][1]-old[0][1]
 clean=abs(dy)>=abs(dx) and all(a[1]!=b[1] and (a[0]==b[0] or abs(a[0]-b[0])==abs(a[1]-b[1])) for a,b in zip(old,old[1:]))
 return not clean or all(a[1]!=b[1] and (a[0]==b[0] or abs(a[0]-b[0])==abs(a[1]-b[1])) for a,b in zip(path,path[1:]))


def stemmed_chain(path,start_nm,end_nm):
 """Retain short existing pad exits; move the free body between them."""
 path=list(map(tuple,path));total=sum(math.dist(a,b) for a,b in zip(path,path[1:]))
 def cut(points,distance):
  distance=max(0,min(distance,total/4));prefix=[points[0]]
  if not distance:return prefix,points
  for i,(a,b) in enumerate(zip(points,points[1:])):
   span=math.dist(a,b)
   if distance<span:
    q=tuple(round(a[k]+(b[k]-a[k])*distance/span) for k in (0,1))
    return simplify([*prefix,q]),simplify([q,*points[i+1:]])
   distance-=span;prefix.append(b)
  return points,[points[-1]]
 left,body=cut(path,start_nm);right,reversed_body=cut(list(reversed(body)),end_nm)
 return left,list(reversed(reversed_body)),list(reversed(right))
