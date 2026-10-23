"""STM32 recipe support: place. Native checks are required."""
import sys,json,time,math
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from smilepcb import native_worker as w
from smilepcb.keyboard_native import place_diodes
from smilepcb.keyboard_routing.safety import require_via_land_clearance
from smilepcb.keyboard_routing.controller_access import route_controller_access
from smilepcb.keyboard_placement import rgb_chain
from recipes.stm32.transfer import copy_copper,mirror_items
p=w.p
request=json.load(sys.stdin);started=time.monotonic();stop=started+request['budget_s']
project=Path(request['project']);cfg=json.loads((project/'model-before-placement.json').read_text())
board=p.BOARD();nets={}
# Candidate routing uses the same numeric constraints as the final project.
# BOARD defaults otherwise differ before the .kicad_pro has been written.
rules=board.GetDesignSettings();rules.m_MinClearance=p.FromMM(.15);rules.m_CopperEdgeClearance=p.FromMM(.2);rules.m_TrackMinWidth=p.FromMM(.15);rules.m_MinThroughDrill=p.FromMM(.3);rules.m_ViasMinAnnularWidth=p.FromMM(.15);rules.m_ViasMinSize=p.FromMM(.6)
rules.m_NetSettings.GetDefaultNetclass().SetClearance(p.FromMM(.15))
def net(name):
 if name not in nets:nets[name]=p.NETINFO_ITEM(board,name);board.Add(nets[name])
 return nets[name]
def vector(x,y):return p.VECTOR2I(round(x),round(y))
def bbox(shape):
 b=shape.BBox();return (b.GetLeft(),b.GetTop(),b.GetRight(),b.GetBottom())
def box_overlap(a,b,g=0):return a[0]<=b[2]+g and a[2]+g>=b[0] and a[1]<=b[3]+g and a[3]+g>=b[1]
def loaded(part):
 lib,name=part['footprint'].split(':');folder=project/'KeyboardParts.pretty' if lib=='KeyboardParts' else w.SUPPORT/'footprints'/(lib+'.pretty')
 f=p.FootprintLoad(str(folder),name)
 if f is None:raise ValueError('Missing footprint '+part['footprint'])
 f.SetFPID(p.LIB_ID(lib,name));f.SetReference(part['ref']);f.SetValue(part['value']);f.SetUuid(p.KIID())
 f.SetAttributes(f.GetAttributes() & ~p.FP_EXCLUDE_FROM_BOM)
 for d in f.Pads():
  d.SetUuid(p.KIID())
  if d.GetNumber():d.SetNet(net(part['nets'][d.GetNumber()]))
 f.SetPosition(w.point(part['pcb']));board.Add(f)
 if part['side']=='B.Cu':f.Flip(f.GetPosition(),p.FLIP_DIRECTION_LEFT_RIGHT)
 if part.get('rotation'):f.Rotate(f.GetPosition(),p.EDA_ANGLE(part['rotation'],p.DEGREES_T))
 f.Reference().SetVisible(False);f.Value().SetVisible(False)
 return f
for part in cfg['parts']:
 if part['region']!='controller':loaded(part)
def add_outlines():
 for a,z in zip(cfg['outline'],cfg['outline'][1:]+cfg['outline'][:1]):
  edge=p.PCB_SHAPE(board);edge.SetShape(p.SHAPE_T_SEGMENT);edge.SetStart(w.point(a));edge.SetEnd(w.point(z));edge.SetLayer(p.Edge_Cuts);edge.SetWidth(50000);board.Add(edge)
 cutout=p.FootprintLoad(str(project/'KeyboardParts.pretty'),'MINI_E_CUTOUT')
 for key in cfg['layout']['keys']:
  x,y=key['center_mm'];anchor=w.point((x,y+5.08));sign=-1 if key['matrix'][0]%2==0 else 1
  def transformed(v):return vector(sign*v.x+anchor.x,sign*v.y+anchor.y)
  for item in cutout.GraphicalItems():
   if item.GetLayer()!=p.Edge_Cuts:continue
   edge=p.PCB_SHAPE(board);edge.SetShape(item.GetShape())
   if item.GetShape()==p.SHAPE_T_ARC:edge.SetArcGeometry(transformed(item.GetStart()),transformed(item.GetArcMid()),transformed(item.GetEnd()))
   else:edge.SetStart(transformed(item.GetStart()));edge.SetEnd(transformed(item.GetEnd()))
   edge.SetLayer(p.Edge_Cuts);edge.SetWidth(item.GetWidth());board.Add(edge)
