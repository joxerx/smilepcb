from copy import deepcopy
from smilepcb.keyboard_routing.axis_replay import replay_track_axis


def axis():
    return [dict(kind='track', layer=0, end_layer=0, start=[0, 0], end=[10, 0]),
            dict(kind='track', layer=0, end_layer=0, start=[10, 0], end=[20, 10])]


def run(edges, clear=lambda *a: True, accept=lambda *a: True):
    return replay_track_axis(edges, bounds=[-1, -1, 25, 25], layers=(0, 2),
                             edge_clear=clear, goal_accept=accept)


def test_replay_rechecks_both_copper_and_complete_attachments():
    events = []
    edges = axis(); before = deepcopy(edges)
    assert run(edges, lambda a, z, f: events.append((a, z, f)) or True,
               lambda e: events.append('attachments') or True)['accepted']
    assert len(events) == 3 and events[-1] == 'attachments' and edges == before
    assert not run(edges, accept=lambda e: False)['accepted']


def test_obstruction_prevents_attachment_attempt():
    def forbidden(_): raise AssertionError('Blocked body must not be attached')
    assert not run(axis(), clear=lambda *a: False, accept=forbidden)['accepted']


def test_discontinuous_non_octilinear_and_outside_axes_reject():
    for key, value in [('start', [9, 0]), ('end', [20, 9]), ('end', [30, 20]),
                       ('kind', 'via'), ('end_layer', 2)]:
        edges = axis(); edges[-1][key] = value
        assert not run(edges)['accepted']


def test_trim_occupied_head_and_tail_uses_only_existing_contiguous_edges():
    from smilepcb.keyboard_routing.axis_replay import trim_track_axis_endpoints
    edges=[dict(kind='track',layer=0,end_layer=0,start=[x,0],end=[x+1000000,0],payload=None) for x in range(0,5000000,1000000)]
    before=deepcopy(edges)
    out=trim_track_axis_endpoints(edges,free=lambda point,face:point[0] not in (0,5000000))
    assert out==edges[1:4] and edges==before
    out[0]['start'][0]=-1
    assert edges==before


def test_trim_keeps_free_original_ports_without_querying_steered_points():
    from smilepcb.keyboard_routing.axis_replay import trim_track_axis_endpoints
    edges=[dict(kind='track',layer=2,end_layer=2,start=[0,0],end=[2000000,2000000])]
    queried=[]
    def free(point,face):queried.append((point,face));return True
    assert trim_track_axis_endpoints(edges,free=free)==edges
    assert set(queried)=={((0,0),2),((2000000,2000000),2)}


def test_trim_all_blocked_or_only_one_free_vertex_returns_no_axis():
    from smilepcb.keyboard_routing.axis_replay import trim_track_axis_endpoints
    edges=[dict(kind='track',layer=0,end_layer=0,start=[0,0],end=[3000000,0])]
    assert trim_track_axis_endpoints(edges,free=lambda *args:False) is None
    assert trim_track_axis_endpoints(edges,free=lambda point,face:point==(0,0)) is None


def test_trim_measures_remaining_physical_arclength_without_jumping_edges():
    from smilepcb.keyboard_routing.axis_replay import trim_track_axis_endpoints
    edges=[dict(kind='track',layer=0,end_layer=0,start=[-1000000,0],end=[0,0]),
           dict(kind='track',layer=0,end_layer=0,start=[0,0],end=[1000000,1000000]),
           dict(kind='track',layer=0,end_layer=0,start=[1000000,1000000],end=[2000000,1000000])]
    free=lambda point,face:point[0]>=0
    assert trim_track_axis_endpoints(edges,free=free,minimum_run_nm=2400000)==edges[1:]
    assert trim_track_axis_endpoints(edges,free=free,minimum_run_nm=2500000) is None


def test_trim_rejects_discontinuous_multiface_and_via_payload_axes():
    from smilepcb.keyboard_routing.axis_replay import trim_track_axis_endpoints
    for field,value in [('kind','via'),('layer',2),('end_layer',2),('start',[9,0]),('payload',{'operations':['via']})]:
        edges=axis();edges[-1][field]=value
        assert trim_track_axis_endpoints(edges,free=lambda *args:True,minimum_run_nm=1) is None
