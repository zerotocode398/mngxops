"""Nginx 卸载模块单元测试"""
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from apps.credentials.models import Credential
from apps.nginx_uninstall.models import NginxUninstallTask
from apps.nginx_uninstall.services import (
    coalesce_delete_targets,
    create_uninstall_batch_from_data,
    derive_prefix_from_nginx_path,
    detect_nginx_package_origin,
    extract_path_entries_from_nginx_v,
    is_dangerous_path,
    is_file_like_path,
    is_shallow_prefix,
    is_under_path,
    is_valid_package_name,
    normalize_remote_path,
    preview_nodes,
    resolve_nginx_tree_path,
    uninstall_gate_message,
)
from apps.nodes.models import Node


class PathSafetyTests(SimpleTestCase):
    """路径规范化与危险路径校验"""

    def test_normalize_strips_trailing_slash(self):
        """去掉尾部斜杠"""
        self.assertEqual(normalize_remote_path("/opt/app/"), "/opt/app")
        self.assertEqual(normalize_remote_path("/"), "/")

    def test_dangerous_roots_rejected(self):
        """系统根路径禁止删除"""
        for p in ("/", "/usr", "/opt", "/tmp", "/etc", ""):
            self.assertTrue(is_dangerous_path(p), p)

    def test_normal_prefix_allowed(self):
        """正常 prefix 允许"""
        self.assertFalse(is_dangerous_path("/opt/app"))
        self.assertFalse(is_dangerous_path("/usr/local/nginx"))
        self.assertFalse(is_dangerous_path("/data"))

    def test_shallow_prefix(self):
        """非系统根一级目录视为浅路径"""
        self.assertTrue(is_shallow_prefix("/data"))
        self.assertTrue(is_shallow_prefix("/app"))
        self.assertFalse(is_shallow_prefix("/opt"))
        self.assertFalse(is_shallow_prefix("/opt/app"))
        self.assertFalse(is_shallow_prefix("/"))

    def test_valid_package_name(self):
        """合法包名与中文 rpm 报错句"""
        self.assertTrue(is_valid_package_name("nginx"))
        self.assertTrue(is_valid_package_name("nginx-core"))
        self.assertFalse(is_valid_package_name("文件 /home/x 不属于任何软件包"))
        self.assertFalse(is_valid_package_name("file is not owned by any package"))
        self.assertFalse(is_valid_package_name(""))

    def test_derive_prefix_from_sbin(self):
        """由 sbin/nginx 推导 prefix"""
        self.assertEqual(
            derive_prefix_from_nginx_path("/opt/app/sbin/nginx"),
            "/opt/app",
        )


class _FakeSSH:
    """按命令片段返回 (ok, out) 的探测替身"""

    def __init__(self, mapping):
        self.mapping = mapping

    def execute_command(self, cmd):
        """按子串匹配返回探测结果"""
        for key, value in self.mapping.items():
            if key in cmd:
                return value
        return False, ""


