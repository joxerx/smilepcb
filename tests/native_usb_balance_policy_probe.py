"""Generic native USB policy, ownership, contact and pre-mutation controls."""
import json,sys,time
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.usb_pair import balance_pair
from smilepcb.usb_policy import DEFAULT_USB_MAX_LENGTH_NM
from smilepcb.keyboard_routing.local_router import LocalRouter
from smilepcb.keyboard_routing.generation import generation_profile
from smilepcb.keyboard_routing import usb

out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True);report={}
def vec(x,y):return p.VECTOR2I(round(x*1000000),round(y*1000000))
def base():
    b=p.BOARD();b.SetCopperLayerCount(2);r=b.GetDesignSettings()
    r.m_MinClearance=150000;r.m_TrackMinWidth=150000;r.m_CopperEdgeClearance=200000;r.m_HoleClearance=250000
    r.m_NetSettings.GetDefaultNetclass().SetClearance(150000)
    for net in ('USB_DM','USB_DP','wall'):b.Add(p.NETINFO_ITEM(b,net))
    for a,z in (((0,0),(30,0)),((30,0),(30,30)),((30,30),(0,30)),((0,30),(0,0))):
        e=p.PCB_SHAPE(b);e.SetShape(p.SHAPE_T_SEGMENT);e.SetStart(vec(*a));e.SetEnd(vec(*z));e.SetLayer(p.Edge_Cuts);e.SetWidth(50000);b.Add(e)
    return b
def footprint(b,ref):
    f=p.FOOTPRINT(b);f.SetReference(ref);b.Add(f);return f
def pad(b,f,number,net,x,y,size=.2):
    d=p.PAD(f);d.SetNumber(number);d.SetAttribute(p.PAD_ATTRIB_SMD);d.SetShape(p.PAD_SHAPE_RECT)
    layers=p.LSET();layers.AddLayer(p.F_Cu);d.SetLayerSet(layers);d.SetSize(vec(size,size));d.SetPosition(vec(x,y));d.SetNet(b.FindNet(net));f.Add(d);return d
def track(b,net,a,z):
    t=p.PCB_TRACK(b);t.SetStart(vec(*a));t.SetEnd(vec(*z));t.SetWidth(200000);t.SetLayer(p.F_Cu);t.SetNet(b.FindNet(net));b.Add(t);return t
def pair(*,high=False,contact=False,locked=False):
    b=base();f=footprint(b,'U_MCU');g=footprint(b,'J_UDB')
    for i,(net,y,end) in enumerate((('USB_DM',10,11),('USB_DP',13,11.6)),1):
        pad(b,f,str(i),net,5,y);pad(b,g,str(i),net,end,y);t=track(b,net,(5,y),(end,y))
        if net=='USB_DM':owned=t
    obstacle=pad(b,footprint(b,'GUARD'),'1','wall',5,9.62)
    if high:obstacle.SetLocalClearance(400000)
    if contact:pad(b,footprint(b,'CONTACT'),'1','USB_DM',8,10,.1)
    if locked:owned.SetLocked(True)
    return b,owned.m_Uuid.AsString()
def checker(b):
    oracle=LocalRouter(b,deadline=time.monotonic()+10,compact=True)
    def clear(a,z,width,layer,net):return oracle.clear(p.SHAPE_SEGMENT(p.VECTOR2I(*a),p.VECTOR2I(*z),width),layer,net)
    return clear
def rejection(b,uid,**kwargs):
    before=snapshot(b)
    try:balance_pair(b,('USB_DP','USB_DM'),editable_ids=[uid],**kwargs)
    except (ValueError,TimeoutError) as error:return {'rejected':True,'unchanged':snapshot(b)==before,'error':str(error)}
    return {'rejected':False,'unchanged':snapshot(b)==before}

with generation_profile('dsn-process-v2'):
    b,uid=pair();report['legacy_default']=rejection(b,uid)
    b,uid=pair();before=snapshot(b);r=balance_pair(b,('USB_DP','USB_DM'),editable_ids=[uid],clear_segment=checker(b),deadline=time.monotonic()+10);after=snapshot(b)
    report['creation_callback']={'changed':r['changed'],'removed_only_owned':r['removed_ids']==[uid],
        'other_tracks_preserved':all(after['tracks'].get(k)==v for k,v in before['tracks'].items() if k!=uid),
        'footprints_preserved':before['footprints']==after['footprints'],'added_receipt_exact':set(after['tracks'])-set(before['tracks'])==set(r['added_ids']),
        'repair_skew':abs(r['lengths_mm']['USB_DP']-r['lengths_mm']['USB_DM'])<.25}
    for case,kwargs in (('higher_own_clearance',{'high':True}),('interior_contact',{'contact':True}),('locked_owned',{'locked':True})):
        b,uid=pair(**kwargs);report[case]=rejection(b,uid,clear_segment=checker(b),deadline=time.monotonic()+10)
    b,uid=pair();calls=[]
    def forbidden(*args):calls.append(args);raise AssertionError('Expired balance called checker')
    report['expired']=rejection(b,uid,clear_segment=forbidden,deadline=time.monotonic()-1);report['expired']['checker_not_called']=not calls
    b,uid=pair();before=snapshot(b)
    def timeout(*args):raise TimeoutError('Checked callback deadline')
    report['callback_timeout']=rejection(b,uid,clear_segment=timeout,deadline=time.monotonic()+10)

scope={}
for profile in ('native','dsn-safe-v1','dsn-process-v2'):
    b=base();f=footprint(b,'U_MCU');g=footprint(b,'J_UDB')
    for i,(net,y) in enumerate((('USB_DM',10),('USB_DP',11)),1):
        pad(b,f,str(i),net,5,y);pad(b,g,str(i),net,10,y)
    original=usb.balance_pair;seen=[];end=time.monotonic()+10
    def observe(board,nets,**kwargs):
        seen.append({'keys':sorted(kwargs),'callback_callable':callable(kwargs.get('clear_segment')),'deadline_exact':kwargs.get('deadline')==end,'max_length_nm':kwargs.get('max_length_nm')})
        return original(board,nets,**kwargs)
    usb.balance_pair=observe
    try:
        with generation_profile(profile):result=usb.route_controller_usb(b,deadline=end)
    finally:usb.balance_pair=original
    expected=['clear_segment','deadline','editable_ids','max_length_nm'] if profile=='dsn-process-v2' else ['editable_ids','max_length_nm']
    scope[profile]={'accepted':result['accepted'],'one_balance':len(seen)==1,'keyword_scope_exact':bool(seen) and seen[0]['keys']==expected,
        'default_limit_exact':bool(seen) and seen[0]['max_length_nm']==DEFAULT_USB_MAX_LENGTH_NM,'observed':seen,'reason':result.get('reason')}
    if profile=='dsn-process-v2':scope[profile]['callback_and_deadline']=bool(seen) and seen[0]['callback_callable'] and seen[0]['deadline_exact']
report['caller_profile_scope']=scope
(out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
assert report['legacy_default']['rejected'] and report['legacy_default']['unchanged'],report
assert all(report['creation_callback'].values()),report
for case in ('higher_own_clearance','interior_contact','locked_owned','expired','callback_timeout'):
    assert report[case]['rejected'] and report[case]['unchanged'],report
assert report['expired']['checker_not_called'],report
assert all(all(q[k] for k in ('accepted','one_balance','keyword_scope_exact','default_limit_exact')) and q.get('callback_and_deadline',True) for q in scope.values()),report
print(json.dumps(report))
