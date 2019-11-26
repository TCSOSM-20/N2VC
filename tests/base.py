#!/usr/bin/env python3
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

import asyncio
import datetime
import logging
import n2vc.vnf
import pylxd
import pytest
import os
import shlex
import subprocess
import time
import uuid
import yaml

from juju.controller import Controller

# Disable InsecureRequestWarning w/LXD
import urllib3
urllib3.disable_warnings()
logging.getLogger("urllib3").setLevel(logging.WARNING)

here = os.path.dirname(os.path.realpath(__file__))


class CleanController():
    """
    Context manager that automatically connects and disconnects from
    the currently active controller.

    Note: Unlike CleanModel, this will not create a new controller for you,
    and an active controller must already be available.
    """
    def __init__(self):
        self._controller = None

    async def __aenter__(self):
        self._controller = Controller()
        await self._controller.connect()
        return self._controller

    async def __aexit__(self, exc_type, exc, tb):
        await self._controller.disconnect()


def debug(msg):
    """Format debug messages in a consistent way."""
    now = datetime.datetime.now()

    # TODO: Decide on the best way to log. Output from `logging.debug` shows up
    # when a test fails, but print() will always show up when running tox with
    # `-s`, which is really useful for debugging single tests without having to
    # insert a False assert to see the log.
    logging.debug(
        "[{}] {}".format(now.strftime('%Y-%m-%dT%H:%M:%S'), msg)
    )
    print(
        "[{}] {}".format(now.strftime('%Y-%m-%dT%H:%M:%S'), msg)
    )


def get_charm_path():
    return "{}/charms".format(here)


def get_layer_path():
    return "{}/charms/layers".format(here)


def collect_metrics(application):
    """Invoke Juju's metrics collector.

    Caveat: this shells out to the `juju collect-metrics` command, rather than
    making an API call. At the time of writing, that API is not exposed through
    the client library.
    """

    try:
        subprocess.check_call(['juju', 'collect-metrics', application])
    except subprocess.CalledProcessError as e:
        raise Exception("Unable to collect metrics: {}".format(e))


def has_metrics(charm):
    """Check if a charm has metrics defined."""
    metricsyaml = "{}/{}/metrics.yaml".format(
        get_layer_path(),
        charm,
    )
    if os.path.exists(metricsyaml):
        return True
    return False


def get_descriptor(descriptor):
    desc = None
    try:
        tmp = yaml.safe_load(descriptor)

        # Remove the envelope
        root = list(tmp.keys())[0]
        if root == "nsd:nsd-catalog":
            desc = tmp['nsd:nsd-catalog']['nsd'][0]
        elif root == "vnfd:vnfd-catalog":
            desc = tmp['vnfd:vnfd-catalog']['vnfd'][0]
    except ValueError:
        assert False
    return desc


def get_n2vc(loop=None):
    """Return an instance of N2VC.VNF."""
    log = logging.getLogger()
    log.level = logging.DEBUG

    # Extract parameters from the environment in order to run our test
    vca_host = os.getenv('VCA_HOST', '127.0.0.1')
    vca_port = os.getenv('VCA_PORT', 17070)
    vca_user = os.getenv('VCA_USER', 'admin')
    vca_charms = os.getenv('VCA_CHARMS', None)
    vca_secret = os.getenv('VCA_SECRET', None)
    vca_cacert = os.getenv('VCA_CACERT', None)

    # Get the Juju Public key
    juju_public_key = get_juju_public_key()
    if juju_public_key:
        debug("Reading Juju public key @ {}".format(juju_public_key))
        with open(juju_public_key, 'r') as f:
            juju_public_key = f.read()
        debug("Found public key: {}".format(juju_public_key))
    else:
        raise Exception("No Juju Public Key found")

    # Get the ca-cert
    # os.path.expanduser("~/.config/lxc")
    # with open("{}/agent.conf".format(AGENT_PATH), "r") as f:
    #     try:
    #         y = yaml.safe_load(f)
    #         self.cacert = y['cacert']
    #     except yaml.YAMLError as exc:
    #         log("Unable to find Juju ca-cert.")
    #         raise exc

    client = n2vc.vnf.N2VC(
        log=log,
        server=vca_host,
        port=vca_port,
        user=vca_user,
        secret=vca_secret,
        artifacts=vca_charms,
        loop=loop,
        juju_public_key=juju_public_key,
        ca_cert=vca_cacert,
    )
    return client


