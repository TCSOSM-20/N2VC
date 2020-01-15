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

import logging
import os
import asyncio
import time
import base64
import binascii
import re

from n2vc.n2vc_conn import N2VCConnector
from n2vc.n2vc_conn import obj_to_dict, obj_to_yaml
from n2vc.exceptions \
    import N2VCBadArgumentsException, N2VCException, N2VCConnectionException, \
    N2VCExecutionException, N2VCInvalidCertificate
from n2vc.juju_observer import JujuModelObserver

from juju.controller import Controller
from juju.model import Model
from juju.application import Application
from juju.action import Action
from juju.machine import Machine
from juju.client import client

from n2vc.provisioner import SSHProvisioner


class N2VCJujuConnector(N2VCConnector):

    """
    ##################################################################################################
    ########################################## P U B L I C ###########################################
    ##################################################################################################
    """

    def __init__(
        self,
        db: object,
        fs: object,
        log: object = None,
        loop: object = None,
        url: str = '127.0.0.1:17070',
        username: str = 'admin',
        vca_config: dict = None,
        on_update_db=None
    ):
        """Initialize juju N2VC connector
        """

        # parent class constructor
        N2VCConnector.__init__(
            self,
            db=db,
            fs=fs,
            log=log,
            loop=loop,
            url=url,
            username=username,
            vca_config=vca_config,
            on_update_db=on_update_db
        )

        # silence websocket traffic log
        logging.getLogger('websockets.protocol').setLevel(logging.INFO)
        logging.getLogger('juju.client.connection').setLevel(logging.WARN)
        logging.getLogger('model').setLevel(logging.WARN)

        self.info('Initializing N2VC juju connector...')

        """
        ##############################################################
        # check arguments
        ##############################################################
        """

        # juju URL
        if url is None:
            raise N2VCBadArgumentsException('Argument url is mandatory', ['url'])
        url_parts = url.split(':')
        if len(url_parts) != 2:
            raise N2VCBadArgumentsException('Argument url: bad format (localhost:port) -> {}'.format(url), ['url'])
        self.hostname = url_parts[0]
        try:
            self.port = int(url_parts[1])
        except ValueError:
            raise N2VCBadArgumentsException('url port must be a number -> {}'.format(url), ['url'])

        # juju USERNAME
        if username is None:
            raise N2VCBadArgumentsException('Argument username is mandatory', ['username'])

        # juju CONFIGURATION
        if vca_config is None:
            raise N2VCBadArgumentsException('Argument vca_config is mandatory', ['vca_config'])

        if 'secret' in vca_config:
            self.secret = vca_config['secret']
        else:
            raise N2VCBadArgumentsException('Argument vca_config.secret is mandatory', ['vca_config.secret'])

        # pubkey of juju client in osm machine: ~/.local/share/juju/ssh/juju_id_rsa.pub
        # if exists, it will be written in lcm container: _create_juju_public_key()
        if 'public_key' in vca_config:
            self.public_key = vca_config['public_key']
        else:
            self.public_key = None

        # TODO: Verify ca_cert is valid before using. VCA will crash
        # if the ca_cert isn't formatted correctly.
        def base64_to_cacert(b64string):
            """Convert the base64-encoded string containing the VCA CACERT.

            The input string....

            """
            try:
                cacert = base64.b64decode(b64string).decode("utf-8")

                cacert = re.sub(
                    r'\\n',
                    r'\n',
                    cacert,
                )
            except binascii.Error as e:
                self.debug("Caught binascii.Error: {}".format(e))
                raise N2VCInvalidCertificate(message="Invalid CA Certificate")

            return cacert

        self.ca_cert = vca_config.get('ca_cert')
        if self.ca_cert:
            self.ca_cert = base64_to_cacert(vca_config['ca_cert'])

        if 'api_proxy' in vca_config:
            self.api_proxy = vca_config['api_proxy']
            self.debug('api_proxy for native charms configured: {}'.format(self.api_proxy))
        else:
            self.warning('api_proxy is not configured. Support for native charms is disabled')

        if 'enable_os_upgrade' in vca_config:
            self.enable_os_upgrade = vca_config['enable_os_upgrade']
        else:
            self.enable_os_upgrade = True

        if 'apt_mirror' in vca_config:
            self.apt_mirror = vca_config['apt_mirror']
        else:
            self.apt_mirror = None

        self.debug('Arguments have been checked')

        # juju data
        self.controller = None         # it will be filled when connect to juju
        self.juju_models = {}          # model objects for every model_name
        self.juju_observers = {}       # model observers for every model_name
        self._connecting = False       # while connecting to juju (to avoid duplicate connections)
        self._authenticated = False    # it will be True when juju connection be stablished
        self._creating_model = False   # True during model creation

        # create juju pub key file in lcm container at ./local/share/juju/ssh/juju_id_rsa.pub
        self._create_juju_public_key()

        self.info('N2VC juju connector initialized')

    async def get_status(self, namespace: str, yaml_format: bool = True):

        # self.info('Getting NS status. namespace: {}'.format(namespace))

        if not self._authenticated:
            await self._juju_login()

        nsi_id, ns_id, vnf_id, vdu_id, vdu_count = self._get_namespace_components(namespace=namespace)
        # model name is ns_id
        model_name = ns_id
        if model_name is None:
            msg = 'Namespace {} not valid'.format(namespace)
            self.error(msg)
            raise N2VCBadArgumentsException(msg, ['namespace'])

        # get juju model (create model if needed)
        model = await self._juju_get_model(model_name=model_name)

        status = await model.get_status()

        if yaml_format:
            return obj_to_yaml(status)
        else:
            return obj_to_dict(status)

    async def create_execution_environment(
        self,
        namespace: str,
        db_dict: dict,
        reuse_ee_id: str = None,
        progress_timeout: float = None,
        total_timeout: float = None
    ) -> (str, dict):

        self.info('Creating execution environment. namespace: {}, reuse_ee_id: {}'.format(namespace, reuse_ee_id))

        if not self._authenticated:
            await self._juju_login()

        machine_id = None
        if reuse_ee_id:
            model_name, application_name, machine_id = self._get_ee_id_components(ee_id=reuse_ee_id)
        else:
            nsi_id, ns_id, vnf_id, vdu_id, vdu_count = self._get_namespace_components(namespace=namespace)
            # model name is ns_id
            model_name = ns_id
            # application name
            application_name = self._get_application_name(namespace=namespace)

        self.debug('model name: {}, application name:  {}, machine_id: {}'
                   .format(model_name, application_name, machine_id))

        # create or reuse a new juju machine
        try:
            machine = await self._juju_create_machine(
                model_name=model_name,
                application_name=application_name,
                machine_id=machine_id,
                db_dict=db_dict,
                progress_timeout=progress_timeout,
                total_timeout=total_timeout
            )
        except Exception as e:
            message = 'Error creating machine on juju: {}'.format(e)
            self.error(message)
            raise N2VCException(message=message)

        # id for the execution environment
        ee_id = N2VCJujuConnector._build_ee_id(
            model_name=model_name,
            application_name=application_name,
            machine_id=str(machine.entity_id)
        )
        self.debug('ee_id: {}'.format(ee_id))

        # new machine credentials
        credentials = dict()
        credentials['hostname'] = machine.dns_name

        self.info('Execution environment created. ee_id: {}, credentials: {}'.format(ee_id, credentials))

        return ee_id, credentials

    async def register_execution_environment(
        self,
        namespace: str,
        credentials: dict,
        db_dict: dict,
        progress_timeout: float = None,
        total_timeout: float = None
    ) -> str:

        if not self._authenticated:
            await self._juju_login()

        self.info('Registering execution environment. namespace={}, credentials={}'.format(namespace, credentials))

        if credentials is None:
            raise N2VCBadArgumentsException(message='credentials are mandatory', bad_args=['credentials'])
        if credentials.get('hostname'):
            hostname = credentials['hostname']
        else:
            raise N2VCBadArgumentsException(message='hostname is mandatory', bad_args=['credentials.hostname'])
        if credentials.get('username'):
            username = credentials['username']
        else:
            raise N2VCBadArgumentsException(message='username is mandatory', bad_args=['credentials.username'])
        if 'private_key_path' in credentials:
            private_key_path = credentials['private_key_path']
        else:
            # if not passed as argument, use generated private key path
            private_key_path = self.private_key_path

        nsi_id, ns_id, vnf_id, vdu_id, vdu_count = self._get_namespace_components(namespace=namespace)

        # model name
        model_name = ns_id
        # application name
        application_name = self._get_application_name(namespace=namespace)

        # register machine on juju
        try:
            machine_id = await self._juju_provision_machine(
                model_name=model_name,
                hostname=hostname,
                username=username,
                private_key_path=private_key_path,
                db_dict=db_dict,
                progress_timeout=progress_timeout,
                total_timeout=total_timeout
            )
        except Exception as e:
            self.error('Error registering machine: {}'.format(e))
            raise N2VCException(message='Error registering machine on juju: {}'.format(e))

        self.info('Machine registered: {}'.format(machine_id))

        # id for the execution environment
        ee_id = N2VCJujuConnector._build_ee_id(
            model_name=model_name,
            application_name=application_name,
            machine_id=str(machine_id)
        )

        self.info('Execution environment registered. ee_id: {}'.format(ee_id))

        return ee_id

    async def install_configuration_sw(
        self,
        ee_id: str,
        artifact_path: str,
        db_dict: dict,
        progress_timeout: float = None,
        total_timeout: float = None
    ):

        self.info('Installing configuration sw on ee_id: {}, artifact path: {}, db_dict: {}'
                  .format(ee_id, artifact_path, db_dict))

        if not self._authenticated:
            await self._juju_login()

        # check arguments
        if ee_id is None or len(ee_id) == 0:
            raise N2VCBadArgumentsException(message='ee_id is mandatory', bad_args=['ee_id'])
        if artifact_path is None or len(artifact_path) == 0:
            raise N2VCBadArgumentsException(message='artifact_path is mandatory', bad_args=['artifact_path'])
        if db_dict is None:
            raise N2VCBadArgumentsException(message='db_dict is mandatory', bad_args=['db_dict'])

        try:
            model_name, application_name, machine_id = N2VCJujuConnector._get_ee_id_components(ee_id=ee_id)
            self.debug('model: {}, application: {}, machine: {}'.format(model_name, application_name, machine_id))
        except Exception as e:
            raise N2VCBadArgumentsException(
                message='ee_id={} is not a valid execution environment id'.format(ee_id),
                bad_args=['ee_id']
            )

        # remove // in charm path
        while artifact_path.find('//') >= 0:
            artifact_path = artifact_path.replace('//', '/')

        # check charm path
        if not self.fs.file_exists(artifact_path, mode="dir"):
            msg = 'artifact path does not exist: {}'.format(artifact_path)
            raise N2VCBadArgumentsException(message=msg, bad_args=['artifact_path'])

        if artifact_path.startswith('/'):
            full_path = self.fs.path + artifact_path
        else:
            full_path = self.fs.path + '/' + artifact_path

        try:
            application, retries = await self._juju_deploy_charm(
                model_name=model_name,
                application_name=application_name,
                charm_path=full_path,
                machine_id=machine_id,
                db_dict=db_dict,
                progress_timeout=progress_timeout,
                total_timeout=total_timeout
            )
        except Exception as e:
            raise N2VCException(message='Error desploying charm into ee={} : {}'.format(ee_id, e))

        self.info('Configuration sw installed')

    async def get_ee_ssh_public__key(
        self,
        ee_id: str,
        db_dict: dict,
        progress_timeout: float = None,
        total_timeout: float = None
    ) -> str:

        self.info('Generating priv/pub key pair and get pub key on ee_id: {}, db_dict: {}'.format(ee_id, db_dict))

        if not self._authenticated:
            await self._juju_login()

        # check arguments
        if ee_id is None or len(ee_id) == 0:
            raise N2VCBadArgumentsException(message='ee_id is mandatory', bad_args=['ee_id'])
        if db_dict is None:
            raise N2VCBadArgumentsException(message='db_dict is mandatory', bad_args=['db_dict'])

        try:
            model_name, application_name, machine_id = N2VCJujuConnector._get_ee_id_components(ee_id=ee_id)
            self.debug('model: {}, application: {}, machine: {}'.format(model_name, application_name, machine_id))
        except Exception as e:
            raise N2VCBadArgumentsException(
                message='ee_id={} is not a valid execution environment id'.format(ee_id),
                bad_args=['ee_id']
            )

        # try to execute ssh layer primitives (if exist):
        #       generate-ssh-key
        #       get-ssh-public-key

        output = None

        # execute action: generate-ssh-key
        try:
            output, status = await self._juju_execute_action(
                model_name=model_name,
                application_name=application_name,
                action_name='generate-ssh-key',
                db_dict=db_dict,
                progress_timeout=progress_timeout,
                total_timeout=total_timeout
            )
        except Exception as e:
            self.info('Cannot execute action generate-ssh-key: {}\nContinuing...'.format(e))

        # execute action: get-ssh-public-key
        try:
            output, status = await self._juju_execute_action(
                model_name=model_name,
                application_name=application_name,
                action_name='get-ssh-public-key',
                db_dict=db_dict,
                progress_timeout=progress_timeout,
                total_timeout=total_timeout
            )
        except Exception as e:
            msg = 'Cannot execute action get-ssh-public-key: {}\n'.format(e)
            self.info(msg)
            raise e

        # return public key if exists
        return output["pubkey"] if "pubkey" in output else output

    async def add_relation(
        self,
        ee_id_1: str,
        ee_id_2: str,
        endpoint_1: str,
        endpoint_2: str
    ):

        self.debug('adding new relation between {} and {}, endpoints: {}, {}'
                   .format(ee_id_1, ee_id_2, endpoint_1, endpoint_2))

        if not self._authenticated:
            await self._juju_login()

        # get model, application and machines
        model_1, app_1, machine_1 = self._get_ee_id_components(ee_id_1)
        model_2, app_2, machine_2 = self._get_ee_id_components(ee_id_2)

        # model must be the same
        if model_1 != model_2:
            message = 'EE models are not the same: {} vs {}'.format(ee_id_1, ee_id_2)
            self.error(message)
            raise N2VCBadArgumentsException(message=message, bad_args=['ee_id_1', 'ee_id_2'])

        # add juju relations between two applications
        try:
            self._juju_add_relation()
        except Exception as e:
            message = 'Error adding relation between {} and {}'.format(ee_id_1, ee_id_2)
            self.error(message)
            raise N2VCException(message=message)

    async def remove_relation(
        self
    ):
        if not self._authenticated:
            await self._juju_login()
        # TODO
        self.info('Method not implemented yet')
        raise NotImplemented()

    async def deregister_execution_environments(
        self
    ):
        if not self._authenticated:
            await self._juju_login()
        # TODO
        self.info('Method not implemented yet')
        raise NotImplemented()

    async def delete_namespace(
        self,
        namespace: str,
        db_dict: dict = None,
        total_timeout: float = None
    ):
        self.info('Deleting namespace={}'.format(namespace))

        if not self._authenticated:
            await self._juju_login()

        # check arguments
        if namespace is None:
            raise N2VCBadArgumentsException(message='namespace is mandatory', bad_args=['namespace'])

        nsi_id, ns_id, vnf_id, vdu_id, vdu_count = self._get_namespace_components(namespace=namespace)
        if ns_id is not None:
            try:
                await self._juju_destroy_model(
                    model_name=ns_id,
                    total_timeout=total_timeout
                )
            except Exception as e:
                raise N2VCException(message='Error deleting namespace {} : {}'.format(namespace, e))
        else:
            raise N2VCBadArgumentsException(message='only ns_id is permitted to delete yet', bad_args=['namespace'])

        self.info('Namespace {} deleted'.format(namespace))

    async def delete_execution_environment(
        self,
        ee_id: str,
        db_dict: dict = None,
        total_timeout: float = None
    ):
        self.info('Deleting execution environment ee_id={}'.format(ee_id))

        if not self._authenticated:
            await self._juju_login()

        # check arguments
        if ee_id is None:
            raise N2VCBadArgumentsException(message='ee_id is mandatory', bad_args=['ee_id'])

        model_name, application_name, machine_id = self._get_ee_id_components(ee_id=ee_id)

        # destroy the application
        try:
            await self._juju_destroy_application(model_name=model_name, application_name=application_name)
        except Exception as e:
            raise N2VCException(message='Error deleting execution environment {} (application {}) : {}'
                                .format(ee_id, application_name, e))

        # destroy the machine
        try:
            await self._juju_destroy_machine(
                model_name=model_name,
                machine_id=machine_id,
                total_timeout=total_timeout
            )
        except Exception as e:
            raise N2VCException(message='Error deleting execution environment {} (machine {}) : {}'
                                .format(ee_id, machine_id, e))

        self.info('Execution environment {} deleted'.format(ee_id))

    async def exec_primitive(
            self,
            ee_id: str,
            primitive_name: str,
            params_dict: dict,
            db_dict: dict = None,
            progress_timeout: float = None,
            total_timeout: float = None
    ) -> str:

        self.info('Executing primitive: {} on ee: {}, params: {}'.format(primitive_name, ee_id, params_dict))

        if not self._authenticated:
            await self._juju_login()

        # check arguments
        if ee_id is None or len(ee_id) == 0:
            raise N2VCBadArgumentsException(message='ee_id is mandatory', bad_args=['ee_id'])
        if primitive_name is None or len(primitive_name) == 0:
            raise N2VCBadArgumentsException(message='action_name is mandatory', bad_args=['action_name'])
        if params_dict is None:
            params_dict = dict()

        try:
            model_name, application_name, machine_id = N2VCJujuConnector._get_ee_id_components(ee_id=ee_id)
        except Exception:
            raise N2VCBadArgumentsException(
                message='ee_id={} is not a valid execution environment id'.format(ee_id),
                bad_args=['ee_id']
            )

        if primitive_name == 'config':
            # Special case: config primitive
            try:
                await self._juju_configure_application(
                    model_name=model_name,
                    application_name=application_name,
                    config=params_dict,
                    db_dict=db_dict,
                    progress_timeout=progress_timeout,
                    total_timeout=total_timeout
                )
            except Exception as e:
                self.error('Error configuring juju application: {}'.format(e))
                raise N2VCExecutionException(
                    message='Error configuring application into ee={} : {}'.format(ee_id, e),
                    primitive_name=primitive_name
                )
            return 'CONFIG OK'
        else:
            try:
                output, status = await self._juju_execute_action(
                    model_name=model_name,
                    application_name=application_name,
                    action_name=primitive_name,
                    db_dict=db_dict,
                    progress_timeout=progress_timeout,
                    total_timeout=total_timeout,
                    **params_dict
                )
                if status == 'completed':
                    return output
                else:
                    raise Exception('status is not completed: {}'.format(status))
            except Exception as e:
                self.error('Error executing primitive {}: {}'.format(primitive_name, e))
                raise N2VCExecutionException(
                    message='Error executing primitive {} into ee={} : {}'.format(primitive_name, ee_id, e),
                    primitive_name=primitive_name
                )

    async def disconnect(self):
        self.info('closing juju N2VC...')
        await self._juju_logout()

    """
    ##################################################################################################
    ########################################## P R I V A T E #########################################
    ##################################################################################################
    """

    def _write_ee_id_db(
            self,
            db_dict: dict,
            ee_id: str
    ):

        # write ee_id to database: _admin.deployed.VCA.x
        try:
            the_table = db_dict['collection']
            the_filter = db_dict['filter']
            the_path = db_dict['path']
            if not the_path[-1] == '.':
                the_path = the_path + '.'
            update_dict = {the_path + 'ee_id': ee_id}
            # self.debug('Writing ee_id to database: {}'.format(the_path))
            self.db.set_one(
                table=the_table,
                q_filter=the_filter,
                update_dict=update_dict,
                fail_on_empty=True
            )
        except Exception as e:
            self.error('Error writing ee_id to database: {}'.format(e))

    @staticmethod
    def _build_ee_id(
            model_name: str,
            application_name: str,
            machine_id: str
    ):
        """
        Build an execution environment id form model, application and machine
        :param model_name:
        :param application_name:
        :param machine_id:
        :return:
        """
        # id for the execution environment
        return '{}.{}.{}'.format(model_name, application_name, machine_id)

    @staticmethod
    def _get_ee_id_components(
            ee_id: str
    ) -> (str, str, str):
        """
        Get model, application and machine components from an execution environment id
        :param ee_id:
        :return: model_name, application_name, machine_id
        """

        if ee_id is None:
            return None, None, None

        # split components of id
        parts = ee_id.split('.')
        model_name = parts[0]
        application_name = parts[1]
        machine_id = parts[2]
        return model_name, application_name, machine_id

    def _get_application_name(self, namespace: str) -> str:
        """
        Build application name from namespace
        :param namespace:
        :return: app-vnf-<vnf id>-vdu-<vdu-id>-cnt-<vdu-count>
        """

        # TODO: Enforce the Juju 50-character application limit

        # split namespace components
        _, _, vnf_id, vdu_id, vdu_count = self._get_namespace_components(namespace=namespace)

        if vnf_id is None or len(vnf_id) == 0:
            vnf_id = ''
        else:
            # Shorten the vnf_id to its last twelve characters
            vnf_id = 'vnf-' + vnf_id[-12:]

        if vdu_id is None or len(vdu_id) == 0:
            vdu_id = ''
        else:
            # Shorten the vdu_id to its last twelve characters
            vdu_id = '-vdu-' + vdu_id[-12:]

        if vdu_count is None or len(vdu_count) == 0:
            vdu_count = ''
        else:
            vdu_count = '-cnt-' + vdu_count

        application_name = 'app-{}{}{}'.format(vnf_id, vdu_id, vdu_count)

        return N2VCJujuConnector._format_app_name(application_name)

    async def _juju_create_machine(
            self,
            model_name: str,
            application_name: str,
            machine_id: str = None,
            db_dict: dict = None,
            progress_timeout: float = None,
            total_timeout: float = None
    ) -> Machine:

        self.debug('creating machine in model: {}, existing machine id: {}'.format(model_name, machine_id))

        # get juju model and observer (create model if needed)
        model = await self._juju_get_model(model_name=model_name)
        observer = self.juju_observers[model_name]

        # find machine id in model
        machine = None
        if machine_id is not None:
            self.debug('Finding existing machine id {} in model'.format(machine_id))
            # get juju existing machines in the model
            existing_machines = await model.get_machines()
            if machine_id in existing_machines:
                self.debug('Machine id {} found in model (reusing it)'.format(machine_id))
                machine = model.machines[machine_id]

        if machine is None:
            self.debug('Creating a new machine in juju...')
            # machine does not exist, create it and wait for it
            machine = await model.add_machine(
                spec=None,
                constraints=None,
                disks=None,
                series='xenial'
            )

            # register machine with observer
            observer.register_machine(machine=machine, db_dict=db_dict)

            # id for the execution environment
            ee_id = N2VCJujuConnector._build_ee_id(
                model_name=model_name,
                application_name=application_name,
                machine_id=str(machine.entity_id)
            )

            # write ee_id in database
            self._write_ee_id_db(
                db_dict=db_dict,
                ee_id=ee_id
            )

            # wait for machine creation
            await observer.wait_for_machine(
                machine_id=str(machine.entity_id),
                progress_timeout=progress_timeout,
                total_timeout=total_timeout
            )

        else:

            self.debug('Reusing old machine pending')

            # register machine with observer
            observer.register_machine(machine=machine, db_dict=db_dict)

            # machine does exist, but it is in creation process (pending), wait for create finalisation
            await observer.wait_for_machine(
                machine_id=machine.entity_id,
                progress_timeout=progress_timeout,
                total_timeout=total_timeout)

        self.debug("Machine ready at " + str(machine.dns_name))
        return machine

    async def _juju_provision_machine(
            self,
            model_name: str,
            hostname: str,
            username: str,
            private_key_path: str,
            db_dict: dict = None,
            progress_timeout: float = None,
            total_timeout: float = None
    ) -> str:

        if not self.api_proxy:
            msg = 'Cannot provision machine: api_proxy is not defined'
            self.error(msg=msg)
            raise N2VCException(message=msg)

        self.debug('provisioning machine. model: {}, hostname: {}, username: {}'.format(model_name, hostname, username))

        if not self._authenticated:
            await self._juju_login()

        # get juju model and observer
        model = await self._juju_get_model(model_name=model_name)
        observer = self.juju_observers[model_name]

        # TODO check if machine is already provisioned
        machine_list = await model.get_machines()

        provisioner = SSHProvisioner(
            host=hostname,
            user=username,
            private_key_path=private_key_path,
            log=self.log
        )

        params = None
        try:
            params = provisioner.provision_machine()
        except Exception as ex:
            msg = "Exception provisioning machine: {}".format(ex)
            self.log.error(msg)
            raise N2VCException(message=msg)

        params.jobs = ['JobHostUnits']

        connection = model.connection()

        # Submit the request.
        self.debug("Adding machine to model")
        client_facade = client.ClientFacade.from_connection(connection)
        results = await client_facade.AddMachines(params=[params])
        error = results.machines[0].error
        if error:
            msg = "Error adding machine: {}}".format(error.message)
            self.error(msg=msg)
            raise ValueError(msg)

        machine_id = results.machines[0].machine

        # Need to run this after AddMachines has been called,
        # as we need the machine_id
        self.debug("Installing Juju agent into machine {}".format(machine_id))
        asyncio.ensure_future(provisioner.install_agent(
            connection=connection,
            nonce=params.nonce,
            machine_id=machine_id,
            api=self.api_proxy,
        ))

        # wait for machine in model (now, machine is not yet in model, so we must wait for it)
        machine = None
        for i in range(10):
            machine_list = await model.get_machines()
            if machine_id in machine_list:
                self.debug('Machine {} found in model!'.format(machine_id))
                machine = model.machines.get(machine_id)
                break
            await asyncio.sleep(2)

        if machine is None:
            msg = 'Machine {} not found in model'.format(machine_id)
            self.error(msg=msg)
            raise Exception(msg)

        # register machine with observer
        observer.register_machine(machine=machine, db_dict=db_dict)

        # wait for machine creation
        self.debug('waiting for provision finishes... {}'.format(machine_id))
        await observer.wait_for_machine(
            machine_id=machine_id,
            progress_timeout=progress_timeout,
            total_timeout=total_timeout
        )

        self.debug("Machine provisioned {}".format(machine_id))

        return machine_id

    async def _juju_deploy_charm(
            self,
            model_name: str,
            application_name: str,
            charm_path: str,
            machine_id: str,
            db_dict: dict,
            progress_timeout: float = None,
            total_timeout: float = None
    ) -> (Application, int):

        # get juju model and observer
        model = await self._juju_get_model(model_name=model_name)
        observer = self.juju_observers[model_name]

        # check if application already exists
        application = None
        if application_name in model.applications:
            application = model.applications[application_name]

        if application is None:

            # application does not exist, create it and wait for it
            self.debug('deploying application {} to machine {}, model {}'
                       .format(application_name, machine_id, model_name))
            self.debug('charm: {}'.format(charm_path))
            series = 'xenial'
            # series = None
            application = await model.deploy(
                entity_url=charm_path,
                application_name=application_name,
                channel='stable',
                num_units=1,
                series=series,
                to=machine_id
            )

            # register application with observer
            observer.register_application(application=application, db_dict=db_dict)

            self.debug('waiting for application deployed... {}'.format(application.entity_id))
            retries = await observer.wait_for_application(
                application_id=application.entity_id,
                progress_timeout=progress_timeout,
                total_timeout=total_timeout)
            self.debug('application deployed')

        else:

            # register application with observer
            observer.register_application(application=application, db_dict=db_dict)

            # application already exists, but not finalised
            self.debug('application already exists, waiting for deployed...')
            retries = await observer.wait_for_application(
                application_id=application.entity_id,
                progress_timeout=progress_timeout,
                total_timeout=total_timeout)
            self.debug('application deployed')

        return application, retries

    async def _juju_execute_action(
            self,
            model_name: str,
            application_name: str,
            action_name: str,
            db_dict: dict,
            progress_timeout: float = None,
            total_timeout: float = None,
            **kwargs
    ) -> Action:

        # get juju model and observer
        model = await self._juju_get_model(model_name=model_name)
        observer = self.juju_observers[model_name]

        application = await self._juju_get_application(model_name=model_name, application_name=application_name)

        unit = application.units[0]
        if unit is not None:
            actions = await application.get_actions()
            if action_name in actions:
                self.debug('executing action "{}" using params: {}'.format(action_name, kwargs))
                action = await unit.run_action(action_name, **kwargs)

                # register action with observer
                observer.register_action(action=action, db_dict=db_dict)

                await observer.wait_for_action(
                    action_id=action.entity_id,
                    progress_timeout=progress_timeout,
                    total_timeout=total_timeout)
                self.debug('action completed with status: {}'.format(action.status))
                output = await model.get_action_output(action_uuid=action.entity_id)
                status = await model.get_action_status(uuid_or_prefix=action.entity_id)
                if action.entity_id in status:
                    status = status[action.entity_id]
                else:
                    status = 'failed'
                return output, status

        raise N2VCExecutionException(
            message='Cannot execute action on charm',
            primitive_name=action_name
        )

    async def _juju_configure_application(
            self,
            model_name: str,
            application_name: str,
            config: dict,
            db_dict: dict,
            progress_timeout: float = None,
            total_timeout: float = None
    ):

        # get the application
        application = await self._juju_get_application(model_name=model_name, application_name=application_name)

        self.debug('configuring the application {} -> {}'.format(application_name, config))
        res = await application.set_config(config)
        self.debug('application {} configured. res={}'.format(application_name, res))

        # Verify the config is set
        new_conf = await application.get_config()
        for key in config:
            value = new_conf[key]['value']
            self.debug('    {} = {}'.format(key, value))
            if config[key] != value:
                raise N2VCException(
                    message='key {} is not configured correctly {} != {}'.format(key, config[key], new_conf[key])
                )

        # check if 'verify-ssh-credentials' action exists
        # unit = application.units[0]
        actions = await application.get_actions()
        if 'verify-ssh-credentials' not in actions:
            msg = 'Action verify-ssh-credentials does not exist in application {}'.format(application_name)
            self.debug(msg=msg)
            return False

        # execute verify-credentials
        num_retries = 20
        retry_timeout = 15.0
        for i in range(num_retries):
            try:
                self.debug('Executing action verify-ssh-credentials...')
                output, ok = await self._juju_execute_action(
                    model_name=model_name,
                    application_name=application_name,
                    action_name='verify-ssh-credentials',
                    db_dict=db_dict,
                    progress_timeout=progress_timeout,
                    total_timeout=total_timeout
                )
                self.debug('Result: {}, output: {}'.format(ok, output))
                return True
            except Exception as e:
                self.debug('Error executing verify-ssh-credentials: {}. Retrying...'.format(e))
                await asyncio.sleep(retry_timeout)
        else:
            self.error('Error executing verify-ssh-credentials after {} retries. '.format(num_retries))
            return False

    async def _juju_get_application(
            self,
            model_name: str,
            application_name: str
    ):
        """Get the deployed application."""

        model = await self._juju_get_model(model_name=model_name)

        application_name = N2VCJujuConnector._format_app_name(application_name)

        if model.applications and application_name in model.applications:
            return model.applications[application_name]
        else:
            raise N2VCException(message='Cannot get application {} from model {}'.format(application_name, model_name))

    async def _juju_get_model(self, model_name: str) -> Model:
        """ Get a model object from juju controller
        If the model does not exits, it creates it.

        :param str model_name: name of the model
        :returns Model: model obtained from juju controller or Exception
        """

        # format model name
        model_name = N2VCJujuConnector._format_model_name(model_name)

        if model_name in self.juju_models:
            return self.juju_models[model_name]

        if self._creating_model:
            self.debug('Another coroutine is creating a model. Wait...')
        while self._creating_model:
            # another coroutine is creating a model, wait
            await asyncio.sleep(0.1)
            # retry (perhaps another coroutine has created the model meanwhile)
            if model_name in self.juju_models:
                return self.juju_models[model_name]

        try:
            self._creating_model = True

            # get juju model names from juju
            model_list = await self.controller.list_models()

            if model_name not in model_list:
                self.info('Model {} does not exist. Creating new model...'.format(model_name))
                config_dict = {'authorized-keys': self.public_key}
                if self.apt_mirror:
                    config_dict['apt-mirror'] = self.apt_mirror
                if not self.enable_os_upgrade:
                    config_dict['enable-os-refresh-update'] = False
                    config_dict['enable-os-upgrade'] = False

                model = await self.controller.add_model(
                    model_name=model_name,
                    config=config_dict
                )
                self.info('New model created, name={}'.format(model_name))
            else:
                self.debug('Model already exists in juju. Getting model {}'.format(model_name))
                model = await self.controller.get_model(model_name)
                self.debug('Existing model in juju, name={}'.format(model_name))

            self.juju_models[model_name] = model
            self.juju_observers[model_name] = JujuModelObserver(n2vc=self, model=model)
            return model

        except Exception as e:
            msg = 'Cannot get model {}. Exception: {}'.format(model_name, e)
            self.error(msg)
            raise N2VCException(msg)
        finally:
            self._creating_model = False

    async def _juju_add_relation(
            self,
            model_name: str,
            application_name_1: str,
            application_name_2: str,
            relation_1: str,
            relation_2: str
    ):

        self.debug('adding relation')

        # get juju model and observer
        model = await self._juju_get_model(model_name=model_name)

        r1 = '{}:{}'.format(application_name_1, relation_1)
        r2 = '{}:{}'.format(application_name_2, relation_2)
        await model.add_relation(relation1=r1, relation2=r2)

    async def _juju_destroy_application(
        self,
        model_name: str,
        application_name: str
    ):

        self.debug('Destroying application {} in model {}'.format(application_name, model_name))

        # get juju model and observer
        model = await self._juju_get_model(model_name=model_name)

        application = model.applications.get(application_name)
        if application:
            await application.destroy()
        else:
            self.debug('Application not found: {}'.format(application_name))

    async def _juju_destroy_machine(
        self,
        model_name: str,
        machine_id: str,
        total_timeout: float = None
    ):

        self.debug('Destroying machine {} in model {}'.format(machine_id, model_name))

        if total_timeout is None:
            total_timeout = 3600

        # get juju model and observer
        model = await self._juju_get_model(model_name=model_name)

        machines = await model.get_machines()
        if machine_id in machines:
            machine = model.machines[machine_id]
            await machine.destroy(force=True)
            # max timeout
            end = time.time() + total_timeout
            # wait for machine removal
            machines = await model.get_machines()
            while machine_id in machines and time.time() < end:
                self.debug('Waiting for machine {} is destroyed'.format(machine_id))
                await asyncio.sleep(0.5)
                machines = await model.get_machines()
            self.debug('Machine destroyed: {}'.format(machine_id))
        else:
            self.debug('Machine not found: {}'.format(machine_id))

    async def _juju_destroy_model(
            self,
            model_name: str,
            total_timeout: float = None
    ):

        self.debug('Destroying model {}'.format(model_name))

        if total_timeout is None:
            total_timeout = 3600

        model = await self._juju_get_model(model_name=model_name)
        uuid = model.info.uuid

        # destroy machines
        machines = await model.get_machines()
        for machine_id in machines:
            try:
                await self._juju_destroy_machine(model_name=model_name, machine_id=machine_id)
            except Exception as e:
                # ignore exceptions destroying machine
                pass

        await self._juju_disconnect_model(model_name=model_name)
        self.juju_models[model_name] = None
        self.juju_observers[model_name] = None

        self.debug('destroying model {}...'.format(model_name))
        await self.controller.destroy_model(uuid)
        self.debug('model destroy requested {}'.format(model_name))

        # wait for model is completely destroyed
        end = time.time() + total_timeout
        while time.time() < end:
            self.debug('Waiting for model is destroyed...')
            try:
                # await self.controller.get_model(uuid)
                models = await self.controller.list_models()
                if model_name not in models:
                    self.debug('The model {} ({}) was destroyed'.format(model_name, uuid))
                    return
            except Exception as e:
                pass
            await asyncio.sleep(1.0)

    async def _juju_login(self):
        """Connect to juju controller

        """

        # if already authenticated, exit function
        if self._authenticated:
            return

        # if connecting, wait for finish
        # another task could be trying to connect in parallel
        while self._connecting:
            await asyncio.sleep(0.1)

        # double check after other task has finished
        if self._authenticated:
            return

        try:
            self._connecting = True
            self.info(
                'connecting to juju controller: {} {}:{} ca_cert: {}'
                .format(self.url, self.username, self.secret, '\n'+self.ca_cert if self.ca_cert else 'None'))

            # Create controller object
            self.controller = Controller(loop=self.loop)
            # Connect to controller
            await self.controller.connect(
                endpoint=self.url,
                username=self.username,
                password=self.secret,
                cacert=self.ca_cert
            )
            self._authenticated = True
            self.info('juju controller connected')
        except Exception as e:
            message = 'Exception connecting to juju: {}'.format(e)
            self.error(message)
            raise N2VCConnectionException(
                message=message,
                url=self.url
            )
        finally:
            self._connecting = False

    async def _juju_logout(self):
        """Logout of the Juju controller."""
        if not self._authenticated:
            return False

        # disconnect all models
        for model_name in self.juju_models:
            try:
                await self._juju_disconnect_model(model_name)
            except Exception as e:
                self.error('Error disconnecting model {} : {}'.format(model_name, e))
                # continue with next model...

        self.info("Disconnecting controller")
        try:
            await self.controller.disconnect()
        except Exception as e:
            raise N2VCConnectionException(message='Error disconnecting controller: {}'.format(e), url=self.url)

        self.controller = None
        self._authenticated = False
        self.info('disconnected')

    async def _juju_disconnect_model(
        self,
        model_name: str
    ):
        self.debug("Disconnecting model {}".format(model_name))
        if model_name in self.juju_models:
            await self.juju_models[model_name].disconnect()
            self.juju_models[model_name] = None
            self.juju_observers[model_name] = None
        else:
            self.warning('Cannot disconnect model: {}'.format(model_name))

    def _create_juju_public_key(self):
        """Recreate the Juju public key on lcm container, if needed
        Certain libjuju commands expect to be run from the same machine as Juju
         is bootstrapped to. This method will write the public key to disk in
         that location: ~/.local/share/juju/ssh/juju_id_rsa.pub
        """

        # Make sure that we have a public key before writing to disk
        if self.public_key is None or len(self.public_key) == 0:
            if 'OSMLCM_VCA_PUBKEY' in os.environ:
                self.public_key = os.getenv('OSMLCM_VCA_PUBKEY', '')
                if len(self.public_key) == 0:
                    return
            else:
                return

        pk_path = "{}/.local/share/juju/ssh".format(os.path.expanduser('~'))
        file_path = "{}/juju_id_rsa.pub".format(pk_path)
        self.debug('writing juju public key to file:\n{}\npublic key: {}'.format(file_path, self.public_key))
        if not os.path.exists(pk_path):
            # create path and write file
            os.makedirs(pk_path)
            with open(file_path, 'w') as f:
                self.debug('Creating juju public key file: {}'.format(file_path))
                f.write(self.public_key)
        else:
            self.debug('juju public key file already exists: {}'.format(file_path))

    @staticmethod
    def _format_model_name(name: str) -> str:
        """Format the name of the model.

        Model names may only contain lowercase letters, digits and hyphens
        """

        return name.replace('_', '-').replace(' ', '-').lower()

    @staticmethod
    def _format_app_name(name: str) -> str:
        """Format the name of the application (in order to assure valid application name).

        Application names have restrictions (run juju deploy --help):
            - contains lowercase letters 'a'-'z'
            - contains numbers '0'-'9'
            - contains hyphens '-'
            - starts with a lowercase letter
            - not two or more consecutive hyphens
            - after a hyphen, not a group with all numbers
        """

        def all_numbers(s: str) -> bool:
            for c in s:
                if not c.isdigit():
                    return False
            return True

        new_name = name.replace('_', '-')
        new_name = new_name.replace(' ', '-')
        new_name = new_name.lower()
        while new_name.find('--') >= 0:
            new_name = new_name.replace('--', '-')
        groups = new_name.split('-')

        # find 'all numbers' groups and prefix them with a letter
        app_name = ''
        for i in range(len(groups)):
            group = groups[i]
            if all_numbers(group):
                group = 'z' + group
            if i > 0:
                app_name += '-'
            app_name += group

        if app_name[0].isdigit():
            app_name = 'z' + app_name

        return app_name
