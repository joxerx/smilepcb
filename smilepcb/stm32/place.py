"""STM32 recipe support: place. Native checks are required."""
import sys,json,time,math
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from smilepcb import native_worker as w
from smilepcb.keyboard_native import place_diodes
from smilepcb.keyboard_routing.safety import require_via_land_clearance
from smilepcb.keyboard_routing.controller_access import route_controller_access
from smilepcb.keyboard_placement import rgb_chain,support_signal_preference
from smilepcb.stm32.transfer import copy_copper,mirror_items
p=w.p
def _placement_deadlines(started,budget_s,staged):
    """Keep preparation on its original clock; late recovery uses declared time."""
    final=started+budget_s
    return (min(final,started+220) if staged else final),final


def _ordered_source_options(request):
    options={}
    for key in ('coherent_via_banks','unified_controller_plan','component_corridors'):
        requested=request.get(key,False)
        if type(requested) is not bool:raise ValueError(key+' must be boolean')
        if requested:options[key]=True
    return options


request=json.load(sys.stdin);started=time.monotonic()
from smilepcb.stm32.core_pose_native import assembly_controls,validate_raw_model
controls=assembly_controls(request)
source_options=_ordered_source_options(request)
stop,placement_stop=_placement_deadlines(started,request['budget_s'],request.get('prepare_ordered_controller',False))
from smilepcb.keyboard_routing.generation import select_generation_profile
creation=select_generation_profile(request.get('generation_profile','native'))
project=Path(request['project']);cfg=json.loads((project/'model-before-placement.json').read_text())
nets={}
_native_owners=[]
if controls['assembly_only']:
 manager=p.GetSettingsManager();pro=str(project/'keyboard.kicad_pro')
 if not manager.LoadProject(pro):raise ValueError('Cannot load actual assembly project')
 native_project=manager.GetProject(pro)
 if native_project is None:raise ValueError('Native assembly project owner is missing')
 # Use the measured saved-board lifecycle; constructing a fresh BOARD
 # before SetProject is nondeterministic in this Native toolchain.
 seed=request.get('_core_empty_seed')
 if not seed:raise ValueError('Assembly-only Native loading requires an explicit saved empty seed')
 board=p.LoadBoard(str(Path(seed).resolve()))
 if board is None or list(board.GetFootprints()) or list(board.GetTracks()):raise ValueError('Native assembly seed is not empty')
 board.SetProject(native_project)
 _native_owners=[manager,native_project,board,board.GetDesignSettings()]
 validate_raw_model(cfg)
else:board=p.BOARD()
# Candidate routing uses the same numeric constraints as the final project.
# BOARD defaults otherwise differ before the .kicad_pro has been written.
from smilepcb.keyboard_process import apply_native_rules
process_profile=request.get('process_profile')
process=apply_native_rules(board,process_profile);rules=board.GetDesignSettings()
if request.get('_core_native_rules') is not None:
 from smilepcb.stm32.core_pose_native import _rules
 if _rules(board)!=request['_core_native_rules']:raise ValueError('Assembly Native context differs from recorded caller rules')

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
aperture_owners={}
def add_outlines():
 led_owners={f.GetReference():f.m_Uuid.AsString() for f in board.GetFootprints()}
 for a,z in zip(cfg['outline'],cfg['outline'][1:]+cfg['outline'][:1]):
  edge=p.PCB_SHAPE(board);edge.SetShape(p.SHAPE_T_SEGMENT);edge.SetStart(w.point(a));edge.SetEnd(w.point(z));edge.SetLayer(p.Edge_Cuts);edge.SetWidth(50000);board.Add(edge)
 if cfg['layout']['rgb']:
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
    aperture_owners[edge.m_Uuid.AsString()]=led_owners['LED'+str(key['id'])]
