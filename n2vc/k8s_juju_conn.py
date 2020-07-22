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
import concurrent
import os
import uuid
import yaml

import juju
from juju.controller import Controller
from juju.model import Model
from n2vc.exceptions import K8sException
from n2vc.k8s_conn import K8sConnector
from n2vc.kubectl import Kubectl
from .exceptions import MethodNotImplemented


# from juju.bundle import BundleHandler
# import re
# import ssl
# from .vnf import N2VC
class K8sJujuConnector(K8sConnector):
    def __init__(
        self,
        fs: object,
        db: object,
        kubectl_command: str = "/usr/bin/kubectl",
        juju_command: str = "/usr/bin/juju",
        log: object = None,
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
            self, db, log=log, on_update_db=on_update_db,
        )

        self.fs = fs
        self.log.debug("Initializing K8S Juju connector")

        self.authenticated = False
        self.models = {}

        self.juju_command = juju_command
        self.juju_secret = ""

        self.log.debug("K8S Juju connector initialized")

    """Initialization"""

    async def init_env(
        self,
        k8s_creds: str,
        namespace: str = "kube-system",
        reuse_cluster_uuid: str = None,
    ) -> (str, bool):
        """
        It prepares a given K8s cluster environment to run Juju bundles.

        :param k8s_creds: credentials to access a given K8s cluster, i.e. a valid
            '.kube/config'
        :param namespace: optional namespace to be used for juju. By default,
            'kube-system' will be used
        :param reuse_cluster_uuid: existing cluster uuid for reuse
        :return: uuid of the K8s cluster and True if connector has installed some
            software in the cluster
            (on error, an exception will be raised)
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

        ###################################################
        # TODO: Make it idempotent, calling add-k8s and   #
        # bootstrap whenever reuse_cluster_uuid is passed #
        # as parameter                                    #
        # `init_env` is called to initialize the K8s      #
        # cluster for juju. If this initialization fails, #
        # it can be called again by LCM with the param    #
        # reuse_cluster_uuid, e.g. to try to fix it.       #
        ###################################################

        # This is a new cluster, so bootstrap it

        cluster_uuid = reuse_cluster_uuid or str(uuid.uuid4())

        # Is a local k8s cluster?
        localk8s = self.is_local_k8s(k8s_creds)

        # If the k8s is external, the juju controller needs a loadbalancer
        loadbalancer = False if localk8s else True

        # Name the new k8s cloud
        k8s_cloud = "k8s-{}".format(cluster_uuid)

        self.log.debug("Adding k8s cloud {}".format(k8s_cloud))
        await self.add_k8s(k8s_cloud, k8s_creds)

        # Bootstrap Juju controller
        self.log.debug("Bootstrapping...")
        await self.bootstrap(k8s_cloud, cluster_uuid, loadbalancer)
        self.log.debug("Bootstrap done.")

        # Get the controller information

        # Parse ~/.local/share/juju/controllers.yaml
        # controllers.testing.api-endpoints|ca-cert|uuid
        self.log.debug("Getting controller endpoints")
        with open(os.path.expanduser("~/.local/share/juju/controllers.yaml")) as f:
            controllers = yaml.load(f, Loader=yaml.Loader)
            controller = controllers["controllers"][cluster_uuid]
            endpoints = controller["api-endpoints"]
            self.juju_endpoint = endpoints[0]
            self.juju_ca_cert = controller["ca-cert"]

        # Parse ~/.local/share/juju/accounts
        # controllers.testing.user|password
        self.log.debug("Getting accounts")
        with open(os.path.expanduser("~/.local/share/juju/accounts.yaml")) as f:
            controllers = yaml.load(f, Loader=yaml.Loader)
            controller = controllers["controllers"][cluster_uuid]

            self.juju_user = controller["user"]
            self.juju_secret = controller["password"]

        # raise Exception("EOL")

        self.juju_public_key = None

        config = {
            "endpoint": self.juju_endpoint,
            "username": self.juju_user,
            "secret": self.juju_secret,
            "cacert": self.juju_ca_cert,
            "namespace": namespace,
            "loadbalancer": loadbalancer,
        }

        # Store the cluster configuration so it
        # can be used for subsequent calls
        self.log.debug("Setting config")
        await self.set_config(cluster_uuid, config)

        # Login to the k8s cluster
        if not self.authenticated:
            await self.login(cluster_uuid)

        # We're creating a new cluster
        # print("Getting model {}".format(self.get_namespace(cluster_uuid),
        #    cluster_uuid=cluster_uuid))
        # model = await self.get_model(
        #    self.get_namespace(cluster_uuid),
        #    cluster_uuid=cluster_uuid
        # )

        # Disconnect from the model
        # if model and model.is_connected():
        #    await model.disconnect()

        return cluster_uuid, True

    """Repo Management"""

    async def repo_add(
        self, name: str, url: str, _type: str = "charm",
    ):
        raise MethodNotImplemented()

    async def repo_list(self):
        raise MethodNotImplemented()

    async def repo_remove(
        self, name: str,
    ):
        raise MethodNotImplemented()

    async def synchronize_repos(self, cluster_uuid: str, name: str):
        """
        Returns None as currently add_repo is not implemented
        """
        return None

    """Reset"""

    async def reset(
        self, cluster_uuid: str, force: bool = False, uninstall_sw: bool = False
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
                    self.log.debug("[reset] Destroying model")
                    await self.controller.destroy_model(namespace, destroy_storage=True)

                # Disconnect from the controller
                self.log.debug("[reset] Disconnecting controller")
                await self.logout()

                # Destroy the controller (via CLI)
                self.log.debug("[reset] Destroying controller")
                await self.destroy_controller(cluster_uuid)

                self.log.debug("[reset] Removing k8s cloud")
                k8s_cloud = "k8s-{}".format(cluster_uuid)
                await self.remove_cloud(k8s_cloud)

        except Exception as ex:
            self.log.debug("Caught exception during reset: {}".format(ex))

        return True

    """Deployment"""

    async def install(
        self,
        cluster_uuid: str,
        kdu_model: str,
        atomic: bool = True,
        timeout: float = 300,
        params: dict = None,
        db_dict: dict = None,
        kdu_name: str = None,
        namespace: str = None,
    ) -> bool:
        """Install a bundle

        :param cluster_uuid str: The UUID of the cluster to install to
        :param kdu_model str: The name or path of a bundle to install
        :param atomic bool: If set, waits until the model is active and resets
                            the cluster on failure.
        :param timeout int: The time, in seconds, to wait for the install
                            to finish
        :param params dict: Key-value pairs of instantiation parameters
        :param kdu_name: Name of the KDU instance to be installed
        :param namespace: K8s namespace to use for the KDU instance

        :return: If successful, returns ?
        """

        if not self.authenticated:
            self.log.debug("[install] Logging in to the controller")
            await self.login(cluster_uuid)

        ##
        # Get or create the model, based on the NS
        # uuid.
        if kdu_name:
            kdu_instance = "{}-{}".format(kdu_name, db_dict["filter"]["_id"])
        else:
            kdu_instance = db_dict["filter"]["_id"]

        self.log.debug("Checking for model named {}".format(kdu_instance))

        # Create the new model
        self.log.debug("Adding model: {}".format(kdu_instance))
        model = await self.add_model(kdu_instance, cluster_uuid=cluster_uuid)

        if model:
            # TODO: Instantiation parameters

            """
            "Juju bundle that models the KDU, in any of the following ways:
                - <juju-repo>/<juju-bundle>
                - <juju-bundle folder under k8s_models folder in the package>
                - <juju-bundle tgz file (w/ or w/o extension) under k8s_models folder
                    in the package>
                - <URL_where_to_fetch_juju_bundle>
            """
            try:
                previous_workdir = os.getcwd()
            except FileNotFoundError:
                previous_workdir = "/app/storage"

            bundle = kdu_model
            if kdu_model.startswith("cs:"):
                bundle = kdu_model
            elif kdu_model.startswith("http"):
                # Download the file
                pass
            else:
                new_workdir = kdu_model.strip(kdu_model.split("/")[-1])

                os.chdir(new_workdir)

                bundle = "local:{}".format(kdu_model)

            if not bundle:
                # Raise named exception that the bundle could not be found
                raise Exception()

            self.log.debug("[install] deploying {}".format(bundle))
            await model.deploy(bundle)

            # Get the application
            if atomic:
                # applications = model.applications
                self.log.debug("[install] Applications: {}".format(model.applications))
                for name in model.applications:
                    self.log.debug("[install] Waiting for {} to settle".format(name))
                    application = model.applications[name]
                    try:
                        # It's not enough to wait for all units to be active;
                        # the application status needs to be active as well.
                        self.log.debug("Waiting for all units to be active...")
                        await model.block_until(
                            lambda: all(
                                unit.agent_status == "idle"
                                and application.status in ["active", "unknown"]
                                and unit.workload_status in ["active", "unknown"]
                                for unit in application.units
                            ),
                            timeout=timeout,
                        )
                        self.log.debug("All units active.")

                    # TODO use asyncio.TimeoutError
                    except concurrent.futures._base.TimeoutError:
                        os.chdir(previous_workdir)
                        self.log.debug("[install] Timeout exceeded; resetting cluster")
                        await self.reset(cluster_uuid)
                        return False

            # Wait for the application to be active
            if model.is_connected():
                self.log.debug("[install] Disconnecting model")
                await model.disconnect()

            os.chdir(previous_workdir)

            return kdu_instance
        raise Exception("Unable to install")

    async def instances_list(self, cluster_uuid: str) -> list:
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

        with open(kdu_model, "r") as f:
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
            for name in bundle["applications"]:
                self.log.debug(model.applications)
                application = model.applications[name]
                self.log.debug(application)

                path = bundle["applications"][name]["charm"]

                try:
                    await application.upgrade_charm(switch=path)
                except juju.errors.JujuError as ex:
                    if "already running charm" in str(ex):
                        # We're already running this version
                        pass

        await model.disconnect()

        return True
        raise MethodNotImplemented()

    """Rollback"""

    async def rollback(
        self, cluster_uuid: str, kdu_instance: str, revision: int = 0,
    ) -> str:
        """Rollback a model

        :param cluster_uuid str: The UUID of the cluster to rollback
        :param kdu_instance str: The unique name of the KDU instance
        :param revision int: The revision to revert to. If omitted, rolls back
                             the previous upgrade.

        :return: If successful, returns the revision of active KDU instance,
                 or raises an exception
        """
        raise MethodNotImplemented()

    """Deletion"""

    async def uninstall(self, cluster_uuid: str, kdu_instance: str) -> bool:
        """Uninstall a KDU instance

        :param cluster_uuid str: The UUID of the cluster
        :param kdu_instance str: The unique name of the KDU instance

        :return: Returns True if successful, or raises an exception
        """
        if not self.authenticated:
            self.log.debug("[uninstall] Connecting to controller")
            await self.login(cluster_uuid)

        self.log.debug("[uninstall] Destroying model")

        await self.controller.destroy_models(kdu_instance)

        self.log.debug("[uninstall] Model destroyed and disconnecting")
        await self.logout()

        return True

    async def exec_primitive(
        self,
        cluster_uuid: str = None,
        kdu_instance: str = None,
        primitive_name: str = None,
        timeout: float = 300,
        params: dict = None,
        db_dict: dict = None,
    ) -> str:
        """Exec primitive (Juju action)

        :param cluster_uuid str: The UUID of the cluster
        :param kdu_instance str: The unique name of the KDU instance
        :param primitive_name: Name of action that will be executed
        :param timeout: Timeout for action execution
        :param params: Dictionary of all the parameters needed for the action
        :db_dict: Dictionary for any additional data

        :return: Returns the output of the action
        """
        if not self.authenticated:
            self.log.debug("[exec_primitive] Connecting to controller")
            await self.login(cluster_uuid)

        if not params or "application-name" not in params:
            raise K8sException(
                "Missing application-name argument, \
                                argument needed for K8s actions"
            )
        try:
            self.log.debug(
                "[exec_primitive] Getting model "
                "kdu_instance: {}".format(kdu_instance)
            )

            model = await self.get_model(kdu_instance, cluster_uuid)

            application_name = params["application-name"]
            application = model.applications[application_name]

            actions = await application.get_actions()
            if primitive_name not in actions:
                raise K8sException("Primitive {} not found".format(primitive_name))

            unit = None
            for u in application.units:
                if await u.is_leader_from_status():
                    unit = u
                    break

            if unit is None:
                raise K8sException("No leader unit found to execute action")

            self.log.debug("[exec_primitive] Running action: {}".format(primitive_name))
            action = await unit.run_action(primitive_name, **params)

            output = await model.get_action_output(action_uuid=action.entity_id)
            status = await model.get_action_status(uuid_or_prefix=action.entity_id)

            status = (
                status[action.entity_id] if action.entity_id in status else "failed"
            )

            if status != "completed":
                raise K8sException(
                    "status is not completed: {} output: {}".format(status, output)
                )

            return output

        except Exception as e:
            error_msg = "Error executing primitive {}: {}".format(primitive_name, e)
            self.log.error(error_msg)
            raise K8sException(message=error_msg)

    """Introspection"""

    async def inspect_kdu(self, kdu_model: str,) -> dict:
        """Inspect a KDU

        Inspects a bundle and returns a dictionary of config parameters and
        their default values.

        :param kdu_model str: The name or path of the bundle to inspect.

        :return: If successful, returns a dictionary of available parameters
                 and their default values.
        """

        kdu = {}
        with open(kdu_model, "r") as f:
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
            kdu = bundle["applications"]

        return kdu

    async def help_kdu(self, kdu_model: str,) -> str:
        """View the README

        If available, returns the README of the bundle.

        :param kdu_model str: The name or path of a bundle

        :return: If found, returns the contents of the README.
        """
        readme = None

        files = ["README", "README.txt", "README.md"]
        path = os.path.dirname(kdu_model)
        for file in os.listdir(path):
            if file in files:
                with open(file, "r") as f:
                    readme = f.read()
                    break

        return readme

    async def status_kdu(self, cluster_uuid: str, kdu_instance: str,) -> dict:
        """Get the status of the KDU

        Get the current status of the KDU instance.

        :param cluster_uuid str: The UUID of the cluster
        :param kdu_instance str: The unique id of the KDU instance

        :return: Returns a dictionary containing namespace, state, resources,
                 and deployment_time.
        """
        status = {}

        model = await self.get_model(
            self.get_namespace(cluster_uuid), cluster_uuid=cluster_uuid
        )

        # model = await self.get_model_by_uuid(cluster_uuid)
        if model:
            model_status = await model.get_status()
            status = model_status.applications

            for name in model_status.applications:
                application = model_status.applications[name]
                status[name] = {"status": application["status"]["status"]}

            if model.is_connected():
                await model.disconnect()

        return status

    async def get_services(
        self, cluster_uuid: str, kdu_instance: str, namespace: str
    ) -> list:
        """Return a list of services of a kdu_instance"""

        credentials = self.get_credentials(cluster_uuid=cluster_uuid)

        config_path = "/tmp/{}".format(cluster_uuid)
        config_file = "{}/config".format(config_path)

        if not os.path.exists(config_path):
            os.makedirs(config_path)
        with open(config_file, "w") as f:
            f.write(credentials)

        kubectl = Kubectl(config_file=config_file)
        return kubectl.get_services(
            field_selector="metadata.namespace={}".format(kdu_instance)
        )

    async def get_service(
        self, cluster_uuid: str, service_name: str, namespace: str
    ) -> object:
        """Return data for a specific service inside a namespace"""

        credentials = self.get_credentials(cluster_uuid=cluster_uuid)

        config_path = "/tmp/{}".format(cluster_uuid)
        config_file = "{}/config".format(config_path)

        if not os.path.exists(config_path):
            os.makedirs(config_path)
        with open(config_file, "w") as f:
            f.write(credentials)

        kubectl = Kubectl(config_file=config_file)

        return kubectl.get_services(
            field_selector="metadata.name={},metadata.namespace={}".format(
                service_name, namespace
            )
        )[0]

    # Private methods
    async def add_k8s(self, cloud_name: str, credentials: str,) -> bool:
        """Add a k8s cloud to Juju

        Adds a Kubernetes cloud to Juju, so it can be bootstrapped with a
        Juju Controller.

        :param cloud_name str: The name of the cloud to add.
        :param credentials dict: A dictionary representing the output of
            `kubectl config view --raw`.

        :returns: True if successful, otherwise raises an exception.
        """

        cmd = [self.juju_command, "add-k8s", "--local", cloud_name]
        self.log.debug(cmd)

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.PIPE,
        )

        # Feed the process the credentials
        process.stdin.write(credentials.encode("utf-8"))
        await process.stdin.drain()
        process.stdin.close()

        _stdout, stderr = await process.communicate()

        return_code = process.returncode

        self.log.debug("add-k8s return code: {}".format(return_code))

        if return_code > 0:
            raise Exception(stderr)

        return True

    async def add_model(self, model_name: str, cluster_uuid: str,) -> Model:
        """Adds a model to the controller

        Adds a new model to the Juju controller

        :param model_name str: The name of the model to add.
        :returns: The juju.model.Model object of the new model upon success or
                  raises an exception.
        """
        if not self.authenticated:
            await self.login(cluster_uuid)

        self.log.debug(
            "Adding model '{}' to cluster_uuid '{}'".format(model_name, cluster_uuid)
        )
        model = None
        try:
            if self.juju_public_key is not None:
                model = await self.controller.add_model(
                    model_name, config={"authorized-keys": self.juju_public_key}
                )
            else:
                model = await self.controller.add_model(model_name)
        except Exception as ex:
            self.log.debug(ex)
            self.log.debug("Caught exception: {}".format(ex))
            pass

        return model

    async def bootstrap(
        self, cloud_name: str, cluster_uuid: str, loadbalancer: bool
    ) -> bool:
        """Bootstrap a Kubernetes controller

        Bootstrap a Juju controller inside the Kubernetes cluster

        :param cloud_name str: The name of the cloud.
        :param cluster_uuid str: The UUID of the cluster to bootstrap.
        :param loadbalancer bool: If the controller should use loadbalancer or not.
        :returns: True upon success or raises an exception.
        """

        if not loadbalancer:
            cmd = [self.juju_command, "bootstrap", cloud_name, cluster_uuid]
        else:
            """
            For public clusters, specify that the controller service is using a
            LoadBalancer.
            """
            cmd = [
                self.juju_command,
                "bootstrap",
                cloud_name,
                cluster_uuid,
                "--config",
                "controller-service-type=loadbalancer",
            ]

        self.log.debug(
            "Bootstrapping controller {} in cloud {}".format(cluster_uuid, cloud_name)
        )

        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )

        _stdout, stderr = await process.communicate()

        return_code = process.returncode

        if return_code > 0:
            #
            if b"already exists" not in stderr:
                raise Exception(stderr)

        return True

    async def destroy_controller(self, cluster_uuid: str) -> bool:
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
            cluster_uuid,
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )

        _stdout, stderr = await process.communicate()

        return_code = process.returncode

        if return_code > 0:
            #
            if "already exists" not in stderr:
                raise Exception(stderr)

    def get_credentials(self, cluster_uuid: str) -> str:
        """
        Get Cluster Kubeconfig
        """
        k8scluster = self.db.get_one(
            "k8sclusters", q_filter={"_id": cluster_uuid}, fail_on_empty=False
        )

        self.db.encrypt_decrypt_fields(
            k8scluster.get("credentials"),
            "decrypt",
            ["password", "secret"],
            schema_version=k8scluster["schema_version"],
            salt=k8scluster["_id"],
        )

        return yaml.safe_dump(k8scluster.get("credentials"))

    def get_config(self, cluster_uuid: str,) -> dict:
        """Get the cluster configuration

        Gets the configuration of the cluster

        :param cluster_uuid str: The UUID of the cluster.
        :return: A dict upon success, or raises an exception.
        """
        cluster_config = "{}/{}.yaml".format(self.fs.path, cluster_uuid)
        if os.path.exists(cluster_config):
            with open(cluster_config, "r") as f:
                config = yaml.safe_load(f.read())
                return config
        else:
            raise Exception(
                "Unable to locate configuration for cluster {}".format(cluster_uuid)
            )

    async def get_model(self, model_name: str, cluster_uuid: str,) -> Model:
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
        if model_name in models:
            self.log.debug("Found model: {}".format(model_name))
            model = await self.controller.get_model(model_name)
        return model

    def get_namespace(self, cluster_uuid: str,) -> str:
        """Get the namespace UUID
        Gets the namespace's unique name

        :param cluster_uuid str: The UUID of the cluster
        :returns: The namespace UUID, or raises an exception
        """
        config = self.get_config(cluster_uuid)

        # Make sure the name is in the config
        if "namespace" not in config:
            raise Exception("Namespace not found.")

        # TODO: We want to make sure this is unique to the cluster, in case
        # the cluster is being reused.
        # Consider pre/appending the cluster id to the namespace string
        return config["namespace"]

    async def has_model(self, model_name: str) -> bool:
        """Check if a model exists in the controller

        Checks to see if a model exists in the connected Juju controller.

        :param model_name str: The name of the model
        :return: A boolean indicating if the model exists
        """
        models = await self.controller.list_models()

        if model_name in models:
            return True
        return False

    def is_local_k8s(self, credentials: str,) -> bool:
        """Check if a cluster is local

        Checks if a cluster is running in the local host

        :param credentials dict: A dictionary containing the k8s credentials
        :returns: A boolean if the cluster is running locally
        """

        creds = yaml.safe_load(credentials)

        if creds and os.getenv("OSMLCM_VCA_APIPROXY"):
            for cluster in creds["clusters"]:
                if "server" in cluster["cluster"]:
                    if os.getenv("OSMLCM_VCA_APIPROXY") in cluster["cluster"]["server"]:
                        return True

        return False

    async def login(self, cluster_uuid):
        """Login to the Juju controller."""

        if self.authenticated:
            return

        self.connecting = True

        # Test: Make sure we have the credentials loaded
        config = self.get_config(cluster_uuid)

        self.juju_endpoint = config["endpoint"]
        self.juju_user = config["username"]
        self.juju_secret = config["secret"]
        self.juju_ca_cert = config["cacert"]
        self.juju_public_key = None

        self.controller = Controller()

        if self.juju_secret:
            self.log.debug(
                "Connecting to controller... ws://{} as {}/{}".format(
                    self.juju_endpoint, self.juju_user, self.juju_secret,
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
                self.log.debug(ex)
                self.log.debug("Caught exception: {}".format(ex))
                pass
        else:
            self.log.fatal("VCA credentials not configured.")
            self.authenticated = False

    async def logout(self):
        """Logout of the Juju controller."""
        self.log.debug("[logout]")
        if not self.authenticated:
            return False

        for model in self.models:
            self.log.debug("Logging out of model {}".format(model))
            await self.models[model].disconnect()

        if self.controller:
            self.log.debug("Disconnecting controller {}".format(self.controller))
            await self.controller.disconnect()
            self.controller = None

        self.authenticated = False

    async def remove_cloud(self, cloud_name: str,) -> bool:
        """Remove a k8s cloud from Juju

        Removes a Kubernetes cloud from Juju.

        :param cloud_name str: The name of the cloud to add.

        :returns: True if successful, otherwise raises an exception.
        """

        # Remove the bootstrapped controller
        cmd = [self.juju_command, "remove-k8s", "--client", cloud_name]
        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )

        _stdout, stderr = await process.communicate()

        return_code = process.returncode

        if return_code > 0:
            raise Exception(stderr)

        # Remove the cloud from the local config
        cmd = [self.juju_command, "remove-cloud", "--client", cloud_name]
        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )

        _stdout, stderr = await process.communicate()

        return_code = process.returncode

        if return_code > 0:
            raise Exception(stderr)

        return True

    async def set_config(self, cluster_uuid: str, config: dict,) -> bool:
        """Save the cluster configuration

        Saves the cluster information to the file store

        :param cluster_uuid str: The UUID of the cluster
        :param config dict: A dictionary containing the cluster configuration
        :returns: Boolean upon success or raises an exception.
        """

        cluster_config = "{}/{}.yaml".format(self.fs.path, cluster_uuid)
        if not os.path.exists(cluster_config):
            self.log.debug("Writing config to {}".format(cluster_config))
            with open(cluster_config, "w") as f:
                f.write(yaml.dump(config, Dumper=yaml.Dumper))

        return True
