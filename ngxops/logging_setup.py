"""配置安全的控制台与轮转文件日志。"""

import logging
import re
import traceback
from contextlib import contextmanager
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Dict, Iterator, Optional


_PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
    re.DOTALL,
)
_URL_CREDENTIAL_PATTERN = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s@]+@")
_SENSITIVE_VALUE_PATTERN = re.compile(
    r"(?i)\b(password|passwd|private[_ -]?key|secret|access[_ -]?token|token|"
    r"session(?:[_ -]?key|id)?|csrf[_ -]?token|api[_ -]?key|authorization|cookie)\b"
    r"(\s*[:=]\s*)(?:bearer\s+)?"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;&]+)"
)
_LOG_CONTEXT_FIELDS = (
    "request_id",
    "task_id",
    "node_id",
    "operation_type",
    "source_batch",
    "actor_user_id",
)
_LOG_CONTEXT: ContextVar[Dict[str, str]] = ContextVar(
    "ngxops_log_context", default={}
)


@contextmanager
def log_context(**values: object) -> Iterator[None]:
    """在当前请求或任务作用域内补充可关联的日志字段。"""
    current = dict(_LOG_CONTEXT.get())
    for name in _LOG_CONTEXT_FIELDS:
        if name not in values:
            continue
        value = values[name]
        text = "-" if value is None or value == "" else str(value)
        current[name] = text.replace("\r", " ").replace("\n", " ")[:128]
    token = _LOG_CONTEXT.set(current)
    try:
        yield
    finally:
        _LOG_CONTEXT.reset(token)


class LogContextFilter(logging.Filter):
    """将当前请求或任务上下文附加到所有传播至根记录器的日志。"""

    def filter(self, record: logging.LogRecord) -> bool:
        """为格式化器提供稳定的关联字段。"""
        context = _LOG_CONTEXT.get()
        for name in _LOG_CONTEXT_FIELDS:
            setattr(record, name, context.get(name, "-"))
        return True


class RedactingFormatter(logging.Formatter):
    """在输出前遮蔽常见格式的密钥和认证值。"""

    def format(self, record: logging.LogRecord) -> str:
        """格式化并遮蔽日志内容中的敏感值。"""
        rendered = super().format(record)
        rendered = _PRIVATE_KEY_PATTERN.sub("[REDACTED PRIVATE KEY]", rendered)
        rendered = _URL_CREDENTIAL_PATTERN.sub(r"\1[REDACTED]@", rendered)
        return _SENSITIVE_VALUE_PATTERN.sub(r"\1\2[REDACTED]", rendered)


def configure_logging(
    data_dir: Path,
    log_level: str,
    log_name: str = "ngxops.log",
) -> Path:
    """配置根记录器并返回本次使用的日志文件路径。"""
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / log_name
    root_logger = logging.getLogger()
    level = 5 if log_level.lower() == "trace" else getattr(
        logging,
        log_level.upper(),
        logging.INFO,
    )
    root_logger.setLevel(level)
    logging.getLogger("paramiko").setLevel(logging.WARNING)
    logging.getLogger("paramiko.transport").setLevel(logging.WARNING)

    for handler in list(root_logger.handlers):
        if getattr(handler, "_ngxops_handler", False):
            root_logger.removeHandler(handler)
            handler.close()

    formatter = RedactingFormatter(
        "%(asctime)s %(levelname)s %(name)s request_id=%(request_id)s "
        "task_id=%(task_id)s node_id=%(node_id)s "
        "operation_type=%(operation_type)s "
        "source_batch=%(source_batch)s actor_user_id=%(actor_user_id)s "
        "%(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler = RotatingFileHandler(
        str(log_path),
        maxBytes=10 * 1024 * 1024,
        backupCount=10,
        encoding="utf-8",
    )
    file_handler.addFilter(LogContextFilter())
    file_handler.setFormatter(formatter)
    file_handler._ngxops_handler = True
    root_logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.addFilter(LogContextFilter())
    console_handler.setFormatter(formatter)
    console_handler._ngxops_handler = True
    root_logger.addHandler(console_handler)
    return log_path


def log_exception(
    logger: logging.Logger,
    message: str,
    exc: BaseException,
    context: Optional[str] = None,
) -> None:
    """记录异常类型、消息和堆栈位置，不包含局部变量或请求正文。"""
    stack = "".join(traceback.format_tb(exc.__traceback__))
    suffix = " {}".format(context) if context else ""
    logger.error(
        "%s%s exception_type=%s exception=%s\n%s",
        message,
        suffix,
        type(exc).__name__,
        str(exc)[:2000],
        stack,
    )
