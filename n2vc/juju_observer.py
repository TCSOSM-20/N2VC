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
import time

from juju.model import ModelObserver, Model
from juju.machine import Machine
from juju.application import Application
from juju.action import Action

from n2vc.n2vc_conn import N2VCConnector, juju_status_2_osm_status
from n2vc.exceptions import N2VCTimeoutException


class _Entity:
    def __init__(self, entity_id: str, entity_type: str, obj: object, db_dict: dict):
        self.entity_id = entity_id
        self.entity_type = entity_type
        self.obj = obj
        self.event = asyncio.Event()
        self.db_dict = db_dict


class JujuModelObserver(ModelObserver):

    def __init__(self, n2vc: N2VCConnector, model: Model):
        self.n2vc = n2vc
        self.model = model
        model.add_observer(self)
        self.machines = dict()
        self.applications = dict()
        self.actions = dict()

    def register_machine(self, machine: Machine, db_dict: dict):
        entity_id = machine.entity_id
        entity = _Entity(entity_id=entity_id, entity_type='machine', obj=machine, db_dict=db_dict)
        self.machines[entity_id] = entity

    def unregister_machine(self, machine_id: str):
        if machine_id in self.machines:
            del self.machines[machine_id]

    def is_machine_registered(self, machine_id: str):
        return machine_id in self.machines

    def register_application(self, application: Application, db_dict: dict):
        entity_id = application.entity_id
        entity = _Entity(entity_id=entity_id, entity_type='application', obj=application, db_dict=db_dict)
        self.applications[entity_id] = entity

    def unregister_application(self, application_id: str):
        if application_id in self.applications:
            del self.applications[application_id]

    def is_application_registered(self, application_id: str):
        return application_id in self.applications

    def register_action(self, action: Action, db_dict: dict):
        entity_id = action.entity_id
        entity = _Entity(entity_id=entity_id, entity_type='action', obj=action, db_dict=db_dict)
        self.actions[entity_id] = entity

    def unregister_action(self, action_id: str):
        if action_id in self.actions:
            del self.actions[action_id]

    def is_action_registered(self, action_id: str):
        return action_id in self.actions

    async def wait_for_machine(
            self,
            machine_id: str,
            progress_timeout: float = None,
            total_timeout: float = None) -> int:

        if not self.is_machine_registered(machine_id):
            return

        # wait for a final state
        entity = self.machines[machine_id]
        return await self._wait_for_entity(
            entity=entity,
            field_to_check='agent_status',
            final_states_list=['started'],
            progress_timeout=progress_timeout,
            total_timeout=total_timeout)

    async def wait_for_application(
            self,
            application_id: str,
            progress_timeout: float = None,
            total_timeout: float = None) -> int:

        if not self.is_application_registered(application_id):
            return

        # application statuses: unknown, active, waiting
        # wait for a final state
        entity = self.applications[application_id]
        return await self._wait_for_entity(
            entity=entity,
            field_to_check='status',
            final_states_list=['active', 'blocked'],
            progress_timeout=progress_timeout,
            total_timeout=total_timeout)

    async def wait_for_action(
            self,
            action_id: str,
            progress_timeout: float = None,
            total_timeout: float = None) -> int:

        if not self.is_action_registered(action_id):
            return

        # action statuses: pending, running, completed, failed, cancelled
        # wait for a final state
        entity = self.actions[action_id]
        return await self._wait_for_entity(
            entity=entity,
            field_to_check='status',
            final_states_list=['completed', 'failed', 'cancelled'],
            progress_timeout=progress_timeout,
            total_timeout=total_timeout)

    async def _wait_for_entity(
            self,
            entity: _Entity,
            field_to_check: str,
            final_states_list: list,
            progress_timeout: float = None,
            total_timeout: float = None) -> int:

        # default values for no timeout
        if total_timeout is None:
            total_timeout = 100000
        if progress_timeout is None:
            progress_timeout = 100000

        # max end time
        now = time.time()
        total_end = now + total_timeout

        if now >= total_end:
            raise N2VCTimeoutException(
                message='Total timeout {} seconds, {}: {}'.format(total_timeout, entity.entity_type, entity.entity_id),
                timeout='total'
            )

        # update next progress timeout
        progress_end = now + progress_timeout  # type: float

        # which is closest? progress or end timeout?
        closest_end = min(total_end, progress_end)

        next_timeout = closest_end - now

        retries = 0

        while entity.obj.__getattribute__(field_to_check) not in final_states_list:
            retries += 1
            if await _wait_for_event_or_timeout(entity.event, next_timeout):
                entity.event.clear()
            else:
                message = 'Progress timeout {} seconds, {}}: {}'\
                    .format(progress_timeout, entity.entity_type, entity.entity_id)
                self.n2vc.debug(message)
                raise N2VCTimeoutException(message=message, timeout='progress')
        self.n2vc.debug('End of wait. Final state: {}, retries: {}'
                        .format(entity.obj.__getattribute__(field_to_check), retries))
        return retries

    async def on_change(self, delta, old, new, model):

        if new is None:
            return

        # log
        self.n2vc.debug('on_change(): type: {}, entity: {}, id: {}'
                        .format(delta.type, delta.entity, new.entity_id))

        if delta.entity == 'machine':

            # check registered machine
            if new.entity_id not in self.machines:
                return

            # write change in database
            await self.n2vc.write_app_status_to_db(
                db_dict=self.machines[new.entity_id].db_dict,
                status=juju_status_2_osm_status(delta.entity, new.agent_status),
                detailed_status=new.status_message,
                vca_status=new.status,
                entity_type='machine'
            )

            # set event for this machine
            self.machines[new.entity_id].event.set()

        elif delta.entity == 'application':

            # check registered application
            if new.entity_id not in self.applications:
                return

            # write change in database
            await self.n2vc.write_app_status_to_db(
                db_dict=self.applications[new.entity_id].db_dict,
                status=juju_status_2_osm_status(delta.entity, new.status),
                detailed_status=new.status_message,
                vca_status=new.status,
                entity_type='application'
            )

            # set event for this application
            self.applications[new.entity_id].event.set()

        elif delta.entity == 'unit':

            # get the application for this unit
            application_id = delta.data['application']

            # check registered application
            if application_id not in self.applications:
                return

            # write change in database
            await self.n2vc.write_app_status_to_db(
                db_dict=self.applications[application_id].db_dict,
                status=juju_status_2_osm_status(delta.entity, new.workload_status),
                detailed_status=new.workload_status_message,
                vca_status=new.workload_status,
                entity_type='unit'
            )

            # set event for this application
            self.applications[application_id].event.set()

        elif delta.entity == 'action':

            # check registered action
            if new.entity_id not in self.actions:
                return

            # write change in database
            await self.n2vc.write_app_status_to_db(
                db_dict=self.actions[new.entity_id].db_dict,
                status=juju_status_2_osm_status(delta.entity, new.status),
                detailed_status=new.status,
                vca_status=new.status,
                entity_type='action'
            )

            # set event for this application
            self.actions[new.entity_id].event.set()


async def _wait_for_event_or_timeout(event: asyncio.Event, timeout: float = None):
    try:
        await asyncio.wait_for(fut=event.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        pass
    return event.is_set()
