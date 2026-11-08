import json
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_routing.gpio import apply_matrix_gpio

def fixture():
    b=p.BOARD();b.SetCopperLayerCount(2)
    for name in ('ROW0','COL0','NC_MCU_3','+3V3'):
        b.Add(p.NETINFO_ITEM(b,name))
    cfg={'parts':[{'ref':'U_MCU','nets':{'1':'ROW0','2':'COL0','3':'NC_MCU_3','4':'+3V3'},'no_connect':['3']}],
         'mcu_gpio_pads':{'A0':'1','A1':'2','A2':'3'},'layout':{'gpio':{'rows':['A0'],'cols':['A1']}},
         'qmk':{'matrix_pins':{'rows':['A0'],'cols':['A1']}}}
    for ref,positions in [('U_MCU',[(10,10,'ROW0'),(11,10,'COL0'),(12,10,'NC_MCU_3'),(13,10,'+3V3')]),('keys',[(3,4,'ROW0'),(20,12,'COL0')])]:
        f=p.FOOTPRINT(b);f.SetReference(ref);b.Add(f)
        for i,(x,y,net) in enumerate(positions,1):
            d=p.PAD(f);d.SetNumber(str(i));d.SetNet(b.FindNet(net));d.SetAttribute(p.PAD_ATTRIB_SMD);d.SetShape(p.PAD_SHAPE_RECT);d.SetSize(p.VECTOR2I(300000,500000));layers=p.LSET();layers.AddLayer(p.B_Cu);d.SetLayerSet(layers);d.SetPosition(p.VECTOR2I(x*1000000,y*1000000));f.Add(d)
    t=p.PCB_TRACK(b);t.SetLayer(p.B_Cu);t.SetWidth(200000);t.SetNet(b.FindNet('+3V3'));t.SetStart(p.VECTOR2I(13000000,10000000));t.SetEnd(p.VECTOR2I(13000000,12000000));b.Add(t)
    return b,cfg
b,cfg=fixture();before=snapshot(b);apply_matrix_gpio(b,cfg);after=snapshot(b)
result={'power_unchanged':before['tracks']==after['tracks'],'power_pin_unchanged':next(d for f in b.GetFootprints() if f.GetReference()=='U_MCU' for d in f.Pads() if d.GetNumber()=='4').GetNetname()=='+3V3'}
b,cfg=fixture();t=p.PCB_TRACK(b);t.SetLayer(p.B_Cu);t.SetWidth(150000);t.SetNet(b.FindNet('ROW0'));t.SetStart(p.VECTOR2I(10000000,10000000));t.SetEnd(p.VECTOR2I(10000000,12000000));b.Add(t);before=snapshot(b)
try:apply_matrix_gpio(b,cfg);result['matrix_rejected']=False
except ValueError:result['matrix_rejected']=True
result['rejection_unchanged']=snapshot(b)==before
print(json.dumps(result))
