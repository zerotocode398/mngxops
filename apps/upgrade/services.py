"""Nginx 升级模块 - 服务层"""
import json
import re
import os
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
    """将 configure 参数字符串拆分为 token 列表

    每个形如 --xxx 或 --xxx=yyy 为一组
    """
    tokens = []
    # 使用正则匹配 -- 开头的参数（值可能包含等号后的内容，但不能含空格分隔的下一个 -- 参数）
    for match in re.finditer(r"--[\w\-]+(?:=[^\s]*)?", opts_str):
        tokens.append(match.group(0))
    return tokens


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

    return " \\\n    ".join(remaining)


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

    def log(msg):
        timestamp = timezone.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{timestamp}] {msg}"
        log_lines.append(line)
        task.log_output = "\n".join(log_lines)
        task.current_step = msg
        NginxUpgradeTask.objects.filter(pk=task_id).update(
            log_output=task.log_output, current_step=task.current_step, updated_at=timezone.now()
        )

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

        # ---- Step 1: 环境检查 ----
        update_status("fetching_config", 5)
        log("开始环境检查...")
        _ensure_remote_dir(node.ip, node.port, credential.username, **auth_kwargs_copy)

        # 检查编译依赖
        check_cmd = "which gcc && which make && (dpkg -l | grep -q libpcre3-dev || rpm -q pcre-devel) && echo 'DEPS_OK'"
        with SSHClient(node.ip, node.port, credential.username, **auth_kwargs_copy) as ssh:
            success, output = ssh.execute_command(check_cmd)
        if not success or "DEPS_OK" not in output:
            log(f"编译环境依赖检查失败: {output}")
            update_status("failed", 5, error_message=f"编译环境依赖缺失: {output}\n请安装 gcc, make, pcre-devel, zlib-devel, openssl-devel")
            return
        log("编译环境依赖检查通过")

        # ---- Step 2: 获取 nginx -V ----
        update_status("fetching_config", 10)
        log("获取当前 Nginx 编译参数...")
        success, parsed = fetch_nginx_v_from_node(node)
        if not success:
            log(f"获取编译参数失败: {parsed}")
            update_status("failed", 10, error_message=str(parsed))
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
        target_opts = task.target_configure_opts
        # 将 \n    (多行格式) 转为单行
        target_opts_single = " ".join(line.strip().rstrip("\\") for line in target_opts.split("\n") if line.strip())
        configure_cmd = f"cd {work_dir}/{extract_dir} && ./configure {target_opts_single} 2>&1"
        log(f"configure 命令: {configure_cmd[:200]}...")
        with SSHClient(node.ip, node.port, credential.username, **auth_kwargs_copy) as ssh:
            success, output = ssh.execute_command(configure_cmd)
        if not success:
            log(f"configure 失败: {output}")
            update_status("failed", 55, error_message=f"configure 失败:\n{output}")
            return
        log("configure 成功")

        # ---- Step 8: 执行 make ----
        update_status("compiling", 65)
        make_jobs = task.make_jobs or 4
        log(f"执行 make -j{make_jobs} ...")
        with SSHClient(node.ip, node.port, credential.username, **auth_kwargs_copy) as ssh:
            success, output = ssh.execute_command(
                f"cd {work_dir}/{extract_dir} && make -j{make_jobs} 2>&1"
            )
        if not success:
            log(f"make 失败: {output}")
            update_status("failed", 65, error_message=f"make 失败:\n{output}")
            return
        log("make 编译成功")

        # ---- Step 9: 替换二进制 ----
        update_status("replacing_binary", 80)
        log("替换二进制文件...")
        new_binary = f"{work_dir}/{extract_dir}/objs/nginx"
        with SSHClient(node.ip, node.port, credential.username, **auth_kwargs_copy) as ssh:
            success, output = ssh.execute_command(f"cp {new_binary} {binary_path} 2>&1")
        if not success:
            log(f"替换二进制失败: {output}")
            update_status("failed", 80, error_message=f"替换二进制失败: {output}")
            return
        log("二进制文件替换完成")

        # ---- Step 10: nginx -t 语法检查 ----
        update_status("verifying", 85)
        log("执行 nginx -t 语法检查...")
        from utils.ssh import execute_nginx_test
        nginx_bin = task.current_binary_path or parsed["binary_path"]
        success, output = execute_nginx_test(
            node.ip, node.port, credential.username,
            nginx_path=nginx_bin, **auth_kwargs_copy,
        )
        if not success:
            log(f"语法检查失败: {output}")
            # 尝试回滚
            _rollback_binary(node, credential, binary_path, backup_path, auth_kwargs_copy, log)
            update_status("failed", 85, error_message=f"nginx -t 语法检查失败，已自动回滚:\n{output}")
            return
        log("nginx -t 语法检查通过")

        # ---- Step 11: 平滑升级 ----
        update_status("upgrading", 90)
        log("执行平滑升级 (USR2+WINCH+QUIT)...")
        success, result = _smooth_upgrade(
            node.ip, node.port, credential.username,
            prefix=parsed["prefix"], auth_kwargs=auth_kwargs_copy, log_fn=log,
        )
        if not success:
            log(f"平滑升级失败: {result}")
            _rollback_binary(node, credential, binary_path, backup_path, auth_kwargs_copy, log)
            update_status("failed", 90, error_message=f"平滑升级失败: {result}")
            return

        # ---- Step 12: 最终验证 ----
        update_status("verifying", 95)
        log("最终验证...")
        verify_cmd = f"{nginx_bin} -v 2>&1"
        with SSHClient(node.ip, node.port, credential.username, **auth_kwargs_copy) as ssh:
            success, output = ssh.execute_command(verify_cmd)
        log(f"新版本: {output.strip()}")

        # 完成
        update_status("success", 100, finished_at=timezone.now())
        log("✅ Nginx 编译升级成功完成!")

        # 更新节点上的 nginx_version
        from apps.nodes.models import Node as NodeModel
        target_ver = task.target_version or source_package.version
        NodeModel.objects.filter(pk=node.pk).update(nginx_version=f"nginx/{target_ver}")

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