def create_lxd_container(public_key=None, name="test_name"):
    """
    Returns a container object

    If public_key isn't set, we'll use the Juju ssh key

    :param public_key: The public key to inject into the container
    :param name: The name of the test being run
    """
    container = None

    # Format name so it's valid
    name = name.replace("_", "-").replace(".", "")

    client = get_lxd_client()
    if not client:
        raise Exception("Unable to connect to LXD")

    test_machine = "test-{}-{}".format(
        uuid.uuid4().hex[-4:],
        name,
    )

    private_key_path, public_key_path = find_n2vc_ssh_keys()

    try:
        # create profile w/cloud-init and juju ssh key
        if not public_key:
            public_key = ""
            with open(public_key_path, "r") as f:
                public_key = f.readline()

        client.profiles.create(
            test_machine,
            config={
                'user.user-data': '#cloud-config\nssh_authorized_keys:\n- {}'.format(public_key)},
            devices={
                'root': {'path': '/', 'pool': 'default', 'type': 'disk'},
                'eth0': {
                    'nictype': 'bridged',
                    'parent': 'lxdbr0',
                    'type': 'nic'
                }
            }
        )
    except Exception as ex:
        debug("Error creating lxd profile {}: {}".format(test_machine, ex))
        raise ex

    try:
        # create lxc machine
        config = {
            'name': test_machine,
            'source': {
                'type': 'image',
                'alias': 'xenial',
                'mode': 'pull',
                'protocol': 'simplestreams',
                'server': 'https://cloud-images.ubuntu.com/releases',
            },
            'profiles': [test_machine],
        }
        container = client.containers.create(config, wait=True)
        container.start(wait=True)
    except Exception as ex:
        debug("Error creating lxd container {}: {}".format(test_machine, ex))
        # This is a test-ending failure.
        raise ex

    def wait_for_network(container, timeout=30):
        """Wait for eth0 to have an ipv4 address."""
        starttime = time.time()
        while(time.time() < starttime + timeout):
            time.sleep(1)
            if 'eth0' in container.state().network:
                addresses = container.state().network['eth0']['addresses']
                if len(addresses) > 0:
                    if addresses[0]['family'] == 'inet':
                        return addresses[0]
        return None

    try:
        wait_for_network(container)
    except Exception as ex:
        debug(
            "Error waiting for container {} network: {}".format(
                test_machine,
                ex,
            )
        )

    try:
        waitcount = 0
        while waitcount <= 5:
            if is_sshd_running(container):
                break
            waitcount += 1
            time.sleep(1)
        if waitcount >= 5:
            debug("couldn't detect sshd running")
            raise Exception("Unable to verify container sshd")

    except Exception as ex:
        debug(
            "Error checking sshd status on {}: {}".format(
                test_machine,
                ex,
            )
        )

    # HACK: We need to give sshd a chance to bind to the interface,
    # and pylxd's container.execute seems to be broken and fails and/or
    # hangs trying to properly check if the service is up.
    (exit_code, stdout, stderr) = container.execute([
        'ping',
        '-c', '5',   # Wait for 5 ECHO_REPLY
        '8.8.8.8',   # Ping Google's public DNS
        '-W', '15',  # Set a 15 second deadline
    ])
    if exit_code > 0:
        # The network failed
        raise Exception("Unable to verify container network")

    return container


def is_sshd_running(container):
    """Check if sshd is running in the container.

    Check to see if the sshd process is running and listening on port 22.

    :param container: The container to check
    :return boolean: True if sshd is running.
    """
    debug("Container: {}".format(container))
    try:
        (rc, stdout, stderr) = container.execute(
            ["service", "ssh", "status"]
        )
        # If the status is a) found and b) running, the exit code will be 0
        if rc == 0:
            return True
    except Exception as ex:
        debug("Failed to check sshd service status: {}".format(ex))

    return False


