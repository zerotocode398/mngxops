"""创建并执行独立的 Nginx 全新安装流水线。"""

import json
import logging
import posixpath
import re
import shlex
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from sqlalchemy import select, update
from sqlalchemy.orm import joinedload, selectinload, sessionmaker

from ngxops.configs.models import ConfigSyncSetting
from ngxops.credentials.crypto import CredentialDecryptionError
from ngxops.database.session import session_scope
from ngxops.logging_setup import log_exception
from ngxops.nginx_install.models import NginxInstallRun
from ngxops.nodes.models import Node, NodeSyncSetting
from ngxops.settings.service import read_setting
from ngxops.tasks.executor import TaskCancelled, TaskContext, TaskOutcome
from ngxops.tasks.models import Task, TaskLog
from ngxops.upgrade.builtin_modules import BUILTIN_ADD_MODULES
from ngxops.upgrade.models import NginxModulePackage, NginxSourcePackage
from ngxops.upgrade.services import (
    _BATCH_LOCK as _PACKAGE_BATCH_LOCK,
    UpgradeTaskError,
    _append_command_output,
    _connect_target,
    _file_md5,
    _prepare_module,
    _read_archive_root,
    _require_remote_success,
    _run_remote_command,
    _tail_output,
    _upload_file,
    _verify_remote_md5,
    compute_target_configure_opts,
    enrich_third_party_modules,
    join_configure_opts,
    tokenize_configure_args,
    validate_remote_work_dir,
    validate_target_prefix,
)


logger = logging.getLogger(__name__)
_DEFAULT_INSTALL_MODULES = (
    "--with-http_ssl_module",
    "--with-http_v2_module",
    "--with-http_realip_module",
    "--with-http_stub_status_module",
    "--with-stream",
    "--with-stream_ssl_module",
)
_ACCOUNT_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}$")


def default_install_modules() -> List[str]:
    """返回首次安装时默认启用的官方模块。"""
    return list(_DEFAULT_INSTALL_MODULES)


def derive_paths_from_prefix(prefix: str) -> Dict[str, str]:
    """由 Nginx 安装前缀推导二进制和主配置路径。"""
    normalized = validate_target_prefix(prefix or "/opt/app")
    return {
        "prefix": normalized,
        "nginx_path": posixpath.join(normalized, "sbin", "nginx"),
        "main_conf_path": posixpath.join(normalized, "conf", "nginx.conf"),
    }


def build_install_configure_opts(
    prefix: str,
    added_modules: Sequence[str],
    added_third_party: Sequence[dict],
    remote_work_dir: str,
    user: str = "root",
    group: str = "root",
    extra_opts: str = "",
) -> str:
    """校验安装参数并生成可安全复用的 configure 参数快照。"""
    normalized_prefix = validate_target_prefix(prefix or "/opt/app")
    normalized_work_dir = validate_remote_work_dir(remote_work_dir)
    normalized_user = _validate_account_name(user or "root", "Nginx 用户")
    normalized_group = _validate_account_name(group or "root", "Nginx 用户组")
    modules = list(dict.fromkeys(added_modules or ()))
    if any(module not in BUILTIN_ADD_MODULES for module in modules):
        raise ValueError("包含不支持的 Nginx 官方模块参数")
    third_party = enrich_third_party_modules(added_third_party or (), normalized_work_dir)
    extra_tokens = _parse_extra_options(extra_opts)
    protected = ("--prefix", "--sbin-path", "--user", "--group")
    if any(token.split("=", 1)[0] in protected for token in extra_tokens):
        raise ValueError("额外参数不能覆盖安装路径、二进制路径或运行用户")
    return compute_target_configure_opts(
        [
            "--prefix={}".format(normalized_prefix),
            "--user={}".format(normalized_user),
            "--group={}".format(normalized_group),
        ],
        modules + extra_tokens,
        [],
        third_party,
        normalized_work_dir,
    )


def _validate_account_name(value: str, label: str) -> str:
    """校验 systemd 和 configure 可安全使用的账户标识。"""
    normalized = (value or "").strip()
    if not _ACCOUNT_NAME.fullmatch(normalized):
        raise ValueError("{}格式无效".format(label))
    return normalized


def _parse_extra_options(value: str) -> List[str]:
    """解析额外 configure 参数并拒绝非选项片段。"""
    try:
        tokens = shlex.split(value or "", posix=True)
    except ValueError as exc:
        raise ValueError("额外 configure 参数引号不完整") from exc
    if any(not token.startswith("--") or "\x00" in token for token in tokens):
        raise ValueError("额外 configure 参数必须由 -- 开头")
    return tokens


