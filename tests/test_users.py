import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import main


class UserApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test.db")
        self.client = TestClient(main.create_app(Path(self.db_path)))
        self.client.__enter__()

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        self.temp_dir.cleanup()

    def test_healthcheck(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

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
        updated_body = updated.json()
        self.assertEqual(updated_body["name"], "Ada Byron")
        self.assertEqual(updated_body["email"], "ada.byron@example.com")
        self.assertNotEqual(updated_body["updated_at"], created_body["updated_at"])

        refetched = self.client.get(f"/users/{user_id}")
        self.assertEqual(refetched.status_code, 200)
        self.assertEqual(refetched.json()["email"], "ada.byron@example.com")

        deleted = self.client.delete(f"/users/{user_id}")
        self.assertEqual(deleted.status_code, 204)

        listed_after_delete = self.client.get("/users")
        self.assertEqual(listed_after_delete.status_code, 200)
        self.assertEqual(listed_after_delete.json(), [])

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
        self.assertEqual(
            duplicate.json(), {"detail": "A user with this email already exists"}
        )

    def test_update_user_rejects_duplicate_email(self) -> None:
        first = self.client.post(
            "/users", json={"name": "User One", "email": "one@example.com"}
        )
        second = self.client.post(
            "/users", json={"name": "User Two", "email": "two@example.com"}
        )
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)

        duplicate_update = self.client.put(
            f"/users/{second.json()['id']}",
            json={"name": "User Two", "email": "one@example.com"},
        )
        self.assertEqual(duplicate_update.status_code, 409)
        self.assertEqual(
            duplicate_update.json(), {"detail": "A user with this email already exists"}
        )

    def test_missing_user_update_and_delete_return_404(self) -> None:
        missing_update = self.client.put(
            "/users/999",
            json={"name": "Missing User", "email": "missing@example.com"},
        )
        self.assertEqual(missing_update.status_code, 404)
        self.assertEqual(missing_update.json(), {"detail": "User not found"})

        missing_delete = self.client.delete("/users/999")
        self.assertEqual(missing_delete.status_code, 404)
        self.assertEqual(missing_delete.json(), {"detail": "User not found"})


if __name__ == "__main__":
    unittest.main()