def destroy_lxd_container(container):
    """Stop and delete a LXD container.

    Sometimes we see errors talking to LXD -- ephemerial issues like
    load or a bug that's killed the API. We'll do our best to clean
    up here, and we should run a cleanup after all tests are finished
    to remove any extra containers and profiles belonging to us.
    """

    if type(container) is bool:
        return

    name = container.name
    debug("Destroying container {}".format(name))

    client = get_lxd_client()

    def wait_for_stop(timeout=30):
        """Wait for eth0 to have an ipv4 address."""
        starttime = time.time()
        while(time.time() < starttime + timeout):
            time.sleep(1)
            if container.state == "Stopped":
                return

    def wait_for_delete(timeout=30):
        starttime = time.time()
        while(time.time() < starttime + timeout):
            time.sleep(1)
            if client.containers.exists(name) is False:
                return

    try:
        container.stop(wait=False)
        wait_for_stop()
    except Exception as ex:
        debug(
            "Error stopping container {}: {}".format(
                name,
                ex,
            )
        )

    try:
        container.delete(wait=False)
        wait_for_delete()
    except Exception as ex:
        debug(
            "Error deleting container {}: {}".format(
                name,
                ex,
            )
        )

    try:
        # Delete the profile created for this container
        profile = client.profiles.get(name)
        if profile:
            profile.delete()
    except Exception as ex:
        debug(
            "Error deleting profile {}: {}".format(
                name,
                ex,
            )
        )


def find_lxd_config():
    """Find the LXD configuration directory."""
    paths = []
    paths.append(os.path.expanduser("~/.config/lxc"))
    paths.append(os.path.expanduser("~/snap/lxd/current/.config/lxc"))

    for path in paths:
        if os.path.exists(path):
            crt = os.path.expanduser("{}/client.crt".format(path))
            key = os.path.expanduser("{}/client.key".format(path))
            if os.path.exists(crt) and os.path.exists(key):
                return (crt, key)
    return (None, None)


def find_n2vc_ssh_keys():
    """Find the N2VC ssh keys."""

    paths = []
    paths.append(os.path.expanduser("~/.ssh/"))

    for path in paths:
        if os.path.exists(path):
            private = os.path.expanduser("{}/id_n2vc_rsa".format(path))
            public = os.path.expanduser("{}/id_n2vc_rsa.pub".format(path))
            if os.path.exists(private) and os.path.exists(public):
                return (private, public)
    return (None, None)


def find_juju_ssh_keys():
    """Find the Juju ssh keys."""

    paths = []
    paths.append(os.path.expanduser("~/.local/share/juju/ssh"))

    for path in paths:
        if os.path.exists(path):
            private = os.path.expanduser("{}/juju_id_rsa".format(path))
            public = os.path.expanduser("{}/juju_id_rsa.pub".format(path))
            if os.path.exists(private) and os.path.exists(public):
                return (private, public)
    return (None, None)


def get_juju_private_key():
    keys = find_juju_ssh_keys()
    return keys[0]


def get_juju_public_key():
    """Find the Juju public key."""
    paths = []

    if 'VCA_PATH' in os.environ:
        paths.append("{}/ssh".format(os.environ["VCA_PATH"]))

    paths.append(os.path.expanduser("~/.local/share/juju/ssh"))
    paths.append("/root/.local/share/juju/ssh")

    for path in paths:
        if os.path.exists(path):
            public = os.path.expanduser("{}/juju_id_rsa.pub".format(path))
            if os.path.exists(public):
                return public
    return None


def get_lxd_client(host=None, port="8443", verify=False):
    """ Get the LXD client."""

    if host is None:
        if 'LXD_HOST' in os.environ:
            host = os.environ['LXD_HOST']
        else:
            host = '127.0.0.1'

    passwd = None
    if 'LXD_SECRET' in os.environ:
        passwd = os.environ['LXD_SECRET']

    # debug("Connecting to LXD remote {} w/authentication ({})".format(
    #     host,
    #     passwd
    # ))
    client = None
    (crt, key) = find_lxd_config()

    if crt and key:
        client = pylxd.Client(
            endpoint="https://{}:{}".format(host, port),
            cert=(crt, key),
            verify=verify,
        )

        # If the LXD server has a pasword set, authenticate with it.
        if not client.trusted and passwd:
            try:
                client.authenticate(passwd)
                if not client.trusted:
                    raise Exception("Unable to authenticate with LXD remote")
            except pylxd.exceptions.LXDAPIException as ex:
                if 'Certificate already in trust store' in ex:
                    pass

    return client


