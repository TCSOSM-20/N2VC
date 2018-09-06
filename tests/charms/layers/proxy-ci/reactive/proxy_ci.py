from charmhelpers.core.hookenv import (
    action_fail,
    action_set,
    status_set,
)
from charms.reactive import (
    set_flag,
    clear_flag,
    when_not,
    when,
)
import charms.sshproxy


@when_not('proxy-ci.installed')
def install_metrics_ci():
    status_set('blocked', "Waiting for SSH credentials.")
    set_flag('proxy-ci.installed')


@when('actions.test', 'proxy-ci.installed')
def test():
    err = ''
    try:
        cmd = ['hostname']
        result, err = charms.sshproxy._run(cmd)
        if len(result) == 0:
            raise Exception("Proxy failed")
    except Exception as e:
        action_fail('command failed: {}'.format(e))
    else:
        action_set({'output': result})
    finally:
        clear_flag('actions.test')