def _smooth_upgrade(host, port, username, prefix, auth_kwargs, log_fn):
    """执行平滑升级：USR2 → 等待新 master → WINCH old → QUIT old

    Returns:
        tuple: (success: bool, message: str)
    """
    pid_file = f"{prefix.rstrip('/')}/logs/nginx.pid"
    try:
        with SSHClient(host, port, username, **auth_kwargs) as ssh:
            # 读取当前 pid
            success, output = ssh.execute_command(f"cat {pid_file} 2>/dev/null")
            if not success:
                return False, f"无法读取 PID 文件: {output}"
            old_pid = output.strip()

            # 发送 USR2 启动新 master
            success, output = ssh.execute_command(f"kill -USR2 {old_pid} 2>&1")
            if not success:
                return False, f"USR2 信号发送失败: {output}"
            log_fn(f"已发送 USR2 信号到旧 master (pid={old_pid})")

            # 等待新 master 启动
            import time as _time
            _time.sleep(2)

            # 检查新旧 pid 文件
            success, output = ssh.execute_command(
                f"cat {pid_file} 2>/dev/null && echo '---' && cat {pid_file}.oldbin 2>/dev/null"
            )
            if not success or "---" not in output:
                log_fn("警告: 无法确认新旧 PID 文件状态")

            # 发送 WINCH 优雅关闭旧 worker
            success, output = ssh.execute_command(f"kill -WINCH {old_pid} 2>&1")
            if not success:
                log_fn(f"警告: WINCH 信号发送失败: {output}")
            else:
                log_fn(f"已发送 WINCH 信号到旧 master (pid={old_pid})")

            _time.sleep(1)

            # 发送 QUIT 退出旧 master
            success, output = ssh.execute_command(f"kill -QUIT {old_pid} 2>&1")
            if not success:
                log_fn(f"警告: QUIT 信号发送失败: {output}")
            else:
                log_fn(f"已发送 QUIT 信号到旧 master (pid={old_pid})，旧进程已退出")

        return True, "平滑升级完成"
    except Exception as e:
        return False, str(e)


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