def _phase(
    session_factory: sessionmaker,
    task_id: int,
    context: TaskContext,
    phase: str,
    progress: int,
    detail: str,
) -> None:
    """持久化安装阶段、任务进度和可读步骤。"""
    with session_scope(session_factory) as session:
        with session.begin():
            session.execute(
                update(NginxInstallRun)
                .where(NginxInstallRun.task_id == task_id)
                .values(phase=phase)
            )
    context.update_progress(progress, detail)
    context.append_log(detail)


def _load_install_snapshot(
    session_factory: sessionmaker,
    package_dir: Path,
    encryption_key: bytes,
    task_id: int,
) -> Tuple[dict, dict, dict]:
    """复核安装快照、在线节点、凭证与本地归档文件。"""
    with session_scope(session_factory) as session:
        run = session.scalars(
            select(NginxInstallRun).where(NginxInstallRun.task_id == task_id)
        ).one_or_none()
        if run is None:
            raise UpgradeTaskError("Nginx 安装任务不存在")
        node = session.scalars(
            select(Node)
            .options(joinedload(Node.credential))
            .where(Node.id == run.node_id, Node.is_deleted.is_(False))
        ).one_or_none()
        if node is None:
            raise UpgradeTaskError("目标节点不存在或已删除")
        if node.is_locked or node.status != "online":
            raise UpgradeTaskError("节点状态已变化，不满足在线安装门禁")
        credential = node.credential
        if credential is None or not credential.is_enabled:
            raise UpgradeTaskError("节点未配置有效的 SSH 凭证")
        try:
            secret = (
                credential.get_password(encryption_key)
                if credential.auth_type == "password"
                else credential.get_private_key(encryption_key)
            )
        except CredentialDecryptionError as exc:
            raise UpgradeTaskError("节点 SSH 凭证解密失败") from exc
        package = session.get(NginxSourcePackage, run.source_package_id)
        if package is None:
            raise UpgradeTaskError("Nginx 源码包已不存在")
        local_path = package_dir / package.file_name
        if not local_path.is_file() or _file_md5(local_path) != package.file_md5:
            raise UpgradeTaskError("Nginx 源码包缺失或校验值已变化")
        third_party = json.loads(run.third_party_json or "[]")
        for item in third_party:
            if item.get("source") != "package":
                continue
            module_package = session.get(NginxModulePackage, item.get("package_id"))
            if module_package is None:
                raise UpgradeTaskError("第三方模块离线包已不存在")
        run_snapshot = {
            "task_id": task_id,
            "node_id": node.id,
            "hostname": node.hostname,
            "ip": node.ip,
            "port": node.port,
            "remote_work_dir": run.remote_work_dir,
            "target_prefix": run.target_prefix,
            "nginx_user": run.nginx_user,
            "nginx_group": run.nginx_group,
            "target_configure_opts": run.target_configure_opts,
            "added_modules": json.loads(run.added_modules_json or "[]"),
            "third_party": third_party,
            "make_jobs": run.make_jobs,
            "listen_port": run.listen_port,
            "target_version": run.target_version,
            "main_conf_path": run.main_conf_path,
        }
        target = {
            "id": node.id,
            "hostname": node.hostname,
            "ip": node.ip,
            "port": node.port,
            "nginx_path": node.nginx_path,
            "username": credential.username,
            "auth_type": credential.auth_type,
            "password": secret if credential.auth_type == "password" else "",
            "private_key": secret if credential.auth_type != "password" else "",
            "ssh_timeout": read_setting(session, "node.ssh_connect_timeout", 10),
            "detect_retries": read_setting(session, "node.detect_retries", 1),
        }
        package_snapshot = {
            "name": package.name,
            "version": package.version,
            "file_name": package.file_name,
            "file_md5": package.file_md5,
            "local_path": str(local_path),
        }
    return run_snapshot, target, package_snapshot


def _systemd_quote(value: str) -> str:
    """引用 systemd unit 命令参数并转义路径中的特殊字符。"""
    escaped = value.replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"')
    return '"{}"'.format(escaped)


