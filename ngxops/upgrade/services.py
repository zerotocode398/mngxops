"""提供升级包校验、参数整理和远程编译执行服务。"""

import hashlib
import io
import json
import logging
import os
import posixpath
import re
import shlex
import tarfile
import time
import threading
import uuid
import zipfile
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlsplit

import paramiko
from sqlalchemy import select, update
from sqlalchemy.orm import joinedload, sessionmaker

from ngxops.audit.service import prepare_audit_session, sync_task_audit_result
from ngxops.credentials.crypto import CredentialDecryptionError
from ngxops.credentials.models import Credential
from ngxops.database.session import session_scope
from ngxops.logging_setup import log_exception
from ngxops.nodes.models import Node
from ngxops.releases.services import _reload_nginx
from ngxops.settings.models import preset_by_key
from ngxops.settings.service import read_setting
from ngxops.tasks.executor import TaskCancelled, TaskContext, TaskOutcome
from ngxops.tasks.models import Task
from ngxops.upgrade.models import (
    NginxModulePackage,
    NginxSourcePackage,
    NginxUpgradeLog,
    NginxUpgradeRun,
)
from ngxops.upgrade.builtin_modules import BUILTIN_ADD_MODULES


logger = logging.getLogger(__name__)
_MODULE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,99}$")
_VERSION_FROM_NAME = re.compile(r"nginx-([0-9]+(?:\.[0-9]+){1,3})", re.IGNORECASE)
_VERSION_FROM_OUTPUT = re.compile(r"nginx version:\s*nginx/([0-9]+(?:\.[0-9]+){1,3})")
_VALID_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
_VALID_GIT_URL = re.compile(r"^(?:https?://|ssh://|git://|[A-Za-z0-9_.-]+@)[^\s]+$")


@dataclass(frozen=True)
class _UpgradeRunSnapshot:
    """保存后台升级执行所需的非 ORM 参数快照。"""

    node_hostname: str
    node_ip: str
    remote_work_dir: str
    current_configure_opts: str
    third_party_json: str
    current_binary_path: str
    target_configure_opts: str
    make_jobs: int
    upgrade_mode: str
    target_prefix: str
    target_version: str
_MAX_ARCHIVE_MEMBERS = 50000
_MAX_UNPACKED_BYTES = 2 * 1024 * 1024 * 1024
_BATCH_LOCK = threading.Lock()


class PackageConflict(ValueError):
    """表示上传包与已有用户版本或文件内容冲突。"""

    def __init__(self, message: str, conflict_type: str) -> None:
        """保存面向上传页面的冲突类型。"""
        super().__init__(message)
        self.conflict_type = conflict_type


class UpgradeTaskError(RuntimeError):
    """表示升级流水线遇到可安全展示的业务失败。"""


def archive_extension(filename: str, package_kind: str) -> str:
    """校验归档扩展名并返回规范后缀。"""
    lowered = (filename or "").lower()
    extensions = (".tar.gz", ".tgz")
    if package_kind == "module":
        extensions = extensions + (".zip",)
    for extension in extensions:
        if lowered.endswith(extension):
            return extension
    if package_kind == "source":
        raise ValueError("仅支持 .tar.gz / .tgz 格式的源码包")
    raise ValueError("仅支持 .tar.gz / .tgz / .zip 格式的模块包")


def _validate_archive_member(name: str) -> bool:
    """拒绝绝对路径、盘符路径和包含父目录的归档成员。"""
    normalized = name.replace("\\", "/").rstrip("/")
    if not normalized or normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        return False
    return all(part not in ("..", "") for part in normalized.split("/") if part != ".")


def validate_archive_content(content: bytes, filename: str, package_kind: str) -> str:
    """校验归档类型、成员路径、成员数量和展开体积。"""
    extension = archive_extension(filename, package_kind)
    try:
        if extension == ".zip":
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                entries = archive.infolist()
                if len(entries) > _MAX_ARCHIVE_MEMBERS:
                    raise ValueError("归档文件包含的条目过多")
                total_size = 0
                for entry in entries:
                    if not _validate_archive_member(entry.filename):
                        raise ValueError("归档包含不安全的文件路径")
                    mode = entry.external_attr >> 16
                    if mode & 0o170000 == 0o120000:
                        raise ValueError("归档不允许包含符号链接")
                    file_type = mode & 0o170000
                    if file_type not in (0, 0o100000, 0o040000):
                        raise ValueError("归档包含不支持的特殊文件")
                    total_size += entry.file_size
                if total_size > _MAX_UNPACKED_BYTES:
                    raise ValueError("归档展开体积超过 2 GiB")
        else:
            with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as archive:
                members = archive.getmembers()
                if len(members) > _MAX_ARCHIVE_MEMBERS:
                    raise ValueError("归档文件包含的条目过多")
                total_size = 0
                for member in members:
                    if not _validate_archive_member(member.name):
                        raise ValueError("归档包含不安全的文件路径")
                    if not (
                        member.isfile()
                        or member.isdir()
                        or member.issym()
                        or member.islnk()
                    ):
                        raise ValueError("归档包含不支持的特殊文件")
                    if member.issym() or member.islnk():
                        target = posixpath.normpath(
                            posixpath.join(posixpath.dirname(member.name), member.linkname)
                        )
                        if member.linkname.startswith("/") or target == ".." or target.startswith("../"):
                            raise ValueError("归档包含指向目录外部的链接")
                    total_size += max(0, member.size)
                if total_size > _MAX_UNPACKED_BYTES:
                    raise ValueError("归档展开体积超过 2 GiB")
    except (tarfile.TarError, zipfile.BadZipFile, EOFError, OSError) as exc:
        raise ValueError("无法读取归档文件，请确认文件完整") from exc
    return extension


def _package_digest(content: bytes) -> str:
    """返回归档内容的 MD5 传输校验值。"""
    return hashlib.md5(content).hexdigest()


def _package_active_task_ids(session, package_id: int, package_kind: str) -> List[int]:
    """查询仍在执行且依赖指定归档包的任务。"""
    from ngxops.nginx_install.models import NginxInstallRun

    query = (
        select(NginxUpgradeRun.task_id)
        .join(Task, Task.id == NginxUpgradeRun.task_id)
        .where(Task.status.in_(("pending", "running")))
    )
    if package_kind == "source":
        query = query.where(NginxUpgradeRun.source_package_id == package_id)
        active_ids = list(session.scalars(query))
        install_ids = session.scalars(
            select(NginxInstallRun.task_id)
            .join(Task, Task.id == NginxInstallRun.task_id)
            .where(
                NginxInstallRun.source_package_id == package_id,
                Task.status.in_(("pending", "running")),
            )
        )
        return active_ids + list(install_ids)
    query = query.with_only_columns(
        NginxUpgradeRun.task_id, NginxUpgradeRun.third_party_json
    )
    active_ids = []
    for task_id, third_party_json in session.execute(query):
        try:
            modules = json.loads(third_party_json or "[]")
        except ValueError:
            modules = []
        if any(
            isinstance(item, dict)
            and item.get("source") == "package"
            and str(item.get("package_id")) == str(package_id)
            for item in modules
        ):
            active_ids.append(task_id)
    install_query = (
        select(NginxInstallRun.task_id, NginxInstallRun.third_party_json)
        .join(Task, Task.id == NginxInstallRun.task_id)
        .where(Task.status.in_(("pending", "running")))
    )
    for task_id, third_party_json in session.execute(install_query):
        try:
            modules = json.loads(third_party_json or "[]")
        except ValueError:
            modules = []
        if any(
            isinstance(item, dict)
            and item.get("source") == "package"
            and str(item.get("package_id")) == str(package_id)
            for item in modules
        ):
            active_ids.append(task_id)
    return active_ids


