"""验证配置绑定操作审计包含可识别的标签和节点信息。"""

from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import select

from ngxops.accounts.models import User
from ngxops.app import create_app
from ngxops.audit.models import AuditLog
from ngxops.audit.service import prepare_audit_session
from ngxops.config import get_settings
from ngxops.configs.models import BindingVersion, Config, ConfigBinding
from ngxops.database.migration_runner import upgrade_database
from ngxops.database.session import session_scope
from ngxops.nodes.models import Node


@pytest.fixture
def audit_session_factory(tmp_path):
    """创建迁移完成的隔离审计数据库。"""
    settings = replace(
        get_settings(),
        data_dir=tmp_path,
        resource_dir=Path.cwd(),
        debug=False,
        reload=False,
    )
    app = create_app(settings)
    upgrade_database(app.state.database.engine)
    return app.state.database.session_factory


def test_binding_audit_names_config_and_node(audit_session_factory):
    """创建绑定和版本时记录配置标签名、节点身份与版本号。"""
    with session_scope(audit_session_factory) as session:
        with session.begin():
            user = User(
                username="audit-admin",
                password="unused-test-password",
                is_superuser=True,
                is_active=True,
            )
            session.add(user)
            session.flush()
            user_id = user.id
            config = Config(name="grafana", created_by=user_id)
            node = Node(
                hostname="edge-proxy",
                ip="192.0.2.101",
                created_by=user_id,
            )
            session.add_all([config, node])
            session.flush()
            config_id = config.id
            node_id = node.id

    with session_scope(audit_session_factory) as session:
        prepare_audit_session(
            session,
            actor_id=user_id,
            username="audit-admin",
            ip="127.0.0.1",
        )
        binding = ConfigBinding(
            config_id=config_id,
            node_id=node_id,
            remote_path="/etc/nginx/conf.d/grafana.conf",
            content="server {};",
            current_version=1,
            sync_status="not_synced",
            source="manual",
            created_by=user_id,
        )
        session.add(binding)
        session.flush()
        session.add(
            BindingVersion(
                binding_id=binding.id,
                version=1,
                content=binding.content,
                remark="初始版本",
                created_by=user_id,
            )
        )
        session.commit()
        rows = session.scalars(
            select(AuditLog).where(
                AuditLog.module.in_(("配置绑定", "绑定版本"))
            )
        ).all()
        entries = [(row.module, row.action, row.detail) for row in rows]

    binding_log = next(row for row in entries if row[0] == "配置绑定")
    version_log = next(row for row in entries if row[0] == "绑定版本")
    assert binding_log[1] == "创建配置绑定"
    assert binding_log[2] == "配置「grafana」绑定到节点「edge-proxy (192.0.2.101)」"
    assert version_log[1] == "创建绑定版本"
    assert version_log[2] == "配置「grafana」在节点「edge-proxy (192.0.2.101)」创建绑定版本 V1"