def _systemd_unit_content(nginx_path: str, user: str, group: str) -> str:
    """生成使用编译路径和运行用户的 systemd unit 内容。"""
    return "\n".join(
        (
            "[Unit]",
            "Description=nginx (managed by ngxops)",
            "After=network.target",
            "",
            "[Service]",
            "Type=forking",
            "ExecStart={}".format(_systemd_quote(nginx_path)),
            "ExecReload={} -s reload".format(_systemd_quote(nginx_path)),
            "ExecStop={} -s quit".format(_systemd_quote(nginx_path)),
            "KillMode=mixed",
            "PrivateTmp=true",
            "User={}".format(user),
            "Group={}".format(group),
            "",
            "[Install]",
            "WantedBy=multi-user.target",
            "",
        )
    )


def _systemd_capability(client) -> Tuple[bool, bool, str]:
    """检查 systemctl、unit 写权限和免密 sudo 能力。"""
    status, _output = _run_remote_command(
        client, "command -v systemctl >/dev/null 2>&1", timeout=20
    )
    if status != 0:
        return False, False, "未找到 systemctl"
    status, _output = _run_remote_command(
        client, "test -w /etc/systemd/system", timeout=20
    )
    if status == 0:
        return True, False, "当前用户可写 systemd unit 目录"
    status, _output = _run_remote_command(
        client, "sudo -n true >/dev/null 2>&1", timeout=20
    )
    if status == 0:
        return True, True, "免密 sudo 可用"
    return False, False, "unit 目录不可写且免密 sudo 不可用"


def _run_systemd(client, command: str, use_sudo: bool) -> Tuple[int, str]:
    """按探测结果通过直接权限或免密 sudo 执行 systemd 命令。"""
    prefix = "sudo -n " if use_sudo else ""
    return _run_remote_command(client, prefix + command, timeout=120)


def _register_systemd_unit(
    client,
    nginx_path: str,
    user: str,
    group: str,
    use_sudo: bool,
    context: TaskContext,
) -> None:
    """写入托管 unit 并完成 daemon-reload 和 enable。"""
    content = _systemd_unit_content(nginx_path, user, group)
    quoted_content = shlex.quote(content)
    if use_sudo:
        command = "printf %s {} | sudo -n tee /etc/systemd/system/nginx.service >/dev/null".format(
            quoted_content
        )
    else:
        command = "printf %s {} > /etc/systemd/system/nginx.service".format(
            quoted_content
        )
    _require_remote_success(client, command, "注册 systemd unit 失败")
    context.append_log("已写入 /etc/systemd/system/nginx.service")
    for command, message in (
        ("systemctl daemon-reload", "systemd daemon-reload 失败"),
        ("systemctl enable nginx", "systemctl enable nginx 失败"),
    ):
        status, _output = _run_systemd(client, command, use_sudo)
        if status != 0:
            raise UpgradeTaskError(message)
        context.append_log("执行 {} 成功".format(command))


def _apply_listen_port(client, conf_path: str, listen_port: int, context: TaskContext) -> None:
    """将主配置中的默认 IPv4 或 IPv6 listen 80 改为目标端口。"""
    script = (
        "import re,sys\n"
        "path={!r}\n"
        "port={}\n"
        "text=open(path,'r',encoding='utf-8',errors='replace').read()\n"
        "def repl(m): return m.group(1)+str(port)+m.group(2)\n"
        "text,n1=re.subn(r'(listen\\s+)80(\\b)',repl,text)\n"
        "text,n2=re.subn(r'(listen\\s+\\[::\\]:)80(\\b)',repl,text)\n"
        "if n1+n2:\n"
        " open(path,'w',encoding='utf-8').write(text)\n"
        "print('CHANGED:%d'%(n1+n2) if n1+n2 else 'NO_CHANGE')\n"
    ).format(conf_path, listen_port)
    status, output = _run_remote_command(
        client, "python3 -c {}".format(shlex.quote(script)), timeout=30
    )
    if status == 0:
        context.append_log(
            "已将主配置 listen 改为 {}: {}".format(listen_port, conf_path)
            if "CHANGED:" in output
            else "主配置未找到 listen 80，保留现有 listen 值: {}".format(conf_path)
        )
        return
    context.append_log("python3 改写失败，尝试 sed 回退")
    quoted = shlex.quote(conf_path)
    fallback = (
        "cp -- {path} {path}.bak.ngxops && sed -E -i "
        "-e 's/(listen[[:space:]]+)80([[:space:];])/\\1{port}\\2/g' "
        "-e 's/(listen[[:space:]]+\\[::\\]:)80([[:space:];])/\\1{port}\\2/g' "
        "{path}"
    ).format(path=quoted, port=listen_port)
    status, _output = _run_remote_command(client, fallback, timeout=30)
    if status != 0:
        raise UpgradeTaskError("写入 Nginx 监听端口失败")
    context.append_log("已通过 sed 将主配置 listen 改为 {}".format(listen_port))


