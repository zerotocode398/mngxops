"""发布服务层测试。"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.releases.models import generate_batch_number


class TestGenerateBatchNumber(TestCase):
    def test_batch_number_format(self):
        batch = generate_batch_number()
        self.assertIn("release-", batch)
        parts = batch.split("-")
        self.assertEqual(len(parts), 3)

    def test_batch_number_unique(self):
        batch1 = generate_batch_number()
        self.assertIn("release-", batch1)
        self.assertTrue(batch1.endswith("0001"))
