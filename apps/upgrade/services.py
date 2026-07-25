"""Nginx 升级模块 - 服务层"""
import json
import re
import os
import shlex
import time
import threading
from datetime import datetime, timezone as dt_timezone

from django.conf import settings
from django.db import close_old_connections
from django.utils import timezone

from utils.ssh import SSHClient, _build_ssh_client
from utils.setting_service import get_setting


def parse_nginx_v_output(raw_output):
    """将 nginx -V 的输出解析为结构化参数

    Args:
        raw_output: nginx -V 2>&1 的原始输出

    Returns:
        dict: {
            "version": "nginx/1.24.0",
            "configure_opts": "--prefix=/usr/local/nginx --with-http_ssl_module ...",
            "prefix": "/usr/local/nginx",
            "binary_path": "/usr/local/nginx/sbin/nginx",
            "params": ["--prefix=/usr/local/nginx", "--with-http_ssl_module", ...],
            "builtin_modules": ["--with-http_ssl_module", ...],
            "third_party_modules": ["--add-module=/path/to/module", ...],
        }
    """
    result = {
        "version": "",
        "configure_opts": "",
        "prefix": "/usr/local/nginx",
        "binary_path": "/usr/local/nginx/sbin/nginx",
        "params": [],
        "builtin_modules": [],
        "third_party_modules": [],
    }

    if not raw_output:
        return result

    # 提取版本号
    version_match = re.search(r"nginx version:\s*nginx/([\d.]+)", raw_output)
    if version_match:
        result["version"] = f"nginx/{version_match.group(1)}"

    # 提取 configure arguments
    opts_match = re.search(r"configure arguments:\s*(.+)", raw_output, re.DOTALL)
    if not opts_match:
        return result

    configure_opts = opts_match.group(1).strip()
    result["configure_opts"] = configure_opts

    # 解析各个参数
    tokens = _tokenize_configure_args(configure_opts)
    result["params"] = tokens

    # 分离内置模块和第三方模块
    for token in tokens:
        if token.startswith("--add-module=") or token.startswith("--add-dynamic-module="):
            result["third_party_modules"].append(token)
            continue
        result["builtin_modules"].append(token)

        # 提取 --prefix
        if token.startswith("--prefix="):
            result["prefix"] = token.split("=", 1)[1]
        # 提取 --sbin-path
        if token.startswith("--sbin-path="):
            result["binary_path"] = token.split("=", 1)[1]

    # 如果没有显式指定 sbin-path，则推导
    if not any(token.startswith("--sbin-path=") for token in tokens):
        result["binary_path"] = result["prefix"].rstrip("/") + "/sbin/nginx"

    return result


def _tokenize_configure_args(opts_str):
    """将 configure 参数字符串拆分为 token 列表（支持引号内空格）

    每个形如 --xxx 或 --xxx=yyy 为一组；--with-cc-opt='-O2 -g' 计为单个 token。
    shlex 会去掉外壳引号，token 内保留等号后的原始值。
    """
    if not opts_str or not str(opts_str).strip():
        return []
    # 多行续行符压成空格，便于 shlex 解析
    flat = re.sub(r"\\\s*\n\s*", " ", str(opts_str))
    flat = re.sub(r"\s+", " ", flat).strip()
    try:
        tokens = shlex.split(flat, posix=True)
    except ValueError:
        tokens = []
    # 仅保留 configure 风格参数；异常时回退为引号感知扫描
    tokens = [t for t in tokens if t.startswith("--")]
    if tokens:
        return tokens
    return _tokenize_configure_args_fallback(flat)


def _tokenize_configure_args_fallback(opts_str):
    """在 shlex 失败时按引号边界扫描 -- 参数"""
    tokens = []
    i = 0
    s = opts_str or ""
    n = len(s)
    while i < n:
        while i < n and s[i].isspace():
            i += 1
        if i >= n:
            break
        if not (s[i:i + 2] == "--"):
            i += 1
            continue
        start = i
        i += 2
        while i < n and (s[i].isalnum() or s[i] in "-_"):
            i += 1
        if i < n and s[i] == "=":
            i += 1
            if i < n and s[i] in ("'", '"'):
                quote = s[i]
                i += 1
                while i < n and s[i] != quote:
                    if s[i] == "\\" and i + 1 < n:
                        i += 2
                        continue
                    i += 1
                if i < n and s[i] == quote:
                    i += 1
            else:
                while i < n and not s[i].isspace():
                    i += 1
        raw = s[start:i]
        # 去掉 = 后外壳引号，与 shlex 行为一致
        if "=" in raw:
            key, val = raw.split("=", 1)
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                val = val[1:-1]
            tokens.append(f"{key}={val}")
        else:
            tokens.append(raw)
    return tokens