def _listen_error(output: str) -> str:
    """为特权端口绑定错误补充非 root 配置指引。"""
    message = "nginx -t 语法检查失败：\n{}".format(_tail_output(output))
    normalized = (output or "").lower()
    if "permission denied" in normalized or "bind()" in normalized:
        message += "\n提示：非 root SSH 用户无法监听特权端口（如 80），请调整监听端口后重试。"
    return message


def _write_node_install_result(
    session_factory: sessionmaker,
    run: dict,
    version: str,
    nginx_path: str,
    main_conf_path: str,
    user_id: int,
) -> None:
    """回写节点探测状态及节点和配置同步主配置路径。"""
    now = datetime.utcnow()
    with session_scope(session_factory) as session:
        with session.begin():
            node = session.get(Node, run["node_id"])
            if node is None:
                raise UpgradeTaskError("安装完成但节点记录已不存在")
            node.status = "online"
            node.last_probe_at = now
            node.nginx_available = True
            node.last_nginx_probe_at = now
            node.nginx_version = version or run["target_version"]
            node.nginx_path = nginx_path
            node.updated_at = now
            node_sync = session.get(NodeSyncSetting, node.id)
            if node_sync is None:
                node_sync = NodeSyncSetting(
                    node_id=node.id,
                    main_conf_path=main_conf_path,
                    updated_at=now,
                )
                session.add(node_sync)
            else:
                node_sync.main_conf_path = main_conf_path
                node_sync.updated_at = now
            sync_setting = session.scalar(
                select(ConfigSyncSetting).where(
                    ConfigSyncSetting.node_id == node.id
                )
            )
            if sync_setting is None:
                session.add(
                    ConfigSyncSetting(
                        node_id=node.id,
                        main_conf_path=main_conf_path,
                        updated_by=user_id,
                        updated_at=now,
                    )
                )
            else:
                sync_setting.main_conf_path = main_conf_path
                sync_setting.updated_by = user_id
                sync_setting.updated_at = now
            session.execute(
                update(NginxInstallRun)
                .where(NginxInstallRun.task_id == run["task_id"])
                .values(nginx_path=nginx_path, main_conf_path=main_conf_path)
            )


def _save_sync_result(
    session_factory: sessionmaker,
    task_id: int,
    success: bool,
    detail: str,
) -> None:
    """保存自动配置同步的独立状态与摘要。"""
    with session_scope(session_factory) as session:
        with session.begin():
            session.execute(
                update(NginxInstallRun)
                .where(NginxInstallRun.task_id == task_id)
                .values(sync_ok=success, sync_detail=detail[:500])
            )


def _sync_installed_configs(
    session_factory: sessionmaker,
    encryption_key: bytes,
    run: dict,
    context: TaskContext,
    ssh_client: Any,
) -> Tuple[bool, str, dict]:
    """安装后复用配置发现与同步规则，且不改变安装任务成功状态。"""
    from ngxops.configs.tasks import _compact_node_result, _node_result_summary, _sync_node

    try:
        result = _sync_node(
            context,
            session_factory,
            encryption_key,
            run["node_id"],
            run["trigger_user_id"],
            run["task_id"],
            "full",
            (),
            run["main_conf_path"],
            ssh_client=ssh_client,
        )
    except TaskCancelled:
        raise
    except Exception as exc:
        log_exception(
            logger,
            "Nginx 安装后配置同步异常",
            exc,
            "task_id={}".format(run["task_id"]),
        )
        detail = "配置同步异常（{}）".format(type(exc).__name__)
        context.append_log(detail, "error")
        return False, detail, {
            "node_id": run["node_id"],
            "hostname": run["hostname"],
            "ip": run["ip"],
            "summary": {"total": 1, "success": 0, "failed": 1},
            "errors": [{"path": run["main_conf_path"], "message": detail}],
        }
    summary = _node_result_summary(result)
    success = summary["failed"] == 0 and summary["total"] > 0
    detail = "新增 {}，更新 {}，跳过 {}，远程缺失 {}，清理 {}，错误 {}".format(
        len(result["created"]),
        len(result["updated"]),
        len(result["skipped"]),
        len(result["orphaned"]),
        len(result["deleted"]),
        len(result["errors"]),
    )
    return success, detail, _compact_node_result(result)


