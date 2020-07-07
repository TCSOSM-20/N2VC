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
import asynctest
import juju
from juju.errors import JujuAPIError
import logging
from .utils import FakeN2VC, FakeMachine, FakeApplication
from n2vc.libjuju import Libjuju
from n2vc.exceptions import (
    JujuControllerFailedConnecting,
    JujuModelAlreadyExists,
    JujuMachineNotFound,
    JujuApplicationNotFound,
    JujuActionNotFound,
    JujuApplicationExists,
)


class LibjujuTestCase(asynctest.TestCase):
    @asynctest.mock.patch("juju.controller.Controller.update_endpoints")
    @asynctest.mock.patch("juju.client.connector.Connector.connect")
    @asynctest.mock.patch("juju.controller.Controller.connection")
    @asynctest.mock.patch("n2vc.libjuju.Libjuju._get_api_endpoints_db")
    def setUp(
        self,
        mock__get_api_endpoints_db=None,
        mock_connection=None,
        mock_connect=None,
        mock_update_endpoints=None,
    ):
        loop = asyncio.get_event_loop()
        n2vc = FakeN2VC()
        mock__get_api_endpoints_db.return_value = ["127.0.0.1:17070"]
        endpoints = "127.0.0.1:17070"
        username = "admin"
        password = "secret"
        cacert = """
    -----BEGIN CERTIFICATE-----
    SOMECERT
    -----END CERTIFICATE-----"""
        self.libjuju = Libjuju(
            endpoints,
            "192.168.0.155:17070",
            username,
            password,
            cacert,
            loop,
            log=None,
            db={"get_one": []},
            n2vc=n2vc,
            apt_mirror="192.168.0.100",
            enable_os_upgrade=True,
        )
        logging.disable(logging.CRITICAL)
        loop.run_until_complete(self.libjuju.disconnect())


@asynctest.mock.patch("juju.controller.Controller.connect")
@asynctest.mock.patch(
    "juju.controller.Controller.api_endpoints",
    new_callable=asynctest.CoroutineMock(return_value=["127.0.0.1:17070"]),
)
@asynctest.mock.patch("n2vc.libjuju.Libjuju._update_api_endpoints_db")
class GetControllerTest(LibjujuTestCase):
    def setUp(self):
        super(GetControllerTest, self).setUp()

    def test_diff_endpoint(
        self, mock__update_api_endpoints_db, mock_api_endpoints, mock_connect
    ):
        self.libjuju.endpoints = []
        controller = self.loop.run_until_complete(self.libjuju.get_controller())
        mock__update_api_endpoints_db.assert_called_once_with(["127.0.0.1:17070"])
        self.assertIsInstance(controller, juju.controller.Controller)

    @asynctest.mock.patch("n2vc.libjuju.Libjuju.disconnect_controller")
    def test_exception(
        self,
        mock_disconnect_controller,
        mock__update_api_endpoints_db,
        mock_api_endpoints,
        mock_connect,
    ):
        self.libjuju.endpoints = []
        mock__update_api_endpoints_db.side_effect = Exception()
        with self.assertRaises(JujuControllerFailedConnecting):
            controller = self.loop.run_until_complete(self.libjuju.get_controller())
            self.assertIsNone(controller)
            mock_disconnect_controller.assert_called_once()

    def test_same_endpoint_get_controller(
        self, mock__update_api_endpoints_db, mock_api_endpoints, mock_connect
    ):
        self.libjuju.endpoints = ["127.0.0.1:17070"]
        controller = self.loop.run_until_complete(self.libjuju.get_controller())
        mock__update_api_endpoints_db.assert_not_called()
        self.assertIsInstance(controller, juju.controller.Controller)


class DisconnectTest(LibjujuTestCase):
    def setUp(self):
        super(DisconnectTest, self).setUp()

    @asynctest.mock.patch("juju.model.Model.disconnect")
    def test_disconnect_model(self, mock_disconnect):
        self.loop.run_until_complete(self.libjuju.disconnect_model(juju.model.Model()))
        mock_disconnect.assert_called_once()

    @asynctest.mock.patch("juju.controller.Controller.disconnect")
    def test_disconnect_controller(self, mock_disconnect):
        self.loop.run_until_complete(
            self.libjuju.disconnect_controller(juju.controller.Controller())
        )
        mock_disconnect.assert_called_once()


