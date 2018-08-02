"""Test the deployment and configuration of a proxy charm.
    1. Deploy proxy charm to a unit
    2. Execute 'get-ssh-public-key' primitive and get returned value
    3. Create LXD container with unit's public ssh key
    4. Verify SSH works between unit and container
    5. Destroy Juju unit
    6. Stop and Destroy LXD container
"""
import asyncio
import functools
import os
import sys
import logging
import unittest
from . import utils
import yaml
from n2vc.vnf import N2VC

NSD_YAML = """
nsd:nsd-catalog:
    nsd:
    -   id: singlecharmvdu-ns
        name: singlecharmvdu-ns
        short-name: singlecharmvdu-ns
        description: NS with 1 VNFs singlecharmvdu-vnf connected by datanet and mgmtnet VLs
        version: '1.0'
        logo: osm.png
        constituent-vnfd:
        -   vnfd-id-ref: singlecharmvdu-vnf
            member-vnf-index: '1'
        vld:
        -   id: mgmtnet
            name: mgmtnet
            short-name: mgmtnet
            type: ELAN
            mgmt-network: 'true'
            vim-network-name: mgmt
            vnfd-connection-point-ref:
            -   vnfd-id-ref: singlecharmvdu-vnf
                member-vnf-index-ref: '1'
                vnfd-connection-point-ref: vnf-mgmt
            -   vnfd-id-ref: singlecharmvdu-vnf
                member-vnf-index-ref: '2'
                vnfd-connection-point-ref: vnf-mgmt
        -   id: datanet
            name: datanet
            short-name: datanet
            type: ELAN
            vnfd-connection-point-ref:
            -   vnfd-id-ref: singlecharmvdu-vnf
                member-vnf-index-ref: '1'
                vnfd-connection-point-ref: vnf-data
            -   vnfd-id-ref: singlecharmvdu-vnf
                member-vnf-index-ref: '2'
                vnfd-connection-point-ref: vnf-data
"""

VNFD_YAML = """
vnfd:vnfd-catalog:
    vnfd:
    -   id: singlecharmvdu-vnf
        name: singlecharmvdu-vnf
        short-name: singlecharmvdu-vnf
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
            vdu-configuration:
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
                        value: ubuntu
                -   seq: '2'
                    name: touch
                    parameter:
                    -   name: filename
                        value: '/home/ubuntu/first-touch-mgmtVM'
                config-primitive:
                -   name: touch
                    parameter:
                    -   name: filename
                        data-type: STRING
                        default-value: '/home/ubuntu/touched'

"""


