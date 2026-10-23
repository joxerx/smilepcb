"""Stable ordering for the selected native DSN format; no CAD geometry edits."""
import re
import json
import sexpdata as sx


def _canonical_wire_paths(wiring):
    from copy import deepcopy
    result=[]
    for wire in wiring[1:]:
        paths=[node for node in wire[1:] if isinstance(node,list) and str(node[0])=='path'] if str(wire[0])=='wire' else []
        if not paths:result.append(wire);continue
        if len(paths)!=1:raise ValueError('Unsupported native wire path declaration')
        path=paths[0];coordinates=path[3:]
        if len(coordinates)<4 or len(coordinates)%2:raise ValueError('Unsupported native wire coordinates')
        points=list(zip(coordinates[::2],coordinates[1::2]))
        for a,b in zip(points,points[1:]):
            copy=deepcopy(wire);target=next(node for node in copy[1:] if isinstance(node,list) and str(node[0])=='path')
            target[3:]=[value for point in sorted((a,b)) for value in point]
            result.append(copy)
    wiring[1:]=result


def canonical_dsn(text):
    if '(unit um)' not in text:
        raise ValueError('Only native micron-unit DSN is supported')
    # Paths are metadata, not component identifiers; remove output-directory noise.
    text = re.sub(r'^\(pcb\s+(?:"(?:\\.|[^"\\])*"|[^\s()]+)',
                  '(pcb "smilepcb-routing"', text, count=1)
    marks = list(re.finditer(r'^  \((\w+)(?=[\s)])', text, flags=re.M))
    required = {'placement', 'library', 'network', 'wiring'}
    seen, parsed = set(), []
    for i, mark in enumerate(marks):
        tag = mark[1]
        if tag not in required:
            continue
        if tag in seen:
            raise ValueError('Duplicate DSN exchange section')
        seen.add(tag)
        end = marks[i+1].start() if i+1 < len(marks) else text.rfind('\n)')
        section = text[mark.start():end]
        # sexpdata treats square brackets as list delimiters; DSN does not.
        # Quote those complete identifiers before parsing, in both definitions
        # and references, so their value and arity remain intact.
        section = re.sub(r'"(?:\\.|[^"\\])*"|[^()\s"]+',
            lambda m: json.dumps(m[0]) if not m[0].startswith('"') and any(c in m[0] for c in '[]') else m[0], section)
        tree = sx.loads(section, nil=None, true=None, false=None)
        children = tree[1:]
        if tag == 'placement':
            for node in children:
                if str(node[0]) != 'component' or any(str(n[0]) != 'place' for n in node[2:]):
                    raise ValueError('Unsupported native placement declaration')
                node[2:] = sorted(node[2:], key=lambda p: str(p[1]))
        elif tag == 'network':
            for node in children:
                if str(node[0]) == 'net':
                    for pins in node[2:]:
                        if str(pins[0]) == 'pins':
                            pins[1:] = sorted(pins[1:], key=str)
                elif str(node[0]) == 'class':
                    cut = next((j for j in range(2, len(node)) if isinstance(node[j], list)), len(node))
                    node[2:cut] = sorted(node[2:cut], key=str)
                else:
                    raise ValueError('Unsupported native network declaration')
        parsed.append((mark.start(),end,tag,tree))
    if seen != required:
        raise ValueError('Incomplete native DSN exchange sections')
    from .routing_images import normalize_images
    sections={tag:tree for _,_,tag,tree in parsed}
    normalize_images(sections['library'],sections['placement'])
    _canonical_wire_paths(sections['wiring'])
    for start,end,tag,tree in reversed(parsed):
        children=tree[1:]
        if tag == 'network':
            children.sort(key=lambda n:(0 if str(n[0])=='net' else 1,str(n[1])))
        else:
            children.sort(key=lambda n:(str(n[0]),str(n[1]),sx.dumps(n)))
        replacement=f'  ({tag}\n'+''.join('    '+sx.dumps(n)+'\n' for n in children)+'  )\n'
        text = text[:start] + replacement + text[end:]
    return text


CLEARANCE_MARGIN_UM = 10


def explicit_plane_routing(text):
    """Route discrete copper rather than trusting exported zone *outlines*.

    DSN plane polygons are not KiCad's filled islands or thermal connections.
    Remove them in the exchange only; retain every native zone and its rules.
    Placement, padstacks, network and fixed wires remain byte-identical.
    """
    start = text.index('\n  (structure')
    end = text.index('\n  (placement', start)
    structure = sx.loads(text[start:end])
    structure[1:] = [node for node in structure[1:] if str(node[0]) != 'plane']
    return text[:start] + '\n  ' + sx.dumps(structure) + text[end:]


