"""执行配置版本发布并持久化任务进度、日志和结果树。"""

import hashlib
import io
import json
import logging
import re
import shlex
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import select, update
from sqlalchemy.orm import joinedload, sessionmaker

from ngxops.configs.models import ConfigBinding
from ngxops.credentials.crypto import CredentialDecryptionError
from ngxops.database.session import session_scope
from ngxops.logging_setup import log_exception
from ngxops.nodes.models import Node
from ngxops.settings.service import read_setting
from ngxops.tasks.executor import TaskCancelled, TaskContext, TaskOutcome, create_task


logger = logging.getLogger(__name__)
MAX_NODE_WORKERS = 3
DEFAULT_BACKUP_DIR = "/opt/app/mascloud/ansible/mngxops"
_NODE_LABEL_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_ANSI_ESCAPE_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_TERMINAL_ITEM_STATUSES = frozenset(("success", "failed"))


class RemoteCommandError(RuntimeError):
    """表示 SSH 命令通道无法完成远程操作。"""


def _remote_command(client, command: str, timeout: int = 45) -> Tuple[int, str]:
    """执行一条受 shell 引用保护的远程命令并返回退出码和输出。"""
    try:
        _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
        output = stdout.read().decode("utf-8", "replace")
        error = stderr.read().decode("utf-8", "replace")
        status = stdout.channel.recv_exit_status()
    except Exception as exc:
        detail = str(exc).strip()
        suffix = "：{}: {}".format(type(exc).__name__, detail) if detail else ""
        raise RemoteCommandError("SSH 远程命令未完成{}".format(suffix)) from exc
    return status, "\n".join(part for part in (output, error) if part).strip()


def _safe_node_label(hostname: str) -> str:
    """将主机名转换为备份目录可用的安全标签。"""
    label = _NODE_LABEL_RE.sub("_", (hostname or "").strip()).strip("._")
    return label or "unknown"


def _node_log_label(item: dict) -> str:
    """返回便于多节点日志筛选的主机名和 IP 标识。"""
    hostname = str(item.get("hostname") or "").strip()
    ip = str(item.get("ip") or "").strip()
    return "{} ({})".format(hostname, ip) if hostname and ip else hostname or ip


def _log_release_step(
    context: Optional[TaskContext],
    item: Optional[dict],
    message: str,
    level: str = "info",
) -> None:
    """写入包含节点和配置标识的发布步骤日志。"""
    if context is None:
        return
    prefix = ""
    if item is not None:
        prefix = "节点 {}".format(_node_log_label(item))
        config_name = str(item.get("config_name") or "").strip()
        if config_name:
            prefix += " 配置 {}".format(config_name)
        prefix += "："
    context.append_log(prefix + message, level)


def _append_remote_output(
    context: Optional[TaskContext],
    item: Optional[dict],
    output: str,
    level: str,
    label: str = "远程错误输出",
) -> None:
    """将远程诊断或异常详情拆成未超过任务日志字段限制的多行。"""
    if context is None or not output:
        return
    normalized = _ANSI_ESCAPE_RE.sub(
        "", output.replace("\r\n", "\n").replace("\r", "\n")
    )
    normalized = "".join(
        character if character.isprintable() or character == "\n" else " "
        for character in normalized
    )
    for line in normalized.splitlines():
        for offset in range(0, len(line), 3000):
            _log_release_step(
                context,
                item,
                "{}：{}".format(label, line[offset : offset + 3000]),
                level,
            )


def _remote_step(
    client,
    command: str,
    label: str,
    timeout: int = 45,
    context: Optional[TaskContext] = None,
    item: Optional[dict] = None,
    nonzero_level: str = "error",
    log_output: bool = False,
) -> Tuple[int, str]:
    """记录远程步骤状态及失败诊断后返回命令结果。"""
    _log_release_step(context, item, "开始远程步骤：{}".format(label))
    try:
        status, output = _remote_command(client, command, timeout=timeout)
    except RemoteCommandError as exc:
        _log_release_step(
            context,
            item,
            "远程步骤异常：{}".format(label),
            "error",
        )
        _append_remote_output(context, item, str(exc), "error", "SSH异常详情")
        raise
    if status == 0:
        _log_release_step(
            context, item, "远程步骤成功：{}（退出码 0）".format(label)
        )
        if log_output:
            _append_remote_output(context, item, output, "info", "远程命令输出")
    else:
        _log_release_step(
            context,
            item,
            "远程步骤失败：{}（退出码 {}）".format(label, status),
            nonzero_level,
        )
        _append_remote_output(context, item, output, nonzero_level)
    return status, output


def _tree_text(value: str, byte_limit: int) -> str:
    """清理控制字符并按 UTF-8 字节限制任务结果文本。"""
    printable = "".join(
        character if character.isprintable() else " " for character in value
    )
    return printable.encode("utf-8")[:byte_limit].decode("utf-8", "ignore")


