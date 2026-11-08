from smilepcb.native_worker import p

def fixture(face):
    b=p.BOARD();b.SetCopperLayerCount(2);r=b.GetDesignSettings();r.m_MinClearance=150000;r.m_TrackMinWidth=150000;r.m_MinThroughDrill=200000;r.m_ViasMinSize=500000;r.m_ViasMinAnnularWidth=150000;r.m_CopperEdgeClearance=200000
    for n in ('r0','r1','c0','c1'):b.Add(p.NETINFO_ITEM(b,n))
    vertices=[(0,0),(40000000,0),(40000000,40000000),(0,40000000)]
    for a,z in zip(vertices,vertices[1:]+vertices[:1]):
        e=p.PCB_SHAPE(b);e.SetShape(p.SHAPE_T_SEGMENT);e.SetStart(p.VECTOR2I(*a));e.SetEnd(p.VECTOR2I(*z));e.SetLayer(p.Edge_Cuts);e.SetWidth(50000);b.Add(e)
    def fp(ref,pos):
        f=p.FOOTPRINT(b);f.SetReference(ref);f.SetPosition(p.VECTOR2I(*pos));f.SetLayer(face);b.Add(f);return f
    def pad(f,num,n,pos,smd=False):
        d=p.PAD(f);d.SetNumber(num);d.SetPosition(p.VECTOR2I(*pos));d.SetNet(b.FindNet(n));d.SetShape(p.PAD_SHAPE_RECT if smd else p.PAD_SHAPE_CIRCLE);d.SetSize(p.VECTOR2I(700000,300000) if smd and pos[0]==22000000 else p.VECTOR2I(300000,700000) if smd else p.VECTOR2I(900000,900000));d.SetAttribute(p.PAD_ATTRIB_SMD if smd else p.PAD_ATTRIB_PTH)
        if smd:
            ls=p.LSET();ls.AddLayer(face);d.SetLayerSet(ls)
        else:d.SetLayerSet(p.LSET.AllCuMask());d.SetDrillSize(p.VECTOR2I(300000,300000))
        f.Add(d);return d
    chip=fp('U_MCU',(18000000,18000000))
    for number,n,xy in [('1','r0',(22000000,17500000)),('2','r1',(22000000,18000000)),('3','c0',(17500000,14000000)),('4','c1',(18000000,14000000))]:pad(chip,number,n,xy,True)
    cfg={'parts':[{'ref':'U_MCU','region':'controller','nets':{'1':'r0','2':'r1','3':'c0','4':'c1'}}],'layout':{'keys':[],'gpio':{'rows':['A0','A1'],'cols':['B0','B1']},'rgb':False},'mcu_gpio_pads':{'A0':'1','A1':'2','B0':'3','B1':'4'},'plane_nets':[],'qmk':{'matrix_pins':{'rows':['A0','A1'],'cols':['B0','B1']}}}
    for row,y in enumerate((5000000,30000000)):
        for col,x in enumerate((5000000,30000000)):
            key=1+row*2+col;cfg['layout']['keys'].append({'id':key,'matrix':[row,col]})
            for ref,net,pt in [('SW'+str(key),'c'+str(col),(x,y)),('D'+str(key),'r'+str(row),(x,y+2500000))]:
                f=fp(ref,pt);pad(f,'1',net,pt);cfg['parts'].append({'ref':ref,'region':'matrix','nets':{'1':net}})
    for key in cfg['layout']['keys']:
        row,col=key['matrix'];x=(5000000,30000000)[col];y=(5000000,30000000)[row]
        net='key'+str(key['id']);b.Add(p.NETINFO_ITEM(b,net))
        for ref,pt in [('SW'+str(key['id']),(x+2000000,y)),('D'+str(key['id']),(x+2000000,y+2500000))]:
            f=next(f for f in b.GetFootprints() if f.GetReference()==ref);pad(f,'2',net,pt)
            next(q for q in cfg['parts'] if q['ref']==ref)['nets']['2']=net
    b.Add(p.NETINFO_ITEM(b,'GND'));pad(chip,'5','GND',(14000000,18000000),True)
    cfg['parts'][0]['nets']['5']='GND';cfg['plane_nets']=['GND']
    support=fp('C_RETURN',(11000000,18000000));pad(support,'1','GND',(11000000,18000000),True)
    cfg['parts'].append({'ref':'C_RETURN','region':'controller','nets':{'1':'GND'}})
    return b,cfg
