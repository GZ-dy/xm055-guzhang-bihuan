from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any


class MessageAssociationError(ValueError):
    pass


def resolve_message_event(
    database_path: Path,
    *,
    event_key: str,
    message_id: str,
    conversation_key: str,
    sender_key: str,
    content: str,
    proposed_case_id: str,
    raw_content: str = "",
    explicit_case_id: str = "",
    quote_message_id: str = "",
    quoted_text: str = "",
    received_at: str = "",
) -> dict[str, Any]:
    """Reserve one inbound message and resolve it to exactly one local case."""
    if not event_key.strip() or not message_id.strip():
        raise MessageAssociationError("message_id is required for persistent association")
    if not conversation_key.strip():
        raise MessageAssociationError("conversation_key is required for persistent association")

    database_path.parent.mkdir(parents=True, exist_ok=True)
    now = received_at or datetime.now().isoformat(timespec="seconds")
    normalized_content = _normalized(content)
    normalized_raw_content = _normalized(raw_content or content)
    if not normalized_content:
        raise MessageAssociationError("empty business content")

    with closing(sqlite3.connect(database_path, timeout=10)) as connection:
        connection.row_factory = sqlite3.Row
        _ensure_schema(connection)
        connection.commit()
        connection.execute("BEGIN IMMEDIATE")

        existing = connection.execute(
            """
            SELECT e.case_id, e.message_id, e.conversation_key, e.sender_key,
                   e.raw_content_hash, e.outcome_json, c.source_text
              FROM message_events e
              JOIN message_cases c ON c.case_id = e.case_id
             WHERE e.event_key = ?
            """,
            (event_key,),
        ).fetchone()
        if existing is not None:
            if (
                str(existing["message_id"]) != message_id
                or str(existing["conversation_key"]) != conversation_key
                or str(existing["sender_key"]) != sender_key
                or str(existing["raw_content_hash"]) != _content_hash(normalized_raw_content)
            ):
                raise MessageAssociationError("event_key conflicts with a different message")
            duplicate_outcome = _json_object(existing["outcome_json"])
            connection.commit()
            return {
                "event_key": event_key,
                "case_id": str(existing["case_id"]),
                "combined_text": str(existing["source_text"]),
                "messages": _case_messages(connection, str(existing["case_id"])),
                "association": "duplicate" if duplicate_outcome else "retry",
                "duplicate_outcome": duplicate_outcome,
            }

        case_id = proposed_case_id
        association = "new"
        if explicit_case_id:
            row = connection.execute(
                "SELECT conversation_key FROM message_cases WHERE case_id = ?",
                (explicit_case_id,),
            ).fetchone()
            if row is None:
                raise MessageAssociationError(f"unknown case_id: {explicit_case_id}")
            if str(row["conversation_key"]) != conversation_key:
                raise MessageAssociationError("case_id belongs to another conversation")
            case_id = explicit_case_id
            association = "explicit_case_id"
        elif quote_message_id.strip():
            matches = connection.execute(
                """
                SELECT DISTINCT case_id
                  FROM message_events
                 WHERE conversation_key = ? AND message_id = ?
                """,
                (conversation_key, quote_message_id.strip()),
            ).fetchall()
            if len(matches) > 1:
                raise MessageAssociationError("quoted message matches multiple cases; use 补充 <case_id>")
            if len(matches) == 1:
                case_id = str(matches[0]["case_id"])
                association = "quoted_message"
        elif quoted_text.strip():
            quoted_hash = _content_hash(quoted_text)
            matches = connection.execute(
                """
                SELECT DISTINCT case_id
                  FROM message_events
                 WHERE conversation_key = ? AND raw_content_hash = ?
                """,
                (conversation_key, quoted_hash),
            ).fetchall()
            if len(matches) > 1:
                raise MessageAssociationError("quoted message matches multiple cases; use 补充 <case_id>")
            if len(matches) == 1:
                case_id = str(matches[0]["case_id"])
                association = "quoted_text"

        case_row = connection.execute(
            "SELECT source_text FROM message_cases WHERE case_id = ?",
            (case_id,),
        ).fetchone()
        if case_row is None:
            combined_text = normalized_content
            connection.execute(
                """
                INSERT INTO message_cases(
                    case_id, conversation_key, sender_key, source_text,
                    status, latest_outcome_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'draft', '', ?, ?)
                """,
                (case_id, conversation_key, sender_key, combined_text, now, now),
            )
        else:
            combined_text = _append_message(str(case_row["source_text"]), normalized_content)
            connection.execute(
                "UPDATE message_cases SET source_text = ?, updated_at = ? WHERE case_id = ?",
                (combined_text, now, case_id),
            )

        connection.execute(
            """
            INSERT INTO message_events(
                event_key, message_id, conversation_key, sender_key,
                content, content_hash, raw_content, raw_content_hash,
                case_id, received_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_key,
                message_id,
                conversation_key,
                sender_key,
                normalized_content,
                _content_hash(normalized_content),
                normalized_raw_content,
                _content_hash(normalized_raw_content),
                case_id,
                now,
            ),
        )
        connection.commit()
        return {
            "event_key": event_key,
            "case_id": case_id,
            "combined_text": combined_text,
            "messages": _case_messages(connection, case_id),
            "association": association,
            "duplicate_outcome": None,
        }


def complete_message_event(
    database_path: Path,
    *,
    event_key: str,
    case_id: str,
    outcome: dict[str, Any],
) -> None:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(outcome, ensure_ascii=False, default=str)
    status = str((outcome.get("result") or {}).get("status") or "error")
    now = datetime.now().isoformat(timespec="seconds")
    with closing(sqlite3.connect(database_path, timeout=10)) as connection:
        _ensure_schema(connection)
        connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "UPDATE message_events SET outcome_json = ? WHERE event_key = ? AND case_id = ?",
            (payload, event_key, case_id),
        )
        connection.execute(
            """
            UPDATE message_cases
               SET status = ?, latest_outcome_json = ?, updated_at = ?
             WHERE case_id = ?
            """,
            (status, payload, now, case_id),
        )
        connection.commit()


def _ensure_schema(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS message_cases(
            case_id TEXT PRIMARY KEY,
            conversation_key TEXT NOT NULL,
            sender_key TEXT NOT NULL,
            source_text TEXT NOT NULL,
            status TEXT NOT NULL,
            latest_outcome_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS message_events(
            event_key TEXT PRIMARY KEY,
            message_id TEXT NOT NULL,
            conversation_key TEXT NOT NULL,
            sender_key TEXT NOT NULL,
            content TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            raw_content TEXT NOT NULL,
            raw_content_hash TEXT NOT NULL,
            case_id TEXT NOT NULL REFERENCES message_cases(case_id),
            received_at TEXT NOT NULL,
            outcome_json TEXT NOT NULL DEFAULT ''
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS message_events_quote_lookup
            ON message_events(conversation_key, message_id, raw_content_hash)
        """
    )


def _case_messages(connection: sqlite3.Connection, case_id: str) -> list[dict[str, str]]:
    rows = connection.execute(
        """
        SELECT event_key, message_id, conversation_key, sender_key,
               content, raw_content, received_at
          FROM message_events
         WHERE case_id = ?
         ORDER BY rowid
        """,
        (case_id,),
    ).fetchall()
    return [
        {
            "event_key": str(row["event_key"]),
            "message_id": str(row["message_id"]),
            "conversation_key": str(row["conversation_key"]),
            "sender_key": str(row["sender_key"]),
            "text": str(row["content"]),
            "raw_text": str(row["raw_content"]),
            "received_at": str(row["received_at"]),
        }
        for row in rows
    ]


def _normalized(value: str) -> str:
    return "\n".join(line.rstrip() for line in str(value or "").strip().splitlines()).strip()


def _append_message(existing: str, addition: str) -> str:
    return f"{existing.rstrip()}\n{addition.lstrip()}" if existing.strip() else addition


def _content_hash(value: str) -> str:
    return hashlib.sha256(_normalized(value).encode("utf-8")).hexdigest()


def _json_object(value: Any) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        payload = json.loads(str(value))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None
