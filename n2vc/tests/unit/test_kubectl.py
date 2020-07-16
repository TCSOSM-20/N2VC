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

from unittest import TestCase, mock
from n2vc.kubectl import Kubectl
from n2vc.utils import Dict
from kubernetes.client.rest import ApiException

fake_list_services = Dict(
    {
        "items": [
            Dict(
                {
                    "metadata": Dict(
                        {
                            "name": "squid",
                            "namespace": "test",
                            "labels": {"juju-app": "squid"},
                        }
                    ),
                    "spec": Dict(
                        {
                            "cluster_ip": "10.152.183.79",
                            "type": "LoadBalancer",
                            "ports": [
                                Dict(
                                    {
                                        "name": None,
                                        "node_port": None,
                                        "port": 30666,
                                        "protocol": "TCP",
                                        "target_port": 30666,
                                    }
                                )
                            ],
                        }
                    ),
                    "status": Dict(
                        {
                            "load_balancer": Dict(
                                {
                                    "ingress": [
                                        Dict({"hostname": None, "ip": "192.168.0.201"})
                                    ]
                                }
                            )
                        }
                    ),
                }
            )
        ]
    }
)


class FakeCoreV1Api:
    def list_service_for_all_namespaces(self, **kwargs):
        return fake_list_services


class ProvisionerTest(TestCase):
    @mock.patch("n2vc.kubectl.config.load_kube_config")
    @mock.patch("n2vc.kubectl.client.CoreV1Api")
    def setUp(self, mock_core, mock_config):
        mock_core.return_value = mock.MagicMock()
        mock_config.return_value = mock.MagicMock()
        self.kubectl = Kubectl()

    @mock.patch("n2vc.kubectl.client.CoreV1Api")
    def test_get_service(self, mock_corev1api):
        mock_corev1api.return_value = FakeCoreV1Api()
        services = self.kubectl.get_services(
            field_selector="metadata.namespace", label_selector="juju-operator=squid"
        )
        keys = ["name", "cluster_ip", "type", "ports", "external_ip"]
        self.assertTrue(k in service for service in services for k in keys)

    @mock.patch("n2vc.kubectl.client.CoreV1Api.list_service_for_all_namespaces")
    def test_get_service_exception(self, list_services):
        list_services.side_effect = ApiException()
        with self.assertRaises(ApiException):
            self.kubectl.get_services()
