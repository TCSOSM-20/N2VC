# Copyright 2019 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
#     Unless required by applicable law or agreed to in writing, software
#     distributed under the License is distributed on an "AS IS" BASIS,
#     WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#     See the License for the specific language governing permissions and
#     limitations under the License.

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
def install_proxy_ci():
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


@when('db.joined')
def provides_db(db):
    """Simulate providing database credentials."""
    db.configure(
        database="mydb",
        user="myuser",
        password="mypassword",
        host="myhost",
        slave="myslave",
    )


@when('db.available')
def requires_db(db):
    """Simulate receiving database credentials."""
    pass