def store_package(
    session,
    package_dir: Path,
    *,
    package_kind: str,
    filename: str,
    content: bytes,
    user_id: int,
    name: str,
    version: str,
    description: str,
    is_official: bool = False,
    overwrite: bool = False,
    audit_actor_id: Optional[int] = None,
    audit_username: Optional[str] = None,
    audit_ip: Optional[str] = None,
) -> Tuple[Any, bool]:
    """验证并持久化源码包或离线模块包，返回记录及是否覆盖。"""
    extension = validate_archive_content(content, filename, package_kind)
    safe_name = Path(filename.replace("\\", "/")).name
    if package_kind == "source":
        normalized_version = (version or "").strip()
        if not normalized_version:
            match = _VERSION_FROM_NAME.search(safe_name)
            normalized_version = match.group(1) if match else ""
        if not normalized_version:
            raise ValueError("请输入版本号或选择具有 nginx-版本号 命名的文件")
        if len(normalized_version) > 50:
            raise ValueError("版本号不能超过 50 个字符")
        clean_name = (name or "").strip()
        if not clean_name or len(clean_name) > 100:
            raise ValueError("源码包名称必须为 1 到 100 个字符")
        model = NginxSourcePackage
        owner_conflict = session.scalar(
            select(model).where(
                model.name == clean_name,
                model.version == normalized_version,
                model.created_by == user_id,
            )
        )
        label = "源码包"
    else:
        normalized_version = (version or "").strip()
        clean_name = (name or "").strip()
        if not _MODULE_NAME.fullmatch(clean_name):
            raise ValueError("模块名只允许字母、数字、点、下划线、加号和连字符")
        if len(normalized_version) > 50:
            raise ValueError("模块版本不能超过 50 个字符")
        model = NginxModulePackage
        owner_conflict = session.scalar(
            select(model).where(
                model.name == clean_name,
                model.version == normalized_version,
                model.created_by == user_id,
            )
        )
        name = clean_name
        label = "模块包"

    duplicate = None
    digest = None
    # 源码包与模块包均先按名称和版本短路，再按 MD5 检查内容重复。
    if owner_conflict is None:
        digest = _package_digest(content)
        duplicate = session.scalar(select(model).where(model.file_md5 == digest))
    existing = owner_conflict or duplicate

    if existing and not overwrite:
        is_identity_conflict = owner_conflict is not None
        if is_identity_conflict:
            message = "已有同名同版本{}，是否覆盖？".format(label)
        else:
            duplicate_label = existing.name
            if existing.version:
                duplicate_label += "（版本 {}）".format(existing.version)
            message = "上传文件内容与已有{}“{}”相同，是否覆盖？".format(
                label, duplicate_label
            )
        raise PackageConflict(message, "version" if is_identity_conflict else "md5")
    if existing:
        active_tasks = _package_active_task_ids(session, existing.id, package_kind)
        if active_tasks:
            raise ValueError("该包正被执行中的升级任务使用，暂不能覆盖")

    if digest is None:
        digest = _package_digest(content)
    package_dir.mkdir(parents=True, exist_ok=True)
    storage_name = "{}-{}{}".format(package_kind, uuid.uuid4().hex, extension)
    stored_path = package_dir / storage_name
    stored_path.write_bytes(content)
    old_path = package_dir / existing.file_name if existing else None
    try:
        if audit_actor_id is not None:
            prepare_audit_session(
                session,
                actor_id=audit_actor_id,
                username=audit_username,
                ip=audit_ip,
            )
        if existing:
            existing.name = (name or "").strip()
            existing.version = normalized_version
            existing.file_name = storage_name
            existing.file_size = len(content)
            existing.file_md5 = digest
            existing.description = (description or "").strip()[:4000]
            existing.created_by = user_id
            existing.created_at = datetime.utcnow()
            if package_kind == "source":
                existing.is_official = bool(is_official)
            record = existing
        elif package_kind == "source":
            record = model(
                name=(name or "").strip(),
                version=normalized_version,
                file_name=storage_name,
                file_size=len(content),
                file_md5=digest,
                description=(description or "").strip()[:4000],
                is_official=bool(is_official),
                created_by=user_id,
            )
            session.add(record)
        else:
            record = model(
                name=name,
                version=normalized_version,
                file_name=storage_name,
                file_size=len(content),
                file_md5=digest,
                description=(description or "").strip()[:4000],
                created_by=user_id,
            )
            session.add(record)
        session.commit()
    except Exception:
        session.rollback()
        stored_path.unlink(missing_ok=True)
        raise
    if old_path and old_path != stored_path:
        old_path.unlink(missing_ok=True)
    return record, bool(existing)


def persist_uploaded_package(session_factory, package_dir: Path, **values):
    """在线程池中创建独立 Session 并保存上传包。"""
    with _BATCH_LOCK:
        with session_scope(session_factory) as session:
            return store_package(session, package_dir, **values)


def delete_package(
    session_factory,
    package_dir: Path,
    package_id: int,
    package_kind: str,
    *,
    audit_actor_id: Optional[int] = None,
    audit_username: Optional[str] = None,
    audit_ip: Optional[str] = None,
) -> bool:
    """串行检查活动任务并删除指定包记录和归档文件。"""
    model = NginxSourcePackage if package_kind == "source" else NginxModulePackage
    with _BATCH_LOCK:
        with session_scope(session_factory) as session:
            if audit_actor_id is not None:
                prepare_audit_session(
                    session,
                    actor_id=audit_actor_id,
                    username=audit_username,
                    ip=audit_ip,
                )
            package = session.get(model, package_id)
            if package is None:
                return False
            if _package_active_task_ids(session, package_id, package_kind):
                raise ValueError("该包正被执行中的升级或安装任务使用")
            file_path = package_dir / package.file_name
            session.delete(package)
            session.commit()
        file_path.unlink(missing_ok=True)
    return True


def parse_nginx_v_output(raw_output: str) -> Dict[str, Any]:
    """从 nginx -V 文本提取版本、prefix 和 configure 参数。"""
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
    version_match = _VERSION_FROM_OUTPUT.search(raw_output)
    if version_match:
        result["version"] = version_match.group(1)
    options_match = re.search(r"configure arguments:\s*(.+)", raw_output, re.DOTALL)
    if not options_match:
        return result
    configure_opts = options_match.group(1).strip()
    tokens = tokenize_configure_args(configure_opts)
    result["configure_opts"] = configure_opts
    result["params"] = tokens
    for token in tokens:
        if token.startswith(("--add-module=", "--add-dynamic-module=")):
            result["third_party_modules"].append(token)
        else:
            result["builtin_modules"].append(token)
            if token.startswith("--prefix="):
                result["prefix"] = token.split("=", 1)[1]
            elif token.startswith("--sbin-path="):
                result["binary_path"] = token.split("=", 1)[1]
    if not any(item.startswith("--sbin-path=") for item in tokens):
        result["binary_path"] = result["prefix"].rstrip("/") + "/sbin/nginx"
    return result


def tokenize_configure_args(value: str) -> List[str]:
    """按 shell 引号规则读取 configure 参数并丢弃非参数片段。"""
    flattened = re.sub(r"\\\s*\n\s*", " ", value or "")
    try:
        values = shlex.split(flattened, posix=True)
    except ValueError:
        values = []
    return [item for item in values if item.startswith("--")]


def join_configure_opts(tokens: Sequence[str], multiline: bool = True) -> str:
    """将参数安全格式化为 configure 命令展示文本。"""
    safe_tokens = [shlex.quote(item) for item in tokens if item]
    separator = " \\\n    " if multiline else " "
    return separator.join(safe_tokens)


def _format_nginx_v_log_lines(raw_output: str, parameters: Sequence[str]) -> List[str]:
    """保留 nginx -V 信息并将 configure 参数格式化为续行。"""
    lines = []
    replaced = False
    formatted_parameters = join_configure_opts(parameters).splitlines()
    for line in (raw_output or "").splitlines():
        if line.lstrip().startswith("configure arguments:"):
            indent = line[: len(line) - len(line.lstrip())]
            lines.append(indent + "configure arguments:")
            lines.extend(formatted_parameters)
            replaced = True
        else:
            lines.append(line)
    if not replaced:
        lines.append("configure arguments:")
        lines.extend(formatted_parameters)
    return lines


