"""
Deploy a VNF w/native charm that collects metrics
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
        -   id: metricsnative-ns
            name: metricsnative-ns
            short-name: metricsnative-ns
            description: NS with 1 VNFs metricsnative-vnf connected by datanet and mgmtnet VLs
            version: '1.0'
            logo: osm.png
            constituent-vnfd:
            -   vnfd-id-ref: metricsnative-vnf
                member-vnf-index: '1'
            vld:
            -   id: mgmtnet
                name: mgmtnet
                short-name: mgmtnet
                type: ELAN
                mgmt-network: 'true'
                vim-network-name: mgmt
                vnfd-connection-point-ref:
                -   vnfd-id-ref: metricsnative-vnf
                    member-vnf-index-ref: '1'
                    vnfd-connection-point-ref: vnf-mgmt
                -   vnfd-id-ref: metricsnative-vnf
                    member-vnf-index-ref: '2'
                    vnfd-connection-point-ref: vnf-mgmt
            -   id: datanet
                name: datanet
                short-name: datanet
                type: ELAN
                vnfd-connection-point-ref:
                -   vnfd-id-ref: metricsnative-vnf
                    member-vnf-index-ref: '1'
                    vnfd-connection-point-ref: vnf-data
                -   vnfd-id-ref: metricsnative-vnf
                    member-vnf-index-ref: '2'
                    vnfd-connection-point-ref: vnf-data
    """

    VNFD_YAML = """
    vnfd:vnfd-catalog:
        vnfd:
        -   id: metricsnative-vnf
            name: metricsnative-vnf
            short-name: metricsnative-vnf
            version: '1.0'
            description: A VNF consisting of 2 VDUs w/charms connected to an internal VL, and one VDU with cloud-init
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
            vnf-configuration:
                juju:
                    charm: metrics-ci
                    proxy: false
                config-primitive:
                -   name: touch
                    parameter:
                    -   name: filename
                        data-type: STRING
                        default-value: '/home/ubuntu/touched'
    """

    # @pytest.mark.serial
    @pytest.mark.asyncio
    async def test_metrics_native(self, event_loop):
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

            while self.running():
                logging.debug("Waiting for test to finish...")
                await asyncio.sleep(15)
            logging.debug("test_metrics_native stopped")

        return 'ok'
