"""Native operations for explicitly generated full keyboard projects."""
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from smilepcb import native_worker as w
from smilepcb import routing_native
from smilepcb.keyboard_placement import controller_origin
p = w.p




def validate(board, request):
    if board.GetCopperLayerCount()!=2:
        raise ValueError('Keyboard recipe requires exactly two copper layers')
    if any(t.GetLayer() not in (p.F_Cu,p.B_Cu) for t in board.GetTracks()):
        raise ValueError('Routing object is outside the two copper layers')
    cfg = json.loads((Path(request['board']).parent/'.smilepcb-keyboard.json').read_text())
    expected = {part['ref']:part for part in cfg['parts'] if not part['ref'].startswith('#')}
    if {f.GetReference() for f in board.GetFootprints()} != set(expected):
        raise ValueError('Native footprint inventory differs from the generated model')
    for f in board.GetFootprints():
        if f.GetFPID().GetUniStringLibId() != expected[f.GetReference()]['footprint']:
            raise ValueError('Native footprint identity differs from the generated model')


def place_diodes(board, cfg):
    """Select legal local diode positions using actual courtyards, pads and holes."""
    import math
    fps = {f.GetReference():f for f in board.GetFootprints()}
    movable = {f'D{k["id"]}' for k in cfg['layout']['keys']}
    fixed = [f for ref,f in fps.items() if ref not in movable]
    report = {}
    def close(a,b,gap):
        aa,bb = a.BBox(),b.BBox()
        return (aa.GetLeft() <= bb.GetRight()+gap and aa.GetRight()+gap >= bb.GetLeft()
                and aa.GetTop() <= bb.GetBottom()+gap and aa.GetBottom()+gap >= bb.GetTop())
    for key in cfg['layout']['keys']:
        ref = 'D'+str(key['id']); fp = fps[ref]
        holes = [pad.GetEffectiveHoleShape() for other in fixed for pad in other.Pads() if pad.GetDrillSize().x]
        pads = [(pad.GetEffectiveShape(p.B_Cu),pad.GetNetname()) for other in fixed
                for pad in other.Pads() if pad.GetNumber() and pad.IsOnLayer(p.B_Cu)]
        courts = [other.GetCourtyard(p.B_Cu) for other in fixed if other.GetCourtyard(p.B_Cu).OutlineCount()]
        candidates = [(4,-9),(-4,-9),(4,9),(-4,9),(8,2),(-8,2),(8,-1),(-8,-1)]
        candidates += [(dx/2,dy/2) for dy in range(-17,18,2) for dx in range(-17,18,2)]
        candidates.sort(key=lambda xy:math.dist(xy,(4,-9)))
        selected = None
        for dx,dy in candidates:
            xy = [key['center_mm'][0]+dx,key['center_mm'][1]+dy]
            if not (-.5<=xy[0]<=cfg['layout']['size_mm'][0]+.5 and -.5<=xy[1]<=cfg['layout']['size_mm'][1]+.5): continue
            fp.SetPosition(w.point(xy))
            court = fp.GetCourtyard(p.B_Cu)
            if any(close(court,c,0) and p.SHAPE.Collide(court,c,0) for c in courts): continue
            if any(close(court,h,0) and p.SHAPE.Collide(court,h,0) for h in holes): continue
            legal = True
            for pad in fp.Pads():
                shape = pad.GetEffectiveShape(p.B_Cu)
                if any(close(shape,h,p.FromMM(.251)) and p.SHAPE.Collide(shape,h,p.FromMM(.251)) for h in holes):
                    legal = False; break
                if any(net!=pad.GetNetname() and close(shape,other,p.FromMM(.151)) and
                       p.SHAPE.Collide(shape,other,p.FromMM(.151)) for other,net in pads):
                    legal = False; break
            if legal:
                selected = xy; break
        if selected is None: raise ValueError('No legal diode placement within key envelope: '+ref)
        report[ref] = selected; fixed.append(fp)
    return report


def check_controller_envelope(board,cfg,origin):
    refs={part['ref'] for part in cfg['parts'] if part['region']=='controller'}
    bounds=[round((cfg['controller_bounds'][i]+origin[i%2])*1000000) for i in range(4)]
    measured=[]
    for fp in board.GetFootprints():
        if fp.GetReference() not in refs:continue
        courtyard=fp.GetCourtyard(fp.GetLayer())
        box=courtyard.BBox() if courtyard.OutlineCount() else fp.GetBoundingBox(False,False)
        actual=[box.GetLeft(),box.GetTop(),box.GetRight(),box.GetBottom()]
        if actual[0]<bounds[0] or actual[1]<bounds[1] or actual[2]>bounds[2] or actual[3]>bounds[3]:
            raise ValueError('Native controller envelope exceeds recipe: '+fp.GetReference())
        measured.append(actual)
    return [(min(a[i] for a in measured) if i<2 else max(a[i] for a in measured))/1000000-origin[i%2]
            for i in range(4)]


