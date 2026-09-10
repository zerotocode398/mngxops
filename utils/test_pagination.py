"""分页工具测试。"""

from django.test import TestCase, RequestFactory

from utils.pagination import PerPagePaginationMixin


class FakeView(PerPagePaginationMixin):
    def __init__(self, request):
        self.request = request
        self.object_list = list(range(50))

    def get_queryset(self):
        return self.object_list


class TestPerPagePaginationMixin(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_default_paginate_by(self):
        fake = FakeView(self.factory.get("/"))
        result = fake.get_paginate_by(None)
        self.assertEqual(result, 10)

    def test_valid_per_page(self):
        fake = FakeView(self.factory.get("/?per_page=20"))
        result = fake.get_paginate_by(None)
        self.assertEqual(result, 20)

    def test_valid_per_page_50(self):
        fake = FakeView(self.factory.get("/?per_page=50"))
        result = fake.get_paginate_by(None)
        self.assertEqual(result, 50)

    def test_valid_per_page_100(self):
        fake = FakeView(self.factory.get("/?per_page=100"))
        result = fake.get_paginate_by(None)
        self.assertEqual(result, 100)

    def test_invalid_per_page(self):
        fake = FakeView(self.factory.get("/?per_page=999"))
        result = fake.get_paginate_by(None)
        self.assertEqual(result, 10)

    def test_non_numeric(self):
        fake = FakeView(self.factory.get("/?per_page=abc"))
        result = fake.get_paginate_by(None)
        self.assertEqual(result, 10)

    def test_negative(self):
        fake = FakeView(self.factory.get("/?per_page=-1"))
        result = fake.get_paginate_by(None)
        self.assertEqual(result, 10)

    def test_zero(self):
        fake = FakeView(self.factory.get("/?per_page=0"))
        result = fake.get_paginate_by(None)
        self.assertEqual(result, 10)

    def test_per_page_options(self):
        fake = FakeView(self.factory.get("/"))
        self.assertIn(10, fake.per_page_options)
        self.assertIn(20, fake.per_page_options)
        self.assertIn(50, fake.per_page_options)
        self.assertIn(100, fake.per_page_options)
