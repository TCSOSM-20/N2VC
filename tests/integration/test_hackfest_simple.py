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
    -   id: hackfest-simplecharm-ns
        name: hackfest-simplecharm-ns
        short-name: hackfest-simplecharm-ns
        description: NS with 2 VNFs hackfest-simplecharm-vnf connected by datanet and mgmtnet VLs
        version: '1.0'
        logo: osm.png
        constituent-vnfd:
        -   vnfd-id-ref: hackfest-simplecharm-vnf
            member-vnf-index: '1'
        -   vnfd-id-ref: hackfest-simplecharm-vnf
            member-vnf-index: '2'
        vld:
        -   id: mgmtnet
            name: mgmtnet
            short-name: mgmtnet
            type: ELAN
            mgmt-network: 'true'
            vim-network-name: mgmt
            vnfd-connection-point-ref:
            -   vnfd-id-ref: hackfest-simplecharm-vnf
                member-vnf-index-ref: '1'
                vnfd-connection-point-ref: vnf-mgmt
            -   vnfd-id-ref: hackfest-simplecharm-vnf
                member-vnf-index-ref: '2'
                vnfd-connection-point-ref: vnf-mgmt
        -   id: datanet
            name: datanet
            short-name: datanet
            type: ELAN
            vnfd-connection-point-ref:
            -   vnfd-id-ref: hackfest-simplecharm-vnf
                member-vnf-index-ref: '1'
                vnfd-connection-point-ref: vnf-data
            -   vnfd-id-ref: hackfest-simplecharm-vnf
                member-vnf-index-ref: '2'
                vnfd-connection-point-ref: vnf-data
    """

    VNFD_YAML = """
vnfd:vnfd-catalog:
    vnfd:
    -   id: hackfest-simplecharm-vnf
        name: hackfest-simplecharm-vnf
        short-name: hackfest-simplecharm-vnf
        version: '1.0'
        description: A VNF consisting of 2 VDUs connected to an internal VL, and one VDU with cloud-init
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
            image: hackfest3-mgmt
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
                    type: PARAVIRT
                external-connection-point-ref: vnf-mgmt
            -   name: mgmtVM-eth1
                position: '2'
                type: INTERNAL
                virtual-interface:
                    type: PARAVIRT
                internal-connection-point-ref: mgmtVM-internal
            internal-connection-point:
            -   id: mgmtVM-internal
                name: mgmtVM-internal
                short-name: mgmtVM-internal
                type: VPORT
            cloud-init-file: cloud-config.txt
        -   id: dataVM
            name: dataVM
            image: hackfest3-mgmt
            count: '1'
            vm-flavor:
                vcpu-count: '1'
                memory-mb: '1024'
                storage-gb: '10'
            interface:
            -   name: dataVM-eth0
                position: '1'
                type: INTERNAL
                virtual-interface:
                    type: PARAVIRT
                internal-connection-point-ref: dataVM-internal
            -   name: dataVM-xe0
                position: '2'
                type: EXTERNAL
                virtual-interface:
                    type: PARAVIRT
                external-connection-point-ref: vnf-data
            internal-connection-point:
            -   id: dataVM-internal
                name: dataVM-internal
                short-name: dataVM-internal
                type: VPORT
        vnf-configuration:
            juju:
                charm: simple
            initial-config-primitive:
            -   seq: '1'
                name: config
                parameter:
                -   name: ssh-hostname
                    value: <rw_mgmt_ip>
                -   name: ssh-username
                    value: ubuntu
                -   name: ssh-password
                    value: osm4u
            -   seq: '2'
                name: touch
                parameter:
                -   name: filename
                    value: '/home/ubuntu/first-touch'
            config-primitive:
            -   name: touch
                parameter:
                -   name: filename
                    data-type: STRING
                    default-value: '/home/ubuntu/touched'
    """

    # @pytest.mark.serial
    @pytest.mark.asyncio
    async def test_multivdu_multicharm(self, event_loop):
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
            logging.debug("test_multivdu_multicharm stopped")

        return 'ok'
