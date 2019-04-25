from charmhelpers.core.hookenv import (
    action_get,
    action_fail,
    action_set,
    status_set,
)
from charms.reactive import (
    clear_flag,
    set_flag,
    when,
    when_not,
)
import charms.sshproxy
import os


@when('sshproxy.configured')
@when_not('simple.installed')
def install_simple_proxy_charm():
    """Post-install actions.

    This function will run when two conditions are met:
    1. The 'sshproxy.configured' state is set
    2. The 'simple.installed' state is not set

    This ensures that the workload status is set to active only when the SSH
    proxy is properly configured.
    """
    set_flag('simple.installed')
    status_set('active', 'Ready!')


@when('actions.touch')
def touch():
    if not in_action_context():
        clear_flag('actions.touch')
        return

    err = ''
    try:
        filename = action_get('filename')
        cmd = ['touch {}'.format(filename)]
        result, err = charms.sshproxy._run(cmd)
    except Exception:
        action_fail('command failed:' + err)
    else:
        action_set({'output': result})
    finally:
        clear_flag('actions.touch')


def in_action_context():
    """Determine whether we're running on an action context."""
    return 'JUJU_ACTION_UUID' in os.environ