def _run_install(
    session_factory: sessionmaker,
    encryption_key: bytes,
    package_dir: Path,
    task_id: int,
    context: TaskContext,
) -> TaskOutcome:
    """在线程中执行单节点源码编译安装和启动。"""
    from ngxops.upgrade.services import _run_remote_command as run_remote

    run = None
    target = None
    client = None
    try:
        run, target, package = _load_install_snapshot(
            session_factory, package_dir, encryption_key, task_id
        )
        run["trigger_user_id"] = _task_trigger_user(session_factory, task_id)
        context.check_cancelled()
        paths = derive_paths_from_prefix(run["target_prefix"])
        run["nginx_path"] = paths["nginx_path"]
        run["main_conf_path"] = paths["main_conf_path"]
        work_dir = run["remote_work_dir"]
        remote_package = posixpath.join(work_dir, package["file_name"])
        _phase(session_factory, task_id, context, "checking_tools", 5, "检查 gcc 与 make")
        client = _connect_target(target, context)
        _require_remote_success(
            client,
            "command -v gcc >/dev/null 2>&1 && command -v make >/dev/null 2>&1",
            "目标节点缺少 gcc 或 make 编译工具",
        )
        context.check_cancelled()

        _phase(session_factory, task_id, context, "uploading_package", 10, "创建远程工作目录")
        _require_remote_success(
            client,
            "mkdir -p -- {}".format(shlex.quote(work_dir)),
            "创建远程编译工作目录失败",
        )
        _phase(session_factory, task_id, context, "uploading_package", 15, "上传 Nginx 源码包")
        _upload_file(client, package["local_path"], remote_package, context, 15, 27)
        _verify_remote_md5(client, remote_package, package["file_md5"], "源码包")
        context.append_log("源码包传输校验通过")
        context.check_cancelled()

        _phase(session_factory, task_id, context, "extracting_package", 30, "解压 Nginx 源码包")
        _require_remote_success(
            client,
            "tar -xzf {} -C {} --no-same-owner".format(
                shlex.quote(remote_package), shlex.quote(work_dir)
            ),
            "解压 Nginx 源码包失败",
        )
        source_root = _read_archive_root(client, remote_package)
        source_dir = posixpath.join(work_dir, source_root)
        context.check_cancelled()

        third_party = run["third_party"]
        if third_party:
            _phase(session_factory, task_id, context, "preparing_modules", 38, "准备第三方模块")
            for index, item in enumerate(third_party):
                context.check_cancelled()
                _prepare_module(
                    client,
                    item,
                    index,
                    work_dir,
                    package_dir,
                    session_factory,
                    context,
                )
        else:
            context.update_progress(44, "没有第三方模块需要准备")
            context.append_log("没有第三方模块需要准备")
        context.check_cancelled()

        options = tokenize_configure_args(run["target_configure_opts"])
        if not options:
            raise UpgradeTaskError("目标 configure 参数为空")
        if "--prefix={}".format(paths["prefix"]) not in options:
            raise UpgradeTaskError("安装前缀与编译参数快照不一致")
        configure = " ".join(shlex.quote(item) for item in options)
        _phase(session_factory, task_id, context, "configuring", 48, "执行 ./configure")
        status, output = run_remote(
            client,
            "cd -- {} && ./configure {} 2>&1".format(
                shlex.quote(source_dir), configure
            ),
            timeout=1800,
            context=context,
        )
        _append_command_output(context, output)
        if status != 0:
            raise UpgradeTaskError("configure 失败：\n{}".format(_tail_output(output)))
        context.check_cancelled()

        _phase(
            session_factory,
            task_id,
            context,
            "compiling",
            58,
            "执行 make -j{}".format(run["make_jobs"]),
        )
        status, output = run_remote(
            client,
            "cd -- {} && make -j{} 2>&1".format(
                shlex.quote(source_dir), run["make_jobs"]
            ),
            timeout=7200,
            context=context,
        )
        _append_command_output(context, output)
        if status != 0:
            raise UpgradeTaskError("make 编译失败：\n{}".format(_tail_output(output)))
        context.check_cancelled()

        _phase(session_factory, task_id, context, "installing", 78, "执行 make install")
        status, output = run_remote(
            client,
            "cd -- {} && make install 2>&1".format(shlex.quote(source_dir)),
            timeout=1800,
            context=context,
        )
        _append_command_output(context, output)
        if status != 0:
            raise UpgradeTaskError("make install 失败：\n{}".format(_tail_output(output)))

        _phase(session_factory, task_id, context, "verifying", 84, "写入监听端口并执行 nginx -t")
        if run["listen_port"] != 80:
            _apply_listen_port(
                client, paths["main_conf_path"], run["listen_port"], context
            )
        status, output = run_remote(
            client,
            "{} -t 2>&1".format(shlex.quote(paths["nginx_path"])),
            timeout=120,
        )
        _append_command_output(context, output)
        if status != 0:
            raise UpgradeTaskError(_listen_error(output))
        context.check_cancelled()

        _phase(session_factory, task_id, context, "starting", 88, "探测 systemd 管理能力")
        use_systemd, use_sudo, capability = _systemd_capability(client)
        context.append_log(
            "启动策略：{}（{}）".format(
                "systemd 托管" if use_systemd else "直接二进制启动", capability
            )
        )
        if use_systemd:
            _register_systemd_unit(
                client,
                paths["nginx_path"],
                run["nginx_user"],
                run["nginx_group"],
                use_sudo,
                context,
            )
            status, output = _run_systemd(client, "systemctl start nginx", use_sudo)
            if status != 0:
                raise UpgradeTaskError("systemctl start nginx 失败")
        else:
            status, output = run_remote(
                client, "{} 2>&1".format(shlex.quote(paths["nginx_path"])), timeout=60
            )
            if status != 0:
                raise UpgradeTaskError("Nginx 二进制启动失败：\n{}".format(_tail_output(output)))
        if output:
            _append_command_output(context, output)
        context.append_log("Nginx 启动完成")

        _phase(session_factory, task_id, context, "verifying", 92, "读取安装后的 Nginx 版本")
        status, output = run_remote(
            client,
            "{} -v 2>&1".format(shlex.quote(paths["nginx_path"])),
            timeout=30,
        )
        match = re.search(r"nginx/([0-9]+(?:\.[0-9]+){1,3})", output or "")
        version = match.group(1) if status == 0 and match else package["version"]
        context.append_log("回写 Nginx 版本 {} 和路径 {}".format(version, paths["nginx_path"]))
        _write_node_install_result(
            session_factory,
            run,
            version,
            paths["nginx_path"],
            paths["main_conf_path"],
            run["trigger_user_id"],
        )

        _phase(session_factory, task_id, context, "syncing_config", 95, "安装完成，自动同步 Nginx 配置")
        sync_ok, sync_detail, sync_result = _sync_installed_configs(
            session_factory, encryption_key, run, context, client
        )
        _save_sync_result(session_factory, task_id, sync_ok, sync_detail)
        _phase(session_factory, task_id, context, "success", 99, "Nginx 全新安装完成")
        message = "安装成功" if sync_ok else "安装成功，配置同步失败：{}".format(sync_detail)
        tree = {
            "summary": {
                "total": 2,
                "success": 2 if sync_ok else 1,
                "failed": 0 if sync_ok else 1,
            },
            "node": {
                "hostname": run["hostname"],
                "ip": run["ip"],
                "version": version,
                "nginx_path": paths["nginx_path"],
                "main_conf_path": paths["main_conf_path"],
                "startup_mode": "systemd" if use_systemd else "binary",
                "install": {"status": "success"},
                "config_sync": sync_result,
            },
        }
        return TaskOutcome("success", message[:2000], tree)
    except TaskCancelled:
        _set_final_phase(session_factory, task_id, "cancelled")
        raise
    except Exception as exc:
        log_exception(
            logger,
            "Nginx 安装任务异常",
            exc,
            "task_id={}".format(task_id),
        )
        if isinstance(exc, UpgradeTaskError):
            message = str(exc)
        elif isinstance(exc, ValueError):
            message = str(exc)
        else:
            message = "Nginx 安装失败（{}）".format(type(exc).__name__)
        context.append_log(message, "error")
        _set_final_phase(session_factory, task_id, "failed")
        tree = {
            "summary": {"total": 1, "success": 0, "failed": 1},
            "node": {
                "hostname": run["hostname"] if run else "",
                "ip": run["ip"] if run else "",
                "install": {"status": "failed", "message": message[:2000]},
            },
        }
        return TaskOutcome("failed", message[:2000], tree)
    finally:
        if client is not None:
            client.close()


