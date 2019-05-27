"""
Deploy a multi-vdu, multi-charm VNF
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
        -   id: multivdurelate-ns
            name: multivdurelate-ns
            short-name: multivdurelate-ns
            description: NS with 1 VNF connected by datanet and mgmtnet VLs
            version: '1.0'
            logo: osm.png
            constituent-vnfd:
            -   vnfd-id-ref: multivdurelate-vnf
                member-vnf-index: '1'
            vld:
            -   id: mgmtnet
                name: mgmtnet
                short-name: mgmtnet
                type: ELAN
                mgmt-network: 'true'
                vim-network-name: mgmt
                vnfd-connection-point-ref:
                -   vnfd-id-ref: multivdurelate-vnf
                    member-vnf-index-ref: '1'
                    vnfd-connection-point-ref: vnf-mgmt
                -   vnfd-id-ref: multivdurelate-vnf
                    member-vnf-index-ref: '2'
                    vnfd-connection-point-ref: vnf-mgmt
            -   id: datanet
                name: datanet
                short-name: datanet
                type: ELAN
                vnfd-connection-point-ref:
                -   vnfd-id-ref: multivdurelate-vnf
                    member-vnf-index-ref: '1'
                    vnfd-connection-point-ref: vnf-data
                -   vnfd-id-ref: multivdurelate-vnf
                    member-vnf-index-ref: '2'
                    vnfd-connection-point-ref: vnf-data
    """

    VNFD_YAML = """
    vnfd:vnfd-catalog:
        vnfd:
        -   id: multivdurelate-vnf
            name: multivdurelate-vnf
            short-name: multivdurelate-vnf
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
                        # Relation needs to map to the vdu providing or
                        # requiring, so that we can map to the deployed app.
                        relation:
                        -   provides: dataVM:db
                            requires: mgmtVM:app
                    initial-config-primitive:
                    -   seq: '1'
                        name: test
            -   id: dataVM
                name: dataVM
                image: xenial
                count: '1'
                vm-flavor:
                    vcpu-count: '1'
                    memory-mb: '1024'
                    storage-gb: '10'
                interface:
                -   name: dataVM-eth0
                    position: '1'
                    type: EXTERNAL
                    virtual-interface:
                        type: VIRTIO
                    external-connection-point-ref: vnf-mgmt
                -   name: dataVM-eth1
                    position: '2'
                    type: INTERNAL
                    virtual-interface:
                        type: VIRTIO
                    internal-connection-point-ref: dataVM-internal
                internal-connection-point:
                -   id: dataVM-internal
                    name: dataVM-internal
                    short-name: dataVM-internal
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
    async def test_multivdu_relate(self, event_loop):
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
                vnf_index += 1

            while await self.running():
                logging.debug("Waiting for test to finish...")
                await asyncio.sleep(15)

            # assert False
            logging.debug("test_multivdu_relate stopped")

        return 'ok'
