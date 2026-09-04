"""nginx_service 服务层测试（pytest 风格示例）"""

import pytest
from django.utils import timezone
from freezegun import freeze_time

from apps.nginx_service.services import generate_service_batch_number
from apps.releases.models import TaskCenterTask


@pytest.mark.django_db
class TestGenerateServiceBatchNumber:
    """批次号生成逻辑"""

    def test_first_batch_of_day(self, admin_user):
        """当天第一条批次从 0001 开始"""
        batch = generate_service_batch_number()
        today = timezone.now().strftime("%y%m%d")
        assert batch == f"OP-{today}-0001"

    def test_sequential_increment(self, admin_user):
        """连续调用批次号递增（每次创建任务后才能递增）"""
        today = timezone.now().strftime("%y%m%d")
        batch1 = generate_service_batch_number()
        assert batch1 == f"OP-{today}-0001"
        TaskCenterTask.objects.create(
            operation_type="nginx_service_control",
            source_batch=batch1,
            trigger_user=admin_user,
        )
        batch2 = generate_service_batch_number()
        assert batch2 == f"OP-{today}-0002"

    def test_only_counts_service_control_tasks(self, admin_user):
        """仅统计启停类任务，忽略其他类型"""
        TaskCenterTask.objects.create(
            operation_type="nginx_upgrade",
            source_batch="OP-260101-0099",
            trigger_user=admin_user,
        )
        batch = generate_service_batch_number()
        today = timezone.now().strftime("%y%m%d")
        assert batch == f"OP-{today}-0001"

    def test_respects_existing_max_seq(self, admin_user):
        """已有当天批次时延续最大序号"""
        today = timezone.now().strftime("%y%m%d")
        TaskCenterTask.objects.create(
            operation_type="nginx_service_control",
            source_batch=f"OP-{today}-0042",
            trigger_user=admin_user,
        )
        batch = generate_service_batch_number()
        assert batch == f"OP-{today}-0043"

    @freeze_time("2026-01-15 12:00:00")
    def test_cross_day_resets_sequence(self, admin_user):
        """跨天后序号从 0001 重新开始"""
        TaskCenterTask.objects.create(
            operation_type="nginx_service_control",
            source_batch="OP-260114-0099",
            trigger_user=admin_user,
        )
        batch = generate_service_batch_number()
        assert batch == "OP-260115-0001"