def _format_configure_token(token):
    """将单个 token 格式化为可安全写入 shell 的 configure 参数"""
    if not token:
        return ""
    if "=" not in token:
        return token
    key, val = token.split("=", 1)
    # 含空格或特殊字符时对值重新加引号
    if any(c in val for c in " \t\"'\\$`|&;<>()"):
        return f"{key}={shlex.quote(val)}"
    return token


def _join_configure_opts(tokens, multiline=True):
    """将 token 列表安全拼接为 configure 参数字符串"""
    formatted = [_format_configure_token(t) for t in tokens if t]
    if not formatted:
        return ""
    if multiline:
        return " \\\n    ".join(formatted)
    return " ".join(formatted)


def fetch_nginx_v_from_node(node):
    """从目标节点获取 nginx -V 输出

    Args:
        node: Node 实例（含 credential 关联）

    Returns:
        tuple: (success: bool, data_or_error: dict|str)
    """
    from apps.nodes.views import _get_node_credential

    credential = _get_node_credential(node)
    if not credential:
        return False, "节点未配置有效的 SSH 凭证"

    auth_kwargs = _build_auth_kwargs(credential)

    try:
        # 确定 nginx 二进制路径
        nginx_path = node.nginx_path or "nginx"
        command = f"{nginx_path} -V 2>&1"

        with SSHClient(node.ip, node.port, credential.username, **auth_kwargs) as ssh:
            success, output = ssh.execute_command(command)

        if not success:
            return False, f"执行 nginx -V 失败: {output}"

        parsed = parse_nginx_v_output(output)
        if not parsed["configure_opts"]:
            return False, f"无法解析 nginx -V 输出: {output}"

        return True, parsed
    except Exception as e:
        return False, str(e)


def _build_auth_kwargs(credential):
    """根据凭证类型构建认证参数"""
    if credential.auth_type == "password":
        return {"password": credential.get_password()}
    else:
        return {"private_key": credential.get_private_key()}


def compute_target_configure_opts(current_params, added_modules, removed_modules, added_third_party):
    """基于当前参数 + 增减生成最终的编译参数

    Args:
        current_params: 当前编译参数列表
        added_modules: 要新增的内置模块列表
        removed_modules: 要移除的参数列表
        added_third_party: 要新增的第三方模块列表

    Returns:
        str: 合并后的 configure 参数字符串
    """
    # 去除已移除的参数
    remaining = [p for p in current_params if p not in removed_modules]

    # 添加新模块（去重）
    existing_set = set(remaining)
    for mod in added_modules:
        if mod not in existing_set:
            remaining.append(mod)
            existing_set.add(mod)

    # 添加第三方模块
    for tp in added_third_party:
        if isinstance(tp, dict):
            module_path = tp.get("module_path", "")
            if module_path and not any(module_path in r for r in remaining):
                remaining.append(f"--add-module={module_path}")
        elif isinstance(tp, str) and tp not in existing_set:
            remaining.append(tp)
            existing_set.add(tp)

    return _join_configure_opts(remaining, multiline=True)


def _tail_output(text, max_lines=80):
    """截取输出末尾若干行，便于弹窗展示真实编译错误"""
    if not text:
        return ""
    lines = text.strip().splitlines()
    if len(lines) <= max_lines:
        return "\n".join(lines)
    return "\n".join(lines[-max_lines:])


