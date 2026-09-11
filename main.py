import os
import json
import sqlite3
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, ValidationError, model_validator


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = BASE_DIR / "goal_manager.db"
CHANGELOG_PATH = BASE_DIR / "migrations" / "db.changelog.sql"
CHANGELOG_FILENAME = "migrations/db.changelog.sql"


class UserCreate(BaseModel):
    name: str
    email: str


class UserUpdate(BaseModel):
    name: str
    email: str


class User(UserCreate):
    id: int
    created_at: str
    updated_at: str


class GoalPayload(BaseModel):
    title: str
    description: str
    labels: list[str]
    start_date: date
    end_date: date
    priority: str

    @model_validator(mode="after")
    def validate_date_range(self) -> "GoalPayload":
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class GoalCreate(GoalPayload):
    pass


class GoalUpdate(GoalPayload):
    pass


class Goal(GoalPayload):
    id: int
    user_id: int
    created_at: str
    updated_at: str


def get_db_path() -> Path:
    return Path(os.environ.get("GOAL_MANAGER_DB_PATH", DEFAULT_DB_PATH))


def resolve_db_path(database_path: Optional[Path] = None) -> Path:
    if database_path is not None:
        return Path(database_path)
    return get_db_path()


def get_connection(database_path: Path) -> sqlite3.Connection:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def parse_changesets(changelog_path: Path) -> list[tuple[str, str, str]]:
    changesets: list[tuple[str, str, str]] = []
    current_author = None
    current_id = None
    current_lines: list[str] = []

    for raw_line in changelog_path.read_text(encoding="utf-8").splitlines():
        stripped_line = raw_line.strip()
        if stripped_line.startswith("--changeset "):
            if current_author is not None and current_id is not None:
                changesets.append(
                    (current_author, current_id, "\n".join(current_lines).strip())
                )
            author_and_id = stripped_line.removeprefix("--changeset ").strip()
            current_author, current_id = author_and_id.split(":", maxsplit=1)
            current_lines = []
            continue
        if stripped_line.startswith("--rollback "):
            continue
        if stripped_line.startswith("--"):
            continue
        current_lines.append(raw_line)

    if current_author is not None and current_id is not None:
        changesets.append((current_author, current_id, "\n".join(current_lines).strip()))

    return changesets


def execute_changeset(connection: sqlite3.Connection, sql: str) -> None:
    for statement in sql.split(";"):
        normalized_statement = statement.strip()
        if normalized_statement:
            connection.execute(normalized_statement)


def apply_migrations(database_path: Path) -> None:
    with get_connection(database_path) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS databasechangelog (
                    id TEXT NOT NULL,
                    author TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    dateexecuted TEXT NOT NULL,
                    orderexecuted INTEGER NOT NULL,
                    exectype TEXT NOT NULL,
                    PRIMARY KEY (id, author, filename)
                )
                """
            )
            applied_changes = {
                (row["author"], row["id"], row["filename"])
                for row in connection.execute(
                    "SELECT author, id, filename FROM databasechangelog"
                )
            }
            next_order = connection.execute(
                "SELECT COALESCE(MAX(orderexecuted), 0) FROM databasechangelog"
            ).fetchone()[0]

            for author, change_id, sql in parse_changesets(CHANGELOG_PATH):
                key = (author, change_id, CHANGELOG_FILENAME)
                if key in applied_changes or not sql:
                    continue

                execute_changeset(connection, sql)
                next_order += 1
                connection.execute(
                    """
                    INSERT INTO databasechangelog (
                        id, author, filename, dateexecuted, orderexecuted, exectype
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        change_id,
                        author,
                        CHANGELOG_FILENAME,
                        datetime.now(timezone.utc).isoformat(),
                        next_order,
                        "EXECUTED",
                    ),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def row_to_user(row: sqlite3.Row) -> User:
    return User(**dict(row))


def row_to_goal(row: sqlite3.Row) -> Goal:
    goal = dict(row)
    try:
        goal["labels"] = json.loads(goal["labels"])
        return Goal(**goal)
    except (TypeError, ValidationError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Stored goal data is invalid",
        ) from exc


