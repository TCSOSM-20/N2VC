import asyncio
import logging
import os
import os.path
import re
import shlex
import ssl
import subprocess
import sys
# import time

# FIXME: this should load the juju inside or modules without having to
# explicitly install it. Check why it's not working.
# Load our subtree of the juju library
path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
path = os.path.join(path, "modules/libjuju/")
if path not in sys.path:
    sys.path.insert(1, path)

from juju.controller import Controller
from juju.model import ModelObserver
from juju.errors import JujuAPIError

# We might need this to connect to the websocket securely, but test and verify.
try:
    ssl._create_default_https_context = ssl._create_unverified_context
except AttributeError:
    # Legacy Python doesn't verify by default (see pep-0476)
    #   https://www.python.org/dev/peps/pep-0476/
    pass


# Custom exceptions
class JujuCharmNotFound(Exception):
    """The Charm can't be found or is not readable."""


class JujuApplicationExists(Exception):
    """The Application already exists."""


class N2VCPrimitiveExecutionFailed(Exception):
    """Something failed while attempting to execute a primitive."""


# Quiet the debug logging
logging.getLogger('websockets.protocol').setLevel(logging.INFO)
logging.getLogger('juju.client.connection').setLevel(logging.WARN)
logging.getLogger('juju.model').setLevel(logging.WARN)
logging.getLogger('juju.machine').setLevel(logging.WARN)


class VCAMonitor(ModelObserver):
    """Monitor state changes within the Juju Model."""
    log = None
    ns_name = None
    applications = {}

    def __init__(self, ns_name):
        self.log = logging.getLogger(__name__)

        self.ns_name = ns_name

    def AddApplication(self, application_name, callback, *callback_args):
        if application_name not in self.applications:
            self.applications[application_name] = {
                'callback': callback,
                'callback_args': callback_args
            }

    def RemoveApplication(self, application_name):
        if application_name in self.applications:
            del self.applications[application_name]

    async def on_change(self, delta, old, new, model):
        """React to changes in the Juju model."""

        if delta.entity == "unit":
            # Ignore change events from other applications
            if delta.data['application'] not in self.applications.keys():
                return

            try:

                application_name = delta.data['application']

                callback = self.applications[application_name]['callback']
                callback_args = \
                    self.applications[application_name]['callback_args']

                if old and new:
                    # Fire off a callback with the application state
                    if callback:
                        callback(
                            self.ns_name,
                            delta.data['application'],
                            new.workload_status,
                            new.workload_status_message,
                            *callback_args)

                if old and not new:
                    # This is a charm being removed
                    if callback:
                        callback(
                            self.ns_name,
                            delta.data['application'],
                            "removed",
                            "",
                            *callback_args)
            except Exception as e:
                self.log.debug("[1] notify_callback exception: {}".format(e))

        elif delta.entity == "action":
            # TODO: Decide how we want to notify the user of actions

            # uuid = delta.data['id']     # The Action's unique id
            # msg = delta.data['message'] # The output of the action
            #
            # if delta.data['status'] == "pending":
            #     # The action is queued
            #     pass
            # elif delta.data['status'] == "completed""
            #     # The action was successful
            #     pass
            # elif delta.data['status'] == "failed":
            #     # The action failed.
            #     pass

            pass

########
# TODO
#
# Create unique models per network service
# Document all public functions