@asynctest.mock.patch("n2vc.libjuju.Libjuju.get_controller")
@asynctest.mock.patch("n2vc.libjuju.Libjuju.model_exists")
@asynctest.mock.patch("juju.controller.Controller.add_model")
@asynctest.mock.patch("n2vc.libjuju.Libjuju.disconnect_controller")
@asynctest.mock.patch("n2vc.libjuju.Libjuju.disconnect_model")
class AddModelTest(LibjujuTestCase):
    def setUp(self):
        super(AddModelTest, self).setUp()

    def test_existing_model(
        self,
        mock_disconnect_model,
        mock_disconnect_controller,
        mock_add_model,
        mock_model_exists,
        mock_get_controller,
    ):
        mock_model_exists.return_value = True

        with self.assertRaises(JujuModelAlreadyExists):
            self.loop.run_until_complete(
                self.libjuju.add_model("existing_model", "cloud")
            )

            mock_disconnect_controller.assert_called()

    # TODO Check two job executing at the same time and one returning without doing anything.

    def test_non_existing_model(
        self,
        mock_disconnect_model,
        mock_disconnect_controller,
        mock_add_model,
        mock_model_exists,
        mock_get_controller,
    ):
        mock_model_exists.return_value = False
        mock_get_controller.return_value = juju.controller.Controller()

        self.loop.run_until_complete(
            self.libjuju.add_model("nonexisting_model", "cloud")
        )

        mock_add_model.assert_called_once()
        mock_disconnect_controller.assert_called()
        mock_disconnect_model.assert_called()


@asynctest.mock.patch("juju.controller.Controller.get_model")
class GetModelTest(LibjujuTestCase):
    def setUp(self):
        super(GetModelTest, self).setUp()

    def test_get_model(
        self, mock_get_model,
    ):
        mock_get_model.return_value = juju.model.Model()
        model = self.loop.run_until_complete(
            self.libjuju.get_model(juju.controller.Controller(), "model")
        )
        self.assertIsInstance(model, juju.model.Model)


@asynctest.mock.patch("n2vc.libjuju.Libjuju.get_controller")
@asynctest.mock.patch("juju.controller.Controller.list_models")
class ModelExistsTest(LibjujuTestCase):
    def setUp(self):
        super(ModelExistsTest, self).setUp()

    async def test_existing_model(
        self, mock_list_models, mock_get_controller,
    ):
        mock_list_models.return_value = ["existing_model"]
        self.assertTrue(
            await self.libjuju.model_exists(
                "existing_model", juju.controller.Controller()
            )
        )

    @asynctest.mock.patch("n2vc.libjuju.Libjuju.disconnect_controller")
    async def test_no_controller(
        self, mock_disconnect_controller, mock_list_models, mock_get_controller,
    ):
        mock_list_models.return_value = ["existing_model"]
        mock_get_controller.return_value = juju.controller.Controller()
        self.assertTrue(await self.libjuju.model_exists("existing_model"))
        mock_disconnect_controller.assert_called_once()

    async def test_non_existing_model(
        self, mock_list_models, mock_get_controller,
    ):
        mock_list_models.return_value = ["existing_model"]
        self.assertFalse(
            await self.libjuju.model_exists(
                "not_existing_model", juju.controller.Controller()
            )
        )


