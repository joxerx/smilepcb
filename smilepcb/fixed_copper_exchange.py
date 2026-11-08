"""Exact fixed-copper occupancy for the temporary DSN/SES exchange.

Freerouting's trace/drill tiles conservatively miter round ends. Represent
retained copper as enclosing polygons; keep a smaller, entirely contained
through-layer connector for each fixed via. Native geometry is never edited.
The connector's original circular padstack is restored before native SES import.
Selectable and editable vias retain their real manufacturing dimensions.
"""
from copy import deepcopy
import math
import sexpdata as sx


def _read(text):
    return sx.loads(text.replace('(string_quote ")','(string_quote __SMILEPCB_QUOTE__)'),nil=None,true=None,false=None)


def _write(tree):
    return sx.dumps(tree).replace('(string_quote __SMILEPCB_QUOTE__)','(string_quote ")')


def _child(node,tag):
    return next((v for v in node if isinstance(v,list) and str(v[0])==tag),None)


def _capsule(a,b,radius):
    values=[*a,*b,radius]
    if not all(isinstance(v,(int,float)) and math.isfinite(v) for v in values) or radius<=0:
        raise ValueError('Invalid fixed copper geometry')
    angle=math.atan2(b[1]-a[1],b[0]-a[0]);points=[]
    # Tangent enclosure plus a rounding guard, on the 0.01 um exchange grid.
    outer=(radius+.02)/math.cos(math.pi/64)
    for center,start in ((b,angle-math.pi/2),(a,angle+math.pi/2)):
        for i in range(33):
            phi=start+i*math.pi/32
            points.extend((round(center[0]+outer*math.cos(phi),2),round(center[1]+outer*math.sin(phi),2)))
    return points


def fixed_copper_exchange(text,*,indices=None):
    tree=_read(text)
    if _child(tree,'unit')!=[sx.Symbol('unit'),sx.Symbol('um')]:
        raise ValueError('Fixed copper exchange requires micron units')
    wiring=_child(tree,'wiring');library=_child(tree,'library');structure=_child(tree,'structure')
    if wiring is None or library is None or structure is None:raise ValueError('Incomplete DSN')
    definitions={str(n[1]):n for n in library[1:] if str(n[0])=='padstack'}
    chosen=set(range(1,len(wiring))) if indices is None else set(indices)
    if any(type(i) is not int or not 1<=i<len(wiring) for i in chosen):raise ValueError('Unknown fixed copper index')
    selected=_child(structure,'via');shared={str(v) for v in selected[1:]} if selected else set()
    fixed=lambda row: _child(row,'type') in ([sx.Symbol('type'),sx.Symbol('fix')],[sx.Symbol('type'),sx.Symbol('protect')])
    shared.update(str(v[1]) for i,v in enumerate(wiring[1:],1) if str(v[0])=='via' and (not fixed(v) or i not in chosen))
    manifest={'fixed_segments':0,'fixed_vias':0,'via_padstacks':{},'fixed_via_instances':[], 'enclosure_guard_um':.02}
    output=[wiring[0]];aliases={}
    for index,row in enumerate(wiring[1:],1):
        if index not in chosen:output.append(row);continue
        path=_child(row,'path') if str(row[0])=='wire' and fixed(row) else None
        if path is not None:
            if len(path)<7 or len(path[3:])%2:raise ValueError('Invalid fixed path')
            pts=list(zip(path[3::2],path[4::2]))
            for a,b in zip(pts,pts[1:]):
                output.append([row[0],[sx.Symbol('polygon'),path[1],0,*_capsule(a,b,path[2]/2)],
                               *[deepcopy(v) for v in row[1:] if v is not path]])
                manifest['fixed_segments']+=1
            # ConductionArea.getNormalContacts joins traces and drill centres,
            # not adjacent conduction areas. Keep an entirely contained spine
            # so neighbouring capsules retain the original electrical graph.
            spine=deepcopy(row);_child(spine,'path')[2]=min(1,path[2]/2);output.append(spine)
        elif str(row[0])=='via' and fixed(row):
            original=str(row[1]);definition=definitions[original]
            shapes=[v for v in definition[2:] if isinstance(v,list) and str(v[0])=='shape']
            if len(shapes)!=2 or {str(v[1][1]) for v in shapes}!={'F.Cu','B.Cu'} or any(str(v[1][0])!='circle' or len(v[1])!=3 for v in shapes):
                raise ValueError('Unsupported fixed via profile')
            if original not in aliases:
                name=original
                if original in shared:
                    name='SmilePCBExactVia'+str(len(aliases))
                    if name in definitions:raise ValueError('Fixed-via alias already exists')
                    definition=deepcopy(definition);definition[1]=name;library.append(definition)
                manifest['via_padstacks'][name]=deepcopy(definition)
                for shape in definition[2:]:
                    if isinstance(shape,list) and str(shape[0])=='shape':shape[1][2]/=2
                aliases[original]=name
            net=_child(row,'net')
            if net is None:raise ValueError('Fixed via has no net')
            # shapes came from the original definition; read immutable originals
            # after the first instance so repeated vias keep the full annulus.
            original_shapes=[v for v in manifest['via_padstacks'][aliases[original]][2:] if isinstance(v,list) and str(v[0])=='shape']
            for shape in original_shapes:
                output.append([sx.Symbol('wire'),[sx.Symbol('polygon'),shape[1][1],0,*_capsule(row[2:4],row[2:4],shape[1][2]/2)],deepcopy(net),[sx.Symbol('type'),sx.Symbol('fix')]])
            clone=deepcopy(row);clone[1]=aliases[original];output.append(clone);manifest['fixed_vias']+=1
            manifest['fixed_via_instances'].append([aliases[original],row[2],row[3],str(net[1])])
        else:output.append(row)
    wiring[:]=output
    # JSON-safe padstack declarations preserve atom/string distinction on reload.
    manifest['via_padstacks']={k:sx.dumps(v) for k,v in manifest['via_padstacks'].items()}
    return _write(tree),manifest