def resolve_module_path(item: dict, work_dir: str, index: int = 0) -> str:
    """为第三方模块生成工作目录内的安全路径。"""
    name = (item.get("name") or "module-{}".format(index)).strip()
    if not _MODULE_NAME.fullmatch(name):
        raise ValueError("第三方模块名称无效")
    return posixpath.join(work_dir.rstrip("/"), "nginx-modules", name)


def enrich_third_party_modules(items: Sequence[Any], work_dir: str) -> List[dict]:
    """补齐第三方模块的规范来源、路径和包标识。"""
    normalized = []
    for index, raw_item in enumerate(items):
        if not isinstance(raw_item, dict):
            raise ValueError("第三方模块参数格式无效")
        item = dict(raw_item)
        name = (item.get("name") or "").strip()
        if not _MODULE_NAME.fullmatch(name):
            raise ValueError("第三方模块名称无效")
        source = (item.get("source") or "git").strip().lower()
        if source == "git":
            git_url = (item.get("git_url") or "").strip()
            branch = (item.get("branch") or "master").strip()
            if not _VALID_GIT_URL.fullmatch(git_url):
                raise ValueError("第三方模块 Git 地址格式无效")
            if git_url.lower().startswith(("http://", "https://", "ssh://", "git://")):
                try:
                    parsed_url = urlsplit(git_url)
                except ValueError as exc:
                    raise ValueError("第三方模块 Git 地址格式无效") from exc
                if not parsed_url.hostname or parsed_url.password:
                    raise ValueError("第三方模块 Git 地址主机无效或包含密码")
                if git_url.lower().startswith(("http://", "https://")) and parsed_url.username:
                    raise ValueError("HTTP(S) Git 地址不能包含用户名或密码")
            if not _VALID_BRANCH.fullmatch(branch) or ".." in branch.split("/"):
                raise ValueError("第三方模块分支名格式无效")
            item.update({"source": "git", "git_url": git_url, "branch": branch})
        elif source == "package":
            try:
                item["package_id"] = int(item.get("package_id"))
            except (TypeError, ValueError) as exc:
                raise ValueError("请选择有效的第三方模块离线包") from exc
            item["source"] = source
        else:
            raise ValueError("第三方模块来源无效")
        item["module_path"] = resolve_module_path(item, work_dir, index)
        normalized.append(item)
    return normalized


def compute_target_configure_opts(
    current_params: Sequence[str],
    added_modules: Sequence[str],
    removed_modules: Sequence[str],
    third_party: Sequence[dict],
    work_dir: str,
) -> str:
    """按当前参数及模块增减生成目标 configure 参数。"""
    remaining = [item for item in current_params if item not in set(removed_modules)]
    existing = set(remaining)
    for module in added_modules:
        if module not in existing:
            remaining.append(module)
            existing.add(module)
    for item in enrich_third_party_modules(third_party, work_dir):
        token = "--add-module={}".format(item["module_path"])
        if token not in existing:
            remaining.append(token)
            existing.add(token)
    return join_configure_opts(remaining)


def effective_removed_modules(
    current_params: Sequence[str], requested_removals: Sequence[str]
) -> List[str]:
    """只保留当前节点编译基线中实际存在的待移除参数。"""
    current = set(current_params)
    return [item for item in requested_removals if item in current]


def fetch_node_nginx_v(session_factory, encryption_key: bytes, node_id: int) -> Dict[str, Any]:
    """在同步路由线程中连接节点并读取 nginx -V 基线。"""
    target = _load_node_target(session_factory, encryption_key, node_id)
    client = _connect_target(target)
    try:
        status, output = _run_remote_command(
            client,
            "{} -V 2>&1".format(shlex.quote(target["nginx_path"])),
            timeout=30,
        )
    finally:
        client.close()
    if status != 0:
        raise ValueError("执行 nginx -V 失败")
    parsed = parse_nginx_v_output(output)
    if not parsed["configure_opts"]:
        raise ValueError("无法解析 nginx -V 输出")
    parsed["runtime_binary_path"] = target["nginx_path"]
    return parsed


def _load_node_target(session_factory, encryption_key: bytes, node_id: int) -> dict:
    """读取节点与凭证快照并在会话关闭前解密凭证明文。"""
    with session_scope(session_factory) as session:
        node = session.scalars(
            select(Node)
            .options(joinedload(Node.credential))
            .where(Node.id == node_id, Node.is_deleted.is_(False))
        ).one_or_none()
        if node is None:
            raise ValueError("目标节点不存在或已删除")
        if node.is_locked:
            raise ValueError("节点已锁定")
        if node.status != "online":
            raise ValueError("节点 SSH 状态不是在线")
        if node.nginx_available is not True:
            raise ValueError("节点尚未检测到可用 Nginx")
        credential = node.credential
        if credential is None or not credential.is_enabled:
            raise ValueError("节点未配置有效的 SSH 凭证")
        try:
            secret = (
                credential.get_password(encryption_key)
                if credential.auth_type == "password"
                else credential.get_private_key(encryption_key)
            )
        except CredentialDecryptionError as exc:
            raise ValueError("节点 SSH 凭证解密失败") from exc
        return {
            "id": node.id,
            "hostname": node.hostname,
            "ip": node.ip,
            "port": node.port,
            "nginx_path": node.nginx_path or "/usr/sbin/nginx",
            "nginx_version": node.nginx_version,
            "credential_id": credential.id,
            "username": credential.username,
            "auth_type": credential.auth_type,
            "password": secret if credential.auth_type == "password" else "",
            "private_key": secret if credential.auth_type == "key" else "",
            "ssh_timeout": read_setting(session, "node.ssh_connect_timeout", 3),
            "detect_retries": read_setting(session, "node.detect_retries", 1),
        }


def _load_private_key(value: str) -> paramiko.PKey:
    """将凭证明文解析成 Paramiko 私钥对象。"""
    from io import StringIO

    last_error = None
    for key_type in (paramiko.RSAKey, paramiko.ECDSAKey, paramiko.Ed25519Key):
        try:
            return key_type.from_private_key(StringIO(value))
        except (paramiko.SSHException, TypeError, ValueError) as exc:
            last_error = exc
    raise ValueError("SSH 私钥无法解析") from last_error


def _connect_target(target: dict, context: Optional[TaskContext] = None) -> paramiko.SSHClient:
    """按系统超时和重试设置连接 SSH 节点。"""
    timeout = max(1, int(target.get("ssh_timeout", 3)))
    retry_count = min(max(int(target.get("detect_retries", 1)), 0), 10)
    pkey = None
    if target["auth_type"] != "password":
        pkey = _load_private_key(target["private_key"])
    options = {
        "hostname": target["ip"],
        "port": target["port"],
        "username": target["username"],
        "timeout": timeout,
        "banner_timeout": timeout,
        "auth_timeout": timeout,
        "look_for_keys": False,
        "allow_agent": False,
    }
    if target["auth_type"] == "password":
        options["password"] = target["password"]
    else:
        options["pkey"] = pkey
    last_error = None
    for attempt in range(retry_count + 1):
        if context is not None:
            context.check_cancelled()
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        if context is not None:
            context.register_cancel_callback(client.close)
        try:
            client.connect(**options)
            transport = client.get_transport()
            if transport is not None:
                transport.set_keepalive(30)
            return client
        except paramiko.AuthenticationException as exc:
            client.close()
            raise ValueError("SSH 认证失败") from exc
        except Exception as exc:
            client.close()
            last_error = exc
            if attempt >= retry_count:
                raise ValueError("SSH 连接失败") from exc
    raise ValueError("SSH 连接失败") from last_error