def _backup_remote_file(
    client,
    remote_path: str,
    backup_dir: str,
    hostname: str,
    task_id: int,
    binding_id: int,
    context: Optional[TaskContext] = None,
    item: Optional[dict] = None,
) -> Tuple[Optional[str], str]:
    """备份现有远程文件并在首次发布时明确返回无备份状态。"""
    parent = "{}/{}/".format(backup_dir.rstrip("/"), _safe_node_label(hostname))
    filename = remote_path.rstrip("/").rsplit("/", 1)[-1] or "config"
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
    backup_path = "{}{}.{}.{}.{}".format(
        parent,
        filename,
        timestamp,
        task_id,
        binding_id,
    )
    source_q = shlex.quote(remote_path)
    parent_q = shlex.quote(parent)
    backup_q = shlex.quote(backup_path)
    command = (
        "if [ -f {source} ]; then "
        "if mkdir -p -- {parent} && cp -p -- {source} {backup}; then "
        "printf '%s' '__NGXOPS_BACKED_UP__'; else exit 4; fi; "
        "elif [ ! -e {source} ] && [ ! -L {source} ]; then "
        "printf '%s' '__NGXOPS_MISSING__'; else exit 3; fi"
    ).format(source=source_q, parent=parent_q, backup=backup_q)
    try:
        status, output = _remote_step(
            client,
            command,
            "检查并备份远程目标文件",
            context=context,
            item=item,
        )
    except RemoteCommandError:
        return None, "远程备份检查失败（诊断见任务日志）"
    if status != 0:
        return None, "远程备份失败{}".format(_format_remote_detail(output))
    if output == "__NGXOPS_BACKED_UP__":
        source_md5, source_error = _remote_md5(
            client, remote_path, context, item, "源文件"
        )
        backup_md5, backup_error = _remote_md5(
            client, backup_path, context, item, "备份文件"
        )
        if source_error or backup_error:
            detail = source_error or backup_error
            _log_release_step(context, item, detail, "error")
            return None, "远程备份校验失败：{}".format(detail)
        if source_md5 != backup_md5:
            _log_release_step(context, item, "远程备份 MD5 与源文件不一致", "error")
            return None, "远程备份 MD5 与源文件不一致"
        _log_release_step(
            context,
            item,
            "已备份现有目标文件并通过 MD5 校验：{}".format(backup_path),
        )
        return backup_path, ""
    if output == "__NGXOPS_MISSING__":
        _log_release_step(context, item, "远程目标文件不存在，首次发布跳过备份")
        return None, ""
    _log_release_step(
        context, item, "远程目标文件状态无法识别：{}".format(output), "error"
    )
    return None, "远程文件状态无法识别"


def _format_remote_detail(output: str) -> str:
    """指向任务日志中已记录的远程命令诊断输出。"""
    return "（远程诊断见任务日志）" if output else ""


def _remote_md5(
    client,
    remote_path: str,
    context: Optional[TaskContext] = None,
    item: Optional[dict] = None,
    label: str = "远程文件",
) -> Tuple[Optional[str], str]:
    """读取远程文件 MD5 并返回不含路径之外数据的错误摘要。"""
    command = "md5sum -- {} | awk '{{print $1}}'".format(shlex.quote(remote_path))
    try:
        status, output = _remote_step(
            client,
            command,
            "校验{} MD5".format(label),
            context=context,
            item=item,
        )
    except RemoteCommandError:
        return None, "远程文件校验命令失败（诊断见任务日志）"
    digest = output.splitlines()[0].strip() if output else ""
    if status != 0 or not re.fullmatch(r"[0-9a-fA-F]{32}", digest):
        error = "无法读取远程文件 MD5{}".format(_format_remote_detail(output))
        _log_release_step(context, item, error, "error")
        if status == 0:
            _append_remote_output(context, item, output, "error")
        return None, error
    return digest.lower(), ""


def _restore_remote_file(
    client,
    remote_path: str,
    backup_path: Optional[str],
    context: Optional[TaskContext] = None,
    item: Optional[dict] = None,
) -> Tuple[bool, str]:
    """从备份恢复原文件或删除没有旧版本的首次发布文件。"""
    target_q = shlex.quote(remote_path)
    if backup_path:
        command = "cp -p -- {} {}".format(
            shlex.quote(backup_path),
            target_q,
        )
    else:
        command = "rm -f -- {}".format(target_q)
    try:
        status, output = _remote_step(
            client,
            command,
            "恢复远程配置文件",
            context=context,
            item=item,
        )
    except RemoteCommandError:
        return False, "SSH 通道关闭，无法恢复远程文件（诊断见任务日志）"
    if status != 0:
        return False, "恢复远程文件失败{}".format(_format_remote_detail(output))
    _log_release_step(context, item, "远程配置文件已恢复")
    return True, "远程文件已恢复"


