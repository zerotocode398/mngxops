"""验证用户、角色和用户组管理页面及权限继承流程。"""

from dataclasses import replace
from pathlib import Path
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ngxops.accounts.models import User
from ngxops.accounts.passwords import make_password
from ngxops.app import create_app
from ngxops.config import get_settings
from ngxops.database.migration_runner import upgrade_database
from ngxops.database.session import session_scope
from ngxops.rbac.models import (
    PermissionItem,
    Role,
    Team,
    profile_permissions,
    profile_roles,
    team_members,
)
from ngxops.rbac.service import user_has_permission


@pytest.fixture
def rbac_client(tmp_path):
    """创建隔离数据库、管理员和两个普通运维账户。"""
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
                admin = User(
                    username="rbac-admin",
                    password=make_password("rbac-admin-password-123"),
                    is_superuser=True,
                    is_active=True,
                )
                inherited_user = User(
                    username="ops-inherited",
                    password=make_password("ops-inherited-password-123"),
                    email="inherited@example.test",
                    is_active=True,
                )
                personal_user = User(
                    username="ops-personal",
                    password=make_password("ops-personal-password-123"),
                    email="personal@example.test",
                    is_active=True,
                )
                session.add_all([admin, inherited_user, personal_user])
                session.flush()
                inherited_user_id = inherited_user.id
                personal_user_id = personal_user.id

        login_page = client.get("/login/")
        token = re.search(
            r'name="csrf_token" value="([^"]+)"', login_page.text
        ).group(1)
        login = client.post(
            "/login/",
            data={
                "csrf_token": token,
                "username": "rbac-admin",
                "password": "rbac-admin-password-123",
            },
        )
        assert login.status_code == 302
        yield client, app, token, inherited_user_id, personal_user_id


def test_team_navigation_opens_distinct_team_management_page(rbac_client):
    """验证用户组菜单进入独立用户组页，角色兼容别名仍指向角色列表。"""
    client, _app, _token, _inherited_id, _personal_id = rbac_client

    teams = client.get("/users/teams/")
    roles = client.get("/users/roles/")
    users = client.get("/users/")
    legacy_role_alias = client.get("/users/groups/")

    assert teams.status_code == 200
    assert "用户组列表" in teams.text
    assert "关联角色" in teams.text
    assert re.search(
        r'<a (?=[^>]*href="/users/teams/")'
        r'(?=[^>]*data-nav="users:team_list")[^>]*>',
        teams.text,
    )
    assert roles.status_code == 200
    assert "角色列表" in roles.text
    assert "权限数" in roles.text
    assert users.status_code == 200
    for response in (users, roles, teams):
        assert "pagination-footer" in response.text
        assert "/static/js/app.js?v=6" in response.text
    assert legacy_role_alias.status_code == 200
    assert "角色列表" in legacy_role_alias.text


