from charmhelpers.core.hookenv import (
    status_set,
)
from charms.reactive import (
    set_flag,
    when_not,
)


@when_not('metrics-ci.installed')
def install_metrics_ci():
    status_set('blocked', "Waiting for SSH credentials.")
    set_flag('metrics-ci.installed')
