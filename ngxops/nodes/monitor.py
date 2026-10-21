"""按系统设置异步巡检节点的 SSH 与 Nginx 状态。"""

import logging
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from sqlalchemy import case, or_, select, update
from sqlalchemy.orm import sessionmaker

from ngxops.credentials.models import Credential
from ngxops.database.session import session_scope
from ngxops.nodes.models import Node
from ngxops.nodes.tasks import _apply_probe_result, _load_targets, _probe_target
from ngxops.settings.service import read_setting
from ngxops.tasks.executor import TaskCancelled


logger = logging.getLogger(__name__)
_MAX_MONITOR_WORKERS = 10
_STALE_INTERVAL_MULTIPLIER = 3
_MONITOR_TICK_SECONDS = 5


class _MonitorContext:
    """提供探测代码所需的最小取消接口。"""

    def __init__(self, stop_event: threading.Event) -> None:
        """保存巡检线程的停止信号。"""
        self._stop_event = stop_event

    def check_cancelled(self) -> None:
        """在巡检停止时阻止尚未开始的远程连接。"""
        if self._stop_event.is_set():
            raise TaskCancelled()

    def register_cancel_callback(self, callback) -> None:
        """兼容 SSH 连接器的取消回调注册接口。"""
        return None


class NodeStatusMonitor:
    """管理进程内唯一的节点状态巡检线程。"""

    def __init__(self, session_factory: sessionmaker, encryption_key: bytes) -> None:
        """保存巡检所需的数据访问器和凭证解密密钥。"""
        self._session_factory = session_factory
        self._encryption_key = encryption_key
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """启动后台巡检线程；重复调用不会创建第二个线程。"""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="ngxops-node-status-monitor",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """停止接收巡检并等待有界 SSH 操作完成。"""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None

    def _run(self) -> None:
        """轮询到期节点并以有限并发执行状态探测。"""
        futures: Dict[int, Future] = {}
        next_due: Dict[int, datetime] = {}
        current_interval = None
        next_expiration = 0.0
        with ThreadPoolExecutor(
            max_workers=_MAX_MONITOR_WORKERS,
            thread_name_prefix="ngxops-node-monitor",
        ) as pool:
            while not self._stop_event.is_set():
                try:
                    interval, concurrency = _load_monitor_settings(
                        self._session_factory
                    )
                    if interval != current_interval:
                        next_due.clear()
                        current_interval = interval

                    now_monotonic = time.monotonic()
                    if now_monotonic >= next_expiration:
                        _expire_stale_states(self._session_factory, interval)
                        next_expiration = now_monotonic + min(interval, 30)

                    _collect_finished_probes(futures, next_due, interval)
                    now = datetime.utcnow()
                    candidates = _load_due_nodes(
                        self._session_factory,
                        interval,
                        now,
                    )
                    eligible_ids = {
                        node_id
                        for node_id, _probe_at, _nginx_at in candidates
                    }
                    for node_id in list(next_due):
                        if node_id not in eligible_ids:
                            next_due.pop(node_id, None)

                    for node_id, probe_at, nginx_probe_at in candidates:
                        if len(futures) >= concurrency:
                            break
                        if node_id in futures:
                            continue
                        if next_due.get(node_id, datetime.min) > now:
                            continue
                        if not _probe_is_due(probe_at, nginx_probe_at, now, interval):
                            continue
                        next_due[node_id] = now + timedelta(seconds=interval)
                        futures[node_id] = pool.submit(
                            _probe_and_store,
                            self._session_factory,
                            node_id,
                            self._encryption_key,
                            self._stop_event,
                        )

                    if futures:
                        wait(
                            list(futures.values()),
                            timeout=_MONITOR_TICK_SECONDS,
                            return_when=FIRST_COMPLETED,
                        )
                    else:
                        self._stop_event.wait(_MONITOR_TICK_SECONDS)
                except Exception as exc:
                    logger.error(
                        "Node status monitor iteration failed error_type=%s",
                        type(exc).__name__,
                    )
                    self._stop_event.wait(_MONITOR_TICK_SECONDS)


