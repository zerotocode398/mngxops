"""验证登录失败锁定策略提示和剩余时间显示。"""

from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ngxops.accounts.models import LoginFailureState, User
from ngxops.accounts.passwords import make_password
from ngxops.app import create_app
from ngxops.config import get_settings
from ngxops.database.migration_runner import upgrade_database
from ngxops.database.session import session_scope
from ngxops.settings.models import SystemSetting
from ngxops.settings.service import initialize_defaults


@pytest.fixture
def login_client(tmp_path):
    """创建隔离数据库、登录账户和带 CSRF 令牌的匿名客户端。"""
    settings = replace(
        get_settings(),
        data_dir=tmp_path,
        resource_dir=Path.cwd(),
        debug=False,
        reload=False,
    )
    app = create_app(settings)
    with TestClient(app, follow_redirects=False) as client:
        upgrade_database(app.state.database.engine)
        with session_scope(app.state.database.session_factory) as session:
            with session.begin():
                user = User(
                    username="login-lock-user",
                    password=make_password("Correct-Password-123!"),
                    is_active=True,
                )
                session.add(user)
                session.flush()
                user_id = user.id
        login_page = client.get("/login/")
        token = re.search(
            r'name="csrf_token" value="([^"]+)"', login_page.text
        ).group(1)
        yield client, app, token, user_id


def _submit_wrong_password(client, token, username="login-lock-user"):
    """提交一次错误密码并返回登录页响应。"""
    return client.post(
        "/login/",
        data={
            "csrf_token": token,
            "username": username,
            "password": "Incorrect-Password-123!",
        },
    )


def test_login_lock_policy_and_live_remaining_time(login_client):
    """验证默认阈值说明、达到阈值锁定和后续剩余时间倒计时。"""
    client, app, token, user_id = login_client

    page = client.get("/login/")
    assert "连续登录失败 5 次后将临时锁定 15 分钟" in page.text
    unknown = _submit_wrong_password(client, token, "unknown-login-user")
    assert "用户名或密码错误" in unknown.text
    assert re.search(r'data-login-lock-countdown="\d+"', unknown.text) is None

    for _attempt in range(4):
        response = _submit_wrong_password(client, token)
        assert response.status_code == 200
        assert "用户名或密码错误" in response.text
        assert re.search(r'data-login-lock-countdown="\d+"', response.text) is None

    locked = _submit_wrong_password(client, token)
    assert locked.status_code == 200
    assert "连续登录失败已达到限制，账号已临时锁定。" in locked.text
    assert 'data-login-lock-countdown="900"' in locked.text
    assert "15 分钟" in locked.text
    assert 'data-login-auto-dismiss="false"' in locked.text
    assert "login-error-progress" not in locked.text
    assert "锁定时间已到，可以再次尝试登录。" in locked.text

    with session_scope(app.state.database.session_factory) as session:
        with session.begin():
            state = session.get(LoginFailureState, user_id)
            assert state is not None
            state.login_locked_until = datetime.utcnow() + timedelta(seconds=65)

    retry = _submit_wrong_password(client, token)
    remaining = int(
        re.search(r'data-login-lock-countdown="(\d+)"', retry.text).group(1)
    )
    assert 60 <= remaining <= 65
    assert "1 分" in retry.text


def test_login_lock_hint_uses_current_system_settings(login_client):
    """验证登录阈值和锁定时长提示跟随系统设置更新。"""
    client, app, token, _user_id = login_client
    with session_scope(app.state.database.session_factory) as session:
        with session.begin():
            initialize_defaults(session)
            threshold = session.scalar(
                select(SystemSetting).where(
                    SystemSetting.key == "auth.login_fail_lock_count"
                )
            )
            duration = session.scalar(
                select(SystemSetting).where(
                    SystemSetting.key == "auth.login_fail_lock_minutes"
                )
            )
            threshold.value = "3"
            duration.value = "7"

    page = client.get("/login/")
    assert "连续登录失败 3 次后将临时锁定 7 分钟" in page.text
    for _attempt in range(2):
        assert "用户名或密码错误" in _submit_wrong_password(client, token).text

    locked = _submit_wrong_password(client, token)
    assert 'data-login-lock-countdown="420"' in locked.text
