import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

import main


class GoalUiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test.db")
        self.client = TestClient(main.create_app(Path(self.db_path)))
        self.client.__enter__()

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        self.temp_dir.cleanup()

    def _create_user(self, name: str = "UI User", email: str = "ui@example.com") -> int:
        response = self.client.post("/users", json={"name": name, "email": email})
        self.assertEqual(response.status_code, 201)
        return response.json()["id"]

    def test_dashboard_lists_goals_with_status(self) -> None:
        user_id = self._create_user()
        today = date.today()
        self.client.post(
            f"/users/{user_id}/goals",
            json={
                "title": "Current goal",
                "description": "In progress",
                "labels": ["ui"],
                "start_date": (today - timedelta(days=1)).isoformat(),
                "end_date": (today + timedelta(days=1)).isoformat(),
                "priority": "medium",
            },
        )
        self.client.post(
            f"/users/{user_id}/goals",
            json={
                "title": "Old goal",
                "description": "Already ended",
                "labels": ["ui"],
                "start_date": (today - timedelta(days=4)).isoformat(),
                "end_date": (today - timedelta(days=2)).isoformat(),
                "priority": "low",
            },
        )

        response = self.client.get("/ui/dashboard")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Current goal", response.text)
        self.assertIn("Old goal", response.text)
        self.assertIn("In Progress", response.text)
        self.assertIn("Overdue", response.text)

    def test_create_goal_form_and_submission(self) -> None:
        user_id = self._create_user(name="Creator", email="creator@example.com")
        form_response = self.client.get(f"/ui/users/{user_id}/goals/new")
        self.assertEqual(form_response.status_code, 200)
        self.assertIn("Create goal for Creator", form_response.text)

        submit = self.client.post(
            f"/ui/users/{user_id}/goals/new",
            data={
                "title": "Goal from form",
                "description": "Created via html form",
                "labels": "frontend, ui",
                "start_date": "2026-09-11",
                "end_date": "2026-09-20",
                "priority": "high",
            },
            follow_redirects=False,
        )
        self.assertEqual(submit.status_code, 303)
        detail_path = submit.headers["location"]
        self.assertIn(f"/ui/users/{user_id}/goals/", detail_path)

        detail = self.client.get(detail_path)
        self.assertEqual(detail.status_code, 200)
        self.assertIn("Goal from form", detail.text)
        self.assertIn("frontend, ui", detail.text)

    def test_edit_goal_form_and_submission(self) -> None:
        user_id = self._create_user(name="Editor", email="editor@example.com")
        created = self.client.post(
            f"/users/{user_id}/goals",
            json={
                "title": "Before update",
                "description": "Original description",
                "labels": ["edit"],
                "start_date": "2026-09-11",
                "end_date": "2026-09-12",
                "priority": "low",
            },
        )
        self.assertEqual(created.status_code, 201)
        goal_id = created.json()["id"]

        edit_page = self.client.get(f"/ui/users/{user_id}/goals/{goal_id}/edit")
        self.assertEqual(edit_page.status_code, 200)
        self.assertIn("Before update", edit_page.text)

        updated = self.client.post(
            f"/ui/users/{user_id}/goals/{goal_id}/edit",
            data={
                "title": "After update",
                "description": "Updated description",
                "labels": "edit, done",
                "start_date": "2026-09-11",
                "end_date": "2026-09-20",
                "priority": "medium",
            },
            follow_redirects=False,
        )
        self.assertEqual(updated.status_code, 303)
        self.assertTrue(
            updated.headers["location"].endswith(f"/ui/users/{user_id}/goals/{goal_id}")
        )

        detail = self.client.get(updated.headers["location"])
        self.assertEqual(detail.status_code, 200)
        self.assertIn("After update", detail.text)
        self.assertIn("edit, done", detail.text)

    def test_create_goal_submission_shows_validation_error(self) -> None:
        user_id = self._create_user(name="Invalid Creator", email="invalid-creator@example.com")
        response = self.client.post(
            f"/ui/users/{user_id}/goals/new",
            data={
                "title": "Bad date range",
                "description": "should fail",
                "labels": "ui",
                "start_date": "2026-09-20",
                "end_date": "2026-09-11",
                "priority": "high",
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("end_date must be on or after start_date", response.text)
        self.assertIn("Create goal for Invalid Creator", response.text)

    def test_edit_goal_submission_shows_validation_error(self) -> None:
        user_id = self._create_user(name="Invalid Editor", email="invalid-editor@example.com")
        created = self.client.post(
            f"/users/{user_id}/goals",
            json={
                "title": "Stable goal",
                "description": "original",
                "labels": ["stable"],
                "start_date": "2026-09-11",
                "end_date": "2026-09-12",
                "priority": "medium",
            },
        )
        self.assertEqual(created.status_code, 201)
        goal_id = created.json()["id"]

        response = self.client.post(
            f"/ui/users/{user_id}/goals/{goal_id}/edit",
            data={
                "title": "Stable goal",
                "description": "edited",
                "labels": "stable",
                "start_date": "2026-09-20",
                "end_date": "2026-09-11",
                "priority": "medium",
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("end_date must be on or after start_date", response.text)
        self.assertIn("Edit goal for Invalid Editor", response.text)


if __name__ == "__main__":
    unittest.main()