def _load_monitor_settings(session_factory: sessionmaker) -> Tuple[int, int]:
    """读取并限制巡检间隔和并发参数。"""
    with session_scope(session_factory) as session:
        interval = read_setting(session, "node.status_probe_interval_seconds", 60)
        concurrency = read_setting(session, "node.status_probe_concurrency", 3)
    return min(300, max(30, int(interval))), min(10, max(1, int(concurrency)))


def _load_due_nodes(
    session_factory: sessionmaker,
    interval: int,
    now: datetime,
) -> List[Tuple[int, Optional[datetime], Optional[datetime]]]:
    """读取未锁定、凭证有效且可能到期的节点探测时间。"""
    cutoff = now - timedelta(seconds=interval)
    with session_scope(session_factory) as session:
        rows = session.execute(
            select(Node.id, Node.last_probe_at, Node.last_nginx_probe_at)
            .join(Credential, Credential.id == Node.credential_id)
            .where(
                Node.is_deleted.is_(False),
                Node.is_locked.is_(False),
                Credential.is_enabled.is_(True),
            )
            .order_by(
                case((Node.last_probe_at.is_(None), 0), else_=1),
                Node.last_probe_at.asc(),
                Node.id.asc(),
            )
        ).all()
    return [
        (node_id, probe_at, nginx_probe_at)
        for node_id, probe_at, nginx_probe_at in rows
        if probe_at is None
        or nginx_probe_at is None
        or probe_at <= cutoff
        or nginx_probe_at <= cutoff
    ]


def _probe_is_due(
    probe_at: Optional[datetime],
    nginx_probe_at: Optional[datetime],
    now: datetime,
    interval: int,
) -> bool:
    """确认 SSH 或 Nginx 最近一次观测已经到达巡检间隔。"""
    cutoff = now - timedelta(seconds=interval)
    return (
        probe_at is None
        or nginx_probe_at is None
        or probe_at <= cutoff
        or nginx_probe_at <= cutoff
    )


def _collect_finished_probes(
    futures: Dict[int, Future],
    next_due: Dict[int, datetime],
    interval: int,
) -> None:
    """回收已完成探测并安排每个节点的下一次到期时间。"""
    finished = [node_id for node_id, future in futures.items() if future.done()]
    for node_id in finished:
        future = futures.pop(node_id)
        try:
            future.result()
        except TaskCancelled:
            pass
        except Exception as exc:
            logger.warning(
                "Node status probe failed node_id=%s error_type=%s",
                node_id,
                type(exc).__name__,
            )
        next_due[node_id] = datetime.utcnow() + timedelta(seconds=interval)


def _probe_and_store(
    session_factory: sessionmaker,
    node_id: int,
    encryption_key: bytes,
    stop_event: threading.Event,
) -> None:
    """单次连接探测节点并复用 NX-022 状态写回规则。"""
    targets = _load_targets(session_factory, [node_id], encryption_key)
    if not targets:
        return
    target = targets[0]
    if target["is_locked"] or target["credential_error"]:
        return
    target["detect_retries"] = 0
    result = _probe_target(target, _MonitorContext(stop_event))
    _apply_probe_result(session_factory, target, result, update_nginx=True)


def _expire_stale_states(session_factory: sessionmaker, interval: int) -> None:
    """将超过三个巡检间隔的 SSH 与 Nginx 结果降为未知。"""
    now = datetime.utcnow()
    cutoff = now - timedelta(seconds=interval * _STALE_INTERVAL_MULTIPLIER)
    with session_scope(session_factory) as session:
        with session.begin():
            session.execute(
                update(Node)
                .where(
                    Node.is_deleted.is_(False),
                    Node.is_locked.is_(False),
                    Node.status != "unknown",
                    or_(Node.last_probe_at.is_(None), Node.last_probe_at <= cutoff),
                )
                .values(status="unknown", updated_at=now)
            )
            session.execute(
                update(Node)
                .where(
                    Node.is_deleted.is_(False),
                    Node.is_locked.is_(False),
                    Node.nginx_available.is_not(None),
                    or_(
                        Node.last_nginx_probe_at.is_(None),
                        Node.last_nginx_probe_at <= cutoff,
                    ),
                )
                .values(nginx_available=None, nginx_version="", updated_at=now)
            )