@asynctest.mock.patch("n2vc.libjuju.Libjuju.get_controller")
@asynctest.mock.patch("n2vc.libjuju.Libjuju.get_model")
@asynctest.mock.patch("n2vc.libjuju.Libjuju.disconnect_model")
@asynctest.mock.patch("n2vc.libjuju.Libjuju.disconnect_controller")
@asynctest.mock.patch("juju.model.Model.get_status")
class GetModelStatusTest(LibjujuTestCase):
    def setUp(self):
        super(GetModelStatusTest, self).setUp()

    def test_success(
        self,
        mock_get_status,
        mock_disconnect_controller,
        mock_disconnect_model,
        mock_get_model,
        mock_get_controller,
    ):
        mock_get_model.return_value = juju.model.Model()
        mock_get_status.return_value = {"status"}

        status = self.loop.run_until_complete(self.libjuju.get_model_status("model"))

        mock_get_status.assert_called_once()
        mock_disconnect_controller.assert_called_once()
        mock_disconnect_model.assert_called_once()

        self.assertEqual(status, {"status"})

    def test_excpetion(
        self,
        mock_get_status,
        mock_disconnect_controller,
        mock_disconnect_model,
        mock_get_model,
        mock_get_controller,
    ):
        mock_get_model.return_value = juju.model.Model()
        mock_get_status.side_effect = Exception()

        with self.assertRaises(Exception):
            status = self.loop.run_until_complete(
                self.libjuju.get_model_status("model")
            )

            mock_disconnect_controller.assert_called_once()
            mock_disconnect_model.assert_called_once()

            self.assertIsNone(status)


@asynctest.mock.patch("n2vc.libjuju.Libjuju.get_controller")
@asynctest.mock.patch("n2vc.libjuju.Libjuju.get_model")
@asynctest.mock.patch("n2vc.libjuju.Libjuju.disconnect_model")
@asynctest.mock.patch("n2vc.libjuju.Libjuju.disconnect_controller")
@asynctest.mock.patch("juju.model.Model.get_machines")
@asynctest.mock.patch("juju.model.Model.add_machine")
@asynctest.mock.patch("n2vc.juju_watcher.JujuModelWatcher.wait_for")
class CreateMachineTest(LibjujuTestCase):
    def setUp(self):
        super(CreateMachineTest, self).setUp()

    def test_existing_machine(
        self,
        mock_wait_for,
        mock_add_machine,
        mock_get_machines,
        mock_disconnect_controller,
        mock_disconnect_model,
        mock_get_model,
        mock_get_controller,
    ):
        mock_get_model.return_value = juju.model.Model()
        mock_get_machines.return_value = {"existing_machine": FakeMachine()}
        machine, bool_res = self.loop.run_until_complete(
            self.libjuju.create_machine("model", "existing_machine")
        )

        self.assertIsInstance(machine, FakeMachine)
        self.assertFalse(bool_res)

        mock_disconnect_controller.assert_called()
        mock_disconnect_model.assert_called()

    def test_non_existing_machine(
        self,
        mock_wait_for,
        mock_add_machine,
        mock_get_machines,
        mock_disconnect_controller,
        mock_disconnect_model,
        mock_get_model,
        mock_get_controller,
    ):
        mock_get_model.return_value = juju.model.Model()
        with self.assertRaises(JujuMachineNotFound):
            machine, bool_res = self.loop.run_until_complete(
                self.libjuju.create_machine("model", "non_existing_machine")
            )
            self.assertIsNone(machine)
            self.assertIsNone(bool_res)

            mock_disconnect_controller.assert_called()
            mock_disconnect_model.assert_called()

    def test_no_machine(
        self,
        mock_wait_for,
        mock_add_machine,
        mock_get_machines,
        mock_disconnect_controller,
        mock_disconnect_model,
        mock_get_model,
        mock_get_controller,
    ):
        mock_get_model.return_value = juju.model.Model()
        mock_add_machine.return_value = FakeMachine()

        machine, bool_res = self.loop.run_until_complete(
            self.libjuju.create_machine("model")
        )

        self.assertIsInstance(machine, FakeMachine)
        self.assertTrue(bool_res)

        mock_wait_for.assert_called_once()
        mock_add_machine.assert_called_once()

        mock_disconnect_controller.assert_called()
        mock_disconnect_model.assert_called()


# TODO test provision machine


