##
# Copyright 2019 Telefonica Investigacion y Desarrollo, S.A.U.
# This file is part of OSM
# All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# For those usages not covered by the Apache License, Version 2.0 please
# contact with: nfvlabs@tid.es
##

import asyncio
import os
import random
import shutil
import subprocess
import time
from uuid import uuid4

from n2vc.exceptions import K8sException
from n2vc.k8s_conn import K8sConnector
import yaml


class K8sHelmConnector(K8sConnector):

    """
    ####################################################################################
    ################################### P U B L I C ####################################
    ####################################################################################
    """
    service_account = "osm"

    def __init__(
        self,
        fs: object,
        db: object,
        kubectl_command: str = "/usr/bin/kubectl",
        helm_command: str = "/usr/bin/helm",
        log: object = None,
        on_update_db=None,
    ):
        """

        :param fs: file system for kubernetes and helm configuration
        :param db: database object to write current operation status
        :param kubectl_command: path to kubectl executable
        :param helm_command: path to helm executable
        :param log: logger
        :param on_update_db: callback called when k8s connector updates database
        """

        # parent class
        K8sConnector.__init__(self, db=db, log=log, on_update_db=on_update_db)

        self.log.info("Initializing K8S Helm connector")

        # random numbers for release name generation
        random.seed(time.time())

        # the file system
        self.fs = fs

        # exception if kubectl is not installed
        self.kubectl_command = kubectl_command
        self._check_file_exists(filename=kubectl_command, exception_if_not_exists=True)

        # exception if helm is not installed
        self._helm_command = helm_command
        self._check_file_exists(filename=helm_command, exception_if_not_exists=True)

        # initialize helm client-only
        self.log.debug("Initializing helm client-only...")
        command = "{} init --client-only".format(self._helm_command)
        try:
            asyncio.ensure_future(
                self._local_async_exec(command=command, raise_exception_on_error=False)
            )
            # loop = asyncio.get_event_loop()
            # loop.run_until_complete(self._local_async_exec(command=command,
            # raise_exception_on_error=False))
        except Exception as e:
            self.warning(
                msg="helm init failed (it was already initialized): {}".format(e)
            )

        self.log.info("K8S Helm connector initialized")

    @staticmethod
    def _get_namespace_cluster_id(cluster_uuid: str) -> (str, str):
        """
        Parses cluster_uuid stored at database that can be either 'namespace:cluster_id' or only
        cluster_id for backward compatibility
        """
        namespace, _, cluster_id = cluster_uuid.rpartition(':')
        return namespace, cluster_id

    async def init_env(
        self, k8s_creds: str, namespace: str = "kube-system", reuse_cluster_uuid=None
    ) -> (str, bool):
        """
        It prepares a given K8s cluster environment to run Charts on both sides:
            client (OSM)
            server (Tiller)

        :param k8s_creds: credentials to access a given K8s cluster, i.e. a valid
            '.kube/config'
        :param namespace: optional namespace to be used for helm. By default,
            'kube-system' will be used
        :param reuse_cluster_uuid: existing cluster uuid for reuse
        :return: uuid of the K8s cluster and True if connector has installed some
            software in the cluster
        (on error, an exception will be raised)
        """

        if reuse_cluster_uuid:
            namespace_, cluster_id = self._get_namespace_cluster_id(reuse_cluster_uuid)
            namespace = namespace_ or namespace
        else:
            cluster_id = str(uuid4())
        cluster_uuid = "{}:{}".format(namespace, cluster_id)

        self.log.debug("Initializing K8S Cluster {}. namespace: {}".format(cluster_id, namespace))

        # create config filename
        _kube_dir, helm_dir, config_filename, _cluster_dir = self._get_paths(
            cluster_name=cluster_id, create_if_not_exist=True
        )
        with open(config_filename, "w") as f:
            f.write(k8s_creds)

        # check if tiller pod is up in cluster
        command = "{} --kubeconfig={} --namespace={} get deployments".format(
            self.kubectl_command, config_filename, namespace
        )
        output, _rc = await self._local_async_exec(
            command=command, raise_exception_on_error=True
        )

        output_table = self._output_to_table(output=output)

        # find 'tiller' pod in all pods
        already_initialized = False
        try:
            for row in output_table:
                if row[0].startswith("tiller-deploy"):
                    already_initialized = True
                    break
        except Exception:
            pass

        # helm init
        n2vc_installed_sw = False
        if not already_initialized:
            self.log.info(
                "Initializing helm in client and server: {}".format(cluster_id)
            )
            command = "{} --kubeconfig={} --namespace kube-system create serviceaccount {}".format(
                self.kubectl_command, config_filename, self.service_account)
            _, _rc = await self._local_async_exec(command=command, raise_exception_on_error=False)

            command = ("{} --kubeconfig={} create clusterrolebinding osm-tiller-cluster-rule "
                       "--clusterrole=cluster-admin --serviceaccount=kube-system:{}"
                       ).format(self.kubectl_command, config_filename, self.service_account)
            _, _rc = await self._local_async_exec(command=command, raise_exception_on_error=False)

            command = ("{} --kubeconfig={} --tiller-namespace={} --home={} --service-account {} "
                       "init").format(self._helm_command, config_filename, namespace, helm_dir,
                                      self.service_account)
            _, _rc = await self._local_async_exec(command=command, raise_exception_on_error=True)
            n2vc_installed_sw = True
        else:
            # check client helm installation
            check_file = helm_dir + "/repository/repositories.yaml"
            if not self._check_file_exists(filename=check_file, exception_if_not_exists=False):
                self.log.info("Initializing helm in client: {}".format(cluster_id))
                command = (
                    "{} --kubeconfig={} --tiller-namespace={} "
                    "--home={} init --client-only"
                ).format(self._helm_command, config_filename, namespace, helm_dir)
                output, _rc = await self._local_async_exec(
                    command=command, raise_exception_on_error=True
                )
            else:
                self.log.info("Helm client already initialized")

        self.log.info("Cluster {} initialized".format(cluster_id))

        return cluster_uuid, n2vc_installed_sw

    async def repo_add(
        self, cluster_uuid: str, name: str, url: str, repo_type: str = "chart"
    ):
        _, cluster_id = self._get_namespace_cluster_id(cluster_uuid)
        self.log.debug("Cluster {}, adding {} repository {}. URL: {}".format(
            cluster_id, repo_type, name, url))

        # config filename
        _kube_dir, helm_dir, config_filename, _cluster_dir = self._get_paths(
            cluster_name=cluster_id, create_if_not_exist=True
        )

        # helm repo update
        command = "{} --kubeconfig={} --home={} repo update".format(
            self._helm_command, config_filename, helm_dir
        )
        self.log.debug("updating repo: {}".format(command))
        await self._local_async_exec(command=command, raise_exception_on_error=False)

        # helm repo add name url
        command = "{} --kubeconfig={} --home={} repo add {} {}".format(
            self._helm_command, config_filename, helm_dir, name, url
        )
        self.log.debug("adding repo: {}".format(command))
        await self._local_async_exec(command=command, raise_exception_on_error=True)

    async def repo_list(self, cluster_uuid: str) -> list:
        """
        Get the list of registered repositories

        :return: list of registered repositories: [ (name, url) .... ]
        """

        _, cluster_id = self._get_namespace_cluster_id(cluster_uuid)
        self.log.debug("list repositories for cluster {}".format(cluster_id))

        # config filename
        _kube_dir, helm_dir, config_filename, _cluster_dir = self._get_paths(
            cluster_name=cluster_id, create_if_not_exist=True
        )

        command = "{} --kubeconfig={} --home={} repo list --output yaml".format(
            self._helm_command, config_filename, helm_dir
        )

        output, _rc = await self._local_async_exec(
            command=command, raise_exception_on_error=True
        )
        if output and len(output) > 0:
            return yaml.load(output, Loader=yaml.SafeLoader)
        else:
            return []

    async def repo_remove(self, cluster_uuid: str, name: str):
        """
        Remove a repository from OSM

        :param cluster_uuid: the cluster or 'namespace:cluster'
        :param name: repo name in OSM
        :return: True if successful
        """

        _, cluster_id = self._get_namespace_cluster_id(cluster_uuid)
        self.log.debug("list repositories for cluster {}".format(cluster_id))

        # config filename
        _kube_dir, helm_dir, config_filename, _cluster_dir = self._get_paths(
            cluster_name=cluster_id, create_if_not_exist=True
        )

        command = "{} --kubeconfig={} --home={} repo remove {}".format(
            self._helm_command, config_filename, helm_dir, name
        )

        await self._local_async_exec(command=command, raise_exception_on_error=True)

    async def reset(
        self, cluster_uuid: str, force: bool = False, uninstall_sw: bool = False
    ) -> bool:

        namespace, cluster_id = self._get_namespace_cluster_id(cluster_uuid)
        self.log.debug("Resetting K8s environment. cluster uuid: {} uninstall={}"
                       .format(cluster_id, uninstall_sw))

        # get kube and helm directories
        _kube_dir, helm_dir, config_filename, _cluster_dir = self._get_paths(
            cluster_name=cluster_id, create_if_not_exist=False
        )

        # uninstall releases if needed.
        if uninstall_sw:
            releases = await self.instances_list(cluster_uuid=cluster_uuid)
            if len(releases) > 0:
                if force:
                    for r in releases:
                        try:
                            kdu_instance = r.get("Name")
                            chart = r.get("Chart")
                            self.log.debug(
                                "Uninstalling {} -> {}".format(chart, kdu_instance)
                            )
                            await self.uninstall(
                                cluster_uuid=cluster_uuid, kdu_instance=kdu_instance
                            )
                        except Exception as e:
                            self.log.error(
                                "Error uninstalling release {}: {}".format(kdu_instance, e)
                            )
                else:
                    msg = (
                        "Cluster uuid: {} has releases and not force. Leaving K8s helm environment"
                    ).format(cluster_id)
                    self.log.warn(msg)
                    uninstall_sw = False  # Allow to remove k8s cluster without removing Tiller

        if uninstall_sw:

            self.log.debug("Uninstalling tiller from cluster {}".format(cluster_id))

            if not namespace:
                # find namespace for tiller pod
                command = "{} --kubeconfig={} get deployments --all-namespaces".format(
                    self.kubectl_command, config_filename
                )
                output, _rc = await self._local_async_exec(
                    command=command, raise_exception_on_error=False
                )
                output_table = K8sHelmConnector._output_to_table(output=output)
                namespace = None
                for r in output_table:
                    try:
                        if "tiller-deploy" in r[1]:
                            namespace = r[0]
                            break
                    except Exception:
                        pass
                else:
                    msg = "Tiller deployment not found in cluster {}".format(cluster_id)
                    self.log.error(msg)

                self.log.debug("namespace for tiller: {}".format(namespace))

            if namespace:
                # uninstall tiller from cluster
                self.log.debug(
                    "Uninstalling tiller from cluster {}".format(cluster_id)
                )
                command = "{} --kubeconfig={} --home={} reset".format(
                    self._helm_command, config_filename, helm_dir
                )
                self.log.debug("resetting: {}".format(command))
                output, _rc = await self._local_async_exec(
                    command=command, raise_exception_on_error=True
                )
                # Delete clusterrolebinding and serviceaccount.
                # Ignore if errors for backward compatibility
                command = ("{} --kubeconfig={} delete clusterrolebinding.rbac.authorization.k8s."
                           "io/osm-tiller-cluster-rule").format(self.kubectl_command,
                                                                config_filename)
                output, _rc = await self._local_async_exec(command=command,
                                                           raise_exception_on_error=False)
                command = "{} --kubeconfig={} --namespace kube-system delete serviceaccount/{}".\
                    format(self.kubectl_command, config_filename, self.service_account)
                output, _rc = await self._local_async_exec(command=command,
                                                           raise_exception_on_error=False)

            else:
                self.log.debug("namespace not found")

        # delete cluster directory
        direct = self.fs.path + "/" + cluster_id
        self.log.debug("Removing directory {}".format(direct))
        shutil.rmtree(direct, ignore_errors=True)

        return True

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
    ):

        _, cluster_id = self._get_namespace_cluster_id(cluster_uuid)
        self.log.debug("installing {} in cluster {}".format(kdu_model, cluster_id))

        # config filename
        _kube_dir, helm_dir, config_filename, _cluster_dir = self._get_paths(
            cluster_name=cluster_id, create_if_not_exist=True
        )

        # params to str
        # params_str = K8sHelmConnector._params_to_set_option(params)
        params_str, file_to_delete = self._params_to_file_option(
            cluster_id=cluster_id, params=params
        )

        timeout_str = ""
        if timeout:
            timeout_str = "--timeout {}".format(timeout)

        # atomic
        atomic_str = ""
        if atomic:
            atomic_str = "--atomic"
        # namespace
        namespace_str = ""
        if namespace:
            namespace_str = "--namespace {}".format(namespace)

        # version
        version_str = ""
        if ":" in kdu_model:
            parts = kdu_model.split(sep=":")
            if len(parts) == 2:
                version_str = "--version {}".format(parts[1])
                kdu_model = parts[0]

        # generate a name for the release. Then, check if already exists
        kdu_instance = None
        while kdu_instance is None:
            kdu_instance = K8sHelmConnector._generate_release_name(kdu_model)
            try:
                result = await self._status_kdu(
                    cluster_id=cluster_id,
                    kdu_instance=kdu_instance,
                    show_error_log=False,
                )
                if result is not None:
                    # instance already exists: generate a new one
                    kdu_instance = None
            except K8sException:
                pass

        # helm repo install
        command = (
            "{helm} install {atomic} --output yaml --kubeconfig={config} --home={dir} "
            "{params} {timeout} --name={name} {ns} {model} {ver}".format(
                helm=self._helm_command,
                atomic=atomic_str,
                config=config_filename,
                dir=helm_dir,
                params=params_str,
                timeout=timeout_str,
                name=kdu_instance,
                ns=namespace_str,
                model=kdu_model,
                ver=version_str,
            )
        )
        self.log.debug("installing: {}".format(command))

        if atomic:
            # exec helm in a task
            exec_task = asyncio.ensure_future(
                coro_or_future=self._local_async_exec(
                    command=command, raise_exception_on_error=False
                )
            )

            # write status in another task
            status_task = asyncio.ensure_future(
                coro_or_future=self._store_status(
                    cluster_id=cluster_id,
                    kdu_instance=kdu_instance,
                    db_dict=db_dict,
                    operation="install",
                    run_once=False,
                )
            )

            # wait for execution task
            await asyncio.wait([exec_task])

            # cancel status task
            status_task.cancel()

            output, rc = exec_task.result()

        else:

            output, rc = await self._local_async_exec(
                command=command, raise_exception_on_error=False
            )

        # remove temporal values yaml file
        if file_to_delete:
            os.remove(file_to_delete)

        # write final status
        await self._store_status(
            cluster_id=cluster_id,
            kdu_instance=kdu_instance,
            db_dict=db_dict,
            operation="install",
            run_once=True,
            check_every=0,
        )

        if rc != 0:
            msg = "Error executing command: {}\nOutput: {}".format(command, output)
            self.log.error(msg)
            raise K8sException(msg)

        self.log.debug("Returning kdu_instance {}".format(kdu_instance))
        return kdu_instance

    async def instances_list(self, cluster_uuid: str) -> list:
        """
        returns a list of deployed releases in a cluster

        :param cluster_uuid: the 'cluster' or 'namespace:cluster'
        :return:
        """

        _, cluster_id = self._get_namespace_cluster_id(cluster_uuid)
        self.log.debug("list releases for cluster {}".format(cluster_id))

        # config filename
        _kube_dir, helm_dir, config_filename, _cluster_dir = self._get_paths(
            cluster_name=cluster_id, create_if_not_exist=True
        )

        command = "{} --kubeconfig={} --home={} list --output yaml".format(
            self._helm_command, config_filename, helm_dir
        )

        output, _rc = await self._local_async_exec(
            command=command, raise_exception_on_error=True
        )

        if output and len(output) > 0:
            return yaml.load(output, Loader=yaml.SafeLoader).get("Releases")
        else:
            return []

    async def upgrade(
        self,
        cluster_uuid: str,
        kdu_instance: str,
        kdu_model: str = None,
        atomic: bool = True,
        timeout: float = 300,
        params: dict = None,
        db_dict: dict = None,
    ):

        _, cluster_id = self._get_namespace_cluster_id(cluster_uuid)
        self.log.debug("upgrading {} in cluster {}".format(kdu_model, cluster_id))

        # config filename
        _kube_dir, helm_dir, config_filename, _cluster_dir = self._get_paths(
            cluster_name=cluster_id, create_if_not_exist=True
        )

        # params to str
        # params_str = K8sHelmConnector._params_to_set_option(params)
        params_str, file_to_delete = self._params_to_file_option(
            cluster_id=cluster_id, params=params
        )

        timeout_str = ""
        if timeout:
            timeout_str = "--timeout {}".format(timeout)

        # atomic
        atomic_str = ""
        if atomic:
            atomic_str = "--atomic"

        # version
        version_str = ""
        if kdu_model and ":" in kdu_model:
            parts = kdu_model.split(sep=":")
            if len(parts) == 2:
                version_str = "--version {}".format(parts[1])
                kdu_model = parts[0]

        # helm repo upgrade
        command = (
            "{} upgrade {} --output yaml --kubeconfig={} " "--home={} {} {} {} {} {}"
        ).format(
            self._helm_command,
            atomic_str,
            config_filename,
            helm_dir,
            params_str,
            timeout_str,
            kdu_instance,
            kdu_model,
            version_str,
        )
        self.log.debug("upgrading: {}".format(command))

        if atomic:

            # exec helm in a task
            exec_task = asyncio.ensure_future(
                coro_or_future=self._local_async_exec(
                    command=command, raise_exception_on_error=False
                )
            )
            # write status in another task
            status_task = asyncio.ensure_future(
                coro_or_future=self._store_status(
                    cluster_id=cluster_id,
                    kdu_instance=kdu_instance,
                    db_dict=db_dict,
                    operation="upgrade",
                    run_once=False,
                )
            )

            # wait for execution task
            await asyncio.wait([exec_task])

            # cancel status task
            status_task.cancel()
            output, rc = exec_task.result()

        else:

            output, rc = await self._local_async_exec(
                command=command, raise_exception_on_error=False
            )

        # remove temporal values yaml file
        if file_to_delete:
            os.remove(file_to_delete)

        # write final status
        await self._store_status(
            cluster_id=cluster_id,
            kdu_instance=kdu_instance,
            db_dict=db_dict,
            operation="upgrade",
            run_once=True,
            check_every=0,
        )

        if rc != 0:
            msg = "Error executing command: {}\nOutput: {}".format(command, output)
            self.log.error(msg)
            raise K8sException(msg)

        # return new revision number
        instance = await self.get_instance_info(
            cluster_uuid=cluster_uuid, kdu_instance=kdu_instance
        )
        if instance:
            revision = int(instance.get("Revision"))
            self.log.debug("New revision: {}".format(revision))
            return revision
        else:
            return 0

    async def rollback(
        self, cluster_uuid: str, kdu_instance: str, revision=0, db_dict: dict = None
    ):

        _, cluster_id = self._get_namespace_cluster_id(cluster_uuid)
        self.log.debug(
            "rollback kdu_instance {} to revision {} from cluster {}".format(
                kdu_instance, revision, cluster_id
            )
        )

        # config filename
        _kube_dir, helm_dir, config_filename, _cluster_dir = self._get_paths(
            cluster_name=cluster_id, create_if_not_exist=True
        )

        command = "{} rollback --kubeconfig={} --home={} {} {} --wait".format(
            self._helm_command, config_filename, helm_dir, kdu_instance, revision
        )

        # exec helm in a task
        exec_task = asyncio.ensure_future(
            coro_or_future=self._local_async_exec(
                command=command, raise_exception_on_error=False
            )
        )
        # write status in another task
        status_task = asyncio.ensure_future(
            coro_or_future=self._store_status(
                cluster_id=cluster_id,
                kdu_instance=kdu_instance,
                db_dict=db_dict,
                operation="rollback",
                run_once=False,
            )
        )

        # wait for execution task
        await asyncio.wait([exec_task])

        # cancel status task
        status_task.cancel()

        output, rc = exec_task.result()

        # write final status
        await self._store_status(
            cluster_id=cluster_id,
            kdu_instance=kdu_instance,
            db_dict=db_dict,
            operation="rollback",
            run_once=True,
            check_every=0,
        )

        if rc != 0:
            msg = "Error executing command: {}\nOutput: {}".format(command, output)
            self.log.error(msg)
            raise K8sException(msg)

        # return new revision number
        instance = await self.get_instance_info(
            cluster_uuid=cluster_uuid, kdu_instance=kdu_instance
        )
        if instance:
            revision = int(instance.get("Revision"))
            self.log.debug("New revision: {}".format(revision))
            return revision
        else:
            return 0

    async def uninstall(self, cluster_uuid: str, kdu_instance: str):
        """
        Removes an existing KDU instance. It would implicitly use the `delete` call
        (this call would happen after all _terminate-config-primitive_ of the VNF
        are invoked).

        :param cluster_uuid: UUID of a K8s cluster known by OSM, or namespace:cluster_id
        :param kdu_instance: unique name for the KDU instance to be deleted
        :return: True if successful
        """

        _, cluster_id = self._get_namespace_cluster_id(cluster_uuid)
        self.log.debug(
            "uninstall kdu_instance {} from cluster {}".format(
                kdu_instance, cluster_id
            )
        )

        # config filename
        _kube_dir, helm_dir, config_filename, _cluster_dir = self._get_paths(
            cluster_name=cluster_id, create_if_not_exist=True
        )

        command = "{} --kubeconfig={} --home={} delete --purge {}".format(
            self._helm_command, config_filename, helm_dir, kdu_instance
        )

        output, _rc = await self._local_async_exec(
            command=command, raise_exception_on_error=True
        )

        return self._output_to_table(output)

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

        :param cluster_uuid str: The UUID of the cluster or namespace:cluster
        :param kdu_instance str: The unique name of the KDU instance
        :param primitive_name: Name of action that will be executed
        :param timeout: Timeout for action execution
        :param params: Dictionary of all the parameters needed for the action
        :db_dict: Dictionary for any additional data

        :return: Returns the output of the action
        """
        raise K8sException(
            "KDUs deployed with Helm don't support actions "
            "different from rollback, upgrade and status"
        )

    async def inspect_kdu(self, kdu_model: str, repo_url: str = None) -> str:

        self.log.debug(
            "inspect kdu_model {} from (optional) repo: {}".format(kdu_model, repo_url)
        )

        return await self._exec_inspect_comand(
            inspect_command="", kdu_model=kdu_model, repo_url=repo_url
        )

    async def values_kdu(self, kdu_model: str, repo_url: str = None) -> str:

        self.log.debug(
            "inspect kdu_model values {} from (optional) repo: {}".format(
                kdu_model, repo_url
            )
        )

        return await self._exec_inspect_comand(
            inspect_command="values", kdu_model=kdu_model, repo_url=repo_url
        )

    async def help_kdu(self, kdu_model: str, repo_url: str = None) -> str:

        self.log.debug(
            "inspect kdu_model {} readme.md from repo: {}".format(kdu_model, repo_url)
        )

        return await self._exec_inspect_comand(
            inspect_command="readme", kdu_model=kdu_model, repo_url=repo_url
        )

    async def status_kdu(self, cluster_uuid: str, kdu_instance: str) -> str:

        # call internal function
        _, cluster_id = self._get_namespace_cluster_id(cluster_uuid)
        return await self._status_kdu(
            cluster_id=cluster_id,
            kdu_instance=kdu_instance,
            show_error_log=True,
            return_text=True,
        )

    async def get_services(self,
                           cluster_uuid: str,
                           kdu_instance: str,
                           namespace: str) -> list:

        self.log.debug(
            "get_services: cluster_uuid: {}, kdu_instance: {}".format(
                cluster_uuid, kdu_instance
            )
        )

        status = await self._status_kdu(
            cluster_uuid, kdu_instance, return_text=False
        )

        service_names = self._parse_helm_status_service_info(status)
        service_list = []
        for service in service_names:
            service = await self.get_service(cluster_uuid, service, namespace)
            service_list.append(service)

        return service_list

    async def get_service(self,
                          cluster_uuid: str,
                          service_name: str,
                          namespace: str) -> object:

        self.log.debug(
            "get service, service_name: {}, namespace: {}, cluster_uuid: {}".format(
                service_name, namespace, cluster_uuid)
        )

        # get paths
        _kube_dir, helm_dir, config_filename, _cluster_dir = self._get_paths(
            cluster_name=cluster_uuid, create_if_not_exist=True
        )

        command = "{} --kubeconfig={} --namespace={} get service {} -o=yaml".format(
            self.kubectl_command, config_filename, namespace, service_name
        )

        output, _rc = await self._local_async_exec(
            command=command, raise_exception_on_error=True
        )

        data = yaml.load(output, Loader=yaml.SafeLoader)

        service = {
            "name": service_name,
            "type": self._get_deep(data, ("spec", "type")),
            "ports": self._get_deep(data, ("spec", "ports")),
            "cluster_ip": self._get_deep(data, ("spec", "clusterIP"))
        }
        if service["type"] == "LoadBalancer":
            ip_map_list = self._get_deep(data, ("status", "loadBalancer", "ingress"))
            ip_list = [elem["ip"] for elem in ip_map_list]
            service["external_ip"] = ip_list

        return service

    async def synchronize_repos(self, cluster_uuid: str):

        _, cluster_id = self._get_namespace_cluster_id(cluster_uuid)
        self.log.debug("syncronize repos for cluster helm-id: {}",)
        try:
            update_repos_timeout = (
                300  # max timeout to sync a single repos, more than this is too much
            )
            db_k8scluster = self.db.get_one(
                "k8sclusters", {"_admin.helm-chart.id": cluster_uuid}
            )
            if db_k8scluster:
                nbi_repo_list = (
                    db_k8scluster.get("_admin").get("helm_chart_repos") or []
                )
                cluster_repo_dict = (
                    db_k8scluster.get("_admin").get("helm_charts_added") or {}
                )
                # elements that must be deleted
                deleted_repo_list = []
                added_repo_dict = {}
                self.log.debug("helm_chart_repos: {}".format(nbi_repo_list))
                self.log.debug("helm_charts_added: {}".format(cluster_repo_dict))

                # obtain repos to add: registered by nbi but not added
                repos_to_add = [
                    repo for repo in nbi_repo_list if not cluster_repo_dict.get(repo)
                ]

                # obtain repos to delete: added by cluster but not in nbi list
                repos_to_delete = [
                    repo
                    for repo in cluster_repo_dict.keys()
                    if repo not in nbi_repo_list
                ]

                # delete repos: must delete first then add because there may be
                # different repos with same name but
                # different id and url
                self.log.debug("repos to delete: {}".format(repos_to_delete))
                for repo_id in repos_to_delete:
                    # try to delete repos
                    try:
                        repo_delete_task = asyncio.ensure_future(
                            self.repo_remove(
                                cluster_uuid=cluster_uuid,
                                name=cluster_repo_dict[repo_id],
                            )
                        )
                        await asyncio.wait_for(repo_delete_task, update_repos_timeout)
                    except Exception as e:
                        self.warning(
                            "Error deleting repo, id: {}, name: {}, err_msg: {}".format(
                                repo_id, cluster_repo_dict[repo_id], str(e)
                            )
                        )
                    # always add to the list of to_delete if there is an error
                    # because if is not there
                    # deleting raises error
                    deleted_repo_list.append(repo_id)

                # add repos
                self.log.debug("repos to add: {}".format(repos_to_add))
                for repo_id in repos_to_add:
                    # obtain the repo data from the db
                    # if there is an error getting the repo in the database we will
                    # ignore this repo and continue
                    # because there is a possible race condition where the repo has
                    # been deleted while processing
                    db_repo = self.db.get_one("k8srepos", {"_id": repo_id})
                    self.log.debug(
                        "obtained repo: id, {}, name: {}, url: {}".format(
                            repo_id, db_repo["name"], db_repo["url"]
                        )
                    )
                    try:
                        repo_add_task = asyncio.ensure_future(
                            self.repo_add(
                                cluster_uuid=cluster_uuid,
                                name=db_repo["name"],
                                url=db_repo["url"],
                                repo_type="chart",
                            )
                        )
                        await asyncio.wait_for(repo_add_task, update_repos_timeout)
                        added_repo_dict[repo_id] = db_repo["name"]
                        self.log.debug(
                            "added repo: id, {}, name: {}".format(
                                repo_id, db_repo["name"]
                            )
                        )
                    except Exception as e:
                        # deal with error adding repo, adding a repo that already
                        # exists does not raise any error
                        # will not raise error because a wrong repos added by
                        # anyone could prevent instantiating any ns
                        self.log.error(
                            "Error adding repo id: {}, err_msg: {} ".format(
                                repo_id, repr(e)
                            )
                        )

                return deleted_repo_list, added_repo_dict

            else:  # else db_k8scluster does not exist
                raise K8sException(
                    "k8cluster with helm-id : {} not found".format(cluster_uuid)
                )

        except Exception as e:
            self.log.error("Error synchronizing repos: {}".format(str(e)))
            raise K8sException("Error synchronizing repos")

    """
    ####################################################################################
    ################################### P R I V A T E ##################################
    ####################################################################################
    """

    async def _exec_inspect_comand(
        self, inspect_command: str, kdu_model: str, repo_url: str = None
    ):

        repo_str = ""
        if repo_url:
            repo_str = " --repo {}".format(repo_url)
            idx = kdu_model.find("/")
            if idx >= 0:
                idx += 1
                kdu_model = kdu_model[idx:]

        inspect_command = "{} inspect {} {}{}".format(
            self._helm_command, inspect_command, kdu_model, repo_str
        )
        output, _rc = await self._local_async_exec(
            command=inspect_command, encode_utf8=True
        )

        return output

    async def _status_kdu(
        self,
        cluster_id: str,
        kdu_instance: str,
        show_error_log: bool = False,
        return_text: bool = False,
    ):

        self.log.debug("status of kdu_instance {}".format(kdu_instance))

        # config filename
        _kube_dir, helm_dir, config_filename, _cluster_dir = self._get_paths(
            cluster_name=cluster_id, create_if_not_exist=True
        )

        command = "{} --kubeconfig={} --home={} status {} --output yaml".format(
            self._helm_command, config_filename, helm_dir, kdu_instance
        )

        output, rc = await self._local_async_exec(
            command=command,
            raise_exception_on_error=True,
            show_error_log=show_error_log,
        )

        if return_text:
            return str(output)

        if rc != 0:
            return None

        data = yaml.load(output, Loader=yaml.SafeLoader)

        # remove field 'notes'
        try:
            del data.get("info").get("status")["notes"]
        except KeyError:
            pass

        # parse field 'resources'
        try:
            resources = str(data.get("info").get("status").get("resources"))
            resource_table = self._output_to_table(resources)
            data.get("info").get("status")["resources"] = resource_table
        except Exception:
            pass

        return data

    async def get_instance_info(self, cluster_uuid: str, kdu_instance: str):
        instances = await self.instances_list(cluster_uuid=cluster_uuid)
        for instance in instances:
            if instance.get("Name") == kdu_instance:
                return instance
        self.log.debug("Instance {} not found".format(kdu_instance))
        return None

    @staticmethod
    def _generate_release_name(chart_name: str):
        # check embeded chart (file or dir)
        if chart_name.startswith("/"):
            # extract file or directory name
            chart_name = chart_name[chart_name.rfind("/") + 1 :]
        # check URL
        elif "://" in chart_name:
            # extract last portion of URL
            chart_name = chart_name[chart_name.rfind("/") + 1 :]

        name = ""
        for c in chart_name:
            if c.isalpha() or c.isnumeric():
                name += c
            else:
                name += "-"
        if len(name) > 35:
            name = name[0:35]

        # if does not start with alpha character, prefix 'a'
        if not name[0].isalpha():
            name = "a" + name

        name += "-"

        def get_random_number():
            r = random.randrange(start=1, stop=99999999)
            s = str(r)
            s = s.rjust(10, "0")
            return s

        name = name + get_random_number()
        return name.lower()

    async def _store_status(
        self,
        cluster_id: str,
        operation: str,
        kdu_instance: str,
        check_every: float = 10,
        db_dict: dict = None,
        run_once: bool = False,
    ):
        while True:
            try:
                await asyncio.sleep(check_every)
                detailed_status = await self._status_kdu(
                    cluster_id=cluster_id, kdu_instance=kdu_instance,
                    return_text=False
                )
                status = detailed_status.get("info").get("Description")
                self.log.debug('KDU {} STATUS: {}.'.format(kdu_instance, status))
                # write status to db
                result = await self.write_app_status_to_db(
                    db_dict=db_dict,
                    status=str(status),
                    detailed_status=str(detailed_status),
                    operation=operation,
                )
                if not result:
                    self.log.info("Error writing in database. Task exiting...")
                    return
            except asyncio.CancelledError:
                self.log.debug("Task cancelled")
                return
            except Exception as e:
                self.log.debug("_store_status exception: {}".format(str(e)), exc_info=True)
                pass
            finally:
                if run_once:
                    return

    async def _is_install_completed(self, cluster_id: str, kdu_instance: str) -> bool:

        status = await self._status_kdu(
            cluster_id=cluster_id, kdu_instance=kdu_instance, return_text=False
        )

        # extract info.status.resources-> str
        # format:
        #       ==> v1/Deployment
        #       NAME                    READY   UP-TO-DATE   AVAILABLE   AGE
        #       halting-horse-mongodb   0/1     1            0           0s
        #       halting-petit-mongodb   1/1     1            0           0s
        # blank line
        resources = K8sHelmConnector._get_deep(status, ("info", "status", "resources"))

        # convert to table
        resources = K8sHelmConnector._output_to_table(resources)

        num_lines = len(resources)
        index = 0
        while index < num_lines:
            try:
                line1 = resources[index]
                index += 1
                # find '==>' in column 0
                if line1[0] == "==>":
                    line2 = resources[index]
                    index += 1
                    # find READY in column 1
                    if line2[1] == "READY":
                        # read next lines
                        line3 = resources[index]
                        index += 1
                        while len(line3) > 1 and index < num_lines:
                            ready_value = line3[1]
                            parts = ready_value.split(sep="/")
                            current = int(parts[0])
                            total = int(parts[1])
                            if current < total:
                                self.log.debug("NOT READY:\n    {}".format(line3))
                                ready = False
                            line3 = resources[index]
                            index += 1

            except Exception:
                pass

        return ready

    def _parse_helm_status_service_info(self, status):

        # extract info.status.resources-> str
        # format:
        #       ==> v1/Deployment
        #       NAME                    READY   UP-TO-DATE   AVAILABLE   AGE
        #       halting-horse-mongodb   0/1     1            0           0s
        #       halting-petit-mongodb   1/1     1            0           0s
        # blank line
        resources = K8sHelmConnector._get_deep(status, ("info", "status", "resources"))

        service_list = []
        first_line_skipped = service_found = False
        for line in resources:
            if not service_found:
                if len(line) >= 2 and line[0] == "==>" and line[1] == "v1/Service":
                    service_found = True
                    continue
            else:
                if len(line) >= 2 and line[0] == "==>":
                    service_found = first_line_skipped = False
                    continue
                if not line:
                    continue
                if not first_line_skipped:
                    first_line_skipped = True
                    continue
                service_list.append(line[0])

        return service_list

    @staticmethod
    def _get_deep(dictionary: dict, members: tuple):
        target = dictionary
        value = None
        try:
            for m in members:
                value = target.get(m)
                if not value:
                    return None
                else:
                    target = value
        except Exception:
            pass
        return value

    # find key:value in several lines
    @staticmethod
    def _find_in_lines(p_lines: list, p_key: str) -> str:
        for line in p_lines:
            try:
                if line.startswith(p_key + ":"):
                    parts = line.split(":")
                    the_value = parts[1].strip()
                    return the_value
            except Exception:
                # ignore it
                pass
        return None

    # params for use in -f file
    # returns values file option and filename (in order to delete it at the end)
    def _params_to_file_option(self, cluster_id: str, params: dict) -> (str, str):

        if params and len(params) > 0:
            self._get_paths(cluster_name=cluster_id, create_if_not_exist=True)

            def get_random_number():
                r = random.randrange(start=1, stop=99999999)
                s = str(r)
                while len(s) < 10:
                    s = "0" + s
                return s

            params2 = dict()
            for key in params:
                value = params.get(key)
                if "!!yaml" in str(value):
                    value = yaml.load(value[7:])
                params2[key] = value

            values_file = get_random_number() + ".yaml"
            with open(values_file, "w") as stream:
                yaml.dump(params2, stream, indent=4, default_flow_style=False)

            return "-f {}".format(values_file), values_file

        return "", None

    # params for use in --set option
    @staticmethod
    def _params_to_set_option(params: dict) -> str:
        params_str = ""
        if params and len(params) > 0:
            start = True
            for key in params:
                value = params.get(key, None)
                if value is not None:
                    if start:
                        params_str += "--set "
                        start = False
                    else:
                        params_str += ","
                    params_str += "{}={}".format(key, value)
        return params_str

    @staticmethod
    def _output_to_lines(output: str) -> list:
        output_lines = list()
        lines = output.splitlines(keepends=False)
        for line in lines:
            line = line.strip()
            if len(line) > 0:
                output_lines.append(line)
        return output_lines

    @staticmethod
    def _output_to_table(output: str) -> list:
        output_table = list()
        lines = output.splitlines(keepends=False)
        for line in lines:
            line = line.replace("\t", " ")
            line_list = list()
            output_table.append(line_list)
            cells = line.split(sep=" ")
            for cell in cells:
                cell = cell.strip()
                if len(cell) > 0:
                    line_list.append(cell)
        return output_table

    def _get_paths(
        self, cluster_name: str, create_if_not_exist: bool = False
    ) -> (str, str, str, str):
        """
        Returns kube and helm directories

        :param cluster_name:
        :param create_if_not_exist:
        :return: kube, helm directories, config filename and cluster dir.
                Raises exception if not exist and cannot create
        """

        base = self.fs.path
        if base.endswith("/") or base.endswith("\\"):
            base = base[:-1]

        # base dir for cluster
        cluster_dir = base + "/" + cluster_name
        if create_if_not_exist and not os.path.exists(cluster_dir):
            self.log.debug("Creating dir {}".format(cluster_dir))
            os.makedirs(cluster_dir)
        if not os.path.exists(cluster_dir):
            msg = "Base cluster dir {} does not exist".format(cluster_dir)
            self.log.error(msg)
            raise K8sException(msg)

        # kube dir
        kube_dir = cluster_dir + "/" + ".kube"
        if create_if_not_exist and not os.path.exists(kube_dir):
            self.log.debug("Creating dir {}".format(kube_dir))
            os.makedirs(kube_dir)
        if not os.path.exists(kube_dir):
            msg = "Kube config dir {} does not exist".format(kube_dir)
            self.log.error(msg)
            raise K8sException(msg)

        # helm home dir
        helm_dir = cluster_dir + "/" + ".helm"
        if create_if_not_exist and not os.path.exists(helm_dir):
            self.log.debug("Creating dir {}".format(helm_dir))
            os.makedirs(helm_dir)
        if not os.path.exists(helm_dir):
            msg = "Helm config dir {} does not exist".format(helm_dir)
            self.log.error(msg)
            raise K8sException(msg)

        config_filename = kube_dir + "/config"
        return kube_dir, helm_dir, config_filename, cluster_dir

    @staticmethod
    def _remove_multiple_spaces(strobj):
        strobj = strobj.strip()
        while "  " in strobj:
            strobj = strobj.replace("  ", " ")
        return strobj

    def _local_exec(self, command: str) -> (str, int):
        command = K8sHelmConnector._remove_multiple_spaces(command)
        self.log.debug("Executing sync local command: {}".format(command))
        # raise exception if fails
        output = ""
        try:
            output = subprocess.check_output(
                command, shell=True, universal_newlines=True
            )
            return_code = 0
            self.log.debug(output)
        except Exception:
            return_code = 1

        return output, return_code

    async def _local_async_exec(
        self,
        command: str,
        raise_exception_on_error: bool = False,
        show_error_log: bool = True,
        encode_utf8: bool = False,
    ) -> (str, int):

        command = K8sHelmConnector._remove_multiple_spaces(command)
        self.log.debug("Executing async local command: {}".format(command))

        # split command
        command = command.split(sep=" ")

        try:
            process = await asyncio.create_subprocess_exec(
                *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )

            # wait for command terminate
            stdout, stderr = await process.communicate()

            return_code = process.returncode

            output = ""
            if stdout:
                output = stdout.decode("utf-8").strip()
                # output = stdout.decode()
            if stderr:
                output = stderr.decode("utf-8").strip()
                # output = stderr.decode()

            if return_code != 0 and show_error_log:
                self.log.debug(
                    "Return code (FAIL): {}\nOutput:\n{}".format(return_code, output)
                )
            else:
                self.log.debug("Return code: {}".format(return_code))

            if raise_exception_on_error and return_code != 0:
                raise K8sException(output)

            if encode_utf8:
                output = output.encode("utf-8").strip()
                output = str(output).replace("\\n", "\n")

            return output, return_code

        except asyncio.CancelledError:
            raise
        except K8sException:
            raise
        except Exception as e:
            msg = "Exception executing command: {} -> {}".format(command, e)
            self.log.error(msg)
            if raise_exception_on_error:
                raise K8sException(e) from e
            else:
                return "", -1

    def _check_file_exists(self, filename: str, exception_if_not_exists: bool = False):
        # self.log.debug('Checking if file {} exists...'.format(filename))
        if os.path.exists(filename):
            return True
        else:
            msg = "File {} does not exist".format(filename)
            if exception_if_not_exists:
                # self.log.error(msg)
                raise K8sException(msg)