def routing_dsn(text):
    """Apply a declared exchange-only guard band to untyped clearances.

    Custom-pad polygon approximation and SES quantization can otherwise leave
    a route a few microns below KiCad's true clearance. Native PCB rules stay.
    Typed pad-to-pad exemptions and all copper geometry remain unchanged.
    """
    text = canonical_dsn(text).replace('(string_quote ")', '(string_quote __DSN_QUOTE__)')
    pattern = r'"(?:\\.|[^"\\])*"|\(clearance (\d+)\)'
    result = re.sub(pattern, lambda m: m[0] if m[1] is None else
                    f'(clearance {int(m[1]) + CLEARANCE_MARGIN_UM})', text)
    return result.replace('(string_quote __DSN_QUOTE__)', '(string_quote ")')


def freerouting_route_modes(text):
    """Translate KiCad's unlocked copper marker without releasing locks.

    Freerouting's Wiring.calcFixed accepts NORMAL as UNFIXED; an unknown
    marker such as KiCad's ROUTE becomes USER_FIXED. This translation changes
    only native wire/via attributes, never geometry or component placement.
    """
    start=text.index('\n  (wiring')
    prefix,wiring=text[:start],text[start:]
    declaration=r'(?m)^    \((?:wire|via)\b[^\n]+'
    if len(re.findall(declaration,wiring))!=len(re.findall(r'\((?:wire|via)\b',wiring)):
        raise ValueError('Unconsumed native DSN copper declaration')
    counts={'native_route_items':0,'retained_fixed_items':0}
    def convert(match):
        row=match[0]
        mode=re.search(r'\(type (route|normal|fix|protect|shove_fixed)\)\)$',row)
        if mode is None:
            raise ValueError('Unsupported native DSN copper mode declaration')
        if mode[1]=='route':
            counts['native_route_items']+=1
            return row[:mode.start()]+'(type normal))'
        if mode[1]!='normal':counts['retained_fixed_items']+=1
        return row
    result=re.sub(declaration,convert,wiring)
    return prefix+result,counts


def split_junctions(text):
    """Expose same-net/layer branch vertices in temporary native DSN wires.

    Wire attributes and other wiring objects are retained. The native PCB is
    untouched, and exact bidirectional copper coverage is checked independently.
    """
    from copy import deepcopy
    from decimal import Decimal, ROUND_HALF_UP
    from .routing_copper import dsn_wire_shadow, routing_delta
    before = dsn_wire_shadow(text)  # Reject unsupported units/path declarations.
    start = text.index('\n  (wiring')
    end = text.rfind('\n)')
    tree = sx.loads(text[start:end], nil=None, true=None, false=None)
    def child(wire, name):
        return next(n for n in wire[1:] if isinstance(n,list) and str(n[0])==name)
    wires = [n for n in tree[1:] if str(n[0])=='wire']
    def grid(value):
        scaled=Decimal(str(value))*10
        if not scaled.is_finite():
            raise ValueError('Non-finite DSN junction coordinate')
        # Native export may contain extra decimals. Match the declared exchange
        # grid and its independently parsed copper shadow, never the PCB itself.
        return int(scaled.to_integral_value(rounding=ROUND_HALF_UP))
    def positions(path):return [(grid(a),grid(b)) for a,b in zip(path[3::2],path[4::2])]
    def coordinate(value):return value//10 if value%10==0 else value/10
    points = {}
    for wire in wires:
        path = child(wire,'path'); key = (str(path[1]),str(child(wire,'net')[1]))
        points.setdefault(key,set()).update(positions(path))
    result=[];pieces=0
    for node in tree[1:]:
        if str(node[0])!='wire':
            result.append(node);continue
        path=child(node,'path');key=(str(path[1]),str(child(node,'net')[1]))
        xy=positions(path)
        for a,b in zip(xy,xy[1:]):
            dx,dy=b[0]-a[0],b[1]-a[1];length=dx*dx+dy*dy
            if not length:raise ValueError('Zero-length DSN wire is unsupported')
            cuts=[q for q in points[key] if (q[0]-a[0])*dy==(q[1]-a[1])*dx
                  and 0<(q[0]-a[0])*dx+(q[1]-a[1])*dy<length]
            ordered=[a]+sorted(cuts,key=lambda q:(q[0]-a[0])*dx+(q[1]-a[1])*dy)+[b]
            for u,v in zip(ordered,ordered[1:]):
                copy=deepcopy(node);child(copy,'path')[3:]=[coordinate(c) for c in (*u,*v)]
                result.append(copy);pieces+=1
    output=text[:start]+'\n  (wiring\n'+''.join('    '+sx.dumps(n)+'\n' for n in result)+'  )\n'+text[end:]
    after=dsn_wire_shadow(output)
    if routing_delta(before,after) or routing_delta(after,before):
        raise ValueError('DSN junction normalization changed copper')
    return output,{'before_wires':len(wires),'after_segments':pieces,'exact_bidirectional_copper_coverage':True}
