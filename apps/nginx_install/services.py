"""Nginx 全新安装独立流水线（不调用 run_upgrade_task）"""
import json
import logging
import os
import shlex
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.db import close_old_connections
from django.utils import timezone

from apps.releases.task_cancel import (
    finish_if_active,
    is_cancelled,
    register_ssh,
    unregister_ssh,
    update_if_active,
)
from apps.releases.task_result import (
    build_tree_result,
    item_failed,
    item_success,
    node_header,
    short_error_tail,
)
from utils.setting_service import get_setting

logger = logging.getLogger(__name__)

# 全新安装默认内置模块（无 nginx -V 基线时使用）
DEFAULT_INSTALL_MODULES = [
    "--with-http_ssl_module",
    "--with-http_v2_module",
    "--with-http_realip_module",
    "--with-http_stub_status_module",
    "--with-stream",
    "--with-stream_ssl_module",
]


def _auth_kwargs(credential):
    """按凭证类型组装 SSH 认证参数"""
    if credential.auth_type == "password":
        return {"password": credential.get_password()}
    return {"private_key": credential.get_private_key()}


def _tail_output(text, max_lines=80):
    """截取输出末尾若干行"""
    if not text:
        return ""
    lines = text.strip().splitlines()
    if len(lines) <= max_lines:
        return "\n".join(lines)
    return "\n".join(lines[-max_lines:])


def _default_install_prefix():
    """读取安装缺省 --prefix"""
    return (get_setting("install.default_prefix", "/opt/app") or "/opt/app").strip() or "/opt/app"


def _default_listen_port():
    """读取安装缺省监听端口"""
    try:
        port = int(get_setting("install.default_listen_port", "80") or 80)
    except (TypeError, ValueError):
        port = 80
    if port < 1 or port > 65535:
        return 80
    return port


def derive_paths_from_prefix(prefix):
    """由 --prefix 推导二进制与主配置路径"""
    fallback = _default_install_prefix()
    prefix = (prefix or fallback).rstrip("/") or fallback
    return {
        "prefix": prefix,
        "nginx_path": f"{prefix}/sbin/nginx",
        "main_conf_path": f"{prefix}/conf/nginx.conf",
    }


def _apply_listen_port_to_conf(ssh, conf_path, listen_port, log_fn=None):
    """将主配置中默认 listen 80 / [::]:80 改写为目标端口"""
    def _log(msg):
        if log_fn:
            log_fn(msg)

    try:
        port = int(listen_port)
    except (TypeError, ValueError):
        port = 80
    if port < 1 or port > 65535:
        port = 80

    quoted = shlex.quote(conf_path)
    # 优先 python3；失败则 sed 回退（仅替换独立端口 80）
    py = (
        "import re,sys\n"
        f"path={conf_path!r}\n"
        f"port={port}\n"
        "text=open(path,'r',encoding='utf-8',errors='replace').read()\n"
        "def repl(m): return m.group(1)+str(port)+m.group(2)\n"
        "new,n1=re.subn(r'(listen\\s+)80(\\b)',repl,text)\n"
        "new,n2=re.subn(r'(listen\\s+\\[::\\]:)80(\\b)',repl,new)\n"
        "\n"
        "if n1+n2==0:\n"
        " print('NO_CHANGE'); sys.exit(0)\n"
        "open(path,'w',encoding='utf-8').write(new)\n"
        "print('CHANGED:%d'%(n1+n2))\n"
    )
    ok, out = ssh.execute_command(f"python3 -c {shlex.quote(py)}")
    out = (out or "").strip()
    if ok:
        if out.startswith("NO_CHANGE"):
            _log(f"主配置未找到 listen 80，跳过改写: {conf_path}")
        else:
            _log(f"已将主配置 listen 改为 {port}: {conf_path} ({out})")
        return True, out

    _log(f"python3 改写失败，尝试 sed: {out}")
    sed_cmd = (
        f"cp {quoted} {quoted}.bak.mngxops && "
        f"sed -E -i "
        f"-e 's/(listen[[:space:]]+)80([[:space:];])/\\1{port}\\2/g' "
        f"-e 's/(listen[[:space:]]+\\[::\\]:)80([[:space:];])/\\1{port}\\2/g' "
        f"{quoted} && echo CHANGED"
    )
    ok2, out2 = ssh.execute_command(sed_cmd)
    out2 = (out2 or "").strip()
    if not ok2:
        _log(f"改写 listen 失败: {out2}")
        return False, out2 or out or "改写 listen 失败"
    _log(f"已将主配置 listen 改为 {port}: {conf_path} (sed)")
    return True, out2 or "CHANGED"


