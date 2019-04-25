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