def _run_remote_command(client, command: str, timeout: int = 45) -> Tuple[int, str]:
    """执行一条远程命令并收集标准输出与错误输出。"""
    _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    output = stdout.read().decode("utf-8", "replace")
    error = stderr.read().decode("utf-8", "replace")
    return stdout.channel.recv_exit_status(), "\n".join(
        part for part in (output, error) if part
    ).strip()


def validate_remote_work_dir(value: str) -> str:
    """校验目标机绝对工作目录并折叠重复路径分隔。"""
    path = (value or "").strip()
    if not path.startswith("/") or "\x00" in path or "\n" in path or "\r" in path:
        raise ValueError("远程工作目录必须是有效的绝对路径")
    normalized = posixpath.normpath(path)
    if normalized == "/" or normalized.startswith("/../"):
        raise ValueError("远程工作目录无效")
    return normalized


def validate_target_prefix(value: str) -> str:
    """校验切换路径模式使用的目标安装目录。"""
    path = (value or "").strip()
    if not path.startswith("/") or "\x00" in path or "\n" in path or "\r" in path:
        raise ValueError("目标安装目录必须是有效的绝对路径")
    normalized = posixpath.normpath(path)
    if normalized == "/":
        raise ValueError("目标安装目录不能是根目录")
    return normalized


def rewrite_configure_prefix(tokens: Sequence[str], prefix: str) -> List[str]:
    """将 configure 参数中的安装 prefix 与显式二进制路径指向目标目录。"""
    prefix = validate_target_prefix(prefix)
    rewritten = []
    found = False
    for token in tokens:
        if token.startswith("--prefix="):
            rewritten.append("--prefix={}".format(prefix))
            found = True
        elif token.startswith("--sbin-path="):
            rewritten.append(
                "--sbin-path={}".format(posixpath.join(prefix, "sbin", "nginx"))
            )
        else:
            rewritten.append(token)
    if not found:
        rewritten.insert(0, "--prefix={}".format(prefix))
    return rewritten


def create_upgrade_runner(session_factory, encryption_key: bytes, package_dir: Path, task_id: int):
    """构造绑定任务 ID 的统一执行器回调。"""
    def run(context: TaskContext):
        """执行单节点升级流水线并返回脱敏结果。"""
        context.set_detail_log_sink(
            lambda messages, level: _append_upgrade_execution_logs(
                session_factory, task_id, messages, level
            )
        )
        context.append_detail_log("任务开始执行")
        return _run_upgrade(session_factory, encryption_key, package_dir, task_id, context)

    return run


def _set_run_phase(
    session_factory,
    task_id: int,
    context: TaskContext,
    phase: str,
    progress: int,
    detail: str,
    log_detail: bool = True,
) -> None:
    """同时更新升级阶段、统一任务进度和可读步骤。"""
    with session_scope(session_factory) as session:
        with session.begin():
            session.execute(
                update(NginxUpgradeRun)
                .where(NginxUpgradeRun.task_id == task_id)
                .values(phase=phase)
            )
    context.update_progress(progress, detail=detail)
    context.append_log(detail)
    if log_detail:
        context.append_detail_log(detail)


def _task_center_failure_summary(message: str) -> str:
    """从升级异常中移除命令输出，仅保留任务中心可读摘要。"""
    if message.startswith("Nginx reload/start 失败"):
        return "Nginx reload/start 失败"
    lines = message.splitlines()
    return (lines[0] if lines else "Nginx 升级失败")[:500]


def _append_upgrade_execution_logs(
    session_factory, task_id: int, messages: Sequence[str], level: str
) -> bool:
    """将脱敏后的升级执行日志按行追加到专属日志表。"""
    if not messages:
        return False
    with session_scope(session_factory) as session:
        with session.begin():
            task_exists = session.scalar(select(Task.id).where(Task.id == task_id))
            if task_exists is None:
                return False
            session.add_all(
                NginxUpgradeLog(task_id=task_id, level=level, message=message)
                for message in messages
            )
            session.execute(
                update(Task)
                .where(Task.id == task_id)
                .values(updated_at=datetime.utcnow())
            )
    return True


class _UpgradeDetailedLogContext:
    """将共享 Nginx reload 服务的日志转入升级完整日志。"""

    def __init__(self, context: TaskContext) -> None:
        """绑定升级任务的完整日志上下文。"""
        self._context = context

    def append_log(self, message: str, level: str = "info") -> bool:
        """把共享服务日志转发为升级专属日志。"""
        raw_output_prefixes = ("远程命令输出：", "远程错误输出：")
        for prefix in raw_output_prefixes:
            if message.startswith(prefix):
                message = message[len(prefix):]
                break
        return self._context.append_detail_log(message, level)


