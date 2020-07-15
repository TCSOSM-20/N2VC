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
import asynctest
from n2vc.n2vc_juju_conn import N2VCJujuConnector
from osm_common import fslocal
from n2vc.exceptions import (
    JujuK8sProxycharmNotSupported,
    N2VCBadArgumentsException,
    N2VCException,
)


class N2VCJujuConnTestCase(asynctest.TestCase):
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
        db = {}
        vca_config = {
            "secret": "secret",
            "api_proxy": "api_proxy",
            "cloud": "cloud",
            "k8s_cloud": "k8s_cloud",
        }

        logging.disable(logging.CRITICAL)

        self.n2vc = N2VCJujuConnector(
            db=db,
            fs=fslocal.FsLocal(),
            log=None,
            loop=loop,
            url="2.2.2.2:17070",
            username="admin",
            vca_config=vca_config,
            on_update_db=None,
        )


@asynctest.mock.patch("osm_common.fslocal.FsLocal.file_exists")
@asynctest.mock.patch(
    "osm_common.fslocal.FsLocal.path", new_callable=asynctest.PropertyMock, create=True
)
@asynctest.mock.patch("n2vc.libjuju.Libjuju.deploy_charm")
@asynctest.mock.patch("n2vc.libjuju.Libjuju.add_model")
class K8sProxyCharmsTest(N2VCJujuConnTestCase):
    def setUp(self):
        super(K8sProxyCharmsTest, self).setUp()

    def test_success(
        self, mock_add_model, mock_deploy_charm, mock_path, mock_file_exists,
    ):
        mock_file_exists.return_value = True
        mock_path.return_value = "/path"
        ee_id = self.loop.run_until_complete(
            self.n2vc.install_k8s_proxy_charm(
                "charm", "nsi-id.ns-id.vnf-id.vdu", "////path/", {},
            )
        )

        mock_add_model.assert_called_once_with("ns-id-k8s", "k8s_cloud")
        mock_deploy_charm.assert_called_once_with(
            model_name="ns-id-k8s",
            application_name="app-vnf-vnf-id-vdu-vdu",
            path="/path/path/",
            machine_id=None,
            db_dict={},
            progress_timeout=None,
            total_timeout=None,
            config=None,
        )
        self.assertEqual(ee_id, "ns-id-k8s.app-vnf-vnf-id-vdu-vdu.k8s")

    @asynctest.mock.patch(
        "n2vc.n2vc_juju_conn.N2VCJujuConnector.k8s_cloud",
        new_callable=asynctest.PropertyMock,
        create=True,
    )
    def test_no_k8s_cloud(
        self,
        mock_k8s_cloud,
        mock_add_model,
        mock_deploy_charm,
        mock_path,
        mock_file_exists,
    ):
        mock_k8s_cloud.return_value = None
        with self.assertRaises(JujuK8sProxycharmNotSupported):
            ee_id = self.loop.run_until_complete(
                self.n2vc.install_k8s_proxy_charm(
                    "charm", "nsi-id.ns-id.vnf-id.vdu", "/path/", {},
                )
            )
            self.assertIsNone(ee_id)

    def test_no_artifact_path(
        self, mock_add_model, mock_deploy_charm, mock_path, mock_file_exists,
    ):
        with self.assertRaises(N2VCBadArgumentsException):
            ee_id = self.loop.run_until_complete(
                self.n2vc.install_k8s_proxy_charm(
                    "charm", "nsi-id.ns-id.vnf-id.vdu", "", {},
                )
            )
            self.assertIsNone(ee_id)

    def test_no_db(
        self, mock_add_model, mock_deploy_charm, mock_path, mock_file_exists,
    ):
        with self.assertRaises(N2VCBadArgumentsException):
            ee_id = self.loop.run_until_complete(
                self.n2vc.install_k8s_proxy_charm(
                    "charm", "nsi-id.ns-id.vnf-id.vdu", "/path/", None,
                )
            )
            self.assertIsNone(ee_id)

    def test_file_not_exists(
        self, mock_add_model, mock_deploy_charm, mock_path, mock_file_exists,
    ):
        mock_file_exists.return_value = False
        with self.assertRaises(N2VCBadArgumentsException):
            ee_id = self.loop.run_until_complete(
                self.n2vc.install_k8s_proxy_charm(
                    "charm", "nsi-id.ns-id.vnf-id.vdu", "/path/", {},
                )
            )
            self.assertIsNone(ee_id)

    def test_exception(
        self, mock_add_model, mock_deploy_charm, mock_path, mock_file_exists,
    ):
        mock_file_exists.return_value = True
        mock_path.return_value = "/path"
        mock_deploy_charm.side_effect = Exception()
        with self.assertRaises(N2VCException):
            ee_id = self.loop.run_until_complete(
                self.n2vc.install_k8s_proxy_charm(
                    "charm", "nsi-id.ns-id.vnf-id.vdu", "path/", {},
                )
            )
            self.assertIsNone(ee_id)
