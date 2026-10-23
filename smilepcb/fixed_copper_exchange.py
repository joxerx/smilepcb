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
