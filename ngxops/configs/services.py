"""实现配置绑定、版本快照和状态转换。"""

from datetime import datetime
from difflib import SequenceMatcher
from typing import Dict, List, Optional

from sqlalchemy import update
from sqlalchemy.orm import Session

from ngxops.configs.models import BindingVersion, ConfigBinding


PHYSICAL_DELETE_STATUSES = frozenset(("not_synced", "orphaned", "marked_deleted"))


def build_split_diff_rows(
    base_content: str,
    target_content: str,
) -> List[Dict[str, object]]:
    """将两段配置正文转换为带左右行号的差异行。"""
    base_lines = base_content.splitlines()
    target_lines = target_content.splitlines()
    matcher = SequenceMatcher(a=base_lines, b=target_lines)
    rows = []
    for tag, left_start, left_end, right_start, right_end in matcher.get_opcodes():
        if tag == "equal":
            for offset, line in enumerate(base_lines[left_start:left_end]):
                rows.append(
                    {
                        "type": "equal",
                        "left_no": left_start + offset + 1,
                        "left": line,
                        "right_no": right_start + offset + 1,
                        "right": line,
                    }
                )
            continue
        left_block = base_lines[left_start:left_end]
        right_block = target_lines[right_start:right_end]
        for offset in range(max(len(left_block), len(right_block))):
            has_left = offset < len(left_block)
            has_right = offset < len(right_block)
            rows.append(
                {
                    "type": tag,
                    "left_no": left_start + offset + 1 if has_left else "",
                    "left": left_block[offset] if has_left else "",
                    "right_no": right_start + offset + 1 if has_right else "",
                    "right": right_block[offset] if has_right else "",
                }
            )
    return rows


def save_binding_revision(
    db_session: Session,
    binding_id: int,
    expected_version: int,
    remote_path: str,
    content: str,
    remark: str,
    user_id: int,
) -> Optional[int]:
    """以版本号条件更新绑定并追加一条不可变版本快照。"""
    next_version = expected_version + 1
    result = db_session.execute(
        update(ConfigBinding)
        .where(
            ConfigBinding.id == binding_id,
            ConfigBinding.current_version == expected_version,
            ConfigBinding.sync_status != "marked_deleted",
        )
        .values(
            remote_path=remote_path,
            content=content,
            current_version=next_version,
            sync_status="modified",
            updated_at=datetime.utcnow(),
        )
    )
    if result.rowcount != 1:
        db_session.rollback()
        return None
    db_session.add(
        BindingVersion(
            binding_id=binding_id,
            version=next_version,
            content=content,
            remark=remark,
            created_by=user_id,
        )
    )
    db_session.commit()
    return next_version


def restore_binding_revision(
    db_session: Session,
    binding: ConfigBinding,
    version: BindingVersion,
    user_id: int,
) -> Optional[int]:
    """从历史版本创建新版本并将绑定状态设为本地已修改。"""
    next_version = binding.current_version + 1
    result = db_session.execute(
        update(ConfigBinding)
        .where(
            ConfigBinding.id == binding.id,
            ConfigBinding.current_version == binding.current_version,
            ConfigBinding.sync_status != "marked_deleted",
        )
        .values(
            content=version.content,
            current_version=next_version,
            sync_status="modified",
            updated_at=datetime.utcnow(),
        )
    )
    if result.rowcount != 1:
        db_session.rollback()
        return None
    db_session.add(
        BindingVersion(
            binding_id=binding.id,
            version=next_version,
            content=version.content,
            remark="恢复自 v{}".format(version.version),
            created_by=user_id,
        )
    )
    db_session.commit()
    return next_version


def remove_binding(db_session: Session, binding: ConfigBinding) -> str:
    """按 Nginx 可用状态和同步状态删除或标记一条绑定。"""
    if (
        binding.node.nginx_available is not True
        or binding.sync_status in PHYSICAL_DELETE_STATUSES
    ):
        db_session.delete(binding)
        db_session.commit()
        return "deleted"
    binding.sync_status = "marked_deleted"
    binding.updated_at = datetime.utcnow()
    db_session.commit()
    return "marked_deleted"


def restore_marked_binding(db_session: Session, binding: ConfigBinding) -> bool:
    """取消绑定的待删除状态并重置为尚未同步。"""
    result = db_session.execute(
        update(ConfigBinding)
        .where(
            ConfigBinding.id == binding.id,
            ConfigBinding.sync_status == "marked_deleted",
        )
        .values(sync_status="not_synced", updated_at=datetime.utcnow())
    )
    if result.rowcount != 1:
        db_session.rollback()
        return False
    db_session.commit()
    return True


def mark_node_bindings_orphaned(
    db_session: Session,
    node_id: int,
    updated_at: datetime,
) -> int:
    """将节点上未标记删除的配置绑定统一标记为远程已删除。"""
    result = db_session.execute(
        update(ConfigBinding)
        .where(
            ConfigBinding.node_id == node_id,
            ConfigBinding.sync_status.notin_(("orphaned", "marked_deleted")),
        )
        .values(sync_status="orphaned", updated_at=updated_at)
    )
    return max(result.rowcount or 0, 0)
