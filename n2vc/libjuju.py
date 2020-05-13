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
from juju.client.connector import NoConnectionException
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

        self.endpoints = [endpoint]  # TODO: Store and get endpoints from DB
        self.api_proxy = api_proxy
        self.username = username
        self.password = password
        self.cacert = cacert
        self.loop = loop or asyncio.get_event_loop()
        self.log = log or logging.getLogger("Libjuju")
        self.db = db
        self.n2vc = n2vc

        # Generate config for models
        self.model_config = {}
        if apt_mirror:
            self.model_config["apt-mirror"] = apt_mirror
        self.model_config["enable-os-refresh-update"] = enable_os_upgrade
        self.model_config["enable-os-upgrade"] = enable_os_upgrade

        self.reconnecting = asyncio.Lock(loop=self.loop)
        self.creating_model = asyncio.Lock(loop=self.loop)

        self.models = set()
        self.controller = Controller(loop=self.loop)

        self.loop.run_until_complete(self.connect())

    async def connect(self):
        """Connect to the controller"""

        self.log.debug("Connecting from controller")
        await self.controller.connect(
            endpoint=self.endpoints,
            username=self.username,
            password=self.password,
            cacert=self.cacert,
        )
        e = self.controller.connection().endpoint
        self.log.info("Connected to controller: {}".format(e))

    async def disconnect(self):
        """Disconnect from controller"""

        self.log.debug("Disconnecting from controller")
        await self.controller.disconnect()
        self.log.info("Disconnected from controller")

    def controller_connected(self) -> bool:
        """Check if the controller connection is open

        :return: bool: True if connected, False if not connected
        """

        is_connected = False
        try:
            is_connected = self.controller.connection().is_open
        except NoConnectionException:
            self.log.warning("VCA not connected")
        return is_connected

    async def disconnect_model(self, model: Model):
        """
        Disconnect model

        :param: model: Model that will be disconnected
        """
        try:
            await model.disconnect()
        except Exception:
            pass

    async def _reconnect(
        self,
        retry: bool = False,
        timeout: int = 5,
        time_between_retries: int = 3,
        maximum_retries: int = 0,
    ):
        """
        Reconnect to the controller

        :param: retry:                  Set it to True to retry if the connection fails
        :param: time_between_retries:   Time in seconds between retries
        :param: maximum_retries         Maximum retries. If not set, it will retry forever

        :raises: Exception if cannot connect to the controller
        """

        if self.reconnecting.locked():
            # Return if another function is trying to reconnect
            return
        async with self.reconnecting:
            attempt = 0
            while True:
                try:
                    await asyncio.wait_for(self.connect(), timeout=timeout)
                    break
                except asyncio.TimeoutError:
                    self.log.error("Error reconnecting to controller: Timeout")
                except Exception as e:
                    self.log.error("Error reconnecting to controller: {}".format(e))

                attempt += 1
                maximum_retries_reached = attempt == maximum_retries

                if not retry or maximum_retries_reached:
                    raise JujuControllerFailedConnecting("Controller is not connected")
                else:
                    await asyncio.sleep(time_between_retries)

    async def add_model(self, model_name: str, cloud_name: str):
        """
        Create model

        :param: model_name: Model name
        :param: cloud_name: Cloud name
        """

        # Reconnect to the controller if not connected
        if not self.controller_connected():
            await self._reconnect()

        # Raise exception if model already exists
        if await self.model_exists(model_name):
            raise JujuModelAlreadyExists("Model {} already exists.".format(model_name))

        # Block until other workers have finished model creation
        while self.creating_model.locked():
            await asyncio.sleep(0.1)

        # If the model exists, return it from the controller
        if model_name in self.models:
            return await self.get_model(model_name)

        # Create the model
        self.log.debug("Creating model {}".format(model_name))
        async with self.creating_model:
            model = await self.controller.add_model(
                model_name,
                config=self.model_config,
                cloud_name=cloud_name,
                credential_name=cloud_name,
            )
            await self.disconnect_model(model)
            self.models.add(model_name)

    async def get_model(self, model_name: str) -> Model:
        """
        Get model from controller

        :param: model_name: Model name

        :return: Model: The created Juju model object
        """

        # Check if controller is connected
        if not self.controller_connected():
            await self._reconnect()
        return await self.controller.get_model(model_name)

    async def model_exists(self, model_name: str) -> bool:
        """
        Check if model exists

        :param: model_name: Model name

        :return bool
        """

        # Check if controller is connected
        if not self.controller_connected():
            await self._reconnect()

        return model_name in await self.controller.list_models()

    async def get_model_status(self, model_name: str) -> FullStatus:
        """
        Get model status

        :param: model_name: Model name

        :return: Full status object
        """
        model = await self.get_model(model_name)
        status = await model.get_status()
        await self.disconnect_model(model)
        return status

    async def create_machine(
        self,
        model_name: str,
        machine_id: str = None,
        db_dict: dict = None,
        progress_timeout: float = None,
        total_timeout: float = None,
        series: str = "xenial",
    ) -> (Machine, bool):
        """
        Create machine

        :param: model_name:         Model name
        :param: machine_id:         Machine id
        :param: db_dict:            Dictionary with data of the DB to write the updates
        :param: progress_timeout:   Maximum time between two updates in the model
        :param: total_timeout:      Timeout for the entity to be active

        :return: (juju.machine.Machine, bool):  Machine object and a boolean saying
                                                if the machine is new or it already existed
        """
        new = False
        machine = None

        self.log.debug(
            "Creating machine (id={}) in model: {}".format(machine_id, model_name)
        )

        # Get model
        model = await self.get_model(model_name)
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

        self.log.debug("Machine ready at {}".format(machine.dns_name))
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

        # Get model
        model = await self.get_model(model_name)

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

        self.log.debug("Machine provisioned {}".format(machine_id))

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

        :return: (juju.application.Application): Juju application
        """

        # Get model
        model = await self.get_model(model_name)

        try:
            application = None
            if application_name not in model.applications:
                self.log.debug(
                    "Deploying charm {} to machine {} in model ~{}".format(
                        application_name, machine_id, model_name
                    )
                )
                self.log.debug("charm: {}".format(path))
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

                await JujuModelWatcher.wait_for(
                    model=model,
                    entity=application,
                    progress_timeout=progress_timeout,
                    total_timeout=total_timeout,
                    db_dict=db_dict,
                    n2vc=self.n2vc,
                )
            else:
                raise JujuApplicationExists("Application {} exists".format(application_name))

        except Exception as e:
            raise e
        finally:
            await self.disconnect_model(model)

        self.log.debug("application deployed")

        return application

    async def _get_application(
        self, model: Model, application_name: str
    ) -> Application:
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
        # Get model and observer
        model = await self.get_model(model_name)

        try:
            # Get application
            application = await self._get_application(
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

            self.log.debug(
                "Executing action {} using params {}".format(action_name, kwargs)
            )
            action = await unit.run_action(action_name, **kwargs)

            # Register action with observer and wait for it to finish
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

            self.log.debug("action completed with status: {}".format(action.status))
        except Exception as e:
            raise e
        finally:
            await self.disconnect_model(model)

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

        # Get model
        model = await self.get_model(model_name)

        # Get application
        application = await self._get_application(
            model, application_name=application_name,
        )

        # Get list of actions
        actions = await application.get_actions()

        # Disconnect from model
        await self.disconnect_model(model)

        return actions

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

        # Get model
        model = await self.get_model(model_name)

        # Build relation strings
        r1 = "{}:{}".format(application_name_1, relation_1)
        r2 = "{}:{}".format(application_name_2, relation_2)

        # Add relation
        self.log.debug("Adding relation: {} -> {}".format(r1, r2))
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

    async def destroy_model(
        self, model_name: str, total_timeout: float,
    ):
        """
        Destroy model

        :param: model_name:     Model name
        :param: total_timeout:  Timeout
        """
        model = await self.get_model(model_name)
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
        self.models.remove(model_name)
        await self.controller.destroy_model(uuid)

        # Wait until model is destroyed
        self.log.debug("Waiting for model {} to be destroyed...".format(model_name))
        last_exception = ""

        if total_timeout is None:
            total_timeout = 3600
        end = time.time() + total_timeout
        while time.time() < end:
            try:
                models = await self.controller.list_models()
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
        if config:
            model = await self.get_model(model_name)
            application = await self._get_application(
                model, application_name=application_name,
            )
            await application.set_config(config)
            await self.disconnect_model(model)
