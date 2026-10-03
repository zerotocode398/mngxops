"""管理后台 Web 服务进程及其本地控制记录。"""

import asyncio
from contextlib import contextmanager
import json
import logging
import os
import secrets
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Tuple

import psutil
import uvicorn

from ngxops.config import Settings, is_frozen


logger = logging.getLogger(__name__)
START_TIMEOUT_SECONDS = 20
STOP_TIMEOUT_SECONDS = 20


def start_service(settings: Settings, host: str, port: int, log_level: str) -> int:
    """在后台启动单 worker 服务并等待健康检查成功。"""
    _, pid_path, lock_path = _service_paths(settings.data_dir)
    with _start_lock(lock_path):
        record = _read_record(pid_path)
        if record and _get_matching_process(record) is not None:
            raise RuntimeError(
                "The service is already running with PID {}.".format(record["pid"])
            )
        _remove_record(pid_path)
        token = secrets.token_urlsafe(32)
        command = _managed_command(token, host, port, log_level)
        environment = os.environ.copy()
        environment["NGXOPS_HOME"] = str(settings.data_dir)
        process = subprocess.Popen(
            command,
            cwd=str(_working_directory(settings)),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            **_detached_process_options(),
        )
        process_info = psutil.Process(process.pid)
        record = {
            "pid": process.pid,
            "create_time": process_info.create_time(),
            "executable": os.path.normcase(os.path.realpath(process_info.exe())),
            "token": token,
            "host": host,
            "port": port,
        }
        _write_record(pid_path, record)

        if not _wait_until_healthy(record):
            _terminate_recorded_process(record, pid_path)
            raise RuntimeError(
                "The service did not become healthy. Review logs/ngxops.log."
            )
        logger.info(
            "service_action action=start result=success pid=%s host=%s port=%s",
            process.pid,
            host,
            port,
        )
        return process.pid


def stop_service(data_dir: Path, timeout: int = STOP_TIMEOUT_SECONDS) -> bool:
    """验证服务进程身份后请求停止并等待清理完成。"""
    _, pid_path, _ = _service_paths(data_dir)
    record = _read_record(pid_path)
    if not record:
        logger.info("service_action action=stop result=not_running")
        return False

    process = _get_matching_process(record)
    if process is None:
        _remove_record(pid_path, record.get("token"))
        logger.info("service_action action=stop result=stale_record")
        return False

    stopped = False
    try:
        if os.name == "nt":
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            process.terminate()
        process.wait(timeout=max(1, timeout))
        stopped = True
    except psutil.TimeoutExpired:
        logger.warning(
            "service_action action=stop result=timeout pid=%s; forcing termination",
            process.pid,
        )
        process.kill()
        process.wait(timeout=5)
        stopped = True
    except psutil.NoSuchProcess:
        stopped = True
    except psutil.AccessDenied as exc:
        raise RuntimeError(
            "Permission denied while stopping the service. Run this command "
            "as the same OS user."
        ) from exc

    if stopped:
        _remove_record(pid_path, record.get("token"))

    logger.info("service_action action=stop result=success pid=%s", record["pid"])
    return True


def service_status(data_dir: Path) -> Optional[Dict[str, Any]]:
    """返回正在运行的服务信息，并清理过期控制记录。"""
    _, pid_path, _ = _service_paths(data_dir)
    record = _read_record(pid_path)
    if not record:
        logger.info("service_action action=status result=stopped")
        return None
    process = _get_matching_process(record)
    if process is None:
        _remove_record(pid_path, record.get("token"))
        logger.info("service_action action=status result=stopped")
        return None
    logger.info("service_action action=status result=running pid=%s", record["pid"])
    return record


def run_managed_server(
    data_dir: Path,
    token: str,
    host: str,
    port: int,
    log_level: str,
) -> None:
    """在受管进程中运行不支持自动重载的单 worker Uvicorn。"""
    _, pid_path, _ = _service_paths(data_dir)
    server = uvicorn.Server(
        uvicorn.Config(
            "ngxops.asgi:app",
            host=host,
            port=port,
            log_level=log_level,
            reload=False,
            workers=1,
            access_log=False,
            log_config=None,
        )
    )

    if os.name == "nt" and hasattr(signal, "SIGBREAK"):
        signal.signal(
            signal.SIGBREAK,
            lambda signum, frame: setattr(server, "should_exit", True),
        )

    try:
        asyncio.run(server.serve())
    finally:
        _remove_record(pid_path, token)


