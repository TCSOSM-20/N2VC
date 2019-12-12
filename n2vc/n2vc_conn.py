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


import abc
import asyncio
import os
import subprocess
import shlex
import time
from enum import Enum
from http import HTTPStatus
from n2vc.loggable import Loggable
from n2vc.exceptions import N2VCBadArgumentsException
import yaml

from osm_common.dbmongo import DbException


class N2VCDeploymentStatus(Enum):
    PENDING = 'pending'
    RUNNING = 'running'
    COMPLETED = 'completed'
    FAILED = 'failed'
    UNKNOWN = 'unknown'


class N2VCConnector(abc.ABC, Loggable):
    """Generic N2VC connector

    Abstract class
    """

    """
    ##################################################################################################
    ########################################## P U B L I C ###########################################
    ##################################################################################################
    """

    def __init__(
            self,
            db: object,
            fs: object,
            log: object,
            loop: object,
            url: str,
            username: str,
            vca_config: dict,
            on_update_db=None
    ):
        """Initialize N2VC abstract connector. It defines de API for VCA connectors

        :param object db: Mongo object managing the MongoDB (repo common DbBase)
        :param object fs: FileSystem object managing the package artifacts (repo common FsBase)
        :param object log: the logging object to log to
        :param object loop: the loop to use for asyncio (default current thread loop)
        :param str url: a string that how to connect to the VCA (if needed, IP and port can be obtained from there)
        :param str username: the username to authenticate with VCA
        :param dict vca_config: Additional parameters for the specific VCA. For example, for juju it will contain:
            secret: The password to authenticate with
            public_key: The contents of the juju public SSH key
            ca_cert str: The CA certificate used to authenticate
        :param on_update_db: callback called when n2vc connector updates database. Received arguments:
            table: e.g. "nsrs"
            filter: e.g. {_id: <nsd-id> }
            path: e.g. "_admin.deployed.VCA.3."
            updated_data: e.g. , "{ _admin.deployed.VCA.3.status: 'xxx', etc }"
        """

        # parent class
        Loggable.__init__(self, log=log, log_to_console=True, prefix='\nN2VC')

        # check arguments
        if db is None:
            raise N2VCBadArgumentsException('Argument db is mandatory', ['db'])
        if fs is None:
            raise N2VCBadArgumentsException('Argument fs is mandatory', ['fs'])

        self.info('url={}, username={}, vca_config={}'.format(url, username, vca_config))

        # store arguments into self
        self.db = db
        self.fs = fs
        self.loop = loop or asyncio.get_event_loop()
        self.url = url
        self.username = username
        self.vca_config = vca_config
        self.on_update_db = on_update_db

        # generate private/public key-pair
        self.private_key_path = None
        self.public_key_path = None
        self.get_public_key()

    @abc.abstractmethod
    async def get_status(self, namespace: str, yaml_format: bool = True):
        """Get namespace status

        :param namespace: we obtain ns from namespace
        :param yaml_format: returns a yaml string
        """

    # TODO: review which public key
    def get_public_key(self) -> str:
        """Get the VCA ssh-public-key

        Returns the SSH public key from local mahine, to be injected into virtual machines to
        be managed by the VCA.
        First run, a ssh keypair will be created.
        The public key is injected into a VM so that we can provision the
        machine with Juju, after which Juju will communicate with the VM 
        directly via the juju agent.
        """

        public_key = ''

        # Find the path where we expect our key lives (~/.ssh)
        homedir = os.environ.get('HOME')
        if not homedir:
            self.warning('No HOME environment variable, using /tmp')
            homedir = '/tmp'
        sshdir = "{}/.ssh".format(homedir)
        if not os.path.exists(sshdir):
            os.mkdir(sshdir)

        self.private_key_path = "{}/id_n2vc_rsa".format(sshdir)
        self.public_key_path = "{}.pub".format(self.private_key_path)

        # If we don't have a key generated, then we have to generate it using ssh-keygen
        if not os.path.exists(self.private_key_path):
            cmd = "ssh-keygen -t {} -b {} -N '' -f {}".format(
                "rsa",
                "4096",
                self.private_key_path
            )
            # run command with arguments
            subprocess.check_output(shlex.split(cmd))

        # Read the public key. Only one public key (one line) in the file
        with open(self.public_key_path, "r") as file:
            public_key = file.readline()

        return public_key

    @abc.abstractmethod
    async def create_execution_environment(
        self,
        namespace: str,
        db_dict: dict,
        reuse_ee_id: str = None,
        progress_timeout: float = None,
        total_timeout: float = None
    ) -> (str, dict):
        """Create an Execution Environment. Returns when it is created or raises an exception on failing

        :param str namespace: Contains a dot separate string.
                    LCM will use: [<nsi-id>].<ns-id>.<vnf-id>.<vdu-id>[-<count>]
        :param dict db_dict: where to write to database when the status changes.
            It contains a dictionary with {collection: str, filter: {},  path: str},
                e.g. {collection: "nsrs", filter: {_id: <nsd-id>, path: "_admin.deployed.VCA.3"}
        :param str reuse_ee_id: ee id from an older execution. It allows us to reuse an older environment
        :param float progress_timeout:
        :param float total_timeout:
        :returns str, dict: id of the new execution environment and credentials for it
                    (credentials can contains hostname, username, etc depending on underlying cloud)
        """

    @abc.abstractmethod
    async def register_execution_environment(
        self,
        namespace: str,
        credentials: dict,
        db_dict: dict,
        progress_timeout: float = None,
        total_timeout: float = None
    ) -> str:
        """
        Register an existing execution environment at the VCA

        :param str namespace: same as create_execution_environment method
        :param dict credentials: credentials to access the existing execution environment
                    (it can contains hostname, username, path to private key, etc depending on underlying cloud)
        :param dict db_dict: where to write to database when the status changes.
            It contains a dictionary with {collection: str, filter: {},  path: str},
                e.g. {collection: "nsrs", filter: {_id: <nsd-id>, path: "_admin.deployed.VCA.3"}
        :param float progress_timeout:
        :param float total_timeout:
        :returns str: id of the execution environment
        """

    @abc.abstractmethod
    async def install_configuration_sw(
        self,
        ee_id: str,
        artifact_path: str,
        db_dict: dict,
        progress_timeout: float = None,
        total_timeout: float = None
    ):
        """
        Install the software inside the execution environment identified by ee_id

        :param str ee_id: the id of the execution environment returned by create_execution_environment
                        or register_execution_environment
        :param str artifact_path: where to locate the artifacts (parent folder) using the self.fs
            the final artifact path will be a combination of this artifact_path and additional string from
            the config_dict (e.g. charm name)
        :param dict db_dict: where to write into database when the status changes.
                        It contains a dict with {collection: <str>, filter: {},  path: <str>},
                            e.g. {collection: "nsrs", filter: {_id: <nsd-id>, path: "_admin.deployed.VCA.3"}
        :param float progress_timeout:
        :param float total_timeout:
        """

    @abc.abstractmethod
    async def get_ee_ssh_public__key(
        self,
        ee_id: str,
        db_dict: dict,
        progress_timeout: float = None,
        total_timeout: float = None
    ) -> str:
        """
        Generate a priv/pub key pair in the execution environment and return the public key

        :param str ee_id: the id of the execution environment returned by create_execution_environment
                        or register_execution_environment
        :param dict db_dict: where to write into database when the status changes.
                        It contains a dict with {collection: <str>, filter: {},  path: <str>},
                            e.g. {collection: "nsrs", filter: {_id: <nsd-id>, path: "_admin.deployed.VCA.3"}
        :param float progress_timeout:
        :param float total_timeout:
        :returns: public key of the execution environment
                    For the case of juju proxy charm ssh-layered, it is the one returned by 'get-ssh-public-key'
                    primitive.
                    It raises a N2VC exception if fails
        """

    @abc.abstractmethod
    async def add_relation(
        self,
        ee_id_1: str,
        ee_id_2: str,
        endpoint_1: str,
        endpoint_2: str
    ):
        """
        Add a relation between two Execution Environments (using their associated endpoints).

        :param str ee_id_1: The id of the first execution environment
        :param str ee_id_2: The id of the second execution environment
        :param str endpoint_1: The endpoint in the first execution environment
        :param str endpoint_2: The endpoint in the second execution environment
        """

    # TODO
    @abc.abstractmethod
    async def remove_relation(
        self
    ):
        """
        """

    # TODO
    @abc.abstractmethod
    async def deregister_execution_environments(
        self
    ):
        """
        """

    @abc.abstractmethod
    async def delete_namespace(
        self,
        namespace: str,
        db_dict: dict = None,
        total_timeout: float = None
    ):
        """
        Remove a network scenario and its execution environments
        :param namespace: [<nsi-id>].<ns-id>
        :param dict db_dict: where to write into database when the status changes.
                        It contains a dict with {collection: <str>, filter: {},  path: <str>},
                            e.g. {collection: "nsrs", filter: {_id: <nsd-id>, path: "_admin.deployed.VCA.3"}
        :param float total_timeout:
        """

    @abc.abstractmethod
    async def delete_execution_environment(
        self,
        ee_id: str,
        db_dict: dict = None,
        total_timeout: float = None
    ):
        """
        Delete an execution environment
        :param str ee_id: id of the execution environment to delete
        :param dict db_dict: where to write into database when the status changes.
                        It contains a dict with {collection: <str>, filter: {},  path: <str>},
                            e.g. {collection: "nsrs", filter: {_id: <nsd-id>, path: "_admin.deployed.VCA.3"}
        :param float total_timeout:
        """

    @abc.abstractmethod
    async def exec_primitive(
        self,
        ee_id: str,
        primitive_name: str,
        params_dict: dict,
        db_dict: dict = None,
        progress_timeout: float = None,
        total_timeout: float = None
    ) -> str:
        """
        Execute a primitive in the execution environment

        :param str ee_id: the one returned by create_execution_environment or register_execution_environment
        :param str primitive_name: must be one defined in the software. There is one called 'config',
            where, for the proxy case, the 'credentials' of VM are provided
        :param dict params_dict: parameters of the action
        :param dict db_dict: where to write into database when the status changes.
                        It contains a dict with {collection: <str>, filter: {},  path: <str>},
                            e.g. {collection: "nsrs", filter: {_id: <nsd-id>, path: "_admin.deployed.VCA.3"}
        :param float progress_timeout:
        :param float total_timeout:
        :returns str: primitive result, if ok. It raises exceptions in case of fail
        """

    async def disconnect(self):
        """
        Disconnect from VCA
        """

    """
    ##################################################################################################
    ########################################## P R I V A T E #########################################
    ##################################################################################################
    """

    def _get_namespace_components(self, namespace: str) -> (str, str, str, str, str):
        """
        Split namespace components

        :param namespace: [<nsi-id>].<ns-id>.<vnf-id>.<vdu-id>[-<count>]
        :return: nsi_id, ns_id, vnf_id, vdu_id, vdu_count
        """

        # check parameters
        if namespace is None or len(namespace) == 0:
            raise N2VCBadArgumentsException('Argument namespace is mandatory', ['namespace'])

        # split namespace components
        parts = namespace.split('.')
        nsi_id = None
        ns_id = None
        vnf_id = None
        vdu_id = None
        vdu_count = None
        if len(parts) > 0 and len(parts[0]) > 0:
            nsi_id = parts[0]
        if len(parts) > 1 and len(parts[1]) > 0:
            ns_id = parts[1]
        if len(parts) > 2 and len(parts[2]) > 0:
            vnf_id = parts[2]
        if len(parts) > 3 and len(parts[3]) > 0:
            vdu_id = parts[3]
            vdu_parts = parts[3].split('-')
            if len(vdu_parts) > 1:
                vdu_id = vdu_parts[0]
                vdu_count = vdu_parts[1]

        return nsi_id, ns_id, vnf_id, vdu_id, vdu_count

    async def write_app_status_to_db(
            self,
            db_dict: dict,
            status: N2VCDeploymentStatus,
            detailed_status: str,
            vca_status: str,
            entity_type: str
    ):
        if not db_dict:
            self.debug('No db_dict => No database write')
            return

        # self.debug('status={} / detailed-status={} / VCA-status={} / entity_type={}'
        #            .format(str(status.value), detailed_status, vca_status, entity_type))

        try:

            the_table = db_dict['collection']
            the_filter = db_dict['filter']
            the_path = db_dict['path']
            if not the_path[-1] == '.':
                the_path = the_path + '.'
            update_dict = {
                the_path + 'status': str(status.value),
                the_path + 'detailed-status': detailed_status,
                the_path + 'VCA-status': vca_status,
                the_path + 'entity-type': entity_type,
                the_path + 'status-time': str(time.time()),
            }

            self.db.set_one(
                table=the_table,
                q_filter=the_filter,
                update_dict=update_dict,
                fail_on_empty=True
            )

            # database callback
            if self.on_update_db:
                if asyncio.iscoroutinefunction(self.on_update_db):
                    await self.on_update_db(the_table, the_filter, the_path, update_dict)
                else:
                    self.on_update_db(the_table, the_filter, the_path, update_dict)

        except DbException as e:
            if e.http_code == HTTPStatus.NOT_FOUND:
                self.error('NOT_FOUND error: Exception writing status to database: {}'.format(e))
            else:
                self.info('Exception writing status to database: {}'.format(e))