def _deploy_binding_file(
    client,
    item: dict,
    backup_dir: str,
    task_id: int,
    context: Optional[TaskContext] = None,
) -> Tuple[bool, Optional[str], str, str]:
    """备份、临时上传、校验配置文件并执行单项 nginx -t。"""
    remote_path = item["remote_path"]
    content = item["content"].replace("\r\n", "\n").replace("\r", "\n")
    payload = content.encode("utf-8")
    if not payload.strip():
        return False, None, "", "配置内容为空，无法发布"
    backup_path, backup_error = _backup_remote_file(
        client,
        remote_path,
        backup_dir,
        item["hostname"],
        task_id,
        item["binding_id"],
        context,
        item,
    )
    if backup_error:
        return False, None, "", backup_error
    filename = remote_path.rstrip("/").rsplit("/", 1)[-1] or "config"
    temporary_path = "/tmp/{}.ngxops_tmp.{}.{}".format(
        filename,
        task_id,
        item["binding_id"],
    )
    expected_md5 = hashlib.md5(payload).hexdigest()
    target_may_be_changed = False
    try:
        _log_release_step(
            context,
            item,
            "开始通过 SFTP 上传临时文件：{}（{} 字节）".format(
                temporary_path, len(payload)
            ),
        )
        sftp = client.open_sftp()
        try:
            sftp.putfo(io.BytesIO(payload), temporary_path)
        finally:
            sftp.close()
        _log_release_step(context, item, "SFTP 临时文件上传完成")
        size_command = "wc -c < {}".format(shlex.quote(temporary_path))
        size_status, size_output = _remote_step(
            client,
            size_command,
            "检查临时文件大小",
            context=context,
            item=item,
        )
        if size_status != 0 or not size_output.strip().isdigit():
            _log_release_step(
                context,
                item,
                "临时文件大小检查失败：{}".format(size_output or "未返回大小"),
                "error",
            )
            return False, backup_path, "", "临时文件大小检查失败"
        if int(size_output.strip()) != len(payload):
            _log_release_step(
                context,
                item,
                "临时文件大小不匹配：预期 {} 字节，实际 {} 字节".format(
                    len(payload), size_output.strip()
                ),
                "error",
            )
            return False, backup_path, "", "临时文件大小与配置正文不一致"
        _log_release_step(
            context, item, "临时文件大小校验通过：{} 字节".format(len(payload))
        )
        temporary_md5, error = _remote_md5(
            client, temporary_path, context, item, "临时文件"
        )
        if error:
            return False, backup_path, "", error
        if temporary_md5 != expected_md5:
            _log_release_step(context, item, "临时文件 MD5 与本地正文不一致", "error")
            return False, backup_path, "", "临时文件 MD5 校验失败"
        remote_directory = (
            remote_path.rsplit("/", 1)[0] if "/" in remote_path else "."
        ) or "/"
        directory_status, directory_output = _remote_step(
            client,
            "mkdir -p -- {}".format(shlex.quote(remote_directory)),
            "创建远程目标目录",
            context=context,
            item=item,
        )
        if directory_status != 0:
            raise ValueError(
                "创建远程目标目录失败{}".format(
                    _format_remote_detail(directory_output)
                )
            )
        copy_command = "cp -- {} {}".format(
            shlex.quote(temporary_path),
            shlex.quote(remote_path),
        )
        target_may_be_changed = True
        copy_status, copy_output = _remote_step(
            client,
            copy_command,
            "复制临时文件到目标路径",
            context=context,
            item=item,
        )
        if copy_status != 0:
            raise ValueError(
                "复制到目标路径失败{}".format(_format_remote_detail(copy_output))
            )
        target_md5, error = _remote_md5(
            client, remote_path, context, item, "目标文件"
        )
        if error:
            raise ValueError(error)
        if target_md5 != expected_md5:
            _log_release_step(context, item, "目标文件 MD5 与本地正文不一致", "error")
            raise ValueError("目标文件 MD5 校验失败")
        nginx_test = "{} -t".format(shlex.quote(item["nginx_path"] or "/usr/sbin/nginx"))
        test_status, test_output = _remote_step(
            client,
            nginx_test,
            "运行 nginx -t 配置检查",
            timeout=90,
            context=context,
            item=item,
            log_output=True,
        )
        if test_status != 0:
            raise ValueError(
                "nginx -t 未通过{}".format(_format_remote_detail(test_output))
            )
        _log_release_step(context, item, "nginx -t 配置检查通过")
        _log_release_step(context, item, "配置文件发布准备完成，等待节点统一 reload")
        return True, backup_path, target_md5, "配置上传并通过 nginx -t"
    except Exception as exc:
        if isinstance(exc, ValueError):
            error = str(exc).strip()
        elif isinstance(exc, RemoteCommandError):
            error = "远程命令执行失败（诊断见任务日志）"
        else:
            detail = str(exc).strip()
            _log_release_step(context, item, "上传或校验异常：{}".format(type(exc).__name__), "error")
            _append_remote_output(
                context,
                item,
                detail or "无异常详情",
                "error",
                "SFTP异常详情",
            )
            error = "上传或校验远程文件失败（{}，详情见任务日志）".format(
                type(exc).__name__
            )
        if target_may_be_changed:
            restored, restore_message = _restore_remote_file(
                client,
                remote_path,
                backup_path,
                context,
                item,
            )
            if not restored:
                error = "{}；{}".format(error, restore_message)
        return False, backup_path, "", error
    finally:
        try:
            status, output = _remote_step(
                client,
                "rm -f -- {}".format(shlex.quote(temporary_path)),
                "清理远程临时文件",
                context=context,
                item=item,
            )
            if status != 0:
                logger.warning("发布临时文件清理失败 binding_id=%s", item["binding_id"])
        except RemoteCommandError as exc:
            logger.warning("发布临时文件清理通道失败 binding_id=%s", item["binding_id"])
            _log_release_step(
                context, item, "清理远程临时文件失败：{}".format(exc), "warning"
            )


