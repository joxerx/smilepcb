"""Fail if first-clear USB rejection discards a recoverable fixed MCU pose."""
import copy
import importlib.util
import json
import sys
import time
from pathlib import Path
from smilepcb.native_worker import p, snapshot
from smilepcb.keyboard_routing.usb import route_controller_usb
from smilepcb.keyboard_native import verify_usb_pair
from smilepcb.keyboard_routing.generation import generation_profile

out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
def vec(x,y):return p.VECTOR2I(round(x*1e6),round(y*1e6))
def fixture(back=False):
    b=p.BOARD();b.SetCopperLayerCount(2);r=b.GetDesignSettings()
    r.m_MinClearance=150000;r.m_TrackMinWidth=150000;r.m_CopperEdgeClearance=200000;r.m_HoleClearance=250000
    r.m_NetSettings.GetDefaultNetclass().SetClearance(150000)
    for net in ('USB_DM','USB_DP','GND'):b.Add(p.NETINFO_ITEM(b,net))
    for a,z in (((0,0),(40,0)),((40,0),(40,30)),((40,30),(0,30)),((0,30),(0,0))):
        e=p.PCB_SHAPE(b);e.SetShape(p.SHAPE_T_SEGMENT);e.SetStart(vec(*a));e.SetEnd(vec(*z));e.SetLayer(p.Edge_Cuts);e.SetWidth(50000);b.Add(e)
    for ref,x in (('U_MCU',5),('J_UDB',26),('FIXED',35)):
        f=p.FOOTPRINT(b);f.SetReference(ref);b.Add(f);f.SetPosition(vec(x,10))
        for i,net in enumerate(('USB_DM','USB_DP') if ref!='FIXED' else ('GND',),1):
            d=p.PAD(f);d.SetNumber(str(i));d.SetAttribute(p.PAD_ATTRIB_SMD);d.SetShape(p.PAD_SHAPE_RECT)
            layers=p.LSET();layers.AddLayer(p.F_Cu);d.SetLayerSet(layers);d.SetSize(vec(.3,.3));d.SetPosition(vec(x,9+i));d.SetNet(b.FindNet(net));f.Add(d)
        if back:f.Flip(f.GetPosition(),p.FLIP_DIRECTION_LEFT_RIGHT)
    # Immutable imported copper unrelated to the USB placement trial.
    t=p.PCB_TRACK(b);t.SetStart(vec(33,20));t.SetEnd(vec(35,20));t.SetWidth(200000);t.SetLayer(p.F_Cu);t.SetNet(b.FindNet('GND'));b.Add(t)
    g=next(f for f in b.GetFootprints() if f.GetReference()=='J_UDB')
    cfg={'parts':[{'ref':f.GetReference(),'pcb':[f.GetPosition().x/1e6,f.GetPosition().y/1e6],'rotation':f.GetOrientationDegrees(),'side':b.GetLayerName(f.GetLayer())} for f in b.GetFootprints()]}
    return b,g,cfg

def select(b,g,cfg,**kwargs):
    if importlib.util.find_spec('smilepcb.stm32.usb_placement') is None:
        # Baseline's first-clear behavior: actual proof rejects the whole pose.
        return route_controller_usb(b,deadline=kwargs['deadline'])
    from smilepcb.stm32.usb_placement import select_usb_placement
    return select_usb_placement(b,g,cfg,preferred_nm=(15000000,10000000),**kwargs)

def legal(g):
    # Native pad envelopes must remain inside the fixture outline.
    return all(200000<d.GetPosition().x<39800000 and 200000<d.GetPosition().y<29800000 for d in g.Pads())