def _nginx_t_fail_message(output):
    """组装 nginx -t 失败文案（特权端口权限场景补充引导）"""
    text = (output or "").strip()
    msg = f"nginx -t 语法检查失败:\n{text}"
    low = text.lower()
    if "permission denied" in low or "bind()" in low:
        msg += (
            "\n提示: 非 root 账号无法监听特权端口（如 80）。"
            "请调整安装监听端口后重试，或改配置后通过发布/启停执行 start。"
        )
    return msg


def build_install_configure_opts(
    prefix, added_modules, added_third_party, remote_work_dir, user=None, group=None
):
    """组装全新安装的 configure 参数字符串"""
    from apps.upgrade.services import compute_target_configure_opts

    fallback = _default_install_prefix()
    prefix = (prefix or fallback).rstrip("/") or fallback
    base = [f"--prefix={prefix}"]
    user = (user or "").strip()
    group = (group or "").strip()
    if user:
        base.append(f"--user={user}")
    if group:
        base.append(f"--group={group}")
    modules = list(added_modules or [])
    return compute_target_configure_opts(
        base, modules, [], added_third_party or [], remote_work_dir=remote_work_dir
    )


def run_install_task(task_id):
    """执行单节点全新安装（线程内调用，独立于升级流水线）"""
    from apps.upgrade.services import (
        _ensure_remote_dir,
        _extract_package_name,
        _join_configure_opts,
        _prepare_one_third_party_module,
        _third_party_modules_dir,
        _tokenize_configure_args,
        enrich_third_party_module_paths,
    )
    from utils.nginx_ops import start_nginx
    from utils.ssh import (
        SSHClient,
        check_remote_file_md5,
        execute_nginx_test,
        get_nginx_version,
        upload_file_via_sftp,
    )

    from .models import NginxInstallTask

    close_old_connections()

    try:
        task = NginxInstallTask.objects.select_related(
            "node", "source_package", "operator", "task_center"
        ).get(pk=task_id)
    except NginxInstallTask.DoesNotExist:
        return

    node = task.node
    tc_id = task.task_center_id
    log_lines = []
    hostname = node.hostname or node.ip

    def _persist_log(current_step=None):
        """刷写安装任务日志"""
        task.log_output = "\n".join(log_lines)
        fields = {"log_output": task.log_output, "updated_at": timezone.now()}
        if current_step is not None:
            task.current_step = current_step
            fields["current_step"] = current_step
        NginxInstallTask.objects.filter(pk=task_id).update(**fields)

    def log(msg):
        """追加摘要日志"""
        timestamp = timezone.now().strftime("%Y-%m-%d %H:%M:%S")
        log_lines.append(f"[{timestamp}] {msg}")
        _persist_log(current_step=msg)

    def log_raw(output):
        """追加命令原始输出"""
        text = (output or "").rstrip("\n")
        if not text:
            return
        log_lines.append(text)
        _persist_log()

    def set_task_status(status, progress, error_message=None, finished_at=None, **extra):
        """更新安装任务状态字段"""
        updates = {
            "status": status,
            "progress": progress,
            "updated_at": timezone.now(),
        }
        if error_message is not None:
            updates["error_message"] = error_message
        if finished_at is not None:
            updates["finished_at"] = finished_at
        updates.update(extra)
        NginxInstallTask.objects.filter(pk=task_id).update(**updates)

    def fail(progress, message):
        """标记安装失败并同步任务中心（含结果树）"""
        log(message)
        set_task_status("failed", progress, error_message=message, finished_at=timezone.now())
        if tc_id:
            from apps.releases.task_progress import _clear_release_progress_state, _set_current_step

            _set_current_step(tc_id, hostname, None)
            _clear_release_progress_state(tc_id)
            blocks = [
                node_header(node.ip, node.hostname),
                item_failed("Nginx 安装", short_error_tail(message)),
            ]
            finish_if_active(
                tc_id,
                status="failed",
                progress=100,
                finished_at=timezone.now(),
                detail=message[:200],
                result=build_tree_result(0, 1, 1, blocks),
            )
        return False

    def cancelled():
        """协作式取消检查"""
        if tc_id and is_cancelled(tc_id):
            set_task_status(
                "cancelled", 100, error_message="用户手动取消", finished_at=timezone.now()
            )
            return True
        return False

    try:
        credential = node.credential
        if not credential or not credential.is_enabled:
            return fail(5, "节点凭证不可用")

        auth_kwargs = _auth_kwargs(credential)
        paths = derive_paths_from_prefix(task.target_prefix)
        nginx_bin = paths["nginx_path"]
        main_conf = paths["main_conf_path"]

        if tc_id:
            update_if_active(
                tc_id,
                status="running",
                progress=5,
                detail=f"{hostname} · 开始安装",
                started_at=timezone.now(),
            )
            from apps.releases.task_progress import _set_current_step
            _set_current_step(tc_id, hostname, "检查编译工具")

        # ---- 编译工具预检 ----
        set_task_status("uploading_package", 8)
        log("检查编译工具 gcc / make ...")
        check_cmd = "command -v gcc >/dev/null && command -v make >/dev/null && echo 'DEPS_OK'"
        with SSHClient(node.ip, node.port, credential.username, **auth_kwargs) as ssh:
            success, output = ssh.execute_command(check_cmd)
        if not success or "DEPS_OK" not in (output or ""):
            return fail(
                10,
                f"编译工具缺失: {output}\n请安装 gcc 与 make",
            )
        log("编译工具检查通过")
        if cancelled():
            return

        # ---- 上传源码包 ----
        source_package = task.source_package
        if not source_package or not source_package.package_file:
            return fail(10, "未选择源码包")

        set_task_status("uploading_package", 15)
        if tc_id:
            update_if_active(tc_id, progress=15, detail=f"{hostname} · 上传源码包")
            _set_current_step(tc_id, hostname, "上传源码包")
        log("上传源码包到目标节点...")
        work_dir = task.remote_work_dir or get_setting(
            "upgrade.default_work_dir", "/tmp/nginx-upgrade"
        )
        package_filename = os.path.basename(source_package.package_file.name)
        remote_package_path = f"{work_dir}/{package_filename}"

        _ensure_remote_dir(
            node.ip, node.port, credential.username,
            work_dir=work_dir, **auth_kwargs,
        )
        local_path = source_package.package_file.path
        success, msg = upload_file_via_sftp(
            node.ip, node.port, credential.username,
            local_path=local_path, remote_path=remote_package_path,
            **auth_kwargs,
        )
        if not success:
            return fail(20, f"上传源码包失败: {msg}")
        log(f"源码包已上传到 {remote_package_path}")

        success, remote_md5 = check_remote_file_md5(
            node.ip, node.port, credential.username,
            file_path=remote_package_path, **auth_kwargs,
        )
        if success and source_package.file_md5 and remote_md5 != source_package.file_md5:
            return fail(20, "源码包 MD5 校验失败，传输可能损坏")
        log(f"MD5 校验通过 ({(remote_md5 or '')[:16]}...)")
        if cancelled():
            return

        # ---- 解压 ----
        set_task_status("uploading_package", 25)
        log("解压源码包...")
        extract_dir = _extract_package_name(package_filename)
        with SSHClient(node.ip, node.port, credential.username, **auth_kwargs) as ssh:
            success, output = ssh.execute_command(
                f"cd {work_dir} && tar -xzf {remote_package_path} 2>&1"
            )
        if not success:
            return fail(25, f"解压失败: {output}")
        log(f"源码包解压完成: {work_dir}/{extract_dir}")
        if cancelled():
            return

        # ---- 第三方模块 ----
        set_task_status("downloading_modules", 35)
        third_party = json.loads(task.added_third_party or "[]")
        if third_party:
            if tc_id:
                update_if_active(tc_id, progress=35, detail=f"{hostname} · 准备第三方模块")
                _set_current_step(tc_id, hostname, "准备第三方模块")
            log(f"准备 {len(third_party)} 个第三方模块...")
            modules_dir = _third_party_modules_dir(work_dir)
            with SSHClient(node.ip, node.port, credential.username, **auth_kwargs) as ssh:
                ssh.execute_command(f"mkdir -p {shlex.quote(modules_dir)}")
                for idx, tp in enumerate(third_party):
                    if not isinstance(tp, dict):
                        continue
                    ok, err = _prepare_one_third_party_module(
                        ssh=ssh,
                        tp=tp,
                        idx=idx,
                        modules_dir=modules_dir,
                        node=node,
                        credential=credential,
                        auth_kwargs=auth_kwargs,
                        log=log,
                    )
                    if not ok:
                        return fail(40, err or "第三方模块准备失败")
            third_party = enrich_third_party_module_paths(third_party, work_dir)
            opt_tokens = _tokenize_configure_args(task.target_configure_opts or "")
            if not opt_tokens:
                opt_tokens = [
                    line.strip().rstrip("\\").strip()
                    for line in (task.target_configure_opts or "").split("\n")
                    if line.strip()
                ]
            for tp in third_party:
                if not isinstance(tp, dict):
                    continue
                mp = tp.get("module_path") or ""
                if mp and not any(mp in t for t in opt_tokens):
                    opt_tokens.append(f"--add-module={mp}")
            joined = _join_configure_opts(opt_tokens, multiline=True)
            NginxInstallTask.objects.filter(pk=task_id).update(
                added_third_party=json.dumps(third_party, ensure_ascii=False),
                target_configure_opts=joined,
            )
            task.target_configure_opts = joined
        else:
            log("无第三方模块需要准备")
        if cancelled():
            return

        # ---- configure ----
        set_task_status("configuring", 50)
        if tc_id:
            update_if_active(tc_id, progress=50, detail=f"{hostname} · configure")
            _set_current_step(tc_id, hostname, "configure")
        log("执行 ./configure ...")
        target_opts = task.target_configure_opts or ""
        opt_tokens = _tokenize_configure_args(target_opts)
        if not opt_tokens:
            opt_tokens = [
                line.strip().rstrip("\\").strip()
                for line in target_opts.split("\n")
                if line.strip()
            ]
        # 确保含 --prefix
        if not any(t.startswith("--prefix=") for t in opt_tokens):
            opt_tokens.insert(0, f"--prefix={paths['prefix']}")
        target_opts_single = _join_configure_opts(opt_tokens, multiline=False)
        configure_cmd = f"cd {work_dir}/{extract_dir} && ./configure {target_opts_single} 2>&1"
        log(f"configure 命令: {configure_cmd}")
        with SSHClient(node.ip, node.port, credential.username, **auth_kwargs) as ssh:
            if tc_id:
                register_ssh(tc_id, ssh)
            try:
                success, output = ssh.execute_command(configure_cmd)
            finally:
                if tc_id:
                    unregister_ssh(tc_id, ssh)
        log_raw(output)
        if not success:
            return fail(55, f"configure 失败:\n{_tail_output(output)}")
        log("configure 成功")
        if cancelled():
            return

        # ---- make ----
        set_task_status("compiling", 65)
        if tc_id:
            update_if_active(tc_id, progress=65, detail=f"{hostname} · make")
            _set_current_step(tc_id, hostname, "make")
        make_jobs = task.make_jobs or int(get_setting("upgrade.make_jobs_default", "4") or 4)
        make_cmd = f"cd {work_dir}/{extract_dir} && make -j{make_jobs} 2>&1"
        log(f"执行 make -j{make_jobs} ...")
        with SSHClient(node.ip, node.port, credential.username, **auth_kwargs) as ssh:
            if tc_id:
                register_ssh(tc_id, ssh)
            try:
                success, output = ssh.execute_command(make_cmd)
            finally:
                if tc_id:
                    unregister_ssh(tc_id, ssh)
        log_raw(output)
        if not success:
            return fail(65, f"make 失败:\n{_tail_output(output)}")
        log("make 编译成功")
        if cancelled():
            return

        # ---- make install ----
        set_task_status("installing", 78)
        if tc_id:
            update_if_active(tc_id, progress=78, detail=f"{hostname} · make install")
            _set_current_step(tc_id, hostname, "make install")
        install_cmd = f"cd {work_dir}/{extract_dir} && make install 2>&1"
        log("执行 make install ...")
        with SSHClient(node.ip, node.port, credential.username, **auth_kwargs) as ssh:
            success, output = ssh.execute_command(install_cmd)
        log_raw(output)
        if not success:
            return fail(80, f"make install 失败:\n{_tail_output(output)}")
        log("make install 完成")
        if cancelled():
            return

        # ---- 写入监听端口 ----
        listen_port = getattr(task, "listen_port", None) or _default_listen_port()
        if int(listen_port) != 80:
            set_task_status("verifying", 82)
            if tc_id:
                update_if_active(
                    tc_id, progress=82, detail=f"{hostname} · 写入 listen {listen_port}"
                )
                _set_current_step(tc_id, hostname, f"写入 listen {listen_port}")
            log(f"将主配置 listen 改为 {listen_port} ...")
            with SSHClient(node.ip, node.port, credential.username, **auth_kwargs) as ssh:
                ok, apply_out = _apply_listen_port_to_conf(
                    ssh, main_conf, listen_port, log_fn=log,
                )
            if not ok:
                return fail(82, f"写入监听端口失败:\n{apply_out}")
        else:
            log("监听端口为 80，保持源码包默认主配置")
        if cancelled():
            return

        # ---- nginx -t ----
        set_task_status("verifying", 85)
        if tc_id:
            update_if_active(tc_id, progress=85, detail=f"{hostname} · nginx -t")
            _set_current_step(tc_id, hostname, "nginx -t")
        log("执行 nginx -t 语法检查...")
        success, output = execute_nginx_test(
            node.ip, node.port, credential.username,
            nginx_path=nginx_bin, **auth_kwargs,
        )
        log_raw(output)
        if not success:
            return fail(85, _nginx_t_fail_message(output))
        log("nginx -t 语法检查通过")
        if cancelled():
            return

        # ---- start ----
        set_task_status("starting", 88)
        if tc_id:
            update_if_active(tc_id, progress=88, detail=f"{hostname} · 启动 Nginx")
            _set_current_step(tc_id, hostname, "启动 Nginx")
        log("启动 Nginx ...")
        ok, result = start_nginx(
            node.ip, node.port, credential.username,
            nginx_path=nginx_bin, log_fn=log, **auth_kwargs,
        )
        if not ok:
            return fail(88, f"Nginx 启动失败: {result}")
        log(f"启动完成: {result}")

        # ---- 回写节点 ----
        set_task_status("verifying", 92)
        ver_ok, ver_info = get_nginx_version(
            node.ip, node.port, credential.username,
            nginx_path=nginx_bin, **auth_kwargs,
        )
        target_ver = task.target_version or (source_package.version if source_package else "")
        if ver_ok and ver_info:
            node_ver = ver_info if str(ver_info).startswith("nginx/") else f"nginx/{ver_info}"
        else:
            node_ver = f"nginx/{target_ver}" if target_ver else ""
        log(f"回写节点版本={node_ver} 路径={nginx_bin}")

        from apps.nodes.services import mark_node_probe_success
        mark_node_probe_success(node)
        node.nginx_version = node_ver
        node.nginx_path = nginx_bin
        node.save(update_fields=[
            "nginx_version", "nginx_path", "status", "last_probe_at", "updated_at",
        ])

        from apps.configs.services import save_sync_path
        save_sync_path(node, main_conf, user=task.operator)
        log(f"主配置路径已写入: {main_conf}")

        # ---- 自动配置同步 ----
        set_task_status("syncing_config", 95)
        if tc_id:
            update_if_active(tc_id, progress=95, detail=f"{hostname} · 配置同步")
            _set_current_step(tc_id, hostname, "配置同步")
        sync_ok, sync_detail = _auto_sync_configs(
            node=node,
            credential=credential,
            auth_kwargs=auth_kwargs,
            main_conf=main_conf,
            operator=task.operator,
            task_center_id=tc_id,
            log=log,
        )
        set_task_status(
            "syncing_config", 95,
            sync_ok=sync_ok,
            sync_detail=sync_detail[:255] if sync_detail else "",
        )

        # ---- 完成 ----
        finish_msg = "安装成功"
        if not sync_ok:
            finish_msg = f"安装成功，配置同步失败：{sync_detail}"
        set_task_status("success", 100, finished_at=timezone.now())
        log(f"✅ {finish_msg}")
        if tc_id:
            from apps.releases.task_progress import _clear_release_progress_state, _set_current_step

            _set_current_step(tc_id, hostname, None)
            _clear_release_progress_state(tc_id)
            blocks = [node_header(node.ip, node.hostname), item_success("Nginx 安装")]
            if sync_ok:
                blocks.append(item_success(f"配置同步 ({sync_detail})"))
            else:
                blocks.append(item_failed("配置同步", sync_detail or "同步失败"))
            ok_n = 1 + (1 if sync_ok else 0)
            fail_n = 0 if sync_ok else 1
            finish_if_active(
                tc_id,
                status="success",
                progress=100,
                finished_at=timezone.now(),
                detail=finish_msg[:200],
                result=build_tree_result(ok_n, fail_n, 2, blocks),
            )
        return True

    except Exception as exc:
        logger.exception("Nginx 安装异常 task=%s", task_id)
        try:
            fail(100, f"安装过程发生异常: {exc}")
        except Exception:
            pass
        return False


