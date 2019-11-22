#  Copyright 2019 Canonical Ltd.

#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at

#      http://www.apache.org/licenses/LICENSE-2.0

#      Unless required by applicable law or agreed to in writing, software
#      distributed under the License is distributed on an "AS IS" BASIS,
#      WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#      See the License for the specific language governing permissions and
#      limitations under the License.

import argparse
import asyncio
import logging
import n2vc.k8s_juju_conn
from base import get_juju_public_key
import os
from osm_common.fslocal import FsLocal
import subprocess
import yaml


def get_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cluster_uuid", help='The UUID of an existing cluster to use', default=None)
    parser.add_argument("--reset", action="store_true")
    return parser.parse_args()

async def main():

    args = get_args()

    reuse_cluster_uuid = args.cluster_uuid

    log = logging.getLogger()
    log.level = logging.DEBUG

    # Extract parameters from the environment in order to run our tests
    vca_host = os.getenv('VCA_HOST', '127.0.0.1')
    vca_port = os.getenv('VCA_PORT', 17070)
    vca_user = os.getenv('VCA_USER', 'admin')
    vca_charms = os.getenv('VCA_CHARMS', None)
    vca_secret = os.getenv('VCA_SECRET', None)
    vca_ca_cert = os.getenv('VCA_CACERT', None)

    # Get the Juju Public key
    juju_public_key = get_juju_public_key()
    if juju_public_key:
        with open(juju_public_key, 'r') as f:
            juju_public_key = f.read()
    else:
        raise Exception("No Juju Public Key found")

    storage = {
        'driver': 'local',
        'path': '/srv/app/storage'
    }
    fs = FsLocal()
    fs.fs_connect(storage)

    client = n2vc.k8s_juju_conn.K8sJujuConnector(
        kubectl_command = '/bin/true',
        fs = fs,
    )

    # kubectl config view --raw
    # microk8s.config

    # if microk8s then
    kubecfg = subprocess.getoutput('microk8s.config')
    # else
    # kubecfg.subprocess.getoutput('kubectl config view --raw')
    
    k8screds = yaml.load(kubecfg, Loader=yaml.FullLoader)
    namespace = 'testing'
    kdu_model = "./tests/bundles/k8s-zookeeper.yaml"

    """init_env"""
    cluster_uuid = await client.init_env(k8screds, namespace, reuse_cluster_uuid=reuse_cluster_uuid)
    print(cluster_uuid)

    if not reuse_cluster_uuid:
        # This is a new cluster, so install to it

        """install"""
        # async def install(self, cluster_uuid, kdu_model, atomic=True, timeout=None, params=None):
        # TODO: Re-add storage declaration to bundle. The API doesn't support the storage option yet. David is investigating.

        # Deploy the bundle
        kdu_instance = await client.install(cluster_uuid, kdu_model, atomic=True, timeout=600)

        if kdu_instance:
            # Inspect
            print("Getting status")
            status = await client.status_kdu(cluster_uuid, kdu_instance)
            print(status)

    # Inspect the bundle
    config = await client.inspect_kdu(kdu_model)
    print(config)

    readme = await client.help_kdu(kdu_model)
    # print(readme)


    """upgrade
    Upgrade to a newer version of the bundle
    """
    kdu_model_upgrade = "./tests/bundles/k8s-zookeeper-upgrade.yaml"
    upgraded = await client.upgrade(cluster_uuid, namespace, kdu_model=kdu_model_upgrade)

    kdu_model_upgrade = "./tests/bundles/k8s-zookeeper-downgrade.yaml"
    upgraded = await client.upgrade(cluster_uuid, namespace, kdu_model=kdu_model_upgrade)

    """uninstall"""

    """reset"""
    if args.reset:
        await client.reset(cluster_uuid)

    await client.logout()

    print("Done")

if __name__ == "__main__":
    asyncio.run(main())