def _delete_binding_file(
    client,
    item: dict,
    backup_dir: str,
    task_id: int,
    context: Optional[TaskContext] = None,
) -> Tuple[bool, Optional[str], str, str]:
    """备份后删除标记绑定的远程文件并校验 Nginx 配置。"""
    remote_path = item["remote_path"]
    backup_path, backup_error = _backup_remote_file(
        client,
        remote_path,
        backup_dir,
        item["hostname"],
        task_id,
        item["binding_id"],
        context,
        item,
    )
    if backup_error:
        return False, None, "", backup_error

    def fail(message: str) -> Tuple[bool, Optional[str], str, str]:
        """恢复删除前文件并返回安全错误摘要。"""
        restored, detail = _restore_remote_file(
            client, remote_path, backup_path, context, item
        )
        if not restored:
            message = "{}；{}".format(message, detail)
        return False, backup_path, "", message

    try:
        remove_status, remove_output = _remote_step(
            client,
            "rm -f -- {}".format(shlex.quote(remote_path)),
            "删除远程配置文件",
            context=context,
            item=item,
        )
        if remove_status != 0:
            return fail(
                "删除远程配置失败{}".format(_format_remote_detail(remove_output))
            )

        exists_status, exists_output = _remote_step(
            client,
            "if [ ! -e {path} ] && [ ! -L {path} ]; then exit 0; else exit 1; fi".format(
                path=shlex.quote(remote_path)
            ),
            "确认远程配置文件已删除",
            context=context,
            item=item,
        )
        if exists_status != 0:
            return fail(
                "未能确认远程配置已删除{}".format(
                    _format_remote_detail(exists_output)
                )
            )

        nginx_test = "{} -t".format(
            shlex.quote(item["nginx_path"] or "/usr/sbin/nginx")
        )
        test_status, test_output = _remote_step(
            client,
            nginx_test,
            "运行 nginx -t 配置检查",
            timeout=90,
            context=context,
            item=item,
            log_output=True,
        )
        if test_status != 0:
            return fail(
                "删除配置后 nginx -t 未通过{}".format(
                    _format_remote_detail(test_output)
                )
            )
    except RemoteCommandError:
        return fail("远程删除或校验未完成（诊断见任务日志）")
    _log_release_step(context, item, "远程配置已删除并通过 nginx -t")
    return True, backup_path, "", "远程配置已删除并通过 nginx -t"