def create(request):
    project = Path(request['board']).parent
    cfg = json.loads((project/'.smilepcb-keyboard.json').read_text())
    origin = controller_origin(cfg)
    board = p.BOARD()
    nets = {}
    for component in cfg['parts']:
        for name in component['nets'].values():
            if name not in nets:
                nets[name] = p.NETINFO_ITEM(board,name); board.Add(nets[name])
    for part in cfg['parts']:
        ref = part['ref']
        if ref.startswith('#'): continue
        lib,name = part['footprint'].split(':')
        folder = project/'KeyboardParts.pretty' if lib == 'KeyboardParts' else w.SUPPORT/'footprints'/(lib+'.pretty')
        fp = p.FootprintLoad(str(folder),name)
        if fp is None: raise ValueError('Missing footprint '+part['footprint'])
        fp.SetFPID(p.LIB_ID(lib,name)); fp.SetReference(ref); fp.SetValue(part['value'])
        fp.SetAttributes(fp.GetAttributes() & ~p.FP_EXCLUDE_FROM_BOM)
        fp.SetUuid(p.KIID())
        for pad in fp.Pads():
            pad.SetUuid(p.KIID())
            if pad.GetNumber(): pad.SetNet(nets[part['nets'][pad.GetNumber()]])
        xy = [part['pcb'][j] + (origin[j] if part['region']=='controller' else 0) for j in range(2)]
        fp.SetPosition(w.point(xy)); board.Add(fp)
        if part['side'] == 'B.Cu': fp.Flip(fp.GetPosition(),p.FLIP_DIRECTION_LEFT_RIGHT)
        if part.get('rotation'): fp.Rotate(fp.GetPosition(),p.EDA_ANGLE(part['rotation'],p.DEGREES_T))
        path = p.KIID_PATH()
        for value in request['paths'][ref].split('/'):
            if value: path.push_back(p.KIID(value))
        fp.SetPath(path)
        for name,value in request.get('fields',{}).get(ref,{}).items():
            fp.SetField(name,value); fp.GetField(name).SetVisible(False)
        fp.Reference().SetVisible(False); fp.Value().SetVisible(False)
    controller_envelope=check_controller_envelope(board,cfg,origin)
    corners = cfg['outline']
    for a,b in zip(corners,corners[1:]+corners[:1]):
        edge = p.PCB_SHAPE(board); edge.SetShape(p.SHAPE_T_SEGMENT)
        edge.SetStart(w.point(a)); edge.SetEnd(w.point(b)); edge.SetLayer(p.Edge_Cuts)
        edge.SetWidth(p.FromMM(.05)); board.Add(edge)
    if cfg['layout']['rgb']:
        source = p.FootprintLoad(str(project/'KeyboardParts.pretty'),'MINI_E_CUTOUT')
        for key in cfg['layout']['keys']:
            x,y = key['center_mm']; origin_led = w.point((x,y+5.08))
            sign = -1 if key['matrix'][0]%2==0 else 1
            def transform(pos): return p.VECTOR2I(sign*pos.x+origin_led.x,sign*pos.y+origin_led.y)
            for item in source.GraphicalItems():
                if item.GetLayer()!=p.Edge_Cuts: continue
                edge = p.PCB_SHAPE(board); edge.SetShape(item.GetShape())
                if item.GetShape()==p.SHAPE_T_ARC:
                    edge.SetArcGeometry(transform(item.GetStart()),transform(item.GetArcMid()),transform(item.GetEnd()))
                else:
                    edge.SetStart(transform(item.GetStart())); edge.SetEnd(transform(item.GetEnd()))
                edge.SetLayer(p.Edge_Cuts); edge.SetWidth(item.GetWidth()); board.Add(edge)
    diode_positions = place_diodes(board,cfg)
    board.BuildConnectivity()
    if not p.SaveBoard(request['board'],board): raise ValueError('Cannot save generated board')
    result = w.snapshot(p.LoadBoard(request['board']))
    result['placement'] = {'controller_origin':origin,'controller_native_bounds_mm':controller_envelope,'outline_expanded':False,
                           'method':'whole-block empty-region search','diodes':diode_positions}
    return result


def pad_gaps(board):
    import time
    from collections import defaultdict
    from smilepcb.keyboard_routing.native_graph import copper_graph
    nets={d.GetNetname() for f in board.GetFootprints() for d in f.Pads() if d.GetNumber() and d.GetNetname()}
    graph=copper_graph(board,nets,time.monotonic()+30)
    groups=defaultdict(set)
    for i,node in enumerate(graph['nodes']):
        if isinstance(node['item'],p.PAD) and node['item'].GetNumber():
            groups[node['net']].add(graph['find'](i))
    return {net:len(roots)-1 for net,roots in groups.items() if len(roots)>1}