def run_upgrade_task(task_id):
    """执行升级任务（在线程中调用）

    Args:
        task_id: NginxUpgradeTask 主键
    """
    from .models import NginxUpgradeTask
    from apps.releases.models import TaskCenterTask

    close_old_connections()

    try:
        task = NginxUpgradeTask.objects.select_related("node", "source_package", "operator").get(pk=task_id)
    except NginxUpgradeTask.DoesNotExist:
        return

    node = task.node
    log_lines = []

    def _persist_log(current_step=None):
        """将内存日志刷入数据库"""
        task.log_output = "\n".join(log_lines)
        if current_step is not None:
            task.current_step = current_step
        NginxUpgradeTask.objects.filter(pk=task_id).update(
            log_output=task.log_output,
            current_step=task.current_step,
            updated_at=timezone.now(),
        )

    def log(msg):
        """追加一条带时间戳的摘要日志"""
        timestamp = timezone.now().strftime("%Y-%m-%d %H:%M:%S")
        log_lines.append(f"[{timestamp}] {msg}")
        _persist_log(current_step=msg)

    def log_raw(output):
        """追加命令原始输出整块（不逐行打时间戳，避免刷爆日志）"""
        text = (output or "").rstrip("\n")
        if not text:
            return
        log_lines.append(text)
        _persist_log()

    def update_status(status, progress, **kwargs):
        updates = {"status": status, "progress": progress, "updated_at": timezone.now()}
        updates.update(kwargs)
        NginxUpgradeTask.objects.filter(pk=task_id).update(**updates)

    try:
        from apps.nodes.views import _get_node_credential
        credential = _get_node_credential(node)
        if not credential:
            update_status("failed", 0, error_message="节点未配置有效的 SSH 凭证")
            return

        auth_kwargs = _build_auth_kwargs(credential)
        if "password" in auth_kwargs:
            auth_kwargs_copy = {"password": auth_kwargs["password"]}
        else:
            auth_kwargs_copy = {"private_key": auth_kwargs["private_key"]}

        # ---- Step 1: 获取 nginx -V（优先写入当前版本，便于失败任务列表展示）----
        update_status("fetching_config", 5)
        log("获取当前 Nginx 编译参数...")
        _ensure_remote_dir(node.ip, node.port, credential.username, **auth_kwargs_copy)

        success, parsed = fetch_nginx_v_from_node(node)
        if not success:
            log(f"获取编译参数失败: {parsed}")
            update_status("failed", 5, error_message=str(parsed))
            return
        log(f"当前版本: {parsed['version']}")
        log(f"当前 prefix: {parsed['prefix']}")

        # 更新任务信息
        NginxUpgradeTask.objects.filter(pk=task_id).update(
            current_version=parsed["version"],
            current_configure_opts=parsed["configure_opts"],
            current_configure_path=parsed["prefix"],
            current_binary_path=parsed["binary_path"],
        )

        # ---- Step 2: 编译工具预检（仅 gcc/make；缺库由 configure/make 回报）----
        update_status("fetching_config", 10)
        log("检查编译工具 gcc / make ...")
        check_cmd = "command -v gcc >/dev/null && command -v make >/dev/null && echo 'DEPS_OK'"
        with SSHClient(node.ip, node.port, credential.username, **auth_kwargs_copy) as ssh:
            success, output = ssh.execute_command(check_cmd)
        if not success or "DEPS_OK" not in (output or ""):
            log(f"编译工具检查失败: {output}")
            update_status(
                "failed", 10,
                error_message=f"编译工具缺失: {output}\n请安装 gcc 与 make；其余依赖（如 pcre/zlib/openssl/libxslt）由 ./configure 检测",
            )
            return
        log("编译工具检查通过")

        # ---- Step 3: 上传源码包 ----
        source_package = task.source_package
        if not source_package or not source_package.package_file:
            log("未选择源码包")
            update_status("failed", 10, error_message="未选择源码包")
            return

        update_status("uploading_package", 20)
        log("上传源码包到目标节点...")
        work_dir = task.remote_work_dir
        package_filename = os.path.basename(source_package.package_file.name)
        remote_package_path = f"{work_dir}/{package_filename}"

        _ensure_remote_dir(node.ip, node.port, credential.username, **auth_kwargs_copy)

        # SFTP 上传
        local_path = source_package.package_file.path
        from utils.ssh import upload_file_via_sftp
        success, msg = upload_file_via_sftp(
            node.ip, node.port, credential.username,
            local_path=local_path, remote_path=remote_package_path,
            **auth_kwargs_copy,
        )
        if not success:
            log(f"上传源码包失败: {msg}")
            update_status("failed", 20, error_message=f"上传源码包失败: {msg}")
            return
        log(f"源码包已上传到 {remote_package_path}")

        # 校验 MD5
        from utils.ssh import check_remote_file_md5
        success, remote_md5 = check_remote_file_md5(
            node.ip, node.port, credential.username,
            file_path=remote_package_path, **auth_kwargs_copy,
        )
        if success and source_package.file_md5 and remote_md5 != source_package.file_md5:
            log(f"MD5 校验失败: 本地={source_package.file_md5[:8]}... 远程={remote_md5[:8]}...")
            update_status("failed", 20, error_message="源码包 MD5 校验失败，传输可能损坏")
            return
        log(f"MD5 校验通过 ({remote_md5[:16]}...)")

        # ---- Step 4: 远程解压 ----
        update_status("uploading_package", 30)
        log("解压源码包...")
        extract_dir = _extract_package_name(package_filename)
        with SSHClient(node.ip, node.port, credential.username, **auth_kwargs_copy) as ssh:
            success, output = ssh.execute_command(
                f"cd {work_dir} && tar -xzf {remote_package_path} 2>&1"
            )
        if not success:
            log(f"解压失败: {output}")
            update_status("failed", 30, error_message=f"解压失败: {output}")
            return
        log(f"源码包解压完成: {work_dir}/{extract_dir}")

        # ---- Step 5: 下载第三方模块 ----
        update_status("downloading_modules", 40)
        third_party = json.loads(task.added_third_party or "[]")
        if third_party:
            log(f"下载 {len(third_party)} 个第三方模块...")
            modules_dir = f"{work_dir}/nginx-modules"
            with SSHClient(node.ip, node.port, credential.username, **auth_kwargs_copy) as ssh:
                ssh.execute_command(f"mkdir -p {modules_dir}")
                for idx, tp in enumerate(third_party):
                    if isinstance(tp, dict):
                        name = tp.get("name", f"module-{idx}")
                        git_url = tp.get("git_url", "")
                        branch = tp.get("branch", "master")
                        module_path = f"{modules_dir}/{name}"
                        if git_url:
                            cmd = f"cd {modules_dir} && git clone --depth 1 --branch {branch} {git_url} {name} 2>&1"
                            success, output = ssh.execute_command(cmd)
                            if not success:
                                log(f"下载第三方模块 {name} 失败: {output}")
                                update_status("failed", 40, error_message=f"下载第三方模块 {name} 失败: {output}")
                                return
                            tp["module_path"] = module_path
                            log(f"第三方模块 {name} 下载完成: {module_path}")
            # 更新任务中的第三方模块路径
            NginxUpgradeTask.objects.filter(pk=task_id).update(
                added_third_party=json.dumps(third_party, ensure_ascii=False)
            )
        else:
            log("无第三方模块需要下载")

        # ---- Step 6: 备份旧二进制 ----
        update_status("backing_up", 50)
        binary_path = task.current_binary_path or parsed["binary_path"]
        timestamp = timezone.now().strftime("%Y%m%d%H%M%S")
        backup_path = f"{binary_path}.old.{timestamp}"
        with SSHClient(node.ip, node.port, credential.username, **auth_kwargs_copy) as ssh:
            success, output = ssh.execute_command(f"cp {binary_path} {backup_path} 2>&1")
        if not success:
            log(f"备份旧二进制失败: {output}")
            update_status("failed", 50, error_message=f"备份旧二进制失败: {output}")
            return
        log(f"旧二进制已备份到: {backup_path}")
        NginxUpgradeTask.objects.filter(pk=task_id).update(old_binary_backup=backup_path)

        # ---- Step 7: 执行 configure ----
        update_status("configuring", 55)
        log("执行 ./configure ...")
        target_opts = task.target_configure_opts or ""
        # 先分词再安全拼装，避免引号内空格被拆碎
        opt_tokens = _tokenize_configure_args(target_opts)
        if not opt_tokens:
            opt_tokens = [
                line.strip().rstrip("\\").strip()
                for line in target_opts.split("\n")
                if line.strip()
            ]
        target_opts_single = _join_configure_opts(opt_tokens, multiline=False)
        configure_cmd = f"cd {work_dir}/{extract_dir} && ./configure {target_opts_single} 2>&1"
        log(f"configure 命令: {configure_cmd}")
        with SSHClient(node.ip, node.port, credential.username, **auth_kwargs_copy) as ssh:
            success, output = ssh.execute_command(configure_cmd)
        log_raw(output)
        if not success:
            log("configure 失败")
            update_status("failed", 55, error_message=f"configure 失败:\n{_tail_output(output)}")
            return
        log("configure 成功")

        # ---- Step 8: 执行 make ----
        update_status("compiling", 65)
        make_jobs = task.make_jobs or 4
        make_cmd = f"cd {work_dir}/{extract_dir} && make -j{make_jobs} 2>&1"
        log(f"执行 make -j{make_jobs} ...")
        with SSHClient(node.ip, node.port, credential.username, **auth_kwargs_copy) as ssh:
            success, output = ssh.execute_command(make_cmd)
        log_raw(output)
        if not success:
            log("make 失败")
            update_status("failed", 65, error_message=f"make 失败:\n{_tail_output(output)}")
            return
        log("make 编译成功")

        # ---- Step 9: make install 覆盖安装（替代手写 cp objs/nginx）----
        update_status("replacing_binary", 80)
        install_cmd = f"cd {work_dir}/{extract_dir} && make install 2>&1"
        log("执行 make install 安装新版本...")
        with SSHClient(node.ip, node.port, credential.username, **auth_kwargs_copy) as ssh:
            success, output = ssh.execute_command(install_cmd)
        log_raw(output)
        if not success:
            log("make install 失败")
            update_status("failed", 80, error_message=f"make install 失败:\n{_tail_output(output)}")
            return
        log("make install 完成，二进制已覆盖安装")

        # ---- Step 10: nginx -t 语法检查 ----
        update_status("verifying", 85)
        log("执行 nginx -t 语法检查...")
        from utils.ssh import execute_nginx_test
        nginx_bin = task.current_binary_path or parsed["binary_path"]
        success, output = execute_nginx_test(
            node.ip, node.port, credential.username,
            nginx_path=nginx_bin, **auth_kwargs_copy,
        )
        log_raw(output)
        if not success:
            log("语法检查失败，准备回滚旧二进制")
            _rollback_binary(node, credential, binary_path, backup_path, auth_kwargs_copy, log)
            update_status("failed", 85, error_message=f"nginx -t 语法检查失败，已自动回滚:\n{output}")
            return
        log("nginx -t 语法检查通过")

        # ---- Step 11: 按启动方式 reload（替代硬编码 PID 的 USR2 平滑升级）----
        update_status("upgrading", 90)
        from utils.nginx_ops import reload_nginx
        log("检测 Nginx 启动方式并执行 reload...")
        success, result = reload_nginx(
            node.ip, node.port, credential.username,
            nginx_path=nginx_bin, log_fn=log, **auth_kwargs_copy,
        )
        if not success:
            log(f"reload 失败: {result}")
            _rollback_binary(node, credential, binary_path, backup_path, auth_kwargs_copy, log)
            update_status("failed", 90, error_message=f"Nginx reload 失败: {result}")
            return
        log(f"reload 完成: {result}")

        # ---- Step 12: 最终验证 ----
        update_status("verifying", 95)
        log("最终验证...")
        from utils.ssh import get_nginx_version
        ver_ok, ver_info = get_nginx_version(
            node.ip, node.port, credential.username,
            nginx_path=nginx_bin, **auth_kwargs_copy,
        )
        if ver_ok:
            log(f"新版本: {ver_info}")
        else:
            verify_cmd = f"{nginx_bin} -v 2>&1"
            with SSHClient(node.ip, node.port, credential.username, **auth_kwargs_copy) as ssh:
                _, output = ssh.execute_command(verify_cmd)
            log(f"新版本: {(output or '').strip()}")

        # 完成
        update_status("success", 100, finished_at=timezone.now())
        log("✅ Nginx 编译升级成功完成!")

        # 更新节点 nginx_version（优先真实 -v，失败则回退目标版本）
        from apps.nodes.models import Node as NodeModel
        target_ver = task.target_version or source_package.version
        if ver_ok and ver_info:
            # get_nginx_version 返回纯版本号，节点字段统一 nginx/x.y.z
            node_ver = ver_info if str(ver_info).startswith("nginx/") else f"nginx/{ver_info}"
        else:
            node_ver = f"nginx/{target_ver}"
        NodeModel.objects.filter(pk=node.pk).update(nginx_version=node_ver)

    except Exception as e:
        log(f"升级过程发生异常: {str(e)}")
        try:
            NginxUpgradeTask.objects.filter(pk=task_id).update(
                status="failed", error_message=str(e), finished_at=timezone.now(),
                log_output="\n".join(log_lines) if log_lines else "",
            )
        except Exception:
            pass