def _run_upgrade(session_factory, encryption_key: bytes, package_dir: Path, task_id: int, context: TaskContext):
    """在后台线程执行节点升级、验证、必要时自动回滚。"""
    client = None
    lifecycle_details = {"listen_ports": "", "startup_mode": ""}
    try:
        run, target, package = _load_run_snapshot(
            session_factory, encryption_key, package_dir, task_id
        )
        lifecycle_details.update({"hostname": run.node_hostname, "ip": run.node_ip})
        context.check_cancelled()
        _set_run_phase(session_factory, task_id, context, "fetching_config", 5, "正在获取 Nginx 编译参数")
        client = _connect_target(target, context)
        status, raw_output = _run_remote_command(
            client,
            "{} -V 2>&1".format(shlex.quote(target["nginx_path"])),
            timeout=30,
            context=context,
            log_output=False,
        )
        parsed = parse_nginx_v_output(raw_output)
        if status != 0 or not parsed["configure_opts"]:
            raise UpgradeTaskError("读取当前 Nginx 编译参数失败")
        lifecycle_details["listen_ports"] = _read_nginx_listen_ports(
            client, target["nginx_path"], context
        )
        if run.current_configure_opts and set(parsed["params"]) != set(
            tokenize_configure_args(run.current_configure_opts)
        ):
            raise UpgradeTaskError("节点编译参数已变化，请重新读取 nginx -V 后再提交")
        context.append_detail_logs(
            _format_nginx_v_log_lines(raw_output, parsed["params"])
        )
        _update_run_current(session_factory, task_id, parsed)

        _set_run_phase(
            session_factory,
            task_id,
            context,
            "fetching_config",
            10,
            "检查 gcc 与 make",
            log_detail=False,
        )
        status, output = _run_remote_command(
            client,
            "command -v gcc >/dev/null 2>&1 && command -v make >/dev/null 2>&1",
            context=context,
        )
        if status != 0:
            raise UpgradeTaskError("目标节点缺少 gcc 或 make 编译工具")
        context.append_detail_log("检查 gcc 与 make，检查通过")

        work_dir = run.remote_work_dir
        package_remote = posixpath.join(work_dir, package["file_name"])
        _set_run_phase(
            session_factory,
            task_id,
            context,
            "uploading_package",
            15,
            "创建远程编译工作目录",
            log_detail=False,
        )
        _require_remote_success(
            client,
            "mkdir -p -- {}".format(shlex.quote(work_dir)),
            "创建远程工作目录失败",
            context=context,
        )
        context.append_detail_log(
            "创建远程编译工作目录 {}，创建成功".format(work_dir)
        )

        _set_run_phase(
            session_factory,
            task_id,
            context,
            "uploading_package",
            20,
            "上传 Nginx 源码包",
            log_detail=False,
        )
        _upload_file(client, package["local_path"], package_remote, context, 20, 29)
        _verify_remote_md5(
            client, package_remote, package["file_md5"], "源码包", context=context
        )

        _set_run_phase(
            session_factory,
            task_id,
            context,
            "uploading_package",
            30,
            "解压 Nginx 源码包",
            log_detail=False,
        )
        _require_remote_success(
            client,
            "tar -xzf {} -C {} --no-same-owner".format(
                shlex.quote(package_remote), shlex.quote(work_dir)
            ),
            "解压 Nginx 源码包失败",
            context=context,
        )
        source_root = _read_archive_root(
            client, package_remote, context=context, log_output=False
        )
        context.append_detail_log(
            "上传并解压 Nginx 源码包 {}，操作成功，目录 {}".format(
                package["file_name"], source_root
            )
        )
        source_dir = posixpath.join(work_dir, source_root)

        third_party = json.loads(run.third_party_json or "[]")
        if third_party:
            _set_run_phase(
                session_factory,
                task_id,
                context,
                "downloading_modules",
                40,
                "准备第三方模块",
                log_detail=False,
            )
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
            module_names = "、".join(item["name"] for item in third_party)
            context.append_detail_log(
                "准备带三方模块 {}，准备成功".format(module_names)
            )
        else:
            context.update_progress(40, detail="没有第三方模块需要准备")

        context.check_cancelled()
        backup_path = "{}.old.{}.{}".format(
            run.current_binary_path,
            datetime.utcnow().strftime("%Y%m%d%H%M%S"),
            task_id,
        )
        _set_run_phase(
            session_factory,
            task_id,
            context,
            "backing_up",
            50,
            "备份当前 Nginx 二进制",
            log_detail=False,
        )
        context.append_detail_log(
            "备份当前 Nginx 二进制到 {}".format(backup_path)
        )
        _require_remote_success(
            client,
            "cp -p -- {} {}".format(
                shlex.quote(run.current_binary_path), shlex.quote(backup_path)
            ),
            "备份当前 Nginx 二进制失败",
            context=context,
        )
        tokens = tokenize_configure_args(run.target_configure_opts)
        if not tokens:
            raise UpgradeTaskError("目标 configure 参数为空")
        configure = join_configure_opts(tokens, multiline=False)
        _set_run_phase(
            session_factory,
            task_id,
            context,
            "configuring",
            55,
            "执行 ./configure",
            log_detail=False,
        )
        context.append_detail_logs(
            ["执行 ./configure，编译参数："] + join_configure_opts(tokens).splitlines()
        )
        status, _output = _run_remote_command(
            client,
            "cd -- {} && ./configure {} 2>&1".format(
                shlex.quote(source_dir), configure
            ),
            timeout=1800,
            context=context,
        )
        if status != 0:
            raise UpgradeTaskError("configure 失败")

        context.check_cancelled()
        _set_run_phase(session_factory, task_id, context, "compiling", 65, "执行 make -j{}".format(run.make_jobs))
        status, _output = _run_remote_command(
            client,
            "cd -- {} && make -j{} 2>&1".format(
                shlex.quote(source_dir), run.make_jobs
            ),
            timeout=7200,
            context=context,
        )
        if status != 0:
            raise UpgradeTaskError("make 编译失败")

        context.check_cancelled()
        _set_run_phase(session_factory, task_id, context, "replacing_binary", 80, "执行 make install")
        status, _output = _run_remote_command(
            client,
            "cd -- {} && make install 2>&1".format(shlex.quote(source_dir)),
            timeout=1800,
            context=context,
        )
        if status != 0:
            raise UpgradeTaskError("make install 失败")

        target_binary = run.current_binary_path
        if run.upgrade_mode == "switch_path":
            target_binary = posixpath.join(run.target_prefix, "sbin", "nginx")
        _set_run_phase(session_factory, task_id, context, "verifying", 85, "执行 nginx -t")
        status, _output = _run_remote_command(
            client,
            "{} -t 2>&1".format(shlex.quote(target_binary)),
            timeout=120,
            context=context,
        )
        if status != 0:
            _restore_binary(client, backup_path, run.current_binary_path, context)
            raise UpgradeTaskError("nginx -t 校验失败，已尝试恢复旧二进制")

        _set_run_phase(session_factory, task_id, context, "upgrading", 90, "按节点启动方式 reload Nginx")
        reload_ok, reload_message = _reload_nginx(
            client,
            target_binary,
            context=_UpgradeDetailedLogContext(context),
        )
        lifecycle_details["startup_mode"] = (
            "systemctl"
            if "systemd" in reload_message.lower()
            else ("二进制" if reload_ok else "未确认")
        )
        context.append_log(
            "Nginx reload/start 成功" if reload_ok else "Nginx reload/start 失败",
            level="info" if reload_ok else "error",
        )
        if not reload_ok:
            _restore_binary(client, backup_path, run.current_binary_path, context)
            raise UpgradeTaskError("Nginx reload/start 失败")

        _set_run_phase(session_factory, task_id, context, "verifying", 95, "读取升级后的 Nginx 版本")
        status, output = _run_remote_command(
            client,
            "{} -v 2>&1".format(shlex.quote(target_binary)),
            timeout=30,
            context=context,
        )
        version_match = _VERSION_FROM_OUTPUT.search(output)
        final_version = version_match.group(1) if version_match else run.target_version
        _update_node_nginx(session_factory, target["id"], final_version, target_binary)
        _set_run_phase(session_factory, task_id, context, "success", 99, "Nginx 编译升级完成")
        context.set_result_tree(
            {"summary": {"total": 1, "success": 1, "failed": 0}, "node": {
                "hostname": target["hostname"], "ip": target["ip"],
                "current_version": parsed["version"], "target_version": final_version,
                "listen_ports": lifecycle_details["listen_ports"],
                "startup_mode": lifecycle_details["startup_mode"],
            }}
        )
        context.append_detail_log("Nginx 编译升级成功")
        return TaskOutcome("success", "Nginx 编译升级成功", None)
    except Exception as exc:
        if isinstance(exc, TaskCancelled):
            context.append_detail_log("任务已取消", level="warning")
            raise
        log_exception(
            logger,
            "Nginx 升级任务异常",
            exc,
            "task_id={}".format(task_id),
        )
        if isinstance(exc, UpgradeTaskError):
            message = str(exc)
        elif isinstance(exc, ValueError):
            message = str(exc)
        else:
            message = "Nginx 升级失败（{}）".format(type(exc).__name__)
        summary = _task_center_failure_summary(message)
        context.append_log(summary, level="error")
        context.append_detail_log(message, level="error")
        context.set_result_tree(
            {
                "summary": {"total": 1, "success": 0, "failed": 1},
                "error": summary,
                "node": lifecycle_details,
            }
        )
        return TaskOutcome("failed", summary[:2000], None)
    finally:
        if client is not None:
            client.close()


def _load_run_snapshot(session_factory, encryption_key: bytes, package_dir: Path, task_id: int):
    """加载任务执行参数、节点认证材料和本地包路径快照。"""
    with session_scope(session_factory) as session:
        run = session.scalars(
            select(NginxUpgradeRun).where(NginxUpgradeRun.task_id == task_id)
        ).one_or_none()
        if run is None or run.upgrade_mode == "rollback":
            raise UpgradeTaskError("升级任务不存在")
        node = session.scalars(
            select(Node).options(joinedload(Node.credential)).where(
                Node.id == run.node_id, Node.is_deleted.is_(False)
            )
        ).one_or_none()
        if node is None:
            raise UpgradeTaskError("目标节点不存在或已删除")
        if node.is_locked or node.status != "online" or node.nginx_available is not True:
            raise UpgradeTaskError("节点状态已变化，不满足在线 Nginx 升级门禁")
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
        package_path = package_dir / package.file_name
        if not package_path.is_file():
            raise UpgradeTaskError("Nginx 源码包文件缺失")
        if _file_md5(package_path) != package.file_md5:
            raise UpgradeTaskError("Nginx 源码包校验值已变化")
        run_snapshot = _UpgradeRunSnapshot(
            node_hostname=run.node_hostname,
            node_ip=run.node_ip,
            remote_work_dir=run.remote_work_dir,
            current_configure_opts=run.current_configure_opts,
            third_party_json=run.third_party_json,
            current_binary_path=run.current_binary_path,
            target_configure_opts=run.target_configure_opts,
            make_jobs=run.make_jobs,
            upgrade_mode=run.upgrade_mode,
            target_prefix=run.target_prefix,
            target_version=run.target_version,
        )
        return run_snapshot, {
            "id": node.id,
            "hostname": node.hostname,
            "ip": node.ip,
            "port": node.port,
            "nginx_path": node.nginx_path or "/usr/sbin/nginx",
            "nginx_version": node.nginx_version,
            "credential_id": credential.id,
            "username": credential.username,
            "auth_type": credential.auth_type,
            "password": secret if credential.auth_type == "password" else "",
            "private_key": secret if credential.auth_type == "key" else "",
            "ssh_timeout": read_setting(session, "node.ssh_connect_timeout", 3),
            "detect_retries": read_setting(session, "node.detect_retries", 1),
        }, {
            "file_name": package.file_name,
            "file_md5": package.file_md5,
            "local_path": str(package_path),
        }


