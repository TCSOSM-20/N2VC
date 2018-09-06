"""
This test exercises LXD, to make sure that we can:
1. Create a container profile
2. Launch a container with a profile
3. Stop a container
4. Destroy a container
5. Delete a container profile

"""
import logging
# import os
import pytest
from . import base
import subprocess
import shlex
import tempfile


@pytest.mark.asyncio
async def test_lxd():

    container = base.create_lxd_container(name="test-lxd")
    assert container is not None

    # Get the hostname of the container
    hostname = container.name

    # Delete the container
    base.destroy_lxd_container(container)

    # Verify the container is deleted
    client = base.get_lxd_client()
    assert client.containers.exists(hostname) is False


@pytest.mark.asyncio
async def test_lxd_ssh():

    with tempfile.TemporaryDirectory() as tmp:
        try:
            # Create a temporary keypair
            cmd = shlex.split(
                "ssh-keygen -t rsa -b 4096 -N '' -f {}/id_lxd_rsa".format(
                    tmp,
                )
            )
            subprocess.check_call(cmd)
        except subprocess.CalledProcessError as e:
            logging.debug(e)
            assert False

        # Slurp the public key
        public_key = None
        with open("{}/id_lxd_rsa.pub".format(tmp), "r") as f:
            public_key = f.read()

        assert public_key is not None

        # Create the container with the keypair injected via profile
        container = base.create_lxd_container(
            public_key=public_key,
            name="test-lxd"
        )
        assert container is not None

        # Get the hostname of the container
        hostname = container.name

        addresses = container.state().network['eth0']['addresses']
        # The interface may have more than one address, but we only need
        # the first one for testing purposes.
        ipaddr = addresses[0]['address']

        # Verify we can SSH into container
        try:
            cmd = shlex.split(
                "ssh -i {}/id_lxd_rsa {} root@{} hostname".format(
                    tmp,
                    "-oStrictHostKeyChecking=no",
                    ipaddr,
                )
            )
            subprocess.check_call(cmd)
        except subprocess.CalledProcessError as e:
            logging.debug(e)
            assert False

        # Delete the container
        base.destroy_lxd_container(container)

        # Verify the container is deleted
        client = base.get_lxd_client()
        assert client.containers.exists(hostname) is False

        # Verify the container profile is deleted
        assert client.profiles.exists(hostname) is False
