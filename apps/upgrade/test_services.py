"""Nginx 升级服务层测试。"""

from django.test import TestCase

from apps.upgrade.services import (
    parse_nginx_v_output,
    _tokenize_configure_args,
    _tokenize_configure_args_fallback,
    _format_configure_token,
    _join_configure_opts,
    compute_target_configure_opts,
    _tail_output,
    _get_extract_dir,
    enrich_third_party_module_paths,
    resolve_third_party_module_path,
)


class TestParseNginxVOutput(TestCase):
    def test_empty_input(self):
        result = parse_nginx_v_output("")
        self.assertEqual(result["version"], "")
        self.assertEqual(result["configure_opts"], "")

    def test_full_output(self):
        output = (
            "nginx version: nginx/1.24.0\n"
            "configure arguments: --prefix=/usr/local/nginx "
            "--with-http_ssl_module --with-http_v2_module"
        )
        result = parse_nginx_v_output(output)
        self.assertEqual(result["version"], "1.24.0")
        self.assertEqual(result["prefix"], "/usr/local/nginx")
        self.assertIn("--with-http_ssl_module", result["builtin_modules"])

    def test_with_add_module(self):
        output = (
            "nginx version: nginx/1.25.0\n"
            "configure arguments: --prefix=/opt/nginx "
            "--with-http_ssl_module "
            "--add-module=/path/to/module"
        )
        result = parse_nginx_v_output(output)
        self.assertEqual(result["version"], "1.25.0")
        self.assertEqual(len(result["third_party_modules"]), 1)
        self.assertIn("--add-module=/path/to/module", result["third_party_modules"])

    def test_with_sbin_path(self):
        output = (
            "nginx version: nginx/1.22.0\n"
            "configure arguments: --prefix=/opt/nginx "
            "--sbin-path=/opt/nginx/sbin/nginx"
        )
        result = parse_nginx_v_output(output)
        self.assertEqual(result["binary_path"], "/opt/nginx/sbin/nginx")


class TestTokenizeConfigureArgs(TestCase):
    def test_simple_args(self):
        tokens = _tokenize_configure_args("--prefix=/opt/nginx --with-http_ssl_module")
        self.assertIn("--prefix=/opt/nginx", tokens)
        self.assertIn("--with-http_ssl_module", tokens)

    def test_with_quoted_values(self):
        tokens = _tokenize_configure_args("--with-cc-opt='-O2 -g' --prefix=/opt/nginx")
        self.assertIn("--prefix=/opt/nginx", tokens)

    def test_empty(self):
        tokens = _tokenize_configure_args("")
        self.assertEqual(tokens, [])

    def test_fallback(self):
        tokens = _tokenize_configure_args_fallback(
            "--prefix=/opt/nginx --with-http_ssl_module"
        )
        self.assertIn("--prefix=/opt/nginx", tokens)


class TestFormatConfigureToken(TestCase):
    def test_simple_token(self):
        result = _format_configure_token("--with-http_ssl_module")
        self.assertEqual(result, "--with-http_ssl_module")

    def test_token_with_value(self):
        result = _format_configure_token("--prefix=/opt/nginx")
        self.assertEqual(result, "--prefix=/opt/nginx")

    def test_empty_token(self):
        result = _format_configure_token("")
        self.assertEqual(result, "")


class TestJoinConfigureOpts(TestCase):
    def test_single_line(self):
        tokens = ["--prefix=/opt/nginx", "--with-http_ssl_module"]
        result = _join_configure_opts(tokens, multiline=False)
        self.assertIn("--prefix=/opt/nginx", result)
        self.assertIn("--with-http_ssl_module", result)

    def test_multiline(self):
        tokens = ["--prefix=/opt/nginx", "--with-http_ssl_module"]
        result = _join_configure_opts(tokens, multiline=True)
        self.assertIn("\\", result)

    def test_empty(self):
        result = _join_configure_opts([])
        self.assertEqual(result, "")


class TestComputeTargetConfigureOpts(TestCase):
    def test_add_modules(self):
        current = ["--prefix=/opt/nginx", "--with-http_ssl_module"]
        result = compute_target_configure_opts(
            current_params=current,
            added_modules=["--with-http_v2_module"],
            removed_modules=[],
            added_third_party=[],
        )
        self.assertIn("--with-http_v2_module", result)
        self.assertIn("--with-http_ssl_module", result)

    def test_remove_modules(self):
        current = ["--prefix=/opt/nginx", "--with-http_ssl_module"]
        result = compute_target_configure_opts(
            current_params=current,
            added_modules=[],
            removed_modules=["--with-http_ssl_module"],
            added_third_party=[],
        )
        self.assertNotIn("--with-http_ssl_module", result)

    def test_add_third_party(self):
        current = ["--prefix=/opt/nginx"]
        third_party = [{"name": "echo", "module_path": "/tmp/nginx-modules/echo"}]
        result = compute_target_configure_opts(
            current_params=current,
            added_modules=[],
            removed_modules=[],
            added_third_party=third_party,
        )
        self.assertIn("--add-module=/tmp/nginx-modules/echo", result)


class TestTailOutput(TestCase):
    def test_short_output(self):
        result = _tail_output("line1\nline2\nline3", max_lines=80)
        self.assertEqual(result, "line1\nline2\nline3")

    def test_long_output(self):
        lines = [f"line{i}" for i in range(100)]
        text = "\n".join(lines)
        result = _tail_output(text, max_lines=80)
        self.assertEqual(len(result.splitlines()), 80)

    def test_empty(self):
        self.assertEqual(_tail_output(""), "")


class TestResolveThirdPartyModulePath(TestCase):
    def test_existing_path(self):
        tp = {"name": "echo", "module_path": "/custom/path"}
        result = resolve_third_party_module_path(tp, "/tmp/work")
        self.assertEqual(result, "/custom/path")

    def test_derived_path(self):
        tp = {"name": "echo"}
        result = resolve_third_party_module_path(tp, "/tmp/work")
        self.assertEqual(result, "/tmp/work/nginx-modules/echo")

    def test_empty_name(self):
        tp = {"name": ""}
        result = resolve_third_party_module_path(tp, "/tmp/work")
        self.assertEqual(result, "/tmp/work/nginx-modules/module-0")


class TestEnrichThirdPartyModulePaths(TestCase):
    def test_enrich(self):
        modules = [{"name": "echo"}, {"name": "lua", "module_path": "/existing/lua"}]
        result = enrich_third_party_module_paths(modules, "/tmp/work")
        self.assertEqual(result[0]["module_path"], "/tmp/work/nginx-modules/echo")
        self.assertEqual(result[1]["module_path"], "/existing/lua")
