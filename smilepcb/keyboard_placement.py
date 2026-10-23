"""Geometry-only placement shared by the circuit model and native CAD worker."""
import math


def controller_origin(cfg):
    nm=lambda value:round(value*1000000)
    x0,y0,x1,y1 = map(nm,cfg['controller_bounds'])
    width,height = map(nm,cfg['layout']['size_mm'])
    boxes = []
    for key in cfg['layout']['keys']:
        x,y = key['position_u']; kw,kh = key['size_u']
        boxes.append(tuple(map(nm,(x*19.05,y*19.05,(x+kw)*19.05,(y+kh)*19.05))))
    # Keep the fixed 2 mm outline border available for escape routing.
    # Footprint-only placement could put a whole MCU via bank against the
    # edge. Prefer the greatest edge clearance among legal empty regions;
    # no component or board-specific corridor is moved after routing.
    # Legal regions change only when a block edge crosses a padded key edge.
    # Sampling those events finds narrow off-grid gaps that a 0.5 mm lattice
    # misses. Integer native coordinates keep touching comparisons exact.
    padding=nm(.5);border=nm(2)
    def candidates(lo,hi,limit,axis):
        values={-lo,limit-hi,(limit-lo-hi)//2}
        for box in boxes:values.update((box[axis]-hi-padding,box[axis+2]-lo+padding))
        return sorted(value for value in values if -lo<=value<=limit-hi)
    best=None;best_clearance=-1
    for y in candidates(y0,y1,height,1):
        for x in reversed(candidates(x0,x1,width,0)):
            box = (x+x0,y+y0,x+x1,y+y1)
            clearance=min(box[0]+border,box[1]+border,width+border-box[2],height+border-box[3])
            if clearance<=best_clearance:continue
            if all(box[2]+padding <= b[0] or box[0]-padding >= b[2] or
                   box[3]+padding <= b[1] or box[1]-padding >= b[3] for b in boxes):
                best=[x/1000000,y/1000000];best_clearance=clearance
    if best is not None:return best
    raise ValueError('Controller block does not fit inside the fixed outline and key envelopes')



def rgb_chain(layout, source):
    """Choose a cut and orientation of the serpentine cycle by lead length.

    The circuit and firmware consume this one result. No native copper or
    per-key coordinates are patched after either output is generated.
    """
    base=layout['rgb_order']
    positions={k['id']:(k['center_mm'][0],k['center_mm'][1]+5.08) for k in layout['keys']}
    cycle=sum(math.dist(positions[a],positions[b]) for a,b in zip(base,base[1:]+base[:1]))
    choices=[]
    for order in (base,list(reversed(base))):
        for index,first in enumerate(order):
            last=order[index-1]
            length=cycle-math.dist(positions[last],positions[first])+math.dist(source,positions[first])
            choices.append((length,tuple(order[index:]+order[:index])))
    length,order=min(choices)
    return list(order),{'method':'serpentine cycle with shortest driver lead and cut',
                       'estimated_path_mm':length,'input_lead_mm':math.dist(source,positions[order[0]]),
                       'driver_position_mm':list(source)}
