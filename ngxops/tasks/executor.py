"""在专用线程池中运行阻塞任务并持久化任务生命周期。"""

import json
import logging
import re
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from typing import Any, Callable, Dict, NamedTuple, Optional, Sequence

from sqlalchemy import func, inspect, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ngxops.audit.service import prepare_audit_session
from ngxops.audit.service import write_audit_log
from ngxops.logging_setup import log_exception
from ngxops.database.session import session_scope
from ngxops.tasks.models import ACTIVE_STATUSES, OPERATION_TYPES, Task, TaskLog


logger = logging.getLogger(__name__)
MAX_WORKERS = 4
MAX_LOG_LENGTH = 4000
MAX_RESULT_LENGTH = 1024 * 1024
_SENSITIVE_KEY = re.compile(
    r"(?:password|passwd|private.?key|secret|token|credential|api.?key|session.?key)",
    re.IGNORECASE,
)
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:password|passwd|private[_ -]?key|secret|token|credential|"
    r"api[_ -]?key|session[_ -]?key)\b\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)


class TaskCancelled(Exception):
    """表示任务收到协作式取消信号。"""


def _now() -> datetime:
    """返回供 SQLite 持久化的 UTC 时间。"""
    return datetime.utcnow()


def _redact_text(value: str) -> str:
    """移除日志和结果文本中的凭证明文及私钥片段。"""
    redacted = _PRIVATE_KEY_BLOCK.sub("[已隐藏私钥]", value)
    return _SENSITIVE_ASSIGNMENT.sub(r"\1[已隐藏]", redacted)