class DetectPackageOriginTests(SimpleTestCase):
    """rpm/dpkg 包归属判定"""

    def test_chinese_not_owned_is_source(self):
        """中文不属于任何软件包视为源码安装"""
        ssh = _FakeSSH({
            "rpm -qf": (
                True,
                "文件 /home/umpay/ums/nginx/sbin/nginx 不属于任何软件包",
            ),
            "dpkg -S": (False, ""),
        })
        info = detect_nginx_package_origin(ssh, "/home/umpay/ums/nginx/sbin/nginx")
        self.assertEqual(info["origin"], "source")
        self.assertEqual(info["package"], "")

    def test_english_not_owned_is_source(self):
        """英文 not owned 视为源码安装"""
        ssh = _FakeSSH({
            "rpm -qf": (False, "file /usr/sbin/nginx is not owned by any package"),
            "dpkg -S": (False, ""),
        })
        info = detect_nginx_package_origin(ssh, "/usr/sbin/nginx")
        self.assertEqual(info["origin"], "source")

    def test_rpm_package_name(self):
        """合法 rpm 包名视为包安装"""
        ssh = _FakeSSH({
            "rpm -qf": (True, "nginx"),
        })
        info = detect_nginx_package_origin(ssh, "/usr/sbin/nginx")
        self.assertEqual(info["origin"], "package")
        self.assertEqual(info["mgr"], "rpm")
        self.assertEqual(info["package"], "nginx")

    def test_deb_package_name(self):
        """合法 dpkg 包名视为包安装"""
        ssh = _FakeSSH({
            "rpm -qf": (False, ""),
            "dpkg -S": (True, "nginx-core: /usr/sbin/nginx"),
        })
        info = detect_nginx_package_origin(ssh, "/usr/sbin/nginx")
        self.assertEqual(info["origin"], "package")
        self.assertEqual(info["mgr"], "deb")
        self.assertEqual(info["package"], "nginx-core")


class ResolveNginxTreePathTests(SimpleTestCase):
    """收敛到 …/nginx 目录"""

    def test_conf_and_log_and_modules(self):
        """配置/日志/模块收敛到 nginx 目录"""
        self.assertEqual(
            resolve_nginx_tree_path("/etc/nginx/nginx.conf"),
            "/etc/nginx",
        )
        self.assertEqual(
            resolve_nginx_tree_path("/var/log/nginx/error.log"),
            "/var/log/nginx",
        )
        self.assertEqual(
            resolve_nginx_tree_path("/usr/lib64/nginx/modules"),
            "/usr/lib64/nginx",
        )
        self.assertEqual(
            resolve_nginx_tree_path("/var/lib/nginx/tmp/proxy"),
            "/var/lib/nginx",
        )

    def test_sbin_binary_not_expanded(self):
        """sbin/bin 下二进制不收敛到父目录"""
        self.assertEqual(
            resolve_nginx_tree_path("/usr/sbin/nginx"),
            "/usr/sbin/nginx",
        )
        self.assertEqual(
            resolve_nginx_tree_path("/usr/local/bin/nginx"),
            "/usr/local/bin/nginx",
        )

    def test_no_nginx_segment_unchanged(self):
        """无 nginx 目录段时保持原路径"""
        self.assertEqual(
            resolve_nginx_tree_path("/run/nginx.pid"),
            "/run/nginx.pid",
        )
        self.assertEqual(
            resolve_nginx_tree_path("/opt/app/conf/nginx.conf"),
            "/opt/app/conf/nginx.conf",
        )

    def test_dangerous_resolve_falls_back(self):
        """收敛结果若为危险根则回退原路径"""
        self.assertEqual(resolve_nginx_tree_path("/etc/nginx"), "/etc/nginx")

    def test_file_like_nginx_dir_vs_binary(self):
        """名为 nginx 的目录非文件；sbin 下为文件"""
        self.assertFalse(is_file_like_path("/etc/nginx"))
        self.assertTrue(is_file_like_path("/usr/sbin/nginx"))
        self.assertTrue(is_file_like_path("/etc/nginx/nginx.conf"))


class CoalesceDeleteTargetsTests(SimpleTestCase):
    """删除目标父子去重"""

    def test_keep_outermost(self):
        """父目录与子路径只留最外层"""
        result = coalesce_delete_targets([
            ("/etc/nginx/conf.d/test.conf", "file"),
            ("/etc/nginx", "dir"),
            ("/etc/nginx/conf.d", "dir"),
        ])
        self.assertEqual(result, [("/etc/nginx", "dir")])

    def test_sibling_paths_kept(self):
        """兄弟路径均保留"""
        result = coalesce_delete_targets([
            ("/etc/nginx", "dir"),
            ("/var/log/nginx", "dir"),
            ("/usr/sbin/nginx", "file"),
        ])
        paths = {p for p, _ in result}
        self.assertEqual(paths, {"/etc/nginx", "/var/log/nginx", "/usr/sbin/nginx"})

    def test_string_list_form(self):
        """支持纯路径列表"""
        self.assertEqual(
            coalesce_delete_targets([
                "/etc/nginx/conf.d",
                "/etc/nginx/conf.d/test.conf",
            ]),
            ["/etc/nginx/conf.d"],
        )