def _task_trigger_user(session_factory: sessionmaker, task_id: int) -> int:
    """读取安装统一任务的操作用户 ID。"""
    with session_scope(session_factory) as session:
        trigger_user_id = session.scalar(
            select(Task.trigger_user_id).where(Task.id == task_id)
        )
    if trigger_user_id is None:
        raise UpgradeTaskError("安装任务操作人不存在")
    return trigger_user_id


def _set_final_phase(
    session_factory: sessionmaker, task_id: int, phase: str
) -> None:
    """保存安装流水线的取消或失败阶段。"""
    with session_scope(session_factory) as session:
        with session.begin():
            session.execute(
                update(NginxInstallRun)
                .where(NginxInstallRun.task_id == task_id)
                .values(phase=phase)
            )


def create_install_runner(
    session_factory: sessionmaker,
    encryption_key: bytes,
    package_dir: Path,
    task_id: int,
):
    """为统一任务执行器创建绑定安装任务 ID 的回调。"""
    def run(context: TaskContext) -> TaskOutcome:
        """执行单节点安装并返回结构化业务结果。"""
        return _run_install(
            session_factory, encryption_key, package_dir, task_id, context
        )

    return run


def _new_batch_number(session, now: datetime) -> str:
    """在当前数据库事务中生成当日自增安装批次号。"""
    prefix = "IN-{}-".format(now.strftime("%y%m%d"))
    latest = session.scalar(
        select(NginxInstallRun.batch_number)
        .where(NginxInstallRun.batch_number.like(prefix + "%"))
        .order_by(NginxInstallRun.batch_number.desc())
        .limit(1)
    )
    sequence = int(latest.rsplit("-", 1)[-1]) + 1 if latest else 1
    return "{}{:04d}".format(prefix, sequence)


