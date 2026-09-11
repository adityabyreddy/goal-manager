import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

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

    def test_update_returns_404_when_row_is_removed_before_commit(self) -> None:
        created = self.client.post(
            "/users", json={"name": "Transient User", "email": "transient@example.com"}
        )
        self.assertEqual(created.status_code, 201)

        with patch.object(main, "write_user", return_value=Mock(rowcount=0)):
            response = self.client.put(
                f"/users/{created.json()['id']}",
                json={"name": "Transient User", "email": "transient@example.com"},
            )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "User not found"})

    def test_create_get_list_update_and_delete_goal_scoped_to_user(self) -> None:
        first_user = self.client.post(
            "/users", json={"name": "Goal Owner", "email": "owner@example.com"}
        )
        second_user = self.client.post(
            "/users", json={"name": "Other User", "email": "other@example.com"}
        )
        self.assertEqual(first_user.status_code, 201)
        self.assertEqual(second_user.status_code, 201)

        first_user_id = first_user.json()["id"]
        second_user_id = second_user.json()["id"]
        payload = {
            "title": "Ship goal CRUD",
            "description": "Implement user-scoped goal APIs",
            "labels": ["backend", "api"],
            "start_date": "2026-09-11",
            "end_date": "2026-09-30",
            "priority": "high",
        }
        created = self.client.post(f"/users/{first_user_id}/goals", json=payload)
        self.assertEqual(created.status_code, 201)
        created_body = created.json()
        self.assertEqual(created_body["user_id"], first_user_id)
        self.assertEqual(created_body["labels"], ["backend", "api"])
        self.assertEqual(created_body["priority"], "high")

        goal_id = created_body["id"]
        listed = self.client.get(f"/users/{first_user_id}/goals")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()), 1)
        self.assertEqual(listed.json()[0]["id"], goal_id)

        fetched = self.client.get(f"/users/{first_user_id}/goals/{goal_id}")
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.json()["title"], "Ship goal CRUD")

        hidden = self.client.get(f"/users/{second_user_id}/goals/{goal_id}")
        self.assertEqual(hidden.status_code, 404)
        self.assertEqual(hidden.json(), {"detail": "Goal not found"})

        updated = self.client.put(
            f"/users/{first_user_id}/goals/{goal_id}",
            json={
                "title": "Ship goal APIs",
                "description": "Implement and verify goal CRUD",
                "labels": ["backend", "verified"],
                "start_date": "2026-09-12",
                "end_date": "2026-10-01",
                "priority": "medium",
            },
        )
        self.assertEqual(updated.status_code, 200)
        updated_body = updated.json()
        self.assertEqual(updated_body["title"], "Ship goal APIs")
        self.assertEqual(updated_body["labels"], ["backend", "verified"])
        self.assertEqual(updated_body["priority"], "medium")
        self.assertNotEqual(updated_body["updated_at"], created_body["updated_at"])

        deleted = self.client.delete(f"/users/{first_user_id}/goals/{goal_id}")
        self.assertEqual(deleted.status_code, 204)

        missing = self.client.get(f"/users/{first_user_id}/goals/{goal_id}")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json(), {"detail": "Goal not found"})

    def test_goal_endpoints_require_existing_user(self) -> None:
        payload = {
            "title": "Missing parent",
            "description": "Should fail",
            "labels": ["missing"],
            "start_date": "2026-09-11",
            "end_date": "2026-09-12",
            "priority": "low",
        }

        created = self.client.post("/users/999/goals", json=payload)
        self.assertEqual(created.status_code, 404)
        self.assertEqual(created.json(), {"detail": "User not found"})

        listed = self.client.get("/users/999/goals")
        self.assertEqual(listed.status_code, 404)
        self.assertEqual(listed.json(), {"detail": "User not found"})

    def test_update_and_delete_goal_return_404_for_missing_or_wrong_user(self) -> None:
        owner = self.client.post("/users", json={"name": "Owner", "email": "owner2@example.com"})
        other = self.client.post("/users", json={"name": "Other", "email": "other2@example.com"})
        self.assertEqual(owner.status_code, 201)
        self.assertEqual(other.status_code, 201)

        goal = self.client.post(
            f"/users/{owner.json()['id']}/goals",
            json={
                "title": "Scoped goal",
                "description": "Keep hidden",
                "labels": ["private"],
                "start_date": "2026-09-11",
                "end_date": "2026-09-12",
                "priority": "high",
            },
        )
        self.assertEqual(goal.status_code, 201)
        goal_id = goal.json()["id"]

        wrong_user_update = self.client.put(
            f"/users/{other.json()['id']}/goals/{goal_id}",
            json={
                "title": "Scoped goal",
                "description": "Keep hidden",
                "labels": ["private"],
                "start_date": "2026-09-11",
                "end_date": "2026-09-12",
                "priority": "high",
            },
        )
        self.assertEqual(wrong_user_update.status_code, 404)
        self.assertEqual(wrong_user_update.json(), {"detail": "Goal not found"})

        missing_delete = self.client.delete(f"/users/{owner.json()['id']}/goals/999")
        self.assertEqual(missing_delete.status_code, 404)
        self.assertEqual(missing_delete.json(), {"detail": "Goal not found"})


if __name__ == "__main__":
    unittest.main()
