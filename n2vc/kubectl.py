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

from kubernetes import client, config
from kubernetes.client.rest import ApiException
import logging


class Kubectl:
    def __init__(self, config_file=None):
        config.load_kube_config(config_file=config_file)
        self.logger = logging.getLogger("Kubectl")

    def get_services(self, field_selector=None, label_selector=None):
        kwargs = {}
        if field_selector:
            kwargs["field_selector"] = field_selector
        if label_selector:
            kwargs["label_selector"] = label_selector

        try:
            v1 = client.CoreV1Api()
            result = v1.list_service_for_all_namespaces(**kwargs)
            return [
                {
                    "name": i.metadata.name,
                    "cluster_ip": i.spec.cluster_ip,
                    "type": i.spec.type,
                    "ports": [
                        {
                            "name": p.name,
                            "node_port": p.node_port,
                            "port": p.port,
                            "protocol": p.protocol,
                            "target_port": p.target_port,
                        }
                        for p in i.spec.ports
                    ]
                    if i.spec.ports
                    else [],
                    "external_ip": [i.ip for i in i.status.load_balancer.ingress]
                    if i.status.load_balancer.ingress
                    else None,
                }
                for i in result.items
            ]
        except ApiException as e:
            self.logger.error("Error calling get services: {}".format(e))
            raise e
