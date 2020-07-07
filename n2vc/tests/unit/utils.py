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

from n2vc.utils import Dict, EntityType, N2VCDeploymentStatus
from n2vc.n2vc_conn import N2VCConnector
from unittest.mock import MagicMock


async def AsyncMockFunc():
    await asyncio.sleep(1)


class AsyncMock(MagicMock):
    async def __call__(self, *args, **kwargs):
        return super(AsyncMock, self).__call__(*args, **kwargs)


class FakeN2VC(MagicMock):
    last_written_values = None

    async def write_app_status_to_db(
        self,
        db_dict: dict,
        status: N2VCDeploymentStatus,
        detailed_status: str,
        vca_status: str,
        entity_type: str,
    ):
        self.last_written_values = Dict(
            {
                "n2vc_status": status,
                "message": detailed_status,
                "vca_status": vca_status,
                "entity": entity_type,
            }
        )

    osm_status = N2VCConnector.osm_status


class FakeMachine(MagicMock):
    entity_id = "2"
    dns_name = "FAKE ENDPOINT"
    model_name = "FAKE MODEL"
    entity_type = EntityType.MACHINE

    async def destroy(self, force):
        pass


class FakeWatcher(AsyncMock):

    delta_to_return = None

    async def Next(self):
        return Dict({"deltas": self.delta_to_return})


class FakeConnection(MagicMock):
    endpoint = None
    is_open = False


class FakeAction(MagicMock):
    entity_id = "id"
    status = "ready"


class FakeUnit(MagicMock):
    async def is_leader_from_status(self):
        return True

    async def run_action(self, action_name):
        return FakeAction()


class FakeApplication(AsyncMock):

    async def set_config(self, config):
        pass

    async def add_unit(self, to):
        pass

    async def get_actions(self):
        return ["existing_action"]

    units = [FakeUnit(), FakeUnit()]


FAKE_DELTA_MACHINE_PENDING = Dict(
    {
        "deltas": ["machine", "change", {}],
        "entity": "machine",
        "type": "change",
        "data": {
            "id": "2",
            "instance-id": "juju-1b5808-2",
            "agent-status": {"current": "pending", "message": "", "version": ""},
            "instance-status": {"current": "running", "message": "Running"},
        },
    }
)
FAKE_DELTA_MACHINE_STARTED = Dict(
    {
        "deltas": ["machine", "change", {}],
        "entity": "machine",
        "type": "change",
        "data": {
            "id": "2",
            "instance-id": "juju-1b5808-2",
            "agent-status": {"current": "started", "message": "", "version": ""},
            "instance-status": {"current": "running", "message": "Running"},
        },
    }
)

FAKE_DELTA_UNIT_PENDING = Dict(
    {
        "deltas": ["unit", "change", {}],
        "entity": "unit",
        "type": "change",
        "data": {
            "name": "git/0",
            "application": "git",
            "machine-id": "6",
            "workload-status": {"current": "waiting", "message": ""},
            "agent-status": {"current": "idle", "message": ""},
        },
    }
)

FAKE_DELTA_UNIT_STARTED = Dict(
    {
        "deltas": ["unit", "change", {}],
        "entity": "unit",
        "type": "change",
        "data": {
            "name": "git/0",
            "application": "git",
            "machine-id": "6",
            "workload-status": {"current": "active", "message": ""},
            "agent-status": {"current": "idle", "message": ""},
        },
    }
)

FAKE_DELTA_APPLICATION_MAINTENANCE = Dict(
    {
        "deltas": ["application", "change", {}],
        "entity": "application",
        "type": "change",
        "data": {
            "name": "git",
            "status": {
                "current": "maintenance",
                "message": "installing charm software",
            },
        },
    }
)

FAKE_DELTA_APPLICATION_ACTIVE = Dict(
    {
        "deltas": ["application", "change", {}],
        "entity": "application",
        "type": "change",
        "data": {"name": "git", "status": {"current": "active", "message": "Ready!"}},
    }
)

