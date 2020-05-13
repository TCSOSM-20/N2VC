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
import time
from juju.client import client
from n2vc.utils import FinalStatus, EntityType
from n2vc.exceptions import EntityInvalidException
from n2vc.n2vc_conn import N2VCConnector
from juju.model import ModelEntity, Model
from juju.client.overrides import Delta

import logging

logger = logging.getLogger("__main__")


class JujuModelWatcher:
    @staticmethod
    async def wait_for(
        model,
        entity: ModelEntity,
        progress_timeout: float = 3600,
        total_timeout: float = 3600,
        db_dict: dict = None,
        n2vc: N2VCConnector = None,
    ):
        """
        Wait for entity to reach its final state.

        :param: model:              Model to observe
        :param: entity:             Entity object
        :param: progress_timeout:   Maximum time between two updates in the model
        :param: total_timeout:      Timeout for the entity to be active
        :param: db_dict:            Dictionary with data of the DB to write the updates
        :param: n2vc:               N2VC Connector objector

        :raises: asyncio.TimeoutError when timeout reaches
        """

        if progress_timeout is None:
            progress_timeout = 3600.0
        if total_timeout is None:
            total_timeout = 3600.0

        entity_type = EntityType.get_entity(type(entity))
        if entity_type not in FinalStatus:
            raise EntityInvalidException("Entity type not found")

        # Get final states
        final_states = FinalStatus[entity_type].status
        field_to_check = FinalStatus[entity_type].field

        # Coroutine to wait until the entity reaches the final state
        wait_for_entity = asyncio.ensure_future(
            asyncio.wait_for(
                model.block_until(
                    lambda: entity.__getattribute__(field_to_check) in final_states
                ),
                timeout=total_timeout,
            )
        )

        # Coroutine to watch the model for changes (and write them to DB)
        watcher = asyncio.ensure_future(
            JujuModelWatcher.model_watcher(
                model,
                entity_id=entity.entity_id,
                entity_type=entity_type,
                timeout=progress_timeout,
                db_dict=db_dict,
                n2vc=n2vc,
            )
        )

        tasks = [wait_for_entity, watcher]
        try:
            # Execute tasks, and stop when the first is finished
            # The watcher task won't never finish (unless it timeouts)
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        except Exception as e:
            raise e
        finally:
            # Cancel tasks
            for task in tasks:
                task.cancel()

    @staticmethod
    async def model_watcher(
        model: Model,
        entity_id: str,
        entity_type: EntityType,
        timeout: float,
        db_dict: dict = None,
        n2vc: N2VCConnector = None,
    ):
        """
        Observes the changes related to an specific entity in a model

        :param: model:          Model to observe
        :param: entity_id:      ID of the entity to be observed
        :param: entity_type:    EntityType (p.e. .APPLICATION, .MACHINE, and .ACTION)
        :param: timeout:        Maximum time between two updates in the model
        :param: db_dict:        Dictionary with data of the DB to write the updates
        :param: n2vc:           N2VC Connector objector

        :raises: asyncio.TimeoutError when timeout reaches
        """

        allwatcher = client.AllWatcherFacade.from_connection(model.connection())

        # Genenerate array with entity types to listen
        entity_types = (
            [entity_type, EntityType.UNIT]
            if entity_type == EntityType.APPLICATION  # TODO: Add .ACTION too
            else [entity_type]
        )

        # Get time when it should timeout
        timeout_end = time.time() + timeout

        while True:
            change = await allwatcher.Next()
            for delta in change.deltas:
                write = False
                delta_entity = None

                # Get delta EntityType
                delta_entity = EntityType.get_entity_from_delta(delta.entity)

                if delta_entity in entity_types:
                    # Get entity id
                    if entity_type == EntityType.APPLICATION:
                        id = (
                            delta.data["application"]
                            if delta_entity == EntityType.UNIT
                            else delta.data["name"]
                        )
                    else:
                        id = delta.data["id"]

                    # Write if the entity id match
                    write = True if id == entity_id else False

                    # Update timeout
                    timeout_end = time.time() + timeout
                    (status, status_message, vca_status) = JujuModelWatcher.get_status(
                        delta, entity_type=delta_entity
                    )

                    if write and n2vc is not None and db_dict:
                        # Write status to DB
                        status = n2vc.osm_status(delta_entity, status)
                        await n2vc.write_app_status_to_db(
                            db_dict=db_dict,
                            status=status,
                            detailed_status=status_message,
                            vca_status=vca_status,
                            entity_type=delta_entity.value.__name__.lower(),
                        )
            # Check if timeout
            if time.time() > timeout_end:
                raise asyncio.TimeoutError()

    @staticmethod
    def get_status(delta: Delta, entity_type: EntityType) -> (str, str, str):
        """
        Get status from delta

        :param: delta:          Delta generated by the allwatcher
        :param: entity_type:    EntityType (p.e. .APPLICATION, .MACHINE, and .ACTION)

        :return (status, message, vca_status)
        """
        if entity_type == EntityType.MACHINE:
            return (
                delta.data["agent-status"]["current"],
                delta.data["instance-status"]["message"],
                delta.data["instance-status"]["current"],
            )
        elif entity_type == EntityType.ACTION:
            return (
                delta.data["status"],
                delta.data["status"],
                delta.data["status"],
            )
        elif entity_type == EntityType.APPLICATION:
            return (
                delta.data["status"]["current"],
                delta.data["status"]["message"],
                delta.data["status"]["current"],
            )
        elif entity_type == EntityType.UNIT:
            return (
                delta.data["workload-status"]["current"],
                delta.data["workload-status"]["message"],
                delta.data["workload-status"]["current"],
            )