def juju_status_2_osm_status(type: str, status: str) -> N2VCDeploymentStatus:
    if type == 'application' or type == 'unit':
        if status in ['waiting', 'maintenance']:
            return N2VCDeploymentStatus.RUNNING
        if status in ['error']:
                return N2VCDeploymentStatus.FAILED
        elif status in ['active']:
            return N2VCDeploymentStatus.COMPLETED
        elif status in ['blocked']:
            return N2VCDeploymentStatus.RUNNING
        else:
            return N2VCDeploymentStatus.UNKNOWN
    elif type == 'action':
        if status in ['running']:
            return N2VCDeploymentStatus.RUNNING
        elif status in ['completed']:
            return N2VCDeploymentStatus.COMPLETED
        else:
            return N2VCDeploymentStatus.UNKNOWN
    elif type == 'machine':
        if status in ['pending']:
            return N2VCDeploymentStatus.PENDING
        elif status in ['started']:
            return N2VCDeploymentStatus.COMPLETED
        else:
            return N2VCDeploymentStatus.UNKNOWN

    return N2VCDeploymentStatus.FAILED


def obj_to_yaml(obj: object) -> str:
    # dump to yaml
    dump_text = yaml.dump(obj, default_flow_style=False, indent=2)
    # split lines
    lines = dump_text.splitlines()
    # remove !!python/object tags
    yaml_text = ''
    for line in lines:
        index = line.find('!!python/object')
        if index >= 0:
            line = line[:index]
        yaml_text += line + '\n'
    return yaml_text


def obj_to_dict(obj: object) -> dict:
    # convert obj to yaml
    yaml_text = obj_to_yaml(obj)
    # parse to dict
    return yaml.load(yaml_text, Loader=yaml.Loader)
