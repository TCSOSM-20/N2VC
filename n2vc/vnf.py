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
import base64
import binascii
import logging
import os.path
import re
import shlex
import ssl
import subprocess

from juju.client import client
from juju.controller import Controller
from juju.errors import JujuAPIError, JujuError
from juju.model import ModelObserver

import n2vc.exceptions
from n2vc.provisioner import SSHProvisioner


# import time
# FIXME: this should load the juju inside or modules without having to
# explicitly install it. Check why it's not working.
# Load our subtree of the juju library
# path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
# path = os.path.join(path, "modules/libjuju/")
# if path not in sys.path:
#     sys.path.insert(1, path)
# We might need this to connect to the websocket securely, but test and verify.
try:
    ssl._create_default_https_context = ssl._create_unverified_context
except AttributeError:
    # Legacy Python doesn't verify by default (see pep-0476)
    #   https://www.python.org/dev/peps/pep-0476/
    pass


# Custom exceptions
# Deprecated. Please use n2vc.exceptions namespace.
class JujuCharmNotFound(Exception):
    """The Charm can't be found or is not readable."""


class JujuApplicationExists(Exception):
    """The Application already exists."""


class N2VCPrimitiveExecutionFailed(Exception):
    """Something failed while attempting to execute a primitive."""


class NetworkServiceDoesNotExist(Exception):
    """The Network Service being acted against does not exist."""


class PrimitiveDoesNotExist(Exception):
    """The Primitive being executed does not exist."""


# Quiet the debug logging
logging.getLogger("websockets.protocol").setLevel(logging.INFO)
logging.getLogger("juju.client.connection").setLevel(logging.WARN)
logging.getLogger("juju.model").setLevel(logging.WARN)
logging.getLogger("juju.machine").setLevel(logging.WARN)


