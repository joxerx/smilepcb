"""Native geometry/composition bridge for the explicitly selected synthetic case."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from smilepcb import native_worker as w

p = w.p


def box(bounds, margin=0):
    return [p.ToMM(bounds.GetLeft()) - margin, p.ToMM(bounds.GetTop()) - margin,
            p.ToMM(bounds.GetRight()) + margin, p.ToMM(bounds.GetBottom()) + margin]


def run(request):
    board = p.LoadBoard(request['board'])
    before = w.snapshot(board)
    assert len(before['footprints']) == 53 and len(before['tracks']) == 410
    assert {f['ref'] for f in before['footprints'].values()} >= {'U_MCU', 'U_RGB', 'LED12'}
    if request['operation'] == 'context':
        fp = p.FootprintLoad(str(Path(request['board']).parent / 'SmilePCB_Lab.pretty'), request['footprint'].split(':')[1])
        assert fp
        fp.SetPosition(w.point([0, 0]))
        # Conservative whole-footprint boxes include pads and fabrication body,
        # excluding text. Existing zones are refilled around the new pads/holes.
        return {'outline': box(board.GetBoardEdgesBoundingBox()),
                'encoder_box': box(fp.GetBoundingBox(False, False)),
                'encoder_pads': {pad.GetNumber(): [p.ToMM(pad.GetPosition().x), p.ToMM(pad.GetPosition().y)]
                                 for pad in fp.Pads() if pad.GetNumber() in ('A', 'B')},
                'components': [box(fp.GetBoundingBox(False, False), .4) for fp in board.GetFootprints()],
                'copper': [box(t.GetBoundingBox(), .4) for t in board.GetTracks()]}
    if request['operation'] != 'compose':
        raise ValueError('Unknown encoder operation')
    addition = request['part']
    for net in addition['nets'].values():
        if board.FindNet(net) is None:
            board.Add(p.NETINFO_ITEM(board, net))
    library, name = addition['footprint'].split(':')
    encoder = p.FootprintLoad(str(Path(request['board']).parent / 'SmilePCB_Lab.pretty'), name)
    assert encoder
    encoder.SetFPID(p.LIB_ID(library, name))
    encoder.SetReference(addition['ref'])
    encoder.SetValue(addition['value'])
    encoder.SetPosition(w.point(addition['pcb']))
    for pad in encoder.Pads():
        if pad.GetNumber() in addition['nets']:
            pad.SetNet(board.FindNet(addition['nets'][pad.GetNumber()]))
    board.Add(encoder)
    path = p.KIID_PATH()
    for component in request['path'].strip('/').split('/'):
        path.push_back(p.KIID(component))
    encoder.SetPath(path)
    mcu = next(f for f in board.GetFootprints() if f.GetReference() == 'U_MCU')
    seen = set()
    for pad in mcu.Pads():
        number = pad.GetNumber()
        if number not in request['changes']:
            continue
        old, new = request['changes'][number]
        assert pad.GetNetname() == old
        assert all(t.GetNetname() != old for t in board.GetTracks())
        pad.SetNet(board.FindNet(new))
        seen.add(number)
    assert seen == set(request['changes'])
    board.BuildConnectivity()
    p.ZONE_FILLER(board).Fill(board.Zones())
    assert p.SaveBoard(request['board'], board)
    return w.snapshot(p.LoadBoard(request['board']))


if __name__ == '__main__':
    print(json.dumps(run(json.load(sys.stdin))))
