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

import concurrent
from .exceptions import NotImplemented

import juju
# from juju.bundle import BundleHandler
from juju.controller import Controller
from juju.model import Model
from juju.errors import JujuAPIError, JujuError

import logging

from n2vc.k8s_conn import K8sConnector

import os
# import re
# import ssl
import subprocess
# from .vnf import N2VC

import uuid
import yaml


class K8sJujuConnector(K8sConnector):

    def __init__(
            self,
            fs: object,
            db: object,
            kubectl_command: str = '/usr/bin/kubectl',
            juju_command: str = '/usr/bin/juju',
            log=None,
            on_update_db=None,
    ):
        """

        :param kubectl_command: path to kubectl executable
        :param helm_command: path to helm executable
        :param fs: file system for kubernetes and helm configuration
        :param log: logger
        """

        # parent class
        K8sConnector.__init__(
            self,
            db,
            log=log,
            on_update_db=on_update_db,
        )

        self.fs = fs
        self.info('Initializing K8S Juju connector')

        self.authenticated = False
        self.models = {}
        self.log = logging.getLogger(__name__)

        self.juju_command = juju_command
        self.juju_secret = ""

        self.info('K8S Juju connector initialized')

    """Initialization"""
    async def init_env(
        self,
        k8s_creds: str,
        namespace: str = 'kube-system',
        reuse_cluster_uuid: str = None,
    ) -> (str, bool):
        """Initialize a Kubernetes environment

        :param k8s_creds dict: A dictionary containing the Kubernetes cluster
        configuration
        :param namespace str: The Kubernetes namespace to initialize

        :return: UUID of the k8s context or raises an exception
        """

        """Bootstrapping

        Bootstrapping cannot be done, by design, through the API. We need to
        use the CLI tools.
        """

        """
        WIP: Workflow

        1. Has the environment already been bootstrapped?
        - Check the database to see if we have a record for this env

        2. If this is a new env, create it
        - Add the k8s cloud to Juju
        - Bootstrap
        - Record it in the database

        3. Connect to the Juju controller for this cloud

        """
        # cluster_uuid = reuse_cluster_uuid
        # if not cluster_uuid:
        #     cluster_uuid = str(uuid4())

        ##################################################
        # TODO: Pull info from db based on the namespace #
        ##################################################

        if not reuse_cluster_uuid:
            # This is a new cluster, so bootstrap it

            cluster_uuid = str(uuid.uuid4())

            # Add k8s cloud to Juju (unless it's microk8s)

            # Does the kubeconfig contain microk8s?
            microk8s = self.is_microk8s_by_credentials(k8s_creds)

            # Name the new k8s cloud
            k8s_cloud = "{}-k8s".format(namespace)

            print("Adding k8s cloud {}".format(k8s_cloud))
            await self.add_k8s(k8s_cloud, k8s_creds)

            # Bootstrap Juju controller
            print("Bootstrapping...")
            await self.bootstrap(k8s_cloud, cluster_uuid, microk8s)
            print("Bootstrap done.")

            # Get the controller information

            # Parse ~/.local/share/juju/controllers.yaml
            # controllers.testing.api-endpoints|ca-cert|uuid
            print("Getting controller endpoints")
            with open(os.path.expanduser(
                "~/.local/share/juju/controllers.yaml"
            )) as f:
                controllers = yaml.load(f, Loader=yaml.Loader)
                controller = controllers['controllers'][cluster_uuid]
                endpoints = controller['api-endpoints']
                self.juju_endpoint = endpoints[0]
                self.juju_ca_cert = controller['ca-cert']

            # Parse ~/.local/share/juju/accounts
            # controllers.testing.user|password
            print("Getting accounts")
            with open(os.path.expanduser(
                "~/.local/share/juju/accounts.yaml"
            )) as f:
                controllers = yaml.load(f, Loader=yaml.Loader)
                controller = controllers['controllers'][cluster_uuid]

                self.juju_user = controller['user']
                self.juju_secret = controller['password']

            print("user: {}".format(self.juju_user))
            print("secret: {}".format(self.juju_secret))
            print("endpoint: {}".format(self.juju_endpoint))
            print("ca-cert: {}".format(self.juju_ca_cert))

            # raise Exception("EOL")

            self.juju_public_key = None

            config = {
                'endpoint': self.juju_endpoint,
                'username': self.juju_user,
                'secret': self.juju_secret,
                'cacert': self.juju_ca_cert,
                'namespace': namespace,
                'microk8s': microk8s,
            }

            # Store the cluster configuration so it
            # can be used for subsequent calls
            print("Setting config")
            await self.set_config(cluster_uuid, config)

        else:
            # This is an existing cluster, so get its config
            cluster_uuid = reuse_cluster_uuid

            config = self.get_config(cluster_uuid)

            self.juju_endpoint = config['endpoint']
            self.juju_user = config['username']
            self.juju_secret = config['secret']
            self.juju_ca_cert = config['cacert']
            self.juju_public_key = None

        # Login to the k8s cluster
        if not self.authenticated:
            await self.login(cluster_uuid)

        # We're creating a new cluster
        print("Getting model {}".format(self.get_namespace(cluster_uuid), cluster_uuid=cluster_uuid))
        model = await self.get_model(
            self.get_namespace(cluster_uuid), 
            cluster_uuid=cluster_uuid
        )

        # Disconnect from the model
        if model and model.is_connected():
            await model.disconnect()

        return cluster_uuid, True

    """Repo Management"""
    async def repo_add(
        self,
        name: str,
        url: str,
        type: str = "charm",
    ):
        raise NotImplemented()

    async def repo_list(self):
        raise NotImplemented()

    async def repo_remove(
        self,
        name: str,
    ):
        raise NotImplemented()

    """Reset"""
    async def reset(
            self,
            cluster_uuid: str,
            force: bool = False,
            uninstall_sw: bool = False
    ) -> bool:
        """Reset a cluster

        Resets the Kubernetes cluster by removing the model that represents it.

        :param cluster_uuid str: The UUID of the cluster to reset
        :return: Returns True if successful or raises an exception.
        """

        try:
            if not self.authenticated:
                await self.login(cluster_uuid)

            if self.controller.is_connected():
                # Destroy the model
                namespace = self.get_namespace(cluster_uuid)
                if await self.has_model(namespace):
                    print("[reset] Destroying model")
                    await self.controller.destroy_model(
                        namespace,
                        destroy_storage=True
                    )

                # Disconnect from the controller
                print("[reset] Disconnecting controller")
                await self.controller.disconnect()

                # Destroy the controller (via CLI)
                print("[reset] Destroying controller")
                await self.destroy_controller(cluster_uuid)

                """Remove the k8s cloud

                Only remove the k8s cloud if it's not a microk8s cloud,
                since microk8s is a built-in cloud type.
                """
                # microk8s = self.is_microk8s_by_cluster_uuid(cluster_uuid)
                # if not microk8s:
                print("[reset] Removing k8s cloud")
                namespace = self.get_namespace(cluster_uuid)
                k8s_cloud = "{}-k8s".format(namespace)
                await self.remove_cloud(k8s_cloud)

        except Exception as ex:
            print("Caught exception during reset: {}".format(ex))

    """Deployment"""

    async def install(
        self,
        cluster_uuid: str,
        kdu_model: str,
        atomic: bool = True,
        timeout: float = 300,
        params: dict = None,
        db_dict: dict = None
    ) -> bool:
        """Install a bundle

        :param cluster_uuid str: The UUID of the cluster to install to
        :param kdu_model str: The name or path of a bundle to install
        :param atomic bool: If set, waits until the model is active and resets
                            the cluster on failure.
        :param timeout int: The time, in seconds, to wait for the install
                            to finish
        :param params dict: Key-value pairs of instantiation parameters

        :return: If successful, returns ?
        """

        if not self.authenticated:
            print("[install] Logging in to the controller")
            await self.login(cluster_uuid)

        ##
        # Get or create the model, based on the namespace the cluster was
        # instantiated with.
        namespace = self.get_namespace(cluster_uuid)

        self.log.debug("Checking for model named {}".format(namespace))
        model = await self.get_model(namespace, cluster_uuid=cluster_uuid)
        if not model:
            # Create the new model
            self.log.debug("Adding model: {}".format(namespace))
            model = await self.add_model(namespace, cluster_uuid=cluster_uuid)

        if model:
            # TODO: Instantiation parameters

            """
            "Juju bundle that models the KDU, in any of the following ways:
                - <juju-repo>/<juju-bundle>
                - <juju-bundle folder under k8s_models folder in the package>
                - <juju-bundle tgz file (w/ or w/o extension) under k8s_models folder in the package>
                - <URL_where_to_fetch_juju_bundle>
            """

            bundle = kdu_model
            if kdu_model.startswith("cs:"):
                bundle = kdu_model
            elif kdu_model.startswith("http"):
                # Download the file
                pass
            else:
                # Local file

                # if kdu_model.endswith(".tar.gz") or kdu_model.endswith(".tgz")
                # Uncompress temporarily
                # bundle = <uncompressed file>
                pass

            if not bundle:
                # Raise named exception that the bundle could not be found
                raise Exception()

            print("[install] deploying {}".format(bundle))
            await model.deploy(bundle)

            # Get the application
            if atomic:
                # applications = model.applications
                print("[install] Applications: {}".format(model.applications))
                for name in model.applications:
                    print("[install] Waiting for {} to settle".format(name))
                    application = model.applications[name]
                    try:
                        # It's not enough to wait for all units to be active;
                        # the application status needs to be active as well.
                        print("Waiting for all units to be active...")
                        await model.block_until(
                            lambda: all(
                                unit.agent_status == 'idle'
                                and application.status in ['active', 'unknown']
                                and unit.workload_status in [
                                    'active', 'unknown'
                                ] for unit in application.units
                            ),
                            timeout=timeout
                        )
                        print("All units active.")

                    except concurrent.futures._base.TimeoutError:
                        print("[install] Timeout exceeded; resetting cluster")
                        await self.reset(cluster_uuid)
                        return False

            # Wait for the application to be active
            if model.is_connected():
                print("[install] Disconnecting model")
                await model.disconnect()

            return True
        raise Exception("Unable to install")

    async def instances_list(
            self,
            cluster_uuid: str
    ) -> list:
        """
        returns a list of deployed releases in a cluster

        :param cluster_uuid: the cluster
        :return:
        """
        return []

    async def upgrade(
        self,
        cluster_uuid: str,
        kdu_instance: str,
        kdu_model: str = None,
        params: dict = None,
    ) -> str:
        """Upgrade a model

        :param cluster_uuid str: The UUID of the cluster to upgrade
        :param kdu_instance str: The unique name of the KDU instance
        :param kdu_model str: The name or path of the bundle to upgrade to
        :param params dict: Key-value pairs of instantiation parameters

        :return: If successful, reference to the new revision number of the
                 KDU instance.
        """

        # TODO: Loop through the bundle and upgrade each charm individually

        """
        The API doesn't have a concept of bundle upgrades, because there are
        many possible changes: charm revision, disk, number of units, etc.

        As such, we are only supporting a limited subset of upgrades. We'll
        upgrade the charm revision but leave storage and scale untouched.

        Scale changes should happen through OSM constructs, and changes to
        storage would require a redeployment of the service, at least in this
        initial release.
        """
        namespace = self.get_namespace(cluster_uuid)
        model = await self.get_model(namespace, cluster_uuid=cluster_uuid)

        with open(kdu_model, 'r') as f:
            bundle = yaml.safe_load(f)

            """
            {
                'description': 'Test bundle',
                'bundle': 'kubernetes',
                'applications': {
                    'mariadb-k8s': {
                        'charm': 'cs:~charmed-osm/mariadb-k8s-20',
                        'scale': 1,
                        'options': {
                            'password': 'manopw',
                            'root_password': 'osm4u',
                            'user': 'mano'
                        },
                        'series': 'kubernetes'
                    }
                }
            }
            """
            # TODO: This should be returned in an agreed-upon format
            for name in bundle['applications']:
                print(model.applications)
                application = model.applications[name]
                print(application)

                path = bundle['applications'][name]['charm']

                try:
                    await application.upgrade_charm(switch=path)
                except juju.errors.JujuError as ex:
                    if 'already running charm' in str(ex):
                        # We're already running this version
                        pass

        await model.disconnect()

        return True
        raise NotImplemented()

    """Rollback"""
    async def rollback(
        self,
        cluster_uuid: str,
        kdu_instance: str,
        revision: int = 0,
    ) -> str:
        """Rollback a model

        :param cluster_uuid str: The UUID of the cluster to rollback
        :param kdu_instance str: The unique name of the KDU instance
        :param revision int: The revision to revert to. If omitted, rolls back
                             the previous upgrade.

        :return: If successful, returns the revision of active KDU instance,
                 or raises an exception
        """
        raise NotImplemented()

    """Deletion"""
    async def uninstall(
        self,
        cluster_uuid: str,
        kdu_instance: str,
    ) -> bool:
        """Uninstall a KDU instance

        :param cluster_uuid str: The UUID of the cluster to uninstall
        :param kdu_instance str: The unique name of the KDU instance

        :return: Returns True if successful, or raises an exception
        """
        removed = False

        # Remove an application from the model
        model = await self.get_model(self.get_namespace(cluster_uuid), cluster_uuid=cluster_uuid)

        if model:
            # Get the application
            if kdu_instance not in model.applications:
                # TODO: Raise a named exception
                raise Exception("Application not found.")

            application = model.applications[kdu_instance]

            # Destroy the application
            await application.destroy()

            # TODO: Verify removal

            removed = True
        return removed

    """Introspection"""
    async def inspect_kdu(
        self,
        kdu_model: str,
    ) -> dict:
        """Inspect a KDU

        Inspects a bundle and returns a dictionary of config parameters and
        their default values.

        :param kdu_model str: The name or path of the bundle to inspect.

        :return: If successful, returns a dictionary of available parameters
                 and their default values.
        """

        kdu = {}
        with open(kdu_model, 'r') as f:
            bundle = yaml.safe_load(f)

            """
            {
                'description': 'Test bundle',
                'bundle': 'kubernetes',
                'applications': {
                    'mariadb-k8s': {
                        'charm': 'cs:~charmed-osm/mariadb-k8s-20',
                        'scale': 1,
                        'options': {
                            'password': 'manopw',
                            'root_password': 'osm4u',
                            'user': 'mano'
                        },
                        'series': 'kubernetes'
                    }
                }
            }
            """
            # TODO: This should be returned in an agreed-upon format
            kdu = bundle['applications']

        return kdu

    async def help_kdu(
        self,
        kdu_model: str,
    ) -> str:
        """View the README

        If available, returns the README of the bundle.

        :param kdu_model str: The name or path of a bundle

        :return: If found, returns the contents of the README.
        """
        readme = None

        files = ['README', 'README.txt', 'README.md']
        path = os.path.dirname(kdu_model)
        for file in os.listdir(path):
            if file in files:
                with open(file, 'r') as f:
                    readme = f.read()
                    break

        return readme

    async def status_kdu(
        self,
        cluster_uuid: str,
        kdu_instance: str,
    ) -> dict:
        """Get the status of the KDU

        Get the current status of the KDU instance.

        :param cluster_uuid str: The UUID of the cluster
        :param kdu_instance str: The unique id of the KDU instance

        :return: Returns a dictionary containing namespace, state, resources,
                 and deployment_time.
        """
        status = {}

        model = await self.get_model(self.get_namespace(cluster_uuid), cluster_uuid=cluster_uuid)

        # model = await self.get_model_by_uuid(cluster_uuid)
        if model:
            model_status = await model.get_status()
            status = model_status.applications

            for name in model_status.applications:
                application = model_status.applications[name]
                status[name] = {
                    'status': application['status']['status']
                }

            if model.is_connected():
                await model.disconnect()

        return status

    # Private methods
    async def add_k8s(
        self,
        cloud_name: str,
        credentials: str,
    ) -> bool:
        """Add a k8s cloud to Juju

        Adds a Kubernetes cloud to Juju, so it can be bootstrapped with a
        Juju Controller.

        :param cloud_name str: The name of the cloud to add.
        :param credentials dict: A dictionary representing the output of
            `kubectl config view --raw`.

        :returns: True if successful, otherwise raises an exception.
        """

        cmd = [self.juju_command, "add-k8s", "--local", cloud_name]
        print(cmd)
        p = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            # input=yaml.dump(credentials, Dumper=yaml.Dumper).encode("utf-8"),
            input=credentials.encode("utf-8"),
            # encoding='ascii'
        )
        retcode = p.returncode
        print("add-k8s return code: {}".format(retcode))

        if retcode > 0:
            raise Exception(p.stderr)
        return True

    async def add_model(
        self,
        model_name: str,
        cluster_uuid: str,
    ) -> juju.model.Model:
        """Adds a model to the controller

        Adds a new model to the Juju controller

        :param model_name str: The name of the model to add.
        :returns: The juju.model.Model object of the new model upon success or
                  raises an exception.
        """
        if not self.authenticated:
            await self.login(cluster_uuid)

        self.log.debug("Adding model '{}' to cluster_uuid '{}'".format(model_name, cluster_uuid))
        model = await self.controller.add_model(
            model_name,
            config={'authorized-keys': self.juju_public_key}
        )
        return model

    async def bootstrap(
        self,
        cloud_name: str,
        cluster_uuid: str,
        microk8s: bool
    ) -> bool:
        """Bootstrap a Kubernetes controller

        Bootstrap a Juju controller inside the Kubernetes cluster

        :param cloud_name str: The name of the cloud.
        :param cluster_uuid str: The UUID of the cluster to bootstrap.
        :param microk8s bool: If this is a microk8s cluster.
        :returns: True upon success or raises an exception.
        """

        if microk8s:
            cmd = [self.juju_command, "bootstrap", cloud_name, cluster_uuid]
        else:
            """
            For non-microk8s clusters, specify that the controller service is using a LoadBalancer.
            """
            cmd = [self.juju_command, "bootstrap", cloud_name, cluster_uuid, "--config", "controller-service-type=loadbalancer"]

        print("Bootstrapping controller {} in cloud {}".format(
            cluster_uuid, cloud_name
        ))

        p = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            # encoding='ascii'
        )
        retcode = p.returncode

        if retcode > 0:
            #
            if b'already exists' not in p.stderr:
                raise Exception(p.stderr)

        return True

    async def destroy_controller(
        self,
        cluster_uuid: str
    ) -> bool:
        """Destroy a Kubernetes controller

        Destroy an existing Kubernetes controller.

        :param cluster_uuid str: The UUID of the cluster to bootstrap.
        :returns: True upon success or raises an exception.
        """
        cmd = [
            self.juju_command,
            "destroy-controller",
            "--destroy-all-models",
            "--destroy-storage",
            "-y",
            cluster_uuid
        ]

        p = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            # encoding='ascii'
        )
        retcode = p.returncode

        if retcode > 0:
            #
            if 'already exists' not in p.stderr:
                raise Exception(p.stderr)

    def get_config(
        self,
        cluster_uuid: str,
    ) -> dict:
        """Get the cluster configuration

        Gets the configuration of the cluster

        :param cluster_uuid str: The UUID of the cluster.
        :return: A dict upon success, or raises an exception.
        """
        cluster_config = "{}/{}.yaml".format(self.fs.path, cluster_uuid)
        if os.path.exists(cluster_config):
            with open(cluster_config, 'r') as f:
                config = yaml.safe_load(f.read())
                return config
        else:
            raise Exception(
                "Unable to locate configuration for cluster {}".format(
                    cluster_uuid
                )
            )

    async def get_model(
        self,
        model_name: str,
        cluster_uuid: str,
    ) -> juju.model.Model:
        """Get a model from the Juju Controller.

        Note: Model objects returned must call disconnected() before it goes
        out of scope.

        :param model_name str: The name of the model to get
        :return The juju.model.Model object if found, or None.
        """
        if not self.authenticated:
            await self.login(cluster_uuid)

        model = None
        models = await self.controller.list_models()
        self.log.debug(models)
        if model_name in models:
            self.log.debug("Found model: {}".format(model_name))
            model = await self.controller.get_model(
                model_name
            )
        return model

    def get_namespace(
        self,
        cluster_uuid: str,
    ) -> str:
        """Get the namespace UUID
        Gets the namespace's unique name

        :param cluster_uuid str: The UUID of the cluster
        :returns: The namespace UUID, or raises an exception
        """
        config = self.get_config(cluster_uuid)

        # Make sure the name is in the config
        if 'namespace' not in config:
            raise Exception("Namespace not found.")

        # TODO: We want to make sure this is unique to the cluster, in case
        # the cluster is being reused.
        # Consider pre/appending the cluster id to the namespace string
        return config['namespace']

    async def has_model(
        self,
        model_name: str
    ) -> bool:
        """Check if a model exists in the controller

        Checks to see if a model exists in the connected Juju controller.

        :param model_name str: The name of the model
        :return: A boolean indicating if the model exists
        """
        models = await self.controller.list_models()

        if model_name in models:
            return True
        return False

    def is_microk8s_by_cluster_uuid(
        self,
        cluster_uuid: str,
    ) -> bool:
        """Check if a cluster is micro8s

        Checks if a cluster is running microk8s

        :param cluster_uuid str: The UUID of the cluster
        :returns: A boolean if the cluster is running microk8s
        """
        config = self.get_config(cluster_uuid)
        return config['microk8s']

    def is_microk8s_by_credentials(
        self,
        credentials: str,
    ) -> bool:
        """Check if a cluster is micro8s

        Checks if a cluster is running microk8s

        :param credentials dict: A dictionary containing the k8s credentials
        :returns: A boolean if the cluster is running microk8s
        """
        creds = yaml.safe_load(credentials)
        if creds:
            for context in creds['contexts']:
                if 'microk8s' in context['name']:
                    return True

        return False

    async def login(self, cluster_uuid):
        """Login to the Juju controller."""

        if self.authenticated:
            return

        self.connecting = True

        # Test: Make sure we have the credentials loaded
        config = self.get_config(cluster_uuid)

        self.juju_endpoint = config['endpoint']
        self.juju_user = config['username']
        self.juju_secret = config['secret']
        self.juju_ca_cert = config['cacert']
        self.juju_public_key = None

        self.controller = Controller()

        if self.juju_secret:
            self.log.debug(
                "Connecting to controller... ws://{} as {}/{}".format(
                    self.juju_endpoint,
                    self.juju_user,
                    self.juju_secret,
                )
            )
            try:
                await self.controller.connect(
                    endpoint=self.juju_endpoint,
                    username=self.juju_user,
                    password=self.juju_secret,
                    cacert=self.juju_ca_cert,
                )
                self.authenticated = True
                self.log.debug("JujuApi: Logged into controller")
            except Exception as ex:
                print(ex)
                self.log.debug("Caught exception: {}".format(ex))
                pass
        else:
            self.log.fatal("VCA credentials not configured.")
            self.authenticated = False

    async def logout(self):
        """Logout of the Juju controller."""
        print("[logout]")
        if not self.authenticated:
            return False

        for model in self.models:
            print("Logging out of model {}".format(model))
            await self.models[model].disconnect()

        if self.controller:
            self.log.debug("Disconnecting controller {}".format(
                self.controller
            ))
            await self.controller.disconnect()
            self.controller = None

        self.authenticated = False

    async def remove_cloud(
        self,
        cloud_name: str,
    ) -> bool:
        """Remove a k8s cloud from Juju

        Removes a Kubernetes cloud from Juju.

        :param cloud_name str: The name of the cloud to add.

        :returns: True if successful, otherwise raises an exception.
        """

        # Remove the bootstrapped controller
        cmd = [self.juju_command, "remove-k8s", "--client", cloud_name]
        p = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            # encoding='ascii'
        )
        retcode = p.returncode

        if retcode > 0:
            raise Exception(p.stderr)

        # Remove the cloud from the local config
        cmd = [self.juju_command, "remove-cloud", "--client", cloud_name]
        p = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            # encoding='ascii'
        )
        retcode = p.returncode

        if retcode > 0:
            raise Exception(p.stderr)


        return True

    async def set_config(
        self,
        cluster_uuid: str,
        config: dict,
    ) -> bool:
        """Save the cluster configuration

        Saves the cluster information to the file store

        :param cluster_uuid str: The UUID of the cluster
        :param config dict: A dictionary containing the cluster configuration
        :returns: Boolean upon success or raises an exception.
        """

        cluster_config = "{}/{}.yaml".format(self.fs.path, cluster_uuid)
        if not os.path.exists(cluster_config):
            print("Writing config to {}".format(cluster_config))
            with open(cluster_config, 'w') as f:
                f.write(yaml.dump(config, Dumper=yaml.Dumper))

        return True