def get_user_or_404(connection: sqlite3.Connection, user_id: int) -> sqlite3.Row:
    row = connection.execute(
        """
        SELECT id, name, email, created_at, updated_at
        FROM users
        WHERE id = ?
        """,
        (user_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return row


def get_goal_or_404(
    connection: sqlite3.Connection, user_id: int, goal_id: int
) -> sqlite3.Row:
    row = connection.execute(
        """
        SELECT id, user_id, title, description, labels, start_date, end_date, priority,
               created_at, updated_at
        FROM goals
        WHERE id = ? AND user_id = ?
        """,
        (goal_id, user_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Goal not found")
    return row


def raise_for_integrity_error(exc: sqlite3.IntegrityError) -> None:
    detail = str(exc)
    if "UNIQUE constraint failed: users.email" in detail:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists",
        ) from exc
    if "FOREIGN KEY constraint failed" in detail:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        ) from exc
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Request data violates database constraints",
    ) from exc


def write_user(
    connection: sqlite3.Connection, query: str, parameters: tuple[object, ...]
) -> sqlite3.Cursor:
    try:
        return connection.execute(query, parameters)
    except sqlite3.IntegrityError as exc:
        raise_for_integrity_error(exc)


def create_app(database_path: Optional[Path] = None) -> FastAPI:
    resolved_db_path = resolve_db_path(database_path)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        apply_migrations(resolved_db_path)
        yield

    app = FastAPI(title="Goal Manager", lifespan=lifespan)

    def open_connection() -> sqlite3.Connection:
        return get_connection(resolved_db_path)

    @app.get("/health")
    def healthcheck() -> dict[str, str]:
        return {"status": "ok"}


    @app.post("/users", response_model=User, status_code=status.HTTP_201_CREATED)
    def create_user(payload: UserCreate) -> User:
        timestamp = datetime.now(timezone.utc).isoformat()
        with open_connection() as connection:
            cursor = write_user(
                connection,
                """
                INSERT INTO users (name, email, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (payload.name, payload.email, timestamp, timestamp),
            )
            connection.commit()
            return row_to_user(get_user_or_404(connection, cursor.lastrowid))


    @app.get("/users", response_model=list[User])
    def list_users() -> list[User]:
        with open_connection() as connection:
            rows = connection.execute(
                """
                SELECT id, name, email, created_at, updated_at
                FROM users
                ORDER BY id
                """
            ).fetchall()
            return [row_to_user(row) for row in rows]


    @app.get("/users/{user_id}", response_model=User)
    def get_user(user_id: int) -> User:
        with open_connection() as connection:
            return row_to_user(get_user_or_404(connection, user_id))


    @app.put("/users/{user_id}", response_model=User)
    def update_user(user_id: int, payload: UserUpdate) -> User:
        with open_connection() as connection:
            cursor = write_user(
                connection,
                """
                UPDATE users
                SET name = ?, email = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    payload.name,
                    payload.email,
                    datetime.now(timezone.utc).isoformat(),
                    user_id,
                ),
            )
            if cursor.rowcount == 0:
                connection.rollback()
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
                )
            connection.commit()
            return row_to_user(get_user_or_404(connection, user_id))


    @app.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_user(user_id: int) -> None:
        with open_connection() as connection:
            cursor = connection.execute("DELETE FROM users WHERE id = ?", (user_id,))
            if cursor.rowcount == 0:
                connection.rollback()
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
                )
            connection.commit()

    @app.post("/users/{user_id}/goals", response_model=Goal, status_code=status.HTTP_201_CREATED)
    def create_goal(user_id: int, payload: GoalCreate) -> Goal:
        timestamp = datetime.now(timezone.utc).isoformat()
        with open_connection() as connection:
            get_user_or_404(connection, user_id)
            cursor = write_user(
                connection,
                """
                INSERT INTO goals (
                   user_id, title, description, labels, start_date, end_date, priority,
                   created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                   user_id,
                   payload.title,
                   payload.description,
                   json.dumps(payload.labels),
                   payload.start_date.isoformat(),
                   payload.end_date.isoformat(),
                   payload.priority,
                   timestamp,
                   timestamp,
                ),
            )
            connection.commit()
            return row_to_goal(get_goal_or_404(connection, user_id, cursor.lastrowid))

    @app.get("/users/{user_id}/goals", response_model=list[Goal])
    def list_goals(user_id: int) -> list[Goal]:
        with open_connection() as connection:
            get_user_or_404(connection, user_id)
            rows = connection.execute(
                """
                SELECT id, user_id, title, description, labels, start_date, end_date, priority,
                      created_at, updated_at
                FROM goals
                WHERE user_id = ?
                ORDER BY id
                """,
                (user_id,),
            ).fetchall()
            return [row_to_goal(row) for row in rows]

    @app.get("/users/{user_id}/goals/{goal_id}", response_model=Goal)
    def get_goal(user_id: int, goal_id: int) -> Goal:
        with open_connection() as connection:
            get_user_or_404(connection, user_id)
            return row_to_goal(get_goal_or_404(connection, user_id, goal_id))

    @app.put("/users/{user_id}/goals/{goal_id}", response_model=Goal)
    def update_goal(user_id: int, goal_id: int, payload: GoalUpdate) -> Goal:
        with open_connection() as connection:
            get_user_or_404(connection, user_id)
            cursor = write_user(
                connection,
                """
                UPDATE goals
                SET title = ?, description = ?, labels = ?, start_date = ?, end_date = ?,
                   priority = ?, updated_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (
                   payload.title,
                   payload.description,
                   json.dumps(payload.labels),
                   payload.start_date.isoformat(),
                   payload.end_date.isoformat(),
                   payload.priority,
                   datetime.now(timezone.utc).isoformat(),
                   goal_id,
                   user_id,
                ),
            )
            if cursor.rowcount == 0:
                connection.rollback()
                raise HTTPException(
                   status_code=status.HTTP_404_NOT_FOUND, detail="Goal not found"
                )
            connection.commit()
            return row_to_goal(get_goal_or_404(connection, user_id, goal_id))

    @app.delete("/users/{user_id}/goals/{goal_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_goal(user_id: int, goal_id: int) -> None:
        with open_connection() as connection:
            get_user_or_404(connection, user_id)
            cursor = connection.execute(
                "DELETE FROM goals WHERE id = ? AND user_id = ?",
                (goal_id, user_id),
            )
            if cursor.rowcount == 0:
                connection.rollback()
                raise HTTPException(
                   status_code=status.HTTP_404_NOT_FOUND, detail="Goal not found"
                )
            connection.commit()

    return app


app = create_app()


def main() -> None:
    print("Run the FastAPI app with: fastapi dev main.py")


if __name__ == "__main__":
    main()