report={}
with generation_profile('dsn-process-v2'):
    for back in (False,True):
        b,g,cfg=fixture(back);before=snapshot(b)
        result=select(b,g,cfg,legal=legal,deadline=time.monotonic()+15)
        assert result['accepted'],result
        assert not result['trials'][0]['accepted'],result
        assert 'envelope' in result['trials'][0]['reason'],result
        after=snapshot(b);uid=g.m_Uuid.AsString()
        assert all(after['footprints'][u]==s for u,s in before['footprints'].items() if u!=uid)
        assert all(after['tracks'][u]==s for u,s in before['tracks'].items())
        lengths=verify_usb_pair(b);assert all(0<x<20 for x in lengths.values())
        part=next(q for q in cfg['parts'] if q['ref']=='J_UDB')
        assert part['pcb']==[g.GetPosition().x/1e6,g.GetPosition().y/1e6]
        assert part['rotation']==g.GetOrientationDegrees() and part['side']==b.GetLayerName(g.GetLayer())
        from smilepcb.stm32.usb_placement import verify_prepared_usb
        assert verify_prepared_usb(b,result['usb'],deadline=time.monotonic()+5)['accepted']
        # An actual downstream rejection must retain the accepted USB checkpoint;
        # rerouting here would add duplicate copper or fail its native proof.
        from smilepcb.keyboard_routing import ordered_controller
        original=ordered_controller.ordered_nets
        def reject_later(cfg):raise ValueError('Injected downstream gate rejection')
        ordered_controller.ordered_nets=reject_later
        try:
            _,prepared=ordered_controller.prepare_ordered_controller(b,cfg,deadline=time.monotonic()+5,prepared_usb=result['usb'])
        finally:ordered_controller.ordered_nets=original
        assert prepared['usb']['accepted'] and prepared['reason']=='Injected downstream gate rejection',prepared
        assert snapshot(b)==after
        g.Move(vec(.5,0))
        try:verify_prepared_usb(b,result['usb'],deadline=time.monotonic()+5)
        except ValueError:pass
        else:raise AssertionError('Stale prepared USB proof accepted a changed connector')
        report['retry_back' if back else 'retry']=result
    # Changes omitted by native_worker.snapshot must invalidate reuse too.
    from smilepcb.routing_quality import using_quality_profile
    from contextlib import nullcontext
    report['changed_context']={}
    for mutation in ('rules','outline','layers','local_clearance','keepout','artwork','generation','quality'):
        b,g,cfg=fixture();selected=select(b,g,cfg,legal=legal,deadline=time.monotonic()+10)
        assert selected['accepted'],selected
        before=snapshot(b)
        scope=nullcontext()
        if mutation=='rules':b.GetDesignSettings().m_MinClearance=5000000
        elif mutation=='outline':
            for edge in b.GetDrawings():edge.Move(vec(50,0))
        elif mutation=='layers':b.SetCopperLayerCount(4)
        elif mutation=='local_clearance':next(iter(g.Pads())).SetLocalClearance(5000000)
        elif mutation=='keepout':
            z=p.ZONE(b);z.SetIsRuleArea(True);z.SetLayer(p.F_Cu);z.SetDoNotAllowTracks(True)
            poly=z.Outline();poly.NewOutline()
            for x,y in ((9,8),(11,8),(11,13),(9,13)):poly.Append(round(x*1e6),round(y*1e6))
            b.Add(z)
        elif mutation=='artwork':
            art=p.PCB_SHAPE(b);art.SetShape(p.SHAPE_T_SEGMENT);art.SetStart(vec(10,8));art.SetEnd(vec(10,13));art.SetWidth(500000);art.SetLayer(p.F_Cu);b.Add(art)
        elif mutation=='generation':scope=generation_profile('native')
        elif mutation=='quality':scope=using_quality_profile('turns')
        assert snapshot(b)==before,mutation
        with scope:
            try:verify_prepared_usb(b,selected['usb'],deadline=time.monotonic()+5)
            except ValueError as error:report['changed_context'][mutation]=str(error)
            else:raise AssertionError('Prepared USB accepted changed '+mutation)
        assert snapshot(b)==before,'Verification changed copper or footprints'
    b,g,cfg=fixture();before=snapshot(b);old=copy.deepcopy(cfg)
    result=select(b,g,cfg,legal=lambda g:g.GetPosition()==vec(26,10) and g.GetOrientationDegrees()==0,deadline=time.monotonic()+5)
    report['rollback']=not result['accepted'] and bool(result['trials']) and snapshot(b)==before and cfg==old
    result=select(b,g,cfg,legal=legal,deadline=time.monotonic()-1)
    report['expired']=not result['accepted'] and snapshot(b)==before and cfg==old
    def broken(g):raise RuntimeError('Injected legality failure')
    try:select(b,g,cfg,legal=broken,deadline=time.monotonic()+5)
    except RuntimeError:report['exception']=snapshot(b)==before and cfg==old
    else:report['exception']=False
assert report['rollback'] and report['expired'] and report['exception'],report
(out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