def _service_paths(data_dir: Path) -> Tuple[Path, Path, Path]:
    """返回服务记录与启动锁的目录路径。"""
    run_dir = data_dir / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir, run_dir / "service.json", run_dir / "start.lock"


@contextmanager
def _start_lock(lock_path: Path) -> Iterator[None]:
    """使用跨进程文件锁避免同时启动多个服务实例。"""
    lock_file = lock_path.open("a+b")
    try:
        lock_file.seek(0, os.SEEK_END)
        if lock_file.tell() == 0:
            lock_file.write(b"0")
            lock_file.flush()
        lock_file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError(
                "Another service start command is already running."
            ) from exc
        yield
    finally:
        try:
            lock_file.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        lock_file.close()


def _managed_command(token: str, host: str, port: int, log_level: str):
    """构造源码或冻结运行时的受管服务命令。"""
    arguments = [
        "_managed-serve",
        "--service-token",
        token,
        "--host",
        host,
        "--port",
        str(port),
        "--log-level",
        log_level,
    ]
    if is_frozen():
        return [sys.executable] + arguments
    return [sys.executable, "-m", "ngxops"] + arguments


def _working_directory(settings: Settings) -> Path:
    """返回启动子进程时稳定存在的程序工作目录。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return settings.resource_dir


def _detached_process_options() -> Dict[str, int]:
    """返回适用于当前操作系统的独立进程选项。"""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _write_record(path: Path, record: Dict[str, Any]) -> None:
    """通过原子替换写入服务 PID 记录。"""
    temporary_path = path.with_suffix(".tmp")
    temporary_path.write_text(
        json.dumps(record, sort_keys=True),
        encoding="utf-8",
    )
    if os.name != "nt":
        temporary_path.chmod(0o600)
    os.replace(str(temporary_path), str(path))


def _read_record(path: Path) -> Optional[Dict[str, Any]]:
    """读取并校验服务控制记录格式。"""
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return None
    if not isinstance(record, dict):
        return None
    required = ("pid", "create_time", "executable", "token", "host", "port")
    if any(key not in record for key in required):
        return None
    if not isinstance(record["pid"], int) or not isinstance(record["token"], str):
        return None
    return record


def _get_matching_process(record: Dict[str, Any]):
    """仅返回 PID、启动时间、程序路径和随机标记全部相符的服务进程。"""
    try:
        process = psutil.Process(record["pid"])
        process_info = process.as_dict(attrs=("cmdline", "create_time", "exe"))
        current_executable = os.path.normcase(
            os.path.realpath(process_info.get("exe") or "")
        )
        expected_executable = os.path.normcase(record["executable"])
        if current_executable != expected_executable:
            return None
        if abs(process_info["create_time"] - float(record["create_time"])) > 0.01:
            return None
        if record["token"] not in (process_info.get("cmdline") or []):
            return None
        return process
    except (psutil.AccessDenied, psutil.NoSuchProcess, KeyError, TypeError, ValueError):
        return None


def _remove_record(path: Path, token: Optional[str] = None) -> None:
    """仅删除匹配指定标记的 PID 记录。"""
    record = _read_record(path)
    if record is None or (token is not None and record.get("token") != token):
        return
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _wait_until_healthy(record: Dict[str, Any]) -> bool:
    """等待后台服务完成启动并通过本机健康检查。"""
    health_host = _health_host(record["host"])
    url = "http://{}:{}/health".format(health_host, record["port"])
    deadline = time.monotonic() + START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if _get_matching_process(record) is None:
            return False
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return True
        except (OSError, urllib.error.URLError):
            time.sleep(0.25)
    return False


def _health_host(host: str) -> str:
    """返回健康检查可用的本机连接地址。"""
    if host in ("", "0.0.0.0"):
        return "127.0.0.1"
    if host == "::":
        return "[::1]"
    if ":" in host and not host.startswith("["):
        return "[{}]".format(host)
    return host


def _terminate_recorded_process(record: Dict[str, Any], pid_path: Path) -> None:
    """清理健康检查失败的受管服务进程。"""
    process = _get_matching_process(record)
    if process is not None:
        try:
            process.terminate()
            process.wait(timeout=5)
        except psutil.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        except psutil.NoSuchProcess:
            pass
    _remove_record(pid_path, record.get("token"))
