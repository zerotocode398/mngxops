"""SSH 客户端测试。"""

import paramiko
from io import StringIO
from unittest.mock import patch, MagicMock

from django.test import TestCase

from utils.ssh import (
    SSHClient,
    _safe_backup_hostname,
    _ssh_connect_timeout,
    _ssh_detect_retries,
    test_ssh_connection as _test_ssh_connection,
)


class TestSafeBackupHostname(TestCase):
    def test_normal_hostname(self):
        self.assertEqual(_safe_backup_hostname("web01"), "web01")

    def test_with_special_chars(self):
        result = _safe_backup_hostname("web@01!")
        self.assertIn("web", result)
        self.assertIn("01", result)
        self.assertNotIn("@", result)
        self.assertNotIn("!", result)

    def test_empty(self):
        self.assertEqual(_safe_backup_hostname(""), "unknown")

    def test_none(self):
        self.assertEqual(_safe_backup_hostname(None), "unknown")

    def test_whitespace(self):
        self.assertEqual(_safe_backup_hostname("   "), "unknown")

    def test_dots_and_dashes(self):
        self.assertEqual(
            _safe_backup_hostname("web-01.example.com"), "web-01.example.com"
        )

    def test_chinese(self):
        result = _safe_backup_hostname("节点01")
        self.assertIn("01", result)


class TestSSHClientInit(TestCase):
    def test_init_defaults(self):
        client = SSHClient("192.168.1.1", 22, "root")
        self.assertEqual(client.host, "192.168.1.1")
        self.assertEqual(client.port, 22)
        self.assertEqual(client.username, "root")
        self.assertIsNone(client.password)
        self.assertIsNone(client.private_key)
        self.assertIsNone(client.client)

    def test_init_with_password(self):
        client = SSHClient("192.168.1.1", 22, "root", password="secret")
        self.assertEqual(client.password, "secret")

    def test_init_with_private_key(self):
        client = SSHClient("192.168.1.1", 22, "root", private_key="key-data")
        self.assertEqual(client.private_key, "key-data")


class TestParsePrivateKey(TestCase):
    def setUp(self):
        self.client = SSHClient("192.168.1.1", 22, "root")

    def test_invalid_key(self):
        with patch(
            "paramiko.RSAKey.from_private_key", side_effect=Exception("bad key")
        ):
            with patch(
                "paramiko.Ed25519Key.from_private_key", side_effect=Exception("bad key")
            ):
                with patch(
                    "paramiko.ECDSAKey.from_private_key",
                    side_effect=Exception("bad key"),
                ):
                    with patch.object(paramiko, "DSSKey", create=True) as mock_dss:
                        mock_dss.from_private_key.side_effect = Exception("bad key")
                        try:
                            result = self.client._parse_private_key("invalid-key-data")
                        except AttributeError:
                            result = None
                        self.assertIsNone(result)

    def test_empty_key(self):
        with patch.object(paramiko, "DSSKey", create=True) as mock_dss:
            mock_dss.from_private_key.side_effect = Exception("bad key")
            try:
                result = self.client._parse_private_key("")
            except AttributeError:
                result = None
            self.assertIsNone(result)

    def test_rsa_key(self):
        rsa_key = (
            "-----BEGIN RSA PRIVATE KEY-----\nMIIEpA\n-----END RSA PRIVATE KEY-----"
        )
        with patch("paramiko.RSAKey.from_private_key") as mock_from_key:
            with patch.object(paramiko, "DSSKey", create=True):
                mock_key = MagicMock()
                mock_from_key.return_value = mock_key
                try:
                    result = self.client._parse_private_key(rsa_key)
                except AttributeError:
                    result = None
                self.assertIsNotNone(result)


class TestSSHConnectTimeout(TestCase):
    @patch("utils.ssh.get_setting")
    def test_default(self, mock_get):
        mock_get.return_value = "10"
        result = _ssh_connect_timeout()
        self.assertEqual(result, 10)

    @patch("utils.ssh.get_setting")
    def test_custom(self, mock_get):
        mock_get.return_value = "30"
        result = _ssh_connect_timeout()
        self.assertEqual(result, 30)

    @patch("utils.ssh.get_setting")
    def test_minimum_one(self, mock_get):
        mock_get.return_value = "0"
        result = _ssh_connect_timeout()
        self.assertEqual(result, 1)

    @patch("utils.ssh.get_setting")
    def test_invalid(self, mock_get):
        mock_get.return_value = "abc"
        result = _ssh_connect_timeout()
        self.assertEqual(result, 10)