def test_team_role_takes_precedence_and_personal_role_is_fallback(rbac_client):
    """验证组角色优先、解除关联后回退个人角色且不复制关系。"""
    client, app, token, inherited_user_id, personal_user_id = rbac_client
    headers = {"X-CSRFToken": token}

    with session_scope(app.state.database.session_factory) as session:
        permission_ids = {
            item.code: item.id
            for item in session.scalars(select(PermissionItem)).all()
        }

    team_role_response = client.post(
        "/users/roles/create/",
        data={
            "csrf_token": token,
            "name": "生产节点只读",
            "description": "查看 Linux 节点状态",
            "permission_ids": [str(permission_ids["nodes.read"])],
        },
    )
    personal_role_response = client.post(
        "/users/roles/create/",
        data={
            "csrf_token": token,
            "name": "个人凭证只读",
            "description": "查看凭证清单",
            "permission_ids": [str(permission_ids["credentials.read"])],
        },
    )
    assert team_role_response.status_code == 303
    assert personal_role_response.status_code == 303
    with session_scope(app.state.database.session_factory) as session:
        team_role_id = session.scalar(
            select(Role.id).where(Role.name == "生产节点只读")
        )
        personal_role_id = session.scalar(
            select(Role.id).where(Role.name == "个人凭证只读")
        )
        session.execute(
            profile_roles.insert().values(
                user_id=personal_user_id,
                role_id=personal_role_id,
            )
        )
        session.execute(
            profile_permissions.insert().values(
                user_id=personal_user_id,
                permission_id=permission_ids["audit.read"],
            )
        )
        session.commit()

    team_response = client.post(
        "/users/teams/create/",
        data={
            "csrf_token": token,
            "name": "生产运维",
            "description": "生产环境值班人员",
            "role_ids": [str(team_role_id)],
        },
    )
    assert team_response.status_code == 303
    with session_scope(app.state.database.session_factory) as session:
        team_id = session.scalar(select(Team.id).where(Team.name == "生产运维"))

    add_members = client.post(
        "/api/users/teams/{}/members".format(team_id),
        headers=headers,
        json={
            "action": "add",
            "user_ids": [inherited_user_id, personal_user_id],
        },
    )
    assert add_members.status_code == 200, add_members.text
    member_page = client.get(
        "/api/users/teams/{}/members".format(team_id),
        params={"search": "ops-inherited", "per_page": 1},
    )
    assert member_page.status_code == 200
    assert member_page.json()["users"][0]["is_member"] is True
    assert member_page.json()["per_page"] == 1
    assert member_page.json()["total_count"] == 1
    assert member_page.json()["total_pages"] == 1
    assert member_page.json()["users"][0]["role_count"] == 1

    personal_user_row = client.get(
        "/users/", params={"search": "ops-personal"}
    )
    assert "生产节点只读" in personal_user_row.text
    assert "个人凭证只读" not in personal_user_row.text

    with session_scope(app.state.database.session_factory) as session:
        inherited_user = session.get(User, inherited_user_id)
        personal_user = session.get(User, personal_user_id)
        assert user_has_permission(session, inherited_user, "nodes", "read")
        assert not user_has_permission(session, personal_user, "credentials", "read")
        assert user_has_permission(session, personal_user, "nodes", "read")
        assert user_has_permission(session, personal_user, "audit", "read")
        assert session.scalar(
            select(profile_roles.c.role_id).where(
                profile_roles.c.user_id == inherited_user_id,
                profile_roles.c.role_id == team_role_id,
            )
        ) is None

    team_form = client.get("/users/teams/{}/edit/".format(team_id))
    assert team_form.status_code == 200
    assert 'data-picker-open="team-roles"' in team_form.text
    assert "teamRolePickerModal" in team_form.text
    detach_role = client.post(
        "/users/teams/{}/edit/".format(team_id),
        data={
            "csrf_token": token,
            "name": "生产运维",
            "description": "生产环境值班人员",
        },
    )
    assert detach_role.status_code == 303

    with session_scope(app.state.database.session_factory) as session:
        inherited_user = session.get(User, inherited_user_id)
        personal_user = session.get(User, personal_user_id)
        assert not user_has_permission(session, inherited_user, "nodes", "read")
        assert user_has_permission(session, personal_user, "credentials", "read")
        assert not user_has_permission(session, personal_user, "nodes", "read")
        assert user_has_permission(session, personal_user, "audit", "read")
        assert session.scalar(
            select(team_members.c.user_id).where(
                team_members.c.team_id == team_id,
                team_members.c.user_id == inherited_user_id,
            )
        ) == inherited_user_id
        assert session.scalar(
            select(profile_roles.c.role_id).where(
                profile_roles.c.user_id == personal_user_id,
                profile_roles.c.role_id == personal_role_id,
            )
        ) == personal_role_id


