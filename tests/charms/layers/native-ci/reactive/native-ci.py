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
    action_get,
    status_set,
)
from charms.reactive import (
    clear_flag,
    set_flag,
    when,
    when_not,
)


@when_not('native-ci.installed')
def install_native_ci_charm():
    set_flag('native-ci.installed')
    status_set('active', 'Ready!')


@when('actions.test', 'native-ci.installed')
def test():
    try:
        result = True
    except Exception as e:
        action_fail('command failed: {}'.format(e))
    else:
        action_set({'output': result})
    finally:
        clear_flag('actions.test')


@when('actions.testint', 'native-ci.installed')
def testint():
    try:
        # Test the value is an int by performing a mathmatical operation on it.
        intval = action_get('intval')
        intval = intval + 1
    except Exception as e:
        action_fail('command failed: {}'.format(e))
    else:
        action_set({'output': intval})
    finally:
        clear_flag('actions.testint')


# @when('db.joined')
# def provides_db(db):
#     """Simulate providing database credentials."""
#     db.configure(
#         database="mydb",
#         user="myuser",
#         password="mypassword",
#         host="myhost",
#         slave="myslave",
#     )


# @when('db.available')
# def requires_db(db):
#     """Simulate receiving database credentials."""
#     pass
