#!/usr/bin/env python3
import asyncio
import functools

import logging
import n2vc.vnf
import pylxd
import pytest
import os
import shlex
import shutil
import subprocess
import tempfile
import time
import uuid
import yaml

from juju.controller import Controller

# Disable InsecureRequestWarning w/LXD
import urllib3
urllib3.disable_warnings()
logging.getLogger("urllib3").setLevel(logging.WARNING)

here = os.path.dirname(os.path.realpath(__file__))


def is_bootstrapped():
    result = subprocess.run(['juju', 'switch'], stdout=subprocess.PIPE)
    return (
        result.returncode == 0 and
        len(result.stdout.decode().strip()) > 0)


bootstrapped = pytest.mark.skipif(
    not is_bootstrapped(),
    reason='bootstrapped Juju environment required')


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


def get_charm_path():
    return "{}/charms".format(here)


def get_layer_path():
    return "{}/charms/layers".format(here)


def parse_metrics(application, results):
    """Parse the returned metrics into a dict."""

    # We'll receive the results for all units, to look for the one we want
    # Caveat: we're grabbing results from the first unit of the application,
    # which is enough for testing, since we're only deploying a single unit.
    retval = {}
    for unit in results:
        if unit.startswith(application):
            for result in results[unit]:
                retval[result['key']] = result['value']
    return retval


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
        tmp = yaml.load(descriptor)

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

    # Running under tox/pytest makes getting env variables harder.

    # Extract parameters from the environment in order to run our test
    vca_host = os.getenv('VCA_HOST', '127.0.0.1')
    vca_port = os.getenv('VCA_PORT', 17070)
    vca_user = os.getenv('VCA_USER', 'admin')
    vca_charms = os.getenv('VCA_CHARMS', None)
    vca_secret = os.getenv('VCA_SECRET', None)

    client = n2vc.vnf.N2VC(
        log=log,
        server=vca_host,
        port=vca_port,
        user=vca_user,
        secret=vca_secret,
        artifacts=vca_charms,
        loop=loop
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
    test_machine = "test-{}-{}".format(
        uuid.uuid4().hex[-4:],
        name,
    )

    private_key_path, public_key_path = find_juju_ssh_keys()

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

    wait_for_network(container)

    # HACK: We need to give sshd a chance to bind to the interface,
    # and pylxd's container.execute seems to be broken and fails and/or
    # hangs trying to properly check if the service is up.
    time.sleep(5)
    client = None

    return container


def destroy_lxd_container(container):
    """Stop and delete a LXD container."""
    name = container.name
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

    container.stop(wait=False)
    wait_for_stop()

    container.delete(wait=False)
    wait_for_delete()

    # Delete the profile created for this container
    profile = client.profiles.get(name)
    if profile:
        profile.delete()


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


def find_juju_ssh_keys():
    """Find the Juju ssh keys."""

    paths = []
    paths.append(os.path.expanduser("~/.local/share/juju/ssh/"))

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


def get_lxd_client(host="127.0.0.1", port="8443", verify=False):
    """ Get the LXD client."""
    client = None
    (crt, key) = find_lxd_config()

    if crt and key:
        client = pylxd.Client(
            endpoint="https://{}:{}".format(host, port),
            cert=(crt, key),
            verify=verify,
        )

    return client

# TODO: This is marked serial but can be run in parallel with work, including:
# - Fixing an event loop issue; seems that all tests stop when one test stops?


@pytest.mark.serial
class TestN2VC(object):
    """TODO:
    1. Validator Validation

    Automatically validate the descriptors we're using here, unless the test author explicitly wants to skip them. Useful to make sure tests aren't being run against invalid descriptors, validating functionality that may fail against a properly written descriptor.

    We need to have a flag (instance variable) that controls this behavior. It may be necessary to skip validation and run against a descriptor implementing features that have not yet been released in the Information Model.
    """

    @classmethod
    def setup_class(self):
        """ setup any state specific to the execution of the given class (which
        usually contains tests).
        """
        # Initialize instance variable(s)
        # self.container = None

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
            self.get_charm(charm)

        # A bit of a hack, in order to allow the N2VC callback to run parallel
        # to pytest. Test(s) should wait for this flag to change to False
        # before returning.
        self._running = True

    @classmethod
    def teardown_class(self):
        """ teardown any state that was previously setup with a call to
        setup_class.
        """
        for application in self.state:
            logging.warn(
                "Destroying container for application {}".format(application)
            )
            if self.state[application]['container']:
                destroy_lxd_container(self.state[application]['container'])

        # Clean up any artifacts created during the test
        logging.debug("Artifacts: {}".format(self.artifacts))
        for charm in self.artifacts:
            artifact = self.artifacts[charm]
            if os.path.exists(artifact['tmpdir']):
                logging.debug("Removing directory '{}'".format(artifact))
                shutil.rmtree(artifact['tmpdir'])
        #
        # Logout of N2VC
        if self.n2vc:
            asyncio.ensure_future(self.n2vc.logout())
        logging.debug("Tearing down")
        pass

    @classmethod
    def all_charms_active(self):
        """Determine if the all deployed charms are active."""
        active = 0
        for application in self.charms:
            if self.charms[application]['status'] == 'active':
                active += 1

        if active == len(self.charms):
            logging.warn("All charms active!")
            return True

        return False

    @classmethod
    def running(self, timeout=600):
        """Returns if the test is still running.

        @param timeout The time, in seconds, to wait for the test to complete.
        """

        # if start + now > start > timeout:
        # self.stop_test()
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
        try:
            subprocess.check_call(['which', 'charm'])
        except subprocess.CalledProcessError as e:
            raise Exception("charm snap not installed.")

        if charm not in self.artifacts:
            try:
                # Note: This builds the charm under N2VC/tests/charms/
                # The snap-installed command only has write access to the users $HOME
                # so writing to /tmp isn't possible at the moment.
                builds = tempfile.mkdtemp(dir=get_charm_path())

                cmd = "charm build {}/{} -o {}/".format(
                    get_layer_path(),
                    charm,
                    builds,
                )
                logging.debug(cmd)

                subprocess.check_call(shlex.split(cmd))

                self.artifacts[charm] = {
                    'tmpdir': builds,
                    'charm': "{}/builds/{}".format(builds, charm),
                }
            except subprocess.CalledProcessError as e:
                raise Exception("charm build failed: {}.".format(e))

        return self.artifacts[charm]['charm']

    @classmethod
    async def deploy(self, vnf_index, charm, params, loop):
        """An inner function to do the deployment of a charm from
        either a vdu or vnf.
        """

        self.n2vc = get_n2vc(loop=loop)

        vnf_name = self.n2vc.FormatApplicationName(
            self.ns_name,
            self.vnf_name,
            str(vnf_index),
        )
        logging.debug("Deploying charm at {}".format(self.artifacts[charm]))

        await self.n2vc.DeployCharms(
            self.ns_name,
            vnf_name,
            self.vnfd,
            self.get_charm(charm),
            params,
            {},
            self.n2vc_callback
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

        # logging.debug(self.charms[application_name])
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
    async def CreateContainer(self, *args):
        """Create a LXD container for use with a proxy charm.abs

        1. Get the public key from the charm via `get-ssh-public-key` action
        2. Create container with said key injected for the ubuntu user
        """
        # Create and configure a LXD container for use with a proxy charm.
        (model, application, _, _) = args
        # self.state[application_name]

        print("trying to create container")
        if self.state[application]['container'] is None:
            logging.debug(
                "Creating container for application {}".format(application)
            )
            # HACK: Set this so the n2vc_callback knows
            # there's a container being created
            self.state[application]['container'] = True

            # Execute 'get-ssh-public-key' primitive and get returned value
            uuid = await self.n2vc.ExecutePrimitive(
                model,
                application,
                "get-ssh-public-key",
                None,
            )
            result = await self.n2vc.GetPrimitiveOutput(model, uuid)
            pubkey = result['pubkey']

            self.state[application]['container'] = create_lxd_container(
                public_key=pubkey,
                name=os.path.basename(__file__)
            )

        return self.state[application]['container']

    @classmethod
    async def stop():
        """Stop the test.

        - Remove charms
        - Stop and delete containers
        - Logout of N2VC
        """
        logging.warning("Stop the test.")
        assert True
        for application in self.charms:
            try:
                logging.warn("Removing charm")
                await self.n2vc.RemoveCharms(model, application)

                logging.warn(
                    "Destroying container for application {}".format(application)
                )
                if self.state[application]['container']:
                    destroy_lxd_container(self.state[application]['container'])
            except Exception as e:
                logging.warn("Error while deleting container: {}".format(e))

        # Clean up any artifacts created during the test
        logging.debug("Artifacts: {}".format(self.artifacts))
        for charm in self.artifacts:
            artifact = self.artifacts[charm]
            if os.path.exists(artifact['tmpdir']):
                logging.debug("Removing directory '{}'".format(artifact))
                shutil.rmtree(artifact['tmpdir'])

        # Logout of N2VC
        await self.n2vc.logout()
        self.n2vc = None

        self._running = False

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
    def n2vc_callback(self, *args, **kwargs):
        """Monitor and react to changes in the charm state.

        This is where we will monitor the state of the charm:
        - is it active?
        - is it in error?
        - is it waiting on input to continue?

        When the state changes, we respond appropriately:
        - configuring ssh credentials for a proxy charm
        - running a service primitive

        Lastly, when the test has finished we begin the teardown, removing the
        charm and associated LXD container, and notify pytest that this test
        is over.

        Args are expected to contain four values, received from N2VC:
        - str, the name of the model
        - str, the name of the application
        - str, the workload status as reported by Juju
        - str, the workload message as reported by Juju
        """
        (model, application, status, message) = args
        # logging.warn("Callback for {}/{} - {} ({})".format(
        #     model,
        #     application,
        #     status,
        #     message
        # ))

        if application not in self.state:
            # Initialize the state of the application
            self.state[application] = {
                'status': None,
                'container': None,
            }

        # Make sure we're only receiving valid status. This will catch charms
        # that aren't setting their workload state and appear as "unknown"
        # assert status not in ["active", "blocked", "waiting", "maintenance"]

        task = None
        if kwargs and 'task' in kwargs:
            task = kwargs['task']
            # logging.debug("Got task: {}".format(task))

        # if application in self.charms:
        self.state[application]['status'] = status

        # Closures and inner functions, oh my.
        def is_active():
            """Is the charm in an active state?"""
            if status in ["active"]:
                return True
            return False

        def is_blocked():
            """Is the charm waiting for us?"""
            if status in ["blocked"]:
                return True
            return False

        def configure_ssh_proxy(task):
            """Configure the proxy charm to use the lxd container."""
            logging.debug("configure_ssh_proxy({})".format(task))

            mgmtaddr = self.get_container_ip(
                self.state[application]['container'],
            )

            logging.debug(
                "Setting config ssh-hostname={}".format(mgmtaddr)
            )

            # task = asyncio.ensure_future(
            #     stop_test,
            # )
            # return

            task = asyncio.ensure_future(
                self.n2vc.ExecutePrimitive(
                    model,
                    application,
                    "config",
                    None,
                    params={
                        'ssh-hostname': mgmtaddr,
                        'ssh-username': 'ubuntu',
                    }
                )
            )

            # Execute the VNFD's 'initial-config-primitive'
            task.add_done_callback(functools.partial(
                execute_initial_config_primitives,
            ))

        def execute_initial_config_primitives(task=None):
            logging.debug("execute_initial_config_primitives({})".format(task))

            init_config = self.charms[application]

            """
            The initial-config-primitive is run during deploy but may fail
             on some steps because proxy charm access isn't configured.

            At this stage, we'll re-run those actions.
            """

            task = asyncio.ensure_future(
                self.n2vc.ExecuteInitialPrimitives(
                    model,
                    application,
                    init_config,
                )
            )

            """
            ExecutePrimitives will return a list of uuids. We need to check the
             status of each. The test continues if all Actions succeed, and
             fails if any of them fail.
            """
            task.add_done_callback(functools.partial(wait_for_uuids))

        def check_metrics():
            task = asyncio.ensure_future(
                self.n2vc.GetMetrics(
                    model,
                    application,
                )
            )

            task.add_done_callback(
                functools.partial(
                    verify_metrics,
                )
            )

        def verify_metrics(task):
            logging.debug("Verifying metrics!")
            # Check if task returned metrics
            results = task.result()

            metrics = parse_metrics(application, results)
            logging.debug(metrics)

            if len(metrics):
                logging.warn("[metrics] removing charms")
                task = asyncio.ensure_future(
                    self.n2vc.RemoveCharms(model, application)
                )

                task.add_done_callback(functools.partial(self.stop))

            else:
                # TODO: Ran into a case where it took 9 attempts before metrics
                # were available; the controller is slow sometimes.
                time.sleep(60)
                check_metrics()

        def wait_for_uuids(task):
            logging.debug("wait_for_uuids({})".format(task))
            uuids = task.result()

            waitfor = len(uuids)
            finished = 0

            def get_primitive_result(uuid, task):
                logging.debug("Got result from action")
                # completed, failed, or running
                result = task.result()

                if status in result and result['status'] \
                   in ["completed", "failed"]:

                    # It's over
                    logging.debug("Action {} is {}".format(
                        uuid,
                        task.result['status'])
                    )
                    pass
                else:
                    logging.debug("action is still running")

            def get_primitive_status(uuid, task):
                result = task.result()

                if result == "completed":
                    # Make sure all primitives are finished
                    global finished
                    finished += 1

                    if waitfor == finished:
                        if self.all_charms_active():
                            logging.debug("Action complete; removing charm")
                            task = asyncio.ensure_future(
                                self.stop,
                            )
                            # task = asyncio.ensure_future(
                            # self.n2vc.RemoveCharms(model, application)
                            # )
                            # task.add_done_callback(functools.partial(stop_test))
                        else:
                            logging.warn("Not all charms in an active state.")
                elif result == "failed":
                    logging.debug("Action failed; removing charm")
                    task = asyncio.ensure_future(
                        self.stop,
                    )
                    # task = asyncio.ensure_future(
                        # self.n2vc.RemoveCharms(model, application)
                    # )
                    # task.add_done_callback(functools.partial(stop_test))

                    # assert False
                    # self._running = False
                    # return
                else:
                    # logging.debug("action is still running: {}".format(result))
                    # logging.debug(result)
                    # pass
                    # The primitive is running; try again.
                    task = asyncio.ensure_future(
                        self.n2vc.GetPrimitiveStatus(model, uuid)
                    )
                    task.add_done_callback(functools.partial(
                        get_primitive_result,
                        uuid,
                    ))

            for actionid in uuids:
                task = asyncio.ensure_future(
                    self.n2vc.GetPrimitiveStatus(model, actionid)
                )
                task.add_done_callback(functools.partial(
                    get_primitive_result,
                    actionid,
                ))

        # def stop_test(task):
        #     """Stop the test.
        #
        #     When the test has either succeeded or reached a failing state,
        #     begin the process of removing the test fixtures.
        #     """
        #     for application in self.charms:
        #         asyncio.ensure_future(
        #             self.n2vc.RemoveCharms(model, application)
        #         )
        #
        #     self._running = False

        if is_blocked():
            # Container only applies to proxy charms.
            if self.isproxy(application):

                if self.state[application]['container'] is None:
                    logging.warn("Creating new container")
                    # Create the new LXD container

                    task = asyncio.ensure_future(self.CreateContainer(*args))

                    # Configure the proxy charm to use the container when ready
                    task.add_done_callback(functools.partial(
                        configure_ssh_proxy,
                    ))

                    # task.add_done_callback(functools.partial(
                    #     stop_test,
                    # ))
                    # create_lxd_container()
                    # self.container = True
                # else:
                #     logging.warn("{} already has container".format(application))
                #
                #     task = asyncio.ensure_future(
                #         self.n2vc.RemoveCharms(model, application)
                #     )
                #     task.add_done_callback(functools.partial(stop_test))

            else:
                # A charm may validly be in a blocked state if it's waiting for
                # relations or some other kind of manual intervention
                # logging.debug("This is not a proxy charm.")
                # TODO: needs testing
                task = asyncio.ensure_future(
                    execute_initial_config_primitives()
                )

                # task.add_done_callback(functools.partial(stop_test))

        elif is_active():
            # Does the charm have metrics defined?
            if has_metrics(self.charms[application]['name']):
                # logging.debug("metrics.yaml defined in the layer!")

                # Force a run of the metric collector, so we don't have
                # to wait for it's normal 5 minute interval run.
                # NOTE: this shouldn't be done outside of CI
                collect_metrics(application)

                # get the current metrics
                check_metrics()
            else:
                # When the charm reaches an active state and hasn't been
                # handled (metrics collection, etc)., the test has succeded.
                # logging.debug("Charm is active! Removing charm...")
                if self.all_charms_active():
                    logging.warn("All charms active!")
                    task = asyncio.ensure_future(
                        self.stop(),
                    )

                    # task = asyncio.ensure_future(
                    #     self.n2vc.RemoveCharms(model, application)
                    # )
                    # task.add_done_callback(functools.partial(stop_test))
                else:
                    logging.warning("Waiting for all charms to be active.")
                    # task = asyncio.ensure_future(
                    #     self.n2vc.RemoveCharms(model, application)
                    # )
                    # task.add_done_callback(functools.partial(stop_test))