@asynctest.mock.patch("n2vc.libjuju.Libjuju.get_controller")
@asynctest.mock.patch("n2vc.libjuju.Libjuju.get_model")
@asynctest.mock.patch("n2vc.libjuju.Libjuju.disconnect_model")
@asynctest.mock.patch("n2vc.libjuju.Libjuju.disconnect_controller")
@asynctest.mock.patch(
    "juju.model.Model.applications", new_callable=asynctest.PropertyMock
)
@asynctest.mock.patch("juju.model.Model.machines", new_callable=asynctest.PropertyMock)
@asynctest.mock.patch("juju.model.Model.deploy")
@asynctest.mock.patch("n2vc.juju_watcher.JujuModelWatcher.wait_for")
@asynctest.mock.patch("n2vc.libjuju.Libjuju.create_machine")
class DeployCharmTest(LibjujuTestCase):
    def setUp(self):
        super(DeployCharmTest, self).setUp()

    def test_existing_app(
        self,
        mock_create_machine,
        mock_wait_for,
        mock_deploy,
        mock_machines,
        mock_applications,
        mock_disconnect_controller,
        mock_disconnect_model,
        mock_get_model,
        mock_get_controller,
    ):
        mock_get_model.return_value = juju.model.Model()
        mock_applications.return_value = {"existing_app"}

        with self.assertRaises(JujuApplicationExists):
            application = self.loop.run_until_complete(
                self.libjuju.deploy_charm("existing_app", "path", "model", "machine",)
            )
            self.assertIsNone(application)

            mock_disconnect_controller.assert_called()
            mock_disconnect_model.assert_called()

    def test_non_existing_machine(
        self,
        mock_create_machine,
        mock_wait_for,
        mock_deploy,
        mock_machines,
        mock_applications,
        mock_disconnect_controller,
        mock_disconnect_model,
        mock_get_model,
        mock_get_controller,
    ):
        mock_get_model.return_value = juju.model.Model()
        mock_machines.return_value = {"existing_machine": FakeMachine()}
        with self.assertRaises(JujuMachineNotFound):
            application = self.loop.run_until_complete(
                self.libjuju.deploy_charm("app", "path", "model", "machine",)
            )

            self.assertIsNone(application)

            mock_disconnect_controller.assert_called()
            mock_disconnect_model.assert_called()

    def test_2_units(
        self,
        mock_create_machine,
        mock_wait_for,
        mock_deploy,
        mock_machines,
        mock_applications,
        mock_disconnect_controller,
        mock_disconnect_model,
        mock_get_model,
        mock_get_controller,
    ):
        mock_get_model.return_value = juju.model.Model()
        mock_machines.return_value = {"existing_machine": FakeMachine()}
        mock_create_machine.return_value = (FakeMachine(), "other")
        mock_deploy.return_value = FakeApplication()
        application = self.loop.run_until_complete(
            self.libjuju.deploy_charm(
                "app", "path", "model", "existing_machine", num_units=2,
            )
        )

        self.assertIsInstance(application, FakeApplication)

        mock_deploy.assert_called_once()
        mock_wait_for.assert_called_once()

        mock_create_machine.assert_called_once()

        mock_disconnect_controller.assert_called()
        mock_disconnect_model.assert_called()

    def test_1_unit(
        self,
        mock_create_machine,
        mock_wait_for,
        mock_deploy,
        mock_machines,
        mock_applications,
        mock_disconnect_controller,
        mock_disconnect_model,
        mock_get_model,
        mock_get_controller,
    ):
        mock_get_model.return_value = juju.model.Model()
        mock_machines.return_value = {"existing_machine": FakeMachine()}
        mock_deploy.return_value = FakeApplication()
        application = self.loop.run_until_complete(
            self.libjuju.deploy_charm("app", "path", "model", "existing_machine")
        )

        self.assertIsInstance(application, FakeApplication)

        mock_deploy.assert_called_once()
        mock_wait_for.assert_called_once()

        mock_disconnect_controller.assert_called()
        mock_disconnect_model.assert_called()


@asynctest.mock.patch(
    "juju.model.Model.applications", new_callable=asynctest.PropertyMock
)
class GetApplicationTest(LibjujuTestCase):
    def setUp(self):
        super(GetApplicationTest, self).setUp()

    def test_existing_application(
        self, mock_applications,
    ):
        mock_applications.return_value = {"existing_app": "exists"}
        model = juju.model.Model()
        result = self.libjuju._get_application(model, "existing_app")
        self.assertEqual(result, "exists")

    def test_non_existing_application(
        self, mock_applications,
    ):
        mock_applications.return_value = {"existing_app": "exists"}
        model = juju.model.Model()
        result = self.libjuju._get_application(model, "nonexisting_app")
        self.assertIsNone(result)