def _reload_nginx(
    client,
    nginx_path: str,
    context: Optional[TaskContext] = None,
    item: Optional[dict] = None,
) -> Tuple[bool, str]:
    """在节点全部配置通过 nginx -t 后执行一次 reload 或启动。"""
    command = ""
    success_message = ""
    systemctl_status, _systemctl_output = _remote_step(
        client,
        "command -v systemctl >/dev/null 2>&1",
        "检查 systemd 管理能力",
        context=context,
        item=item,
        nonzero_level="info",
    )
    if systemctl_status == 0:
        active_status, active_output = _remote_step(
            client,
            "systemctl is-active nginx 2>/dev/null || true",
            "读取 systemd Nginx 运行状态",
            context=context,
            item=item,
        )
        active_state = active_output.splitlines()[-1].strip() if active_output else ""
        _log_release_step(
            context,
            item,
            "systemd Nginx 当前状态：{}".format(active_state or "未知"),
        )
        if active_state in ("active", "activating", "reloading"):
            command = "systemctl reload nginx 2>&1"
            success_message = "systemd nginx reload 成功"
        else:
            enabled_status, enabled_output = _remote_step(
                client,
                "systemctl is-enabled nginx 2>/dev/null || true",
                "读取 systemd Nginx 启用状态",
                context=context,
                item=item,
            )
            enabled_state = enabled_output.splitlines()[-1].strip() if enabled_output else ""
            _log_release_step(
                context,
                item,
                "systemd Nginx 启用状态：{}".format(enabled_state or "未知"),
            )
            if enabled_state in ("enabled", "enabled-runtime", "static"):
                command = "systemctl start nginx 2>&1"
                success_message = "Nginx 未运行，systemd start 成功"
            else:
                command = ""
                success_message = ""
        if command:
            status, output = _remote_step(
                client,
                command,
                "执行 systemd Nginx start/reload",
                timeout=90,
                context=context,
                item=item,
                log_output=True,
            )
            if status == 0:
                _log_release_step(context, item, success_message)
                return True, success_message
            return False, "{}{}".format(
                "Nginx reload/start 失败",
                _format_remote_detail(output),
            )
        _log_release_step(context, item, "没有可用的 systemd Nginx unit")
    binary = shlex.quote(nginx_path or "/usr/sbin/nginx")
    if systemctl_status != 0:
        _log_release_step(context, item, "systemd 不可用，改用 Nginx 二进制操作")
    elif not command:
        _log_release_step(context, item, "改用 Nginx 二进制操作")
    running_status, _running_output = _remote_step(
        client,
        "pgrep -x nginx >/dev/null 2>&1",
        "检查 Nginx 进程",
        context=context,
        item=item,
        nonzero_level="info",
    )
    command = "{} -s reload".format(binary) if running_status == 0 else binary
    status, output = _remote_step(
        client,
        command,
        "执行 Nginx 二进制 reload/start",
        timeout=90,
        context=context,
        item=item,
        log_output=True,
    )
    if status == 0:
        message = "nginx reload 成功" if running_status == 0 else "Nginx 未运行，已启动"
        _log_release_step(context, item, message)
        return True, message
    return False, "Nginx reload/start 失败{}".format(_format_remote_detail(output))


def _load_release_target(
    session_factory: sessionmaker,
    node_id: int,
    encryption_key: bytes,
    items: Sequence[dict],
) -> Tuple[Optional[dict], str]:
    """重新校验节点和绑定状态并在关闭 Session 前解密 SSH 凭证。"""
    with session_scope(session_factory) as session:
        current_bindings = session.scalars(
            select(ConfigBinding).where(
                ConfigBinding.id.in_([item["binding_id"] for item in items]),
                ConfigBinding.node_id == node_id,
            )
        ).all()
        bindings_by_id = {binding.id: binding for binding in current_bindings}
        for item in items:
            binding = bindings_by_id.get(item["binding_id"])
            expected_deleted = item["action"] == "delete"
            if (
                binding is None
                or binding.remote_path != item["remote_path"]
                or (binding.sync_status == "marked_deleted") != expected_deleted
            ):
                return None, "配置绑定状态已变化，请重新选择发布项"
        node = session.scalar(
            select(Node)
            .options(joinedload(Node.credential))
            .where(Node.id == node_id)
        )
        if node is None or node.is_deleted:
            return None, "节点已删除"
        if node.is_locked:
            return None, "节点已锁定"
        if node.status != "online":
            return None, "节点非在线状态"
        if node.nginx_available is not True:
            return None, "节点 Nginx 未确认可用"
        credential = node.credential
        if credential is None or not credential.is_enabled:
            return None, "节点未关联启用的 SSH 凭证"
        target = {
            "id": node.id,
            "hostname": node.hostname,
            "ip": node.ip,
            "port": node.port,
            "nginx_path": node.nginx_path,
            "username": credential.username,
            "auth_type": credential.auth_type,
            "password": "",
            "private_key": "",
            "ssh_timeout": read_setting(session, "node.ssh_connect_timeout", 10),
            "detect_retries": read_setting(session, "node.detect_retries", 1),
        }
        try:
            secret = (
                credential.get_password(encryption_key)
                if credential.auth_type == "password"
                else credential.get_private_key(encryption_key)
            )
        except CredentialDecryptionError:
            return None, "SSH 凭证解密失败"
        target["password" if credential.auth_type == "password" else "private_key"] = secret
    return target, ""


def _update_bindings_success(
    session_factory: sessionmaker,
    items: Sequence[dict],
) -> None:
    """在单个事务中回写本节点成功发布的全部绑定状态。"""
    now = datetime.utcnow()
    with session_scope(session_factory) as session:
        with session.begin():
            for item in items:
                if item["action"] == "delete":
                    binding = session.scalar(
                        select(ConfigBinding).where(
                            ConfigBinding.id == item["binding_id"],
                            ConfigBinding.node_id == item["node_id"],
                            ConfigBinding.sync_status == "marked_deleted",
                        )
                    )
                    if binding is not None:
                        session.delete(binding)
                    continue
                session.execute(
                    update(ConfigBinding)
                    .where(
                        ConfigBinding.id == item["binding_id"],
                        ConfigBinding.node_id == item["node_id"],
                        ConfigBinding.sync_status != "marked_deleted",
                    )
                    .values(
                        synced_version=item["version"],
                        remote_content_hash=item["content_md5"],
                        sync_status="synced",
                        last_sync_time=now,
                        last_sync_error="",
                        updated_at=now,
                    )
                )


