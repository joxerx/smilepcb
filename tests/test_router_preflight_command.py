"""Exchange proofs must never execute any geometry-changing router stage."""
import pytest
from smilepcb.router_commands import preflight_command


def test_preflight_disables_all_routing_stages_without_mutating_the_real_job():
    original=['java','-Xmx512m','-jar','router.jar','--router.fanout.enabled=true',
              '--router.autorouter.enabled=true','--router.optimizer.enabled=true',
              '--router.result_json=job/result.json','-de','job/result.json','-do','job/board.ses']
    before=list(original);command=preflight_command(original,result_json='job/preflight.json')
    assert original==before
    for stage in ('autorouter','fanout','optimizer'):
        assert [v for v in command if v.startswith('--router.'+stage+'.enabled=')]==['--router.'+stage+'.enabled=false']
    assert '--router.result_json=job/preflight.json' in command
    assert command[command.index('-de')+1]=='job/result.json'
    assert command[command.index('-do')+1]=='job/board.ses'
    assert command[:4]==original[:4]


def test_preflight_explicitly_disables_stages_even_when_the_job_uses_defaults():
    command=preflight_command(['java','--router.result_json=old.json'],result_json='new.json')
    assert set(command[1:])=={'--router.result_json=new.json','--router.autorouter.enabled=false',
                           '--router.fanout.enabled=false','--router.optimizer.enabled=false'}


@pytest.mark.parametrize('command',[['java'],['java','--router.result_json=a','--router.result_json=b']])
def test_preflight_rejects_ambiguous_result_configuration(command):
    with pytest.raises(ValueError):preflight_command(command,result_json='new.json')