def diagnosed_fixed_objects(text,report):
    """Select only reported fixed conductors using the pinned DRC position grid.

    Freerouting 2.5.0 prints positions to 0.01 mm. Match bounding centres within
    the 7.1 um diagonal rounding error plus the native DSN rounding guard.
    Net names are read from diagnostics, never hardcoded by circuit role.
    """
    if report.get('coordinateUnits')!='mm':raise ValueError('Unsupported report units')
    wiring=_child(_read(text),'wiring');selected=set()
    for violation in report['violations']:
        if violation['severity']!='error':continue
        matched=False
        for item in violation['items']:
            description=item['description'];kind=description.split(' ')[0]
            if kind not in ('Trace','Via'):continue
            if '[' not in description or not description.endswith(']'):raise ValueError('Unrecognized conductor diagnostic')
            name=description[description.index('[')+1:-1]
            target=(item['pos']['x']*1000,item['pos']['y']*1000);found=[]
            for i,row in enumerate(wiring[1:],1):
                net=_child(row,'net')
                if net is None or str(net[1])!=name:continue
                if _child(row,'type') not in ([sx.Symbol('type'),sx.Symbol('fix')],[sx.Symbol('type'),sx.Symbol('protect')]):continue
                if kind=='Via' and str(row[0])=='via':center=row[2:4]
                elif kind=='Trace' and str(row[0])=='wire':
                    path=_child(row,'path')
                    if path is None:continue
                    points=list(zip(path[3::2],path[4::2]))
                    center=[(min(p[j] for p in points)+max(p[j] for p in points))/2 for j in (0,1)]
                else:continue
                if math.dist(center,target)<8:found.append(i)
            if not found:raise ValueError('Cannot match reported fixed conductor to DSN geometry')
            selected.update(found);matched=True
        if not matched:raise ValueError('Reported conflict has no supported fixed conductor')
    return selected