def test_user_and_team_forms_use_picker_cards_and_shared_pagination(rbac_client):
    """验证用户关系弹窗、彩条卡片及统一的列表分页信息。"""
    client, _app, token, _inherited_id, _personal_id = rbac_client

    role_response = client.post(
        "/users/roles/create/",
        data={"csrf_token": token, "name": "可搜索角色", "description": ""},
    )
    team_response = client.post(
        "/users/teams/create/",
        data={"csrf_token": token, "name": "可搜索用户组", "description": ""},
    )
    assert role_response.status_code == 303
    assert team_response.status_code == 303

    user_form = client.get("/users/create/")
    role_form = client.get("/users/roles/create/")
    team_form = client.get("/users/teams/create/")
    with session_scope(_app.state.database.session_factory) as session:
        role_id = session.scalar(select(Role.id).where(Role.name == "可搜索角色"))
        nodes_read_id = session.scalar(
            select(PermissionItem.id).where(PermissionItem.code == "nodes.read")
        )
    role_edit_form = client.get("/users/roles/{}/edit/".format(role_id))
    user_list = client.get("/users/")
    role_list = client.get("/users/roles/")
    team_list = client.get("/users/teams/")
    release_center = client.get("/releases/center/")
    release_history = client.get("/releases/")
    shared_script = client.get("/static/js/app.js?v=6")

    assert user_form.status_code == 200
    assert 'data-picker-open="user-roles"' in user_form.text
    assert 'data-picker-open="user-teams"' in user_form.text
    assert "userRolePickerModal" in user_form.text
    assert "userTeamPickerModal" in user_form.text
    assert "/static/css/user-form.css?v=1" in user_form.text
    assert "/static/css/rbac-matrix.css?v=1" in user_form.text
    assert "rbac-matrix-row" in user_form.text
    assert team_form.status_code == 200
    assert 'class="card team-form-section team-form-section--basic"' in team_form.text
    assert 'class="card team-form-section team-form-section--roles"' in team_form.text
    assert "teamRolePickerModal" in team_form.text
    assert "/static/css/team-form.css?v=1" in team_form.text
    for response in (role_form, role_edit_form):
        assert response.status_code == 200
        assert 'class="card role-form-section role-form-section--basic"' in response.text
        assert 'class="card role-form-section role-form-section--permissions"' in response.text
        assert "/static/css/role-form.css?v=1" in response.text
        assert "/static/css/rbac-matrix.css?v=1" in response.text
        assert "rbac-matrix-row" in response.text
    matrix_css = client.get("/static/css/rbac-matrix.css?v=1")
    role_css = client.get("/static/css/role-form.css?v=1")
    assert matrix_css.status_code == 200
    assert "grid-template-columns" in matrix_css.text
    assert role_css.status_code == 200
    assert "role-form-section--permissions" in role_css.text
    for section in (
        "user-form-section--basic",
        "user-form-section--roles",
        "user-form-section--teams",
        "user-form-section--permissions",
    ):
        assert section in user_form.text
    assert 'data-picker-limit="3"' in user_form.text
    assert 'name="role_ids"' in user_form.text
    assert 'data-picker-choice="user-roles"' in user_form.text
    assert 'name="team_ids"' in user_form.text
    assert 'data-picker-selection="user-teams"' in user_form.text
    assert 'data-picker-choice="team-roles"' in team_form.text
    for response in (user_list, role_list, team_list):
        assert response.status_code == 200
        assert "pagination-footer" in response.text
        assert "/static/js/app.js?v=6" in response.text
        assert "每页" in response.text
    assert "共 3 条，第 1 / 1 页" in user_list.text
    assert "共 1 条，第 1 / 1 页" in role_list.text
    assert "共 1 条，第 1 / 1 页" in team_list.text
    assert 'id="memberSearch" data-query-tags' in team_list.text
    assert 'id="memberPageSize"' in team_list.text
    assert release_center.status_code == 200
    assert 'id="releaseSearch" data-query-tags' in release_center.text
    assert release_history.status_code == 200
    assert 'id="historySearch" data-query-tags' in release_history.text
    assert shared_script.status_code == 200
    assert "input:not([type='hidden'])[name='search']" in shared_script.text

    create_direct_permission_user = client.post(
        "/users/create/",
        data={
            "csrf_token": token,
            "username": "matrix-direct-user",
            "email": "matrix-direct@example.test",
            "password1": "Qv8!Jr2#Lt6@",
            "password2": "Qv8!Jr2#Lt6@",
            "permission_ids": [str(nodes_read_id)],
        },
    )
    assert create_direct_permission_user.status_code == 303
    with session_scope(_app.state.database.session_factory) as session:
        direct_user = session.scalar(
            select(User).where(User.username == "matrix-direct-user")
        )
        assert user_has_permission(session, direct_user, "nodes", "read")


def test_team_role_picker_paginates_and_applies_search_on_enter(rbac_client):
    """验证用户组角色弹窗分页控件、查询脚本和紧凑输入字号。"""
    client, app, _token, _inherited_id, _personal_id = rbac_client
    with session_scope(app.state.database.session_factory) as session:
        with session.begin():
            admin_id = session.scalar(
                select(User.id).where(User.username == "rbac-admin")
            )
            session.add_all(
                [
                    Role(
                        name="Role {:02d}".format(index),
                        description="Nginx task",
                        created_by=admin_id,
                    )
                    for index in range(12)
                ]
            )

    team_form = client.get("/users/teams/create/")
    picker_script = client.get("/static/js/user-form.js?v=2")
    app_css = client.get("/static/css/app.css?v=14")

    assert team_form.status_code == 200
    assert team_form.text.count("data-picker-row data-picker-search-text=") == 12
    assert 'aria-label="每页角色数"' in team_form.text
    assert "data-picker-page-previous" in team_form.text
    assert "data-picker-page-next" in team_form.text
    assert "/static/js/user-form.js?v=2" in team_form.text
    assert picker_script.status_code == 200
    assert 'query.addEventListener("input"' not in picker_script.text
    assert 'query.addEventListener("keydown"' in picker_script.text
    assert 'event.key === ","' not in picker_script.text
    assert "terms.every" in picker_script.text
    assert "matchingRows.slice(firstIndex, firstIndex + pageSize)" in picker_script.text
    assert app_css.status_code == 200
    assert ".entity-picker-pagination-controls" in app_css.text
    assert "font-size: var(--fs-base);" in app_css.text
