"""提供请求与后台任务隔离的 SQLAlchemy Session。"""

from contextlib import contextmanager
from typing import Generator, Iterator

from fastapi import Request
from sqlalchemy.orm import Session, sessionmaker


@contextmanager
def session_scope(session_factory: sessionmaker) -> Iterator[Session]:
    """创建独立 Session，并在退出时回滚未提交事务后关闭。"""
    session = session_factory()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        if session.in_transaction():
            session.rollback()
        session.close()


def get_session(request: Request) -> Generator[Session, None, None]:
    """为单个 HTTP 请求提供独立 Session。"""
    session_factory = request.app.state.database.session_factory
    with session_scope(session_factory) as session:
        from ngxops.audit.service import prepare_audit_session, request_client_ip

        prepare_audit_session(session, ip=request_client_ip(request))
        yield session
