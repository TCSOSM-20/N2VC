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

import asynctest
import asyncio

from unittest import mock
from unittest.mock import Mock
from n2vc.juju_watcher import JujuModelWatcher
from n2vc.utils import EntityType
from n2vc.exceptions import EntityInvalidException
from .utils import FakeN2VC, AsyncMock, Deltas, FakeWatcher


class JujuWatcherTest(asynctest.TestCase):
    def setUp(self):
        self.n2vc = FakeN2VC()
        self.model = Mock()
        self.loop = asyncio.new_event_loop()

    def test_get_status(self):
        tests = Deltas
        for test in tests:
            (status, message, vca_status) = JujuModelWatcher.get_status(
                test.delta, test.entity.type
            )
            self.assertEqual(status, test.entity_status.status)
            self.assertEqual(message, test.entity_status.message)
            self.assertEqual(vca_status, test.entity_status.vca_status)

    @mock.patch("n2vc.juju_watcher.client.AllWatcherFacade.from_connection")
    def test_model_watcher(self, allwatcher):
        tests = Deltas
        allwatcher.return_value = FakeWatcher()
        for test in tests:
            with self.assertRaises(asyncio.TimeoutError):
                allwatcher.return_value.delta_to_return = [test.delta]
                self.loop.run_until_complete(
                    JujuModelWatcher.model_watcher(
                        self.model,
                        test.filter.entity_id,
                        test.filter.entity_type,
                        timeout=0,
                        db_dict={"something"},
                        n2vc=self.n2vc,
                    )
                )

            self.assertEqual(self.n2vc.last_written_values, test.db.data)
            self.n2vc.last_written_values = None

    @mock.patch("n2vc.juju_watcher.asyncio.wait")
    @mock.patch("n2vc.juju_watcher.EntityType.get_entity")
    def test_wait_for(self, get_entity, wait):
        wait.return_value = asyncio.Future()
        wait.return_value.set_result(None)
        get_entity.return_value = EntityType.MACHINE

        machine = AsyncMock()
        self.loop.run_until_complete(JujuModelWatcher.wait_for(self.model, machine))

    @mock.patch("n2vc.juju_watcher.asyncio.wait")
    @mock.patch("n2vc.juju_watcher.EntityType.get_entity")
    def test_wait_for_exception(self, get_entity, wait):
        wait.return_value = asyncio.Future()
        wait.return_value.set_result(None)
        wait.side_effect = Exception("error")
        get_entity.return_value = EntityType.MACHINE

        machine = AsyncMock()
        with self.assertRaises(Exception):
            self.loop.run_until_complete(JujuModelWatcher.wait_for(self.model, machine))

    def test_wait_for_invalid_entity_exception(self):
        with self.assertRaises(EntityInvalidException):
            self.loop.run_until_complete(
                JujuModelWatcher.wait_for(self.model, AsyncMock(), total_timeout=0)
            )