@asynctest.mock.patch("n2vc.libjuju.Libjuju.get_controller")
@asynctest.mock.patch("n2vc.libjuju.Libjuju.get_model")
@asynctest.mock.patch("n2vc.libjuju.Libjuju.disconnect_model")
@asynctest.mock.patch("n2vc.libjuju.Libjuju.disconnect_controller")
@asynctest.mock.patch("n2vc.libjuju.Libjuju._get_application")
@asynctest.mock.patch("n2vc.juju_watcher.JujuModelWatcher.wait_for")
@asynctest.mock.patch("juju.model.Model.get_action_output")
@asynctest.mock.patch("juju.model.Model.get_action_status")
class ExecuteActionTest(LibjujuTestCase):
    def setUp(self):
        super(ExecuteActionTest, self).setUp()

    def test_no_application(
        self,
        mock_get_action_status,
        mock_get_action_output,
        mock_wait_for,
        mock__get_application,
        mock_disconnect_controller,
        mock_disconnect_model,
        mock_get_model,
        mock_get_controller,
    ):
        mock__get_application.return_value = None
        mock_get_model.return_value = juju.model.Model()

        with self.assertRaises(JujuApplicationNotFound):
            output, status = self.loop.run_until_complete(
                self.libjuju.execute_action("app", "model", "action",)
            )
            self.assertIsNone(output)
            self.assertIsNone(status)

            mock_disconnect_controller.assert_called()
            mock_disconnect_model.assert_called()

    def test_no_action(
        self,
        mock_get_action_status,
        mock_get_action_output,
        mock_wait_for,
        mock__get_application,
        mock_disconnect_controller,
        mock_disconnect_model,
        mock_get_model,
        mock_get_controller,
    ):

        mock_get_model.return_value = juju.model.Model()
        mock__get_application.return_value = FakeApplication()
        with self.assertRaises(JujuActionNotFound):
            output, status = self.loop.run_until_complete(
                self.libjuju.execute_action("app", "model", "action",)
            )
            self.assertIsNone(output)
            self.assertIsNone(status)

            mock_disconnect_controller.assert_called()
            mock_disconnect_model.assert_called()

    # TODO no leader unit found exception

    def test_succesful_exec(
        self,
        mock_get_action_status,
        mock_get_action_output,
        mock_wait_for,
        mock__get_application,
        mock_disconnect_controller,
        mock_disconnect_model,
        mock_get_model,
        mock_get_controller,
    ):
        mock_get_model.return_value = juju.model.Model()
        mock__get_application.return_value = FakeApplication()
        mock_get_action_output.return_value = "output"
        mock_get_action_status.return_value = {"id": "status"}
        output, status = self.loop.run_until_complete(
            self.libjuju.execute_action("app", "model", "existing_action")
        )
        self.assertEqual(output, "output")
        self.assertEqual(status, "status")

        mock_wait_for.assert_called_once()

        mock_disconnect_controller.assert_called()
        mock_disconnect_model.assert_called()


@asynctest.mock.patch("n2vc.libjuju.Libjuju.get_controller")
@asynctest.mock.patch("n2vc.libjuju.Libjuju.get_model")
@asynctest.mock.patch("n2vc.libjuju.Libjuju.disconnect_model")
@asynctest.mock.patch("n2vc.libjuju.Libjuju.disconnect_controller")
@asynctest.mock.patch("n2vc.libjuju.Libjuju._get_application")
class GetActionTest(LibjujuTestCase):
    def setUp(self):
        super(GetActionTest, self).setUp()

    def test_exception(
        self,
        mock_get_application,
        mock_disconnect_controller,
        mock_disconnect_model,
        mock_get_model,
        mock_get_controller,
    ):
        mock_get_application.side_effect = Exception()

        with self.assertRaises(Exception):
            actions = self.loop.run_until_complete(
                self.libjuju.get_actions("app", "model")
            )

            self.assertIsNone(actions)
            mock_disconnect_controller.assert_called_once()
            mock_disconnect_model.assert_called_once()

    def test_success(
        self,
        mock_get_application,
        mock_disconnect_controller,
        mock_disconnect_model,
        mock_get_model,
        mock_get_controller,
    ):
        mock_get_application.return_value = FakeApplication()

        actions = self.loop.run_until_complete(self.libjuju.get_actions("app", "model"))

        self.assertEqual(actions, ["existing_action"])

        mock_get_controller.assert_called_once()
        mock_get_model.assert_called_once()
        mock_disconnect_controller.assert_called_once()
        mock_disconnect_model.assert_called_once()


