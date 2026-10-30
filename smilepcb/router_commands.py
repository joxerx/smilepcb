"""Separate non-mutating exchange proof commands from actual routing jobs."""


def preflight_command(command,*,result_json):
    """Disable every geometry-changing stage, retaining the actual job intact."""
    if not isinstance(command,(list,tuple)) or any(type(v) is not str for v in command):
        raise ValueError('Router command must be a string argument sequence')
    if sum(v.startswith('--router.result_json=') for v in command)!=1:
        raise ValueError('Router command must have one result JSON destination')
    stages=('--router.autorouter.enabled=','--router.fanout.enabled=','--router.optimizer.enabled=')
    result=[]
    for value in command:
        if value.startswith(stages):continue
        result.append('--router.result_json='+str(result_json) if value.startswith('--router.result_json=') else value)
    return result+[stage+'false' for stage in stages]
