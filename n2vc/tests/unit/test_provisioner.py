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

from unittest import TestCase, mock

from mock import mock_open
from n2vc.provisioner import SSHProvisioner
from paramiko.ssh_exception import SSHException


class ProvisionerTest(TestCase):
    def setUp(self):
        self.provisioner = SSHProvisioner(None, None, None)

    @mock.patch("n2vc.provisioner.os.path.exists")
    @mock.patch("n2vc.provisioner.paramiko.RSAKey")
    @mock.patch("n2vc.provisioner.paramiko.SSHClient")
    @mock.patch("builtins.open", new_callable=mock_open, read_data="data")
    def test__get_ssh_client(self, _mock_open, mock_sshclient, _mock_rsakey, _mock_os):
        mock_instance = mock_sshclient.return_value
        sshclient = self.provisioner._get_ssh_client()
        self.assertEqual(mock_instance, sshclient)
        self.assertEqual(
            1,
            mock_instance.set_missing_host_key_policy.call_count,
            "Missing host key call count",
        )
        self.assertEqual(1, mock_instance.connect.call_count, "Connect call count")

    @mock.patch("n2vc.provisioner.os.path.exists")
    @mock.patch("n2vc.provisioner.paramiko.RSAKey")
    @mock.patch("n2vc.provisioner.paramiko.SSHClient")
    @mock.patch("builtins.open", new_callable=mock_open, read_data="data")
    def test__get_ssh_client_no_connection(
        self, _mock_open, mock_sshclient, _mock_rsakey, _mock_os
    ):

        mock_instance = mock_sshclient.return_value
        mock_instance.method_inside_someobject.side_effect = ["something"]
        mock_instance.connect.side_effect = SSHException()

        self.assertRaises(SSHException, self.provisioner._get_ssh_client)
        self.assertEqual(
            1,
            mock_instance.set_missing_host_key_policy.call_count,
            "Missing host key call count",
        )
        self.assertEqual(1, mock_instance.connect.call_count, "Connect call count")

    @mock.patch("n2vc.provisioner.os.path.exists")
    @mock.patch("n2vc.provisioner.paramiko.RSAKey")
    @mock.patch("n2vc.provisioner.paramiko.SSHClient")
    @mock.patch("builtins.open", new_callable=mock_open, read_data="data")
    def test__get_ssh_client_bad_banner(
        self, _mock_open, mock_sshclient, _mock_rsakey, _mock_os
    ):

        mock_instance = mock_sshclient.return_value
        mock_instance.method_inside_someobject.side_effect = ["something"]
        mock_instance.connect.side_effect = [
            SSHException("Error reading SSH protocol banner"),
            None,
            None,
        ]

        sshclient = self.provisioner._get_ssh_client()
        self.assertEqual(mock_instance, sshclient)
        self.assertEqual(
            1,
            mock_instance.set_missing_host_key_policy.call_count,
            "Missing host key call count",
        )
        self.assertEqual(
            3, mock_instance.connect.call_count, "Should attempt 3 connections"
        )

    @mock.patch("time.sleep", autospec=True)
    @mock.patch("n2vc.provisioner.os.path.exists")
    @mock.patch("n2vc.provisioner.paramiko.RSAKey")
    @mock.patch("n2vc.provisioner.paramiko.SSHClient")
    @mock.patch("builtins.open", new_callable=mock_open, read_data="data")
    def test__get_ssh_client_unable_to_connect(
        self, _mock_open, mock_sshclient, _mock_rsakey, _mock_os, _mock_sleep
    ):

        mock_instance = mock_sshclient.return_value
        mock_instance.connect.side_effect = Exception("Unable to connect to port")

        self.assertRaises(Exception, self.provisioner._get_ssh_client)
        self.assertEqual(
            1,
            mock_instance.set_missing_host_key_policy.call_count,
            "Missing host key call count",
        )
        self.assertEqual(
            11, mock_instance.connect.call_count, "Should attempt 11 connections"
        )

    @mock.patch("time.sleep", autospec=True)
    @mock.patch("n2vc.provisioner.os.path.exists")
    @mock.patch("n2vc.provisioner.paramiko.RSAKey")
    @mock.patch("n2vc.provisioner.paramiko.SSHClient")
    @mock.patch("builtins.open", new_callable=mock_open, read_data="data")
    def test__get_ssh_client_unable_to_connect_once(
        self, _mock_open, mock_sshclient, _mock_rsakey, _mock_os, _mock_sleep
    ):

        mock_instance = mock_sshclient.return_value
        mock_instance.connect.side_effect = [
            Exception("Unable to connect to port"),
            None,
        ]

        sshclient = self.provisioner._get_ssh_client()
        self.assertEqual(mock_instance, sshclient)
        self.assertEqual(
            1,
            mock_instance.set_missing_host_key_policy.call_count,
            "Missing host key call count",
        )
        self.assertEqual(
            2, mock_instance.connect.call_count, "Should attempt 2 connections"
        )

    @mock.patch("n2vc.provisioner.os.path.exists")
    @mock.patch("n2vc.provisioner.paramiko.RSAKey")
    @mock.patch("n2vc.provisioner.paramiko.SSHClient")
    @mock.patch("builtins.open", new_callable=mock_open, read_data="data")
    def test__get_ssh_client_other_exception(
        self, _mock_open, mock_sshclient, _mock_rsakey, _mock_os
    ):

        mock_instance = mock_sshclient.return_value
        mock_instance.connect.side_effect = Exception()

        self.assertRaises(Exception, self.provisioner._get_ssh_client)
        self.assertEqual(
            1,
            mock_instance.set_missing_host_key_policy.call_count,
            "Missing host key call count",
        )
        self.assertEqual(
            1, mock_instance.connect.call_count, "Should only attempt 1 connection"
        )


#