def _eligible_node_reason(node: Node, active_node_ids: set) -> str:
    """返回节点不能参加安装批次时的可读原因。"""
    if node.is_deleted:
        return "节点已删除"
    if node.is_locked:
        return "节点已锁定"
    if node.status != "online":
        return "节点非在线状态"
    if node.credential is None or not node.credential.is_enabled:
        return "无可用 SSH 凭证"
    if node.id in active_node_ids:
        return "节点已有进行中的安装任务"
    return ""


def create_install_batch(
    session_factory: sessionmaker,
    executor,
    encryption_key: bytes,
    package_dir: Path,
    user_id: int,
    payload: dict,
    batch_limit: int = 3,
) -> dict:
    """校验目标与包后在同一事务创建安装批次并启动任务。"""
    node_ids = list(dict.fromkeys(payload.get("node_ids") or ()))
    if not node_ids:
        raise ValueError("请选择至少一个目标节点")
    if len(node_ids) > batch_limit:
        raise ValueError("单次最多选择 {} 台节点".format(batch_limit))
    with _PACKAGE_BATCH_LOCK:
        with session_scope(session_factory) as session:
            with session.begin():
                package = session.get(
                    NginxSourcePackage, payload.get("source_package_id")
                )
                if package is None:
                    raise ValueError("请选择有效的 Nginx 源码包")
                local_path = package_dir / package.file_name
                if not local_path.is_file() or _file_md5(local_path) != package.file_md5:
                    raise ValueError("所选 Nginx 源码包缺失或校验失败")
                module_ids = [
                    int(item["package_id"])
                    for item in payload.get("added_third_party", ())
                    if item.get("source") == "package"
                ]
                if module_ids:
                    existing_modules = set(
                        session.scalars(
                            select(NginxModulePackage.id).where(
                                NginxModulePackage.id.in_(set(module_ids))
                            )
                        ).all()
                    )
                    if existing_modules != set(module_ids):
                        raise ValueError("部分第三方模块离线包不存在")
                nodes = session.scalars(
                    select(Node)
                    .options(joinedload(Node.credential))
                    .where(Node.id.in_(node_ids))
                    .order_by(Node.id.asc())
                ).unique().all()
                by_id = {node.id: node for node in nodes}
                if set(by_id) != set(node_ids):
                    raise ValueError("部分节点不存在")
                active_node_ids = set(
                    session.scalars(
                        select(NginxInstallRun.node_id)
                        .join(Task, Task.id == NginxInstallRun.task_id)
                        .where(
                            NginxInstallRun.node_id.in_(node_ids),
                            Task.status.in_(("pending", "running")),
                        )
                    ).all()
                )
                eligible = []
                skipped = []
                for node_id in node_ids:
                    node = by_id[node_id]
                    reason = _eligible_node_reason(node, active_node_ids)
                    if reason:
                        skipped.append(
                            {
                                "id": node.id,
                                "hostname": node.hostname,
                                "ip": node.ip,
                                "reason": reason,
                            }
                        )
                    else:
                        eligible.append(node)
                if not eligible:
                    raise ValueError("所选节点均不满足安装条件")
                now = datetime.now()
                batch_number = _new_batch_number(session, now)
                run_values = {
                    "source_package_id": package.id,
                    "source_package_name": package.name,
                    "target_version": package.version,
                    "remote_work_dir": payload["remote_work_dir"],
                    "target_prefix": payload["target_prefix"],
                    "nginx_user": payload["nginx_user"],
                    "nginx_group": payload["nginx_group"],
                    "target_configure_opts": payload["target_configure_opts"],
                    "added_modules_json": json.dumps(
                        payload["added_modules"], ensure_ascii=False
                    ),
                    "third_party_json": json.dumps(
                        payload["added_third_party"], ensure_ascii=False
                    ),
                    "make_jobs": payload["make_jobs"],
                    "listen_port": payload["listen_port"],
                    "phase": "pending",
                }
                task_ids = []
                for node in eligible:
                    task = Task(
                        operation_type="nginx_install",
                        detail="Nginx 全新安装 {} → {}".format(
                            package.version, payload["target_prefix"]
                        ),
                        source_batch=batch_number,
                        target_hostnames=node.hostname,
                        target_ips=node.ip,
                        target_configs=package.version,
                        subject_type="node",
                        subject_id=node.id,
                        trigger_user_id=user_id,
                    )
                    session.add(task)
                    session.flush()
                    session.add(TaskLog(task_id=task.id, message="安装任务已加入执行队列"))
                    session.add(
                        NginxInstallRun(
                            task_id=task.id,
                            node_id=node.id,
                            batch_number=batch_number,
                            node_hostname=node.hostname,
                            node_ip=node.ip,
                            **run_values
                        )
                    )
                    task_ids.append(task.id)

        for task_id in task_ids:
            try:
                executor.submit(
                    task_id,
                    create_install_runner(
                        session_factory, encryption_key, package_dir, task_id
                    ),
                )
            except RuntimeError:
                with session_scope(session_factory) as session:
                    with session.begin():
                        session.execute(
                            update(Task)
                            .where(Task.id == task_id, Task.status == "pending")
                            .values(
                                status="failed",
                                progress=100,
                                detail="安装任务未能启动",
                                finished_at=datetime.utcnow(),
                            )
                        )
                        session.execute(
                            update(NginxInstallRun)
                            .where(NginxInstallRun.task_id == task_id)
                            .values(phase="failed")
                        )
                        session.add(
                            TaskLog(
                                task_id=task_id,
                                level="error",
                                message="安装任务未能启动",
                            )
                        )
    return {
        "batch_number": batch_number,
        "task_ids": task_ids,
        "skipped": skipped,
    }