@asynctest.mock.patch("n2vc.libjuju.Libjuju.get_controller")
@asynctest.mock.patch("n2vc.libjuju.Libjuju.get_model")
@asynctest.mock.patch("n2vc.libjuju.Libjuju.disconnect_model")
@asynctest.mock.patch("n2vc.libjuju.Libjuju.disconnect_controller")
@asynctest.mock.patch("juju.model.Model.add_relation")
class AddRelationTest(LibjujuTestCase):
    def setUp(self):
        super(AddRelationTest, self).setUp()

    @asynctest.mock.patch("logging.Logger.warning")
    def test_not_found(
        self,
        mock_warning,
        mock_add_relation,
        mock_disconnect_controller,
        mock_disconnect_model,
        mock_get_model,
        mock_get_controller,
    ):
        # TODO in libjuju.py should this fail only with a log message?
        result = {"error": "not found", "response": "response", "request-id": 1}

        mock_get_model.return_value = juju.model.Model()
        mock_add_relation.side_effect = JujuAPIError(result)

        self.loop.run_until_complete(
            self.libjuju.add_relation(
                "model", "app1", "app2", "relation1", "relation2",
            )
        )

        mock_warning.assert_called_with("Relation not found: not found")
        mock_disconnect_controller.assert_called_once()
        mock_disconnect_model.assert_called_once()

    @asynctest.mock.patch("logging.Logger.warning")
    def test_already_exists(
        self,
        mock_warning,
        mock_add_relation,
        mock_disconnect_controller,
        mock_disconnect_model,
        mock_get_model,
        mock_get_controller,
    ):
        # TODO in libjuju.py should this fail silently?
        result = {"error": "already exists", "response": "response", "request-id": 1}

        mock_get_model.return_value = juju.model.Model()
        mock_add_relation.side_effect = JujuAPIError(result)

        self.loop.run_until_complete(
            self.libjuju.add_relation(
                "model", "app1", "app2", "relation1", "relation2",
            )
        )

        mock_warning.assert_called_with("Relation already exists: already exists")
        mock_disconnect_controller.assert_called_once()
        mock_disconnect_model.assert_called_once()

    def test_exception(
        self,
        mock_add_relation,
        mock_disconnect_controller,
        mock_disconnect_model,
        mock_get_model,
        mock_get_controller,
    ):
        mock_get_model.return_value = juju.model.Model()
        result = {"error": "", "response": "response", "request-id": 1}
        mock_add_relation.side_effect = JujuAPIError(result)

        with self.assertRaises(JujuAPIError):
            self.loop.run_until_complete(
                self.libjuju.add_relation(
                    "model", "app1", "app2", "relation1", "relation2",
                )
            )

            mock_disconnect_controller.assert_called_once()
            mock_disconnect_model.assert_called_once()

    def test_success(
        self,
        mock_add_relation,
        mock_disconnect_controller,
        mock_disconnect_model,
        mock_get_model,
        mock_get_controller,
    ):
        mock_get_model.return_value = juju.model.Model()

        self.loop.run_until_complete(
            self.libjuju.add_relation(
                "model", "app1", "app2", "relation1", "relation2",
            )
        )

        mock_add_relation.assert_called_with(
            relation1="app1:relation1", relation2="app2:relation2"
        )
        mock_disconnect_controller.assert_called_once()
        mock_disconnect_model.assert_called_once()


# TODO destroy_model testcase