class ExtractNginxVPathTests(SimpleTestCase):
    """从 nginx -V 提取路径条目"""

    def test_extract_path_tokens(self):
        """提取 prefix 与 *-path 绝对路径"""
        parsed = {
            "prefix": "/opt/app",
            "params": [
                "--prefix=/opt/app",
                "--with-http_ssl_module",
                "--sbin-path=/opt/app/sbin/nginx",
                "--conf-path=/opt/app/conf/nginx.conf",
                "--error-log-path=/opt/app/logs/error.log",
                "--pid-path=/opt/app/logs/nginx.pid",
                "--add-module=/tmp/nginx-modules/foo",
            ],
        }
        entries = extract_path_entries_from_nginx_v(parsed)
        keys = [e["key"] for e in entries]
        self.assertIn("prefix", keys)
        self.assertIn("--sbin-path", keys)
        self.assertIn("--conf-path", keys)
        self.assertNotIn("--add-module", keys)
        prefix = next(e for e in entries if e["key"] == "prefix")
        self.assertTrue(prefix["required"])
        self.assertTrue(prefix["editable"])

    def test_extract_resolves_nginx_tree(self):
        """系统包路径在探测结果中已收敛到 nginx 目录"""
        parsed = {
            "prefix": "/usr/share/nginx",
            "params": [
                "--prefix=/usr/share/nginx",
                "--sbin-path=/usr/sbin/nginx",
                "--conf-path=/etc/nginx/nginx.conf",
                "--modules-path=/usr/lib64/nginx/modules",
                "--error-log-path=/var/log/nginx/error.log",
                "--pid-path=/run/nginx.pid",
            ],
        }
        entries = {e["key"]: e for e in extract_path_entries_from_nginx_v(parsed)}
        self.assertEqual(entries["--sbin-path"]["path"], "/usr/sbin/nginx")
        self.assertEqual(entries["--sbin-path"]["kind"], "file")
        self.assertEqual(entries["--conf-path"]["path"], "/etc/nginx")
        self.assertEqual(entries["--conf-path"]["kind"], "dir")
        self.assertEqual(entries["--modules-path"]["path"], "/usr/lib64/nginx")
        self.assertEqual(entries["--error-log-path"]["path"], "/var/log/nginx")
        self.assertEqual(entries["--pid-path"]["path"], "/run/nginx.pid")

    def test_same_nginx_tree_deduped_to_prefix(self):
        """全在同一 …/nginx 树下时收敛后同路径去重，仅留 --prefix"""
        parsed = {
            "prefix": "/opt/app/nginx",
            "params": [
                "--prefix=/opt/app/nginx",
                "--conf-path=/opt/app/nginx/etc/nginx.conf",
                "--error-log-path=/opt/app/nginx/log/error.log",
                "--http-log-path=/opt/app/nginx/log/access.log",
                "--pid-path=/opt/app/nginx/nginx.pid",
                "--lock-path=/opt/app/nginx/nginx.lock",
            ],
        }
        entries = extract_path_entries_from_nginx_v(parsed)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["key"], "prefix")
        self.assertEqual(entries[0]["path"], "/opt/app/nginx")


