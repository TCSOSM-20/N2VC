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
from unittest import mock
from unittest.mock import Mock

import asynctest

from n2vc.exceptions import N2VCTimeoutException
from n2vc.juju_observer import JujuModelObserver, _Entity


class FakeObject:
    def __init__(self):
        self.complete = True


class JujuModelObserverTest(asynctest.TestCase):
    def setUp(self):
        self.n2vc = Mock()
        self.model = Mock()
        self.juju_observer = JujuModelObserver(n2vc=self.n2vc, model=self.model)
        self.loop = asyncio.new_event_loop()

    def test_wait_no_retries(self):
        obj = FakeObject()
        entity = _Entity(entity_id="eid-1", entity_type="fake", obj=obj, db_dict={})
        result = self.loop.run_until_complete(
            self.juju_observer._wait_for_entity(
                entity=entity,
                field_to_check="complete",
                final_states_list=[True],
                progress_timeout=None,
                total_timeout=None,
            )
        )
        self.assertEqual(result, 0)

    @mock.patch("n2vc.juju_observer.asyncio.wait_for")
    def test_wait_default_values(self, wait_for):
        wait_for.return_value = asyncio.Future()
        wait_for.return_value.set_result(None)
        obj = FakeObject()
        obj.complete = False
        entity = _Entity(entity_id="eid-1", entity_type="fake", obj=obj, db_dict={})
        with self.assertRaises(N2VCTimeoutException):
            self.loop.run_until_complete(
                self.juju_observer._wait_for_entity(
                    entity=entity,
                    field_to_check="complete",
                    final_states_list=[True],
                    progress_timeout=None,
                    total_timeout=None,
                )
            )
        wait_for.assert_called_once_with(fut=mock.ANY, timeout=3600.0)

    @mock.patch("n2vc.juju_observer.asyncio.wait_for")
    def test_wait_default_progress(self, wait_for):
        wait_for.return_value = asyncio.Future()
        wait_for.return_value.set_result(None)
        obj = FakeObject()
        obj.complete = False
        entity = _Entity(entity_id="eid-1", entity_type="fake", obj=obj, db_dict={})
        with self.assertRaises(N2VCTimeoutException):
            self.loop.run_until_complete(
                self.juju_observer._wait_for_entity(
                    entity=entity,
                    field_to_check="complete",
                    final_states_list=[True],
                    progress_timeout=4000,
                    total_timeout=None,
                )
            )
        wait_for.assert_called_once_with(fut=mock.ANY, timeout=3600.0)

    @mock.patch("n2vc.juju_observer.asyncio.wait_for")
    def test_wait_default_total(self, wait_for):
        wait_for.return_value = asyncio.Future()
        wait_for.return_value.set_result(None)
        obj = FakeObject()
        obj.complete = False
        entity = _Entity(entity_id="eid-1", entity_type="fake", obj=obj, db_dict={})
        with self.assertRaises(N2VCTimeoutException):
            self.loop.run_until_complete(
                self.juju_observer._wait_for_entity(
                    entity=entity,
                    field_to_check="complete",
                    final_states_list=[True],
                    progress_timeout=None,
                    total_timeout=4000.0,
                )
            )
        wait_for.assert_called_once_with(fut=mock.ANY, timeout=3600.0)

    @mock.patch("n2vc.juju_observer.asyncio.wait_for")
    def test_wait_total_less_than_progress_timeout(self, wait_for):
        wait_for.return_value = asyncio.Future()
        wait_for.return_value.set_result(None)
        obj = FakeObject()
        obj.complete = False
        entity = _Entity(entity_id="eid-1", entity_type="fake", obj=obj, db_dict={})
        with self.assertRaises(N2VCTimeoutException):
            self.loop.run_until_complete(
                self.juju_observer._wait_for_entity(
                    entity=entity,
                    field_to_check="complete",
                    final_states_list=[True],
                    progress_timeout=4500.0,
                    total_timeout=3000.0,
                )
            )
        wait_for.assert_called_once_with(fut=mock.ANY, timeout=3000.0)

    @mock.patch("n2vc.juju_observer.asyncio.wait_for")
    def test_wait_progress_less_than_total_timeout(self, wait_for):
        wait_for.return_value = asyncio.Future()
        wait_for.return_value.set_result(None)
        obj = FakeObject()
        obj.complete = False
        entity = _Entity(entity_id="eid-1", entity_type="fake", obj=obj, db_dict={})
        with self.assertRaises(N2VCTimeoutException):
            self.loop.run_until_complete(
                self.juju_observer._wait_for_entity(
                    entity=entity,
                    field_to_check="complete",
                    final_states_list=[True],
                    progress_timeout=1500.0,
                    total_timeout=3000.0,
                )
            )
        wait_for.assert_called_once_with(fut=mock.ANY, timeout=1500.0)

    def test_wait_negative_timeout(self):
        obj = FakeObject()
        entity = _Entity(entity_id="eid-1", entity_type="fake", obj=obj, db_dict={})
        with self.assertRaises(N2VCTimeoutException):
            self.loop.run_until_complete(
                self.juju_observer._wait_for_entity(
                    entity=entity,
                    field_to_check="complete",
                    final_states_list=[True],
                    progress_timeout=None,
                    total_timeout=-1000,
                )
            )
