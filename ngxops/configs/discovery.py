"""通过 SSH 读取 Nginx 主配置和递归 include 文件。"""

import posixpath
import re
import shlex
from typing import Callable, Dict, List, Optional, Tuple

import paramiko

from ngxops.credentials.tasks import _connect_ssh
from ngxops.tasks.executor import TaskContext


INCLUDE_PATTERN = re.compile(r"include\s+([^;]+?)\s*;")
SKIP_FILES = frozenset(
    (
        "mime.types",
        "fastcgi_params",
        "fastcgi.conf",
        "uwsgi_params",
        "scgi_params",
        "koi-utf",
        "koi-win",
        "win-utf",
    )
)
MAX_INCLUDE_DEPTH = 3
MAX_DISCOVERED_FILES = 500
MAX_DISCOVERY_ERRORS = 100


def _is_builtin_file(path: str) -> bool:
    """判断路径是否指向 Nginx 随附而不应作为配置导入的文件。"""
    name = posixpath.basename(path)
    return name in SKIP_FILES or "/modules/" in path


def _normalize_include_path(raw_path: str, current_dir: str) -> str:
    """将 include 中的绝对或相对路径规范为 POSIX 路径。"""
    include_path = (raw_path or "").strip().strip("\"'")
    if not include_path:
        return ""
    if include_path.startswith("/"):
        return posixpath.normpath(include_path)
    return posixpath.normpath(posixpath.join(current_dir, include_path))


def _quote_glob_pattern(pattern: str) -> str:
    """引用 glob 字面量，仅保留 shell 的安全通配符语法。"""
    parts = []
    literal = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char in "*?":
            if literal:
                parts.append(shlex.quote("".join(literal)))
                literal = []
            parts.append(char)
            index += 1
            continue
        if char == "[":
            closing = pattern.find("]", index + 1)
            if closing > index + 1:
                choices = pattern[index + 1 : closing]
                if re.fullmatch(r"[A-Za-z0-9_.!^+-]+", choices):
                    if literal:
                        parts.append(shlex.quote("".join(literal)))
                        literal = []
                    parts.append("[{}]".format(choices))
                    index = closing + 1
                    continue
        literal.append(char)
        index += 1
    if literal:
        parts.append(shlex.quote("".join(literal)))
    return "".join(parts)


def _remote_read(
    client: paramiko.SSHClient,
    path: str,
) -> Tuple[Optional[str], bool]:
    """通过已连接 SSH 会话读取单个配置文件。"""
    command = "cat -- {}".format(shlex.quote(path))
    try:
        _stdin, stdout, _stderr = client.exec_command(command, timeout=30)
        exit_status = stdout.channel.recv_exit_status()
        content = stdout.read().decode("utf-8", "replace")
    except Exception:
        return None, False
    if exit_status != 0:
        return None, False
    return content, True


def _expand_include(
    client: paramiko.SSHClient,
    include_path: str,
) -> List[str]:
    """展开 include 通配符并返回远程路径列表。"""
    if not any(token in include_path for token in ("*", "?", "[")):
        return [include_path]
    command = "ls -1d -- {} 2>/dev/null".format(
        _quote_glob_pattern(include_path)
    )
    try:
        _stdin, stdout, _stderr = client.exec_command(command, timeout=20)
        exit_status = stdout.channel.recv_exit_status()
        output = stdout.read().decode("utf-8", "replace")
    except Exception:
        return []
    if exit_status != 0:
        return []
    return [line.strip() for line in output.splitlines() if line.strip()]


def discover_remote_configs(
    target: dict,
    main_conf_path: str,
    context: TaskContext,
    progress_callback: Optional[Callable[[int, str], None]] = None,
    max_depth: int = MAX_INCLUDE_DEPTH,
) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    """复用单条 SSH 连接递归读取配置文件并报告路径级错误。"""
    files = []
    errors = []

    def add_error(path: str, message: str) -> None:
        """记录有界且不包含远程命令输出的路径错误摘要。"""
        if len(errors) >= MAX_DISCOVERY_ERRORS:
            return
        safe_path = path[:500]
        errors.append({"path": safe_path, "message": message})
    client, connect_error = _connect_ssh(
        target["ip"],
        target["port"],
        target["username"],
        target["auth_type"],
        target["password"],
        target["private_key"],
        context,
        target.get("ssh_timeout", 10),
        target.get("detect_retries", 1),
    )
    if client is None:
        add_error(main_conf_path, connect_error)
        return files, errors

    try:
        pending = [(main_conf_path, 0)]
        seen = set()
        while pending:
            context.check_cancelled()
            current_path, current_depth = pending.pop(0)
            if current_path in seen:
                continue
            seen.add(current_path)

            content, success = _remote_read(client, current_path)
            if not success:
                add_error(current_path, "读取远程配置文件失败")
                continue

            if current_path == main_conf_path or not _is_builtin_file(current_path):
                if (
                    len(current_path) > 500
                    or len(posixpath.basename(current_path)) > 255
                ):
                    add_error(current_path, "远程配置路径或文件名超过允许长度")
                    continue
                files.append(
                    {
                        "path": current_path,
                        "name": posixpath.basename(current_path),
                        "content": content or "",
                    }
                )
                if progress_callback:
                    progress_callback(len(files), current_path)
                if len(files) >= MAX_DISCOVERED_FILES:
                    add_error(
                        current_path,
                        "发现文件数量达到上限，扫描结果可能不完整",
                    )
                    break

            current_dir = posixpath.dirname(current_path) or "/"
            for match in INCLUDE_PATTERN.finditer(content or ""):
                include_path = _normalize_include_path(match.group(1), current_dir)
                if not include_path:
                    continue
                for matched_path in _expand_include(client, include_path):
                    normalized_path = posixpath.normpath(
                        matched_path.strip().strip("\"'")
                    )
                    if not normalized_path or normalized_path in seen:
                        continue
                    if _is_builtin_file(normalized_path):
                        continue
                    if len(normalized_path) > 500:
                        add_error(normalized_path, "include 路径超过 500 个字符")
                        continue
                    next_depth = current_depth + 1
                    if next_depth > max_depth:
                        add_error(
                            normalized_path,
                            "include 递归超过 {} 层".format(max_depth),
                        )
                        continue
                    pending.append((normalized_path, next_depth))
    finally:
        client.close()
    return files, errors
