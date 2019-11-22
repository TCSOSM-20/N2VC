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

import paramiko
import subprocess
import os
import shutil
import asyncio
import time
import yaml
from uuid import uuid4
import random
from n2vc.k8s_conn import K8sConnector


class K8sHelmConnector(K8sConnector):

    """
    ##################################################################################################
    ########################################## P U B L I C ###########################################
    ##################################################################################################
    """

    def __init__(
            self,
            fs: object,
            db: object,
            kubectl_command: str = '/usr/bin/kubectl',
            helm_command: str = '/usr/bin/helm',
            log: object = None,
            on_update_db=None
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
        K8sConnector.__init__(
            self,
            db=db,
            log=log,
            on_update_db=on_update_db
        )

        self.info('Initializing K8S Helm connector')

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

        self.info('K8S Helm connector initialized')

    async def init_env(
            self,
            k8s_creds: str,
            namespace: str = 'kube-system',
            reuse_cluster_uuid=None
    ) -> (str, bool):

        cluster_uuid = reuse_cluster_uuid
        if not cluster_uuid:
            cluster_uuid = str(uuid4())

        self.debug('Initializing K8S environment. namespace: {}'.format(namespace))

        # create config filename
        kube_dir, helm_dir, config_filename, cluster_dir = \
            self._get_paths(cluster_name=cluster_uuid, create_if_not_exist=True)
        f = open(config_filename, "w")
        f.write(k8s_creds)
        f.close()

        # check if tiller pod is up in cluster
        command = '{} --kubeconfig={} --namespace={} get deployments'\
            .format(self.kubectl_command, config_filename, namespace)
        output, rc = await self._local_async_exec(command=command, raise_exception_on_error=True)

        output_table = K8sHelmConnector._output_to_table(output=output)

        # find 'tiller' pod in all pods
        already_initialized = False
        try:
            for row in output_table:
                if row[0].startswith('tiller-deploy'):
                    already_initialized = True
                    break
        except Exception as e:
            pass

        # helm init
        n2vc_installed_sw = False
        if not already_initialized:
            self.info('Initializing helm in client and server: {}'.format(cluster_uuid))
            command = '{} --kubeconfig={} --tiller-namespace={} --home={} init'\
                .format(self._helm_command, config_filename, namespace, helm_dir)
            output, rc = await self._local_async_exec(command=command, raise_exception_on_error=True)
            n2vc_installed_sw = True
        else:
            # check client helm installation
            check_file = helm_dir + '/repository/repositories.yaml'
            if not self._check_file_exists(filename=check_file, exception_if_not_exists=False):
                self.info('Initializing helm in client: {}'.format(cluster_uuid))
                command = '{} --kubeconfig={} --tiller-namespace={} --home={} init --client-only'\
                    .format(self._helm_command, config_filename, namespace, helm_dir)
                output, rc = await self._local_async_exec(command=command, raise_exception_on_error=True)
            else:
                self.info('Helm client already initialized')

        self.info('Cluster initialized {}'.format(cluster_uuid))

        return cluster_uuid, n2vc_installed_sw

    async def repo_add(
            self,
            cluster_uuid: str,
            name: str,
            url: str,
            repo_type: str = 'chart'
    ):

        self.debug('adding {} repository {}. URL: {}'.format(repo_type, name, url))

        # config filename
        kube_dir, helm_dir, config_filename, cluster_dir = \
            self._get_paths(cluster_name=cluster_uuid, create_if_not_exist=True)

        # helm repo update
        command = '{} --kubeconfig={} --home={} repo update'.format(self._helm_command, config_filename, helm_dir)
        self.debug('updating repo: {}'.format(command))
        await self._local_async_exec(command=command, raise_exception_on_error=False)

        # helm repo add name url
        command = '{} --kubeconfig={} --home={} repo add {} {}'\
            .format(self._helm_command, config_filename, helm_dir, name, url)
        self.debug('adding repo: {}'.format(command))
        await self._local_async_exec(command=command, raise_exception_on_error=True)

    async def repo_list(
            self,
            cluster_uuid: str
    ) -> list:
        """
        Get the list of registered repositories

        :return: list of registered repositories: [ (name, url) .... ]
        """

        self.debug('list repositories for cluster {}'.format(cluster_uuid))

        # config filename
        kube_dir, helm_dir, config_filename, cluster_dir = \
            self._get_paths(cluster_name=cluster_uuid, create_if_not_exist=True)

        command = '{} --kubeconfig={} --home={} repo list --output yaml'.format(self._helm_command, config_filename, helm_dir)

        output, rc = await self._local_async_exec(command=command, raise_exception_on_error=True)
        if output and len(output) > 0:
            return yaml.load(output, Loader=yaml.SafeLoader)
        else:
            return []

    async def repo_remove(
            self,
            cluster_uuid: str,
            name: str
    ):
        """
        Remove a repository from OSM

        :param cluster_uuid: the cluster
        :param name: repo name in OSM
        :return: True if successful
        """

        self.debug('list repositories for cluster {}'.format(cluster_uuid))

        # config filename
        kube_dir, helm_dir, config_filename, cluster_dir = \
            self._get_paths(cluster_name=cluster_uuid, create_if_not_exist=True)

        command = '{} --kubeconfig={} --home={} repo remove {}'\
            .format(self._helm_command, config_filename, helm_dir, name)

        await self._local_async_exec(command=command, raise_exception_on_error=True)

    async def reset(
            self,
            cluster_uuid: str,
            force: bool = False,
            uninstall_sw: bool = False
    ) -> bool:

        self.debug('Resetting K8s environment. cluster uuid: {}'.format(cluster_uuid))

        # get kube and helm directories
        kube_dir, helm_dir, config_filename, cluster_dir = \
            self._get_paths(cluster_name=cluster_uuid, create_if_not_exist=False)

        # uninstall releases if needed
        releases = await self.instances_list(cluster_uuid=cluster_uuid)
        if len(releases) > 0:
            if force:
                for r in releases:
                    try:
                        kdu_instance = r.get('Name')
                        chart = r.get('Chart')
                        self.debug('Uninstalling {} -> {}'.format(chart, kdu_instance))
                        await self.uninstall(cluster_uuid=cluster_uuid, kdu_instance=kdu_instance)
                    except Exception as e:
                        self.error('Error uninstalling release {}: {}'.format(kdu_instance, e))
            else:
                msg = 'Cluster has releases and not force. Cannot reset K8s environment. Cluster uuid: {}'\
                    .format(cluster_uuid)
                self.error(msg)
                raise Exception(msg)

        if uninstall_sw:

            self.debug('Uninstalling tiller from cluster {}'.format(cluster_uuid))

            # find namespace for tiller pod
            command = '{} --kubeconfig={} get deployments --all-namespaces'\
                .format(self.kubectl_command, config_filename)
            output, rc = await self._local_async_exec(command=command, raise_exception_on_error=False)
            output_table = K8sHelmConnector._output_to_table(output=output)
            namespace = None
            for r in output_table:
                try:
                    if 'tiller-deploy' in r[1]:
                        namespace = r[0]
                        break
                except Exception as e:
                    pass
            else:
                msg = 'Tiller deployment not found in cluster {}'.format(cluster_uuid)
                self.error(msg)
                # raise Exception(msg)

            self.debug('namespace for tiller: {}'.format(namespace))

            force_str = '--force'

            if namespace:
                # delete tiller deployment
                self.debug('Deleting tiller deployment for cluster {}, namespace {}'.format(cluster_uuid, namespace))
                command = '{} --namespace {} --kubeconfig={} {} delete deployment tiller-deploy'\
                    .format(self.kubectl_command, namespace, config_filename, force_str)
                await self._local_async_exec(command=command, raise_exception_on_error=False)

                # uninstall tiller from cluster
                self.debug('Uninstalling tiller from cluster {}'.format(cluster_uuid))
                command = '{} --kubeconfig={} --home={} reset'\
                    .format(self._helm_command, config_filename, helm_dir)
                self.debug('resetting: {}'.format(command))
                output, rc = await self._local_async_exec(command=command, raise_exception_on_error=True)
            else:
                self.debug('namespace not found')

        # delete cluster directory
        dir = self.fs.path + '/' + cluster_uuid
        self.debug('Removing directory {}'.format(dir))
        shutil.rmtree(dir, ignore_errors=True)

        return True

    async def install(
            self,
            cluster_uuid: str,
            kdu_model: str,
            atomic: bool = True,
            timeout: float = 300,
            params: dict = None,
            db_dict: dict = None
    ):

        self.debug('installing {} in cluster {}'.format(kdu_model, cluster_uuid))

        start = time.time()
        end = start + timeout

        # config filename
        kube_dir, helm_dir, config_filename, cluster_dir = \
            self._get_paths(cluster_name=cluster_uuid, create_if_not_exist=True)

        # params to str
        # params_str = K8sHelmConnector._params_to_set_option(params)
        params_str, file_to_delete = self._params_to_file_option(cluster_uuid=cluster_uuid, params=params)

        timeout_str = ''
        if timeout:
            timeout_str = '--timeout {}'.format(timeout)

        # atomic
        atomic_str = ''
        if atomic:
            atomic_str = '--atomic'

        # version
        version_str = ''
        if ':' in kdu_model:
            parts = kdu_model.split(sep=':')
            if len(parts) == 2:
                version_str = '--version {}'.format(parts[1])
                kdu_model = parts[0]

        # generate a name for the releas. Then, check if already exists
        kdu_instance = None
        while kdu_instance is None:
            kdu_instance = K8sHelmConnector._generate_release_name(kdu_model)
            try:
                result = await self._status_kdu(
                    cluster_uuid=cluster_uuid,
                    kdu_instance=kdu_instance,
                    show_error_log=False
                )
                if result is not None:
                    # instance already exists: generate a new one
                    kdu_instance = None
            except:
                kdu_instance = None

        # helm repo install
        command = '{} install {} --output yaml --kubeconfig={} --home={} {} {} --name={} {} {}'\
            .format(self._helm_command, atomic_str, config_filename, helm_dir,
                    params_str, timeout_str, kdu_instance, kdu_model, version_str)
        self.debug('installing: {}'.format(command))

        if atomic:
            # exec helm in a task
            exec_task = asyncio.ensure_future(
                coro_or_future=self._local_async_exec(command=command, raise_exception_on_error=False)
            )
            # write status in another task
            status_task = asyncio.ensure_future(
                coro_or_future=self._store_status(
                    cluster_uuid=cluster_uuid,
                    kdu_instance=kdu_instance,
                    db_dict=db_dict,
                    operation='install',
                    run_once=False
                )
            )

            # wait for execution task
            await asyncio.wait([exec_task])

            # cancel status task
            status_task.cancel()

            output, rc = exec_task.result()

        else:

            output, rc = await self._local_async_exec(command=command, raise_exception_on_error=False)

        # remove temporal values yaml file
        if file_to_delete:
            os.remove(file_to_delete)

        # write final status
        await self._store_status(
            cluster_uuid=cluster_uuid,
            kdu_instance=kdu_instance,
            db_dict=db_dict,
            operation='install',
            run_once=True,
            check_every=0
        )

        if rc != 0:
            msg = 'Error executing command: {}\nOutput: {}'.format(command, output)
            self.error(msg)
            raise Exception(msg)

        self.debug('Returning kdu_instance {}'.format(kdu_instance))
        return kdu_instance

    async def instances_list(
            self,
            cluster_uuid: str
    ) -> list:
        """
        returns a list of deployed releases in a cluster

        :param cluster_uuid: the cluster
        :return:
        """

        self.debug('list releases for cluster {}'.format(cluster_uuid))

        # config filename
        kube_dir, helm_dir, config_filename, cluster_dir = \
            self._get_paths(cluster_name=cluster_uuid, create_if_not_exist=True)

        command = '{} --kubeconfig={} --home={} list --output yaml'\
            .format(self._helm_command, config_filename, helm_dir)

        output, rc = await self._local_async_exec(command=command, raise_exception_on_error=True)

        if output and len(output) > 0:
            return yaml.load(output, Loader=yaml.SafeLoader).get('Releases')
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
            db_dict: dict = None
    ):

        self.debug('upgrading {} in cluster {}'.format(kdu_model, cluster_uuid))

        start = time.time()
        end = start + timeout

        # config filename
        kube_dir, helm_dir, config_filename, cluster_dir = \
            self._get_paths(cluster_name=cluster_uuid, create_if_not_exist=True)

        # params to str
        # params_str = K8sHelmConnector._params_to_set_option(params)
        params_str, file_to_delete = self._params_to_file_option(cluster_uuid=cluster_uuid, params=params)

        timeout_str = ''
        if timeout:
            timeout_str = '--timeout {}'.format(timeout)

        # atomic
        atomic_str = ''
        if atomic:
            atomic_str = '--atomic'

        # version
        version_str = ''
        if kdu_model and ':' in kdu_model:
            parts = kdu_model.split(sep=':')
            if len(parts) == 2:
                version_str = '--version {}'.format(parts[1])
                kdu_model = parts[0]

        # helm repo upgrade
        command = '{} upgrade {} --output yaml --kubeconfig={} --home={} {} {} {} {} {}'\
            .format(self._helm_command, atomic_str, config_filename, helm_dir,
                    params_str, timeout_str, kdu_instance, kdu_model, version_str)
        self.debug('upgrading: {}'.format(command))

        if atomic:

            # exec helm in a task
            exec_task = asyncio.ensure_future(
                coro_or_future=self._local_async_exec(command=command, raise_exception_on_error=False)
            )
            # write status in another task
            status_task = asyncio.ensure_future(
                coro_or_future=self._store_status(
                    cluster_uuid=cluster_uuid,
                    kdu_instance=kdu_instance,
                    db_dict=db_dict,
                    operation='upgrade',
                    run_once=False
                )
            )

            # wait for execution task
            await asyncio.wait([ exec_task ])

            # cancel status task
            status_task.cancel()
            output, rc = exec_task.result()

        else:

            output, rc = await self._local_async_exec(command=command, raise_exception_on_error=False)

        # remove temporal values yaml file
        if file_to_delete:
            os.remove(file_to_delete)

        # write final status
        await self._store_status(
            cluster_uuid=cluster_uuid,
            kdu_instance=kdu_instance,
            db_dict=db_dict,
            operation='upgrade',
            run_once=True,
            check_every=0
        )

        if rc != 0:
            msg = 'Error executing command: {}\nOutput: {}'.format(command, output)
            self.error(msg)
            raise Exception(msg)

        # return new revision number
        instance = await self.get_instance_info(cluster_uuid=cluster_uuid, kdu_instance=kdu_instance)
        if instance:
            revision = int(instance.get('Revision'))
            self.debug('New revision: {}'.format(revision))
            return revision
        else:
            return 0

    async def rollback(
            self,
            cluster_uuid: str,
            kdu_instance: str,
            revision=0,
            db_dict: dict = None
    ):

        self.debug('rollback kdu_instance {} to revision {} from cluster {}'
                   .format(kdu_instance, revision, cluster_uuid))

        # config filename
        kube_dir, helm_dir, config_filename, cluster_dir = \
            self._get_paths(cluster_name=cluster_uuid, create_if_not_exist=True)

        command = '{} rollback --kubeconfig={} --home={} {} {} --wait'\
            .format(self._helm_command, config_filename, helm_dir, kdu_instance, revision)

        # exec helm in a task
        exec_task = asyncio.ensure_future(
            coro_or_future=self._local_async_exec(command=command, raise_exception_on_error=False)
        )
        # write status in another task
        status_task = asyncio.ensure_future(
            coro_or_future=self._store_status(
                cluster_uuid=cluster_uuid,
                kdu_instance=kdu_instance,
                db_dict=db_dict,
                operation='rollback',
                run_once=False
            )
        )

        # wait for execution task
        await asyncio.wait([exec_task])

        # cancel status task
        status_task.cancel()

        output, rc = exec_task.result()

        # write final status
        await self._store_status(
            cluster_uuid=cluster_uuid,
            kdu_instance=kdu_instance,
            db_dict=db_dict,
            operation='rollback',
            run_once=True,
            check_every=0
        )

        if rc != 0:
            msg = 'Error executing command: {}\nOutput: {}'.format(command, output)
            self.error(msg)
            raise Exception(msg)

        # return new revision number
        instance = await self.get_instance_info(cluster_uuid=cluster_uuid, kdu_instance=kdu_instance)
        if instance:
            revision = int(instance.get('Revision'))
            self.debug('New revision: {}'.format(revision))
            return revision
        else:
            return 0

    async def uninstall(
            self,
            cluster_uuid: str,
            kdu_instance: str
    ):
        """
        Removes an existing KDU instance. It would implicitly use the `delete` call (this call would happen
        after all _terminate-config-primitive_ of the VNF are invoked).

        :param cluster_uuid: UUID of a K8s cluster known by OSM
        :param kdu_instance: unique name for the KDU instance to be deleted
        :return: True if successful
        """

        self.debug('uninstall kdu_instance {} from cluster {}'.format(kdu_instance, cluster_uuid))

        # config filename
        kube_dir, helm_dir, config_filename, cluster_dir = \
            self._get_paths(cluster_name=cluster_uuid, create_if_not_exist=True)

        command = '{} --kubeconfig={} --home={} delete --purge {}'\
            .format(self._helm_command, config_filename, helm_dir, kdu_instance)

        output, rc = await self._local_async_exec(command=command, raise_exception_on_error=True)

        return self._output_to_table(output)

    async def inspect_kdu(
            self,
            kdu_model: str
    ) -> str:

        self.debug('inspect kdu_model {}'.format(kdu_model))

        command = '{} inspect values {}'\
            .format(self._helm_command, kdu_model)

        output, rc = await self._local_async_exec(command=command)

        return output

    async def help_kdu(
            self,
            kdu_model: str
    ):

        self.debug('help kdu_model {}'.format(kdu_model))

        command = '{} inspect readme {}'\
            .format(self._helm_command, kdu_model)

        output, rc = await self._local_async_exec(command=command, raise_exception_on_error=True)

        return output

    async def status_kdu(
            self,
            cluster_uuid: str,
            kdu_instance: str
    ):

        return await self._status_kdu(cluster_uuid=cluster_uuid, kdu_instance=kdu_instance, show_error_log=True)


    """
    ##################################################################################################
    ########################################## P R I V A T E #########################################
    ##################################################################################################
    """

    async def _status_kdu(
            self,
            cluster_uuid: str,
            kdu_instance: str,
            show_error_log: bool = False
    ):

        self.debug('status of kdu_instance {}'.format(kdu_instance))

        # config filename
        kube_dir, helm_dir, config_filename, cluster_dir = \
            self._get_paths(cluster_name=cluster_uuid, create_if_not_exist=True)

        command = '{} --kubeconfig={} --home={} status {} --output yaml'\
            .format(self._helm_command, config_filename, helm_dir, kdu_instance)

        output, rc = await self._local_async_exec(
            command=command,
            raise_exception_on_error=True,
            show_error_log=show_error_log
        )

        if rc != 0:
            return None

        data = yaml.load(output, Loader=yaml.SafeLoader)

        # remove field 'notes'
        try:
            del data.get('info').get('status')['notes']
        except KeyError:
            pass

        # parse field 'resources'
        try:
            resources = str(data.get('info').get('status').get('resources'))
            resource_table = self._output_to_table(resources)
            data.get('info').get('status')['resources'] = resource_table
        except Exception as e:
            pass

        return data

    async def get_instance_info(
            self,
            cluster_uuid: str,
            kdu_instance: str
    ):
        instances = await self.instances_list(cluster_uuid=cluster_uuid)
        for instance in instances:
            if instance.get('Name') == kdu_instance:
                return instance
        self.debug('Instance {} not found'.format(kdu_instance))
        return None

    @staticmethod
    def _generate_release_name(
            chart_name: str
    ):
        name = ''
        for c in chart_name:
            if c.isalpha() or c.isnumeric():
                name += c
            else:
                name += '-'
        if len(name) > 35:
            name = name[0:35]

        # if does not start with alpha character, prefix 'a'
        if not name[0].isalpha():
            name = 'a' + name

        name += '-'

        def get_random_number():
            r = random.randrange(start=1, stop=99999999)
            s = str(r)
            s = s.rjust(width=10, fillchar=' ')
            return s

        name = name + get_random_number()
        return name.lower()

    async def _store_status(
            self,
            cluster_uuid: str,
            operation: str,
            kdu_instance: str,
            check_every: float = 10,
            db_dict: dict = None,
            run_once: bool = False
    ):
        while True:
            try:
                await asyncio.sleep(check_every)
                detailed_status = await self.status_kdu(cluster_uuid=cluster_uuid, kdu_instance=kdu_instance)
                status = detailed_status.get('info').get('Description')
                print('=' * 60)
                self.debug('STATUS:\n{}'.format(status))
                self.debug('DETAILED STATUS:\n{}'.format(detailed_status))
                print('=' * 60)
                # write status to db
                result = await self.write_app_status_to_db(
                    db_dict=db_dict,
                    status=str(status),
                    detailed_status=str(detailed_status),
                    operation=operation)
                if not result:
                    self.info('Error writing in database. Task exiting...')
                    return
            except asyncio.CancelledError:
                self.debug('Task cancelled')
                return
            except Exception as e:
                pass
            finally:
                if run_once:
                    return

    async def _is_install_completed(
            self,
            cluster_uuid: str,
            kdu_instance: str
    ) -> bool:

        status = await self.status_kdu(cluster_uuid=cluster_uuid, kdu_instance=kdu_instance)

        # extract info.status.resources-> str
        # format:
        #       ==> v1/Deployment
        #       NAME                    READY   UP-TO-DATE   AVAILABLE   AGE
        #       halting-horse-mongodb   0/1     1            0           0s
        #       halting-petit-mongodb   1/1     1            0           0s
        # blank line
        resources = K8sHelmConnector._get_deep(status, ('info', 'status', 'resources'))

        # convert to table
        resources = K8sHelmConnector._output_to_table(resources)

        num_lines = len(resources)
        index = 0
        while index < num_lines:
            try:
                line1 = resources[index]
                index += 1
                # find '==>' in column 0
                if line1[0] == '==>':
                    line2 = resources[index]
                    index += 1
                    # find READY in column 1
                    if line2[1] == 'READY':
                        # read next lines
                        line3 = resources[index]
                        index += 1
                        while len(line3) > 1 and index < num_lines:
                            ready_value = line3[1]
                            parts = ready_value.split(sep='/')
                            current = int(parts[0])
                            total = int(parts[1])
                            if current < total:
                                self.debug('NOT READY:\n    {}'.format(line3))
                                ready = False
                            line3 = resources[index]
                            index += 1

            except Exception as e:
                pass

        return ready

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
        except Exception as e:
            pass
        return value

    # find key:value in several lines
    @staticmethod
    def _find_in_lines(p_lines: list, p_key: str) -> str:
        for line in p_lines:
            try:
                if line.startswith(p_key + ':'):
                    parts = line.split(':')
                    the_value = parts[1].strip()
                    return the_value
            except Exception as e:
                # ignore it
                pass
        return None

    # params for use in -f file
    # returns values file option and filename (in order to delete it at the end)
    def _params_to_file_option(self, cluster_uuid: str, params: dict) -> (str, str):
        params_str = ''

        if params and len(params) > 0:
            kube_dir, helm_dir, config_filename, cluster_dir = \
                self._get_paths(cluster_name=cluster_uuid, create_if_not_exist=True)

            def get_random_number():
                r = random.randrange(start=1, stop=99999999)
                s = str(r)
                while len(s) < 10:
                    s = '0' + s
                return s

            params2 = dict()
            for key in params:
                value = params.get(key)
                if '!!yaml' in str(value):
                    value = yaml.load(value[7:])
                params2[key] = value

            values_file = get_random_number() + '.yaml'
            with open(values_file, 'w') as stream:
                yaml.dump(params2, stream, indent=4, default_flow_style=False)

            return '-f {}'.format(values_file), values_file

        return '', None

    # params for use in --set option
    @staticmethod
    def _params_to_set_option(params: dict) -> str:
        params_str = ''
        if params and len(params) > 0:
            start = True
            for key in params:
                value = params.get(key, None)
                if value is not None:
                    if start:
                        params_str += '--set '
                        start = False
                    else:
                        params_str += ','
                    params_str += '{}={}'.format(key, value)
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
            line = line.replace('\t', ' ')
            line_list = list()
            output_table.append(line_list)
            cells = line.split(sep=' ')
            for cell in cells:
                cell = cell.strip()
                if len(cell) > 0:
                    line_list.append(cell)
        return output_table

    def _get_paths(self, cluster_name: str, create_if_not_exist: bool = False) -> (str, str, str, str):
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
        cluster_dir = base + '/' + cluster_name
        if create_if_not_exist and not os.path.exists(cluster_dir):
            self.debug('Creating dir {}'.format(cluster_dir))
            os.makedirs(cluster_dir)
        if not os.path.exists(cluster_dir):
            msg = 'Base cluster dir {} does not exist'.format(cluster_dir)
            self.error(msg)
            raise Exception(msg)

        # kube dir
        kube_dir = cluster_dir + '/' + '.kube'
        if create_if_not_exist and not os.path.exists(kube_dir):
            self.debug('Creating dir {}'.format(kube_dir))
            os.makedirs(kube_dir)
        if not os.path.exists(kube_dir):
            msg = 'Kube config dir {} does not exist'.format(kube_dir)
            self.error(msg)
            raise Exception(msg)

        # helm home dir
        helm_dir = cluster_dir + '/' + '.helm'
        if create_if_not_exist and not os.path.exists(helm_dir):
            self.debug('Creating dir {}'.format(helm_dir))
            os.makedirs(helm_dir)
        if not os.path.exists(helm_dir):
            msg = 'Helm config dir {} does not exist'.format(helm_dir)
            self.error(msg)
            raise Exception(msg)

        config_filename = kube_dir + '/config'
        return kube_dir, helm_dir, config_filename, cluster_dir

    @staticmethod
    def _remove_multiple_spaces(str):
        str = str.strip()
        while '  ' in str:
            str = str.replace('  ', ' ')
        return str

    def _local_exec(
            self,
            command: str
    ) -> (str, int):
        command = K8sHelmConnector._remove_multiple_spaces(command)
        self.debug('Executing sync local command: {}'.format(command))
        # raise exception if fails
        output = ''
        try:
            output = subprocess.check_output(command, shell=True, universal_newlines=True)
            return_code = 0
            self.debug(output)
        except Exception as e:
            return_code = 1

        return output, return_code

    async def _local_async_exec(
            self,
            command: str,
            raise_exception_on_error: bool = False,
            show_error_log: bool = True
    ) -> (str, int):

        command = K8sHelmConnector._remove_multiple_spaces(command)
        self.debug('Executing async local command: {}'.format(command))

        # split command
        command = command.split(sep=' ')

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            # wait for command terminate
            stdout, stderr = await process.communicate()

            return_code = process.returncode

            output = ''
            if stdout:
                output = stdout.decode('utf-8').strip()
            if stderr:
                output = stderr.decode('utf-8').strip()

            if return_code != 0 and show_error_log:
                self.debug('Return code (FAIL): {}\nOutput:\n{}'.format(return_code, output))
            else:
                self.debug('Return code: {}'.format(return_code))

            if raise_exception_on_error and return_code != 0:
                raise Exception(output)

            return output, return_code

        except Exception as e:
            msg = 'Exception executing command: {} -> {}'.format(command, e)
            if show_error_log:
                self.error(msg)
            return '', -1

    def _remote_exec(
            self,
            hostname: str,
            username: str,
            password: str,
            command: str,
            timeout: int = 10
    ) -> (str, int):

        command = K8sHelmConnector._remove_multiple_spaces(command)
        self.debug('Executing sync remote ssh command: {}'.format(command))

        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(hostname=hostname, username=username, password=password)
        ssh_stdin, ssh_stdout, ssh_stderr = ssh.exec_command(command=command, timeout=timeout)
        output = ssh_stdout.read().decode('utf-8')
        error = ssh_stderr.read().decode('utf-8')
        if error:
            self.error('ERROR: {}'.format(error))
            return_code = 1
        else:
            return_code = 0
        output = output.replace('\\n', '\n')
        self.debug('OUTPUT: {}'.format(output))

        return output, return_code

    def _check_file_exists(self, filename: str, exception_if_not_exists: bool = False):
        self.debug('Checking if file {} exists...'.format(filename))
        if os.path.exists(filename):
            return True
        else:
            msg = 'File {} does not exist'.format(filename)
            if exception_if_not_exists:
                self.error(msg)
                raise Exception(msg)

