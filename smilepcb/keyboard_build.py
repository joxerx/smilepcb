"""Compose and verify new KLE-driven STM32 keyboards."""
import csv
import json
import os
import shutil
import tempfile
from pathlib import Path
import time
import sys

from . import schematic as s
from .keyboard_layout import parse_layout
from .keyboard_model import design, NAME
from .keyboard_libraries import create_libraries
from .service import encode
from .lab_tools import worker, export_netlist

BRIDGE = Path(__file__).with_name('keyboard_native.py')


def routing_transaction(project, destination, attempt, previous, *, deadline=None):
    """Evaluate rip-up plus repair in a separate native project context."""
    candidate=Path(destination)/'project'
    shutil.copytree(project,candidate)
    independent=attempt(candidate)
    score=independent['pad_group_gaps']
    if type(score) is not int or score<0:raise ValueError('Invalid native connectivity score')
    accepted=score<previous['pad_group_gaps']
    if accepted:
        # Schematic, libraries, model and firmware inputs stay in the source
        # context. Only an improving saved PCB is eligible for replacement.
        with tempfile.NamedTemporaryFile(dir=project,prefix='.smilepcb-route-',suffix='.tmp',delete=False) as temporary:
            staged=Path(temporary.name)
        try:
            shutil.copyfile(candidate/(NAME+'.kicad_pcb'),staged)
            if deadline is not None and not time.perf_counter()<deadline:
                raise TimeoutError('Routing promotion exceeded the build budget')
            os.replace(staged,Path(project)/(NAME+'.kicad_pcb'))
        finally:staged.unlink(missing_ok=True)
    return {'accepted':accepted,'independent':independent if accepted else previous,
            'candidate_independent':independent,'candidate_project':str(candidate)}


def write_schematic(project, cfg, symbols):
    root = s.empty(s.ROOT_ID)
    for index, (name, regions) in enumerate([('controller',{'controller'}),('matrix',{'matrix','mechanical'}),('rgb',{'rgb'})],1):
        selected = [dict(part) for part in cfg['parts'] if part['region'] in regions]
        if not selected: continue
        sheet_id = s.identity('keyboard-sheet/'+name)
        child = s.empty(sheet_id); s.one(child,'paper')[1] = 'A1' if name!='controller' else 'A2'
        from .schematic_layout import arrange_sections,part_envelope
        if name=='controller':
            groups={title:[] for title in ('STM32 / decoupling / debug','USB / 3.3 V supply','RGB power / logic')}
            for part in selected:
                ref=part['ref']
                title=('RGB power / logic' if '_RGB' in ref else 'USB / 3.3 V supply'
                       if ref in ('J_UDB','U_REG','C_IN','C_OUT') or ref.startswith('#') else 'STM32 / decoupling / debug')
                groups[title].append(part)
            groups['STM32 / decoupling / debug'].sort(key=lambda part:part['ref']!='U_MCU')
            sections=list(groups.items())
        else:
            import re
            key_rows={key['id']:key['matrix'][0] for key in cfg['layout']['keys']}
            groups={row:[] for row in range(cfg['layout']['rows'])};other=[]
            for part in selected:
                match=re.fullmatch(r'(?:SW|D|STAB|LED|C_LED)(\d+)',part['ref'])
                if match and int(match[1]) in key_rows:groups[key_rows[int(match[1])]].append(part)
                else:other.append(part)
            sections=[(('Matrix' if name=='matrix' else 'RGB')+' / key row '+str(row),parts) for row,parts in groups.items()]
            sections.append(('Other components',other))
        selected,headings=arrange_sections(sections,symbols,width_mm=540 if name=='controller' else 780)
        boxes=[part_envelope(part,symbols.get(part['symbol']) or s.symbol(part['symbol'])) for part in selected]
        right=max(part['sch'][0]+box[2] for part,box in zip(selected,boxes))+15
        bottom=max(part['sch'][1]+box[3] for part,box in zip(selected,boxes))+15
        paper=next((paper for paper,width,height in [('A2',594,420),('A1',841,594),('A0',1189,841)] if right<width and bottom<height),None)
        if paper is None:raise ValueError('Generated schematic exceeds supported sheet dimensions')
        s.one(child,'paper')[1]=paper
        for title,x,y in headings:
            child.append([s.S('text'),title,[s.S('at'),x,y,0],[*s.effects(2.0),[s.S('justify'),s.S('left')]],
                          [s.S('uuid'),s.S(s.identity('heading/'+name+'/'+title))]])
        s.add_parts(child,selected,'/'+s.ROOT_ID+'/'+sheet_id,symbols=symbols,outward_labels=True)
        for instance in s.children(child,'symbol'):
            s.one(s.one(instance,'instances'),'project')[1] = NAME
        s.write(project/(name+'.kicad_sch'),child)
        x,y = s.snap(30),s.snap(25+index*35)
        root.append([s.S('sheet'),[s.S('at'),x,y],[s.S('size'),s.snap(70),s.snap(25)],
            [s.S('stroke'),[s.S('width'),.1524],[s.S('type'),s.S('solid')]],
            [s.S('fill'),[s.S('color'),0,0,0,0]],[s.S('uuid'),s.S(sheet_id)],
            [s.S('property'),'Sheetname',name,[s.S('at'),x,y-1,0],s.effects()],
            [s.S('property'),'Sheetfile',name+'.kicad_sch',[s.S('at'),x,y+26,0],s.effects()],
            [s.S('instances'),[s.S('project'),NAME,[s.S('path'),'/'+s.ROOT_ID,[s.S('page'),str(index+1)]]]]])
    s.write(project/(NAME+'.kicad_sch'),root)


