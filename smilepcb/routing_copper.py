"""Exact integer copper coverage when SES changes trace segmentation.

Only straight traces and exact vias are supported. Original PCB objects are
never replaced; the importer adds the returned geometry outside their union.
"""
import copy
from fractions import Fraction
import json


def signature(item):
    data = dict(item)
    data.pop('locked', None)
    data['start'], data['end'] = sorted([data['start'], data['end']])
    return json.dumps(data, sort_keys=True)


def intervals(item, others):
    a, b = item['start'], item['end']
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = dx * dx + dy * dy
    if not length:
        raise ValueError('Zero-length protected/returned trace is unsupported')
    matches = []
    for other in others:
        if any(other.get(k) != item.get(k) for k in ('class', 'width', 'net', 'layer')):
            continue
        points = other['start'], other['end']
        if any((p[0]-a[0])*dy != (p[1]-a[1])*dx for p in points):
            continue
        t = sorted((p[0]-a[0])*dx + (p[1]-a[1])*dy for p in points)
        lo, hi = max(0, t[0]), min(length, t[1])
        if lo < hi:
            matches.append((lo, hi))
    merged = []
    for lo, hi in sorted(matches):
        if merged and lo <= merged[-1][1]:
            merged[-1] = merged[-1][0], max(hi, merged[-1][1])
        else:
            merged.append((lo, hi))
    return length, merged


def uncovered(item, others):
    length, covered = intervals(item, others)
    cursor = 0
    gaps = []
    for lo, hi in covered + [(length, length)]:
        if cursor < lo:
            gaps.append((cursor, lo))
        cursor = hi
    a, b = item['start'], item['end']
    pieces = []
    for lo, hi in gaps:
        piece = copy.deepcopy(item)
        for key, t in (('start', lo), ('end', hi)):
            point = [Fraction(a[i]) + Fraction(t * (b[i] - a[i]), length) for i in (0, 1)]
            if any(c.denominator != 1 for c in point):
                raise ValueError('Coverage boundary is not on the native integer grid')
            piece[key] = [int(c) for c in point]
        pieces.append(piece)
    return pieces


def routing_delta(original, returned):
    """Reject loss/change, then return (returned UUID, new-only shape) pairs."""
    old = list(original.values())
    new = list(returned.values())
    new_signatures = {signature(t) for t in new}
    old_signatures = {signature(t) for t in old}
    for item in old:
        if item['class'] == 'PCB_TRACK':
            if uncovered(item, new):
                raise ValueError('Router changed or removed protected copper coverage')
        elif item['class'] == 'PCB_VIA':
            if signature(item) not in new_signatures:
                raise ValueError('Router changed or removed a protected via')
        else:
            raise ValueError('Unsupported protected copper type')
    delta = []
    seen = set(old_signatures)
    for uid, item in returned.items():
        if item['class'] == 'PCB_TRACK':
            pieces = uncovered(item, old)
        elif item['class'] == 'PCB_VIA':
            pieces = [] if signature(item) in old_signatures else [item]
        else:
            raise ValueError('Unsupported returned copper type')
        for piece in pieces:
            key = signature(piece)
            if key not in seen:
                delta.append((uid, piece))
                seen.add(key)
    return delta



def dsn_wire_shadow(text):
    """Read native KiCad DSN straight paths onto its declared 0.1 um grid.

    Parse the exported file, not guessed rounding of nanometre PCB objects.
    The native originals remain separate and exact. Only this temporary
    representation may be split by the router.
    """
    import re
    from decimal import Decimal, ROUND_HALF_UP
    if '(unit um)' not in text or '(resolution um 10)' not in text:
        raise ValueError('Expected native micron DSN with 0.1 um resolution')
    def nm(value):
        return int((Decimal(value)*10).to_integral_value(rounding=ROUND_HALF_UP))*100
    tree = text[text.index('\n  (wiring'):]
    lines = re.findall(r'(?m)^    \(wire[^\n]+', tree)
    if len(lines) != len(re.findall(r'\(wire\b',tree)):
        raise ValueError('Unsupported or unconsumed DSN wire declaration')
    result = {}
    for line in lines:
        match = re.fullmatch(r'    \(wire \(path (F\.Cu|B\.Cu) ([\d.]+) +([^()]+)\)\s*\(net (.+?)\)\s*\(type (fix|protect|shove_fixed|route|normal)\)\)',line)
        if not match:
            raise ValueError('Unsupported native DSN wire shape')
        layer, width, coords, net, mode = match.groups()
        coords = [nm(c) for c in coords.split()]
        if len(coords)<4 or len(coords)%2 or nm(width)<=0:
            raise ValueError('Invalid DSN path')
        net = json.loads(net) if net.startswith('"') else net
        points = [[coords[i],-coords[i+1]] for i in range(0,len(coords),2)]
        for a,b in zip(points,points[1:]):
            if a!=b:
                result['shadow-'+str(len(result))] = dict(
                    **{'class':'PCB_TRACK','start':a,'end':b,'width':nm(width),
                       'layer':0 if layer=='F.Cu' else 2,'net':net,'locked':False})
    return result


