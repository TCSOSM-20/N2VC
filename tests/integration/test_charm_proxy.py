# Copyright 2019 Canonical Ltd.
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
#

"""
Deploy a VNF with a proxy charm, executing an initial-config-primitive
"""

import asyncio
import logging
import pytest
from .. import base


# @pytest.mark.serial
class TestCharm(base.TestN2VC):

    NSD_YAML = """
    nsd:nsd-catalog:
        nsd:
        -   id: charmproxy-ns
            name: charmproxy-ns
            short-name: charmproxy-ns
            description: NS with 1 VNF connected by datanet and mgmtnet VLs
            version: '1.0'
            logo: osm.png
            constituent-vnfd:
            -   vnfd-id-ref: charmproxy-vnf
                member-vnf-index: '1'
            vld:
            -   id: mgmtnet
                name: mgmtnet
                short-name: mgmtnet
                type: ELAN
                mgmt-network: 'true'
                vim-network-name: mgmt
                vnfd-connection-point-ref:
                -   vnfd-id-ref: charmproxy-vnf
                    member-vnf-index-ref: '1'
                    vnfd-connection-point-ref: vnf-mgmt
                -   vnfd-id-ref: charmproxy-vnf
                    member-vnf-index-ref: '2'
                    vnfd-connection-point-ref: vnf-mgmt
            -   id: datanet
                name: datanet
                short-name: datanet
                type: ELAN
                vnfd-connection-point-ref:
                -   vnfd-id-ref: charmproxy-vnf
                    member-vnf-index-ref: '1'
                    vnfd-connection-point-ref: vnf-data
                -   vnfd-id-ref: charmproxy-vnf
                    member-vnf-index-ref: '2'
                    vnfd-connection-point-ref: vnf-data
    """

    VNFD_YAML = """
    vnfd:vnfd-catalog:
        vnfd:
        -   id: charmproxy-vnf
            name: charmproxy-vnf
            short-name: charmproxy-vnf
            version: '1.0'
            description: A VNF consisting of 1 VDUs w/proxy charm
            logo: osm.png
            connection-point:
            -   id: vnf-mgmt
                name: vnf-mgmt
                short-name: vnf-mgmt
                type: VPORT
            -   id: vnf-data
                name: vnf-data
                short-name: vnf-data
                type: VPORT
            mgmt-interface:
                cp: vnf-mgmt
            internal-vld:
            -   id: internal
                name: internal
                short-name: internal
                type: ELAN
                internal-connection-point:
                -   id-ref: mgmtVM-internal
                -   id-ref: dataVM-internal
            vdu:
            -   id: mgmtVM
                name: mgmtVM
                image: xenial
                count: '1'
                vm-flavor:
                    vcpu-count: '1'
                    memory-mb: '1024'
                    storage-gb: '10'
                interface:
                -   name: mgmtVM-eth0
                    position: '1'
                    type: EXTERNAL
                    virtual-interface:
                        type: VIRTIO
                    external-connection-point-ref: vnf-mgmt
                -   name: mgmtVM-eth1
                    position: '2'
                    type: INTERNAL
                    virtual-interface:
                        type: VIRTIO
                    internal-connection-point-ref: mgmtVM-internal
                internal-connection-point:
                -   id: mgmtVM-internal
                    name: mgmtVM-internal
                    short-name: mgmtVM-internal
                    type: VPORT
                cloud-init-file: cloud-config.txt
                vdu-configuration:
                    juju:
                        charm: proxy-ci
                        proxy: true
                    initial-config-primitive:
                    -   seq: '1'
                        name: test
    """

    # @pytest.mark.serial
    @pytest.mark.asyncio
    async def test_charm_proxy(self, event_loop):
        """Deploy and execute the initial-config-primitive of a VNF."""

        if self.nsd and self.vnfd:
            vnf_index = 0

            for config in self.get_config():
                juju = config['juju']
                charm = juju['charm']

                await self.deploy(
                    vnf_index,
                    charm,
                    config,
                    event_loop,
                )

            while await self.running():
                print("Waiting for test to finish...")
                await asyncio.sleep(15)
            logging.debug("test_charm_proxy stopped")

        return 'ok'
