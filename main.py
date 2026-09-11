import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel


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


def get_db_path() -> Path:
    return Path(os.environ.get("GOAL_MANAGER_DB_PATH", DEFAULT_DB_PATH))


def get_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(get_db_path())
    connection.row_factory = sqlite3.Row
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


def apply_migrations() -> None:
    with get_connection() as connection:
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

            connection.executescript(sql)
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


def row_to_user(row: sqlite3.Row) -> User:
    return User(**dict(row))


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


def write_user(
    connection: sqlite3.Connection, query: str, parameters: tuple[object, ...]
) -> sqlite3.Cursor:
    try:
        cursor = connection.execute(query, parameters)
        connection.commit()
        return cursor
    except sqlite3.IntegrityError as exc:
        detail = str(exc)
        if "UNIQUE constraint failed: users.email" in detail:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A user with this email already exists",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User data violates database constraints",
        ) from exc


@asynccontextmanager
async def lifespan(_: FastAPI):
    apply_migrations()
    yield


app = FastAPI(title="Goal Manager", lifespan=lifespan)


@app.get("/health")
def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/users", response_model=User, status_code=status.HTTP_201_CREATED)
def create_user(payload: UserCreate) -> User:
    timestamp = datetime.now(timezone.utc).isoformat()
    with get_connection() as connection:
        cursor = write_user(
            connection,
            """
            INSERT INTO users (name, email, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (payload.name, payload.email, timestamp, timestamp),
        )
        return row_to_user(get_user_or_404(connection, cursor.lastrowid))


@app.get("/users", response_model=list[User])
def list_users() -> list[User]:
    with get_connection() as connection:
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
    with get_connection() as connection:
        return row_to_user(get_user_or_404(connection, user_id))


@app.put("/users/{user_id}", response_model=User)
def update_user(user_id: int, payload: UserUpdate) -> User:
    with get_connection() as connection:
        cursor = write_user(
            connection,
            """
            UPDATE users
            SET name = ?, email = ?, updated_at = ?
            WHERE id = ?
            """,
            (payload.name, payload.email, datetime.now(timezone.utc).isoformat(), user_id),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        return row_to_user(get_user_or_404(connection, user_id))


@app.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: int) -> None:
    with get_connection() as connection:
        cursor = connection.execute("DELETE FROM users WHERE id = ?", (user_id,))
        connection.commit()
        if cursor.rowcount == 0:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")


def main() -> None:
    print("Run the FastAPI app with: fastapi dev main.py")


if __name__ == "__main__":
    main()