class PythonTest(unittest.TestCase):
    n2vc = None
    container = None

    def setUp(self):
        self.log = logging.getLogger()
        self.log.level = logging.DEBUG

        self.loop = asyncio.get_event_loop()

        # self.container = utils.create_lxd_container()
        self.n2vc = utils.get_n2vc()

    def tearDown(self):
        if self.container:
            self.container.stop()
            self.container.delete()

        self.loop.run_until_complete(self.n2vc.logout())

    def n2vc_callback(self, model_name, application_name, workload_status, workload_message, task=None):
        """We pass the vnfd when setting up the callback, so expect it to be
        returned as a tuple."""
        self.log.debug("[Callback] Workload status '{}' for application {}".format(workload_status, application_name))
        self.log.debug("[Callback] Task: \"{}\"".format(task))

        if workload_status == "exec_primitive" and task:
            self.log.debug("Getting Primitive Status")
            # get the uuid from the task
            uuid = task.result()

            # get the status of the action
            task = asyncio.ensure_future(
                self.n2vc.GetPrimitiveStatus(
                    model_name,
                    uuid,
                )
            )
            task.add_done_callback(functools.partial(self.n2vc_callback, model_name, application_name, "primitive_status", task))

        if workload_status == "primitive_status" and task and not self.container:
            self.log.debug("Creating LXD container")
            # Get the ssh key
            result = task.result()
            pubkey = result['pubkey']

            self.container = utils.create_lxd_container(pubkey)
            mgmtaddr = self.container.state().network['eth0']['addresses']

            self.log.debug("Setting config ssh-hostname={}".format(mgmtaddr[0]['address']))
            task = asyncio.ensure_future(
                self.n2vc.ExecutePrimitive(
                    model_name,
                    application_name,
                    "config",
                    None,
                    params={
                        'ssh-hostname': mgmtaddr[0]['address'],
                    }
                )
            )
            task.add_done_callback(functools.partial(self.n2vc_callback, model_name, application_name, None, None))

        if workload_status and not task:
            self.log.debug("Callback: workload status \"{}\"".format(workload_status))

            if workload_status in ["blocked"] and not self.container:
                self.log.debug("Getting public SSH key")

                # Execute 'get-ssh-public-key' primitive and get returned value
                task = asyncio.ensure_future(
                    self.n2vc.ExecutePrimitive(
                        model_name,
                        application_name,
                        "get-ssh-public-key",
                        None,
                        params={
                            'ssh-hostname': '10.195.8.78',
                            'ssh-username': 'ubuntu',
                            'ssh-password': 'ubuntu'
                        }
                    )
                )
                task.add_done_callback(functools.partial(self.n2vc_callback, model_name, application_name, "exec_primitive", task))


                # task = asyncio.ensure_future(
                #     self.n2vc.ExecutePrimitive(
                #         model_name,
                #         application_name,
                #         "config",
                #         None,
                #         params={
                #             'ssh-hostname': '10.195.8.78',
                #             'ssh-username': 'ubuntu',
                #             'ssh-password': 'ubuntu'
                #         }
                #     )
                # )
                # task.add_done_callback(functools.partial(self.n2vc_callback, None, None, None))
                pass
            elif workload_status in ["active"]:
                self.log.debug("Removing charm")
                task = asyncio.ensure_future(
                    self.n2vc.RemoveCharms(model_name, application_name, self.n2vc_callback)
                )
                task.add_done_callback(functools.partial(self.n2vc_callback, None, None, None))

                if self.container:
                    utils.destroy_lxd_container(self.container)
                    self.container = None

                # Stop the test
                self.loop.call_soon_threadsafe(self.loop.stop)

    def test_deploy_application(self):
        """Deploy proxy charm to a unit."""
        stream_handler = logging.StreamHandler(sys.stdout)
        self.log.addHandler(stream_handler)
        try:
            self.log.info("Log handler installed")
            nsd = utils.get_descriptor(NSD_YAML)
            vnfd = utils.get_descriptor(VNFD_YAML)

            if nsd and vnfd:

                vca_charms = os.getenv('VCA_CHARMS', None)

                params = {}
                vnf_index = 0

                def deploy():
                    """An inner function to do the deployment of a charm from
                    either a vdu or vnf.
                    """
                    charm_dir = "{}/{}".format(vca_charms, charm)

                    # Setting this to an IP that will fail the initial config.
                    # This will be detected in the callback, which will execute
                    # the "config" primitive with the right IP address.
                    # mgmtaddr = self.container.state().network['eth0']['addresses']
                    # params['rw_mgmt_ip'] = mgmtaddr[0]['address']

                    # Legacy method is to set the ssh-private-key config
                    # with open(utils.get_juju_private_key(), "r") as f:
                    #     pkey = f.readline()
                    #     params['ssh-private-key'] = pkey

                    ns_name = "default"

                    vnf_name = self.n2vc.FormatApplicationName(
                        ns_name,
                        vnfd['name'],
                        str(vnf_index),
                    )

                    self.loop.run_until_complete(
                        self.n2vc.DeployCharms(
                            ns_name,
                            vnf_name,
                            vnfd,
                            charm_dir,
                            params,
                            {},
                            self.n2vc_callback
                        )
                    )

                # Check if the VDUs in this VNF have a charm
                for vdu in vnfd['vdu']:
                    vdu_config = vdu.get('vdu-configuration')
                    if vdu_config:
                        juju = vdu_config['juju']
                        self.assertIsNotNone(juju)

                        charm = juju['charm']
                        self.assertIsNotNone(charm)

                        params['initial-config-primitive'] = vdu_config['initial-config-primitive']

                        deploy()
                        vnf_index += 1

                # Check if this VNF has a charm
                vnf_config = vnfd.get("vnf-configuration")
                if vnf_config:
                    juju = vnf_config['juju']
                    self.assertIsNotNone(juju)

                    charm = juju['charm']
                    self.assertIsNotNone(charm)

                    params['initial-config-primitive'] = vnf_config['initial-config-primitive']

                    deploy()
                    vnf_index += 1

                self.loop.run_forever()
                # while self.loop.is_running():
                #     # await asyncio.sleep(1)
                #     time.sleep(1)

                # Test actions
                #  ExecutePrimitive(self, nsd, vnfd, vnf_member_index, primitive, callback, *callback_args, **params):

                # self.loop.run_until_complete(n.DestroyNetworkService(nsd))

                # self.loop.run_until_complete(self.n2vc.logout())
        finally:
            self.log.removeHandler(stream_handler)