def _redact_value(value: Any, depth: int = 0) -> Any:
    """递归清理结构化结果中的敏感字段和值。"""
    if depth > 32:
        raise ValueError("任务结果嵌套层级不能超过 32 层")
    if isinstance(value, dict):
        return {
            str(key): (
                "[已隐藏]"
                if _SENSITIVE_KEY.search(str(key))
                else _redact_value(item, depth + 1)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_value(item, depth + 1) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _serialize_result_tree(value: Any) -> str:
    """校验、清理并编码可持久化的 JSON 结果树。"""
    serialized = json.dumps(
        _redact_value(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )
    if len(serialized.encode("utf-8")) > MAX_RESULT_LENGTH:
        raise ValueError("任务结果不能超过 1 MiB")
    return serialized


def _append_log_in_session(
    session: Session,
    task_id: int,
    message: str,
    level: str = "info",
) -> bool:
    """仅在任务仍活跃时追加一条已脱敏日志。"""
    active = session.scalar(
        select(Task.id).where(
            Task.id == task_id,
            Task.status.in_(ACTIVE_STATUSES),
        )
    )
    if active is None:
        return False
    safe_message = _redact_text(str(message))[:MAX_LOG_LENGTH]
    session.add(TaskLog(task_id=task_id, level=level, message=safe_message))
    session.execute(
        update(Task).where(Task.id == task_id).values(updated_at=_now())
    )
    return True


def _start_task(session_factory: sessionmaker, task_id: int) -> bool:
    """将排队任务原子地切换为执行中。"""
    with session_scope(session_factory) as session:
        with session.begin():
            result = session.execute(
                update(Task)
                .where(Task.id == task_id, Task.status == "pending")
                .values(status="running", started_at=_now(), updated_at=_now())
            )
            if result.rowcount != 1:
                return False
            session.add(TaskLog(task_id=task_id, message="任务开始执行"))
            return True


def _finish_task(
    session_factory: sessionmaker,
    task_id: int,
    status: str,
    detail: str,
    result_tree_json: Optional[str] = None,
) -> bool:
    """仅为仍活跃的任务写入终态及完成日志。"""
    values: Dict[str, Any] = {
        "status": status,
        "detail": _redact_text(detail)[:2000],
        "progress": 100,
        "finished_at": _now(),
        "updated_at": _now(),
    }
    if result_tree_json is not None:
        values["result_tree_json"] = result_tree_json
    with session_scope(session_factory) as session:
        with session.begin():
            result = session.execute(
                update(Task)
                .where(Task.id == task_id, Task.status.in_(ACTIVE_STATUSES))
                .values(**values)
            )
            if result.rowcount != 1:
                return False
            session.add(
                TaskLog(
                    task_id=task_id,
                    level="error" if status == "failed" else "info",
                    message=_redact_text(detail)[:MAX_LOG_LENGTH],
                )
            )
            return True


class TaskContext:
    """向业务任务提供受控的进度、日志、结果和取消操作。"""

    def __init__(
        self,
        task_id: int,
        session_factory: sessionmaker,
        cancel_callback_registrar: Callable[[int, Callable[[], None]], None],
    ) -> None:
        """绑定一个任务 ID 与后台 Session 工厂。"""
        self.task_id = task_id
        self._session_factory = session_factory
        self._cancel_callback_registrar = cancel_callback_registrar

    def register_cancel_callback(self, callback: Callable[[], None]) -> None:
        """登记取消时关闭 SSH 等本地阻塞资源的回调。"""
        self._cancel_callback_registrar(self.task_id, callback)

    def check_cancelled(self) -> None:
        """在业务检查点查询持久化状态并抛出取消信号。"""
        with session_scope(self._session_factory) as session:
            status = session.scalar(
                select(Task.status).where(Task.id == self.task_id)
            )
        if status not in ACTIVE_STATUSES:
            raise TaskCancelled()

    def update_progress(self, progress: int, detail: Optional[str] = None) -> bool:
        """持久化活跃任务的单调进度与当前步骤说明。"""
        if not 0 <= progress < 100:
            raise ValueError("活跃任务进度必须在 0 到 99 之间")
        values: Dict[str, Any] = {
            "progress": func.max(Task.progress, progress),
            "updated_at": _now(),
        }
        if detail is not None:
            values["detail"] = _redact_text(str(detail))[:2000]
        with session_scope(self._session_factory) as session:
            with session.begin():
                result = session.execute(
                    update(Task)
                    .where(
                        Task.id == self.task_id,
                        Task.status.in_(ACTIVE_STATUSES),
                    )
                    .values(**values)
                )
                return result.rowcount == 1

    def append_log(self, message: str, level: str = "info") -> bool:
        """为活跃任务追加一条持久化且脱敏的日志。"""
        if level not in ("debug", "info", "warning", "error"):
            raise ValueError("任务日志级别无效")
        with session_scope(self._session_factory) as session:
            with session.begin():
                return _append_log_in_session(
                    session, self.task_id, message, level
                )

    def set_result_tree(self, result_tree: Any) -> bool:
        """持久化脱敏后的 JSON 结果树。"""
        serialized = _serialize_result_tree(result_tree)
        with session_scope(self._session_factory) as session:
            with session.begin():
                result = session.execute(
                    update(Task)
                    .where(
                        Task.id == self.task_id,
                        Task.status.in_(ACTIVE_STATUSES),
                    )
                    .values(result_tree_json=serialized, updated_at=_now())
                )
                return result.rowcount == 1


TaskCallable = Callable[[TaskContext], Any]


class TaskOutcome(NamedTuple):
    """携带业务级终态、说明和结果树的任务返回值。"""

    status: str
    detail: str
    result_tree: Any


class TaskExecutor:
    """管理单进程任务线程池及待运行 Future。"""

    def __init__(
        self,
        session_factory: sessionmaker,
        max_workers: int = MAX_WORKERS,
    ) -> None:
        """创建固定容量的任务线程池。"""
        self._session_factory = session_factory
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="ngxops-task",
        )
        self._lock = threading.RLock()
        self._futures: Dict[int, Future] = {}
        self._cancel_callbacks: Dict[int, list[Callable[[], None]]] = {}
        self._closed = False

    def submit(self, task_id: int, function: TaskCallable) -> None:
        """将已持久化任务提交到专用线程池。"""
        with self._lock:
            if self._closed:
                raise RuntimeError("任务执行器已关闭")
            future = self._pool.submit(self._run, task_id, function)
            self._futures[task_id] = future
            future.add_done_callback(
                lambda completed: self._forget_future(task_id, completed)
            )

    def cancel(self, task_id: int) -> None:
        """取消尚未开始的 Future，运行中任务由检查点协作停止。"""
        with self._lock:
            future = self._futures.get(task_id)
        if future is not None:
            future.cancel()
        self._run_cancel_callbacks(task_id)

    def register_cancel_callback(
        self,
        task_id: int,
        callback: Callable[[], None],
    ) -> None:
        """登记资源回调，并处理登记前已发生的取消。"""
        with self._lock:
            self._cancel_callbacks.setdefault(task_id, []).append(callback)
        try:
            with session_scope(self._session_factory) as session:
                status = session.scalar(
                    select(Task.status).where(Task.id == task_id)
                )
            if status not in ACTIVE_STATUSES:
                self._run_cancel_callbacks(task_id)
        except Exception as exc:
            log_exception(
                logger,
                "读取任务取消状态失败",
                exc,
                "task_id={}".format(task_id),
            )

    def _run_cancel_callbacks(self, task_id: int) -> None:
        """执行并清理任务已登记的本地资源关闭回调。"""
        with self._lock:
            callbacks = self._cancel_callbacks.pop(task_id, [])
        for callback in callbacks:
            try:
                callback()
            except Exception as exc:
                log_exception(
                    logger,
                    "任务资源关闭回调失败",
                    exc,
                    "task_id={}".format(task_id),
                )

    def _clear_cancel_callbacks(self, task_id: int) -> None:
        """任务退出时释放未触发的资源回调引用。"""
        with self._lock:
            self._cancel_callbacks.pop(task_id, None)

    def shutdown(self) -> None:
        """停止接收任务并取消尚未运行的 Future。"""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _forget_future(self, task_id: int, completed: Future) -> None:
        """移除已经结束的 Future 引用。"""
        with self._lock:
            if self._futures.get(task_id) is completed:
                self._futures.pop(task_id, None)

    def _run(self, task_id: int, function: TaskCallable) -> None:
        """运行任务并将成功、取消或异常持久化为终态。"""
        context = TaskContext(
            task_id,
            self._session_factory,
            self.register_cancel_callback,
        )
        try:
            if not _start_task(self._session_factory, task_id):
                return
            result = function(context)
            context.check_cancelled()
            status = "success"
            detail = "任务执行成功"
            result_tree = result
            if isinstance(result, TaskOutcome):
                if result.status not in ("success", "failed"):
                    raise ValueError("任务业务终态无效")
                status = result.status
                detail = result.detail
                result_tree = result.result_tree
            serialized = (
                _serialize_result_tree(result_tree)
                if result_tree is not None
                else None
            )
            _finish_task(
                self._session_factory,
                task_id,
                status,
                detail,
                serialized,
            )
        except TaskCancelled:
            return
        except Exception as exc:
            error_type = type(exc).__name__
            log_exception(
                logger,
                "后台任务执行失败",
                exc,
                "task_id={}".format(task_id),
            )
            try:
                _finish_task(
                    self._session_factory,
                    task_id,
                    "failed",
                    "任务执行失败（{}）".format(error_type),
                    _serialize_result_tree(
                        {"status": "failed", "error_type": error_type}
                    ),
                )
            except Exception as finish_error:
                log_exception(
                    logger,
                    "后台任务终态写入失败",
                    finish_error,
                    "task_id={}".format(task_id),
                )
        finally:
            self._clear_cancel_callbacks(task_id)


def create_task(
    session_factory: sessionmaker,
    executor: TaskExecutor,
    function: TaskCallable,
    *,
    operation_type: str = "other",
    detail: str = "",
    source_batch: str = "",
    target_hostnames: Sequence[str] = (),
    target_ips: Sequence[str] = (),
    target_configs: Sequence[str] = (),
    subject_type: str = "",
    subject_id: Optional[int] = None,
    trigger_user_id: Optional[int] = None,
    trigger_ip: str = "",
) -> int:
    """创建任务记录并将其排入后台线程池。"""
    if operation_type not in OPERATION_TYPES:
        raise ValueError("不支持的任务类型")
    if len(source_batch) > 64:
        raise ValueError("任务批次号不能超过 64 个字符")
    if len(subject_type) > 40:
        raise ValueError("任务关联资源类型不能超过 40 个字符")
    task = Task(
        operation_type=operation_type,
        detail=_redact_text(str(detail))[:2000],
        source_batch=_redact_text(source_batch)[:64],
        target_hostnames=", ".join(
            _redact_text(str(item)) for item in target_hostnames
        )[:4000],
        target_ips=", ".join(_redact_text(str(item)) for item in target_ips)[:4000],
        target_configs=", ".join(
            _redact_text(str(item)) for item in target_configs
        )[:4000],
        subject_type=subject_type,
        subject_id=subject_id,
        trigger_user_id=trigger_user_id,
    )
    with session_scope(session_factory) as session:
        prepare_audit_session(session, actor_id=trigger_user_id, ip=trigger_ip)
        with session.begin():
            session.add(task)
            session.flush()
            task_id = task.id
            session.add(TaskLog(task_id=task_id, message="任务已加入执行队列"))
    try:
        executor.submit(task_id, function)
    except RuntimeError as exc:
        _finish_task(
            session_factory,
            task_id,
            "failed",
            "任务未能启动（{}）".format(type(exc).__name__),
        )
    return task_id


def cancel_task(
    session_factory: sessionmaker,
    task_id: int,
    actor_id: Optional[int] = None,
    actor_username: str = "",
    actor_ip: str = "",
) -> bool:
    """原子地取消待运行或正在运行的任务。"""
    with session_scope(session_factory) as session:
        prepare_audit_session(
            session,
            actor_id=actor_id,
            username=actor_username,
            ip=actor_ip,
        )
        with session.begin():
            source_batch = session.scalar(
                select(Task.source_batch).where(Task.id == task_id)
            )
            result = session.execute(
                update(Task)
                .where(Task.id == task_id, Task.status.in_(ACTIVE_STATUSES))
                .values(
                    status="cancelled",
                    progress=100,
                    detail="用户手动取消",
                    finished_at=_now(),
                    updated_at=_now(),
                )
            )
            if result.rowcount != 1:
                return False
            write_audit_log(
                session,
                "任务中心",
                "取消异步任务",
                "取消任务 #{}".format(task_id),
                task_id=task_id,
                source_batch=source_batch or "",
            )
            session.add(
                TaskLog(task_id=task_id, message="用户手动取消任务")
            )
            return True


def recover_interrupted_tasks(engine: Engine, session_factory: sessionmaker) -> int:
    """启动时将上次进程遗留的待运行和执行中任务标记失败。"""
    if not inspect(engine).has_table("ngxops_tasks"):
        return 0
    with session_scope(session_factory) as session:
        with session.begin():
            task_ids = list(
                session.scalars(
                    select(Task.id).where(Task.status.in_(ACTIVE_STATUSES))
                )
            )
            if not task_ids:
                return 0
            now = _now()
            session.execute(
                update(Task)
                .where(Task.id.in_(task_ids), Task.status.in_(ACTIVE_STATUSES))
                .values(
                    status="failed",
                    detail="服务进程重启，任务已中断",
                    finished_at=now,
                    updated_at=now,
                )
            )
            session.add_all(
                TaskLog(
                    task_id=task_id,
                    level="error",
                    message="服务进程重启，任务未完成并已中断",
                )
                for task_id in task_ids
            )
            return len(task_ids)