add_outlines();board_region=p.SHAPE_POLY_SET()
if not board.GetBoardPolygonOutlines(board_region,False):raise ValueError('Invalid board/cutout contours')
# Preserve the existing automatic diode-placement behavior before MCU packing.
diodes=place_diodes(board,cfg)
fixed=list(board.GetFootprints());cells={};cell=10000000
def add_shape(shape,layer,kind,netname=''):
 box=bbox(shape);entry=(shape,box,kind,netname)
 for x in range(box[0]//cell,box[2]//cell+1):
  for y in range(box[1]//cell,box[3]//cell+1):cells.setdefault((layer,x,y),[]).append(entry)
def add_obstacles(f):
 for layer in (p.F_Cu,p.B_Cu):
  court=f.GetCourtyard(layer)
  if f.GetReference().startswith('STAB'):court=f.GetCourtyard(p.F_Cu)
  if court.OutlineCount():add_shape(court,layer,'body')
 for d in f.Pads():
  if d.GetDrillSize().x:
   for layer in (p.F_Cu,p.B_Cu):add_shape(d.GetEffectiveHoleShape(),layer,'hole')
  for layer in (p.F_Cu,p.B_Cu):
   if d.IsOnLayer(layer) and d.GetNumber():add_shape(d.GetEffectiveShape(layer),layer,'smd' if d.GetAttribute()==p.PAD_ATTRIB_SMD else 'pad',d.GetNetname())
for f in fixed:add_obstacles(f)
from smilepcb.routing_style import edge_shapes
for shape in edge_shapes(board):
 for layer in (p.F_Cu,p.B_Cu):add_shape(shape,layer,'edge')
def nearby(shape,layer,padding):
 box=bbox(shape);seen=set()
 for x in range((box[0]-padding)//cell,(box[2]+padding)//cell+1):
  for y in range((box[1]-padding)//cell,(box[3]+padding)//cell+1):
   for entry in cells.get((layer,x,y),()):
    if id(entry) not in seen:seen.add(id(entry));yield entry
width,height=map(lambda x:round(x*1e6),cfg['layout']['size_mm'])
outline=(-1800000,-1800000,width+1800000,height+1800000)
def inside(shape):
 b=bbox(shape);return outline[0]<=b[0] and outline[1]<=b[1] and b[2]<=outline[2] and b[3]<=outline[3] and board_region.Contains(vector((b[0]+b[2])//2,(b[1]+b[3])//2))
def clear(objects):
 for obj in objects:
  if isinstance(obj,p.FOOTPRINT):
   for layer in (p.F_Cu,p.B_Cu):
    shape=obj.GetCourtyard(layer)
    if not shape.OutlineCount():continue
    if not inside(shape):return False
    for other,b,kind,name in nearby(shape,layer,0):
     if kind in ('body','hole','edge') and box_overlap(bbox(shape),b) and p.SHAPE.Collide(shape,other,0):return False
   conductors=list(obj.Pads())
  else:conductors=[obj]
  for item in conductors:
   for layer in (p.F_Cu,p.B_Cu):
    if not item.IsOnLayer(layer):continue
    shape=item.GetEffectiveShape(layer)
    if not inside(shape):return False
    for other,b,kind,name in nearby(shape,layer,251000):
     if kind=='via_hole' and not (isinstance(item,p.PAD) and item.GetAttribute()==p.PAD_ATTRIB_SMD):continue
     gap=251000 if kind=='hole' else 201000 if kind=='edge' else 100000 if kind=='via_hole' else 151000
     if kind=='body' or name==item.GetNetname() and kind not in ('hole','via_hole'):continue
     if box_overlap(bbox(shape),b,gap) and p.SHAPE.Collide(shape,other,gap):return False
   if isinstance(item,p.PCB_VIA):
    hole=item.GetEffectiveHoleShape()
    for layer in (p.F_Cu,p.B_Cu):
     for other,b,kind,name in nearby(hole,layer,100000):
      if kind=='smd' and p.SHAPE.Collide(hole,other,100000):return False
 return True
def move(objects,delta):
 for obj in objects:obj.Move(vector(*delta))
def pack(objects,preferred,excluded=(),angles=(0,90,180,270)):
 # Search real native geometry on cardinal orientations; no layout-specific slot.
 # The finite search is a probe, not a proof that an unsampled pose is impossible.
 current=(0,0);copper_current=(0,0);current_angle=0;trials=0;by_angle={angle:0 for angle in (0,90,180,270)}
 bodies=[o for o in objects if isinstance(o,p.FOOTPRINT)];copper=[o for o in objects if not isinstance(o,p.FOOTPRINT)]
 def area(f):
  boxes=[bbox(f.GetCourtyard(layer)) for layer in (p.F_Cu,p.B_Cu) if f.GetCourtyard(layer).OutlineCount()]
  return max(((b[2]-b[0])*(b[3]-b[1]) for b in boxes),default=0)
 bodies.sort(key=area,reverse=True)
 candidates=[(abs(x-preferred[0])+abs(y-preferred[1]),x,y) for y in range(0,height+1,500000) for x in range(0,width+1,500000)]
 candidates.sort()
 for start in range(0,len(candidates),1024):
  for angle in angles:
   if current_angle!=angle:
    move(bodies,(-current[0],-current[1]));move(copper,(-copper_current[0],-copper_current[1]));current=(0,0);copper_current=(0,0)
    for obj in objects:obj.Rotate(vector(0,0),p.EDA_ANGLE(angle-current_angle,p.DEGREES_T))
    current_angle=angle
   for _,x,y in candidates[start:start+1024]:
    if time.monotonic()>=stop:return None,{'trials':trials,'trials_by_rotation':by_angle,'failure':'placement budget exhausted'}
    if (x,y,angle) in excluded:continue
    move(bodies,(x-current[0],y-current[1]));current=(x,y);trials+=1;by_angle[angle]+=1
    if not clear(bodies):continue
    move(copper,(x-copper_current[0],y-copper_current[1]));copper_current=(x,y)
    if clear(copper):return {'origin_nm':[x,y],'rotation_deg':angle},{'trials':trials,'trials_by_rotation':by_angle}
 return None,{'trials':trials,'trials_by_rotation':by_angle,'failure':'no pose found on the 0.5 mm cardinal search'}

source=p.LoadBoard(request['core']);mcu=next(f for f in source.GetFootprints() if f.GetReference()=='U1');source_origin=mcu.GetPosition();core=[];ref_map={'U1':'U_MCU',**{'C'+str(i):'C_CORE'+str(i) for i in range(1,8)}}
mcu_part=next(part for part in cfg['parts'] if part['ref']=='U_MCU');net_map={d.GetNetname():mcu_part['nets'][d.GetNumber()] for d in mcu.Pads() if d.GetNumber()}
folder=project/'PublicController.pretty';folder.mkdir(exist_ok=True)
parts=[]
for f in source.GetFootprints():
 clone=p.Cast_to_FOOTPRINT(f.Duplicate(False));old_ref=f.GetReference();ref=ref_map[old_ref];clone.SetReference(ref);clone.SetUuid(p.KIID())
 for d in clone.Pads():
  d.SetUuid(p.KIID())
  if d.GetNumber():d.SetNet(net(net_map[d.GetNetname()]))
 clone.SetFPID(p.LIB_ID('PublicController',old_ref));clone.Reference().SetVisible(False);clone.Value().SetVisible(False)
 board.Add(clone);core.append(clone)
 canonical=p.Cast_to_FOOTPRINT(f.Duplicate(False))
 if canonical.GetLayer()==p.B_Cu:canonical.Flip(canonical.GetPosition(),p.FLIP_DIRECTION_LEFT_RIGHT)
 canonical.SetOrientationDegrees(0);canonical.SetPosition(vector(0,0));canonical.SetFPID(p.LIB_ID('PublicController',old_ref));p.PCB_IO_MGR.FindPlugin(p.PCB_IO_MGR.KICAD_SEXP).FootprintSave(str(folder),canonical)
 part=dict(mcu_part) if old_ref=='U1' else {'ref':ref,'symbol':'Device:C','value':f.GetValue(),'region':'controller','sch':[0,0],'no_connect':[],'uuid':w.identity('symbol/'+ref)}
 part.update(footprint='PublicController:'+old_ref,nets={d.GetNumber():net_map[d.GetNetname()] for d in f.Pads() if d.GetNumber()});parts.append(part)
for t in source.GetTracks():
 clone=copy_copper(t,board,net(net_map[t.GetNetname()]));core.append(clone)
move(core,(-source_origin.x,-source_origin.y))
if request.get('mirror_core'):mirror_items(core)
rotation=request.get('core_rotation_deg')
if rotation is not None and (type(rotation) is not int or rotation not in (0,90,180,270)):
 raise ValueError('Core rotation must be a cardinal angle')
pose,search=pack(core,(width//2,height//2),{tuple(v) for v in request.get('excluded_core_poses',())},
                 (rotation,) if rotation is not None else (0,90,180,270));result={'stage':'placement','status':'incomplete','layout_keys':len(cfg['layout']['keys']),'core_pose':pose,'search':search,'board_enlarged':False,'manual_layout_overrides':False}
result['core_face']='F.Cu' if request.get('mirror_core') else 'B.Cu'
if pose:
 removed={'U_MCU','C_VDD1','C_VDD2','C_IO2','C_VDDA','C_VBAT','C_BULK','C_NRST'}
 aux=[part for part in cfg['parts'] if part['region']=='controller' and part['ref'] not in removed and not part['ref'].startswith('#')]
 for f in [o for o in core if isinstance(o,p.FOOTPRINT)]:add_obstacles(f)
 # Preserve source copper as obstacles for subsequent support placement.
 for t in [o for o in core if not isinstance(o,p.FOOTPRINT)]:
  for layer in (p.F_Cu,p.B_Cu):
   if t.IsOnLayer(layer):add_shape(t.GetEffectiveShape(layer),layer,'pad',t.GetNetname())
   if isinstance(t,p.PCB_VIA):add_shape(t.GetEffectiveHoleShape(),layer,'via_hole')
 fanout_rows=request.get('fanout_rows',0)
 if type(fanout_rows) is not int or fanout_rows not in (0,1,2,3):
  raise ValueError('Fanout placement reservation requires zero to three via rows')
 if fanout_rows:
  chip=next(o for o in core if isinstance(o,p.FOOTPRINT) and o.GetReference()=='U_MCU')
  boxes=[d.GetEffectiveShape(layer).BBox() for d in chip.Pads()
         for layer in (p.F_Cu,p.B_Cu) if d.IsOnLayer(layer)]
  diameter=max(rules.m_ViasMinSize,rules.m_MinThroughDrill+2*rules.m_ViasMinAnnularWidth)
  margin=fanout_rows*diameter+(fanout_rows+1)*rules.m_MinClearance
  bounds=(min(v.GetLeft() for v in boxes)-margin,min(v.GetTop() for v in boxes)-margin,
          max(v.GetRight() for v in boxes)+margin,max(v.GetBottom() for v in boxes)+margin)
  reserve=p.SHAPE_POLY_SET();reserve.NewOutline()
  for x,y in ((bounds[0],bounds[1]),(bounds[2],bounds[1]),(bounds[2],bounds[3]),(bounds[0],bounds[3])):
   reserve.Append(x,y)
  for layer in (p.F_Cu,p.B_Cu):add_shape(reserve,layer,'body')
  result['fanout_placement_reservation']={'via_rows':fanout_rows,'margin_mm':margin/1e6,
       'bounds_mm':[v/1e6 for v in bounds],'purpose':'support-component placement only; not a copper keepout'}
 supports={};failed=None
 for part in sorted(aux,key=lambda d: d['ref']!='J_UDB'):
  if part['ref']=='J_UDB':part['side']=board.GetLayerName(next(o for o in core if isinstance(o,p.FOOTPRINT) and o.GetReference()=='U_MCU').GetLayer())
  fp=loaded(part);old=fp.GetPosition();fp.Move(vector(-old.x,-old.y))
  preferred=(pose['origin_nm'][0]+round(part['pcb'][0]*1e6),pose['origin_nm'][1]+round(part['pcb'][1]*1e6))
  if part['ref']=='J_UDB':
   chip=next(o for o in core if isinstance(o,p.FOOTPRINT) and o.GetReference()=='U_MCU')
   usb=[d.GetPosition() for d in chip.Pads() if d.GetNetname() in ('USB_DM','USB_DP')]
   mx=sum(q.x for q in usb)/len(usb);my=sum(q.y for q in usb)/len(usb);dx=mx-chip.GetPosition().x;dy=my-chip.GetPosition().y;length=math.hypot(dx,dy)
   chip_data={d.GetNetname():d.GetPosition() for d in chip.Pads() if d.GetNetname() in ('USB_DM','USB_DP')}
   connector={d.GetNetname():d.GetPosition() for d in fp.Pads() if d.GetNetname() in ('USB_DM','USB_DP')}
   ca=chip_data['USB_DP']-chip_data['USB_DM'];cb=connector['USB_DP']-connector['USB_DM'];angle=round((math.degrees(math.atan2(ca.y,ca.x))-math.degrees(math.atan2(cb.y,cb.x)))/90)*90
   fp.Rotate(vector(0,0),p.EDA_ANGLE(angle,p.DEGREES_T));connector=[d.GetPosition() for d in fp.Pads() if d.GetNetname() in ('USB_DM','USB_DP')]
   preferred=(round(mx+dx/length*10000000-sum(q.x for q in connector)/2),round(my+dy/length*10000000-sum(q.y for q in connector)/2))
  placement,diagnostic=pack([fp],preferred);supports[part['ref']]={'pose':placement,**diagnostic}
  if not placement:failed=part['ref'];break
  add_obstacles(fp)
 result.update(supports=supports,failed_support=failed)
 if not failed:
  cfg['parts']=[part for part in cfg['parts'] if part['ref'] not in removed]+parts
  # The two schematic power flags are abstract and have no PCB footprint.
  lookup={f.GetReference():f for f in board.GetFootprints()}
  for part in cfg['parts']:
   if part['ref'].startswith('#'):continue
   f=lookup[part['ref']];part.update(pcb=[f.GetPosition().x/1e6,f.GetPosition().y/1e6],side=board.GetLayerName(f.GetLayer()),rotation=f.GetOrientationDegrees())
  cfg['controller_bounds']=[0,0,width/1e6,height/1e6]
  source_xy=next(part['pcb'] for part in cfg['parts'] if part['ref']=='R_RGB')
  order,diag=rgb_chain(cfg['layout'],source_xy);cfg['layout']['rgb_order']=order;cfg['rgb_routing']=diag;by_key={k['id']:k for k in cfg['layout']['keys']};part_by_ref={part['ref']:part for part in cfg['parts']}
  for index,key_id in enumerate(order):
   ref='LED'+str(key_id);part=part_by_ref[ref];part['nets']['3']='RGB_'+str(index);part['nets']['2']='RGB_'+str(index+1) if index<len(order)-1 else 'NC_RGB_END'
   if index==len(order)-1:part['no_connect']=['2']
   else:part.pop('no_connect',None)
   for d in lookup[ref].Pads():
    if d.GetNumber():d.SetNet(net(part['nets'][d.GetNumber()]))
  cfg['qmk']['rgb_matrix']['layout']=[{'matrix':by_key[i]['matrix'],'x':round(by_key[i]['center_mm'][0]*224/cfg['layout']['size_mm'][0]),'y':round(by_key[i]['center_mm'][1]*64/cfg['layout']['size_mm'][1]),'flags':4} for i in order]
  if request.get('optimize_matrix_gpio'):
   from smilepcb.keyboard_routing.gpio import apply_matrix_gpio
   cfg,result['matrix_gpio']=apply_matrix_gpio(board,cfg)
  if request.get('require_controller_access'):
   refs={part['ref'] for part in cfg['parts'] if part['region']=='controller' and not part['ref'].startswith('#')}
   result['controller_access']=route_controller_access(board,controller_refs=refs,power_nets=cfg['plane_nets'],plane_nets=cfg['plane_nets'],usb_nets={'USB_DM','USB_DP'},deadline=min(stop,time.monotonic()+request.get('access_budget_s',55)))
   if result['controller_access']['accepted']:
    from smilepcb.keyboard_native import verify_usb_pair
    try:result['usb_lengths_mm']=verify_usb_pair(board)
    except ValueError as error:result['controller_access'].update(accepted=False,usb_rejection=str(error))
   if not result['controller_access']['accepted']:
    p.SaveBoard(str(project/'rejected-placement.kicad_pcb'),board)
    result.update(status='access-rejected',elapsed_s=time.monotonic()-started)
    print(json.dumps(result));raise SystemExit(0)
  require_via_land_clearance(board);board.BuildConnectivity();p.SaveBoard(str(project/'keyboard.kicad_pcb'),board)
  (project/'.smilepcb-keyboard.json').write_text(json.dumps(cfg,indent=2)+'\n')
  table=(project/'fp-lib-table').read_text().strip();(project/'fp-lib-table').write_text(table[:-1]+' (lib (name "PublicController") (type "KiCad") (uri "${KIPRJMOD}/PublicController.pretty") (options "") (descr "Le Chiffre core; CERN-OHL-W-2.0")))\n')
  result.update(status='placed',core_copper_objects=len(list(source.GetTracks())),model=cfg)
result['elapsed_s']=time.monotonic()-started
print(json.dumps(result))