def _auto_sync_configs(node, credential, auth_kwargs, main_conf, operator, task_center_id, log):
    """安装成功后自动发现并同步配置；失败不否定安装"""
    from apps.configs.services import (
        discover_max_depth,
        sync_discovered_configs,
    )
    from utils.ssh import discover_nginx_configs

    try:
        log(f"开始配置同步，主配置: {main_conf}")
        discovered, errors = discover_nginx_configs(
            node.ip,
            node.port,
            credential.username,
            nginx_conf_path=main_conf,
            max_include_depth=discover_max_depth(),
            **auth_kwargs,
        )
        if errors and not discovered:
            detail = "; ".join(errors)[:200]
            log(f"配置发现失败: {detail}")
            return False, detail or "配置发现失败"

        created, updated, skipped, orphaned, deleted = sync_discovered_configs(
            node,
            discovered or [],
            operator,
            remark="安装后自动同步",
            mark_orphaned=True,
            task_id=task_center_id,
        )
        detail = (
            f"新建{len(created)} 更新{len(updated)} 跳过{len(skipped)} "
            f"删除{len(deleted)}"
        )
        if errors:
            detail = f"{detail}；发现告警: {'; '.join(errors)[:80]}"
        log(f"配置同步完成: {detail}")
        return True, detail
    except Exception as exc:
        logger.exception("安装后配置同步失败 node=%s", node.id)
        log(f"配置同步异常: {exc}")
        return False, str(exc)[:200]


