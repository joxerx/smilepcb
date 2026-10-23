"""STM32 recipe support: complete. Native checks are required."""
import json,sys,shutil,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from smilepcb.keyboard_tools import load_tools,configure_kicad
tools=load_tools(sys.argv[1]);base=ROOT/'.lab/public-controller'
source=base/sys.argv[2]/'project';out=base/sys.argv[3];out.mkdir(exist_ok=False)
configure_kicad(tools,out)
from smilepcb import schematic as s
from smilepcb.keyboard_build import BRIDGE
from smilepcb.keyboard_layout import parse_layout
from smilepcb.keyboard_firmware import write_qmk,compile_qmk,file_hashes
from smilepcb.lab_tools import worker,rule_checks
from smilepcb.keyboard_recovery import recover_generated,controller_interface_ids
from smilepcb.routing_checks import require_clean_rules
from smilepcb.keyboard_lookahead import tail_cleanup_eligible,generated_copper_inventory
port_ids=controller_interface_ids(source)
(out/'retained-controller-ports.json').write_text(json.dumps(port_ids,indent=2)+'\n')
project=out/'project';shutil.copytree(source,project);board=project/'keyboard.kicad_pcb'
fixture=ROOT/'recipes/stm32/inputs'/sys.argv[4]
if (project/'layout.kle.json').read_bytes()!=(fixture/'layout.kle.json').read_bytes():raise ValueError('Keymap input changes geometry')
cfg=json.loads((project/'.smilepcb-keyboard.json').read_text())
configured=parse_layout((fixture/'layout.kle.json').read_text(),json.loads((fixture/'keyboard.json').read_text()))
codes={k['id']:k for k in configured['keys']}
for key in cfg['layout']['keys']:
    if codes[key['id']]['center_mm']!=key['center_mm']:raise ValueError('Keymap input changes switch centres')
    key['keycode']=codes[key['id']]['keycode']
(project/'keyboard.json').write_bytes((fixture/'keyboard.json').read_bytes())
(project/'.smilepcb-keyboard.json').write_text(json.dumps(cfg,indent=2)+'\n')
write_qmk(project,cfg)
budget_s=int(sys.argv[sys.argv.index('--budget-s')+1]) if '--budget-s' in sys.argv[5:] else 3600
if not 120<=budget_s<=3600:raise ValueError('Completion budget must be 120..3600 seconds')
started=time.perf_counter();deadline=started+budget_s
history=[];counts={};stage='firmware';firmware=None
def announce(value):
    global stage
    stage=value;print(value,flush=True)
    (out/'progress.json').write_text(json.dumps({'stage':value,'elapsed_s':time.perf_counter()-started},indent=2)+'\n')
def call(operation,tag=None,**kw):
    if operation in ('components','negotiate'):kw['attempt_counts']=dict(counts)
    tag=tag or operation;path=kw.pop('board_override',board);tick=time.perf_counter()
    data=worker(BRIDGE,dict(operation=operation,board=str(path),**kw),out/(tag+'.log'),timeout_s=min(90,deadline-time.perf_counter()))
    counts.update(data.get('graph',{}).get('attempt_counts',{}))
    (out/(tag+'.json')).write_text(json.dumps(data,indent=2)+'\n')
    history.append({'operation':operation,'tag':tag,'elapsed_s':time.perf_counter()-tick,'pad_group_gaps':data.get('pad_group_gaps'),'connections':data.get('connections')})
    print(tag,'gaps',data.get('pad_group_gaps'),'connections',data.get('connections'),flush=True)
    return data
