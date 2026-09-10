"""credentials 模块单元测试（凭证工具函数、导入导出校验）"""

import pytest
from unittest.mock import patch, MagicMock
import paramiko

from apps.credentials.services import (
    _cell_str,
    _is_empty_optional,
    is_valid_private_key,
)


class TestCellStr:
    """_cell_str 单元格值规范化"""

    def test_none_returns_empty(self):
        assert _cell_str(None) == ""

    def test_string_trimmed(self):
        assert _cell_str("  hello  ") == "hello"

    def test_float_to_int_string(self):
        assert _cell_str(3.0) == "3"

    def test_float_with_decimals(self):
        assert _cell_str(3.14) == "3.14"


class TestIsEmptyOptional:
    """_is_empty_optional 可选字段判空"""

    def test_empty_string(self):
        assert _is_empty_optional("")

    def test_dash(self):
        assert _is_empty_optional("-")

    def test_chinese_dash(self):
        assert _is_empty_optional("—")

    def test_na(self):
        assert _is_empty_optional("n/a")

    def test_none_text(self):
        assert _is_empty_optional("无")

    def test_non_empty(self):
        assert not _is_empty_optional("hello")

    def test_whitespace_only(self):
        assert _is_empty_optional("   ")


class TestIsValidPrivateKey:
    """is_valid_private_key 私钥格式校验"""

    def test_empty_string_invalid(self):
        assert not is_valid_private_key("")

    def test_whitespace_only_invalid(self):
        assert not is_valid_private_key("   ")

    def test_garbage_invalid(self):
        mock_rsa = MagicMock()
        mock_rsa.from_private_key.side_effect = Exception("bad key")
        mock_ecdsa = MagicMock()
        mock_ecdsa.from_private_key.side_effect = Exception("bad key")
        mock_ed25519 = MagicMock()
        mock_ed25519.from_private_key.side_effect = Exception("bad key")
        mock_dss = MagicMock()
        mock_dss.from_private_key.side_effect = Exception("bad key")
        with patch.multiple(
            "paramiko",
            RSAKey=mock_rsa,
            ECDSAKey=mock_ecdsa,
            Ed25519Key=mock_ed25519,
            DSSKey=mock_dss,
            create=True,
        ):
            assert not is_valid_private_key("not a key at all")

    def test_valid_key_returns_true(self):
        mock_rsa = MagicMock()
        mock_rsa.from_private_key.return_value = MagicMock()
        mock_dss = MagicMock()
        with patch.multiple(
            "paramiko",
            RSAKey=mock_rsa,
            DSSKey=mock_dss,
            create=True,
        ):
            assert is_valid_private_key(
                "-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----"
            )

    def test_short_string_invalid(self):
        mock_rsa = MagicMock()
        mock_rsa.from_private_key.side_effect = Exception("bad key")
        mock_ecdsa = MagicMock()
        mock_ecdsa.from_private_key.side_effect = Exception("bad key")
        mock_ed25519 = MagicMock()
        mock_ed25519.from_private_key.side_effect = Exception("bad key")
        mock_dss = MagicMock()
        mock_dss.from_private_key.side_effect = Exception("bad key")
        with patch.multiple(
            "paramiko",
            RSAKey=mock_rsa,
            ECDSAKey=mock_ecdsa,
            Ed25519Key=mock_ed25519,
            DSSKey=mock_dss,
            create=True,
        ):
            assert not is_valid_private_key("abc")