class TestSSHDetectRetries(TestCase):
    @patch("utils.ssh.get_setting")
    def test_default(self, mock_get):
        mock_get.return_value = "1"
        result = _ssh_detect_retries()
        self.assertEqual(result, 1)

    @patch("utils.ssh.get_setting")
    def test_custom(self, mock_get):
        mock_get.return_value = "3"
        result = _ssh_detect_retries()
        self.assertEqual(result, 3)

    @patch("utils.ssh.get_setting")
    def test_minimum_zero(self, mock_get):
        mock_get.return_value = "-1"
        result = _ssh_detect_retries()
        self.assertEqual(result, 0)

    @patch("utils.ssh.get_setting")
    def test_invalid(self, mock_get):
        mock_get.return_value = "abc"
        result = _ssh_detect_retries()
        self.assertEqual(result, 1)


class TestSSHClientClose(TestCase):
    def test_close_none_client(self):
        client = SSHClient("192.168.1.1", 22, "root")
        client.close()

    def test_close_with_client(self):
        client = SSHClient("192.168.1.1", 22, "root")
        mock_client = MagicMock()
        client.client = mock_client
        client.close()
        mock_client.close.assert_called_once()
        self.assertIsNone(client.client)


class TestSSHClientContextManager(TestCase):
    @patch.object(SSHClient, "connect")
    @patch.object(SSHClient, "close")
    def test_context_manager(self, mock_close, mock_connect):
        mock_connect.return_value = (True, "connected")
        with SSHClient("192.168.1.1", 22, "root") as ssh:
            self.assertEqual(ssh._connect_result, (True, "connected"))
        mock_close.assert_called_once()


class TestSSHClientExecuteCommand(TestCase):
    def test_no_client(self):
        client = SSHClient("192.168.1.1", 22, "root")
        success, msg = client.execute_command("ls")
        self.assertFalse(success)
        self.assertIn("未建立", msg)

    @patch("paramiko.SSHClient")
    def test_success(self, mock_paramiko):
        client = SSHClient("192.168.1.1", 22, "root")
        mock_ssh = MagicMock()
        mock_stdout = MagicMock()
        mock_stdout.read.return_value = b"output\n"
        mock_stdout.channel.recv_exit_status.return_value = 0
        mock_stderr = MagicMock()
        mock_stderr.read.return_value = b""
        mock_ssh.exec_command.return_value = (MagicMock(), mock_stdout, mock_stderr)
        client.client = mock_ssh
        success, msg = client.execute_command("ls")
        self.assertTrue(success)
        self.assertEqual(msg, "output")

    @patch("paramiko.SSHClient")
    def test_failure(self, mock_paramiko):
        client = SSHClient("192.168.1.1", 22, "root")
        mock_ssh = MagicMock()
        mock_stdout = MagicMock()
        mock_stdout.read.return_value = b""
        mock_stdout.channel.recv_exit_status.return_value = 1
        mock_stderr = MagicMock()
        mock_stderr.read.return_value = b"error"
        mock_ssh.exec_command.return_value = (MagicMock(), mock_stdout, mock_stderr)
        client.client = mock_ssh
        success, msg = client.execute_command("ls")
        self.assertFalse(success)


class TestTestSSHConnection(TestCase):
    @patch.object(SSHClient, "connect")
    def test_success(self, mock_connect):
        mock_connect.return_value = (True, "connected")
        ok, msg = _test_ssh_connection("192.168.1.1", 22, "root", password="secret")
        self.assertTrue(ok)
        self.assertEqual(msg, "connected")

    @patch.object(SSHClient, "connect")
    def test_failure(self, mock_connect):
        mock_connect.return_value = (False, "auth failed")
        ok, msg = _test_ssh_connection("192.168.1.1", 22, "root", password="secret")
        self.assertFalse(ok)
        self.assertEqual(msg, "auth failed")

    @patch.object(SSHClient, "connect")
    def test_exception(self, mock_connect):
        mock_connect.side_effect = Exception("timeout")
        ok, msg = _test_ssh_connection("192.168.1.1", 22, "root", password="secret")
        self.assertFalse(ok)
        self.assertEqual(msg, "timeout")
