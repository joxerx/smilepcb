"""Routing phases derived from current physical pin roles, never net spelling."""


def ordered_nets(cfg):
    parts={part['ref']:part for part in cfg['parts']}
    mcu=parts['U_MCU'];gpio=cfg['layout']['gpio']
    rows=[mcu['nets'][cfg['mcu_gpio_pads'][pin]] for pin in gpio['rows']]
    columns=[mcu['nets'][cfg['mcu_gpio_pads'][pin]] for pin in gpio['cols']]
    chain=cfg['layout']['rgb_order'] if cfg['layout']['rgb'] else []
    rgb=([parts['LED'+str(chain[0])]['nets']['3']]+
         [parts['LED'+str(key)]['nets']['2'] for key in chain[:-1]]) if chain else []
    excluded=set(rows+columns+rgb)|set(cfg['plane_nets'])
    control=sorted({net for part in cfg['parts'] if part['region']=='controller'
                    for net in part['nets'].values() if not net.startswith(('unconnected-','NC_'))}-excluded)
    return [('control',control),('rows',rows),('columns',columns),('rgb',rgb)]