def restore_fixed_vias(text,manifest):
    tree=_read(text);routes=_child(tree,'routes');library=_child(routes,'library_out') if routes else None
    if library is None:raise ValueError('Session lacks a via library')
    resolution=_child(routes,'resolution')
    if resolution is None or str(resolution[1])!='um' or resolution[2]<=0:
        raise ValueError('Session requires explicit micron resolution')
    expected={tuple(row) for row in manifest['fixed_via_instances']}
    network=_child(routes,'network_out')
    if network is None:raise ValueError('Session lacks a network')
    for net in network[1:]:
        if str(net[0])!='net':raise ValueError('Unsupported session network')
        for via in net[2:]:
            if str(via[0])=='via' and str(via[1]) in manifest['via_padstacks']:
                pose=(str(via[1]),via[2]/resolution[2],via[3]/resolution[2],str(net[1]))
                if pose not in expected:raise ValueError('Session reused a fixed via connector at an undeclared position')
    for i,node in enumerate(library[1:],1):
        if str(node[0])=='padstack' and str(node[1]) in manifest['via_padstacks']:
            library[i]=_read(manifest['via_padstacks'][str(node[1])])
    return _write(tree)


def fixed_trace_exchange(text,*,indices):
    """Enclose reported fixed round strokes without changing any via profile.

    The router's mitered tiles can overestimate a legal native round cap.
    Full conservative capsules retain its occupied area; a contained fixed
    spine retains contacts. Native exact roundtrip and final DRC still gate it.
    """
    selected=set(indices);tree=_read(text);wiring=_child(tree,'wiring')
    if wiring is None or any(type(i) is not int or not 1<=i<len(wiring) for i in selected):
        raise ValueError('Invalid fixed trace selection')
    for i in selected:
        row=wiring[i]
        if (str(row[0])!='wire' or _child(row,'path') is None or
            _child(row,'type') not in ([sx.Symbol('type'),sx.Symbol('fix')],[sx.Symbol('type'),sx.Symbol('protect')])):
            raise ValueError('Only fixed traces have a proven conservative exchange')
    return fixed_copper_exchange(text,indices=selected)


def diagnosed_fixed_traces(text,report):
    """Only trace strokes use the trace adapter; via/pin errors need their own proof."""
    selected=diagnosed_fixed_objects(text,report);wiring=_child(_read(text),'wiring')
    return {i for i in selected if str(wiring[i][0])=='wire' and _child(wiring[i],'path') is not None}


def _fixed_pin_pose(pin,place):
    """Decode a physical alias using DSN placement, including pin rotation scopes."""
    body=[q for q in pin[2:] if not isinstance(q,list)]
    modifiers=[q for q in pin[2:] if isinstance(q,list)]
    if len(body)!=3 or any(len(q)!=2 or str(q[0])!='rotate' for q in modifiers):
        raise ValueError('Unsupported DSN pin pose')
    alias,x,y=body
    values=(x,y,place[2],place[3],place[5],*(q[1] for q in modifiers))
    if any(type(q) not in (int,float) or not math.isfinite(q) for q in values):raise ValueError('Invalid DSN pin coordinates')
    face=str(place[4])
    if face not in ('front','back'):raise ValueError('Unsupported DSN placement side')
    if face=='back':x=-x
    angle=math.radians(place[5]);c,s=math.cos(angle),math.sin(angle)
    px,py=place[2]+c*x-s*y,place[3]+s*x+c*y
    return str(alias),(px*1000,-py*1000)


def _round_pin_profile(stack,radius):
    """Match the source physical circular profile, not a neighboring SMD alias."""
    shapes=[q[1] for q in stack[2:] if str(q[0])=='shape']
    if len(shapes)!=2 or {str(q[1]) for q in shapes}!={'F.Cu','B.Cu'}:return False
    for shape in shapes:
        if str(shape[0])=='circle':
            if len(shape) not in (3,5) or abs(shape[2]/2-radius)>.2 or len(shape)==5 and math.hypot(*shape[3:])>.2:return False
        elif str(shape[0])=='polygon':
            values=shape[3:]
            if len(values)<6 or len(values)%2:return False
            distances=[math.hypot(x,y) for x,y in zip(values[::2],values[1::2])]
            if min(distances)<radius-.2 or max(distances)>(radius+.02)/math.cos(math.pi/64)+.2:return False
        else:return False
    return True