def verify_usb_pair(board):
    """The no-via USB recipe follows the physical controller copper face."""
    controllers=[f for f in board.GetFootprints() if f.GetReference()=='U_MCU']
    if len(controllers)!=1:raise ValueError('USB recipe requires exactly one U_MCU footprint')
    controller=controllers[0]
    layer=controller.GetLayer();lengths={}
    for net in ('USB_DP','USB_DM'):
        tracks=[t for t in board.GetTracks() if t.GetNetname()==net]
        if any(isinstance(t,p.PCB_VIA) or t.GetLayer()!=layer for t in tracks):
            raise ValueError('USB recipe contains a via or changed copper layer')
        lengths[net]=sum(t.GetLength() for t in tracks)/1e6
    if not all(0<v<20 for v in lengths.values()) or abs(lengths['USB_DP']-lengths['USB_DM'])>=.5:
        raise ValueError('USB pair length or skew exceeds the recipe envelope')
    return lengths


def run(request):
    from smilepcb.keyboard_routing.generation import generation_profile
    from smilepcb.routing_quality import using_quality_profile
    with generation_profile(request.get('generation_profile','native')),using_quality_profile(request.get('routing_quality','legacy')) as quality:
        result=_run(request)
        result['routing_quality']=quality.name
        return result


def _run(request):
    if request['operation']=='create': return create(request)
    if request['operation'] in ('key_data_access','controller_return_tree'):
        from smilepcb.keyboard_routing.modules import seed_key_data_access
        from smilepcb.keyboard_routing.power import connect_controller_return_tree
        operation=request['operation']
        maximum=70 if operation=='key_data_access' else 75
        budget=request.get('budget_s',maximum)
        if type(budget) not in (int,float) or not 0<budget<=maximum:
            raise ValueError('Unsupported '+operation+' budget')
        board=p.LoadBoard(request['board']);validate(board,request)
        cfg=json.loads((Path(request['board']).parent/'.smilepcb-keyboard.json').read_text())
        helper=seed_key_data_access if operation=='key_data_access' else connect_controller_return_tree
        result=helper(board,cfg,budget_s=budget)
        if result['accepted'] and not p.SaveBoard(request['board'],board):
            raise ValueError('Cannot save '+operation)
        return result
    if request['operation']=='controller_ports':
        import time
        from smilepcb.keyboard_routing.controller_ports import route_controller_ports
        from smilepcb.keyboard_routing.power import seed_controller_returns
        budget=request.get('budget_s',70)
        if type(budget) is not int or not 1<=budget<=70:
            raise ValueError('Controller interface budget must be 1..70 seconds')
        board=p.LoadBoard(request['board']);validate(board,request)
        cfg=json.loads((Path(request['board']).parent/'.smilepcb-keyboard.json').read_text())
        refs={part['ref'] for part in cfg['parts'] if part['region']=='controller' and not part['ref'].startswith('#')}
        before=w.snapshot(board);edge=board.GetDesignSettings().m_CopperEdgeClearance
        deadline=time.monotonic()+budget
        try:
            board.GetDesignSettings().m_CopperEdgeClearance=max(edge,250000)
            ports=route_controller_ports(board,controller_refs=refs,mcu_refs={'U_MCU'},
                plane_nets=set(cfg['plane_nets']),usb_nets={'USB_DP','USB_DM'},
                clearance_nm=max(board.GetDesignSettings().m_MinClearance,173000),deadline=deadline,
                include_auxiliary_returns=True)
            returns=seed_controller_returns(board,controller_refs=refs,plane_nets=set(cfg['plane_nets']),
                deadline=deadline,via_to_smd_clearance_nm=200000) if ports['accepted'] else None
            accepted=ports['accepted'] and returns['accepted']
            if not accepted:
                for item in list(board.GetTracks()):
                    if item.m_Uuid.AsString() not in before['tracks']:board.Delete(item)
        finally:
            board.GetDesignSettings().m_CopperEdgeClearance=edge
        if accepted and not p.SaveBoard(request['board'],board):raise ValueError('Cannot save controller interfaces')
        return {'accepted':accepted,'ports':ports,'returns':returns}
    if request['operation'] in ('complete','completion_candidates'):
        import time
        from smilepcb.keyboard_routing.completion import repair_candidate,detour_candidates
        board=p.LoadBoard(request['board']);validate(board,request)
        protected=set(request['protected_ids'])
        if request['operation']=='completion_candidates':
            return {'candidates':detour_candidates(board,protected=protected,deadline=time.monotonic()+20)}
        cfg=json.loads((Path(request['board']).parent/'.smilepcb-keyboard.json').read_text())
        result=repair_candidate(board,request['strategy'],protected=protected,plane_nets=set(cfg['plane_nets']),
                                request=request,deadline=time.monotonic()+82)
        if result['accepted'] and not p.SaveBoard(request['board'],board):raise ValueError('Cannot save accepted completion')
        return result
    if request['operation']=='local_modules':
        from smilepcb.keyboard_routing.modules import connect_key_modules
        board=p.LoadBoard(request['board']);validate(board,request)
        cfg=json.loads((Path(request['board']).parent/'.smilepcb-keyboard.json').read_text())
        result=connect_key_modules(board,cfg,via_to_smd_clearance_nm=request.get('via_to_smd_clearance_nm',0))
        if not p.SaveBoard(request['board'],board):raise ValueError('Cannot save key-module routing')
        return result
    if request['operation']=='prune_reported_tails':
        from smilepcb.keyboard_routing.cleanup import prune_reported_tails
        board=p.LoadBoard(request['board']);validate(board,request)
        result=prune_reported_tails(board,request['findings'],protected=set(request['protected_ids']))
        if not p.SaveBoard(request['board'],board):raise ValueError('Cannot save reported-tail cleanup')
        return result
    if request['operation']=='cleanup':
        from smilepcb.keyboard_routing.cleanup import cleanup_generated
        board=p.LoadBoard(request['board']);validate(board,request)
        result=cleanup_generated(board,protected=set(request['protected_ids']))
        if not p.SaveBoard(request['board'],board):raise ValueError('Cannot save tail cleanup')
        return result
    if request['operation']=='negotiate':
        import time
        from smilepcb.keyboard_routing.native_graph import negotiate_routes
        board=p.LoadBoard(request['board']);validate(board,request)
        nets=set(request['nets']);available={d.GetNetname() for f in board.GetFootprints() for d in f.Pads()}
        if not nets or not nets<=available or nets & {'USB_DM','USB_DP','GND'}:
            raise ValueError('Invalid negotiation signal selection')
        before=sum(pad_gaps(board).values())
        zones=[z.Duplicate(False) for z in board.Zones() if not z.GetIsRuleArea()]
        for zone in list(board.Zones()):
            if not zone.GetIsRuleArea():board.Delete(zone)
        factory=None
        if 'track_width_nm' in request:
            from smilepcb.keyboard_routing.local_router import LocalRouter
            factory=lambda b,**kw:LocalRouter(b,track_width_nm=request['track_width_nm'],**kw)
        result=negotiate_routes(board,nets,set(request['protected_ids']),time.monotonic()+45,
                                router_factory=factory,attempt_counts=request.get('attempt_counts',{}))
        for zone in zones:board.Add(zone)
        if zones and not p.ZONE_FILLER(board).Fill(board.Zones()):raise ValueError('Cannot refill after negotiation')
        after=sum(pad_gaps(board).values())
        result.update(accepted=after<before,pad_gaps_before=before,pad_gaps_after=after)
        if after>=before and not request.get('provisional'):
            result['attempted_connections']=result['connected'];result['connected']=0
            return result
        result['provisional']=bool(request.get('provisional'))
        if not p.SaveBoard(request['board'],board):raise ValueError('Cannot save negotiated routing')
        return result
    if request['operation']=='export_open':
        from smilepcb.keyboard_routing.intrinsic import regions
        from smilepcb.routing_policy import fixed_copper_ids
        board=p.LoadBoard(request['board']);validate(board,request)
        protected=fixed_copper_ids(board,request['protected_ids'])
        for track in board.GetTracks():track.SetLocked(track.m_Uuid.AsString() in protected)
        if not p.ExportSpecctraDSN(board,request['dsn']):raise ValueError('Cannot export routing exchange')
        holes=sorted({p.ToMM(pad.GetDrillSize().x)*1000 for f in board.GetFootprints() for pad in f.Pads()
                      if not pad.GetNumber() and pad.GetDrillSize().x})
        return {'protected_ids':sorted(protected),'editable_ids':sorted(t.m_Uuid.AsString() for t in board.GetTracks() if t.m_Uuid.AsString() not in protected),
                'holes_um':holes,'hole_clearance_um':p.ToMM(board.GetDesignSettings().m_HoleClearance)*1000,
                'intrinsic_regions':regions(board)}
    if request['operation']=='import_open':
        from smilepcb.keyboard_routing.session_import import apply_open_session
        board=p.LoadBoard(request['board']);validate(board,request)
        result=apply_open_session(board,Path(request['ses']),request['protected_ids'])
        if not p.SaveBoard(request['board'],board):raise ValueError('Cannot save router session')
        return result
    if request['operation']=='fanout':
        from collections import defaultdict
        from smilepcb.routing_style import Clearance,add_path
        board=p.LoadBoard(request['board']);validate(board,request)
        fp=next(f for f in board.GetFootprints() if f.GetReference()=='U_MCU')
        face=fp.GetLayer();opposite=p.B_Cu if face==p.F_Cu else p.F_Cu
        cfg=json.loads((Path(request['board']).parent/'.smilepcb-keyboard.json').read_text())
        plane_nets=set(cfg.get('plane_nets',()))
        external_nets=None
        if request.get('gpio_only'):
            gpio=cfg['layout']['gpio'];pins=list(gpio['rows'])+list(gpio['cols'])
            if cfg['layout'].get('rgb'):pins += [gpio[k] for k in ('rgb_data','rgb_enable','rgb_output_enable')]
            mcu=next(part for part in cfg['parts'] if part['ref']=='U_MCU')
            # The same bank geometry must reserve MCU returns as well as
            # external signals. Short straight access vias added afterwards
            # can miss a VSS pin trapped between completed signal splays.
            external_nets={mcu['nets'][cfg['mcu_gpio_pads'][pin]] for pin in pins}|plane_nets
        if 'include_nets' in request:
            selected=set(request['include_nets'])
            external_nets=selected if external_nets is None else external_nets & selected
        origin=fp.GetPosition();groups=defaultdict(list)
        for pad in fp.Pads():
            net=pad.GetNetname()
            if not net or net in ('USB_DM','USB_DP') or net.startswith('unconnected-'):continue
            pos=pad.GetPosition();dx,dy=pos.x-origin.x,pos.y-origin.y
            axis=0 if abs(dx)>abs(dy) else 1;sign=1 if (dx if axis==0 else dy)>0 else -1
            coordinates=(pos.x,pos.y);tangent=1-axis
            usb=[d for d in fp.Pads() if d.GetNetname() in ('USB_DM','USB_DP')
                 and abs((d.GetPosition().x,d.GetPosition().y)[axis]-coordinates[axis])<p.FromMM(.01)]
            partition=sum((d.GetPosition().x,d.GetPosition().y)[tangent]<coordinates[tangent] for d in usb)
            groups[axis,sign,partition].append(pad)
        oracle=Clearance(board);report=[]
        for (axis,sign,partition),pads in groups.items():
            tangent=1-axis
            coords=lambda pad:(pad.GetPosition().x,pad.GetPosition().y)
            mean=round(sum(coords(d)[tangent] for d in pads)/len(pads))
            # Expand a bank away from adjacent protected USB pads. Keeping
            # its nearest edge fixed avoids spreading GPIO leads into USB.
            usb=[d for d in fp.Pads() if d.GetNetname() in ('USB_DM','USB_DP')
                 and abs(coords(d)[axis]-coords(pads[0])[axis])<p.FromMM(.01)]
            lo=min(coords(d)[tangent] for d in pads);hi=max(coords(d)[tangent] for d in pads)
            if usb and max(coords(d)[tangent] for d in usb)<lo:mean=lo
            elif usb and min(coords(d)[tangent] for d in usb)>hi:mean=hi
            max_spread=max(abs(coords(d)[tangent]-mean) for d in pads)
            # End at the outermost completed splay. An unnecessary extra
            # millimetre here can pin an entire via bank against the edge,
            # leaving clean DRC yet no usable outward routing channel.
            normal=coords(pads[0])[axis]+sign*(p.FromMM(.9)+max_spread)
            for pad in sorted(pads,key=lambda d:coords(d)[tangent]):
                if external_nets is not None and pad.GetNetname() not in external_nets:continue
                a=coords(pad);b=list(a);b[axis]+=sign*p.FromMM(.9)
                t=mean+2*(a[tangent]-mean)
                c=list(b);c[tangent]=t;c[axis]+=sign*abs(t-a[tangent])
                d=list(c);d[axis]=normal
                path=[a,tuple(b),tuple(c),tuple(d)]
                path=[q for i,q in enumerate(path) if not i or q!=path[i-1]]
                if not all(oracle.clear(u,v,p.FromMM(.15),face,pad.GetNetname()) for u,v in zip(path,path[1:])):
                    report.append({'pin':pad.GetNumber(),'net':pad.GetNetname(),'routed':False,'path_nm':path});continue
                ids=add_path(board,oracle,path,face,pad.GetNetname(),p.FromMM(.15))
                report.append({'pin':pad.GetNumber(),'net':pad.GetNetname(),'routed':True,'ids':ids,
                               'endpoint_nm':list(d),'axis':axis,'sign':sign})
        # Give each fanned-out port access to both routing layers before
        # matrix and RGB routes occupy its escape space. The offset search
        # is relative to each bank; all pads, holes and copper stay obstacles.
        from smilepcb.keyboard_routing.local_router import LocalRouter,vec
        layer_router=LocalRouter(board,compact=True)
        for port in report:
            if not port['routed']:continue
            from smilepcb.routing_style import octile_paths
            a=tuple(port['endpoint_nm']);selected=None;chosen=None
            candidates=[]
            for normal in range(0,3000001,500000):
                for tangent in (0,-500000,500000,-1000000,1000000,-2000000,2000000):
                    end=list(a);end[port['axis']]+=port['sign']*normal;end[1-port['axis']]+=tangent
                    candidates.append((normal*normal+tangent*tangent,tuple(end)))
            for _,end in sorted(candidates):
                if not layer_router.via_clear(end,port['net']):continue
                exit_point=list(end);exit_point[port['axis']]+=port['sign']*p.FromMM(1)
                if not layer_router.clear(p.SHAPE_SEGMENT(vec(end),vec(exit_point),layer_router.track_width),opposite,port['net']):continue
                chosen=next((path for path in octile_paths(a,end) if all(
                    layer_router.clear(p.SHAPE_SEGMENT(vec(u),vec(v),layer_router.track_width),face,port['net'])
                    for u,v in zip(path,path[1:]))),None)
                if chosen is not None:selected=end;break
            if selected is None:
                port['routed']=False;port['failure']='No legal two-layer access via';continue
            mark=len(layer_router.added)
            layer_router.add_path(chosen,face,port['net'])
            layer_router.add_via(selected,port['net'])
            port['ids'].extend(layer_router.added[mark:]);port['via_id']=layer_router.added[-1]
            if port['net'] not in plane_nets:
                # Reserve this runway before selecting the next via. A
                # later via must see the earlier checked exit as copper.
                end=list(selected);end[port['axis']]+=port['sign']*p.FromMM(1)
                mark=len(layer_router.added)
                layer_router.add_path([selected,tuple(end)],opposite,port['net'])
                port['ids'].extend(layer_router.added[mark:]);port['exit_nm']=end
        # Later banks must not invalidate an earlier port's exit.
        by_id={t.m_Uuid.AsString():t for t in board.GetTracks()}
        for port in report:
            if not port.get('via_id'):continue
            if port['net'] in plane_nets:
                # Plane ports attach through real filled copper and are
                # checked by the final independent plane contact graph.
                port['access_method']='native plane fill';continue
            start=by_id[port['via_id']].GetPosition();end=[start.x,start.y]
            end[port['axis']]+=port['sign']*p.FromMM(1)
            if not layer_router.clear(p.SHAPE_SEGMENT(start,vec(end),layer_router.track_width),opposite,port['net']):
                port['routed']=False;port['failure']='A later bank blocked the outward access route'
                continue
        from smilepcb.keyboard_routing.power import seed_plane_access
        controller={part['ref'] for part in cfg['parts'] if part.get('region')=='controller'}
        plane_access=seed_plane_access(board,[pad for f in board.GetFootprints()
            if f.GetReference() in controller and f.GetReference()!='U_MCU'
            for pad in f.Pads() if pad.GetNumber() and pad.GetNetname() in plane_nets])
        if not p.SaveBoard(request['board'],board):raise ValueError('Cannot save MCU fanout')
        return {'ports':report,'plane_access':plane_access}
    if request['operation']=='components':
        import time
        from smilepcb.keyboard_routing.matrix import KeyboardRouter
        from smilepcb.keyboard_routing.native_graph import route_components
        board=p.LoadBoard(request['board']);validate(board,request)
        zones=[z.Duplicate(False) for z in board.Zones() if not z.GetIsRuleArea()]
        for zone in list(board.Zones()):
            if not zone.GetIsRuleArea():board.Delete(zone)
        nets={d.GetNetname() for f in board.GetFootprints() for d in f.Pads() if d.GetNumber() and d.GetNetname()}
        nets.discard('GND')
        interface_refs=();reserved_shapes=[]
        if request.get('controller_interfaces'):
            cfg=json.loads((Path(request['board']).parent/'.smilepcb-keyboard.json').read_text())
            interface_refs={part['ref'] for part in cfg['parts'] if part['region']=='controller'}
            mcu=next(part for part in cfg['parts'] if part['ref']=='U_MCU')
            outside={d.GetNetname() for f in board.GetFootprints() if f.GetReference() not in interface_refs for d in f.Pads()}
            nets &= set(mcu['nets'].values()) & outside
            reserved_shapes=[next(f for f in board.GetFootprints() if f.GetReference()=='U_MCU').GetCourtyard(p.F_Cu)]
        if request.get('matrix_only'):nets={n for n in nets if n.startswith(('ROW','COL'))}
        if request.get('controller_only'):
            cfg=json.loads((Path(request['board']).parent/'.smilepcb-keyboard.json').read_text())
            controller={part['ref'] for part in cfg['parts'] if part['region']=='controller'}
            outside={d.GetNetname() for f in board.GetFootprints() if f.GetReference() not in controller for d in f.Pads()}
            nets-=outside|{'USB_DP','USB_DM'}
        nets-=set(request.get('exclude_nets',()))
        if 'include_nets' in request:nets&=set(request['include_nets'])
        router=KeyboardRouter(board,deadline=time.monotonic()+45,compact=True,reserved_shapes=reserved_shapes,
                              track_width_nm=request.get('track_width_nm'))
        router.grid_step=request.get('grid_step_nm',1000000)
        router.attempt_counts=request.get('attempt_counts',{})
        router.heuristic_weight=3;router.connection_budget_s=request.get('connection_budget_s',2)
        if request.get('relax_preferences'):router.routing_penalty=None
        if request.get('completion_search'):
            router.via_cost_nm=1000000;router.layer_change_heuristic_nm=0;router.heuristic_weight=5
            router.search_budget_s=router.connection_budget_s
        count=route_components(board,router,nets,interface_refs=interface_refs)
        for zone in zones:board.Add(zone)
        if zones and not p.ZONE_FILLER(board).Fill(board.Zones()):raise ValueError('Cannot refill after signal routing')
        if not p.SaveBoard(request['board'],board):raise ValueError('Cannot save component routing')
        return {'connections':count,'added_ids':router.added,'graph':getattr(router,'graph_diagnostic',{})}
    if request['operation']=='verify':
        import math
        import time
        from collections import defaultdict
        from smilepcb.keyboard_routing.native_graph import copper_graph
        board=p.LoadBoard(request['board']);validate(board,request)
        cfg=json.loads((Path(request['board']).parent/'.smilepcb-keyboard.json').read_text())
        by_ref={f.GetReference():f for f in board.GetFootprints()}
        for part in cfg['parts']:
            if part['ref'].startswith('#'):continue
            for pad in by_ref[part['ref']].Pads():
                if pad.GetNumber() and part['nets'].get(pad.GetNumber())!=pad.GetNetname():
                    raise ValueError('Native pin graph differs at '+part['ref']+'/'+pad.GetNumber())
        for key in cfg['layout']['keys']:
            expected=w.point(key['center_mm'])
            if by_ref['SW'+str(key['id'])].GetPosition()!=expected:raise ValueError('Native switch centre differs from KLE')
            if cfg['layout']['rgb']:
                x,y=key['center_mm']
                if by_ref['LED'+str(key['id'])].GetPosition()!=w.point((x,y+5.08)):
                    raise ValueError('Native LED centre differs from its key recipe')
        from smilepcb.keyboard_routing.safety import require_via_land_clearance
        land_checks=require_via_land_clearance(board,deadline=time.monotonic()+10)
        nets={d.GetNetname() for f in board.GetFootprints() for d in f.Pads() if d.GetNumber() and d.GetNetname()}
        graph=copper_graph(board,nets,time.monotonic()+45)
        pad_groups=defaultdict(set)
        for i,node in enumerate(graph['nodes']):
            if isinstance(node['item'],p.PAD) and node['item'].GetNumber():
                pad_groups[node['net']].add(graph['find'](i))
        gaps={net:len(groups)-1 for net,groups in pad_groups.items() if len(groups)>1}
        members=defaultdict(lambda:defaultdict(set))
        for i,node in enumerate(graph['nodes']):
            item=node['item']
            if node['net'] in gaps and isinstance(item,p.PAD) and item.GetNumber():
                members[node['net']][graph['find'](i)].add((item.GetParentFootprint().GetReference(),item.GetNumber()))
        disconnected={net:[{'pad_count':len(pads),'sample_pads':sorted(pads)[:16]}
                           for pads in sorted(groups.values(),key=lambda rows:(len(rows),sorted(rows)))]
                      for net,groups in members.items()}
        usb=verify_usb_pair(board)
        from smilepcb.routing_quality import copper_metrics
        return {'copper_layers':board.GetCopperLayerCount(),'pad_groups_missing':gaps,'pad_group_gaps':sum(gaps.values()),'usb_lengths_mm':usb,
                'copper_metrics':copper_metrics(w.snapshot(board)['tracks']),
                'disconnected_pad_groups':disconnected,
                'via_land_checks':land_checks,
                'all_pin_nets_match':True,'all_key_and_led_centres_match':True}
    if request['operation']=='stitch':
        import time
        from smilepcb.keyboard_routing.power import connect_plane_returns
        board=p.LoadBoard(request['board']);validate(board,request)
        if not p.ZONE_FILLER(board).Fill(board.Zones()):raise ValueError('Cannot refill before stitching')
        cfg=json.loads((Path(request['board']).parent/'.smilepcb-keyboard.json').read_text())
        # Complete the actual pad groups, including direct pad/track joins.
        # Zone-only goals can waste the budget on unanchored islands and
        # miss the short return path between the components that need it.
        planes=cfg.get('plane_nets')
        if planes is None:planes={z.GetNetname() for z in board.Zones() if not z.GetIsRuleArea() and z.GetNetname()}
        result=connect_plane_returns(board,planes,deadline=time.monotonic()+45,
                                     grid_step_nm=request.get('grid_step_nm',500000))
        if not p.ZONE_FILLER(board).Fill(board.Zones()):raise ValueError('Cannot refill after stitching')
        if not p.SaveBoard(request['board'],board):raise ValueError('Cannot save plane stitching')
        return result
    if request['operation']=='local':
        from smilepcb.keyboard_routing.complete import connect_components
        board=p.LoadBoard(request['board']);validate(board,request)
        zones=[z.Duplicate(False) for z in board.Zones() if not z.GetIsRuleArea()]
        for zone in list(board.Zones()):
            if not zone.GetIsRuleArea():board.Delete(zone)
        result=connect_components(board,budget_s=request.get('budget_s',45),max_length_mm=request.get('max_length_mm',30),
                                  plane_nets=request.get('plane_nets',()))
        for zone in zones:board.Add(zone)
        if zones and not p.ZONE_FILLER(board).Fill(board.Zones()):raise ValueError('Cannot refill after local repair')
        if not p.SaveBoard(request['board'],board):raise ValueError('Cannot save local routing')
        return result
    if request['operation']=='seed':
        from smilepcb.routing_style import Clearance,add_path,octile_paths
        board = p.LoadBoard(request['board']); validate(board,request)
        fps = {f.GetReference():f for f in board.GetFootprints()}
        oracle = Clearance(board)
        usb = {}; width = p.FromMM(.2)
        for mcu_pin, connector_pin in [('33','3'),('32','2')]:
            a = next(d for d in fps['U_MCU'].Pads() if d.GetNumber()==mcu_pin)
            b = next(d for d in fps['J_UDB'].Pads() if d.GetNumber()==connector_pin)
            start = (a.GetPosition().x,a.GetPosition().y); stop = (b.GetPosition().x,b.GetPosition().y)
            pair_top = min(d.GetPosition().y for d in fps['U_MCU'].Pads() if d.GetNumber() in ('32','33'))
            # Stagger the bends so the 45-degree fanout widens the pair instead
            # of reducing its perpendicular clearance at the package pitch.
            lead = (start[0]+p.FromMM(1.5)-(start[1]-pair_top),start[1])
            if stop[0] <= lead[0]: raise ValueError('USB block orientation is unsupported')
            paths = [[start,*path] for path in octile_paths(lead,stop)]
            chosen = next((path for path in paths if all(oracle.clear(u,v,width,p.F_Cu,a.GetNetname())
                          for u,v in zip(path,path[1:]))),None)
            if chosen is None: raise ValueError('USB pair cannot be connected inside the controller recipe')
            ids = add_path(board,oracle,chosen,p.F_Cu,a.GetNetname(),width)
            usb[a.GetNetname()] = {'ids':ids,'points_nm':chosen}
        # Protect the back-layer return plane from unrelated routed copper.
        points = [xy for value in usb.values() for xy in value['points_nm']]
        zone = p.ZONE(board); zone.SetIsRuleArea(True); zone.SetLayer(p.B_Cu)
        zone.SetDoNotAllowTracks(True); zone.SetDoNotAllowVias(True); zone.SetDoNotAllowZoneFills(False)
        zone.SetZoneName('USB_RETURN_PATH')
        x0=min(x for x,y in points)-p.FromMM(.5); x1=max(x for x,y in points)+p.FromMM(.5)
        y0=min(y for x,y in points)-p.FromMM(.5); y1=max(y for x,y in points)+p.FromMM(.5)
        poly=zone.Outline(); poly.NewOutline()
        for x,y in ((x0,y0),(x1,y0),(x1,y1),(x0,y1)): poly.Append(x,y)
        board.Add(zone)
        # Long generated row buses must not precede controller escape routes
        # or become immutable recovery obstacles. Matrix routing creates its
        # own copper after the constrained controller connections.
        if not p.SaveBoard(request['board'],board): raise ValueError('Cannot save routed recipe copper')
        return {'usb':usb}
    if request['operation']=='planes':
        board = p.LoadBoard(request['board']); validate(board,request)
        cfg = json.loads((Path(request['board']).parent/'.smilepcb-keyboard.json').read_text())
        for layer in (p.F_Cu,p.B_Cu):
            zone=p.ZONE(board); zone.SetLayer(layer); zone.SetNetCode(board.FindNet('GND').GetNetCode())
            zone.SetLocalClearance(p.FromMM(.2)); zone.SetMinThickness(p.FromMM(.2))
            zone.SetPadConnection(p.ZONE_CONNECTION_THT_THERMAL)
            zone.SetThermalReliefGap(p.FromMM(.25)); zone.SetThermalReliefSpokeWidth(p.FromMM(.3))
            zone.SetIslandRemovalMode(p.ISLAND_REMOVAL_MODE_ALWAYS)
            poly=zone.Outline(); poly.NewOutline()
            for x,y in cfg['outline']: poly.Append(p.FromMM(x),p.FromMM(y))
            board.Add(zone)
        if not p.ZONE_FILLER(board).Fill(board.Zones()): raise ValueError('Native ground refill failed')
        if not p.SaveBoard(request['board'],board): raise ValueError('Cannot save ground fills')
        return w.snapshot(p.LoadBoard(request['board']))
    return routing_native.run(request,validator=validate)


if __name__=='__main__':
    print(json.dumps(run(json.load(sys.stdin))))
