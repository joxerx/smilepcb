"""Parameter-derived closed-carrier pinch fixture; no native work on import.

The automatic production repair is exercised by its companion native probe.
"""
from copy import deepcopy
import math

NM=1_000_000


def blueprint(width=150000,gap=173000,margin=20000,quantum=50000):
    pitch=math.ceil((width+gap+margin)/quantum)*quantum
    if not width+gap<=pitch<math.sqrt(2)*(width+gap):
        raise ValueError('This fixture needs legal straight pitch that pinches at a common 45-degree turn')
    x,y=12*NM,6*NM;p=pitch
    sources={n:[(sx,4*NM),(sx,y)] for n,sx in (('outer_left',x-p),('inner',x),('outer_right',x+p))}
    sources['fixed']=[(24*NM,4*NM),(24*NM,y)]
    fields={'outer_left':[(5*NM,10500000),(4*NM,10500000)],'outer_right':[(5*NM,12*NM),(4*NM,12*NM)],'inner':[(20*NM,14750000),(20500000,14750000)]}
    carriers={'outer_left':[(x-p,y),(x-p,8*NM),(x-p-2500000,10500000),(5*NM,10500000)],'outer_right':[(x+p,y),(x+p,8*NM),(x+p-4*NM,12*NM),(5*NM,12*NM)]}
    live_advances={'inner':[(x,y),(x,6250000)],'fixed':[(24*NM,y),(24*NM,8*NM)]}
    return {'width_nm':width,'gap_nm':gap,'pitch_nm':pitch,'via_diameter_nm':500000,'via_drill_nm':200000,
            'outline_nm':[0,0,30*NM,22*NM],'source_paths':sources,'field_paths':fields,
            'closed_carriers':carriers,'live_advances':live_advances,
            'field_pad_centres':{'outer_left':(4*NM,10500000),'outer_right':(4*NM,12*NM),'inner':(20*NM,14750000),'fixed':(25*NM,16*NM)}}


def build_native(p,*,face=None,translation=(0,0),on_carrier=None):
    """Construct native copper only when a caller explicitly supplies pcbnew."""
    s=blueprint();face=p.B_Cu if face is None else face;other=p.B_Cu if face==p.F_Cu else p.F_Cu
    b=p.BOARD();b.SetCopperLayerCount(2)
    from smilepcb.keyboard_process import apply_native_rules
    apply_native_rules(b,'fr4-2l-1oz-0p5-0p2')
    def point(q):return p.VECTOR2I(q[0]+translation[0],q[1]+translation[1])
    def layer(value):return face if value==2 else other
    names=('outer_left','inner','outer_right','fixed')
    for n in names:b.Add(p.NETINFO_ITEM(b,n))
    corners=[(0,0),(30*NM,0),(30*NM,22*NM),(0,22*NM)]
    for a,z in zip(corners,corners[1:]+corners[:1]):
        item=p.PCB_SHAPE(b);item.SetShape(p.SHAPE_T_SEGMENT);item.SetStart(point(a));item.SetEnd(point(z));item.SetLayer(p.Edge_Cuts);item.SetWidth(50000);b.Add(item)
    def track(net,path,face):
        ids=[]
        for a,z in zip(path,path[1:]):
            item=p.PCB_TRACK(b);item.SetStart(point(a));item.SetEnd(point(z));item.SetWidth(s['width_nm']);item.SetLayer(face);item.SetNet(b.FindNet(net));b.Add(item);ids.append(item.m_Uuid.AsString())
        return ids
    def pad(fp,number,net,pos,face,tht=False):
        item=p.PAD(fp);item.SetNumber(number);item.SetPosition(point(pos));item.SetNet(b.FindNet(net))
        item.SetAttribute(p.PAD_ATTRIB_PTH if tht else p.PAD_ATTRIB_SMD);item.SetShape(p.PAD_SHAPE_CIRCLE if tht else p.PAD_SHAPE_RECT)
        item.SetSize(p.VECTOR2I(700000,700000) if tht else p.VECTOR2I(s['width_nm'],300000))
        ls=p.LSET.AllCuMask() if tht else p.LSET()
        if not tht:ls.AddLayer(face)
        else:item.SetDrillSize(p.VECTOR2I(300000,300000))
        item.SetLayerSet(ls);fp.Add(item);return item.m_Uuid.AsString()
    chip=p.FOOTPRINT(b);chip.SetReference('U_MCU');b.Add(chip);field=p.FOOTPRINT(b);field.SetReference('FIELD');b.Add(field)
    sources=[];immutable=set();field_ids=set();carrier_ids={};advance_ids={}
    for number,n in enumerate(names,1):
        path=s['source_paths'][n];source_face=other if n=='fixed' else face
        pads=[pad(chip,str(number),n,path[0],source_face)]
        if n=='outer_right':pads.append(pad(chip,str(number),n,(path[0][0],path[0][1]+250000),source_face))
        ids=track(n,path,source_face);immutable.update(ids)
        def shifted(q):return [q[0]+translation[0],q[1]+translation[1]]
        sources.append({'net':n,'kind':'same_layer','pad_ids':pads,'added_ids':ids,'terminal_id':ids[-1],'exit_nm':shifted(path[-1]),'heading_origin_nm':shifted(path[-2]),'exit_layer':source_face,'via_id':None,'via_nm':None})
        pad(field,str(number),n,s['field_pad_centres'][n],other if n in ('inner','fixed') else face,n.startswith('outer'))
    for n,path in s['field_paths'].items():field_ids.update(track(n,path,other if n=='inner' else face))
    immutable.update(field_ids)
    current=deepcopy(sources)
    for n,path in {**s['closed_carriers'],**s['live_advances']}.items():
        q=next(q for q in current if q['net']==n)
        if on_carrier is not None and n in s['closed_carriers']:
            from smilepcb.native_worker import snapshot
            creator_before=snapshot(b)
        ids=track(n,path,q['exit_layer']);q['added_ids'].extend(ids);q.update(terminal_id=ids[-1],exit_nm=[path[-1][i]+translation[i] for i in (0,1)],heading_origin_nm=[path[-2][i]+translation[i] for i in (0,1)])
        if on_carrier is not None and n in s['closed_carriers']:on_carrier(b,n,creator_before,ids)
        (carrier_ids if n in s['closed_carriers'] else advance_ids)[n]=ids
    return b,{'spec':s,'source_ports':sources,'current_ports':current,'immutable_ids':sorted(immutable),'field_ids':sorted(field_ids),'carrier_ids':carrier_ids,'live_advance_ids':advance_ids,'face':face,'other_face':other,'translation':translation}
