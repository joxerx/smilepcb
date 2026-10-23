"""Short local key-module wiring before long matrix and RGB connections."""
import time
from .local_router import LocalRouter,xy,distance
from .power import seed_plane_access


def connect_key_modules(board,cfg,*,budget_s=45,via_to_smd_clearance_nm=0):
    stop=time.monotonic()+budget_s
    original_ids={t.m_Uuid.AsString() for t in board.GetTracks()}
    router=LocalRouter(board,deadline=stop,compact=True)
    footprints={f.GetReference():f for f in board.GetFootprints()}
    report=[];returns=[]
    for key in cfg['layout']['keys']:
        index=str(key['id'])
        pairs=[('SW'+index,'2','D'+index,'2')]
        if cfg['layout']['rgb']:
            pairs += [('LED'+index,'1','C_LED'+index,'1'),('LED'+index,'4','C_LED'+index,'2')]
            returns += [d for d in footprints['C_LED'+index].Pads() if d.GetNumber()=='2']
        for a,na,b,nb in pairs:
            sources=[d for d in footprints[a].Pads() if d.GetNumber()==na]
            targets=[d for d in footprints[b].Pads() if d.GetNumber()==nb]
            choices=sorted((distance(xy(x.GetPosition()),xy(y.GetPosition())),j,k,x,y)
                           for j,x in enumerate(sources) for k,y in enumerate(targets))
            connected=False
            for _,_,_,x,y in choices:
                if time.monotonic()>=stop:break
                router.deadline=min(stop,time.monotonic()+.25)
                if router.connect(x,y):connected=True;break
            report.append({'key':key['id'],'from':[a,na],'to':[b,nb],'connected':connected})
    access=seed_plane_access(board,returns,deadline=stop,via_to_smd_clearance_nm=via_to_smd_clearance_nm)
    planes=set(cfg.get('plane_nets',()))
    return_ids=sorted(t.m_Uuid.AsString() for t in board.GetTracks()
                      if t.GetNetname() in planes and t.m_Uuid.AsString() not in original_ids)
    return {'connections':sum(v['connected'] for v in report),'attempts':report,
            'added_ids':router.added,'return_ids':return_ids,'plane_access':access,'budget_exhausted':time.monotonic()>=stop}


def seed_key_data_access(board,cfg,*,deadline=None,budget_s=70):
    """Atomically reserve physical two-face access for actual LED data pads."""
    from smilepcb.native_worker import p,snapshot
    from .native_graph import copper_graph
    from .safety import require_via_land_clearance
    if type(budget_s) not in (int,float) or not 0<budget_s<=70:
        raise ValueError('Key data access budget must be positive and at most 70 seconds')
    started=time.monotonic();stop=started+budget_s
    if deadline is not None:stop=min(stop,deadline)
    before=snapshot(board);original=set(before['tracks'])
    report={'accepted':False,'required':0,'connections':0,'ports':[],'added_ids':[],'failed':[]}
    try:
        pads=[]
        if cfg['layout']['rgb']:
            fps={f.GetReference():f for f in board.GetFootprints()}
            for key in cfg['layout']['keys']:
                fp=fps['LED'+str(key['id'])]
                pads.extend(d for d in fp.Pads() if d.GetNumber() in ('2','3') and d.GetNetname()
                            and not d.GetNetname().startswith(('NC_','unconnected-')))
        report['required']=len(pads)
        ports=seed_plane_access(board,pads,deadline=stop,
            track_width_nm=board.GetDesignSettings().m_TrackMinWidth or p.FromMM(.2),
            max_distance_nm=2000000,via_to_smd_clearance_nm=200000) if pads else []
        by_pad={(row['ref'],row['pin']):row for row in ports}
        report['ports']=[by_pad.get((d.GetParentFootprint().GetReference(),d.GetNumber()),
            {'ref':d.GetParentFootprint().GetReference(),'pin':d.GetNumber(),
             'net':d.GetNetname(),'connected':False,'via_id':None,'ids':[]}) for d in pads]
        for row in report['ports']:row.setdefault('via_id',None)
        report['failed']=[{key:row[key] for key in ('ref','pin','net')}
                          for row in report['ports'] if not row['connected']]
        if report['failed']:
            report['reason']='Incomplete key data access batch'
            return report
        if pads:
            graph=copper_graph(board,{d.GetNetname() for d in pads},stop)
            ids={node['item'].m_Uuid.AsString():i for i,node in enumerate(graph['nodes'])}
            for pad,row in zip(pads,report['ports']):
                if graph['find'](ids[pad.m_Uuid.AsString()])!=graph['find'](ids[row['via_id']]):
                    raise ValueError('LED data pad does not physically reach its access via')
            require_via_land_clearance(board,deadline=stop)
            if time.monotonic()>=stop:raise TimeoutError('Key data access budget exhausted')
        after=snapshot(board)
        if after['footprints']!=before['footprints'] or any(after['tracks'].get(uid)!=shape
                for uid,shape in before['tracks'].items()):
            raise ValueError('Key data access changed original geometry')
        report.update(accepted=True,connections=len(pads),added_ids=sorted(set(after['tracks'])-original))
    except TimeoutError as error:
        report['reason']=str(error)
    finally:
        if not report['accepted']:
            for item in list(board.GetTracks()):
                if item.m_Uuid.AsString() not in original:board.Delete(item)
            for row in report['ports']:row.update(connected=False,via_id=None,ids=[])
            report['added_ids']=[];report['connections']=0
        report['elapsed_s']=time.monotonic()-started
    return report
