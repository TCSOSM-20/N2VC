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
#     limitations under the License.

import base64
import juju
import logging
import n2vc.exceptions
from n2vc.vnf import N2VC  # noqa: F401
import os
import pytest
import re
import ssl
import sys

MODEL_NAME = '5e4e7cb0-5678-4b82-97da-9e4a1b51f5d5'

class TestN2VC(object):

    @classmethod
    def setup_class(self):
        """ setup any state specific to the execution of the given class (which
        usually contains tests).
        """
        # Initialize instance variable(s)
        self.log = logging.getLogger()
        self.log.level = logging.DEBUG

    @classmethod
    def teardown_class(self):
        """ teardown any state that was previously setup with a call to
        setup_class.
        """
        pass

    """Utility functions"""
    def get_n2vc(self, params={}):
        """Return an instance of N2VC.VNF."""


        # Extract parameters from the environment in order to run our test
        vca_host = params['VCA_HOST']
        vca_port = params['VCA_PORT']
        vca_user = params['VCA_USER']
        vca_charms = params['VCA_CHARMS']
        vca_secret = params['VCA_SECRET']
        vca_cacert = params['VCA_CACERT']
        vca_public_key = params['VCA_PUBLIC_KEY']

        client = n2vc.vnf.N2VC(
            log=self.log,
            server=vca_host,
            port=vca_port,
            user=vca_user,
            secret=vca_secret,
            artifacts=vca_charms,
            juju_public_key=vca_public_key,
            ca_cert=vca_cacert,
        )
        return client

    """Tests"""

    def test_vendored_libjuju(self):
        """Test the module import for our vendored version of libjuju.

        Test and verify that the version of libjuju being imported by N2VC is our
        vendored version, not one installed externally.
        """
        for name in sys.modules:
            if name.startswith("juju"):
                module = sys.modules[name]
                if getattr(module, "__file__"):
                    print(getattr(module, "__file__"))
                    assert re.search('n2vc', module.__file__, re.IGNORECASE)

                    # assert module.__file__.find("N2VC")
                    # assert False
        return

    @pytest.mark.asyncio
    async def test_connect_invalid_cacert(self):
        params = {
            'VCA_HOST': os.getenv('VCA_HOST', '127.0.0.1'),
            'VCA_PORT': os.getenv('VCA_PORT', 17070),
            'VCA_USER': os.getenv('VCA_USER', 'admin'),
            'VCA_SECRET': os.getenv('VCA_SECRET', 'admin'),
            'VCA_CHARMS': os.getenv('VCA_CHARMS', None),
            'VCA_PUBLIC_KEY': os.getenv('VCA_PUBLIC_KEY', None),
            'VCA_CACERT': 'invalidcacert',
        }
        with pytest.raises(n2vc.exceptions.InvalidCACertificate):
            client = self.get_n2vc(params)


    @pytest.mark.asyncio
    async def test_login(self):
        """Test connecting to libjuju."""
        params = {
            'VCA_HOST': os.getenv('VCA_HOST', '127.0.0.1'),
            'VCA_PORT': os.getenv('VCA_PORT', 17070),
            'VCA_USER': os.getenv('VCA_USER', 'admin'),
            'VCA_SECRET': os.getenv('VCA_SECRET', 'admin'),
            'VCA_CHARMS': os.getenv('VCA_CHARMS', None),
            'VCA_PUBLIC_KEY': os.getenv('VCA_PUBLIC_KEY', None),
            'VCA_CACERT': os.getenv('VCA_CACERT', "invalidcacert"),
        }

        client = self.get_n2vc(params)

        await client.login()
        assert client.authenticated

        await client.logout()
        assert client.authenticated is False

    @pytest.mark.asyncio
    async def test_model(self):
        """Test models."""
        params = {
            'VCA_HOST': os.getenv('VCA_HOST', '127.0.0.1'),
            'VCA_PORT': os.getenv('VCA_PORT', 17070),
            'VCA_USER': os.getenv('VCA_USER', 'admin'),
            'VCA_SECRET': os.getenv('VCA_SECRET', 'admin'),
            'VCA_CHARMS': os.getenv('VCA_CHARMS', None),
            'VCA_PUBLIC_KEY': os.getenv('VCA_PUBLIC_KEY', None),
            'VCA_CACERT': os.getenv('VCA_CACERT', "invalidcacert"),
        }

        client = self.get_n2vc(params)

        await client.login()
        assert client.authenticated

        self.log.debug("Creating model {}".format(MODEL_NAME))
        await client.CreateNetworkService(MODEL_NAME)

        # assert that model exists
        model = await client.controller.get_model(MODEL_NAME)
        assert model

        await client.DestroyNetworkService(MODEL_NAME)

        # Wait for model to be destroyed
        import time
        time.sleep(5)

        with pytest.raises(juju.errors.JujuAPIError):
            model = await client.controller.get_model(MODEL_NAME)

        await client.logout()
        assert client.authenticated is False