def _file_md5(path: Path) -> str:
    """按固定块读取文件并计算 MD5。"""
    digest = hashlib.md5()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _update_run_current(session_factory, task_id: int, parsed: dict) -> None:
    """持久化工作线程实际读取的 Nginx 参数基线。"""
    with session_scope(session_factory) as session:
        with session.begin():
            session.execute(
                update(NginxUpgradeRun)
                .where(NginxUpgradeRun.task_id == task_id)
                .values(
                    current_version=parsed["version"],
                    current_configure_opts=parsed["configure_opts"],
                    current_prefix=parsed["prefix"],
                    current_binary_path=parsed["binary_path"],
                )
            )


def _require_remote_success(
    client, command: str, message: str, context: Optional[TaskContext] = None
) -> str:
    """执行一次远程命令并将远端正文限制为通用错误。"""
    status, output = _run_remote_command(client, command, context=context)
    if status != 0:
        raise UpgradeTaskError(message)
    return output


def _upload_file(client, local_path: str, remote_path: str, context: TaskContext, start: int, end: int) -> None:
    """通过 SFTP 上传文件并更新真实字节进度。"""
    file_size = os.path.getsize(local_path)
    sftp = client.open_sftp()
    context.register_cancel_callback(sftp.close)
    try:
        def progress(transferred: int, total: int) -> None:
            """将 SFTP 传输回调映射为升级任务百分比。"""
            if total:
                ratio = min(1.0, transferred / total)
                context.update_progress(start + int((end - start) * ratio))

        sftp.put(local_path, remote_path, callback=progress)
    finally:
        sftp.close()
    if file_size <= 0:
        raise UpgradeTaskError("上传文件为空")


def _verify_remote_md5(
    client,
    remote_path: str,
    expected: str,
    label: str,
    context: Optional[TaskContext] = None,
) -> None:
    """比较本地和远端归档 MD5，拒绝不完整传输。"""
    status, output = _run_remote_command(
        client,
        "md5sum -- {} | awk '{{print $1}}'".format(shlex.quote(remote_path)),
        context=context,
        log_output=False,
    )
    actual = output.splitlines()[0].strip().lower() if output else ""
    if status != 0 or actual != expected:
        raise UpgradeTaskError("{} MD5 校验失败".format(label))


def _read_archive_root(
    client,
    archive_path: str,
    context: Optional[TaskContext] = None,
    log_output: bool = True,
) -> str:
    """读取源码包顶层目录并验证其为单一安全名称。"""
    _status, output = _run_remote_command(
        client,
        "tar -tzf {} | awk -F/ 'NF {{print $1; exit}}'".format(
            shlex.quote(archive_path)
        ),
        context=context,
        log_output=log_output,
    )
    root = output.strip().splitlines()[0] if output.strip() else ""
    if not _MODULE_NAME.fullmatch(root) or root in (".", ".."):
        raise UpgradeTaskError("无法确定源码包顶层目录")
    return root


def _prepare_module(client, item: dict, index: int, work_dir: str, package_dir: Path, session_factory, context: TaskContext) -> None:
    """在目标节点准备在线 Git 或平台托管的离线模块。"""
    module_path = resolve_module_path(item, work_dir, index)
    modules_dir = posixpath.dirname(module_path)
    _require_remote_success(
        client,
        "mkdir -p -- {}".format(shlex.quote(modules_dir)),
        "创建模块目录失败",
        context=context,
    )
    if item["source"] == "git":
        setting_key = "upgrade.git_clone_timeout_minutes"
        default_minutes = int(preset_by_key()[setting_key]["value"])
        with session_scope(session_factory) as session:
            timeout_minutes = read_setting(session, setting_key, default_minutes)
        git_url = item["git_url"]
        branch = item["branch"]
        if not _remote_is_dir(client, module_path, context=context):
            command = "git clone --depth 1 --branch {} -- {} {} 2>&1".format(
                shlex.quote(branch), shlex.quote(git_url), shlex.quote(module_path)
            )
        else:
            command = "git -C {} fetch --depth 1 origin {} && git -C {} checkout -f FETCH_HEAD".format(
                shlex.quote(module_path), shlex.quote(branch), shlex.quote(module_path)
            )
        status, output = _run_remote_command(
            client,
            command,
            timeout=timeout_minutes * 60,
            context=context,
            log_output=False,
        )
        if status != 0:
            raise UpgradeTaskError(
                "第三方模块 {} Git 获取失败；目标机可能无 Git 或无法出网，请改用离线包".format(
                    item["name"]
                )
            )
        return

    with session_scope(session_factory) as session:
        module_package = session.get(NginxModulePackage, item["package_id"])
        if module_package is None:
            raise UpgradeTaskError("第三方模块 {} 离线包不存在".format(item["name"]))
        local_path = package_dir / module_package.file_name
        expected_md5 = module_package.file_md5
        archive_name = module_package.file_name
    if not local_path.is_file() or _file_md5(local_path) != expected_md5:
        raise UpgradeTaskError("第三方模块 {} 离线包缺失或校验失败".format(item["name"]))

    remote_archive = posixpath.join(modules_dir, archive_name)
    temporary_dir = "{}.extract.{}".format(module_path, context.task_id)
    _upload_file(client, str(local_path), remote_archive, context, 40, 44)
    _verify_remote_md5(
        client, remote_archive, expected_md5, "第三方模块包", context=context
    )
    _require_remote_success(
        client,
        "rm -rf -- {} {} && mkdir -p -- {}".format(
            shlex.quote(module_path), shlex.quote(temporary_dir), shlex.quote(temporary_dir)
        ),
        "准备第三方模块解压目录失败",
        context=context,
    )
    extract = (
        "unzip -q {} -d {}".format(shlex.quote(remote_archive), shlex.quote(temporary_dir))
        if archive_name.lower().endswith(".zip")
        else "tar -xzf {} -C {} --no-same-owner".format(
            shlex.quote(remote_archive), shlex.quote(temporary_dir)
        )
    )
    _require_remote_success(
        client,
        extract,
        "第三方模块 {} 解压失败".format(item["name"]),
        context=context,
    )
    normalize = (
        "count=$(find {tmp} -mindepth 1 -maxdepth 1 | wc -l); "
        "first=$(find {tmp} -mindepth 1 -maxdepth 1 -type d -print -quit); "
        "if [ \"$count\" = 1 ] && [ -n \"$first\" ]; then mv -- \"$first\" {dst}; "
        "else mv -- {tmp} {dst}; fi; rm -f -- {archive}"
    ).format(
        tmp=shlex.quote(temporary_dir), dst=shlex.quote(module_path), archive=shlex.quote(remote_archive)
    )
    _require_remote_success(
        client,
        normalize,
        "整理第三方模块 {} 目录失败".format(item["name"]),
        context=context,
    )