def prepare_keyboard(kle_file, config_file, output):
    kle_file,config_file,output = map(Path,(kle_file,config_file,output))
    if output.exists(): raise ValueError('Select a new output directory')
    if any(p.is_symlink() or not p.is_file() or p.stat().st_size>65536 for p in (kle_file,config_file)):
        raise ValueError('Inputs must be bounded regular JSON files')
    text = kle_file.read_bytes().decode('utf-8')
    cfg = design(parse_layout(text,json.loads(config_file.read_bytes())))
    output.mkdir(parents=True); project = output/'project'; project.mkdir()
    (project/'layout.kle.json').write_bytes(kle_file.read_bytes())
    (project/'keyboard.json').write_bytes(config_file.read_bytes())
    symbols = create_libraries(project)
    write_schematic(project,cfg,symbols)
    rules = {'min_clearance':.15,'min_copper_edge_clearance':.2,'min_track_width':.15,
             'min_via_annular_width':.15,'min_via_diameter':.6,'min_through_hole_diameter':.3}
    pro = {'meta':{'version':1,'filename':NAME+'.kicad_pro'},
           'board':{'design_settings':{'rules':rules}},
           'net_settings':{'classes':[{'name':'Default','clearance':.15,'track_width':.2,'via_diameter':.6,'via_drill':.3}]}}
    (project/(NAME+'.kicad_pro')).write_text(encode(pro))
    pins,paths,fields = export_netlist(project,output/'netlist.xml',NAME)
    for component in cfg['parts']:
        if component['ref'].startswith('#'): continue
        component['nets'] = {number:net for (ref,number),net in pins.items() if ref==component['ref']}
    (project/'.smilepcb-keyboard.json').write_text(encode(cfg))
    (output/'model.json').write_text(encode(cfg))
    request = {'operation':'create','board':str((project/(NAME+'.kicad_pcb')).resolve()),'paths':paths,'fields':fields}
    snapshot = worker(BRIDGE,request,output/'create.log')
    (output/'snapshot.json').write_text(encode(snapshot))
    from .keyboard_firmware import write_qmk
    write_qmk(project,cfg)
    with (project/'BOM.csv').open('w',newline='') as f:
        out = csv.writer(f); out.writerow(['Reference','Value','Footprint'])
        out.writerows((p['ref'],p['value'],p['footprint']) for p in cfg['parts'] if not p['ref'].startswith('#'))
    return {'project':project,'model':cfg,'snapshot':snapshot,'placement':snapshot['placement']}


