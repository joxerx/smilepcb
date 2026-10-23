"""One public STM32F072/MX/Mini-E circuit model for CAD and QMK."""
from copy import deepcopy
import math

from . import schematic as s
from .circuit import part
from .keyboard_placement import controller_origin,rgb_chain

NAME = 'keyboard'
SW_SYMBOL = 'KeyboardParts:MX_HS'
LED_SYMBOL = 'KeyboardParts:MINI_E'
# Outward-rounded union of this recipe's public native component courtyards
# (KiCad 10.0.7; test points use their native body/pad bounds). Creation checks
# the actual loaded geometry against this envelope before accepting placement.
CONTROLLER_BOUNDS = [-22.05,-6.05,43.525,8.775]


def design(layout, *, controller_origin_mm=None):
    layout = deepcopy(layout)
    count = len(layout['keys'])
    # Engineering allocations retain the earlier conservative 60 mA/LED model.
    brightness = max((b for b in range(256) if 100 + count * (2 + 60*b/255) <= 500), default=-1)
    if layout['rgb'] and brightness < 0:
        raise ValueError('LED idle allocation exceeds the USB power budget')
    power = {'brightness_max': brightness if layout['rgb'] else 0,
             'allocated_ma': math.ceil(100 + count*(2+60*brightness/255)) if layout['rgb'] else 100,
             'usb_budget_ma': 500, 'rgb_power_off_before_configuration': True,
             'hardware_qualified': False, 'basis': 'engineering_allocation_not_guaranteed_maximum'}
    parts = []

    def add(ref, symbol, fp, nets, position, value=None, *, side='F.Cu', rotation=0, no_connect=None, region='controller'):
        index = len(parts)
        component = part(ref, symbol, fp, nets, position,
                         (30+(index % 16)*48, 30+(index // 16)*32), value, no_connect=no_connect)
        component.update(side=side, rotation=rotation, region=region)
        parts.append(component)
        return component

    symbol = s.symbol('MCU_ST_STM32F0:STM32F072CBTx')
    pin_names = {str(s.one(p, 'number')[1]): str(s.one(p, 'name')[1])
                 for unit in s.children(symbol, 'symbol') for p in s.children(unit, 'pin')}
    gpio_pads = {v[1:]: n for n, v in pin_names.items() if v.startswith('P')}
    nets = {n: 'NC_MCU_'+n for n in pin_names}
    for n, name in pin_names.items():
        if name in ('VDD', 'VDDIO2', 'VDDA', 'VBAT'): nets[n] = '+3V3'
        elif name in ('VSS', 'VSSA'): nets[n] = 'GND'
    nets.update({'7':'NRST', '32':'USB_DM', '33':'USB_DP', '34':'SWDIO', '37':'SWCLK', '44':'BOOT0'})
    for group, prefix in [('rows', 'ROW'), ('cols', 'COL')]:
        for i, gpio in enumerate(layout['gpio'][group]):
            pad = gpio_pads[gpio]
            if not nets[pad].startswith('NC_'): raise ValueError('Reserved MCU pin conflict')
            nets[pad] = prefix+str(i)
    if layout['rgb']:
        nets[gpio_pads['B8']] = 'RGB_DATA'
        nets[gpio_pads['B9']] = 'RGB_ENABLE'
        nets[gpio_pads['F1']] = 'RGB_OUTPUT_ENABLE'
    add('U_MCU', 'MCU_ST_STM32F0:STM32F072CBTx', 'Package_QFP:LQFP-48_7x7mm_P0.5mm', nets,
        (0, 0), 'STM32F072CBT6', no_connect=[n for n, v in nets.items() if v.startswith('NC_')])
    add('J_UDB', 'Connector_Generic_MountingPin:Conn_01x04_MountingPin',
        'Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal',
        {'1':'+5V','2':'USB_DM','3':'USB_DP','4':'GND','MP':'GND'}, (16, 0),
        'SM04B-SRSS-TB / complete UDB C5-JSH', rotation=90)
    add('U_REG', 'Regulator_Linear:AP2112K-3.3', 'Package_TO_SOT_SMD:SOT-23-5',
        {'1':'+5V','2':'GND','3':'+5V','4':'NC_REG','5':'+3V3'}, (8, -4.3), no_connect=['4'])
    caps = [('C_IN','+5V','1uF',(12,-4.4),'F.Cu'), ('C_OUT','+3V3','1uF',(8,-4.4),'B.Cu'),
            ('C_VDD1','+3V3','100nF',(2.5,3),'B.Cu'), ('C_VDD2','+3V3','100nF',(-2.5,-3),'B.Cu'),
            ('C_IO2','+3V3','100nF',(3,-2.5),'B.Cu'), ('C_VDDA','+3V3','100nF',(-3,2.5),'B.Cu'),
            ('C_VBAT','+3V3','100nF',(-7,-3),'B.Cu'), ('C_BULK','+3V3','4.7uF',(12,-4.4),'B.Cu'),
            ('C_NRST','NRST','100nF',(-8,4.5),'B.Cu')]
    for ref, net, value, xy, side in caps:
        add(ref,'Device:C','Capacitor_SMD:C_0603_1608Metric',{'1':net,'2':'GND'},xy,value,side=side)
    add('R_BOOT','Device:R','Resistor_SMD:R_0603_1608Metric',{'1':'BOOT0','2':'GND'},(8,4.5),'10k',side='B.Cu')
    add('R_NRST','Device:R','Resistor_SMD:R_0603_1608Metric',{'1':'+3V3','2':'NRST'},(-8,0),'10k',side='B.Cu')
    # Tiny accessible pads replace a tall programming header; no extra connector.
    for i, (net, xy) in enumerate([('SWDIO',(-21,-5)),('SWCLK',(-17,-5)),
                                 ('NRST',(-13,-5)),('GND',(-21,4)),('+3V3',(-21,7)),('BOOT0',(8,7))],1):
        add('TP'+str(i),'Connector:TestPoint','TestPoint:TestPoint_Pad_D1.0mm',{'1':net},xy,net)
    add('#FLG1','power:PWR_FLAG','',{'1':'+5V'},(0,0))
    add('#FLG2','power:PWR_FLAG','',{'1':'GND'},(0,0))
    if layout['rgb']:
        add('U_RGB_POWER','Power_Management:TPS22917DBV','Package_TO_SOT_SMD:SOT-23-6',
            {'1':'+5V','2':'GND','3':'RGB_ENABLE','4':'RGB_CT','5':'RGB_QOD','6':'+5V_RGB'},(29,-2))
        add('U_RGB','74xGxx:74AHCT1G126','Package_TO_SOT_SMD:SOT-23-5',
            {'1':'RGB_OUTPUT_ENABLE','2':'RGB_DATA','3':'GND','4':'RGB_BUFFER','5':'+5V'},(29,4),'SN74AHCT1G126DBV')
        for ref, net, value, xy in [('C_RGB_IN','+5V','1uF',(23,-5)),
                                     ('C_RGB_CT','RGB_CT','10nF',(36,-2)),
                                     ('C_RGB_BULK','+5V_RGB','10uF',(42,-2)),
                                     ('C_RGB_BUF','+5V','100nF',(23,5))]:
            add(ref,'Device:C','Capacitor_SMD:C_0603_1608Metric',{'1':net,'2':'GND'},xy,value)
        add('R_RGB_ENABLE','Device:R','Resistor_SMD:R_0603_1608Metric',{'1':'RGB_ENABLE','2':'GND'},(36,3),'100k')
        add('R_RGB_OE','Device:R','Resistor_SMD:R_0603_1608Metric',{'1':'RGB_OUTPUT_ENABLE','2':'GND'},(23,8),'100k')
        add('R_RGB_QOD','Device:R','Resistor_SMD:R_0603_1608Metric',{'1':'RGB_QOD','2':'+5V_RGB'},(42,3),'1k')
        add('R_RGB','Device:R','Resistor_SMD:R_0603_1608Metric',{'1':'RGB_BUFFER','2':'RGB_0'},(36,7),'330')
        add('R_RGB_INPUT','Device:R','Resistor_SMD:R_0603_1608Metric',{'1':'RGB_DATA','2':'GND'},(23,0),'100k')
        add('R_RGB_OUTPUT','Device:R','Resistor_SMD:R_0603_1608Metric',{'1':'RGB_0','2':'GND'},(42,7),'100k')
    rgb_routing=None
    if layout['rgb']:
        origin=(controller_origin({'layout':layout,'controller_bounds':CONTROLLER_BOUNDS})
                if controller_origin_mm is None else controller_origin_mm)
        driver=next(p for p in parts if p['ref']=='R_RGB')
        source=[origin[i]+driver['pcb'][i] for i in range(2)]
        layout['rgb_order'],rgb_routing=rgb_chain(layout,source)
    led_index = {key: index for index, key in enumerate(layout['rgb_order'])}
    for key in layout['keys']:
        i = key['id']; r,c = key['matrix']; x,y = key['center_mm']; width,height = key['size_u']
        add('SW'+str(i),SW_SYMBOL,'KeyboardParts:MX_HS',{'1':'COL'+str(c),'2':'KEY'+str(i)},
            (x,y), 'MX hot-swap',side='B.Cu',region='matrix')
        add('D'+str(i),'Device:D','Diode_SMD:D_SOD-123',{'1':'ROW'+str(r),'2':'KEY'+str(i)},
            (x+4,y-9),'1N4148W',side='B.Cu',region='matrix')
        if width >= 2 or height == 2:
            size = '6.25' if width == 6.25 else '7.00' if width == 7 else '2.00'
            add('STAB'+str(i),'Mechanical:MountingHole','KeyboardParts:STAB_'+size,
                {},(x,y),'MX PCB stabilizer',rotation=90 if height==2 else 0,region='mechanical')
        if layout['rgb']:
            index = led_index[i]
            add('LED'+str(i),LED_SYMBOL,'KeyboardParts:MINI_E',
                {'1':'+5V_RGB','2':'RGB_'+str(index+1) if index<count-1 else 'NC_RGB_END',
                 '3':'RGB_'+str(index),'4':'GND'},(x,y+5.08),'SK6812MINI-E Rev02',
                side='B.Cu',rotation=180 if r%2==0 else 0,no_connect=['2'] if index==count-1 else None,region='rgb')
            add('C_LED'+str(i),'Device:C','Capacitor_SMD:C_0603_1608Metric',
                {'1':'+5V_RGB','2':'GND'},(x+5.8,y+5.08),'100nF',side='B.Cu',region='rgb')
    qmk = {'keyboard_name':layout['name'],'manufacturer':'SmilePCB','maintainer':'joxerx',
           'processor':'STM32F072','bootloader':'stm32-dfu','diode_direction':'COL2ROW',
           'matrix_pins':{group:layout['gpio'][group] for group in ('rows','cols')},
           'usb':{'vid':'0xFEED','pid':'0x0001','device_version':'0.0.1','max_power':500},
           'layouts':{'LAYOUT':{'layout':[{'matrix':k['matrix'],'x':k['position_u'][0],'y':k['position_u'][1],
                                         'w':k['size_u'][0],'h':k['size_u'][1]} for k in layout['keys']]}}}
    if layout['rgb']:
        by_id = {k['id']:k for k in layout['keys']}
        qmk.update(features={'rgb_matrix':True},ws2812={'pin':'B8','driver':'bitbang'},
                   rgb_matrix={'driver':'ws2812','max_brightness':brightness,'sleep':True,
                               'default':{'on':False,'val':brightness},
                               'layout':[{'matrix':by_id[i]['matrix'],
                                          'x':round(by_id[i]['center_mm'][0]*224/layout['size_mm'][0]),
                                          'y':round(by_id[i]['center_mm'][1]*64/layout['size_mm'][1]),'flags':4}
                                         for i in layout['rgb_order']]})
    return {'schema':1,'profile':layout['profile'],'layout':layout,'parts':parts,'qmk':qmk,'plane_nets':['GND'],
            'mcu_gpio_pads':gpio_pads,'power':power,'rgb_routing':rgb_routing,'controller_bounds':list(CONTROLLER_BOUNDS),
            'outline':[[-2,-2],[layout['size_mm'][0]+2,-2],
                       [layout['size_mm'][0]+2,layout['size_mm'][1]+2],[-2,layout['size_mm'][1]+2]],
            'daughterboard':{'assembly':'Unified Daughterboard C5-JSH, complete unmodified assembly',
                'revision':'2b6c52aa2de6f9f60ca99c6250e4bdf01e8f4eea',
                'url':'https://github.com/Unified-Daughterboard/UDB-C-JSH/tree/2b6c52aa2de6f9f60ca99c6250e4bdf01e8f4eea',
                'cable':'4-wire 1:1 electrical mapping; 1=5V, 2=D-, 3=D+, 4=GND',
                'case_usb_position':'top left above Esc; case assembly not qualified'}}
