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

from enum import Enum
from juju.machine import Machine
from juju.application import Application
from juju.action import Action
from juju.unit import Unit


class N2VCDeploymentStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    UNKNOWN = "unknown"


class Dict(dict):
    """
    Dict class that allows to access the keys like attributes
    """

    def __getattribute__(self, name):
        if name in self:
            return self[name]


class EntityType(Enum):
    MACHINE = Machine
    APPLICATION = Application
    ACTION = Action
    UNIT = Unit

    @classmethod
    def has_value(cls, value):
        return value in cls._value2member_map_  # pylint: disable=E1101

    @classmethod
    def get_entity(cls, value):
        return (
            cls._value2member_map_[value]  # pylint: disable=E1101
            if value in cls._value2member_map_  # pylint: disable=E1101
            else None  # pylint: disable=E1101
        )

    @classmethod
    def get_entity_from_delta(cls, delta_entity: str):
        """
        Get Value from delta entity

        :param: delta_entity: Possible values are "machine", "application", "unit", "action"
        """
        for v in cls._value2member_map_:  # pylint: disable=E1101
            if v.__name__.lower() == delta_entity:
                return cls.get_entity(v)


FinalStatus = Dict(
    {
        EntityType.MACHINE: Dict({"field": "agent_status", "status": ["started"]}),
        EntityType.APPLICATION: Dict(
            {"field": "status", "status": ["active", "blocked"]}
        ),
        EntityType.ACTION: Dict(
            {"field": "status", "status": ["completed", "failed", "cancelled"]}
        ),
    }
)

JujuStatusToOSM = {
    EntityType.MACHINE: {
        "pending": N2VCDeploymentStatus.PENDING,
        "started": N2VCDeploymentStatus.COMPLETED,
    },
    EntityType.APPLICATION: {
        "waiting": N2VCDeploymentStatus.RUNNING,
        "maintenance": N2VCDeploymentStatus.RUNNING,
        "blocked": N2VCDeploymentStatus.RUNNING,
        "error": N2VCDeploymentStatus.FAILED,
        "active": N2VCDeploymentStatus.COMPLETED,
    },
    EntityType.ACTION: {
        "pending": N2VCDeploymentStatus.PENDING,
        "running": N2VCDeploymentStatus.RUNNING,
        "completed": N2VCDeploymentStatus.COMPLETED,
    },
    EntityType.UNIT: {
        "waiting": N2VCDeploymentStatus.RUNNING,
        "maintenance": N2VCDeploymentStatus.RUNNING,
        "blocked": N2VCDeploymentStatus.RUNNING,
        "error": N2VCDeploymentStatus.FAILED,
        "active": N2VCDeploymentStatus.COMPLETED,
    },
}

DB_DATA = Dict(
    {
        "api_endpoints": Dict(
            {"table": "admin", "filter": {"_id": "juju"}, "key": "api_endpoints"}
        )
    }
)