def _result_item(item: dict) -> dict:
    """构造不含正文或凭证的持久化发布结果项。"""
    remote_path = item["remote_path"]
    return {
        "binding_id": item["binding_id"],
        "config_id": item["config_id"],
        "config_name": _tree_text(item["config_name"][:255], 765),
        "version": item["version"],
        "remote_path": _tree_text(remote_path[:160], 480),
        "remote_path_hash": hashlib.sha256(remote_path.encode("utf-8")).hexdigest(),
        "action": item["action"],
        "status": "pending",
        "message": "等待删除" if item["action"] == "delete" else "等待执行",
    }


def _tree_summary(tree: dict) -> dict:
    """汇总发布结果树中的配置数量和终态数量。"""
    rows = [item for node in tree["nodes"] for item in node["bindings"]]
    success = sum(1 for item in rows if item["status"] == "success")
    failed = sum(1 for item in rows if item["status"] == "failed")
    running = sum(
        1
        for item in rows
        if item["status"] in ("running", "waiting_reload")
    )
    return {
        "total": len(rows),
        "success": success,
        "failed": failed,
        "running": running,
        "pending": len(rows) - success - failed - running,
    }


def _persist_tree(
    context: TaskContext,
    tree: dict,
    lock: threading.RLock,
) -> None:
    """串行持久化最新发布结果树和真实完成进度。"""
    with lock:
        tree["summary"] = _tree_summary(tree)
        snapshot = json.loads(json.dumps(tree, ensure_ascii=False))
        context.set_result_tree(snapshot)
        summary = tree["summary"]
        done = summary["success"] + summary["failed"]
        progress = min(95, 5 + int(done * 90 / max(summary["total"], 1)))
        context.update_progress(
            progress,
            "已完成 {} / {} 个配置，成功 {}，失败 {}".format(
                done,
                summary["total"],
                summary["success"],
                summary["failed"],
            ),
        )


def _set_item_status(
    context: TaskContext,
    tree: dict,
    binding_id: int,
    status: str,
    message: str,
    lock: threading.RLock,
) -> None:
    """更新一个绑定的发布阶段并即时保存到任务结果树。"""
    with lock:
        for node in tree["nodes"]:
            for item in node["bindings"]:
                if item["binding_id"] == binding_id:
                    item["status"] = status
                    item["message"] = message[:160]
                    break
        _persist_tree(context, tree, lock)


def _set_node_status(
    context: TaskContext,
    tree: dict,
    node_id: int,
    status: str,
    lock: threading.RLock,
) -> None:
    """更新一个节点的批量发布阶段并保存结果树。"""
    with lock:
        for node in tree["nodes"]:
            if node["node_id"] == node_id:
                node["status"] = status
                break
        _persist_tree(context, tree, lock)


def _rollback_pending(
    context: TaskContext,
    client,
    pending: Sequence[dict],
    tree: dict,
    tree_lock: threading.RLock,
    reason: str,
) -> None:
    """恢复同节点已上传但尚未 reload 生效的配置。"""
    for item in reversed(pending):
        restored, detail = _restore_remote_file(
            client,
            item["remote_path"],
            item.get("backup_path"),
            context,
            item,
        )
        message = "{}，{}".format(reason, detail)
        context.append_log(
            "节点 {} 配置 {} 回滚{}".format(
                _node_log_label(item),
                item["config_name"],
                "成功" if restored else "失败",
            ),
            "info" if restored else "error",
        )
        _set_item_status(context, tree, item["binding_id"], "failed", message, tree_lock)