def fixed_round_pin_exchange(text,report,native_pads,*,completed_nets):
    """Represent reported, retained PTH pads without a coarse drill tile.

    The caller supplies native circular plated pads and natively completed
    nets that the router will not reroute. Full enclosing copper remains a
    same-net conduction area; a contained pin retains the logical endpoint.
    Native pads, via profiles and manufacturing rules never change. Strict
    router preflight and exact native roundtrip are still mandatory.
    """
    if report.get('coordinateUnits')!='mm':raise ValueError('Unsupported report units')
    if not isinstance(completed_nets,(set,frozenset)) or any(type(n) is not str for n in completed_nets):
        raise ValueError('Expected verified completed net names')
    receipt={'converted_pads':[],'enclosure_guard_um':.1,'connector_um':1}
    selected={}
    for violation in report['violations']:
        if violation['severity']!='error':continue
        items=violation['items'];vias=[q for q in items if q['description'].startswith('Via [') and q['description'].endswith(']')]
        for item in items:
            label=item['description']
            if not label.startswith('Pin [') or not label.endswith(']'):continue
            net=label[5:-1]
            if net not in completed_nets or not any(q['description'][5:-1]==net for q in vias):continue
            target=(item['pos']['x']*1000000,-item['pos']['y']*1000000)
            matches=[q for q in native_pads if q['net']==net and math.dist(q['position'],target)<8000]
            if len(matches)>1:raise ValueError('Ambiguous reported native pin')
            if not matches:continue
            pad=matches[0];size=pad['size'];drill=pad['drill']
            if len(size)!=2 or len(drill)!=2 or not 0<drill[0]==drill[1]<size[0]==size[1]:continue
            if any(type(n) is not int for n in (*size,*drill,*pad['position'])):raise ValueError('Expected native integer pin geometry')
            selected[pad['uuid']]=pad
    if not selected:return text,receipt
    if len(selected)>128:raise ValueError('Fixed pin exchange selection is too large')
    tree=_read(text);lib=_child(tree,'library');placement=_child(tree,'placement');wiring=_child(tree,'wiring')
    if _child(tree,'unit')!=[sx.Symbol('unit'),sx.Symbol('um')] or any(q is None for q in (lib,placement,wiring)):
        raise ValueError('Incomplete micron DSN')
    if any(str(q[1]).startswith('SmilePCBFixedPin') for q in lib[1:]):raise ValueError('Fixed pin exchange already applied')
    for index,pad in enumerate(sorted(selected.values(),key=lambda q:(q['ref'],q['number'],*q['position']))):
        places=[(component,place) for component in placement[1:] if str(component[0])=='component'
                for place in component[2:] if isinstance(place,list) and str(place[0])=='place' and str(place[1])==pad['ref']]
        if len(places)!=1:raise ValueError('Fixed pin component is ambiguous or absent')
        component,place=places[0];images=[q for q in lib[1:] if str(q[0])=='image' and str(q[1])==str(component[1])]
        if len(images)!=1:raise ValueError('Fixed pin image is ambiguous or absent')
        image=deepcopy(images[0]);image[1]='SmilePCBFixedPinImage'+str(index)
        candidates=[];network=_child(tree,'network')
        if network is None:raise ValueError('Fixed pin requires explicit network bindings')
        nets=[q for q in network[1:] if str(q[0])=='net' and str(q[1])==pad['net']]
        if len(nets)!=1 or _child(nets[0],'pins') is None:raise ValueError('Fixed pin native net is absent')
        bindings={str(q) for q in _child(nets[0],'pins')[1:]}
        for candidate in image[2:]:
            if str(candidate[0])!='pin':continue
            alias,point=_fixed_pin_pose(candidate,place)
            if pad['ref']+'-'+alias not in bindings or math.dist(point,pad['position'])>200:continue
            stacks=[q for q in lib[1:] if str(q[0])=='padstack' and str(q[1])==str(candidate[1])]
            if len(stacks)!=1:raise ValueError('Fixed pin profile is ambiguous or absent')
            if _round_pin_profile(stacks[0],pad['size'][0]/2000):candidates.append((candidate,stacks[0]))
        if len(candidates)!=1:raise ValueError('Physical fixed pin alias/geometry is ambiguous or absent')
        pin,original=candidates[0];stack=deepcopy(original)
        stack[1]='SmilePCBFixedPinConnector'+str(index);pin[1]=stack[1];layers=[]
        for shape in stack[2:]:
            if str(shape[0])=='shape':
                layers.append(str(shape[1][1]));shape[1]=[sx.Symbol('circle'),shape[1][1],1]
        if len(layers)!=2 or set(layers)!={'F.Cu','B.Cu'}:raise ValueError('Fixed pin must span exactly the two copper layers')
        lib.extend([stack,image]);placement.append([sx.Symbol('component'),image[1],deepcopy(place)]);component.remove(place)
        if len(component)==2:placement.remove(component)
        x,y=pad['position'];point=(x/1000,-y/1000);radius=pad['size'][0]/2000+receipt['enclosure_guard_um']
        for layer in layers:
            wiring.append([sx.Symbol('wire'),[sx.Symbol('polygon'),sx.Symbol(layer),0,*_capsule(point,point,radius)],
                           [sx.Symbol('net'),pad['net']],[sx.Symbol('type'),sx.Symbol('fix')]])
        receipt['converted_pads'].append({k:pad[k] for k in ('ref','number','net','uuid')})
    return _write(tree),receipt


