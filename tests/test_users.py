import os
import tempfile
import unittest

from fastapi.testclient import TestClient

import main


class UserApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test.db")
        os.environ["GOAL_MANAGER_DB_PATH"] = self.db_path
        self.client = TestClient(main.app)
        self.client.__enter__()

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        os.environ.pop("GOAL_MANAGER_DB_PATH", None)
        self.temp_dir.cleanup()

    def test_create_get_list_update_and_delete_user(self) -> None:
        created = self.client.post(
            "/users", json={"name": "Ada Lovelace", "email": "ada@example.com"}
        )
        self.assertEqual(created.status_code, 201)
        created_body = created.json()
        self.assertEqual(created_body["name"], "Ada Lovelace")
        self.assertEqual(created_body["email"], "ada@example.com")

        user_id = created_body["id"]
        fetched = self.client.get(f"/users/{user_id}")
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.json()["id"], user_id)

        listed = self.client.get("/users")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()), 1)

        updated = self.client.put(
            f"/users/{user_id}",
            json={"name": "Ada Byron", "email": "ada.byron@example.com"},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["name"], "Ada Byron")

        deleted = self.client.delete(f"/users/{user_id}")
        self.assertEqual(deleted.status_code, 204)

        missing = self.client.get(f"/users/{user_id}")
        self.assertEqual(missing.status_code, 404)

    def test_create_user_rejects_duplicate_email(self) -> None:
        first = self.client.post(
            "/users", json={"name": "Grace Hopper", "email": "grace@example.com"}
        )
        self.assertEqual(first.status_code, 201)

        duplicate = self.client.post(
            "/users", json={"name": "Rear Admiral Grace Hopper", "email": "grace@example.com"}
        )
        self.assertEqual(duplicate.status_code, 409)


if __name__ == "__main__":
    unittest.main()