class IsUnderPathTests(SimpleTestCase):
    """路径包含判定（探测勾选锁定与执行去重共用）"""

    def test_child_under_parent(self):
        """子路径位于父目录下"""
        self.assertTrue(is_under_path("/opt/app/nginx", "/opt/app"))
        self.assertTrue(is_under_path("/opt/app/nginx/etc", "/opt/app/nginx"))

    def test_equal_is_under(self):
        """相等视为在树内"""
        self.assertTrue(is_under_path("/opt/app", "/opt/app"))

    def test_sibling_not_under(self):
        """兄弟或父相对子不算包含"""
        self.assertFalse(is_under_path("/opt/app", "/opt/app/nginx"))
        self.assertFalse(is_under_path("/var/log/nginx", "/etc/nginx"))


class UninstallGateTests(TestCase):
    """卸载门禁：对齐升级，要求 Nginx 可用"""

    def setUp(self):
        """准备凭证与在线节点"""
        User = get_user_model()
        self.user = User.objects.create_user(username="un_tester", password="x")
        self.cred = Credential.objects.create(
            name="un-cred",
            auth_type="password",
            username="root",
            password="secret",
            is_enabled=True,
            created_by=self.user,
        )
        self.node = Node.objects.create(
            hostname="host-a",
            ip="10.0.0.1",
            port=22,
            status="online",
            nginx_available=True,
            nginx_path="/opt/app/sbin/nginx",
            credential=self.cred,
            created_by=self.user,
        )

    def test_online_with_nginx_ok(self):
        """在线且 Nginx 可用可通过"""
        self.assertIsNone(uninstall_gate_message(self.node))

    def test_unavailable_rejected_even_with_path(self):
        """未检测到时即使有 nginx_path 也拒绝"""
        self.node.nginx_available = False
        self.node.nginx_path = "/opt/app/sbin/nginx"
        self.node.save(update_fields=["nginx_available", "nginx_path", "updated_at"])
        self.assertEqual(uninstall_gate_message(self.node), "未检测到 Nginx")

    def test_no_credential_rejected(self):
        """无凭证拒绝"""
        self.node.credential = None
        self.node.save(update_fields=["credential", "updated_at"])
        self.assertEqual(uninstall_gate_message(self.node), "未配置凭证")


class UninstallCreateOptionsTests(TestCase):
    """创建批次时按节点写入 selected_paths / options_json"""

    def setUp(self):
        """准备可卸载节点"""
        User = get_user_model()
        self.user = User.objects.create_user(username="un_creator", password="x")
        self.cred = Credential.objects.create(
            name="un-cred2",
            auth_type="password",
            username="root",
            password="secret",
            is_enabled=True,
            created_by=self.user,
        )
        self.node = Node.objects.create(
            hostname="host-b",
            ip="10.0.0.2",
            port=22,
            status="online",
            nginx_available=True,
            nginx_path="/opt/app/sbin/nginx",
            credential=self.cred,
            created_by=self.user,
        )

    @patch("apps.nginx_uninstall.services.threading.Thread")
    def test_selected_paths_persisted(self, mock_thread):
        """selected_paths 写入 options_json，未勾选备份则 backup_path 为空"""
        mock_thread.return_value.start = lambda: None
        result = create_uninstall_batch_from_data(self.user, {
            "stop_if_running": True,
            "nodes": [{
                "id": self.node.id,
                "prefix": "/opt/app",
                "selected_paths": [
                    {"key": "prefix", "path": "/opt/app", "kind": "dir"},
                    {"key": "--conf-path", "path": "/var/log/outside.conf", "kind": "file"},
                    {"key": "work_dir", "path": "/tmp/nginx-upgrade", "kind": "dir"},
                ],
            }],
        })
        self.assertTrue(result.get("success"), result)
        task = NginxUninstallTask.objects.get(batch_number=result["source_batch"])
        self.assertEqual(task.backup_path, "")
        opts = json.loads(task.options_json)
        self.assertFalse(opts["remove_backup"])
        self.assertTrue(opts["remove_workdir"])
        self.assertFalse(opts["remove_modules"])
        self.assertEqual(len(opts.get("extra_paths") or []), 1)
        self.assertEqual(opts["extra_paths"][0]["path"], "/var/log/outside.conf")
        self.assertEqual(task.resolved_prefix, "/opt/app")

    @patch("apps.nginx_uninstall.services.threading.Thread")
    def test_conf_path_resolved_on_create(self, mock_thread):
        """创建时将 conf 文件路径收敛为 nginx 目录"""
        mock_thread.return_value.start = lambda: None
        result = create_uninstall_batch_from_data(self.user, {
            "nodes": [{
                "id": self.node.id,
                "selected_paths": [
                    {"key": "prefix", "path": "/opt/app", "kind": "dir"},
                    {"key": "--conf-path", "path": "/etc/nginx/nginx.conf", "kind": "file"},
                ],
            }],
        })
        self.assertTrue(result.get("success"), result)
        task = NginxUninstallTask.objects.get(batch_number=result["source_batch"])
        opts = json.loads(task.options_json)
        self.assertEqual(opts["extra_paths"][0]["path"], "/etc/nginx")
        self.assertEqual(opts["extra_paths"][0]["kind"], "dir")

    @patch("apps.nginx_uninstall.services.threading.Thread")
    def test_dangerous_prefix_rejected(self, mock_thread):
        """危险 prefix 拒绝"""
        mock_thread.return_value.start = lambda: None
        result = create_uninstall_batch_from_data(self.user, {
            "nodes": [{
                "id": self.node.id,
                "selected_paths": [
                    {"key": "prefix", "path": "/opt", "kind": "dir"},
                ],
            }],
        })
        self.assertFalse(result.get("success"))
        self.assertIn("禁止删除路径", result.get("message", ""))