def verify_fixed_input_discrepancies(report,native_snapshot,*,protected_ids,completed_nets,hole_clearance_nm):
    """Audit surrogate-only source via pairs; not a manufacturing certificate.

    The caller must first prove native ERC/physical DRC/parity clean, then an
    exact no-routing roundtrip, and require native final acceptance. Only
    existing fixed through-vias of one natively completed net qualify. Their
    real circular drill gap must satisfy the native hole-clearance floor.
    No new or unrecognized router violation receives an exception.
    """
    if report.get('coordinateUnits')!='mm':raise ValueError('Unsupported router discrepancy units')
    if type(hole_clearance_nm) is not int or hole_clearance_nm<=0:raise ValueError('Expected actual native hole-clearance floor')
    pairs={};count=0
    for violation in report['violations']:
        if violation['severity']!='error':continue
        if violation.get('type')!='holeClearance' or len(violation['items'])!=2:
            raise ValueError('Unrecognized router input discrepancy')
        selected=[]
        for item in violation['items']:
            description=item['description']
            if not description.startswith('Via [') or not description.endswith(']'):raise ValueError('Only fixed via/via discrepancies qualify')
            net=description[5:-1]
            if net not in completed_nets:raise ValueError('Router discrepancy is on an unfinished native net')
            point=(item['pos']['x']*1000000,-item['pos']['y']*1000000)
            matches=[(uid,q) for uid,q in native_snapshot['tracks'].items() if uid in protected_ids
                     and q['class']=='PCB_VIA' and q['net']==net and math.dist(q['start'],point)<8000]
            if len(matches)!=1:raise ValueError('Router discrepancy does not identify one fixed native via')
            uid,via=matches[0]
            if (set((via['top'],via['bottom']))!={0,2} or via['start']!=via['end'] or
                    type(via['drill']) is not int or type(via['width']) is not int or not 0<via['drill']<via['width']):
                raise ValueError('Unsupported native via geometry in router discrepancy')
            selected.append((uid,via))
        (a,first),(b,second)=selected
        if a==b or first['net']!=second['net']:raise ValueError('Router discrepancy is not one same-net physical via pair')
        gap=math.dist(first['start'],second['start'])-(first['drill']+second['drill'])/2
        if gap<hole_clearance_nm:raise ValueError('Source via holes violate the native manufacturing clearance')
        key=tuple(sorted((a,b)))
        pairs[key]={'ids':list(key),'net':first['net'],'hole_gap_nm':gap};count+=1
    return {'accepted':True,'violations':count,'pairs':[pairs[k] for k in sorted(pairs)],
            'native_physical_preflight_and_final_checks_required':True}
