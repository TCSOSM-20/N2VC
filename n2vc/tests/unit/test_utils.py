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

from unittest import TestCase

from n2vc.utils import Dict, EntityType, JujuStatusToOSM, N2VCDeploymentStatus, DB_DATA
from juju.machine import Machine
from juju.application import Application
from juju.action import Action
from juju.unit import Unit


class UtilsTest(TestCase):
    def test_dict(self):
        example = Dict({"key": "value"})
        self.assertEqual(example["key"], example.key)

    def test_entity_type(self):
        self.assertFalse(EntityType.has_value("machine2"))
        values = [Machine, Application, Action, Unit]
        for value in values:
            self.assertTrue(EntityType.has_value(value))

        self.assertEqual(EntityType.MACHINE, EntityType.get_entity(Machine))
        self.assertEqual(EntityType.APPLICATION, EntityType.get_entity(Application))
        self.assertEqual(EntityType.UNIT, EntityType.get_entity(Unit))
        self.assertEqual(EntityType.ACTION, EntityType.get_entity(Action))

    def test_juju_status_to_osm(self):
        tests = [
            {
                "entity_type": EntityType.MACHINE,
                "status": [
                    {"juju": "pending", "osm": N2VCDeploymentStatus.PENDING},
                    {"juju": "started", "osm": N2VCDeploymentStatus.COMPLETED},
                ],
            },
            {
                "entity_type": EntityType.APPLICATION,
                "status": [
                    {"juju": "waiting", "osm": N2VCDeploymentStatus.RUNNING},
                    {"juju": "maintenance", "osm": N2VCDeploymentStatus.RUNNING},
                    {"juju": "blocked", "osm": N2VCDeploymentStatus.RUNNING},
                    {"juju": "error", "osm": N2VCDeploymentStatus.FAILED},
                    {"juju": "active", "osm": N2VCDeploymentStatus.COMPLETED},
                ],
            },
            {
                "entity_type": EntityType.UNIT,
                "status": [
                    {"juju": "waiting", "osm": N2VCDeploymentStatus.RUNNING},
                    {"juju": "maintenance", "osm": N2VCDeploymentStatus.RUNNING},
                    {"juju": "blocked", "osm": N2VCDeploymentStatus.RUNNING},
                    {"juju": "error", "osm": N2VCDeploymentStatus.FAILED},
                    {"juju": "active", "osm": N2VCDeploymentStatus.COMPLETED},
                ],
            },
            {
                "entity_type": EntityType.ACTION,
                "status": [
                    {"juju": "running", "osm": N2VCDeploymentStatus.RUNNING},
                    {"juju": "completed", "osm": N2VCDeploymentStatus.COMPLETED},
                ],
            },
        ]

        for test in tests:
            entity_type = test["entity_type"]
            self.assertTrue(entity_type in JujuStatusToOSM)

            for status in test["status"]:
                juju_status = status["juju"]
                osm_status = status["osm"]
                self.assertTrue(juju_status in JujuStatusToOSM[entity_type])
                self.assertEqual(osm_status, JujuStatusToOSM[entity_type][juju_status])

    def test_db_data(self):
        self.assertEqual(DB_DATA.api_endpoints.table, "admin")
        self.assertEqual(DB_DATA.api_endpoints.filter, {"_id": "juju"})
        self.assertEqual(DB_DATA.api_endpoints.key, "api_endpoints")