def _ensure_remote_dir(host, port, username, password=None, private_key=None):
    """确保远程目录存在"""
    from utils.ssh import SSHClient
    work_dir = get_setting("upgrade.default_work_dir", "/tmp/nginx-upgrade")
    # 这里只是确保关键的 work_dir 存在,在 run_upgrade_task 中会确保完整路径
    try:
        with SSHClient(host, port, username, password=password, private_key=private_key) as ssh:
            ssh.execute_command(f"mkdir -p {work_dir}")
    except Exception:
        pass


def _extract_package_name(filename):
    """从文件名提取解压后的目录名，如 nginx-1.26.1.tar.gz → nginx-1.26.1"""
    name = filename
    if name.endswith(".tar.gz"):
        name = name[:-7]
    elif name.endswith(".tgz"):
        name = name[:-4]
    return name


def _rollback_binary(node, credential, binary_path, backup_path, auth_kwargs, log_fn):
    """回滚二进制到备份版本"""
    try:
        with SSHClient(node.ip, node.port, credential.username, **auth_kwargs) as ssh:
            success, output = ssh.execute_command(f"cp {backup_path} {binary_path} 2>&1")
            if success:
                log_fn(f"已回滚二进制: {backup_path} → {binary_path}")
            else:
                log_fn(f"回滚失败: {output}")
    except Exception as e:
        log_fn(f"回滚异常: {str(e)}")