# TODO: This is marked serial but can be run in parallel with work, including:
# - Fixing an event loop issue; seems that all tests stop when one test stops?


@pytest.mark.serial
class TestN2VC(object):
    """TODO:
    1. Validator Validation

    Automatically validate the descriptors we're using here, unless the test
    author explicitly wants to skip them. Useful to make sure tests aren't
    being run against invalid descriptors, validating functionality that may
    fail against a properly written descriptor.

    We need to have a flag (instance variable) that controls this behavior. It
    may be necessary to skip validation and run against a descriptor
    implementing features that have not yet been released in the Information
    Model.
    """

    """
    The six phases of integration testing, for the test itself and each charm?:

    setup/teardown_class:
    1. Prepare      - Verify the environment and create a new model
    2. Deploy       - Mark the test as ready to execute
    3. Configure    - Configuration to reach Active state
    4. Test         - Execute primitive(s) to verify success
    5. Collect      - Collect any useful artifacts for debugging (charm, logs)
    6. Destroy      - Destroy the model


    1. Prepare      - Building of charm
    2. Deploy       - Deploying charm
    3. Configure    - Configuration to reach Active state
    4. Test         - Execute primitive(s) to verify success
    5. Collect      - Collect any useful artifacts for debugging (charm, logs)
    6. Destroy      - Destroy the charm

    """
    @classmethod
    def setup_class(self):
        """ setup any state specific to the execution of the given class (which
        usually contains tests).
        """
        # Initialize instance variable(s)
        self.n2vc = None

        # Track internal state for each test run
        self.state = {}

        # Parse the test's descriptors
        self.nsd = get_descriptor(self.NSD_YAML)
        self.vnfd = get_descriptor(self.VNFD_YAML)

        self.ns_name = self.nsd['name']
        self.vnf_name = self.vnfd['name']

        self.charms = {}
        self.parse_vnf_descriptor()
        assert self.charms is not {}

        # Track artifacts, like compiled charms, that will need to be removed
        self.artifacts = {}

        # Build the charm(s) needed for this test
        for charm in self.get_charm_names():
            # debug("Building charm {}".format(charm))
            self.get_charm(charm)

        # A bit of a hack, in order to allow the N2VC callback to run parallel
        # to pytest. Test(s) should wait for this flag to change to False
        # before returning.
        self._running = True
        self._stopping = False

    @classmethod
    def teardown_class(self):
        """ teardown any state that was previously setup with a call to
        setup_class.
        """
        debug("Running teardown_class...")
        try:

            debug("Destroying LXD containers...")
            for application in self.state:
                if self.state[application]['container']:
                    destroy_lxd_container(self.state[application]['container'])
            debug("Destroying LXD containers...done.")

            # Logout of N2VC
            if self.n2vc:
                debug("teardown_class(): Logging out of N2VC...")
                yield from self.n2vc.logout()
                debug("teardown_class(): Logging out of N2VC...done.")

            debug("Running teardown_class...done.")
        except Exception as ex:
            debug("Exception in teardown_class: {}".format(ex))

    @classmethod
    def all_charms_active(self):
        """Determine if the all deployed charms are active."""
        active = 0

        for application in self.state:
            if 'status' in self.state[application]:
                debug("status of {} is '{}'".format(
                    application,
                    self.state[application]['status'],
                ))
                if self.state[application]['status'] == 'active':
                    active += 1

        debug("Active charms: {}/{}".format(
            active,
            len(self.charms),
        ))

        if active == len(self.charms):
            return True

        return False

    @classmethod
    def are_tests_finished(self):
        appcount = len(self.state)

        # If we don't have state yet, keep running.
        if appcount == 0:
            debug("No applications")
            return False

        if self._stopping:
            debug("_stopping is True")
            return True

        appdone = 0
        for application in self.state:
            if self.state[application]['done']:
                appdone += 1

        debug("{}/{} charms tested".format(appdone, appcount))

        if appcount == appdone:
            return True

        return False

    @classmethod
    async def running(self, timeout=600):
        """Returns if the test is still running.

        @param timeout The time, in seconds, to wait for the test to complete.
        """
        if self.are_tests_finished():
            await self.stop()
            return False

        await asyncio.sleep(30)

        return self._running

    @classmethod
    def get_charm(self, charm):
        """Build and return the path to the test charm.

        Builds one of the charms in tests/charms/layers and returns the path
        to the compiled charm. The charm will automatically be removed when
        when the test is complete.

        Returns: The path to the built charm or None if `charm build` failed.
        """
        # Make sure the charm snap is installed
        charm_cmd = None
        try:
            subprocess.check_call(['which', 'charm'])
            charm_cmd = "charm build"
        except subprocess.CalledProcessError:
            # charm_cmd = "charm-build"
            # debug("Using legacy charm-build")
            raise Exception("charm snap not installed.")

        if charm not in self.artifacts:
            try:
                # Note: This builds the charm under N2VC/tests/charms/builds/
                # Currently, the snap-installed command only has write access
                # to the $HOME (changing in an upcoming release) so writing to
                # /tmp isn't possible at the moment.

                builds = get_charm_path()
                if not os.path.exists("{}/builds/{}".format(builds, charm)):
                    cmd = "{} --no-local-layers {}/{} -o {}/".format(
                        charm_cmd,
                        get_layer_path(),
                        charm,
                        builds,
                    )
                    # debug(cmd)

                    env = os.environ.copy()
                    env["CHARM_BUILD_DIR"] = builds

                    subprocess.check_call(shlex.split(cmd), env=env)

            except subprocess.CalledProcessError as e:
                # charm build will return error code 100 if the charm fails
                # the auto-run of charm proof, which we can safely ignore for
                # our CI charms.
                if e.returncode != 100:
                    raise Exception("charm build failed: {}.".format(e))

            self.artifacts[charm] = {
                'tmpdir': builds,
                'charm': "{}/builds/{}".format(builds, charm),
            }

        return self.artifacts[charm]['charm']

    @classmethod
    async def deploy(self, vnf_index, charm, params, loop):
        """An inner function to do the deployment of a charm from
        either a vdu or vnf.
        """

        if not self.n2vc:
            self.n2vc = get_n2vc(loop=loop)

        debug("Creating model for Network Service {}".format(self.ns_name))
        await self.n2vc.CreateNetworkService(self.ns_name)

        application = self.n2vc.FormatApplicationName(
            self.ns_name,
            self.vnf_name,
            str(vnf_index),
        )

        # Initialize the state of the application
        self.state[application] = {
            'status': None,     # Juju status
            'container': None,  # lxd container, for proxy charms
            'actions': {},      # Actions we've executed
            'done': False,      # Are we done testing this charm?
            'phase': "deploy",  # What phase is this application in?
        }

        debug("Deploying charm at {}".format(self.artifacts[charm]))

        # If this is a native charm, we need to provision the underlying
        # machine ala an LXC container.
        machine_spec = {}

        if not self.isproxy(application):
            debug("Creating container for native charm")
            # args = ("default", application, None, None)
            self.state[application]['container'] = create_lxd_container(
                name=os.path.basename(__file__)
            )

            hostname = self.get_container_ip(
                self.state[application]['container'],
            )

            machine_spec = {
                'hostname': hostname,
                'username': 'ubuntu',
            }

        await self.n2vc.DeployCharms(
            self.ns_name,
            application,
            self.vnfd,
            self.get_charm(charm),
            params,
            machine_spec,
            self.n2vc_callback,
        )

    @classmethod
    def parse_vnf_descriptor(self):
        """Parse the VNF descriptor to make running tests easier.

        Parse the charm information in the descriptor to make it easy to write
        tests to run again it.

        Each charm becomes a dictionary in a list:
        [
            'is-proxy': True,
            'vnf-member-index': 1,
            'vnf-name': '',
            'charm-name': '',
            'initial-config-primitive': {},
            'config-primitive': {}
        ]
        - charm name
        - is this a proxy charm?
        - what are the initial-config-primitives (day 1)?
        - what are the config primitives (day 2)?

        """
        charms = {}

        # You'd think this would be explicit, but it's just an incremental
        # value that should be consistent.
        vnf_member_index = 0

        """Get all vdu and/or vdu config in a descriptor."""
        config = self.get_config()
        for cfg in config:
            if 'juju' in cfg:

                # Get the name to be used for the deployed application
                application_name = n2vc.vnf.N2VC().FormatApplicationName(
                    self.ns_name,
                    self.vnf_name,
                    str(vnf_member_index),
                )

                charm = {
                    'application-name': application_name,
                    'proxy': True,
                    'vnf-member-index': vnf_member_index,
                    'vnf-name': self.vnf_name,
                    'name': None,
                    'initial-config-primitive': {},
                    'config-primitive': {},
                }

                juju = cfg['juju']
                charm['name'] = juju['charm']

                if 'proxy' in juju:
                    charm['proxy'] = juju['proxy']

                if 'initial-config-primitive' in cfg:
                    charm['initial-config-primitive'] = \
                        cfg['initial-config-primitive']

                if 'config-primitive' in cfg:
                    charm['config-primitive'] = cfg['config-primitive']

                charms[application_name] = charm

            # Increment the vnf-member-index
            vnf_member_index += 1

        self.charms = charms

    @classmethod
    def isproxy(self, application_name):

        assert application_name in self.charms
        assert 'proxy' in self.charms[application_name]
        assert type(self.charms[application_name]['proxy']) is bool

        # debug(self.charms[application_name])
        return self.charms[application_name]['proxy']

    @classmethod
    def get_config(self):
        """Return an iterable list of config items (vdu and vnf).

        As far as N2VC is concerned, the config section for vdu and vnf are
        identical. This joins them together so tests only need to iterate
        through one list.
        """
        configs = []

        """Get all vdu and/or vdu config in a descriptor."""
        vnf_config = self.vnfd.get("vnf-configuration")
        if vnf_config:
            juju = vnf_config['juju']
            if juju:
                configs.append(vnf_config)

        for vdu in self.vnfd['vdu']:
            vdu_config = vdu.get('vdu-configuration')
            if vdu_config:
                juju = vdu_config['juju']
                if juju:
                    configs.append(vdu_config)

        return configs

    @classmethod
    def get_charm_names(self):
        """Return a list of charms used by the test descriptor."""

        charms = {}

        # Check if the VDUs in this VNF have a charm
        for config in self.get_config():
            juju = config['juju']

            name = juju['charm']
            if name not in charms:
                charms[name] = 1

        return charms.keys()

    @classmethod
    def get_phase(self, application):
        return self.state[application]['phase']

    @classmethod
    def set_phase(self, application, phase):
        self.state[application]['phase'] = phase

    @classmethod
    async def configure_proxy_charm(self, *args):
        """Configure a container for use via ssh."""
        (model, application, _, _) = args

        try:
            if self.get_phase(application) == "deploy":
                self.set_phase(application, "configure")

                debug("Start CreateContainer for {}".format(application))
                self.state[application]['container'] = \
                    await self.CreateContainer(*args)
                debug("Done CreateContainer for {}".format(application))

                if self.state[application]['container']:
                    debug("Configure {} for container".format(application))
                    if await self.configure_ssh_proxy(application):
                        await asyncio.sleep(0.1)
                        return True
                else:
                    debug("Failed to configure container for {}".format(application))
            else:
                debug("skipping CreateContainer for {}: {}".format(
                    application,
                    self.get_phase(application),
                ))

        except Exception as ex:
            debug("configure_proxy_charm exception: {}".format(ex))
        finally:
            await asyncio.sleep(0.1)

        return False

    @classmethod
    async def execute_charm_tests(self, *args):
        (model, application, _, _) = args

        debug("Executing charm test(s) for {}".format(application))

        if self.state[application]['done']:
            debug("Trying to execute tests against finished charm...aborting")
            return False

        try:
            phase = self.get_phase(application)
            # We enter the test phase when after deploy (for native charms) or
            # configure, for proxy charms.
            if phase in ["deploy", "configure"]:
                self.set_phase(application, "test")
                if self.are_tests_finished():
                    raise Exception("Trying to execute init-config on finished test")

                if await self.execute_initial_config_primitives(application):
                    # check for metrics
                    await self.check_metrics(application)

                    debug("Done testing {}".format(application))
                    self.state[application]['done'] = True

        except Exception as ex:
            debug("Exception in execute_charm_tests: {}".format(ex))
        finally:
            await asyncio.sleep(0.1)

        return True

    @classmethod
    async def CreateContainer(self, *args):
        """Create a LXD container for use with a proxy charm.abs

        1. Get the public key from the charm via `get-ssh-public-key` action
        2. Create container with said key injected for the ubuntu user

        Returns a Container object
        """
        # Create and configure a LXD container for use with a proxy charm.
        (model, application, _, _) = args

        debug("[CreateContainer] {}".format(args))
        container = None

        try:
            # Execute 'get-ssh-public-key' primitive and get returned value
            uuid = await self.n2vc.ExecutePrimitive(
                model,
                application,
                "get-ssh-public-key",
                None,
            )

            result = await self.n2vc.GetPrimitiveOutput(model, uuid)
            pubkey = result['pubkey']

            container = create_lxd_container(
                public_key=pubkey,
                name=os.path.basename(__file__)
            )

            return container
        except Exception as ex:
            debug("Error creating container: {}".format(ex))
            pass

        return None

    @classmethod
    async def stop(self):
        """Stop the test.

        - Remove charms
        - Stop and delete containers
        - Logout of N2VC

        TODO: Clean up duplicate code between teardown_class() and stop()
        """
        debug("stop() called")

        if self.n2vc and self._running and not self._stopping:
            self._running = False
            self._stopping = True

            # Destroy the network service
            try:
                await self.n2vc.DestroyNetworkService(self.ns_name)
            except Exception as e:
                debug(
                    "Error Destroying Network Service \"{}\": {}".format(
                        self.ns_name,
                        e,
                    )
                )

            # Wait for the applications to be removed and delete the containers
            for application in self.charms:
                try:

                    while True:
                        # Wait for the application to be removed
                        await asyncio.sleep(10)
                        if not await self.n2vc.HasApplication(
                            self.ns_name,
                            application,
                        ):
                            break

                    # Need to wait for the charm to finish, because native charms
                    if self.state[application]['container']:
                        debug("Deleting LXD container...")
                        destroy_lxd_container(
                            self.state[application]['container']
                        )
                        self.state[application]['container'] = None
                        debug("Deleting LXD container...done.")
                    else:
                        debug("No container found for {}".format(application))
                except Exception as e:
                    debug("Error while deleting container: {}".format(e))

            # Logout of N2VC
            try:
                debug("stop(): Logging out of N2VC...")
                await self.n2vc.logout()
                self.n2vc = None
                debug("stop(): Logging out of N2VC...Done.")
            except Exception as ex:
                debug(ex)

            # Let the test know we're finished.
            debug("Marking test as finished.")
            # self._running = False
        else:
            debug("Skipping stop()")

    @classmethod
    def get_container_ip(self, container):
        """Return the IPv4 address of container's eth0 interface."""
        ipaddr = None
        if container:
            addresses = container.state().network['eth0']['addresses']
            # The interface may have more than one address, but we only need
            # the first one for testing purposes.
            ipaddr = addresses[0]['address']

        return ipaddr

    @classmethod
    async def configure_ssh_proxy(self, application, task=None):
        """Configure the proxy charm to use the lxd container.

        Configure the charm to use a LXD container as it's VNF.
        """
        debug("Configuring ssh proxy for {}".format(application))

        mgmtaddr = self.get_container_ip(
            self.state[application]['container'],
        )

        debug(
            "Setting ssh-hostname for {} to {}".format(
                application,
                mgmtaddr,
            )
        )

        await self.n2vc.ExecutePrimitive(
            self.ns_name,
            application,
            "config",
            None,
            params={
                'ssh-hostname': mgmtaddr,
                'ssh-username': 'ubuntu',
            }
        )

        return True

    @classmethod
    async def execute_initial_config_primitives(self, application, task=None):
        debug("Executing initial_config_primitives for {}".format(application))
        try:
            init_config = self.charms[application]

            """
            The initial-config-primitive is run during deploy but may fail
             on some steps because proxy charm access isn't configured.

            Re-run those actions so we can inspect the status.
            """
            uuids = await self.n2vc.ExecuteInitialPrimitives(
                self.ns_name,
                application,
                init_config,
            )

            """
            ExecutePrimitives will return a list of uuids. We need to check the
             status of each. The test continues if all Actions succeed, and
             fails if any of them fail.
            """
            await self.wait_for_uuids(application, uuids)
            debug("Primitives for {} finished.".format(application))

            return True
        except Exception as ex:
            debug("execute_initial_config_primitives exception: {}".format(ex))
            raise ex
            
        return False

    @classmethod
    async def check_metrics(self, application, task=None):
        """Check and run metrics, if present.

        Checks to see if metrics are specified by the charm. If so, collects
        the metrics.

        If no metrics, then mark the test as finished.
        """
        if has_metrics(self.charms[application]['name']):
            debug("Collecting metrics for {}".format(application))

            metrics = await self.n2vc.GetMetrics(
                self.ns_name,
                application,
            )

            return await self.verify_metrics(application, metrics)

    @classmethod
    async def verify_metrics(self, application, metrics):
        """Verify the charm's metrics.

        Verify that the charm has sent metrics successfully.

        Stops the test when finished.
        """
        debug("Verifying metrics for {}: {}".format(application, metrics))

        if len(metrics):
            return True

        else:
            # TODO: Ran into a case where it took 9 attempts before metrics
            # were available; the controller is slow sometimes.
            await asyncio.sleep(30)
            return await self.check_metrics(application)

    @classmethod
    async def wait_for_uuids(self, application, uuids):
        """Wait for primitives to execute.

        The task will provide a list of uuids representing primitives that are
        queued to run.
        """
        debug("Waiting for uuids for {}: {}".format(application, uuids))
        waitfor = len(uuids)
        finished = 0

        while waitfor > finished:
            for uid in uuids:
                await asyncio.sleep(10)

                if uuid not in self.state[application]['actions']:
                    self.state[application]['actions'][uid] = "pending"

                status = self.state[application]['actions'][uid]

                # Have we already marked this as done?
                if status in ["pending", "running"]:

                    debug("Getting status of {} ({})...".format(uid, status))
                    status = await self.n2vc.GetPrimitiveStatus(
                        self.ns_name,
                        uid,
                    )
                    debug("...state of {} is {}".format(uid, status))
                    self.state[application]['actions'][uid] = status

                    if status in ['completed', 'failed']:
                        finished += 1

            debug("{}/{} actions complete".format(finished, waitfor))

            # Wait for the primitive to finish and try again
            if waitfor > finished:
                debug("Waiting 10s for action to finish...")
                await asyncio.sleep(10)

    @classmethod
    def n2vc_callback(self, *args, **kwargs):
        (model, application, status, message) = args
        # debug("callback: {}".format(args))

        if application not in self.state:
            # Initialize the state of the application
            self.state[application] = {
                'status': None,     # Juju status
                'container': None,  # lxd container, for proxy charms
                'actions': {},      # Actions we've executed
                'done': False,      # Are we done testing this charm?
                'phase': "deploy",  # What phase is this application in?
            }

        self.state[application]['status'] = status

        if status in ['waiting', 'maintenance', 'unknown']:
            # Nothing to do for these
            return

        debug("callback: {}".format(args))

        if self.state[application]['done']:
            debug("{} is done".format(application))
            return

        if status in ['error']:
            # To test broken charms, if a charm enters an error state we should
            # end the test
            debug("{} is in an error state, stop the test.".format(application))
            # asyncio.ensure_future(self.stop())
            self.state[application]['done'] = True
            assert False

        if status in ["blocked"] and self.isproxy(application):
            if self.state[application]['phase'] == "deploy":
                debug("Configuring proxy charm for {}".format(application))
                asyncio.ensure_future(self.configure_proxy_charm(*args))

        elif status in ["active"]:
            """When a charm is active, we can assume that it has been properly
            configured (not blocked), regardless of if it's a proxy or not.

            All primitives should be complete by init_config_primitive
            """
            asyncio.ensure_future(self.execute_charm_tests(*args))