def build_keyboard(kle_file, config_file, output, tools):
    """Run a cold build; an incomplete candidate never receives complete=True."""
    from .keyboard_firmware import compile_qmk,file_hashes
    from .lab_tools import rule_checks
    from .routing_checks import require_clean_rules
    started=time.perf_counter();deadline=started+3600;output=Path(output).resolve();stage='compose'
    result=prepare_keyboard(kle_file,config_file,output)
    project=result['project'];board=project/(NAME+'.kicad_pcb')
    source_hashes=file_hashes(project)
    history=[];route_attempts={}
    def remaining(cap=90):
        value=deadline-time.perf_counter()
        if not value>0:raise TimeoutError('Complete keyboard build exceeded its 3600 s budget')
        return min(cap,value)
    def announce(value):
        nonlocal stage
        stage=value
        print('SmilePCB: '+value,file=sys.stderr,flush=True)
        (output/'progress.json').write_text(encode({'stage':stage,'elapsed_s':time.perf_counter()-started}))
    def call(operation,tag=None,**kwargs):
        tick=time.perf_counter()
        if operation in ('components','negotiate'):kwargs['attempt_counts']=dict(route_attempts)
        selected_board=kwargs.pop('board_override',board)
        data=worker(BRIDGE,{'operation':operation,'board':str(selected_board),**kwargs},output/((tag or operation)+'.log'),timeout_s=remaining())
        remaining()
        graph=data.get('graph',data.get('diagnostic',{}))
        route_attempts.update(graph.get('attempt_counts',{}))
        (output/((tag or operation)+'.json')).write_text(encode(data))
        history.append({'operation':operation,'tag':tag or operation,'elapsed_s':time.perf_counter()-tick,
                        'connections':data.get('connections',data.get('connected')),
                        'pad_group_gaps':data.get('pad_group_gaps')})
        return data
    try:
        announce('pre-route native acceptance')
        checks=rule_checks(project,output/'pre-route-checks',NAME,timeout_s=240,deadline=deadline)
        if checks['erc']['exit_code'] or checks['drc']['severity'] or checks['drc']['schematic_parity']:
            raise ValueError('Generated placement/schematic fails native acceptance')
        announce('clean QMK compilation')
        firmware=compile_qmk(project,output/'firmware',tools)
        remaining()
        announce('local key-module wiring and one RGB return per module')
        call('local_modules')
        announce('protected USB and complete MCU fanout')
        seed=call('seed')
        protected=[uid for route in seed['usb'].values() for uid in route['ids']]
        fanout=call('fanout',gpio_only=True)
        if not all(port['routed'] for port in fanout['ports']):raise ValueError('MCU fanout is incomplete')
        recovery_protected=protected+[uid for port in fanout['ports'] for uid in port['ids']]
        recovery_protected += [uid for access in fanout['plane_access'] for uid in access['ids']]
        announce('controller controls and input power before long matrix routes')
        for index,step in enumerate((500000,250000,125000)):
            call('components',tag='controller-'+str(index),controller_only=True,exclude_nets=['+3V3'],grid_step_nm=step,connection_budget_s=5)
        announce('controller interfaces before matrix and RGB channels')
        for index,step in enumerate((1000000,500000,250000)):
            call('components',tag='interfaces-'+str(index),controller_interfaces=True,grid_step_nm=step,connection_budget_s=5)
        announce('local MCU supply completion')
        for index,step in enumerate((500000,250000,125000)):
            call('components',tag='supply-'+str(index),controller_only=True,include_nets=['+3V3'],grid_step_nm=step,connection_budget_s=5)
        announce('matrix component routing')
        for index in range(2):
            if not call('components',tag='matrix-'+str(index),matrix_only=True)['connections']:break
        announce('local component routing')
        for index in range(2):
            if not call('local',tag='local-'+str(index),budget_s=45,max_length_mm=500,plane_nets=['GND'])['connections']:break
        announce('multi-terminal signal completion')
        for index,step in enumerate((1000000,500000,250000)):
            call('components',tag='components-'+str(index),grid_step_nm=step)
        announce('ground refill and physical connectivity')
        call('planes')
        call('stitch')
        independent=call('verify',tag='connectivity-0')
        from .keyboard_recovery import recover_generated
        independent=recover_generated(project,output,independent,protected,call,announce,deadline,
                                      port_ids=recovery_protected)
        if independent['pad_group_gaps']:raise ValueError('Independent native pad connectivity has remaining gaps: '+repr(independent['pad_groups_missing']))
        announce('generated tail cleanup and final native acceptance')
        call('cleanup',protected_ids=protected)
        independent=call('verify',tag='final-connectivity')
        if independent['pad_group_gaps']:raise ValueError('Cleanup changed native pad connectivity')
        checks=rule_checks(project,output/'final-checks',NAME,timeout_s=240,deadline=deadline)
        require_clean_rules(checks)
        if any((project/name).read_bytes()!=Path(source).read_bytes() for name,source in
               [('layout.kle.json',kle_file),('keyboard.json',config_file)]):
            raise ValueError('Saved build inputs changed')
        complete={'complete':True,'keys':len(result['model']['layout']['keys']),'native_checks':checks,
                  'independent':independent,'firmware':firmware,'power':result['model']['power'],
                  'placement':result['placement'],'routing_history':history,
                  'toolchain':tools,'elapsed_s':time.perf_counter()-started,'source_hashes':source_hashes,
                  'output_hashes':file_hashes(project),'hardware_tested':False,'fabrication_ready':False}
        remaining()
        (output/'result.json').write_text(encode(complete))
        announce('complete')
        return complete
    except BaseException as error:
        (output/'failure.json').write_text(encode({'complete':False,'stage':stage,'error':str(error),
            'routing_history':history,'elapsed_s':time.perf_counter()-started}))
        raise