try:
    announce('Clean QMK compilation with explicit fixture keycodes')
    firmware=compile_qmk(project,out/'firmware',tools)
    tree=s.read(board);net_names={n[1]:n[2] for n in tree if s.tag(n)=='net' and len(n)>2}
    protected=[]
    for item in tree:
        if s.tag(item) not in ('segment','via','arc'):continue
        net=s.one(item,'net')[1];name=net_names.get(net,net)
        if name in ('USB_DP','USB_DM'):protected.append(str(s.one(item,'uuid')[1]))
    if not protected:raise ValueError('USB protection inventory is empty')
    (out/'protected-usb.json').write_text(json.dumps(protected,indent=2)+'\n')
    announce('Independent native post-router connectivity')
    independent=call('verify',tag='initial-connectivity')
    for index,step in enumerate((500000,250000,125000)):
        pending={net:gaps for net,gaps in independent['pad_groups_missing'].items() if net not in set(cfg['plane_nets']) and gaps>1}
        if not pending:break
        chosen=max(pending,key=lambda net:(pending[net],net))
        (out/('dominant-'+str(index)+'-selection.json')).write_text(json.dumps({'reason':'largest disconnected non-plane net','selected_net':chosen,'gaps':pending[chosen]},indent=2)+'\n')
        announce('Most fragmented net completion '+str(index))
        call('components',tag='dominant-'+str(index),include_nets=[chosen],grid_step_nm=step,connection_budget_s=3,relax_preferences=True,completion_search=True,track_width_nm=150000)
        call('stitch',tag='dominant-stitch-'+str(index))
        independent=call('verify',tag='dominant-verify-'+str(index))
    isolated=[net for net,groups in independent['disconnected_pad_groups'].items() if any(g['pad_count']==1 and any(ref=='U_MCU' for ref,pin in g['sample_pads']) for g in groups)]
    if isolated:
        from smilepcb.keyboard_build import routing_transaction
        announce('Checked two-face access for isolated controller terminals')
        def escape(candidate):
            candidate_board=candidate/'keyboard.kicad_pcb'
            call('fanout',tag='ports-fanout',board_override=candidate_board,include_nets=isolated)
            call('components',tag='ports-join',board_override=candidate_board,include_nets=isolated,grid_step_nm=125000,connection_budget_s=3,relax_preferences=True,completion_search=True,track_width_nm=150000)
            call('stitch',tag='ports-stitch',board_override=candidate_board)
            data=call('verify',tag='ports-verify',board_override=candidate_board)
            checks=rule_checks(candidate,out/'ports-checks','keyboard',timeout_s=120)
            if checks['erc']['severity'] or checks['drc']['severity'].get('error') or checks['drc']['schematic_parity']:
                raise ValueError('Controller access candidate introduces native rule violations')
            return data
        transaction=routing_transaction(project,out/'ports-candidate',escape,independent,deadline=deadline)
        (out/'ports-transaction.json').write_text(json.dumps(transaction,indent=2)+'\n')
        if transaction['accepted']:independent=call('verify',tag='ports-saved')
    independent=recover_generated(project,out,independent,protected,call,announce,deadline,port_ids=port_ids)
    if independent['pad_group_gaps']:raise ValueError('Remaining independent native pad groups: '+str(independent['pad_groups_missing']))
    announce('Final native cleanup and full ERC/DRC')
    call('cleanup',protected_ids=protected)
    independent=call('verify',tag='final-connectivity')
    checks=rule_checks(project,out/'final-checks','keyboard',timeout_s=240,deadline=deadline)
    for index in range(8):
        report=json.loads((out/('final-checks' if index==0 else 'reported-tail-checks-'+str(index-1))/'drc.json').read_text())
        findings=report['violations']
        if not tail_cleanup_eligible(independent,checks,report,generated_copper_inventory(board),protected):break
        call('prune_reported_tails',tag='reported-tails-'+str(index),findings=findings,protected_ids=protected)
        call('cleanup',tag='reported-tail-cascade-'+str(index),protected_ids=protected)
        independent=call('verify',tag='reported-tail-connectivity-'+str(index))
        if independent['pad_group_gaps']:raise ValueError('Reported-tail cleanup changed connectivity')
        checks=rule_checks(project,out/('reported-tail-checks-'+str(index)),'keyboard',timeout_s=240,deadline=deadline)
    require_clean_rules(checks)
    if independent['pad_group_gaps']:raise ValueError('Cleanup left pad-group gaps')
    result={'complete':True,'fixture':sys.argv[4],'independent':independent,'native_checks':checks,'firmware':firmware,'history':history,'elapsed_s':time.perf_counter()-started,'output_hashes':file_hashes(project),'hardware_tested':False}
    (out/'result.json').write_text(json.dumps(result,indent=2)+'\n');announce('Complete software acceptance')
except BaseException as error:
    (out/'failure.json').write_text(json.dumps({'complete':False,'stage':stage,'error':str(error),'firmware':firmware,'history':history,'elapsed_s':time.perf_counter()-started},indent=2)+'\n')
    raise