class VCAMonitor(ModelObserver):
    """Monitor state changes within the Juju Model."""

    log = None

    def __init__(self, ns_name):
        self.log = logging.getLogger(__name__)

        self.ns_name = ns_name
        self.applications = {}

    def AddApplication(self, application_name, callback, *callback_args):
        if application_name not in self.applications:
            self.applications[application_name] = {
                "callback": callback,
                "callback_args": callback_args,
            }

    def RemoveApplication(self, application_name):
        if application_name in self.applications:
            del self.applications[application_name]

    async def on_change(self, delta, old, new, model):
        """React to changes in the Juju model."""

        if delta.entity == "unit":
            # Ignore change events from other applications
            if delta.data["application"] not in self.applications.keys():
                return

            try:

                application_name = delta.data["application"]

                callback = self.applications[application_name]["callback"]
                callback_args = self.applications[application_name]["callback_args"]

                if old and new:
                    # Fire off a callback with the application state
                    if callback:
                        callback(
                            self.ns_name,
                            delta.data["application"],
                            new.workload_status,
                            new.workload_status_message,
                            *callback_args,
                        )

                if old and not new:
                    # This is a charm being removed
                    if callback:
                        callback(
                            self.ns_name,
                            delta.data["application"],
                            "removed",
                            "",
                            *callback_args,
                        )
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
    def __init__(
        self,
        log=None,
        server="127.0.0.1",
        port=17070,
        user="admin",
        secret=None,
        artifacts=None,
        loop=None,
        juju_public_key=None,
        ca_cert=None,
        api_proxy=None,
    ):
        """Initialize N2VC

        Initializes the N2VC object, allowing the caller to interoperate with the VCA.


        :param log obj: The logging object to log to
        :param server str: The IP Address or Hostname of the Juju controller
        :param port int: The port of the Juju Controller
        :param user str: The Juju username to authenticate with
        :param secret str: The Juju password to authenticate with
        :param artifacts str: The directory where charms required by a vnfd are
            stored.
        :param loop obj: The loop to use.
        :param juju_public_key str: The contents of the Juju public SSH key
        :param ca_cert str: The CA certificate to use to authenticate
        :param api_proxy str: The IP of the host machine

        :Example:
        client = n2vc.vnf.N2VC(
            log=log,
            server='10.1.1.28',
            port=17070,
            user='admin',
            secret='admin',
            artifacts='/app/storage/myvnf/charms',
            loop=loop,
            juju_public_key='<contents of the juju public key>',
            ca_cert='<contents of CA certificate>',
            api_proxy='192.168.1.155'
        )
        """

        # Initialize instance-level variables
        self.api = None
        self.log = None
        self.controller = None
        self.connecting = False
        self.authenticated = False
        self.api_proxy = api_proxy

        if log:
            self.log = log
        else:
            self.log = logging.getLogger(__name__)

        # For debugging
        self.refcount = {
            "controller": 0,
            "model": 0,
        }

        self.models = {}

        # Model Observers
        self.monitors = {}

        # VCA config
        self.hostname = ""
        self.port = 17070
        self.username = ""
        self.secret = ""

        self.juju_public_key = juju_public_key
        if juju_public_key:
            self._create_juju_public_key(juju_public_key)
        else:
            self.juju_public_key = ""

        # TODO: Verify ca_cert is valid before using. VCA will crash
        # if the ca_cert isn't formatted correctly.
        def base64_to_cacert(b64string):
            """Convert the base64-encoded string containing the VCA CACERT.

            The input string....

            """
            try:
                cacert = base64.b64decode(b64string).decode("utf-8")

                cacert = re.sub(r"\\n", r"\n", cacert,)
            except binascii.Error as e:
                self.log.debug("Caught binascii.Error: {}".format(e))
                raise n2vc.exceptions.N2VCInvalidCertificate("Invalid CA Certificate")

            return cacert

        self.ca_cert = None
        if ca_cert:
            self.ca_cert = base64_to_cacert(ca_cert)

        # Quiet websocket traffic
        logging.getLogger("websockets.protocol").setLevel(logging.INFO)
        logging.getLogger("juju.client.connection").setLevel(logging.WARN)
        logging.getLogger("model").setLevel(logging.WARN)
        # logging.getLogger('websockets.protocol').setLevel(logging.DEBUG)

        self.log.debug("JujuApi: instantiated")

        self.server = server
        self.port = port

        self.secret = secret
        if user.startswith("user-"):
            self.user = user
        else:
            self.user = "user-{}".format(user)

        self.endpoint = "%s:%d" % (server, int(port))

        self.artifacts = artifacts

        self.loop = loop or asyncio.get_event_loop()

    def __del__(self):
        """Close any open connections."""
        yield self.logout()

    def _create_juju_public_key(self, public_key):
        """Recreate the Juju public key on disk.

        Certain libjuju commands expect to be run from the same machine as Juju
         is bootstrapped to. This method will write the public key to disk in
         that location: ~/.local/share/juju/ssh/juju_id_rsa.pub
        """
        # Make sure that we have a public key before writing to disk
        if public_key is None or len(public_key) == 0:
            if "OSM_VCA_PUBKEY" in os.environ:
                public_key = os.getenv("OSM_VCA_PUBKEY", "")
                if len(public_key == 0):
                    return
            else:
                return

        path = "{}/.local/share/juju/ssh".format(os.path.expanduser("~"),)
        if not os.path.exists(path):
            os.makedirs(path)

            with open("{}/juju_id_rsa.pub".format(path), "w") as f:
                f.write(public_key)

    def notify_callback(
        self,
        model_name,
        application_name,
        status,
        message,
        callback=None,
        *callback_args
    ):
        try:
            if callback:
                callback(
                    model_name, application_name, status, message, *callback_args,
                )
        except Exception as e:
            self.log.error("[0] notify_callback exception {}".format(e))
            raise e
        return True

    # Public methods
    async def Relate(self, model_name, vnfd):
        """Create a relation between the charm-enabled VDUs in a VNF.

        The Relation mapping has two parts: the id of the vdu owning the endpoint, and
        the name of the endpoint.

        vdu:
            ...
            vca-relationships:
                relation:
                -   provides: dataVM:db
                    requires: mgmtVM:app

        This tells N2VC that the charm referred to by the dataVM vdu offers a relation
        named 'db', and the mgmtVM vdu
        has an 'app' endpoint that should be connected to a database.

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
            juju = vnf_config["juju"]
            if juju:
                configs.append(vnf_config)

        for vdu in vnfd["vdu"]:
            vdu_config = vdu.get("vdu-configuration")
            if vdu_config:
                juju = vdu_config["juju"]
                if juju:
                    configs.append(vdu_config)

        def _get_application_name(name):
            """Get the application name that's mapped to a vnf/vdu."""
            vnf_member_index = 0
            vnf_name = vnfd["name"]

            for vdu in vnfd.get("vdu"):
                # Compare the named portion of the relation to the vdu's id
                if vdu["id"] == name:
                    application_name = self.FormatApplicationName(
                        model_name, vnf_name, str(vnf_member_index),
                    )
                    return application_name
                else:
                    vnf_member_index += 1

            return None

        # Loop through relations
        for cfg in configs:
            if "juju" in cfg:
                juju = cfg["juju"]
                if (
                    "vca-relationships" in juju
                    and "relation" in juju["vca-relationships"]
                ):
                    for rel in juju["vca-relationships"]["relation"]:
                        try:

                            # get the application name for the provides
                            (name, endpoint) = rel["provides"].split(":")
                            application_name = _get_application_name(name)

                            provides = "{}:{}".format(application_name, endpoint)

                            # get the application name for thr requires
                            (name, endpoint) = rel["requires"].split(":")
                            application_name = _get_application_name(name)

                            requires = "{}:{}".format(application_name, endpoint)
                            self.log.debug(
                                "Relation: {} <-> {}".format(provides, requires)
                            )
                            await self.add_relation(
                                model_name, provides, requires,
                            )
                        except Exception as e:
                            self.log.debug("Exception: {}".format(e))

        return

    async def DeployCharms(
        self,
        model_name,
        application_name,
        vnfd,
        charm_path,
        params={},
        machine_spec={},
        callback=None,
        *callback_args
    ):
        """Deploy one or more charms associated with a VNF.

        Deploy the charm(s) referenced in a VNF Descriptor.

        :param str model_name: The name or unique id of the network service.
        :param str application_name: The name of the application
        :param dict vnfd: The name of the application
        :param str charm_path: The path to the Juju charm
        :param dict params: A dictionary of runtime parameters
          Examples::
          {
            'rw_mgmt_ip': '1.2.3.4',
            # Pass the initial-config-primitives section of the vnf or vdu
            'initial-config-primitives': {...}
            'user_values': dictionary with the day-1 parameters provided at
                instantiation time. It will replace values
                inside < >. rw_mgmt_ip will be included here also
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
                "error",
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
        model = await self.get_model(model_name)

        ########################################
        # Verify the application doesn't exist #
        ########################################
        app = await self.get_application(model, application_name)
        if app:
            raise JujuApplicationExists(
                (
                    'Can\'t deploy application "{}" to model '
                    ' "{}" because it already exists.'
                ).format(application_name, model_name)
            )

        ################################################################
        # Register this application with the model-level event monitor #
        ################################################################
        if callback:
            self.log.debug(
                "JujuApi: Registering callback for {}".format(application_name,)
            )
            await self.Subscribe(model_name, application_name, callback, *callback_args)

        #######################################
        # Get the initial charm configuration #
        #######################################

        rw_mgmt_ip = None
        if "rw_mgmt_ip" in params:
            rw_mgmt_ip = params["rw_mgmt_ip"]

        if "initial-config-primitive" not in params:
            params["initial-config-primitive"] = {}

        initial_config = self._get_config_from_dict(
            params["initial-config-primitive"], {"<rw_mgmt_ip>": rw_mgmt_ip}
        )

        ########################################################
        # Check for specific machine placement (native charms) #
        ########################################################
        to = ""
        series = "xenial"

        if machine_spec.keys():
            if all(k in machine_spec for k in ["hostname", "username"]):

                # Allow series to be derived from the native charm
                series = None

                self.log.debug(
                    "Provisioning manual machine {}@{}".format(
                        machine_spec["username"], machine_spec["hostname"],
                    )
                )

                """Native Charm support

                Taking a bare VM (assumed to be an Ubuntu cloud image),
                the provisioning process will:
                - Create an ubuntu user w/sudo access
                - Detect hardware
                - Detect architecture
                - Download and install Juju agent from controller
                - Enable Juju agent
                - Add an iptables rule to route traffic to the API proxy
                """

                to = await self.provision_machine(
                    model_name=model_name,
                    username=machine_spec["username"],
                    hostname=machine_spec["hostname"],
                    private_key_path=self.GetPrivateKeyPath(),
                )
                self.log.debug("Provisioned machine id {}".format(to))

                # TODO: If to is none, raise an exception

                # The native charm won't have the sshproxy layer, typically, but LCM
                # uses the config primitive
                # to interpret what the values are. That's a gap to fill.

                """
                The ssh-* config parameters are unique to the sshproxy layer,
                which most native charms will not be aware of.

                Setting invalid config parameters will cause the deployment to
                fail.

                For the moment, we will strip the ssh-* parameters from native
                charms, until the feature gap is addressed in the information
                model.
                """

                # Native charms don't include the ssh-* config values, so strip them
                # from the initial_config, otherwise the deploy will raise an error.
                # self.log.debug("Removing ssh-* from initial-config")
                for k in ["ssh-hostname", "ssh-username", "ssh-password"]:
                    if k in initial_config:
                        self.log.debug("Removing parameter {}".format(k))
                        del initial_config[k]

        self.log.debug(
            "JujuApi: Deploying charm ({}/{}) from {} to {}".format(
                model_name, application_name, charm_path, to,
            )
        )

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
            series=series,
            # Apply the initial 'config' primitive during deployment
            config=initial_config,
            # Where to deploy the charm to.
            to=to,
        )

        #############################
        # Map the vdu id<->app name #
        #############################
        try:
            await self.Relate(model_name, vnfd)
        except KeyError as ex:
            # We don't currently support relations between NS and VNF/VDU charms
            self.log.warn("[N2VC] Relations not supported: {}".format(ex))
        except Exception:
            # This may happen if not all of the charms needed by the relation
            # are ready. We can safely ignore this, because Relate will be
            # retried when the endpoint of the relation is deployed.
            self.log.warn("[N2VC] Relations not ready")

        # #######################################
        # # Execute initial config primitive(s) #
        # #######################################
        uuids = await self.ExecuteInitialPrimitives(
            model_name, application_name, params,
        )
        return uuids

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
        homedir = os.environ["HOME"]
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
        # public_key = ""

        # Find the path to where we expect our key to live.
        homedir = os.environ["HOME"]
        sshdir = "{}/.ssh".format(homedir)
        if not os.path.exists(sshdir):
            os.mkdir(sshdir)

        private_key_path = "{}/id_n2vc_rsa".format(sshdir)
        public_key_path = "{}.pub".format(private_key_path)

        # If we don't have a key generated, generate it.
        if not os.path.exists(private_key_path):
            cmd = "ssh-keygen -t {} -b {} -N '' -f {}".format(
                "rsa", "4096", private_key_path
            )
            subprocess.check_output(shlex.split(cmd))

        # Read the public key
        with open(public_key_path, "r") as f:
            public_key = f.readline()

        return public_key

    async def ExecuteInitialPrimitives(
        self, model_name, application_name, params, callback=None, *callback_args
    ):
        """Execute multiple primitives.

        Execute multiple primitives as declared in initial-config-primitive.
        This is useful in cases where the primitives initially failed -- for
        example, if the charm is a proxy but the proxy hasn't been configured
        yet.
        """
        uuids = []
        primitives = {}

        # Build a sequential list of the primitives to execute
        for primitive in params["initial-config-primitive"]:
            try:
                if primitive["name"] == "config":
                    pass
                else:
                    seq = primitive["seq"]

                    params_ = {}
                    if "parameter" in primitive:
                        params_ = primitive["parameter"]

                    user_values = params.get("user_values", {})
                    if "rw_mgmt_ip" not in user_values:
                        user_values["rw_mgmt_ip"] = None
                        # just for backward compatibility, because it will be provided
                        # always by modern version of LCM

                    primitives[seq] = {
                        "name": primitive["name"],
                        "parameters": self._map_primitive_parameters(
                            params_, user_values
                        ),
                    }

                    for primitive in sorted(primitives):
                        try:
                            # self.log.debug("Queuing action {}".format(
                            # primitives[primitive]['name']))
                            uuids.append(
                                await self.ExecutePrimitive(
                                    model_name,
                                    application_name,
                                    primitives[primitive]["name"],
                                    callback,
                                    callback_args,
                                    **primitives[primitive]["parameters"],
                                )
                            )
                        except PrimitiveDoesNotExist as e:
                            self.log.debug(
                                "Ignoring exception PrimitiveDoesNotExist: {}".format(e)
                            )
                            pass
                        except Exception as e:
                            self.log.debug(
                                (
                                    "XXXXXXXXXXXXXXXXXXXXXXXXX Unexpected exception: {}"
                                ).format(e)
                            )
                            raise e

            except N2VCPrimitiveExecutionFailed as e:
                self.log.debug("[N2VC] Exception executing primitive: {}".format(e))
                raise
        return uuids

    async def ExecutePrimitive(
        self,
        model_name,
        application_name,
        primitive,
        callback,
        *callback_args,
        **params
    ):
        """Execute a primitive of a charm for Day 1 or Day 2 configuration.

        Execute a primitive defined in the VNF descriptor.

        :param str model_name: The name or unique id of the network service.
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
        self.log.debug("Executing primitive={} params={}".format(primitive, params))
        uuid = None
        try:
            if not self.authenticated:
                await self.login()

            model = await self.get_model(model_name)

            if primitive == "config":
                # config is special, and expecting params to be a dictionary
                await self.set_config(
                    model, application_name, params["params"],
                )
            else:
                app = await self.get_application(model, application_name)
                if app:
                    # Does this primitive exist?
                    actions = await app.get_actions()

                    if primitive not in actions.keys():
                        raise PrimitiveDoesNotExist(
                            "Primitive {} does not exist".format(primitive)
                        )

                    # Run against the first (and probably only) unit in the app
                    unit = app.units[0]
                    if unit:
                        action = await unit.run_action(primitive, **params)
                        uuid = action.id
        except PrimitiveDoesNotExist as e:
            # Catch and raise this exception if it's thrown from the inner block
            raise e
        except Exception as e:
            # An unexpected exception was caught
            self.log.debug("Caught exception while executing primitive: {}".format(e))
            raise N2VCPrimitiveExecutionFailed(e)
        return uuid

    async def RemoveCharms(
        self, model_name, application_name, callback=None, *callback_args
    ):
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
                await self.Unsubscribe(model_name, application_name)

                # self.notify_callback(model_name, application_name, "removing",
                # callback, *callback_args)
                self.log.debug("Removing the application {}".format(application_name))
                await app.remove()

                # await self.disconnect_model(self.monitors[model_name])

                self.notify_callback(
                    model_name,
                    application_name,
                    "removed",
                    "Removing charm {}".format(application_name),
                    callback,
                    *callback_args,
                )

        except Exception as e:
            print("Caught exception: {}".format(e))
            self.log.debug(e)
            raise e

    async def CreateNetworkService(self, ns_uuid):
        """Create a new Juju model for the Network Service.

        Creates a new Model in the Juju Controller.

        :param str ns_uuid: A unique id representing an instaance of a
            Network Service.

        :returns: True if the model was created. Raises JujuError on failure.
        """
        if not self.authenticated:
            await self.login()

        models = await self.controller.list_models()
        if ns_uuid not in models:
            # Get the new model
            await self.get_model(ns_uuid)

        return True

    async def DestroyNetworkService(self, ns_uuid):
        """Destroy a Network Service.

        Destroy the Network Service and any deployed charms.

        :param ns_uuid The unique id of the Network Service

        :returns: True if the model was created. Raises JujuError on failure.
        """

        # Do not delete the default model. The default model was used by all
        # Network Services, prior to the implementation of a model per NS.
        if ns_uuid.lower() == "default":
            return False

        if not self.authenticated:
            await self.login()

        models = await self.controller.list_models()
        if ns_uuid in models:
            model = await self.controller.get_model(ns_uuid)

            for application in model.applications:
                app = model.applications[application]

                await self.RemoveCharms(ns_uuid, application)

                self.log.debug("Unsubscribing Watcher for {}".format(application))
                await self.Unsubscribe(ns_uuid, application)

                self.log.debug("Waiting for application to terminate")
                timeout = 30
                try:
                    await model.block_until(
                        lambda: all(
                            unit.workload_status in ["terminated"] for unit in app.units
                        ),
                        timeout=timeout,
                    )
                except Exception:
                    self.log.debug(
                        "Timed out waiting for {} to terminate.".format(application)
                    )

            for machine in model.machines:
                try:
                    self.log.debug("Destroying machine {}".format(machine))
                    await model.machines[machine].destroy(force=True)
                except JujuAPIError as e:
                    if "does not exist" in str(e):
                        # Our cached model may be stale, because the machine
                        # has already been removed. It's safe to continue.
                        continue
                    else:
                        self.log.debug("Caught exception: {}".format(e))
                        raise e

        # Disconnect from the Model
        if ns_uuid in self.models:
            self.log.debug("Disconnecting model {}".format(ns_uuid))
            # await self.disconnect_model(self.models[ns_uuid])
            await self.disconnect_model(ns_uuid)

        try:
            self.log.debug("Destroying model {}".format(ns_uuid))
            await self.controller.destroy_models(ns_uuid)
        except JujuError:
            raise NetworkServiceDoesNotExist(
                "The Network Service '{}' does not exist".format(ns_uuid)
            )

        return True

    async def GetMetrics(self, model_name, application_name):
        """Get the metrics collected by the VCA.

        :param model_name The name or unique id of the network service
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

    async def Subscribe(self, ns_name, application_name, callback, *callback_args):
        """Subscribe to callbacks for an application.

        :param ns_name str: The name of the Network Service
        :param application_name str: The name of the application
        :param callback obj: The callback method
        :param callback_args list: The list of arguments to append to calls to
            the callback method
        """
        self.monitors[ns_name].AddApplication(
            application_name, callback, *callback_args
        )

    async def Unsubscribe(self, ns_name, application_name):
        """Unsubscribe to callbacks for an application.

        Unsubscribes the caller from notifications from a deployed application.

        :param ns_name str: The name of the Network Service
        :param application_name str: The name of the application
        """
        self.monitors[ns_name].RemoveApplication(application_name,)

    # Non-public methods
    async def add_relation(self, model_name, relation1, relation2):
        """
        Add a relation between two application endpoints.

        :param str model_name: The name or unique id of the network service
        :param str relation1: '<application>[:<relation_name>]'
        :param str relation2: '<application>[:<relation_name>]'
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
            if "not found" in e.message:
                return
            if "already exists" in e.message:
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
            if primitive["name"] == "config":
                # config = self._map_primitive_parameters()
                for parameter in primitive["parameter"]:
                    param = str(parameter["name"])
                    if parameter["value"] == "<rw_mgmt_ip>":
                        config[param] = str(values[parameter["value"]])
                    else:
                        config[param] = str(parameter["value"])

        return config

    def _map_primitive_parameters(self, parameters, user_values):
        params = {}
        for parameter in parameters:
            param = str(parameter["name"])
            value = parameter.get("value")

            # map parameters inside a < >; e.g. <rw_mgmt_ip>. with the provided user
            # _values.
            # Must exist at user_values except if there is a default value
            if isinstance(value, str) and value.startswith("<") and value.endswith(">"):
                if parameter["value"][1:-1] in user_values:
                    value = user_values[parameter["value"][1:-1]]
                elif "default-value" in parameter:
                    value = parameter["default-value"]
                else:
                    raise KeyError(
                        "parameter {}='{}' not supplied ".format(param, value)
                    )

            # If there's no value, use the default-value (if set)
            if value is None and "default-value" in parameter:
                value = parameter["default-value"]

            # Typecast parameter value, if present
            paramtype = "string"
            try:
                if "data-type" in parameter:
                    paramtype = str(parameter["data-type"]).lower()

                    if paramtype == "integer":
                        value = int(value)
                    elif paramtype == "boolean":
                        value = bool(value)
                    else:
                        value = str(value)
                else:
                    # If there's no data-type, assume the value is a string
                    value = str(value)
            except ValueError:
                raise ValueError(
                    "parameter {}='{}' cannot be converted to type {}".format(
                        param, value, paramtype
                    )
                )

            params[param] = value
        return params

    def _get_config_from_yang(self, config_primitive, values):
        """Transform the yang config primitive to dict."""
        config = {}
        for primitive in config_primitive.values():
            if primitive["name"] == "config":
                for parameter in primitive["parameter"].values():
                    param = str(parameter["name"])
                    if parameter["value"] == "<rw_mgmt_ip>":
                        config[param] = str(values[parameter["value"]])
                    else:
                        config[param] = str(parameter["value"])

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
        return re.sub("-+", "-", appname.lower())

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

        return name.replace("_", "-").lower()

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

    async def get_model(self, model_name):
        """Get a model from the Juju Controller.

        Note: Model objects returned must call disconnected() before it goes
        out of scope."""
        if not self.authenticated:
            await self.login()

        if model_name not in self.models:
            # Get the models in the controller
            models = await self.controller.list_models()

            if model_name not in models:
                try:
                    self.models[model_name] = await self.controller.add_model(
                        model_name, config={"authorized-keys": self.juju_public_key}
                    )
                except JujuError as e:
                    if "already exists" not in e.message:
                        raise e
            else:
                self.models[model_name] = await self.controller.get_model(model_name)

            self.refcount["model"] += 1

            # Create an observer for this model
            await self.create_model_monitor(model_name)

        return self.models[model_name]

    async def create_model_monitor(self, model_name):
        """Create a monitor for the model, if none exists."""
        if not self.authenticated:
            await self.login()

        if model_name not in self.monitors:
            self.monitors[model_name] = VCAMonitor(model_name)
            self.models[model_name].add_observer(self.monitors[model_name])

        return True

    async def login(self):
        """Login to the Juju controller."""

        if self.authenticated:
            return

        self.connecting = True

        self.log.debug("JujuApi: Logging into controller")

        self.controller = Controller(loop=self.loop)

        if self.secret:
            self.log.debug(
                "Connecting to controller... ws://{} as {}/{}".format(
                    self.endpoint, self.user, self.secret,
                )
            )
            try:
                await self.controller.connect(
                    endpoint=self.endpoint,
                    username=self.user,
                    password=self.secret,
                    cacert=self.ca_cert,
                )
                self.refcount["controller"] += 1
                self.authenticated = True
                self.log.debug("JujuApi: Logged into controller")
            except Exception as ex:
                self.log.debug("Caught exception: {}".format(ex))
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
            self.authenticated = False

    async def logout(self):
        """Logout of the Juju controller."""
        if not self.authenticated:
            return False

        try:
            for model in self.models:
                await self.disconnect_model(model)

            if self.controller:
                self.log.debug("Disconnecting controller {}".format(self.controller))
                await self.controller.disconnect()
                self.refcount["controller"] -= 1
                self.controller = None

            self.authenticated = False

            self.log.debug(self.refcount)

        except Exception as e:
            self.log.fatal("Fatal error logging out of Juju Controller: {}".format(e))
            raise e
        return True

    async def disconnect_model(self, model):
        self.log.debug("Disconnecting model {}".format(model))
        if model in self.models:
            try:
                await self.models[model].disconnect()
                self.refcount["model"] -= 1
                self.models[model] = None
            except Exception as e:
                self.log.debug("Caught exception: {}".format(e))

    async def provision_machine(
        self, model_name: str, hostname: str, username: str, private_key_path: str
    ) -> int:
        """Provision a machine.

        This executes the SSH provisioner, which will log in to a machine via
        SSH and prepare it for use with the Juju model

        :param model_name str: The name of the model
        :param hostname str: The IP or hostname of the target VM
        :param user str: The username to login to
        :param private_key_path str: The path to the private key that's been injected
            to the VM via cloud-init
        :return machine_id int: Returns the id of the machine or None if provisioning
            fails
        """
        if not self.authenticated:
            await self.login()

        machine_id = None

        if self.api_proxy:
            self.log.debug(
                "Instantiating SSH Provisioner for {}@{} ({})".format(
                    username, hostname, private_key_path
                )
            )
            provisioner = SSHProvisioner(
                host=hostname,
                user=username,
                private_key_path=private_key_path,
                log=self.log,
            )

            params = None
            try:
                params = provisioner.provision_machine()
            except Exception as ex:
                self.log.debug("caught exception from provision_machine: {}".format(ex))
                return None

            if params:
                params.jobs = ["JobHostUnits"]

                model = await self.get_model(model_name)

                connection = model.connection()

                # Submit the request.
                self.log.debug("Adding machine to model")
                client_facade = client.ClientFacade.from_connection(connection)
                results = await client_facade.AddMachines(params=[params])
                error = results.machines[0].error
                if error:
                    raise ValueError("Error adding machine: %s" % error.message)

                machine_id = results.machines[0].machine

                # Need to run this after AddMachines has been called,
                # as we need the machine_id
                self.log.debug("Installing Juju agent")
                await provisioner.install_agent(
                    connection, params.nonce, machine_id, self.api_proxy,
                )
        else:
            self.log.debug("Missing API Proxy")
        return machine_id

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

        # m = await self.get_model()
        # try:
        #    m.remove_relation(a, b)
        # finally:
        #    await m.disconnect()

    async def resolve_error(self, model_name, application=None):
        """Resolve units in error state."""
        if not self.authenticated:
            await self.login()

        model = await self.get_model(model_name)

        app = await self.get_application(model, application)
        if app:
            self.log.debug(
                "JujuApi: Resolving errors for application {}".format(application,)
            )

            for _ in app.units:
                app.resolved(retry=True)

    async def run_action(self, model_name, application, action_name, **params):
        """Execute an action and return an Action object."""
        if not self.authenticated:
            await self.login()
        result = {"status": "", "action": {"tag": None, "results": None}}

        model = await self.get_model(model_name)

        app = await self.get_application(model, application)
        if app:
            # We currently only have one unit per application
            # so use the first unit available.
            unit = app.units[0]

            self.log.debug(
                "JujuApi: Running Action {} against Application {}".format(
                    action_name, application,
                )
            )

            action = await unit.run_action(action_name, **params)

            # Wait for the action to complete
            await action.wait()

            result["status"] = action.status
            result["action"]["tag"] = action.data["id"]
            result["action"]["results"] = action.results

        return result

    async def set_config(self, model_name, application, config):
        """Apply a configuration to the application."""
        if not self.authenticated:
            await self.login()

        app = await self.get_application(model_name, application)
        if app:
            self.log.debug(
                "JujuApi: Setting config for Application {}".format(application,)
            )
            await app.set_config(config)

            # Verify the config is set
            newconf = await app.get_config()
            for key in config:
                if config[key] != newconf[key]["value"]:
                    self.log.debug(
                        (
                            "JujuApi: Config not set! Key {} Value {} doesn't match {}"
                        ).format(key, config[key], newconf[key])
                    )

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

    async def wait_for_application(self, model_name, application_name, timeout=300):
        """Wait for an application to become active."""
        if not self.authenticated:
            await self.login()

        model = await self.get_model(model_name)

        app = await self.get_application(model, application_name)
        self.log.debug("Application: {}".format(app))
        if app:
            self.log.debug(
                "JujuApi: Waiting {} seconds for Application {}".format(
                    timeout, application_name,
                )
            )

            await model.block_until(
                lambda: all(
                    unit.agent_status == "idle"
                    and unit.workload_status in ["active", "unknown"]
                    for unit in app.units
                ),
                timeout=timeout,
            )
