"""Apply geometric matrix allocation before any matrix copper is routed."""
from collections import defaultdict
from smilepcb.native_worker import p
from smilepcb.controller_gpio import assign_matrix_gpio
from smilepcb.keyboard_layout import ROW_PINS,COL_PINS


def apply_matrix_gpio(board,cfg,*,excluded_gpios=()):
    chip=next(f for f in board.GetFootprints() if f.GetReference()=='U_MCU')
    part=next(part for part in cfg['parts'] if part['ref']=='U_MCU')
    names={prefix+str(i) for group,prefix in [('rows','ROW'),('cols','COL')]
           for i in range(len(cfg['layout']['gpio'][group]))}
    assignable={part['nets'][number] for gpio,number in cfg['mcu_gpio_pads'].items()
                if gpio in set(ROW_PINS)|set(COL_PINS) and
                (part['nets'][number] in names or part['nets'][number].startswith('NC_') or
                 number in part.get('no_connect',()) and part['nets'][number].startswith('unconnected-'))}
    if any(t.GetNetname() in assignable for t in board.GetTracks()):
        raise ValueError('Matrix GPIO allocation must precede editable matrix copper')
    pads={pad.GetNumber():pad for pad in chip.Pads() if pad.GetNumber()}
    locations={gpio:(pads[number].GetPosition().x,pads[number].GetPosition().y)
               for gpio,number in cfg['mcu_gpio_pads'].items() if number in pads}
    targets=defaultdict(list)
    for fp in board.GetFootprints():
        if fp.GetReference()=='U_MCU':continue
        for pad in fp.Pads():
            if pad.GetNumber() and pad.GetNetname() in names:
                targets[pad.GetNetname()].append((pad.GetPosition().x,pad.GetPosition().y))
    centroids={net:tuple(round(sum(v[i] for v in rows)/len(rows)) for i in (0,1)) for net,rows in targets.items()}
    updated,receipt=assign_matrix_gpio(cfg,locations,centroids,excluded_gpios=excluded_gpios)
    new_part=next(part for part in updated['parts'] if part['ref']=='U_MCU')
    for number,pad in pads.items():
        name=new_part['nets'][number];net=board.FindNet(name)
        if net is None:net=p.NETINFO_ITEM(board,name);board.Add(net)
        pad.SetNet(net)
    board.BuildConnectivity()
    return updated,receipt