def _run_node_batch(
    context: TaskContext,
    session_factory: sessionmaker,
    encryption_key: bytes,
    node_id: int,
    items: Sequence[dict],
    backup_dir: str,
    task_id: int,
    tree: dict,
    tree_lock: threading.RLock,
    operation_label: str,
) -> None:
    """复用单条 SSH 会话串行处理一个节点的全部配置。"""
    context.check_cancelled()
    target, target_error = _load_release_target(
        session_factory,
        node_id,
        encryption_key,
        items,
    )
    if target is None:
        for item in items:
            _set_item_status(
                context,
                tree,
                item["binding_id"],
                "failed",
                target_error,
                tree_lock,
            )
        _set_node_status(context, tree, node_id, "failed", tree_lock)
        context.append_log(
            "节点 {} 门禁失败：{}".format(_node_log_label(items[0]), target_error),
            "error",
        )
        return
    for item in items:
        item["nginx_path"] = target["nginx_path"]

    from ngxops.credentials.tasks import _connect_ssh

    client, connection_error = _connect_ssh(
        target["ip"],
        target["port"],
        target["username"],
        target["auth_type"],
        target["password"],
        target["private_key"],
        context,
        detailed_logging=True,
    )
    if client is None:
        for item in items:
            _set_item_status(
                context,
                tree,
                item["binding_id"],
                "failed",
                connection_error or "SSH 连接失败",
                tree_lock,
            )
        _set_node_status(context, tree, node_id, "failed", tree_lock)
        context.append_log(
            "节点 {} SSH 连接失败".format(_node_log_label(items[0])),
            "error",
        )
        return

    pending = []
    failure_message = ""
    try:
        _set_node_status(context, tree, node_id, "running", tree_lock)
        context.append_log(
            "节点 {} SSH 已连接，开始{} {} 个配置".format(
                _node_log_label(items[0]), operation_label, len(items)
            )
        )
        for item in items:
            context.check_cancelled()
            if failure_message:
                _set_item_status(
                    context,
                    tree,
                    item["binding_id"],
                    "failed",
                    "同节点前序配置失败，已跳过",
                    tree_lock,
                )
                continue
            _set_item_status(
                context,
                tree,
                item["binding_id"],
                "running",
                "正在备份、上传并校验配置",
                tree_lock,
            )
            context.append_log(
                "节点 {} 开始{} {} v{} 至 {}".format(
                    _node_log_label(item),
                    operation_label,
                    item["config_name"],
                    item["version"],
                    item["remote_path"],
                )
            )
            deploy = (
                _delete_binding_file
                if item["action"] == "delete"
                else _deploy_binding_file
            )
            success, backup_path, content_md5, result = deploy(
                client, item, backup_dir, task_id, context
            )
            if not success:
                failure_message = "配置 {} {}失败：{}".format(
                    item["config_name"], operation_label, result
                )
                _set_item_status(
                    context,
                    tree,
                    item["binding_id"],
                    "failed",
                    result,
                    tree_lock,
                )
                context.append_log(
                    "节点 {} 配置 {} {}失败：{}".format(
                        _node_log_label(item), item["config_name"], operation_label, result
                    ),
                    "error",
                )
                if pending:
                    _rollback_pending(
                        context,
                        client,
                        pending,
                        tree,
                        tree_lock,
                        "同节点后续配置失败，未统一 reload",
                    )
                    pending = []
                continue
            pending.append(
                {
                    **item,
                    "backup_path": backup_path,
                    "content_md5": content_md5,
                }
            )
            _set_item_status(
                context,
                tree,
                item["binding_id"],
                "waiting_reload",
                (
                    "删除和 nginx -t 通过，等待节点统一 reload"
                    if item["action"] == "delete"
                    else "上传和 nginx -t 通过，等待节点统一 reload"
                ),
                tree_lock,
            )
        if pending and not failure_message:
            context.check_cancelled()
            _set_node_status(context, tree, node_id, "reloading", tree_lock)
            context.append_log(
                "节点 {} 全部配置通过校验，执行一次 Nginx reload".format(
                    _node_log_label(items[0])
                )
            )
            reload_ok, reload_message = _reload_nginx(
                client,
                items[0]["nginx_path"],
                context,
                items[0],
            )
            if reload_ok:
                _update_bindings_success(session_factory, pending)
                for item in pending:
                    _set_item_status(
                        context,
                        tree,
                        item["binding_id"],
                        "success",
                        "{}成功并已统一 reload".format(operation_label),
                        tree_lock,
                    )
                _set_node_status(context, tree, node_id, "success", tree_lock)
                context.append_log(
                    "节点 {} {}完成：{}".format(
                        _node_log_label(items[0]), operation_label, reload_message
                    )
                )
            else:
                _rollback_pending(
                    context,
                    client,
                    pending,
                    tree,
                    tree_lock,
                    "Nginx reload 失败",
                )
                _set_node_status(context, tree, node_id, "failed", tree_lock)
                context.append_log(
                    "节点 {} Nginx reload 失败：{}".format(
                        _node_log_label(items[0]), reload_message
                    ),
                    "error",
                )
        elif not failure_message:
            _set_node_status(context, tree, node_id, "failed", tree_lock)
        else:
            _set_node_status(context, tree, node_id, "failed", tree_lock)
    except Exception as exc:
        if not isinstance(exc, TaskCancelled):
            context.append_log(
                "节点 {} 发布流程出现未处理异常：{}".format(
                    _node_log_label(items[0]), type(exc).__name__
                ),
                "error",
            )
            _append_remote_output(
                context, items[0], str(exc) or "无异常详情", "error", "异常详情"
            )
        if pending:
            _rollback_pending(
                context,
                client,
                pending,
                tree,
                tree_lock,
                "节点{}中断，未统一 reload".format(operation_label),
            )
        raise
    finally:
        try:
            client.close()
        except Exception:
            pass