def _remote_is_dir(
    client, path: str, context: Optional[TaskContext] = None
) -> bool:
    """检查远程模块目录是否存在。"""
    status, _output = _run_remote_command(
        client, "test -d {}".format(shlex.quote(path)), context=context
    )
    return status == 0


def _run_remote_command(
    client,
    command: str,
    timeout: int = 45,
    context: Optional[TaskContext] = None,
    log_output: bool = True,
) -> Tuple[int, str]:
    """运行远程命令并实时持久化完整输出，同时保留有限尾部供错误摘要使用。"""
    _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    channel = stdout.channel
    output_tail = deque(maxlen=120)
    pending = ""
    log_lines = []
    log_chars = 0
    started = time.monotonic()

    def flush_logs(include_partial: bool = False) -> None:
        """将尚未达到批量阈值的远程输出追加到任务日志。"""
        nonlocal pending, log_chars
        if include_partial and pending:
            if log_output:
                log_lines.append(pending.rstrip("\r"))
            pending = ""
        if log_output and log_lines:
            _append_command_output(context, "\n".join(log_lines))
            log_lines.clear()
            log_chars = 0

    while True:
        if context is not None:
            try:
                context.check_cancelled()
            except TaskCancelled:
                flush_logs(include_partial=True)
                raise
        chunk = b""
        if channel.recv_ready():
            chunk += channel.recv(65536)
        if channel.recv_stderr_ready():
            chunk += channel.recv_stderr(65536)
        if chunk:
            pending += chunk.decode("utf-8", "replace")
            lines = pending.split("\n")
            pending = lines.pop()
            for line in lines:
                line = line.rstrip("\r")
                output_tail.append(line)
                if log_output:
                    log_lines.append(line)
                    log_chars += len(line) + 1
                    if len(log_lines) >= 20 or log_chars >= 3000:
                        _append_command_output(context, "\n".join(log_lines))
                        log_lines = []
                        log_chars = 0
        if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
            break
        if time.monotonic() - started > timeout:
            flush_logs(include_partial=True)
            channel.close()
            raise UpgradeTaskError("远程命令执行超时")
        if not chunk:
            time.sleep(0.1)
    if pending:
        pending = pending.rstrip("\r")
        output_tail.append(pending)
        if log_output:
            log_lines.append(pending)
        pending = ""
    flush_logs()
    return channel.recv_exit_status(), "\n".join(output_tail)


def _append_command_output(context: Optional[TaskContext], output: str) -> None:
    """将远程命令输出按行写入完整日志或兼容任务摘要日志。"""
    if context is None or output is None:
        return
    lines = output.split("\n")
    if context.has_detail_log_sink():
        context.append_detail_logs(lines)
        return
    batch = []
    batch_chars = 0
    for line in lines:
        if len(line) > 3500:
            if batch:
                context.append_log("\n".join(batch))
                batch = []
                batch_chars = 0
            for offset in range(0, len(line), 3500):
                context.append_log(line[offset:offset + 3500])
            continue
        if batch and batch_chars + len(line) + 1 > 3500:
            context.append_log("\n".join(batch))
            batch = []
            batch_chars = 0
        batch.append(line)
        batch_chars += len(line) + 1
    if batch:
        context.append_log("\n".join(batch))


def _tail_output(output: str, max_lines: int = 80) -> str:
    """为全新安装等共享调用方保留命令错误输出末尾行。"""
    return "\n".join((output or "").strip().splitlines()[-max_lines:])


def _read_nginx_listen_ports(client, binary_path: str, context: TaskContext) -> str:
    """读取 Nginx 生效配置中的监听端口且不保留配置正文。"""
    command = "{} -T 2>&1 | awk '/^[[:space:]]*listen[[:space:]]+/ {{ print }}'".format(
        shlex.quote(binary_path or "nginx")
    )
    try:
        status, output = _run_remote_command(
            client, command, timeout=90, context=context, log_output=False
        )
    except TaskCancelled:
        raise
    except Exception:
        return "未能读取"
    if status != 0:
        return "未能读取"
    ports = set()
    for line in (output or "").splitlines():
        match = re.match(r"^\s*listen\s+([^;]+);", line)
        if not match:
            continue
        tokens = match.group(1).split()
        if not tokens:
            continue
        endpoint = tokens[0].strip("'\"")
        port = endpoint.rsplit(":", 1)[-1] if ":" in endpoint else endpoint
        if port.isdigit() and 1 <= int(port) <= 65535:
            ports.add(int(port))
    return ", ".join(str(port) for port in sorted(ports)) or "未识别"


def _restore_binary(client, backup_path: str, binary_path: str, context: TaskContext) -> None:
    """从升级前备份尝试恢复二进制并记录恢复结果。"""
    status, _output = _run_remote_command(
        client,
        "cp -p -- {} {}".format(shlex.quote(backup_path), shlex.quote(binary_path)),
        timeout=120,
        context=context,
    )
    message = "已恢复旧 Nginx 二进制" if status == 0 else "恢复旧 Nginx 二进制失败"
    level = "info" if status == 0 else "error"
    context.append_log(message, level=level)
    context.append_detail_log(message, level=level)


def _update_node_nginx(session_factory, node_id: int, version: str, binary_path: str) -> None:
    """升级成功后更新节点 Nginx 版本、路径和探测状态。"""
    with session_scope(session_factory) as session:
        with session.begin():
            node = session.get(Node, node_id)
            if node is None or node.is_deleted:
                return
            node.nginx_version = version[:50]
            node.nginx_path = binary_path[:255]
            node.nginx_available = True
            node.last_nginx_probe_at = datetime.utcnow()


