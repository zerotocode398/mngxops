from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class UserListTagSearchTests(TestCase):
	def setUp(self):
		user_model = get_user_model()
		self.admin = user_model.objects.create_superuser(
			username="admin", email="admin@example.com", password="pass1234"
		)
		self.client.force_login(self.admin)

		self.alice = user_model.objects.create_user(
			username="alice", email="alice@example.com", password="pass1234"
		)
		self.bob = user_model.objects.create_user(
			username="bob", email="bob@example.com", password="pass1234"
		)

	def _user_ids(self, response):
		return {u.id for u in response.context["users"]}

	def test_search_multi_tags_use_and_logic(self):
		response = self.client.get(reverse("users:list"), {"search": "alice,alice"})
		self.assertEqual(response.status_code, 200)
		self.assertIn(self.alice.id, self._user_ids(response))
		self.assertNotIn(self.bob.id, self._user_ids(response))

	def test_search_multi_tags_unmatched_filters_out(self):
		response = self.client.get(reverse("users:list"), {"search": "alice,bob"})
		self.assertEqual(response.status_code, 200)
		self.assertSetEqual(self._user_ids(response), set())

	def test_search_supports_full_width_comma(self):
		response = self.client.get(reverse("users:list"), {"search": "alice，bob"})
		self.assertEqual(response.status_code, 200)
		self.assertSetEqual(self._user_ids(response), set())