def dsn_via_shadow(text):
    """Native through-via exchange geometry, including serialization rounding."""
    import re
    from decimal import Decimal,ROUND_HALF_UP
    if '(unit um)' not in text or '(resolution um 10)' not in text:
        raise ValueError('Expected native micron DSN')
    def nm(value):return int((Decimal(str(value))*10).to_integral_value(rounding=ROUND_HALF_UP))*100
    start=text.index('\n  (library');end=text.find('\n  (network',start)
    if end<0:end=text.index('\n  (wiring',start)
    library=text[start:end]
    pattern=(r'\(padstack ("Via\[0-1\]_[\d.]+:[\d.]+_um")\s*'
             r'\(shape \(circle F\.Cu ([\d.]+)\)\)\s*'
             r'\(shape \(circle B\.Cu ([\d.]+)\)\)\s*\(attach off\)\s*\)')
    stacks={json.loads(name):(a,b) for name,a,b in re.findall(pattern,library)}
    wiring=text[text.index('\n  (wiring'):];lines=re.findall(r'(?m)^    \(via[^\n]+',wiring)
    if len(lines)!=len(re.findall(r'\(via\b',wiring)):raise ValueError('Unconsumed DSN via declaration')
    result={}
    for line in lines:
        m=re.fullmatch(r'    \(via ("Via\[0-1\]_([\d.]+):([\d.]+)_um")\s+([^\s()]+) +([^\s()]+) \(net (.+?)\)\s*\(type (?:fix|protect|shove_fixed|route|normal)\)\)',line)
        if not m:raise ValueError('Unsupported native through-via declaration')
        quoted,width,drill,x,y,net=m.groups();name=json.loads(quoted)
        shapes=stacks.get(name,())
        if (len(shapes)!=2 or any(nm(diameter)!=nm(width) for diameter in shapes) or
                not 0<nm(drill)<nm(width)):raise ValueError('Via padstack does not match its native identifier')
        point=[nm(x),-nm(y)];net=json.loads(net) if net.startswith('"') else net
        result['via-shadow-'+str(len(result))]={'class':'PCB_VIA','start':point,'end':point[:],
            'width':nm(width),'drill':nm(drill),'layer':0,'net':net,'locked':False,'via_type':4,'top':0,'bottom':2}
    return result


def exchange_delta(original, returned, shadow, junction_nets,via_shadow=None):
    """Require complete selected exchange coverage, retain actual PCB separately."""
    expected = {uid:t for uid,t in shadow.items() if t['net'] in junction_nets}
    if {t['net'] for t in expected.values()} != {t['net'] for t in original.values()
            if t['class']=='PCB_TRACK' and t['net'] in junction_nets and t['start']!=t['end']}:
        raise ValueError('Exchange shadow is missing a selected source net')
    observed = {uid:t for uid,t in returned.items()
                if t['class']!='PCB_TRACK' or t['start']!=t['end']}
    # Fixed-source omissions are declared, never borrowed on splittable nets.
    for uid,t in original.items():
        if t['class']=='PCB_TRACK' and t['start']==t['end']:
            continue
        if t['class']=='PCB_VIA' or t['net'] not in junction_nets:
            expected['fixed-'+uid]=t
            observed['fixed-'+uid]=t
    # Remove the non-selected exported wire shadows too, so they cannot be
    # imported as slightly shifted duplicate traces.
    for uid,t in shadow.items():
        if t['net'] not in junction_nets:
            expected[uid]=t
            observed[uid]=t
    # Fixed vias can be omitted from SES or returned at the native DSN's
    # serialized coordinates. Neither case is a newly drilled hole. Actual
    # source objects remain exact and separate in the retained PCB.
    for uid,t in (via_shadow or {}).items():
        expected[uid]=t;observed[uid]=t
    return routing_delta({uid:t for uid,t in expected.items()
                          if t['class']!='PCB_TRACK' or t['start']!=t['end']}, observed)
