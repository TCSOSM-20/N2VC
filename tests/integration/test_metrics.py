"""Test the collection of charm metrics.
    1. Deploy a charm w/metrics to a unit
    2. Collect metrics or wait for collection to run
    3. Execute n2vc.GetMetrics()
    5. Destroy Juju unit
"""
import asyncio
import functools
import logging
import sys
import time
import unittest
from .. import utils

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
        vnf-configuration:
            juju:
                charm: metrics-ci
            config-primitive:
            -   name: touch
                parameter:
                -   name: filename
                    data-type: STRING
                    default-value: '/home/ubuntu/touched'
"""


class PythonTest(unittest.TestCase):
    n2vc = None
    charm = None

    def setUp(self):
        self.log = logging.getLogger()
        self.log.level = logging.DEBUG

        self.stream_handler = logging.StreamHandler(sys.stdout)
        self.log.addHandler(self.stream_handler)

        self.loop = asyncio.get_event_loop()

        self.n2vc = utils.get_n2vc()

        # Parse the descriptor
        self.log.debug("Parsing the descriptor")
        self.nsd = utils.get_descriptor(NSD_YAML)
        self.vnfd = utils.get_descriptor(VNFD_YAML)


        # Build the charm

        vnf_config = self.vnfd.get("vnf-configuration")
        if vnf_config:
            juju = vnf_config['juju']
            charm = juju['charm']

            self.log.debug("Building charm {}".format(charm))
            self.charm = utils.build_charm(charm)

    def tearDown(self):
        self.loop.run_until_complete(self.n2vc.logout())
        self.log.removeHandler(self.stream_handler)

    def n2vc_callback(self, model_name, application_name, workload_status,\
                      workload_message, task=None):
        """We pass the vnfd when setting up the callback, so expect it to be
        returned as a tuple."""
        self.log.debug("status: {}; task: {}".format(workload_status, task))

        # if workload_status in ["stop_test"]:
        #     # Stop the test
        #     self.log.debug("Stopping the test1")
        #     self.loop.call_soon_threadsafe(self.loop.stop)
        #     return

        if workload_status:
            if workload_status in ["active"] and not task:
                # Force a run of the metric collector, so we don't have
                # to wait for it's normal 5 minute interval run.
                # NOTE: this shouldn't be done outside of CI
                utils.collect_metrics(application_name)

                # get the current metrics
                task = asyncio.ensure_future(
                    self.n2vc.GetMetrics(
                        model_name,
                        application_name,
                    )
                )
                task.add_done_callback(
                    functools.partial(
                        self.n2vc_callback,
                        model_name,
                        application_name,
                        "collect_metrics",
                        task,
                    )
                )

            elif workload_status in ["collect_metrics"]:

                if task:
                    # Check if task returned metrics
                    results = task.result()

                    foo = utils.parse_metrics(application_name, results)
                    if 'load' in foo:
                        self.log.debug("Removing charm")
                        task = asyncio.ensure_future(
                            self.n2vc.RemoveCharms(model_name, application_name, self.n2vc_callback)
                        )
                        task.add_done_callback(
                            functools.partial(
                                self.n2vc_callback,
                                model_name,
                                application_name,
                                "stop_test",
                                task,
                            )
                        )
                        return

                # No metrics are available yet, so try again in a minute.
                self.log.debug("Sleeping for 60 seconds")
                time.sleep(60)
                task = asyncio.ensure_future(
                    self.n2vc.GetMetrics(
                        model_name,
                        application_name,
                    )
                )
                task.add_done_callback(
                    functools.partial(
                        self.n2vc_callback,
                        model_name,
                        application_name,
                        "collect_metrics",
                        task,
                    )
                )
            elif workload_status in ["stop_test"]:
                # Stop the test
                self.log.debug("Stopping the test2")
                self.loop.call_soon_threadsafe(self.loop.stop)

    def test_deploy_application(self):
        """Deploy proxy charm to a unit."""
        if self.nsd and self.vnfd:
            params = {}
            vnf_index = 0

            def deploy():
                """An inner function to do the deployment of a charm from
                either a vdu or vnf.
                """
                charm_dir = "{}/builds/{}".format(utils.get_charm_path(), charm)

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
                    self.vnfd['name'],
                    str(vnf_index),
                )

                self.loop.run_until_complete(
                    self.n2vc.DeployCharms(
                        ns_name,
                        vnf_name,
                        self.vnfd,
                        charm_dir,
                        params,
                        {},
                        self.n2vc_callback
                    )
                )

            # Check if the VDUs in this VNF have a charm
            # for vdu in vnfd['vdu']:
            #     vdu_config = vdu.get('vdu-configuration')
            #     if vdu_config:
            #         juju = vdu_config['juju']
            #         self.assertIsNotNone(juju)
            #
            #         charm = juju['charm']
            #         self.assertIsNotNone(charm)
            #
            #         params['initial-config-primitive'] = vdu_config['initial-config-primitive']
            #
            #         deploy()
            #         vnf_index += 1
            #
            # # Check if this VNF has a charm
            vnf_config = self.vnfd.get("vnf-configuration")
            if vnf_config:
                juju = vnf_config['juju']
                self.assertIsNotNone(juju)

                charm = juju['charm']
                self.assertIsNotNone(charm)

                if 'initial-config-primitive' in vnf_config:
                    params['initial-config-primitive'] = vnf_config['initial-config-primitive']

                deploy()
                vnf_index += 1

            self.loop.run_forever()
            # while self.loop.is_running():
            #     # await asyncio.sleep(1)
            #     time.sleep(1)