class PreviewNodesParallelTests(TestCase):
    """多节点预览并行且按 id 稳定排序"""

    def setUp(self):
        """准备两台可卸载节点"""
        User = get_user_model()
        self.user = User.objects.create_user(username="un_preview", password="x")
        self.cred = Credential.objects.create(
            name="un-cred-preview",
            auth_type="password",
            username="root",
            password="secret",
            is_enabled=True,
            created_by=self.user,
        )
        self.n1 = Node.objects.create(
            hostname="host-p1",
            ip="10.0.1.1",
            port=22,
            status="online",
            nginx_available=True,
            nginx_path="/opt/app/sbin/nginx",
            credential=self.cred,
            created_by=self.user,
        )
        self.n2 = Node.objects.create(
            hostname="host-p2",
            ip="10.0.1.2",
            port=22,
            status="online",
            nginx_available=True,
            nginx_path="/opt/app/sbin/nginx",
            credential=self.cred,
            created_by=self.user,
        )

    @patch("apps.nginx_uninstall.services.batch_max_count", return_value=5)
    @patch("apps.nginx_uninstall.services._preview_one_node")
    def test_preview_order_matches_node_id(self, mock_one, _mock_batch):
        """并行完成后按节点 id 升序返回"""

        def _fake(node, work_dir):
            return {
                "id": node.id,
                "hostname": node.hostname,
                "ip": node.ip,
                "nginx_path": node.nginx_path or "",
                "nginx_available": True,
                "eligible": True,
                "gate_message": "",
                "prefix": "/opt/app",
                "prefix_source": "mock",
                "backup_path": "/tmp/b",
                "work_dir": work_dir,
                "modules_dir": "",
                "paths": [],
                "running": False,
                "running_error": "",
                "credential_username": "root",
                "manage_mode": "binary",
                "manage_unit": "",
                "can_manage_systemd": False,
                "install_origin": "source",
                "package_mgr": "",
                "package_name": "",
            }

        mock_one.side_effect = _fake
        # 故意反序传入
        result = preview_nodes([self.n2.id, self.n1.id])
        self.assertTrue(result.get("success"), result)
        ids = [n["id"] for n in result["nodes"]]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(ids, [self.n1.id, self.n2.id])
        self.assertEqual(mock_one.call_count, 2)