class N2VC:
    def __init__(self,
                 log=None,
                 server='127.0.0.1',
                 port=17070,
                 user='admin',
                 secret=None,
                 artifacts=None,
                 loop=None,
                 ):
        """Initialize N2VC

        :param vcaconfig dict A dictionary containing the VCA configuration

        :param artifacts str The directory where charms required by a vnfd are
            stored.

        :Example:
        n2vc = N2VC(vcaconfig={
            'secret': 'MzI3MDJhOTYxYmM0YzRjNTJiYmY1Yzdm',
            'user': 'admin',
            'ip-address': '10.44.127.137',
            'port': 17070,
            'artifacts': '/path/to/charms'
        })
        """

        # Initialize instance-level variables
        self.api = None
        self.log = None
        self.controller = None
        self.connecting = False
        self.authenticated = False

        # For debugging
        self.refcount = {
            'controller': 0,
            'model': 0,
        }

        self.models = {}
        self.default_model = None

        # Model Observers
        self.monitors = {}

        # VCA config
        self.hostname = ""
        self.port = 17070
        self.username = ""
        self.secret = ""

        if log:
            self.log = log
        else:
            self.log = logging.getLogger(__name__)

        # Quiet websocket traffic
        logging.getLogger('websockets.protocol').setLevel(logging.INFO)
        logging.getLogger('juju.client.connection').setLevel(logging.WARN)
        logging.getLogger('model').setLevel(logging.WARN)
        # logging.getLogger('websockets.protocol').setLevel(logging.DEBUG)

        self.log.debug('JujuApi: instantiated')

        self.server = server
        self.port = port

        self.secret = secret
        if user.startswith('user-'):
            self.user = user
        else:
            self.user = 'user-{}'.format(user)

        self.endpoint = '%s:%d' % (server, int(port))

        self.artifacts = artifacts

        self.loop = loop or asyncio.get_event_loop()

    def __del__(self):
        """Close any open connections."""
        yield self.logout()

    def notify_callback(self, model_name, application_name, status, message,
                        callback=None, *callback_args):
        try:
            if callback:
                callback(
                    model_name,
                    application_name,
                    status, message,
                    *callback_args,
                )
        except Exception as e:
            self.log.error("[0] notify_callback exception {}".format(e))
            raise e
        return True

    # Public methods
    async def CreateNetworkService(self, nsd):
        """Create a new model to encapsulate this network service.

        Create a new model in the Juju controller to encapsulate the
        charms associated with a network service.

        You can pass either the nsd record or the id of the network
        service, but this method will fail without one of them.
        """
        if not self.authenticated:
            await self.login()

        # Ideally, we will create a unique model per network service.
        # This change will require all components, i.e., LCM and SO, to use
        # N2VC for 100% compatibility. If we adopt unique models for the LCM,
        # services deployed via LCM would't be manageable via SO and vice versa

        return self.default_model

    async def Relate(self, ns_name, vnfd):
        """Create a relation between the charm-enabled VDUs in a VNF.

        The Relation mapping has two parts: the id of the vdu owning the endpoint, and the name of the endpoint.

        vdu:
            ...
            relation:
            -   provides: dataVM:db
                requires: mgmtVM:app

        This tells N2VC that the charm referred to by the dataVM vdu offers a relation named 'db', and the mgmtVM vdu has an 'app' endpoint that should be connected to a database.

        :param str ns_name: The name of the network service.
        :param dict vnfd: The parsed yaml VNF descriptor.
        """

        # Currently, the call to Relate() is made automatically after the
        # deployment of each charm; if the relation depends on a charm that
        # hasn't been deployed yet, the call will fail silently. This will
        # prevent an API breakage, with the intent of making this an explicitly
        # required call in a more object-oriented refactor of the N2VC API.

        configs = []
        vnf_config = vnfd.get("vnf-configuration")
        if vnf_config:
            juju = vnf_config['juju']
            if juju:
                configs.append(vnf_config)

        for vdu in vnfd['vdu']:
            vdu_config = vdu.get('vdu-configuration')
            if vdu_config:
                juju = vdu_config['juju']
                if juju:
                    configs.append(vdu_config)

        def _get_application_name(name):
            """Get the application name that's mapped to a vnf/vdu."""
            vnf_member_index = 0
            vnf_name = vnfd['name']

            for vdu in vnfd.get('vdu'):
                # Compare the named portion of the relation to the vdu's id
                if vdu['id'] == name:
                    application_name = self.FormatApplicationName(
                        ns_name,
                        vnf_name,
                        str(vnf_member_index),
                    )
                    return application_name
                else:
                    vnf_member_index += 1

            return None

        # Loop through relations
        for cfg in configs:
            if 'juju' in cfg:
                if 'relation' in juju:
                    for rel in juju['relation']:
                        try:

                            # get the application name for the provides
                            (name, endpoint) = rel['provides'].split(':')
                            application_name = _get_application_name(name)

                            provides = "{}:{}".format(
                                application_name,
                                endpoint
                            )

                            # get the application name for thr requires
                            (name, endpoint) = rel['requires'].split(':')
                            application_name = _get_application_name(name)

                            requires = "{}:{}".format(
                                application_name,
                                endpoint
                            )
                            self.log.debug("Relation: {} <-> {}".format(
                                provides,
                                requires
                            ))
                            await self.add_relation(
                                ns_name,
                                provides,
                                requires,
                            )
                        except Exception as e:
                            self.log.debug("Exception: {}".format(e))

        return

    async def DeployCharms(self, model_name, application_name, vnfd,
                           charm_path, params={}, machine_spec={},
                           callback=None, *callback_args):
        """Deploy one or more charms associated with a VNF.

        Deploy the charm(s) referenced in a VNF Descriptor.

        :param str model_name: The name of the network service.
        :param str application_name: The name of the application
        :param dict vnfd: The name of the application
        :param str charm_path: The path to the Juju charm
        :param dict params: A dictionary of runtime parameters
          Examples::
          {
            'rw_mgmt_ip': '1.2.3.4',
            # Pass the initial-config-primitives section of the vnf or vdu
            'initial-config-primitives': {...}
          }
        :param dict machine_spec: A dictionary describing the machine to
        install to
          Examples::
          {
            'hostname': '1.2.3.4',
            'username': 'ubuntu',
          }
        :param obj callback: A callback function to receive status changes.
        :param tuple callback_args: A list of arguments to be passed to the
        callback
        """

        ########################################################
        # Verify the path to the charm exists and is readable. #
        ########################################################
        if not os.path.exists(charm_path):
            self.log.debug("Charm path doesn't exist: {}".format(charm_path))
            self.notify_callback(
                model_name,
                application_name,
                "failed",
                callback,
                *callback_args,
            )
            raise JujuCharmNotFound("No artifacts configured.")

        ################################
        # Login to the Juju controller #
        ################################
        if not self.authenticated:
            self.log.debug("Authenticating with Juju")
            await self.login()

        ##########################################
        # Get the model for this network service #
        ##########################################
        # TODO: In a point release, we will use a model per deployed network
        # service. In the meantime, we will always use the 'default' model.
        model_name = 'default'
        model = await self.get_model(model_name)

        ########################################
        # Verify the application doesn't exist #
        ########################################
        app = await self.get_application(model, application_name)
        if app:
            raise JujuApplicationExists("Can't deploy application \"{}\" to model \"{}\" because it already exists.".format(application_name, model_name))

        ################################################################
        # Register this application with the model-level event monitor #
        ################################################################
        if callback:
            self.monitors[model_name].AddApplication(
                application_name,
                callback,
                *callback_args
            )

        ########################################################
        # Check for specific machine placement (native charms) #
        ########################################################
        to = ""
        if machine_spec.keys():
            if all(k in machine_spec for k in ['hostname', 'username']):
                # Get the path to the previously generated ssh private key.
                # Machines we're manually provisioned must have N2VC's public
                # key injected, so if we don't have a keypair, raise an error.
                private_key_path = ""

                # Enlist the existing machine in Juju
                machine = await self.model.add_machine(
                    spec='ssh:{}@{}:{}'.format(
                        specs['host'],
                        specs['user'],
                        private_key_path,
                    )
                )
                # Set the machine id that the deploy below will use.
                to = machine.id
            pass

        #######################################
        # Get the initial charm configuration #
        #######################################

        rw_mgmt_ip = None
        if 'rw_mgmt_ip' in params:
            rw_mgmt_ip = params['rw_mgmt_ip']

        if 'initial-config-primitive' not in params:
            params['initial-config-primitive'] = {}

        initial_config = self._get_config_from_dict(
            params['initial-config-primitive'],
            {'<rw_mgmt_ip>': rw_mgmt_ip}
        )

        self.log.debug("JujuApi: Deploying charm ({}) from {}".format(
            application_name,
            charm_path,
            to=to,
        ))

        ########################################################
        # Deploy the charm and apply the initial configuration #
        ########################################################
        app = await model.deploy(
            # We expect charm_path to be either the path to the charm on disk
            # or in the format of cs:series/name
            charm_path,
            # This is the formatted, unique name for this charm
            application_name=application_name,
            # Proxy charms should use the current LTS. This will need to be
            # changed for native charms.
            series='xenial',
            # Apply the initial 'config' primitive during deployment
            config=initial_config,
            # Where to deploy the charm to.
            to=to,
        )

        # Map the vdu id<->app name,
        #
        await self.Relate(model_name, vnfd)

        # #######################################
        # # Execute initial config primitive(s) #
        # #######################################
        await self.ExecuteInitialPrimitives(
            model_name,
            application_name,
            params,
        )

        # primitives = {}
        #
        # # Build a sequential list of the primitives to execute
        # for primitive in params['initial-config-primitive']:
        #     try:
        #         if primitive['name'] == 'config':
        #             # This is applied when the Application is deployed
        #             pass
        #         else:
        #             seq = primitive['seq']
        #
        #             params = {}
        #             if 'parameter' in primitive:
        #                 params = primitive['parameter']
        #
        #             primitives[seq] = {
        #                 'name': primitive['name'],
        #                 'parameters': self._map_primitive_parameters(
        #                     params,
        #                     {'<rw_mgmt_ip>': rw_mgmt_ip}
        #                 ),
        #             }
        #
        #             for primitive in sorted(primitives):
        #                 await self.ExecutePrimitive(
        #                     model_name,
        #                     application_name,
        #                     primitives[primitive]['name'],
        #                     callback,
        #                     callback_args,
        #                     **primitives[primitive]['parameters'],
        #                 )
        #     except N2VCPrimitiveExecutionFailed as e:
        #         self.log.debug(
        #             "[N2VC] Exception executing primitive: {}".format(e)
        #         )
        #         raise

    async def GetPrimitiveStatus(self, model_name, uuid):
        """Get the status of an executed Primitive.

        The status of an executed Primitive will be one of three values:
        - completed
        - failed
        - running
        """
        status = None
        try:
            if not self.authenticated:
                await self.login()

            # FIXME: This is hard-coded until model-per-ns is added
            model_name = 'default'

            model = await self.get_model(model_name)

            results = await model.get_action_status(uuid)

            if uuid in results:
                status = results[uuid]

        except Exception as e:
            self.log.debug(
                "Caught exception while getting primitive status: {}".format(e)
            )
            raise N2VCPrimitiveExecutionFailed(e)

        return status

    async def GetPrimitiveOutput(self, model_name, uuid):
        """Get the output of an executed Primitive.

        Note: this only returns output for a successfully executed primitive.
        """
        results = None
        try:
            if not self.authenticated:
                await self.login()

            # FIXME: This is hard-coded until model-per-ns is added
            model_name = 'default'

            model = await self.get_model(model_name)
            results = await model.get_action_output(uuid, 60)
        except Exception as e:
            self.log.debug(
                "Caught exception while getting primitive status: {}".format(e)
            )
            raise N2VCPrimitiveExecutionFailed(e)

        return results

    # async def ProvisionMachine(self, model_name, hostname, username):
    #     """Provision machine for usage with Juju.
    #
    #     Provisions a previously instantiated machine for use with Juju.
    #     """
    #     try:
    #         if not self.authenticated:
    #             await self.login()
    #
    #         # FIXME: This is hard-coded until model-per-ns is added
    #         model_name = 'default'
    #
    #         model = await self.get_model(model_name)
    #         model.add_machine(spec={})
    #
    #         machine = await model.add_machine(spec='ssh:{}@{}:{}'.format(
    #             "ubuntu",
    #             host['address'],
    #             private_key_path,
    #         ))
    #         return machine.id
    #
    #     except Exception as e:
    #         self.log.debug(
    #             "Caught exception while getting primitive status: {}".format(e)
    #         )
    #         raise N2VCPrimitiveExecutionFailed(e)

    def GetPrivateKeyPath(self):
        homedir = os.environ['HOME']
        sshdir = "{}/.ssh".format(homedir)
        private_key_path = "{}/id_n2vc_rsa".format(sshdir)
        return private_key_path

    async def GetPublicKey(self):
        """Get the N2VC SSH public key.abs

        Returns the SSH public key, to be injected into virtual machines to
        be managed by the VCA.

        The first time this is run, a ssh keypair will be created. The public
        key is injected into a VM so that we can provision the machine with
        Juju, after which Juju will communicate with the VM directly via the
        juju agent.
        """
        public_key = ""

        # Find the path to where we expect our key to live.
        homedir = os.environ['HOME']
        sshdir = "{}/.ssh".format(homedir)
        if not os.path.exists(sshdir):
            os.mkdir(sshdir)

        private_key_path = "{}/id_n2vc_rsa".format(sshdir)
        public_key_path = "{}.pub".format(private_key_path)

        # If we don't have a key generated, generate it.
        if not os.path.exists(private_key_path):
            cmd = "ssh-keygen -t {} -b {} -N '' -f {}".format(
                "rsa",
                "4096",
                private_key_path
            )
            subprocess.check_output(shlex.split(cmd))

        # Read the public key
        with open(public_key_path, "r") as f:
            public_key = f.readline()

        return public_key

    async def ExecuteInitialPrimitives(self, model_name, application_name,
                                       params, callback=None, *callback_args):
        """Execute multiple primitives.

        Execute multiple primitives as declared in initial-config-primitive.
        This is useful in cases where the primitives initially failed -- for
        example, if the charm is a proxy but the proxy hasn't been configured
        yet.
        """
        uuids = []
        primitives = {}

        # Build a sequential list of the primitives to execute
        for primitive in params['initial-config-primitive']:
            try:
                if primitive['name'] == 'config':
                    pass
                else:
                    seq = primitive['seq']

                    params = {}
                    if 'parameter' in primitive:
                        params = primitive['parameter']

                    primitives[seq] = {
                        'name': primitive['name'],
                        'parameters': self._map_primitive_parameters(
                            params,
                            {'<rw_mgmt_ip>': None}
                        ),
                    }

                    for primitive in sorted(primitives):
                        uuids.append(
                            await self.ExecutePrimitive(
                                model_name,
                                application_name,
                                primitives[primitive]['name'],
                                callback,
                                callback_args,
                                **primitives[primitive]['parameters'],
                            )
                        )
            except N2VCPrimitiveExecutionFailed as e:
                self.log.debug(
                    "[N2VC] Exception executing primitive: {}".format(e)
                )
                raise
        return uuids

    async def ExecutePrimitive(self, model_name, application_name, primitive,
                               callback, *callback_args, **params):
        """Execute a primitive of a charm for Day 1 or Day 2 configuration.

        Execute a primitive defined in the VNF descriptor.

        :param str model_name: The name of the network service.
        :param str application_name: The name of the application
        :param str primitive: The name of the primitive to execute.
        :param obj callback: A callback function to receive status changes.
        :param tuple callback_args: A list of arguments to be passed to the
         callback function.
        :param dict params: A dictionary of key=value pairs representing the
         primitive's parameters
          Examples::
          {
            'rw_mgmt_ip': '1.2.3.4',
            # Pass the initial-config-primitives section of the vnf or vdu
            'initial-config-primitives': {...}
          }
        """
        self.log.debug("Executing {}".format(primitive))
        uuid = None
        try:
            if not self.authenticated:
                await self.login()

            # FIXME: This is hard-coded until model-per-ns is added
            model_name = 'default'

            model = await self.get_model(model_name)

            if primitive == 'config':
                # config is special, and expecting params to be a dictionary
                await self.set_config(
                    model,
                    application_name,
                    params['params'],
                )
            else:
                app = await self.get_application(model, application_name)
                if app:
                    # Run against the first (and probably only) unit in the app
                    unit = app.units[0]
                    if unit:
                        action = await unit.run_action(primitive, **params)
                        uuid = action.id
        except Exception as e:
            self.log.debug(
                "Caught exception while executing primitive: {}".format(e)
            )
            raise N2VCPrimitiveExecutionFailed(e)
        return uuid

    async def RemoveCharms(self, model_name, application_name, callback=None,
                           *callback_args):
        """Remove a charm from the VCA.

        Remove a charm referenced in a VNF Descriptor.

        :param str model_name: The name of the network service.
        :param str application_name: The name of the application
        :param obj callback: A callback function to receive status changes.
        :param tuple callback_args: A list of arguments to be passed to the
         callback function.
        """
        try:
            if not self.authenticated:
                await self.login()

            model = await self.get_model(model_name)
            app = await self.get_application(model, application_name)
            if app:
                # Remove this application from event monitoring
                self.monitors[model_name].RemoveApplication(application_name)

                # self.notify_callback(model_name, application_name, "removing", callback, *callback_args)
                self.log.debug(
                    "Removing the application {}".format(application_name)
                )
                await app.remove()

                # Notify the callback that this charm has been removed.
                self.notify_callback(
                    model_name,
                    application_name,
                    "removed",
                    callback,
                    *callback_args,
                )

        except Exception as e:
            print("Caught exception: {}".format(e))
            self.log.debug(e)
            raise e

    async def DestroyNetworkService(self, nsd):
        raise NotImplementedError()

    async def GetMetrics(self, model_name, application_name):
        """Get the metrics collected by the VCA.

        :param model_name The name of the model
        :param application_name The name of the application
        """
        metrics = {}
        model = await self.get_model(model_name)
        app = await self.get_application(model, application_name)
        if app:
            metrics = await app.get_metrics()

        return metrics

    async def HasApplication(self, model_name, application_name):
        model = await self.get_model(model_name)
        app = await self.get_application(model, application_name)
        if app:
            return True
        return False

    # Non-public methods
    async def add_relation(self, model_name, relation1, relation2):
        """
        Add a relation between two application endpoints.

        :param str model_name Name of the network service.
        :param str relation1 '<application>[:<relation_name>]'
        :param str relation12 '<application>[:<relation_name>]'
        """

        if not self.authenticated:
            await self.login()

        m = await self.get_model(model_name)
        try:
            await m.add_relation(relation1, relation2)
        except JujuAPIError as e:
            # If one of the applications in the relationship doesn't exist,
            # or the relation has already been added, let the operation fail
            # silently.
            if 'not found' in e.message:
                return
            if 'already exists' in e.message:
                return

            raise e

    # async def apply_config(self, config, application):
    #     """Apply a configuration to the application."""
    #     print("JujuApi: Applying configuration to {}.".format(
    #         application
    #     ))
    #     return await self.set_config(application=application, config=config)

    def _get_config_from_dict(self, config_primitive, values):
        """Transform the yang config primitive to dict.

        Expected result:

            config = {
                'config':
            }
        """
        config = {}
        for primitive in config_primitive:
            if primitive['name'] == 'config':
                # config = self._map_primitive_parameters()
                for parameter in primitive['parameter']:
                    param = str(parameter['name'])
                    if parameter['value'] == "<rw_mgmt_ip>":
                        config[param] = str(values[parameter['value']])
                    else:
                        config[param] = str(parameter['value'])

        return config

    def _map_primitive_parameters(self, parameters, values):
        params = {}
        for parameter in parameters:
            param = str(parameter['name'])

            # Typecast parameter value, if present
            if 'data-type' in parameter:
                paramtype = str(parameter['data-type']).lower()
                value = None

                if paramtype == "integer":
                    value = int(parameter['value'])
                elif paramtype == "boolean":
                    value = bool(parameter['value'])
                else:
                    value = str(parameter['value'])

            if parameter['value'] == "<rw_mgmt_ip>":
                params[param] = str(values[parameter['value']])
            else:
                params[param] = value
        return params

    def _get_config_from_yang(self, config_primitive, values):
        """Transform the yang config primitive to dict."""
        config = {}
        for primitive in config_primitive.values():
            if primitive['name'] == 'config':
                for parameter in primitive['parameter'].values():
                    param = str(parameter['name'])
                    if parameter['value'] == "<rw_mgmt_ip>":
                        config[param] = str(values[parameter['value']])
                    else:
                        config[param] = str(parameter['value'])

        return config

    def FormatApplicationName(self, *args):
        """
        Generate a Juju-compatible Application name

        :param args tuple: Positional arguments to be used to construct the
        application name.

        Limitations::
        - Only accepts characters a-z and non-consequitive dashes (-)
        - Application name should not exceed 50 characters

        Examples::

            FormatApplicationName("ping_pong_ns", "ping_vnf", "a")
        """
        appname = ""
        for c in "-".join(list(args)):
            if c.isdigit():
                c = chr(97 + int(c))
            elif not c.isalpha():
                c = "-"
            appname += c
        return re.sub('\-+', '-', appname.lower())

    # def format_application_name(self, nsd_name, vnfr_name, member_vnf_index=0):
    #     """Format the name of the application
    #
    #     Limitations:
    #     - Only accepts characters a-z and non-consequitive dashes (-)
    #     - Application name should not exceed 50 characters
    #     """
    #     name = "{}-{}-{}".format(nsd_name, vnfr_name, member_vnf_index)
    #     new_name = ''
    #     for c in name:
    #         if c.isdigit():
    #             c = chr(97 + int(c))
    #         elif not c.isalpha():
    #             c = "-"
    #         new_name += c
    #     return re.sub('\-+', '-', new_name.lower())

    def format_model_name(self, name):
        """Format the name of model.

        Model names may only contain lowercase letters, digits and hyphens
        """

        return name.replace('_', '-').lower()

    async def get_application(self, model, application):
        """Get the deployed application."""
        if not self.authenticated:
            await self.login()

        app = None
        if application and model:
            if model.applications:
                if application in model.applications:
                    app = model.applications[application]

        return app

    async def get_model(self, model_name='default'):
        """Get a model from the Juju Controller.

        Note: Model objects returned must call disconnected() before it goes
        out of scope."""
        if not self.authenticated:
            await self.login()

        if model_name not in self.models:
            self.models[model_name] = await self.controller.get_model(
                model_name,
            )
            self.refcount['model'] += 1

            # Create an observer for this model
            self.monitors[model_name] = VCAMonitor(model_name)
            self.models[model_name].add_observer(self.monitors[model_name])

        return self.models[model_name]

    async def login(self):
        """Login to the Juju controller."""

        if self.authenticated:
            return

        self.connecting = True

        self.log.debug("JujuApi: Logging into controller")

        cacert = None
        self.controller = Controller(loop=self.loop)

        if self.secret:
            self.log.debug(
                "Connecting to controller... ws://{}:{} as {}/{}".format(
                    self.endpoint,
                    self.port,
                    self.user,
                    self.secret,
                )
            )
            await self.controller.connect(
                endpoint=self.endpoint,
                username=self.user,
                password=self.secret,
                cacert=cacert,
            )
            self.refcount['controller'] += 1
        else:
            # current_controller no longer exists
            # self.log.debug("Connecting to current controller...")
            # await self.controller.connect_current()
            # await self.controller.connect(
            #     endpoint=self.endpoint,
            #     username=self.user,
            #     cacert=cacert,
            # )
            self.log.fatal("VCA credentials not configured.")

        self.authenticated = True
        self.log.debug("JujuApi: Logged into controller")

    async def logout(self):
        """Logout of the Juju controller."""
        if not self.authenticated:
            return

        try:
            if self.default_model:
                self.log.debug("Disconnecting model {}".format(
                    self.default_model
                ))
                await self.default_model.disconnect()
                self.refcount['model'] -= 1
                self.default_model = None

            for model in self.models:
                await self.models[model].disconnect()
                self.refcount['model'] -= 1
                self.models[model] = None

            if self.controller:
                self.log.debug("Disconnecting controller {}".format(
                    self.controller
                ))
                await self.controller.disconnect()
                self.refcount['controller'] -= 1
                self.controller = None

            self.authenticated = False

            self.log.debug(self.refcount)

        except Exception as e:
            self.log.fatal(
                "Fatal error logging out of Juju Controller: {}".format(e)
            )
            raise e

    # async def remove_application(self, name):
    #     """Remove the application."""
    #     if not self.authenticated:
    #         await self.login()
    #
    #     app = await self.get_application(name)
    #     if app:
    #         self.log.debug("JujuApi: Destroying application {}".format(
    #             name,
    #         ))
    #
    #         await app.destroy()

    async def remove_relation(self, a, b):
        """
        Remove a relation between two application endpoints

        :param a An application endpoint
        :param b An application endpoint
        """
        if not self.authenticated:
            await self.login()

        m = await self.get_model()
        try:
            m.remove_relation(a, b)
        finally:
            await m.disconnect()

    async def resolve_error(self, application=None):
        """Resolve units in error state."""
        if not self.authenticated:
            await self.login()

        app = await self.get_application(self.default_model, application)
        if app:
            self.log.debug(
                "JujuApi: Resolving errors for application {}".format(
                    application,
                )
            )

            for unit in app.units:
                app.resolved(retry=True)

    async def run_action(self, application, action_name, **params):
        """Execute an action and return an Action object."""
        if not self.authenticated:
            await self.login()
        result = {
            'status': '',
            'action': {
                'tag': None,
                'results': None,
            }
        }
        app = await self.get_application(self.default_model, application)
        if app:
            # We currently only have one unit per application
            # so use the first unit available.
            unit = app.units[0]

            self.log.debug(
                "JujuApi: Running Action {} against Application {}".format(
                    action_name,
                    application,
                )
            )

            action = await unit.run_action(action_name, **params)

            # Wait for the action to complete
            await action.wait()

            result['status'] = action.status
            result['action']['tag'] = action.data['id']
            result['action']['results'] = action.results

        return result

    async def set_config(self, model_name, application, config):
        """Apply a configuration to the application."""
        if not self.authenticated:
            await self.login()

        app = await self.get_application(model_name, application)
        if app:
            self.log.debug("JujuApi: Setting config for Application {}".format(
                application,
            ))
            await app.set_config(config)

            # Verify the config is set
            newconf = await app.get_config()
            for key in config:
                if config[key] != newconf[key]['value']:
                    self.log.debug("JujuApi: Config not set! Key {} Value {} doesn't match {}".format(key, config[key], newconf[key]))

    # async def set_parameter(self, parameter, value, application=None):
    #     """Set a config parameter for a service."""
    #     if not self.authenticated:
    #         await self.login()
    #
    #     self.log.debug("JujuApi: Setting {}={} for Application {}".format(
    #         parameter,
    #         value,
    #         application,
    #     ))
    #     return await self.apply_config(
    #         {parameter: value},
    #         application=application,
        # )

    async def wait_for_application(self, model_name, application_name,
                                   timeout=300):
        """Wait for an application to become active."""
        if not self.authenticated:
            await self.login()

        # TODO: In a point release, we will use a model per deployed network
        # service. In the meantime, we will always use the 'default' model.
        model_name = 'default'
        model = await self.get_model(model_name)

        app = await self.get_application(model, application_name)
        self.log.debug("Application: {}".format(app))
        # app = await self.get_application(model_name, application_name)
        if app:
            self.log.debug(
                "JujuApi: Waiting {} seconds for Application {}".format(
                    timeout,
                    application_name,
                )
            )

            await model.block_until(
                lambda: all(
                    unit.agent_status == 'idle' and unit.workload_status in
                    ['active', 'unknown'] for unit in app.units
                ),
                timeout=timeout
            )