def batch_max_count():
    """读取批量操作最大节点数"""
    try:
        return max(1, int(get_setting("node.batch_max_count", "3") or 3))
    except (TypeError, ValueError):
        return 3


def _get_node_credential(node):
    """返回可用凭证或 None"""
    cred = getattr(node, "credential", None)
    if cred and cred.is_enabled:
        return cred
    return None


def run_install_batch(install_task_ids):
    """并行执行多节点安装（上限 batch_max_count）"""
    close_old_connections()
    workers = min(batch_max_count(), max(1, len(install_task_ids)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run_install_task, tid): tid for tid in install_task_ids}
        for future in as_completed(futures):
            tid = futures[future]
            try:
                future.result()
            except Exception:
                logger.exception("安装任务线程异常 install_task=%s", tid)


def start_install_batch(install_task_ids):
    """在后台线程启动安装批次并行执行"""
    threading.Thread(
        target=run_install_batch,
        args=(install_task_ids,),
        daemon=True,
    ).start()


def create_install_batch_from_data(user, data):
    """校验并创建安装批次，成功则启动并行执行。

    Returns:
        dict: 与原创建 API JsonResponse 字段一致（不含 HTTP status）。
    """
    from apps.audit.utils import log_task_center_created
    from apps.nodes.models import Node
    from apps.releases.models import TaskCenterTask
    from apps.upgrade.models import NginxSourcePackage
    from apps.upgrade.services import enrich_third_party_module_paths

    from .models import NginxInstallTask, generate_install_batch_number

    node_ids = data.get("node_ids") or []
    if not isinstance(node_ids, list) or not node_ids:
        return {"success": False, "message": "请选择至少一个节点"}

    try:
        node_ids = [int(x) for x in node_ids]
    except (TypeError, ValueError):
        return {"success": False, "message": "节点 ID 无效"}

    batch_max = batch_max_count()
    if len(node_ids) > batch_max:
        return {
            "success": False,
            "message": f"单次最多选择 {batch_max} 台节点",
        }

    package_id = data.get("source_package")
    try:
        package = NginxSourcePackage.objects.get(pk=int(package_id))
    except (TypeError, ValueError, NginxSourcePackage.DoesNotExist):
        return {"success": False, "message": "请选择有效源码包"}

    prefix = (data.get("target_prefix") or "").strip() or (
        get_setting("install.default_prefix", "/opt/app") or "/opt/app"
    )
    nginx_user = (data.get("nginx_user") or "").strip() or (
        get_setting("install.default_user", "root") or "root"
    )
    nginx_group = (data.get("nginx_group") or "").strip() or (
        get_setting("install.default_group", "root") or "root"
    )
    try:
        listen_port = int(
            data.get("listen_port")
            if data.get("listen_port") is not None
            else (get_setting("install.default_listen_port", "80") or 80)
        )
    except (TypeError, ValueError):
        return {"success": False, "message": "监听端口无效"}
    if listen_port < 1 or listen_port > 65535:
        return {"success": False, "message": "监听端口须在 1–65535"}
    work_dir = (data.get("remote_work_dir") or "").strip() or get_setting(
        "upgrade.default_work_dir", "/tmp/nginx-upgrade"
    )
    try:
        make_jobs = int(
            data.get("make_jobs") or get_setting("upgrade.make_jobs_default", "4") or 4
        )
    except (TypeError, ValueError):
        make_jobs = 4
    make_jobs = max(1, min(32, make_jobs))

    added_modules = data.get("added_modules") or []
    if not isinstance(added_modules, list):
        added_modules = []
    added_third_party = data.get("added_third_party") or []
    if not isinstance(added_third_party, list):
        added_third_party = []
    added_third_party = enrich_third_party_module_paths(added_third_party, work_dir)

    configure_opts = (data.get("target_configure_opts") or "").strip()
    if not configure_opts:
        configure_opts = build_install_configure_opts(
            prefix,
            added_modules,
            added_third_party,
            work_dir,
            user=nginx_user,
            group=nginx_group,
        )

    nodes = list(
        Node.objects.filter(id__in=node_ids, is_deleted=False).select_related("credential")
    )
    if len(nodes) != len(set(node_ids)):
        return {"success": False, "message": "部分节点不存在或已删除"}

    rejected = []
    eligible = []
    for node in nodes:
        if node.is_locked:
            rejected.append({"id": node.id, "hostname": node.hostname, "reason": "节点已锁定"})
            continue
        if node.status != "online":
            rejected.append({"id": node.id, "hostname": node.hostname, "reason": "节点非在线"})
            continue
        if not _get_node_credential(node):
            rejected.append({"id": node.id, "hostname": node.hostname, "reason": "无可用凭证"})
            continue
        eligible.append(node)

    if not eligible:
        return {
            "success": False,
            "message": "没有可执行安装的节点",
            "skipped": rejected,
        }

    batch_number = generate_install_batch_number()
    target_version = package.version
    paths = derive_paths_from_prefix(prefix)
    install_task_ids = []
    task_center_ids = []

    for node in eligible:
        tc = TaskCenterTask.objects.create(
            operation_type="nginx_install",
            status="pending",
            detail=f"Nginx 全新安装 {target_version} → {paths['prefix']}",
            target_hostnames=node.hostname,
            target_ips=node.ip,
            target_configs=target_version,
            source_batch=batch_number,
            trigger_user=user,
        )
        log_task_center_created(tc, user=user)
        inst = NginxInstallTask.objects.create(
            batch_number=batch_number,
            node=node,
            source_package=package,
            remote_work_dir=work_dir,
            target_version=target_version,
            target_prefix=prefix,
            target_configure_opts=configure_opts,
            added_modules=json.dumps(added_modules, ensure_ascii=False),
            added_third_party=json.dumps(added_third_party, ensure_ascii=False),
            make_jobs=make_jobs,
            listen_port=listen_port,
            task_center=tc,
            operator=user,
        )
        install_task_ids.append(inst.id)
        task_center_ids.append(tc.id)

    start_install_batch(install_task_ids)

    msg = f"已创建安装批次 {batch_number}，共 {len(install_task_ids)} 台"
    if rejected:
        msg += f"；跳过 {len(rejected)} 台"
    return {
        "success": True,
        "async": True,
        "message": msg,
        "batch_number": batch_number,
        "task_ids": install_task_ids,
        "task_center_ids": task_center_ids,
        "task_center_id": task_center_ids[0] if task_center_ids else None,
        "skipped": rejected,
    }