def load_install_batch(
    session_factory, batch_number: str, owner_user_id: int = None
) -> dict:
    """读取安装批次进度和每个节点的阶段摘要。"""
    with session_scope(session_factory) as session:
        query = (
            select(NginxInstallRun)
            .join(Task, Task.id == NginxInstallRun.task_id)
            .options(joinedload(NginxInstallRun.task))
            .where(NginxInstallRun.batch_number == batch_number)
            .order_by(NginxInstallRun.id.asc())
        )
        if owner_user_id is not None:
            query = query.where(Task.trigger_user_id == owner_user_id)
        runs = session.scalars(query).all()
        if not runs:
            raise ValueError("安装批次不存在")
        items = []
        for run in runs:
            task = run.task
            items.append(
                {
                    "task_id": task.id,
                    "node_id": run.node_id,
                    "hostname": run.node_hostname,
                    "ip": run.node_ip,
                    "status": task.status,
                    "progress": task.progress,
                    "phase": run.phase,
                    "detail": task.detail,
                    "sync_ok": run.sync_ok,
                    "sync_detail": run.sync_detail,
                    "log_url": "/nginx-install/task/{}/log/".format(task.id),
                    "finished": task.status in ("success", "failed", "cancelled"),
                }
            )
    completed = [item for item in items if item["finished"]]
    successes = sum(1 for item in completed if item["status"] == "success")
    failures = sum(1 for item in completed if item["status"] != "success")
    return {
        "batch_number": batch_number,
        "tasks": items,
        "finished": len(completed) == len(items),
        "all_success": len(completed) == len(items) and successes == len(items),
        "success_count": successes,
        "fail_count": failures,
        "total": len(items),
        "progress": int(sum(item["progress"] for item in items) / len(items)),
    }
