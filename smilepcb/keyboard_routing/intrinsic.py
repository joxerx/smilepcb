"""Exchange-only intrinsic contacts inside contiguous compound pads."""
from collections import defaultdict
from smilepcb.native_worker import p


def regions(board):
    result=[]
    for fp in board.GetFootprints():
        for layer,name in ((p.F_Cu,'F.Cu'),(p.B_Cu,'B.Cu')):
            groups=defaultdict(list)
            for pad in fp.Pads():
                if pad.GetNumber() and pad.IsOnLayer(layer):groups[pad.GetNumber()].append(pad)
            for number,pads in groups.items():
                if len(pads)<2 or len({d.GetNetname() for d in pads})!=1:continue
                shapes=[d.GetEffectiveShape(layer) for d in pads];seen={0}
                for _ in pads:
                    seen|={j for j in range(len(pads)) if any(p.SHAPE.Collide(shapes[i],shapes[j],0) for i in seen)}
                if len(seen)!=len(pads):continue
                xs=[d.GetPosition().x for d in pads];ys=[d.GetPosition().y for d in pads]
                horizontal=max(ys)-min(ys)<=1;vertical=max(xs)-min(xs)<=1
                if not (horizontal or vertical):continue
                half=10000
                if horizontal:
                    lo,hi=min(xs),max(xs);middle=round(sum(ys)/len(ys))
                    points=[(lo,middle-half),(hi,middle-half),(hi,middle+half),(lo,middle+half)]
                else:
                    lo,hi=min(ys),max(ys);middle=round(sum(xs)/len(xs))
                    points=[(middle-half,lo),(middle+half,lo),(middle+half,hi),(middle-half,hi)]
                if lo==hi:continue
                # These are topology hints, never additional native copper.
                # Restrict this supported case to a narrow fully covered band.
                covered=True
                for value in range(lo,hi+1,50000):
                    for offset in (-half,0,half):
                        x,y=(value,middle+offset) if horizontal else (middle+offset,value)
                        probe=p.SHAPE_CIRCLE(p.VECTOR2I(x,y),1)
                        if not any(p.SHAPE.Collide(probe,shape,0) for shape in shapes):covered=False;break
                    if not covered:break
                if not covered:continue
                result.append({'ref':fp.GetReference(),'number':number,'net':pads[0].GetNetname(),
                               'layer':name,'points_um':[[x/1000,-y/1000] for x,y in points]})
    return result