@asynctest.mock.patch("juju.model.Model.get_machines")
@asynctest.mock.patch("logging.Logger.debug")
class DestroyMachineTest(LibjujuTestCase):
    def setUp(self):
        super(DestroyMachineTest, self).setUp()

    def test_success(
        self, mock_debug, mock_get_machines,
    ):
        mock_get_machines.side_effect = [
            {"machine": FakeMachine()},
            {"machine": FakeMachine()},
            {},
        ]
        self.loop.run_until_complete(
            self.libjuju.destroy_machine(juju.model.Model(), "machine", 2,)
        )
        calls = [
            asynctest.call("Waiting for machine machine is destroyed"),
            asynctest.call("Machine destroyed: machine"),
        ]
        mock_debug.assert_has_calls(calls)

    def test_no_machine(
        self, mock_debug, mock_get_machines,
    ):
        mock_get_machines.return_value = {}
        self.loop.run_until_complete(
            self.libjuju.destroy_machine(juju.model.Model(), "machine", 2,)
        )
        mock_debug.assert_called_with("Machine not found: machine")


@asynctest.mock.patch("n2vc.libjuju.Libjuju.get_controller")
@asynctest.mock.patch("n2vc.libjuju.Libjuju.get_model")
@asynctest.mock.patch("n2vc.libjuju.Libjuju.disconnect_model")
@asynctest.mock.patch("n2vc.libjuju.Libjuju.disconnect_controller")
@asynctest.mock.patch("n2vc.libjuju.Libjuju._get_application")
class ConfigureApplicationTest(LibjujuTestCase):
    def setUp(self):
        super(ConfigureApplicationTest, self).setUp()

    def test_success(
        self,
        mock_get_application,
        mock_disconnect_controller,
        mock_disconnect_model,
        mock_get_model,
        mock_get_controller,
    ):

        mock_get_application.return_value = FakeApplication()

        self.loop.run_until_complete(
            self.libjuju.configure_application("model", "app", {"config"},)
        )
        mock_get_application.assert_called_once()
        mock_disconnect_controller.assert_called_once()
        mock_disconnect_model.assert_called_once()

    def test_exception(
        self,
        mock_get_application,
        mock_disconnect_controller,
        mock_disconnect_model,
        mock_get_model,
        mock_get_controller,
    ):

        mock_get_application.side_effect = Exception()

        with self.assertRaises(Exception):
            self.loop.run_until_complete(
                self.libjuju.configure_application("model", "app", {"config"},)
            )
            mock_disconnect_controller.assert_called_once()
            mock_disconnect_model.assert_called_once()


# TODO _get_api_endpoints_db test case
# TODO _update_api_endpoints_db test case
# TODO healthcheck test case


@asynctest.mock.patch("n2vc.libjuju.Libjuju.get_controller")
@asynctest.mock.patch("n2vc.libjuju.Libjuju.disconnect_controller")
@asynctest.mock.patch("juju.controller.Controller.list_models")
class ListModelsTest(LibjujuTestCase):
    def setUp(self):
        super(ListModelsTest, self).setUp()

    def test_containing(
        self, mock_list_models, mock_disconnect_controller, mock_get_controller,
    ):
        mock_get_controller.return_value = juju.controller.Controller()
        mock_list_models.return_value = ["existingmodel"]
        models = self.loop.run_until_complete(self.libjuju.list_models("existing"))

        mock_disconnect_controller.assert_called_once()
        self.assertEquals(models, ["existingmodel"])

    def test_not_containing(
        self, mock_list_models, mock_disconnect_controller, mock_get_controller,
    ):
        mock_get_controller.return_value = juju.controller.Controller()
        mock_list_models.return_value = ["existingmodel", "model"]
        models = self.loop.run_until_complete(self.libjuju.list_models("mdl"))

        mock_disconnect_controller.assert_called_once()
        self.assertEquals(models, [])

    def test_no_contains_arg(
        self, mock_list_models, mock_disconnect_controller, mock_get_controller,
    ):
        mock_get_controller.return_value = juju.controller.Controller()
        mock_list_models.return_value = ["existingmodel", "model"]
        models = self.loop.run_until_complete(self.libjuju.list_models())

        mock_disconnect_controller.assert_called_once()
        self.assertEquals(models, ["existingmodel", "model"])