def _build_publish_runner(
    session_factory: sessionmaker,
    encryption_key: bytes,
    selected: Sequence[dict],
    batch_number: str,
    backup_dir: str,
    operation_type: str,
    max_workers: int = MAX_NODE_WORKERS,
):
    """构造发布或回滚执行闭包并将配置正文限制在任务内存生命周期。"""
    operation_label = "回滚" if operation_type == "release_rollback" else "发布"
    groups: Dict[int, List[dict]] = {}
    node_info: Dict[int, dict] = {}
    for item in selected:
        groups.setdefault(item["node_id"], []).append(item)
        node_info[item["node_id"]] = {
            "node_id": item["node_id"],
            "hostname": _tree_text(item["hostname"][:100], 300),
            "ip": item["ip"],
            "status": "pending",
            "bindings": [_result_item(binding) for binding in groups[item["node_id"]]],
        }
    tree = {
        "batch_number": batch_number,
        "summary": {"total": len(selected), "success": 0, "failed": 0, "running": 0, "pending": len(selected)},
        "nodes": [node_info[node_id] for node_id in sorted(node_info)],
    }

    def run(context: TaskContext) -> TaskOutcome:
        """并行执行不同节点并在节点内串行处理配置发布。"""
        tree_lock = threading.RLock()
        context.set_result_tree(tree)
        context.update_progress(1, "{}批次已排队".format(operation_label))
        context.append_log(
            "{}批次 {} 开始：{} 个节点，{} 个配置".format(
                operation_label,
                batch_number,
                len(groups),
                len(selected),
            )
        )
        worker_count = min(max_workers, len(groups))
        with ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="ngxops-release",
        ) as pool:
            futures = {
                pool.submit(
                    _run_node_batch,
                    context,
                    session_factory,
                    encryption_key,
                    node_id,
                    items,
                    backup_dir,
                    context.task_id,
                    tree,
                    tree_lock,
                    operation_label,
                ): (node_id, items)
                for node_id, items in groups.items()
            }
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as exc:
                    log_exception(
                        logger,
                        "{}节点执行失败".format(operation_label),
                        exc,
                        "task_id={}".format(context.task_id),
                    )
                    node_id, node_items = futures[future]
                    for item in node_items:
                        state = next(
                            result_item
                            for node in tree["nodes"]
                            if node["node_id"] == node_id
                            for result_item in node["bindings"]
                            if result_item["binding_id"] == item["binding_id"]
                        )
                        if state["status"] not in _TERMINAL_ITEM_STATUSES:
                            _set_item_status(
                                context,
                                tree,
                                item["binding_id"],
                                "failed",
                                "节点任务异常，{}未完成".format(operation_label),
                                tree_lock,
                            )
                    _set_node_status(context, tree, node_id, "failed", tree_lock)
        context.check_cancelled()
        with tree_lock:
            tree["summary"] = _tree_summary(tree)
            result_tree = json.loads(json.dumps(tree, ensure_ascii=False))
        failed = result_tree["summary"]["failed"]
        succeeded = result_tree["summary"]["success"]
        status = "failed" if failed else "success"
        detail = "{}批次 {} 完成：成功 {}，失败 {}，共 {}".format(
            operation_label,
            batch_number,
            succeeded,
            failed,
            len(selected),
        )
        return TaskOutcome(status, detail, result_tree)

    return run


def create_publish_task(
    session_factory: sessionmaker,
    executor,
    encryption_key: bytes,
    selected: Sequence[dict],
    *,
    batch_number: str,
    backup_dir: str,
    trigger_user_id: int,
    trigger_ip: str = "",
    operation_type: str = "release_publish",
    max_workers: int = MAX_NODE_WORKERS,
) -> int:
    """创建带批次号的发布或回滚任务并提交到统一执行器。"""
    nodes = {}
    for item in selected:
        nodes[item["node_id"]] = item
    runner = _build_publish_runner(
        session_factory,
        encryption_key,
        selected,
        batch_number,
        backup_dir or DEFAULT_BACKUP_DIR,
        operation_type,
        max_workers,
    )
    return create_task(
        session_factory,
        executor,
        runner,
        operation_type=operation_type,
        detail="{} {} 个配置到 {} 个节点".format(
            "回滚" if operation_type == "release_rollback" else "发布",
            len(selected),
            len(nodes),
        ),
        source_batch=batch_number,
        target_hostnames=[item["hostname"] for item in nodes.values()],
        target_ips=[item["ip"] for item in nodes.values()],
        target_configs=[item["config_name"] for item in selected],
        trigger_user_id=trigger_user_id,
        trigger_ip=trigger_ip,
    )