def create_upgrade_batch(
    session_factory,
    executor,
    encryption_key: bytes,
    package_dir: Path,
    user_id: int,
    payload: dict,
    batch_limit: int = 3,
    trigger_ip: str = "",
) -> dict:
    """全量校验并创建同一批次的逐节点升级任务。"""
    try:
        node_ids = [int(value) for value in payload.get("node_ids", [])]
        package_id = int(payload.get("source_package") or 0)
        mode = (payload.get("upgrade_mode") or "upgrade").strip()
        work_dir = validate_remote_work_dir(
            payload.get("remote_work_dir") or "/tmp/nginx-upgrade"
        )
        make_jobs = int(payload.get("make_jobs") or 4)
        target_version = (payload.get("target_version") or "").strip()
        target_prefix = (payload.get("target_prefix") or "").strip()
        added_modules = payload.get("added_modules") or []
        removed_modules = payload.get("removed_modules") or []
        third_party_raw = payload.get("added_third_party") or []
        node_payloads = payload.get("nodes_payload") or []
    except (TypeError, ValueError, AttributeError):
        raise ValueError("请求参数格式错误")
    if not node_ids or len(set(node_ids)) != len(node_ids):
        raise ValueError("请至少选择一个不重复的节点")
    if len(node_ids) > batch_limit:
        raise ValueError("最多只能勾选 {} 个节点".format(batch_limit))
    if mode not in ("upgrade", "switch_path"):
        raise ValueError("升级模式无效；全新安装请使用 Nginx 安装向导")
    if not 1 <= make_jobs <= 32:
        raise ValueError("并行编译数必须在 1 到 32 之间")
    if not isinstance(added_modules, list) or not all(
        isinstance(item, str) and item in BUILTIN_ADD_MODULES for item in added_modules
    ):
        raise ValueError("新增的内置模块参数无效")
    if not isinstance(removed_modules, list) or not all(
        isinstance(item, str) and item.startswith("--") for item in removed_modules
    ):
        raise ValueError("移除的 configure 参数格式无效")
    if mode == "switch_path":
        target_prefix = validate_target_prefix(target_prefix)
    try:
        third_party = enrich_third_party_modules(third_party_raw, work_dir)
    except (TypeError, ValueError) as exc:
        raise ValueError(str(exc)) from exc
    if len({item["name"] for item in third_party}) != len(third_party):
        raise ValueError("第三方模块名称不能重复")

    payload_map = {}
    if not isinstance(node_payloads, list):
        raise ValueError("节点编译参数格式无效")
    for item in node_payloads:
        if not isinstance(item, dict):
            continue
        try:
            payload_map[int(item.get("node_id"))] = item
        except (TypeError, ValueError):
            continue

    with _BATCH_LOCK:
        with session_scope(session_factory) as session:
            prepare_audit_session(session, actor_id=user_id, ip=trigger_ip)
            package = session.get(NginxSourcePackage, package_id)
            if package is None:
                raise ValueError("源码包不存在")
            if not target_version:
                target_version = package.version
            if len(target_version) > 50:
                raise ValueError("目标版本不能超过 50 个字符")
            package_path = package_dir / package.file_name
            if not package_path.is_file():
                logger.warning(
                    "upgrade source package unavailable package_id=%s reason=missing",
                    package.id,
                )
                raise ValueError("源码包文件缺失，请重新上传该源码包")
            if _file_md5(package_path) != package.file_md5:
                logger.warning(
                    "upgrade source package unavailable package_id=%s reason=checksum_mismatch",
                    package.id,
                )
                raise ValueError("源码包校验值已变化，请重新上传该源码包")

            nodes = session.scalars(
                select(Node)
                .options(joinedload(Node.credential))
                .where(Node.id.in_(node_ids), Node.is_deleted.is_(False))
                .order_by(Node.id.asc())
            ).unique().all()
            node_map = {node.id: node for node in nodes}
            if set(node_map) != set(node_ids):
                raise ValueError("部分节点不存在或已删除")
            module_packages = {}
            for item in third_party:
                if item["source"] == "package":
                    module_package = session.get(NginxModulePackage, item["package_id"])
                    if module_package is None:
                        raise ValueError("第三方模块离线包不存在")
                    module_path = package_dir / module_package.file_name
                    if not module_path.is_file() or _file_md5(module_path) != module_package.file_md5:
                        raise ValueError("第三方模块离线包文件缺失或校验值已变化")
                    module_packages[item["package_id"]] = module_package

            normalized_items = {}
            for node_id in node_ids:
                node = node_map[node_id]
                if node.is_locked:
                    raise ValueError("节点 {} 已锁定".format(node.hostname))
                if node.status != "online":
                    raise ValueError("节点 {} SSH 状态不是在线".format(node.hostname))
                if node.nginx_available is not True:
                    raise ValueError("节点 {} 尚未检测到可用 Nginx".format(node.hostname))
                if node.credential is None or not node.credential.is_enabled:
                    raise ValueError("节点 {} 未配置有效凭证".format(node.hostname))
                item = payload_map.get(node_id)
                if item is None:
                    raise ValueError("缺少节点 {} 的 nginx -V 编译参数".format(node.hostname))
                params = item.get("params") or []
                if not isinstance(params, list) or not all(
                    isinstance(value, str) and value.startswith("--") for value in params
                ):
                    raise ValueError("节点 {} 的 configure 参数格式无效".format(node.hostname))
                current_opts = (item.get("current_configure_opts") or "").strip()
                parsed_params = tokenize_configure_args(current_opts)
                if not parsed_params or set(parsed_params) != set(params):
                    raise ValueError("节点 {} 的编译参数基线无效，请重新读取 nginx -V".format(node.hostname))
                node_removed_modules = effective_removed_modules(params, removed_modules)
                prefix = (item.get("prefix") or "").strip() or "/usr/local/nginx"
                binary_path = (item.get("binary_path") or "").strip()
                if not binary_path.startswith("/") or "\x00" in binary_path:
                    raise ValueError("节点 {} 的 nginx 二进制路径无效".format(node.hostname))
                per_node_prefix = target_prefix if mode == "switch_path" else prefix
                if mode == "switch_path":
                    per_node_tokens = rewrite_configure_prefix(
                        tokenize_configure_args(
                            compute_target_configure_opts(
                                params, added_modules, node_removed_modules, third_party, work_dir
                            )
                        ),
                        target_prefix,
                    )
                    target_opts = join_configure_opts(per_node_tokens)
                else:
                    target_opts = compute_target_configure_opts(
                        params, added_modules, node_removed_modules, third_party, work_dir
                    )
                normalized_items[node_id] = {
                    "node": node,
                    "current_version": str(item.get("current_version") or node.nginx_version)[:50],
                    "current_opts": current_opts,
                    "prefix": prefix[:500],
                    "binary_path": binary_path[:500],
                    "target_prefix": per_node_prefix[:500],
                    "target_opts": target_opts,
                    "removed_modules": node_removed_modules,
                }

            now = datetime.utcnow()
            day_prefix = "UG-{}-".format(now.strftime("%y%m%d"))
            latest = session.scalar(
                select(NginxUpgradeRun.batch_number)
                .where(NginxUpgradeRun.batch_number.like(day_prefix + "%"))
                .order_by(NginxUpgradeRun.batch_number.desc())
                .limit(1)
            )
            sequence = int(latest[-4:]) + 1 if latest else 1
            batch_number = "{}{:04d}".format(day_prefix, sequence)
            task_ids = []
            for node_id in node_ids:
                item = normalized_items[node_id]
                node = item["node"]
                task = Task(
                    operation_type="nginx_upgrade",
                    detail="Nginx {} 升级任务已加入队列".format(target_version),
                    source_batch=batch_number,
                    target_hostnames=node.hostname,
                    target_ips=node.ip,
                    trigger_user_id=user_id,
                )
                session.add(task)
                session.flush()
                run = NginxUpgradeRun(
                    task_id=task.id,
                    node_id=node.id,
                    source_package_id=package.id,
                    batch_number=batch_number,
                    node_hostname=node.hostname,
                    node_ip=node.ip,
                    source_package_name=package.name,
                    target_version=target_version,
                    upgrade_mode=mode,
                    remote_work_dir=work_dir,
                    make_jobs=make_jobs,
                    current_version=item["current_version"],
                    current_configure_opts=item["current_opts"],
                    current_prefix=item["prefix"],
                    current_binary_path=item["binary_path"],
                    target_configure_opts=item["target_opts"],
                    target_prefix=item["target_prefix"],
                    added_modules_json=json.dumps(added_modules, ensure_ascii=False),
                    removed_modules_json=json.dumps(item["removed_modules"], ensure_ascii=False),
                    third_party_json=json.dumps(third_party, ensure_ascii=False),
                )
                session.add(run)
                from ngxops.tasks.models import TaskLog
                session.add(TaskLog(task_id=task.id, message="升级任务已加入执行队列"))
                session.add(
                    NginxUpgradeLog(
                        task_id=task.id,
                        message="升级任务已加入执行队列",
                    )
                )
                task_ids.append(task.id)
            session.commit()

    for task_id in task_ids:
        try:
            executor.submit(
                task_id,
                create_upgrade_runner(
                    session_factory, encryption_key, package_dir, task_id
                ),
            )
        except RuntimeError as exc:
            with session_scope(session_factory) as session:
                with session.begin():
                    failed_task = session.execute(
                        update(Task)
                        .where(Task.id == task_id, Task.status == "pending")
                        .values(status="failed", detail="升级任务未能启动", finished_at=datetime.utcnow())
                    )
                    if failed_task.rowcount == 1:
                        sync_task_audit_result(session, [task_id], "failed")
                    from ngxops.tasks.models import TaskLog
                    session.add(TaskLog(task_id=task_id, level="error", message="升级任务未能启动"))
                    session.add(
                        NginxUpgradeLog(
                            task_id=task_id,
                            level="error",
                            message="升级任务未能启动",
                        )
                    )
    return {"batch_number": batch_number, "task_ids": task_ids}