add_outlines();board_region=p.SHAPE_POLY_SET()
if not board.GetBoardPolygonOutlines(board_region,False):raise ValueError('Invalid board/cutout contours')
# Preserve the existing automatic diode-placement behavior before MCU packing.
diodes=place_diodes(board,cfg)
fixed=list(board.GetFootprints());cells={};cell=10000000
def add_shape(shape,layer,kind,netname='',owner=None):
 box=bbox(shape);entry=(shape,box,kind,netname,owner)
 for x in range(box[0]//cell,box[2]//cell+1):
  for y in range(box[1]//cell,box[3]//cell+1):cells.setdefault((layer,x,y),[]).append(entry)
def silkscreen_shapes(f):
 # Ink is a placement constraint on its own face, never a copper obstacle.
 seen=set()
 for item in [*f.GraphicalItems(),*f.GetFields()]:
  uid=item.m_Uuid.AsString();layer=item.GetLayer()
  if uid in seen or layer not in (p.F_SilkS,p.B_SilkS):continue
  seen.add(uid)
  if isinstance(item,p.PCB_TEXT) and not item.IsVisible():continue
  yield item.GetEffectiveShape(layer),layer

def add_obstacles(f):
 owner=f.m_Uuid.AsString()
 for shape,layer in silkscreen_shapes(f):add_shape(shape,layer,'ink',owner=owner)
 for layer in (p.F_Cu,p.B_Cu):
  court=f.GetCourtyard(layer)
  if f.GetReference().startswith('STAB'):court=f.GetCourtyard(p.F_Cu)
  if court.OutlineCount():add_shape(court,layer,'body',owner=owner)
 for d in f.Pads():
  if d.GetDrillSize().x:
   for layer in (p.F_Cu,p.B_Cu):add_shape(d.GetEffectiveHoleShape(),layer,'hole',owner=owner)
  for layer in (p.F_Cu,p.B_Cu):
   if d.IsOnLayer(layer) and d.GetNumber():add_shape(d.GetEffectiveShape(layer),layer,'smd' if d.GetAttribute()==p.PAD_ATTRIB_SMD else 'pad',d.GetNetname(),owner=owner)
for f in fixed:add_obstacles(f)
def add_edge_obstacles():
 # Same zero-stroke native edge shapes as routing_style.edge_shapes. Only
 # emitted mounting apertures have body ownership; copper sees every edge.
 for graphic in [*board.GetDrawings(),*(g for f in board.GetFootprints() for g in f.GraphicalItems())]:
  if not isinstance(graphic,p.PCB_SHAPE) or graphic.GetLayer()!=p.Edge_Cuts:continue
  copy=graphic.Duplicate();copy.SetWidth(0);owner=aperture_owners.get(graphic.m_Uuid.AsString())
  for layer in (p.F_Cu,p.B_Cu):add_shape(copy.GetEffectiveShape(),layer,'edge',owner=('aperture',owner) if owner else None)
add_edge_obstacles()
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
def clear(objects,*,led_mounts=None):
 for obj in objects:
  owner=obj.m_Uuid.AsString() if isinstance(obj,p.FOOTPRINT) else None
  if isinstance(obj,p.FOOTPRINT):
   ink_gap=max(0,rules.m_SilkClearance)+1
   for shape,layer in silkscreen_shapes(obj):
    for other,b,kind,name,source_owner in nearby(shape,layer,ink_gap):
     if owner is not None and source_owner==owner:continue
     if kind=='ink' and box_overlap(bbox(shape),b,ink_gap) and p.SHAPE.Collide(shape,other,ink_gap):return False
   for layer in (p.F_Cu,p.B_Cu):
    shape=obj.GetCourtyard(layer)
    if not shape.OutlineCount():continue
    baseline=(led_mounts or {}).get(owner,{}).get(layer)
    bound_mount=False
    if baseline is not None:
     from smilepcb.stm32.final_led_placement import same_courtyard
     bound_mount=any(same_courtyard(shape,pose,p=p) for pose in baseline['poses'])
     if not bound_mount:raise ValueError('Final LED body differs from supported native poses')
     shape=baseline['envelope']
    own_aperture=('aperture',owner)
    entries=list(nearby(shape,layer,0));bounds=bbox(shape)
    if not inside(shape):
     within=outline[0]<=bounds[0] and outline[1]<=bounds[1] and bounds[2]<=outline[2] and bounds[3]<=outline[3]
     mounted=any(kind=='edge' and source_owner==own_aperture and p.SHAPE.Collide(shape,other,0) for other,b,kind,name,source_owner in entries)
     if not (bound_mount and within and mounted):return False
    for other,b,kind,name,source_owner in entries:
     if owner is not None and source_owner==owner:continue
     # Only the exact supported poses may retain their own aperture relation.
     # Conductors below never receive this exception.
     if bound_mount and kind=='edge' and source_owner==own_aperture:continue
     if kind in ('body','hole','edge') and box_overlap(bounds,b) and p.SHAPE.Collide(shape,other,0):return False
   conductors=list(obj.Pads())
  else:conductors=[obj]
  for item in conductors:
   for layer in (p.F_Cu,p.B_Cu):
    if not item.IsOnLayer(layer):continue
    shape=item.GetEffectiveShape(layer)
    if not inside(shape):return False
    for other,b,kind,name,source_owner in nearby(shape,layer,251000):
     if owner is not None and source_owner==owner:continue
     if kind=='via_hole' and not (isinstance(item,p.PAD) and item.GetAttribute()==p.PAD_ATTRIB_SMD):continue
     gap=251000 if kind=='hole' else 201000 if kind=='edge' else 100000 if kind=='via_hole' else 151000
     if kind=='body' or name==item.GetNetname() and kind not in ('hole','via_hole'):continue
     if box_overlap(bbox(shape),b,gap) and p.SHAPE.Collide(shape,other,gap):return False
   if isinstance(item,p.PCB_VIA):
    hole=item.GetEffectiveHoleShape()
    for layer in (p.F_Cu,p.B_Cu):
     for other,b,kind,name,source_owner in nearby(hole,layer,100000):
      if kind=='smd' and p.SHAPE.Collide(hole,other,100000):return False
 return True
def rebuild_obstacles():
 # Recreate current native geometry and net identities after final LED poses.
 # The fanout reservation was support-placement guidance, not physical copper.
 cells.clear()
 for f in board.GetFootprints():add_obstacles(f)
 add_edge_obstacles()
 for item in board.GetTracks():
  for layer in (p.F_Cu,p.B_Cu):
   if item.IsOnLayer(layer):add_shape(item.GetEffectiveShape(layer),layer,'pad',item.GetNetname())
   if isinstance(item,p.PCB_VIA):add_shape(item.GetEffectiveHoleShape(),layer,'via_hole')

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

source=p.LoadBoard(request['core']);module_import=None;import_identity_map=[]
mcu_part=next(part for part in cfg['parts'] if part['ref']=='U_MCU')
if request.get('core_module') is not None:
 import hashlib
 from smilepcb.stm32.core_module_import import core_import_spec,regenerated_identity_record
 inventory=[{'ref':f.GetReference(),'uuid':f.m_Uuid.AsString(),'value':f.GetValue(),
             'pads':[{'number':d.GetNumber(),'uuid':d.m_Uuid.AsString(),'net':d.GetNetname()} for d in f.Pads()]} for f in source.GetFootprints()]
 module_import=core_import_spec(cfg,request['core_module'],inventory,source_sha256=hashlib.sha256(Path(request['core']).read_bytes()).hexdigest(),copper_nets=[t.GetNetname() for t in source.GetTracks()])
 ref_map=module_import['ref_map'];net_map=module_import['net_map'];mcu=next(f for f in source.GetFootprints() if f.GetReference()==module_import['source_mcu_ref'])
else:
 mcu=next(f for f in source.GetFootprints() if f.GetReference()=='U1');ref_map={'U1':'U_MCU',**{'C'+str(i):'C_CORE'+str(i) for i in range(1,8)}}
 net_map={d.GetNetname():mcu_part['nets'][d.GetNumber()] for d in mcu.Pads() if d.GetNumber()}
# Observe the real empty/pre-import board, before any compiled copy exists.
from smilepcb.keyboard_routing.generated_provenance import inventory as copper_inventory
import tempfile
with tempfile.TemporaryDirectory(prefix='.smilepcb-import-',dir=project) as import_folder:
 import_board=Path(import_folder)/'before.kicad_pcb'
 if not p.SaveBoard(str(import_board),board):raise ValueError('Cannot capture pre-import board')
 before_core_import=copper_inventory(import_board)
source_origin=mcu.GetPosition();core=[];copied_supply_ids=set()
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
 if module_import is not None:
  part=dict(module_import['models'][old_ref])
  import_identity_map.append(regenerated_identity_record(module_import['source_inventory'][old_ref],ref,clone.m_Uuid.AsString(),{d.GetNumber():d.m_Uuid.AsString() for d in clone.Pads()}))
 else:part=dict(mcu_part) if old_ref=='U1' else {'ref':ref,'symbol':'Device:C','value':f.GetValue(),'region':'controller','sch':[0,0],'no_connect':[],'uuid':w.identity('symbol/'+ref)}
 part.update(footprint='PublicController:'+old_ref,nets={d.GetNumber():net_map[d.GetNetname()] for d in f.Pads() if d.GetNumber()});parts.append(part)
for t in source.GetTracks():
 clone=copy_copper(t,board,net(net_map[t.GetNetname()]));core.append(clone)
 if not clone.IsLocked() and (clone.GetNetname() in cfg['plane_nets'] or clone.GetNetname().startswith('+')):
  copied_supply_ids.add(clone.m_Uuid.AsString())
move(core,(-source_origin.x,-source_origin.y))
if request.get('mirror_core'):mirror_items(core)
rotation=request.get('core_rotation_deg')
if rotation is not None and (type(rotation) is not int or rotation not in (0,90,180,270)):
 raise ValueError('Core rotation must be a cardinal angle')
if controls['exact_pose'] is not None:
 exact=controls['exact_pose']
 for obj in core:obj.Rotate(vector(0,0),p.EDA_ANGLE(exact['rotation_deg'],p.DEGREES_T))
 move(core,exact['origin_nm']);exact_legal=clear(core)
 pose=exact if exact_legal or controls['assembly_only'] else None
 search={'trials':1,'exact_pose':True,'exact_pose_legal':exact_legal}
elif request.get('component_corridors',False):
 # The approved corridor recipe chooses usable space before auxiliary packing,
 # rather than fixing the first mechanically legal central pose forever.
 from smilepcb.stm32.core_pose_native import balance_packed_core,_available_halo_radius
 baseline={'origin_nm':[width//2,height//2],'rotation_deg':0}
 move(core,baseline['origin_nm'])
 pose,search=balance_packed_core(board,cfg,core,baseline,deadline=min(stop-40,time.monotonic()+65),legal=clear,excluded=request.get('excluded_core_poses',()),allowed_rotations=(rotation,) if rotation is not None else (0,90,180,270))
 if pose is not None:
  request['_core_bank_halo']=True
  request['_core_envelope_radius_nm']=_available_halo_radius({'diagnostics':search['selected_metrics']})
else:
 pose,search=pack(core,(width//2,height//2),{tuple(v) for v in request.get('excluded_core_poses',())},(rotation,) if rotation is not None else (0,90,180,270))
result={'stage':'placement','generation_profile':creation.name,'process_profile':process_profile,'process_rules':process,'status':'incomplete','layout_keys':len(cfg['layout']['keys']),'core_pose':pose,'search':search,'board_enlarged':False,'manual_layout_overrides':False}
result['core_face']='F.Cu' if request.get('mirror_core') else 'B.Cu'
if module_import is not None:
 result['core_module_import']={'source_sha256':module_import['source_sha256'],'descriptor_sha256':module_import['descriptor_sha256'],
     'identity_map':import_identity_map,'replace_refs':sorted(module_import['replace_refs']),'compiler_evidence':module_import['compiler_evidence']}
if pose:
 removed=module_import['replace_refs'] if module_import is not None else {'U_MCU','C_VDD1','C_VDD2','C_IO2','C_VDDA','C_VBAT','C_BULK','C_NRST'}
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
 if request.get('_core_bank_halo',False):
  from smilepcb.stm32.core_pose_native import _bank_access_halos,_core_envelope_halos,_courtyard_boxes
  chip=next(o for o in core if isinstance(o,p.FOOTPRINT) and o.GetReference()=='U_MCU');centre=chip.GetPosition();bank_pads=[]
  gap=max(rules.m_MinClearance,rules.m_NetSettings.GetDefaultNetclass().GetClearance(),creation.foreign_copper_clearance_nm)
  diameter=max(rules.m_ViasMinSize,rules.m_MinThroughDrill+2*rules.m_ViasMinAnnularWidth)
  depth=diameter+2*gap+rules.m_TrackMinWidth;half=(rules.m_TrackMinWidth+1)//2
  for pad in chip.Pads():
   if not pad.GetNumber():continue
   delta=pad.GetPosition()-centre;axis=0 if abs(delta.x)>abs(delta.y) else 1;sign=1 if (delta.x,delta.y)[axis]>0 else -1
   heading=(sign,0) if axis==0 else (0,sign)
   for layer in (p.F_Cu,p.B_Cu):
    if pad.IsOnLayer(layer):bank_pads.append({'heading':heading,'box':bbox(pad.GetEffectiveShape(layer))})
  halos=_bank_access_halos(bank_pads,depth_nm=depth,margin_nm=half+gap)
  for halo in halos:
   left,top,right,bottom=halo['box'];reserve=p.SHAPE_POLY_SET();reserve.NewOutline()
   for x,y in ((left,top),(right,top),(right,bottom),(left,bottom)):reserve.Append(x,y)
   for layer in (p.F_Cu,p.B_Cu):add_shape(reserve,layer,'body')
  core_features=[]
  for obj in core:
   if isinstance(obj,p.FOOTPRINT):
    for layer in (p.F_Cu,p.B_Cu):
     for box in _courtyard_boxes(obj.GetCourtyard(layer)):core_features.append({'box':box,'layer':layer})
     for pad in obj.Pads():
      if pad.IsOnLayer(layer):core_features.append({'box':bbox(pad.GetEffectiveShape(layer)),'layer':layer})
   else:
    for layer in (p.F_Cu,p.B_Cu):
     if obj.IsOnLayer(layer):core_features.append({'box':bbox(obj.GetEffectiveShape(layer)),'layer':layer})
  envelope_radius=request.get('_core_envelope_radius_nm',depth)
  envelopes=_core_envelope_halos(core_features,depth_nm=envelope_radius)
  for halo in envelopes:
   left,top,right,bottom=halo['box'];reserve=p.SHAPE_POLY_SET();reserve.NewOutline()
   for x,y in ((left,top),(right,top),(right,bottom),(left,bottom)):reserve.Append(x,y)
   add_shape(reserve,halo['layer'],'body')
  result['bank_access_halo']={'strips':halos,'core_envelopes':envelopes,'bank_access_depth_nm':depth,'requested_radius_nm':request.get('_core_envelope_radius_nm'),'envelope_radius_nm':envelope_radius,'purpose':'bounded available-space auxiliary placement guidance; not copper'}
 elif fanout_rows:
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
 from smilepcb.stm32.core_bounds import core_exit_bounds_nm
 fanout_frame=tuple(round(v*1e6) for v in result['fanout_placement_reservation']['bounds_mm']) if 'fanout_placement_reservation' in result else None
 core_exit_frame_nm=core_exit_bounds_nm(core,footprint_type=p.FOOTPRINT,fanout_bounds_nm=fanout_frame)
 result['core_exit_bounds_nm']=list(core_exit_frame_nm)
 supports={};failed=None
 for part in sorted(aux,key=lambda d: d['ref']!='J_UDB'):
  if part['ref']=='J_UDB' and controls['fixed_pose'] is not None:part['side']=controls['fixed_pose']['side']
  elif part['ref']=='J_UDB':part['side']=board.GetLayerName(next(o for o in core if isinstance(o,p.FOOTPRINT) and o.GetReference()=='U_MCU').GetLayer())
  fp=loaded(part);old=fp.GetPosition();fp.Move(vector(-old.x,-old.y))
  preferred=(pose['origin_nm'][0]+round(part['pcb'][0]*1e6),pose['origin_nm'][1]+round(part['pcb'][1]*1e6))
  placed_refs={item['ref'] for item in parts}|set(supports)
  own_pads=[(d.GetNetname(),(d.GetPosition().x,d.GetPosition().y)) for d in fp.Pads() if d.GetNumber()]
  contacts=[(d.GetNetname(),(d.GetPosition().x,d.GetPosition().y))
            for neighbour in board.GetFootprints() if neighbour.GetReference() in placed_refs
            for d in neighbour.Pads() if d.GetNumber()]
  preferred,anchor_nets=support_signal_preference(own_pads,contacts,set(cfg['plane_nets']),preferred)
  if part['ref']=='J_UDB' and controls['fixed_pose'] is None:
   chip=next(o for o in core if isinstance(o,p.FOOTPRINT) and o.GetReference()=='U_MCU')
   usb=[d.GetPosition() for d in chip.Pads() if d.GetNetname() in ('USB_DM','USB_DP')]
   mx=sum(q.x for q in usb)/len(usb);my=sum(q.y for q in usb)/len(usb);dx=mx-chip.GetPosition().x;dy=my-chip.GetPosition().y;length=math.hypot(dx,dy)
   chip_data={d.GetNetname():d.GetPosition() for d in chip.Pads() if d.GetNetname() in ('USB_DM','USB_DP')}
   connector={d.GetNetname():d.GetPosition() for d in fp.Pads() if d.GetNetname() in ('USB_DM','USB_DP')}
   ca=chip_data['USB_DP']-chip_data['USB_DM'];cb=connector['USB_DP']-connector['USB_DM'];angle=round((math.degrees(math.atan2(ca.y,ca.x))-math.degrees(math.atan2(cb.y,cb.x)))/90)*90
   fp.Rotate(vector(0,0),p.EDA_ANGLE(angle,p.DEGREES_T));connector=[d.GetPosition() for d in fp.Pads() if d.GetNetname() in ('USB_DM','USB_DP')]
   preferred=(round(mx+dx/length*10000000-sum(q.x for q in connector)/2),round(my+dy/length*10000000-sum(q.y for q in connector)/2))
  if part['ref']=='J_UDB' and controls['fixed_pose'] is not None:
   fixed=controls['fixed_pose'];fp.SetOrientationDegrees(fixed['rotation_deg']);fp.SetPosition(vector(*fixed['origin_nm']))
   placement={'origin_nm':fixed['origin_nm'],'rotation_deg':fixed['rotation_deg']} if clear([fp]) else None
   preferred=tuple(fixed['origin_nm']);diagnostic={'trials':1,'fixed_pose':True}
  else:placement,diagnostic=pack([fp],preferred)
  supports[part['ref']]={'pose':placement,'preferred_nm':list(preferred),'signal_anchor_nets':anchor_nets,**diagnostic}
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
  if cfg['layout']['rgb']:
   source_xy=next(part['pcb'] for part in cfg['parts'] if part['ref']=='R_RGB')
   order,diag=rgb_chain(cfg['layout'],source_xy);cfg['layout']['rgb_order']=order;cfg['rgb_routing']=diag;by_key={k['id']:k for k in cfg['layout']['keys']};part_by_ref={part['ref']:part for part in cfg['parts']}
   for index,key_id in enumerate(order):
    ref='LED'+str(key_id);part=part_by_ref[ref];part['nets']['3']='RGB_'+str(index);part['nets']['2']='RGB_'+str(index+1) if index<len(order)-1 else 'NC_RGB_END'
    if index==len(order)-1:part['no_connect']=['2']
    else:part.pop('no_connect',None)
    for d in lookup[ref].Pads():
     if d.GetNumber():d.SetNet(net(part['nets'][d.GetNumber()]))
   cfg['qmk']['rgb_matrix']['layout']=[{'matrix':by_key[i]['matrix'],'x':round(by_key[i]['center_mm'][0]*224/cfg['layout']['size_mm'][0]),'y':round(by_key[i]['center_mm'][1]*64/cfg['layout']['size_mm'][1]),'flags':4} for i in order]
   from smilepcb.stm32.final_led_placement import orient_final_led_chain,capture_led_mounts,native_data_offsets
   led_mounts=capture_led_mounts(lookup,order,p=p)
   leds=orient_final_led_chain(board,cfg,lookup,p=p,deadline=stop,data_offsets=native_data_offsets(project,p=p))
   rebuild_obstacles()
   if not clear(leds,led_mounts=led_mounts):
    from smilepcb.keyboard_routing.interface_contract import MARKER
    (project/MARKER).unlink(missing_ok=True)
    if not p.SaveBoard(str(project/'rejected-placement.kicad_pcb'),board):raise ValueError('Cannot retain rejected LED placement')
    result.update(status='access-rejected',rejection_reason='final-led-placement-clearance',elapsed_s=time.monotonic()-started)
    if not controls['assembly_only']:print(json.dumps(result));raise SystemExit(0)
   if time.monotonic()>=stop:raise TimeoutError('Final LED placement budget exhausted')
  # Capture the actual imported baseline immediately before controlled creation.
  from smilepcb.keyboard_routing.generated_provenance import inventory,record_creation
  import tempfile
  with tempfile.TemporaryDirectory(prefix='.smilepcb-placement-',dir=project) as folder:
   baseline=Path(folder)/'baseline.kicad_pcb'
   if not p.SaveBoard(str(baseline),board):raise ValueError('Cannot capture placement copper baseline')
   imported_copper=inventory(baseline)
  if controls['assembly_only']:
   result.update(status='assembled',model=cfg,core_object_ids=[q.m_Uuid.AsString() for q in core],imported_copper=imported_copper,
       assembly_checks={'exact_core_legal':search.get('exact_pose_legal',True),'all_auxiliary':not failed,'final_leds':result.get('rejection_reason')!='final-led-placement-clearance'})
   if not p.SaveBoard(str(project/'assembly.kicad_pcb'),board):raise ValueError('Cannot save isolated complete assembly')
   (project/'assembly.model.json').write_text(json.dumps(cfg,indent=2)+'\n')
   (project/'assembly.result.json').write_text(json.dumps(result,indent=2)+'\n')
   print(json.dumps(result));raise SystemExit(0)
  recovery_ids=None
  if request.get('component_corridors',False) and module_import is not None:
   # Publish the exact fresh Native import delta before any supply recovery.
   from smilepcb.keyboard_routing.generated_provenance import record_import_creation
   recovery_ids=copied_supply_ids
   if not p.SaveBoard(str(project/'keyboard.kicad_pcb'),board):raise ValueError('Cannot save fresh import boundary')
   record_import_creation(project/'keyboard.kicad_pcb',before_core_import,creator='compiled-controller',
       immutable_ids=set(imported_copper)-set(before_core_import)-recovery_ids,generated_ids=recovery_ids)
  if request.get('prepare_ordered_controller'):
   from smilepcb.keyboard_routing.ordered_controller import prepare_ordered_controller
   from smilepcb.keyboard_routing.interface_contract import MARKER
   def rejected_matrix(candidate,model,index,trial):
    folder=project.parent/'rejected-matrix';folder.mkdir(exist_ok=True)
    name='trial-'+str(index)
    if not p.SaveBoard(str(folder/(name+'.kicad_pcb')),candidate):raise ValueError('Cannot retain rejected matrix diagnostic')
    (folder/(name+'.json')).write_text(json.dumps(model,indent=2)+'\n')
    trial['diagnostic_board']='rejected-matrix/'+name+'.kicad_pcb'
    (folder/(name+'.report.json')).write_text(json.dumps(trial,indent=2)+'\n')
   from smilepcb.routing_quality import using_quality_profile
   with using_quality_profile(request.get('routing_quality','legacy')):
    prepared_usb=None
    if 'J_UDB' in lookup:
     from smilepcb.stm32.usb_placement import select_usb_placement
     # Final supports/LEDs and imported copper are now immutable obstacles.
     rebuild_obstacles()
     selection=select_usb_placement(board,lookup['J_UDB'],cfg,preferred_nm=supports['J_UDB']['preferred_nm'],legal=lambda f:clear([f]),deadline=min(stop-26,time.monotonic()+55),max_length_nm=request.get('usb_max_length_nm',...))
     result['usb_placement']=selection
     if selection['accepted']:
      prepared_usb=selection['usb']
      supports['J_UDB']['initial_pack_pose']=supports['J_UDB']['pose']
      supports['J_UDB']['pose']=selection['selected_pose']
      # Retain actual native proof even if a later matrix stage rejects.
      if not p.SaveBoard(str(project/'usb-placement-accepted.kicad_pcb'),board):raise ValueError('Cannot retain accepted USB checkpoint')
      (project/'usb-placement-accepted.model.json').write_text(json.dumps(cfg,indent=2)+'\n')
     else:
      (project/MARKER).unlink(missing_ok=True)
      p.SaveBoard(str(project/'rejected-placement.kicad_pcb'),board)
      result.update(status='access-rejected',rejection_reason='usb-placement',elapsed_s=time.monotonic()-started)
      print(json.dumps(result));raise SystemExit(0)
    cfg,result['ordered_controller']=prepare_ordered_controller(board,cfg,deadline=stop-1,recovery_deadline=placement_stop-1,complete_matrix=request.get('complete_collector_matrix',False),on_rejection=rejected_matrix if request.get('complete_collector_matrix') else None,exit_bounds_nm=core_exit_frame_nm,prepared_usb=prepared_usb,usb_max_length_nm=request.get('usb_max_length_nm',...),**({'module_power_ids':recovery_ids} if recovery_ids is not None else {}),**source_options)
   if not result['ordered_controller']['accepted']:
    (project/MARKER).unlink(missing_ok=True)
    p.SaveBoard(str(project/'rejected-placement.kicad_pcb'),board)
    result.update(status='access-rejected',elapsed_s=time.monotonic()-started)
    print(json.dumps(result));raise SystemExit(0)
   (project/MARKER).write_text(json.dumps(result['ordered_controller']['interface'],indent=2)+'\n')
   if request.get('complete_collector_matrix'):
    from smilepcb.keyboard_routing.field_contract import MARKER as FIELD_MARKER
    (project/FIELD_MARKER).write_text(json.dumps(result['ordered_controller']['matrix_contract'],indent=2)+'\n')
  elif request.get('optimize_matrix_gpio'):
   from smilepcb.keyboard_routing.gpio import apply_matrix_gpio
   cfg,result['matrix_gpio']=apply_matrix_gpio(board,cfg)
  if request.get('require_controller_access'):
   refs={part['ref'] for part in cfg['parts'] if part['region']=='controller' and not part['ref'].startswith('#')}
   result['controller_access']=route_controller_access(board,controller_refs=refs,power_nets=cfg['plane_nets'],plane_nets=cfg['plane_nets'],usb_nets={'USB_DM','USB_DP'},deadline=min(stop,time.monotonic()+request.get('access_budget_s',55)))
   if result['controller_access']['accepted']:
    from smilepcb.keyboard_native import verify_usb_pair
    try:result['usb_lengths_mm']=verify_usb_pair(board,max_length_nm=request.get('usb_max_length_nm',...))
    except ValueError as error:result['controller_access'].update(accepted=False,usb_rejection=str(error))
   if not result['controller_access']['accepted']:
    p.SaveBoard(str(project/'rejected-placement.kicad_pcb'),board)
    result.update(status='access-rejected',elapsed_s=time.monotonic()-started)
    print(json.dumps(result));raise SystemExit(0)
  from smilepcb.usb_policy import resolve_usb_max_length_nm
  cfg.setdefault('routing_preparation',{})['usb_max_length_nm']=resolve_usb_max_length_nm(request.get('usb_max_length_nm',...))
  require_via_land_clearance(board);board.BuildConnectivity()
  if not p.SaveBoard(str(project/'keyboard.kicad_pcb'),board):raise ValueError('Cannot save accepted placement')
  record_creation(project/'keyboard.kicad_pcb',imported_copper,creator='accepted-placement',imported_ids=set(imported_copper)-(recovery_ids or set()))
  (project/'.smilepcb-keyboard.json').write_text(json.dumps(cfg,indent=2)+'\n')
  table=(project/'fp-lib-table').read_text().strip();(project/'fp-lib-table').write_text(table[:-1]+' (lib (name "PublicController") (type "KiCad") (uri "${KIPRJMOD}/PublicController.pretty") (options "") (descr "Le Chiffre core; CERN-OHL-W-2.0")))\n')
  result.update(status='placed',core_copper_objects=len(list(source.GetTracks())),model=cfg)
if controls['assembly_only']:
 result.update(model=cfg,core_object_ids=[q.m_Uuid.AsString() for q in core],assembly_checks={'exact_core_legal':search.get('exact_pose_legal',False),'all_auxiliary':False,'final_leds':False})
 p.SaveBoard(str(project/'assembly.kicad_pcb'),board)
 (project/'assembly.model.json').write_text(json.dumps(cfg,indent=2)+'\n')
 (project/'assembly.result.json').write_text(json.dumps(result,indent=2)+'\n')
result['elapsed_s']=time.monotonic()-started
print(json.dumps(result))
