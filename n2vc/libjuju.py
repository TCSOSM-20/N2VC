# Copyright 2020 Canonical Ltd.
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
import logging
from juju.controller import Controller
from juju.client import client
import time

from juju.errors import JujuAPIError
from juju.model import Model
from juju.machine import Machine
from juju.application import Application
from juju.client._definitions import FullStatus
from n2vc.juju_watcher import JujuModelWatcher
from n2vc.provisioner import AsyncSSHProvisioner
from n2vc.n2vc_conn import N2VCConnector
from n2vc.exceptions import (
    JujuMachineNotFound,
    JujuApplicationNotFound,
    JujuModelAlreadyExists,
    JujuControllerFailedConnecting,
    JujuApplicationExists,
)
from n2vc.utils import DB_DATA
from osm_common.dbbase import DbException


class Libjuju:
    def __init__(
        self,
        endpoint: str,
        api_proxy: str,
        username: str,
        password: str,
        cacert: str,
        loop: asyncio.AbstractEventLoop = None,
        log: logging.Logger = None,
        db: dict = None,
        n2vc: N2VCConnector = None,
        apt_mirror: str = None,
        enable_os_upgrade: bool = True,
    ):
        """
        Constructor

        :param: endpoint:               Endpoint of the juju controller (host:port)
        :param: api_proxy:              Endpoint of the juju controller - Reachable from the VNFs
        :param: username:               Juju username
        :param: password:               Juju password
        :param: cacert:                 Juju CA Certificate
        :param: loop:                   Asyncio loop
        :param: log:                    Logger
        :param: db:                     DB object
        :param: n2vc:                   N2VC object
        :param: apt_mirror:             APT Mirror
        :param: enable_os_upgrade:      Enable OS Upgrade
        """

        self.log = log or logging.getLogger("Libjuju")
        self.db = db
        self.endpoints = self._get_api_endpoints_db() or [endpoint]
        self.api_proxy = api_proxy
        self.username = username
        self.password = password
        self.cacert = cacert
        self.loop = loop or asyncio.get_event_loop()
        self.n2vc = n2vc

        # Generate config for models
        self.model_config = {}
        if apt_mirror:
            self.model_config["apt-mirror"] = apt_mirror
        self.model_config["enable-os-refresh-update"] = enable_os_upgrade
        self.model_config["enable-os-upgrade"] = enable_os_upgrade

        self.loop.set_exception_handler(self.handle_exception)
        self.creating_model = asyncio.Lock(loop=self.loop)

        self.models = set()
        self.log.debug("Libjuju initialized!")

        self.health_check_task = self.loop.create_task(self.health_check())

    async def get_controller(self, timeout: float = 5.0) -> Controller:
        """
        Get controller

        :param: timeout: Time in seconds to wait for controller to connect
        """
        controller = None
        try:
            controller = Controller(loop=self.loop)
            await asyncio.wait_for(
                controller.connect(
                    endpoint=self.endpoints,
                    username=self.username,
                    password=self.password,
                    cacert=self.cacert,
                ),
                timeout=timeout,
            )
            endpoints = await controller.api_endpoints
            if self.endpoints != endpoints:
                self.endpoints = endpoints
                self._update_api_endpoints_db(self.endpoints)
            return controller
        except asyncio.CancelledError as e:
            raise e
        except Exception as e:
            self.log.error(
                "Failed connecting to controller: {}...".format(self.endpoints)
            )
            if controller:
                await self.disconnect_controller(controller)
            raise JujuControllerFailedConnecting(e)

    async def disconnect(self):
        """Disconnect"""
        # Cancel health check task
        self.health_check_task.cancel()
        self.log.debug("Libjuju disconnected!")

    async def disconnect_model(self, model: Model):
        """
        Disconnect model

        :param: model: Model that will be disconnected
        """
        await model.disconnect()

    async def disconnect_controller(self, controller: Controller):
        """
        Disconnect controller

        :param: controller: Controller that will be disconnected
        """
        await controller.disconnect()

    async def add_model(self, model_name: str, cloud_name: str):
        """
        Create model

        :param: model_name: Model name
        :param: cloud_name: Cloud name
        """

        # Get controller
        controller = await self.get_controller()
        model = None
        try:
            # Raise exception if model already exists
            if await self.model_exists(model_name, controller=controller):
                raise JujuModelAlreadyExists(
                    "Model {} already exists.".format(model_name)
                )

            # Block until other workers have finished model creation
            while self.creating_model.locked():
                await asyncio.sleep(0.1)

            # If the model exists, return it from the controller
            if model_name in self.models:
                return

            # Create the model
            async with self.creating_model:
                self.log.debug("Creating model {}".format(model_name))
                model = await controller.add_model(
                    model_name,
                    config=self.model_config,
                    cloud_name=cloud_name,
                    credential_name=cloud_name,
                )
                self.models.add(model_name)
        finally:
            if model:
                await self.disconnect_model(model)
            await self.disconnect_controller(controller)

    async def get_model(
        self, controller: Controller, model_name: str, id=None
    ) -> Model:
        """
        Get model from controller

        :param: controller: Controller
        :param: model_name: Model name

        :return: Model: The created Juju model object
        """
        return await controller.get_model(model_name)

    async def model_exists(
        self, model_name: str, controller: Controller = None
    ) -> bool:
        """
        Check if model exists

        :param: controller: Controller
        :param: model_name: Model name

        :return bool
        """
        need_to_disconnect = False

        # Get controller if not passed
        if not controller:
            controller = await self.get_controller()
            need_to_disconnect = True

        # Check if model exists
        try:
            return model_name in await controller.list_models()
        finally:
            if need_to_disconnect:
                await self.disconnect_controller(controller)

    async def get_model_status(self, model_name: str) -> FullStatus:
        """
        Get model status

        :param: model_name: Model name

        :return: Full status object
        """
        controller = await self.get_controller()
        model = await self.get_model(controller, model_name)
        try:
            return await model.get_status()
        finally:
            await self.disconnect_model(model)
            await self.disconnect_controller(controller)

    async def create_machine(
        self,
        model_name: str,
        machine_id: str = None,
        db_dict: dict = None,
        progress_timeout: float = None,
        total_timeout: float = None,
        series: str = "xenial",
        wait: bool = True,
    ) -> (Machine, bool):
        """
        Create machine

        :param: model_name:         Model name
        :param: machine_id:         Machine id
        :param: db_dict:            Dictionary with data of the DB to write the updates
        :param: progress_timeout:   Maximum time between two updates in the model
        :param: total_timeout:      Timeout for the entity to be active
        :param: series:             Series of the machine (xenial, bionic, focal, ...)
        :param: wait:               Wait until machine is ready

        :return: (juju.machine.Machine, bool):  Machine object and a boolean saying
                                                if the machine is new or it already existed
        """
        new = False
        machine = None

        self.log.debug(
            "Creating machine (id={}) in model: {}".format(machine_id, model_name)
        )

        # Get controller
        controller = await self.get_controller()

        # Get model
        model = await self.get_model(controller, model_name)
        try:
            if machine_id is not None:
                self.log.debug(
                    "Searching machine (id={}) in model {}".format(
                        machine_id, model_name
                    )
                )

                # Get machines from model and get the machine with machine_id if exists
                machines = await model.get_machines()
                if machine_id in machines:
                    self.log.debug(
                        "Machine (id={}) found in model {}".format(
                            machine_id, model_name
                        )
                    )
                    machine = model.machines[machine_id]
                else:
                    raise JujuMachineNotFound("Machine {} not found".format(machine_id))

            if machine is None:
                self.log.debug("Creating a new machine in model {}".format(model_name))

                # Create machine
                machine = await model.add_machine(
                    spec=None, constraints=None, disks=None, series=series
                )
                new = True

                # Wait until the machine is ready
                self.log.debug(
                    "Wait until machine {} is ready in model {}".format(
                        machine.entity_id, model_name
                    )
                )
                if wait:
                    await JujuModelWatcher.wait_for(
                        model=model,
                        entity=machine,
                        progress_timeout=progress_timeout,
                        total_timeout=total_timeout,
                        db_dict=db_dict,
                        n2vc=self.n2vc,
                    )
        finally:
            await self.disconnect_model(model)
            await self.disconnect_controller(controller)

        self.log.debug(
            "Machine {} ready at {} in model {}".format(
                machine.entity_id, machine.dns_name, model_name
            )
        )
        return machine, new

    async def provision_machine(
        self,
        model_name: str,
        hostname: str,
        username: str,
        private_key_path: str,
        db_dict: dict = None,
        progress_timeout: float = None,
        total_timeout: float = None,
    ) -> str:
        """
        Manually provisioning of a machine

        :param: model_name:         Model name
        :param: hostname:           IP to access the machine
        :param: username:           Username to login to the machine
        :param: private_key_path:   Local path for the private key
        :param: db_dict:            Dictionary with data of the DB to write the updates
        :param: progress_timeout:   Maximum time between two updates in the model
        :param: total_timeout:      Timeout for the entity to be active

        :return: (Entity): Machine id
        """
        self.log.debug(
            "Provisioning machine. model: {}, hostname: {}, username: {}".format(
                model_name, hostname, username
            )
        )

        # Get controller
        controller = await self.get_controller()

        # Get model
        model = await self.get_model(controller, model_name)

        try:
            # Get provisioner
            provisioner = AsyncSSHProvisioner(
                host=hostname,
                user=username,
                private_key_path=private_key_path,
                log=self.log,
            )

            # Provision machine
            params = await provisioner.provision_machine()

            params.jobs = ["JobHostUnits"]

            self.log.debug("Adding machine to model")
            connection = model.connection()
            client_facade = client.ClientFacade.from_connection(connection)

            results = await client_facade.AddMachines(params=[params])
            error = results.machines[0].error

            if error:
                msg = "Error adding machine: {}".format(error.message)
                self.log.error(msg=msg)
                raise ValueError(msg)

            machine_id = results.machines[0].machine

            self.log.debug("Installing Juju agent into machine {}".format(machine_id))
            asyncio.ensure_future(
                provisioner.install_agent(
                    connection=connection,
                    nonce=params.nonce,
                    machine_id=machine_id,
                    api=self.api_proxy,
                )
            )

            machine = None
            for _ in range(10):
                machine_list = await model.get_machines()
                if machine_id in machine_list:
                    self.log.debug("Machine {} found in model!".format(machine_id))
                    machine = model.machines.get(machine_id)
                    break
                await asyncio.sleep(2)

            if machine is None:
                msg = "Machine {} not found in model".format(machine_id)
                self.log.error(msg=msg)
                raise JujuMachineNotFound(msg)

            self.log.debug(
                "Wait until machine {} is ready in model {}".format(
                    machine.entity_id, model_name
                )
            )
            await JujuModelWatcher.wait_for(
                model=model,
                entity=machine,
                progress_timeout=progress_timeout,
                total_timeout=total_timeout,
                db_dict=db_dict,
                n2vc=self.n2vc,
            )
        except Exception as e:
            raise e
        finally:
            await self.disconnect_model(model)
            await self.disconnect_controller(controller)

        self.log.debug(
            "Machine provisioned {} in model {}".format(machine_id, model_name)
        )

        return machine_id

    async def deploy_charm(
        self,
        application_name: str,
        path: str,
        model_name: str,
        machine_id: str,
        db_dict: dict = None,
        progress_timeout: float = None,
        total_timeout: float = None,
        config: dict = None,
        series: str = None,
        num_units: int = 1,
    ):
        """Deploy charm

        :param: application_name:   Application name
        :param: path:               Local path to the charm
        :param: model_name:         Model name
        :param: machine_id          ID of the machine
        :param: db_dict:            Dictionary with data of the DB to write the updates
        :param: progress_timeout:   Maximum time between two updates in the model
        :param: total_timeout:      Timeout for the entity to be active
        :param: config:             Config for the charm
        :param: series:             Series of the charm
        :param: num_units:          Number of units

        :return: (juju.application.Application): Juju application
        """
        self.log.debug(
            "Deploying charm {} to machine {} in model ~{}".format(
                application_name, machine_id, model_name
            )
        )
        self.log.debug("charm: {}".format(path))

        # Get controller
        controller = await self.get_controller()

        # Get model
        model = await self.get_model(controller, model_name)

        try:
            application = None
            if application_name not in model.applications:

                if machine_id is not None:
                    if machine_id not in model.machines:
                        msg = "Machine {} not found in model".format(machine_id)
                        self.log.error(msg=msg)
                        raise JujuMachineNotFound(msg)
                    machine = model.machines[machine_id]
                    series = machine.series

                application = await model.deploy(
                    entity_url=path,
                    application_name=application_name,
                    channel="stable",
                    num_units=1,
                    series=series,
                    to=machine_id,
                    config=config,
                )

                self.log.debug(
                    "Wait until application {} is ready in model {}".format(
                        application_name, model_name
                    )
                )
                if num_units > 1:
                    for _ in range(num_units - 1):
                        m, _ = await self.create_machine(model_name, wait=False)
                        await application.add_unit(to=m.entity_id)

                await JujuModelWatcher.wait_for(
                    model=model,
                    entity=application,
                    progress_timeout=progress_timeout,
                    total_timeout=total_timeout,
                    db_dict=db_dict,
                    n2vc=self.n2vc,
                )
                self.log.debug(
                    "Application {} is ready in model {}".format(
                        application_name, model_name
                    )
                )
            else:
                raise JujuApplicationExists(
                    "Application {} exists".format(application_name)
                )
        finally:
            await self.disconnect_model(model)
            await self.disconnect_controller(controller)

        return application

    def _get_application(self, model: Model, application_name: str) -> Application:
        """Get application

        :param: model:              Model object
        :param: application_name:   Application name

        :return: juju.application.Application (or None if it doesn't exist)
        """
        if model.applications and application_name in model.applications:
            return model.applications[application_name]

    async def execute_action(
        self,
        application_name: str,
        model_name: str,
        action_name: str,
        db_dict: dict = None,
        progress_timeout: float = None,
        total_timeout: float = None,
        **kwargs
    ):
        """Execute action

        :param: application_name:   Application name
        :param: model_name:         Model name
        :param: cloud_name:         Cloud name
        :param: action_name:        Name of the action
        :param: db_dict:            Dictionary with data of the DB to write the updates
        :param: progress_timeout:   Maximum time between two updates in the model
        :param: total_timeout:      Timeout for the entity to be active

        :return: (str, str): (output and status)
        """
        self.log.debug(
            "Executing action {} using params {}".format(action_name, kwargs)
        )
        # Get controller
        controller = await self.get_controller()

        # Get model
        model = await self.get_model(controller, model_name)

        try:
            # Get application
            application = self._get_application(
                model, application_name=application_name,
            )
            if application is None:
                raise JujuApplicationNotFound("Cannot execute action")

            # Get unit
            unit = None
            for u in application.units:
                if await u.is_leader_from_status():
                    unit = u
            if unit is None:
                raise Exception("Cannot execute action: leader unit not found")

            actions = await application.get_actions()

            if action_name not in actions:
                raise Exception(
                    "Action {} not in available actions".format(action_name)
                )

            action = await unit.run_action(action_name, **kwargs)

            self.log.debug(
                "Wait until action {} is completed in application {} (model={})".format(
                    action_name, application_name, model_name
                )
            )
            await JujuModelWatcher.wait_for(
                model=model,
                entity=action,
                progress_timeout=progress_timeout,
                total_timeout=total_timeout,
                db_dict=db_dict,
                n2vc=self.n2vc,
            )

            output = await model.get_action_output(action_uuid=action.entity_id)
            status = await model.get_action_status(uuid_or_prefix=action.entity_id)
            status = (
                status[action.entity_id] if action.entity_id in status else "failed"
            )

            self.log.debug(
                "Action {} completed with status {} in application {} (model={})".format(
                    action_name, action.status, application_name, model_name
                )
            )
        except Exception as e:
            raise e
        finally:
            await self.disconnect_model(model)
            await self.disconnect_controller(controller)

        return output, status

    async def get_actions(self, application_name: str, model_name: str) -> dict:
        """Get list of actions

        :param: application_name: Application name
        :param: model_name: Model name

        :return: Dict with this format
            {
                "action_name": "Description of the action",
                ...
            }
        """
        self.log.debug(
            "Getting list of actions for application {}".format(application_name)
        )

        # Get controller
        controller = await self.get_controller()

        # Get model
        model = await self.get_model(controller, model_name)

        try:
            # Get application
            application = self._get_application(
                model, application_name=application_name,
            )

            # Return list of actions
            return await application.get_actions()

        finally:
            # Disconnect from model and controller
            await self.disconnect_model(model)
            await self.disconnect_controller(controller)

    async def add_relation(
        self,
        model_name: str,
        application_name_1: str,
        application_name_2: str,
        relation_1: str,
        relation_2: str,
    ):
        """Add relation

        :param: model_name:             Model name
        :param: application_name_1      First application name
        :param: application_name_2:     Second application name
        :param: relation_1:             First relation name
        :param: relation_2:             Second relation name
        """

        self.log.debug("Adding relation: {} -> {}".format(relation_1, relation_2))

        # Get controller
        controller = await self.get_controller()

        # Get model
        model = await self.get_model(controller, model_name)

        # Build relation strings
        r1 = "{}:{}".format(application_name_1, relation_1)
        r2 = "{}:{}".format(application_name_2, relation_2)

        # Add relation
        try:
            await model.add_relation(relation1=r1, relation2=r2)
        except JujuAPIError as e:
            if "not found" in e.message:
                self.log.warning("Relation not found: {}".format(e.message))
                return
            if "already exists" in e.message:
                self.log.warning("Relation already exists: {}".format(e.message))
                return
            # another exception, raise it
            raise e
        finally:
            await self.disconnect_model(model)
            await self.disconnect_controller(controller)

    async def destroy_model(self, model_name: str, total_timeout: float):
        """
        Destroy model

        :param: model_name:     Model name
        :param: total_timeout:  Timeout
        """

        controller = await self.get_controller()
        model = await self.get_model(controller, model_name)
        try:
            self.log.debug("Destroying model {}".format(model_name))
            uuid = model.info.uuid

            # Destroy applications
            for application_name in model.applications:
                try:
                    await self.destroy_application(
                        model, application_name=application_name,
                    )
                except Exception as e:
                    self.log.error(
                        "Error destroying application {} in model {}: {}".format(
                            application_name, model_name, e
                        )
                    )

            # Destroy machines
            machines = await model.get_machines()
            for machine_id in machines:
                try:
                    await self.destroy_machine(
                        model, machine_id=machine_id, total_timeout=total_timeout,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass

            # Disconnect model
            await self.disconnect_model(model)

            # Destroy model
            if model_name in self.models:
                self.models.remove(model_name)

            await controller.destroy_model(uuid)

            # Wait until model is destroyed
            self.log.debug("Waiting for model {} to be destroyed...".format(model_name))
            last_exception = ""

            if total_timeout is None:
                total_timeout = 3600
            end = time.time() + total_timeout
            while time.time() < end:
                try:
                    models = await controller.list_models()
                    if model_name not in models:
                        self.log.debug(
                            "The model {} ({}) was destroyed".format(model_name, uuid)
                        )
                        return
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    last_exception = e
                await asyncio.sleep(5)
            raise Exception(
                "Timeout waiting for model {} to be destroyed {}".format(
                    model_name, last_exception
                )
            )
        finally:
            await self.disconnect_controller(controller)

    async def destroy_application(self, model: Model, application_name: str):
        """
        Destroy application

        :param: model:              Model object
        :param: application_name:   Application name
        """
        self.log.debug(
            "Destroying application {} in model {}".format(
                application_name, model.info.name
            )
        )
        application = model.applications.get(application_name)
        if application:
            await application.destroy()
        else:
            self.log.warning("Application not found: {}".format(application_name))

    async def destroy_machine(
        self, model: Model, machine_id: str, total_timeout: float = 3600
    ):
        """
        Destroy machine

        :param: model:          Model object
        :param: machine_id:     Machine id
        :param: total_timeout:  Timeout in seconds
        """
        machines = await model.get_machines()
        if machine_id in machines:
            machine = model.machines[machine_id]
            # TODO: change this by machine.is_manual when this is upstreamed:
            # https://github.com/juju/python-libjuju/pull/396
            if "instance-id" in machine.safe_data and machine.safe_data[
                "instance-id"
            ].startswith("manual:"):
                await machine.destroy(force=True)

                # max timeout
                end = time.time() + total_timeout

                # wait for machine removal
                machines = await model.get_machines()
                while machine_id in machines and time.time() < end:
                    self.log.debug(
                        "Waiting for machine {} is destroyed".format(machine_id)
                    )
                    await asyncio.sleep(0.5)
                    machines = await model.get_machines()
                self.log.debug("Machine destroyed: {}".format(machine_id))
        else:
            self.log.debug("Machine not found: {}".format(machine_id))

    async def configure_application(
        self, model_name: str, application_name: str, config: dict = None
    ):
        """Configure application

        :param: model_name:         Model name
        :param: application_name:   Application name
        :param: config:             Config to apply to the charm
        """
        self.log.debug("Configuring application {}".format(application_name))

        if config:
            try:
                controller = await self.get_controller()
                model = await self.get_model(controller, model_name)
                application = self._get_application(
                    model, application_name=application_name,
                )
                await application.set_config(config)
            finally:
                await self.disconnect_model(model)
                await self.disconnect_controller(controller)

    def _get_api_endpoints_db(self) -> [str]:
        """
        Get API Endpoints from DB

        :return: List of API endpoints
        """
        self.log.debug("Getting endpoints from database")

        juju_info = self.db.get_one(
            DB_DATA.api_endpoints.table,
            q_filter=DB_DATA.api_endpoints.filter,
            fail_on_empty=False,
        )
        if juju_info and DB_DATA.api_endpoints.key in juju_info:
            return juju_info[DB_DATA.api_endpoints.key]

    def _update_api_endpoints_db(self, endpoints: [str]):
        """
        Update API endpoints in Database

        :param: List of endpoints
        """
        self.log.debug("Saving endpoints {} in database".format(endpoints))

        juju_info = self.db.get_one(
            DB_DATA.api_endpoints.table,
            q_filter=DB_DATA.api_endpoints.filter,
            fail_on_empty=False,
        )
        # If it doesn't, then create it
        if not juju_info:
            try:
                self.db.create(
                    DB_DATA.api_endpoints.table, DB_DATA.api_endpoints.filter,
                )
            except DbException as e:
                # Racing condition: check if another N2VC worker has created it
                juju_info = self.db.get_one(
                    DB_DATA.api_endpoints.table,
                    q_filter=DB_DATA.api_endpoints.filter,
                    fail_on_empty=False,
                )
                if not juju_info:
                    raise e
        self.db.set_one(
            DB_DATA.api_endpoints.table,
            DB_DATA.api_endpoints.filter,
            {DB_DATA.api_endpoints.key: endpoints},
        )

    def handle_exception(self, loop, context):
        # All unhandled exceptions by libjuju are handled here.
        pass

    async def health_check(self, interval: float = 300.0):
        """
        Health check to make sure controller and controller_model connections are OK

        :param: interval: Time in seconds between checks
        """
        while True:
            try:
                controller = await self.get_controller()
                # self.log.debug("VCA is alive")
            except Exception as e:
                self.log.error("Health check to VCA failed: {}".format(e))
            finally:
                await self.disconnect_controller(controller)
            await asyncio.sleep(interval)

    async def list_models(self, contains: str = None) -> [str]:
        """List models with certain names

        :param: contains:   String that is contained in model name

        :retur: [models] Returns list of model names
        """

        controller = await self.get_controller()
        try:
            models = await controller.list_models()
            if contains:
                models = [model for model in models if contains in model]
            return models
        finally:
            await self.disconnect_controller(controller)