FAKE_DELTA_ACTION_COMPLETED = Dict(
    {
        "deltas": ["action", "change", {}],
        "entity": "action",
        "type": "change",
        "data": {
            "model-uuid": "af19cdd4-374a-4d9f-86b1-bfed7b1b5808",
            "id": "1",
            "receiver": "git/0",
            "name": "add-repo",
            "status": "completed",
            "message": "",
        },
    }
)

Deltas = [
    Dict(
        {
            "entity": Dict({"id": "2", "type": EntityType.MACHINE}),
            "filter": Dict({"entity_id": "2", "entity_type": EntityType.MACHINE}),
            "delta": FAKE_DELTA_MACHINE_PENDING,
            "entity_status": Dict(
                {"status": "pending", "message": "Running", "vca_status": "running"}
            ),
            "db": Dict(
                {
                    "written": True,
                    "data": Dict(
                        {
                            "message": "Running",
                            "entity": "machine",
                            "vca_status": "running",
                            "n2vc_status": N2VCDeploymentStatus.PENDING,
                        }
                    ),
                }
            ),
        }
    ),
    Dict(
        {
            "entity": Dict({"id": "2", "type": EntityType.MACHINE}),
            "filter": Dict({"entity_id": "1", "entity_type": EntityType.MACHINE}),
            "delta": FAKE_DELTA_MACHINE_PENDING,
            "entity_status": Dict(
                {"status": "pending", "message": "Running", "vca_status": "running"}
            ),
            "db": Dict({"written": False, "data": None}),
        }
    ),
    Dict(
        {
            "entity": Dict({"id": "2", "type": EntityType.MACHINE}),
            "filter": Dict({"entity_id": "2", "entity_type": EntityType.MACHINE}),
            "delta": FAKE_DELTA_MACHINE_STARTED,
            "entity_status": Dict(
                {"status": "started", "message": "Running", "vca_status": "running"}
            ),
            "db": Dict(
                {
                    "written": True,
                    "data": Dict(
                        {
                            "message": "Running",
                            "entity": "machine",
                            "vca_status": "running",
                            "n2vc_status": N2VCDeploymentStatus.COMPLETED,
                        }
                    ),
                }
            ),
        }
    ),
    Dict(
        {
            "entity": Dict({"id": "2", "type": EntityType.MACHINE}),
            "filter": Dict({"entity_id": "1", "entity_type": EntityType.MACHINE}),
            "delta": FAKE_DELTA_MACHINE_STARTED,
            "entity_status": Dict(
                {"status": "started", "message": "Running", "vca_status": "running"}
            ),
            "db": Dict({"written": False, "data": None}),
        }
    ),
    Dict(
        {
            "entity": Dict({"id": "git/0", "type": EntityType.UNIT}),
            "filter": Dict({"entity_id": "git", "entity_type": EntityType.APPLICATION}),
            "delta": FAKE_DELTA_UNIT_PENDING,
            "entity_status": Dict(
                {"status": "waiting", "message": "", "vca_status": "waiting"}
            ),
            "db": Dict(
                {
                    "written": True,
                    "data": Dict(
                        {
                            "message": "",
                            "entity": "unit",
                            "vca_status": "waiting",
                            "n2vc_status": N2VCDeploymentStatus.RUNNING,
                        }
                    ),
                }
            ),
        }
    ),
    Dict(
        {
            "entity": Dict({"id": "git/0", "type": EntityType.UNIT}),
            "filter": Dict({"entity_id": "2", "entity_type": EntityType.MACHINE}),
            "delta": FAKE_DELTA_UNIT_PENDING,
            "entity_status": Dict(
                {"status": "waiting", "message": "", "vca_status": "waiting"}
            ),
            "db": Dict({"written": False, "data": None}),
        }
    ),
    Dict(
        {
            "entity": Dict({"id": "git/0", "type": EntityType.UNIT}),
            "filter": Dict({"entity_id": "git", "entity_type": EntityType.APPLICATION}),
            "delta": FAKE_DELTA_UNIT_STARTED,
            "entity_status": Dict(
                {"status": "active", "message": "", "vca_status": "active"}
            ),
            "db": Dict(
                {
                    "written": True,
                    "data": Dict(
                        {
                            "message": "",
                            "entity": "unit",
                            "vca_status": "active",
                            "n2vc_status": N2VCDeploymentStatus.COMPLETED,
                        }
                    ),
                }
            ),
        }
    ),
    Dict(
        {
            "entity": Dict({"id": "git/0", "type": EntityType.UNIT}),
            "filter": Dict({"entity_id": "1", "entity_type": EntityType.ACTION}),
            "delta": FAKE_DELTA_UNIT_STARTED,
            "entity_status": Dict(
                {"status": "active", "message": "", "vca_status": "active"}
            ),
            "db": Dict({"written": False, "data": None}),
        }
    ),
    Dict(
        {
            "entity": Dict({"id": "git", "type": EntityType.APPLICATION}),
            "filter": Dict({"entity_id": "git", "entity_type": EntityType.APPLICATION}),
            "delta": FAKE_DELTA_APPLICATION_MAINTENANCE,
            "entity_status": Dict(
                {
                    "status": "maintenance",
                    "message": "installing charm software",
                    "vca_status": "maintenance",
                }
            ),
            "db": Dict(
                {
                    "written": True,
                    "data": Dict(
                        {
                            "message": "installing charm software",
                            "entity": "application",
                            "vca_status": "maintenance",
                            "n2vc_status": N2VCDeploymentStatus.RUNNING,
                        }
                    ),
                }
            ),
        }
    ),
    Dict(
        {
            "entity": Dict({"id": "git", "type": EntityType.APPLICATION}),
            "filter": Dict({"entity_id": "2", "entity_type": EntityType.MACHINE}),
            "delta": FAKE_DELTA_APPLICATION_MAINTENANCE,
            "entity_status": Dict(
                {
                    "status": "maintenance",
                    "message": "installing charm software",
                    "vca_status": "maintenance",
                }
            ),
            "db": Dict({"written": False, "data": None}),
        }
    ),
    Dict(
        {
            "entity": Dict({"id": "git", "type": EntityType.APPLICATION}),
            "filter": Dict({"entity_id": "git", "entity_type": EntityType.APPLICATION}),
            "delta": FAKE_DELTA_APPLICATION_ACTIVE,
            "entity_status": Dict(
                {"status": "active", "message": "Ready!", "vca_status": "active"}
            ),
            "db": Dict(
                {
                    "written": True,
                    "data": Dict(
                        {
                            "message": "Ready!",
                            "entity": "application",
                            "vca_status": "active",
                            "n2vc_status": N2VCDeploymentStatus.COMPLETED,
                        }
                    ),
                }
            ),
        }
    ),
    Dict(
        {
            "entity": Dict({"id": "git", "type": EntityType.APPLICATION}),
            "filter": Dict({"entity_id": "1", "entity_type": EntityType.ACTION}),
            "delta": FAKE_DELTA_APPLICATION_ACTIVE,
            "entity_status": Dict(
                {"status": "active", "message": "Ready!", "vca_status": "active"}
            ),
            "db": Dict({"written": False, "data": None}),
        }
    ),
    Dict(
        {
            "entity": Dict({"id": "1", "type": EntityType.ACTION}),
            "filter": Dict({"entity_id": "1", "entity_type": EntityType.ACTION}),
            "delta": FAKE_DELTA_ACTION_COMPLETED,
            "entity_status": Dict(
                {
                    "status": "completed",
                    "message": "completed",
                    "vca_status": "completed",
                }
            ),
            "db": Dict(
                {
                    "written": True,
                    "data": Dict(
                        {
                            "message": "completed",
                            "entity": "action",
                            "vca_status": "completed",
                            "n2vc_status": N2VCDeploymentStatus.COMPLETED,
                        }
                    ),
                }
            ),
        }
    ),
    Dict(
        {
            "entity": Dict({"id": "git", "type": EntityType.ACTION}),
            "filter": Dict({"entity_id": "1", "entity_type": EntityType.MACHINE}),
            "delta": FAKE_DELTA_ACTION_COMPLETED,
            "entity_status": Dict(
                {
                    "status": "completed",
                    "message": "completed",
                    "vca_status": "completed",
                }
            ),
            "db": Dict({"written": False, "data": None}),
        }
    ),
]
