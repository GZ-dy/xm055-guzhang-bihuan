from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.input_collection.message_registry import (
    MessageAssociationError,
    complete_message_event,
    resolve_message_event,
)


class MessageRegistryTests(unittest.TestCase):
    def test_duplicate_event_returns_its_own_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "events.sqlite3"
            first = self._resolve(database, event_key="bot:msg-1", message_id="msg-1", proposed_case_id="case-1")
            outcome = {"case_id": "case-1", "result": {"status": "need_review"}, "error": None}
            complete_message_event(database, event_key="bot:msg-1", case_id="case-1", outcome=outcome)

            duplicate = self._resolve(
                database,
                event_key="bot:msg-1",
                message_id="msg-1",
                proposed_case_id="case-other",
            )

            self.assertEqual(first["association"], "new")
            self.assertEqual(duplicate["case_id"], "case-1")
            self.assertEqual(duplicate["association"], "duplicate")
            self.assertEqual(duplicate["duplicate_outcome"], outcome)

    def test_explicit_supplement_appends_to_same_case(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "events.sqlite3"
            self._resolve(database, event_key="bot:msg-1", message_id="msg-1", proposed_case_id="case-1")

            supplement = self._resolve(
                database,
                event_key="bot:msg-2",
                message_id="msg-2",
                proposed_case_id="case-2",
                explicit_case_id="case-1",
                content="恢复时间：2026-08-12 11:35",
            )

            self.assertEqual(supplement["case_id"], "case-1")
            self.assertEqual(supplement["association"], "explicit_case_id")
            self.assertEqual(
                supplement["combined_text"],
                "系统：BOMC\n恢复时间：2026-08-12 11:35",
            )
            self.assertEqual([item["message_id"] for item in supplement["messages"]], ["msg-1", "msg-2"])

    def test_incomplete_supplement_retries_instead_of_returning_root_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "events.sqlite3"
            self._resolve(database, event_key="bot:msg-1", message_id="msg-1", proposed_case_id="case-1")
            complete_message_event(
                database,
                event_key="bot:msg-1",
                case_id="case-1",
                outcome={"case_id": "case-1", "result": {"status": "need_review"}},
            )
            self._resolve(
                database,
                event_key="bot:msg-2",
                message_id="msg-2",
                proposed_case_id="case-2",
                explicit_case_id="case-1",
                content="恢复时间：2026-08-12 11:35",
            )

            replay = self._resolve(
                database,
                event_key="bot:msg-2",
                message_id="msg-2",
                proposed_case_id="case-other",
                explicit_case_id="case-1",
                content="恢复时间：2026-08-12 11:35",
            )

            self.assertEqual(replay["case_id"], "case-1")
            self.assertEqual(replay["association"], "retry")
            self.assertIsNone(replay["duplicate_outcome"])

    def test_event_key_conflict_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "events.sqlite3"
            self._resolve(database, event_key="bot:msg-1", message_id="msg-1", proposed_case_id="case-1")

            with self.assertRaisesRegex(MessageAssociationError, "event_key conflicts"):
                self._resolve(
                    database,
                    event_key="bot:msg-1",
                    message_id="msg-1",
                    proposed_case_id="case-2",
                    raw_content="系统：另一个系统",
                )

    def test_quoted_message_links_only_inside_same_conversation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "events.sqlite3"
            self._resolve(database, event_key="bot:msg-1", message_id="msg-1", proposed_case_id="case-1")

            quoted = self._resolve(
                database,
                event_key="bot:msg-2",
                message_id="msg-2",
                proposed_case_id="case-2",
                quote_message_id="msg-1",
                content="故障原因：CAS服务异常",
            )

            self.assertEqual(quoted["case_id"], "case-1")
            self.assertEqual(quoted["association"], "quoted_message")

            with self.assertRaisesRegex(MessageAssociationError, "another conversation"):
                self._resolve(
                    database,
                    event_key="bot:msg-3",
                    message_id="msg-3",
                    proposed_case_id="case-3",
                    explicit_case_id="case-1",
                    conversation_key="bot:group:other",
                    content="恢复时间：2026-08-12 11:35",
                )

    @staticmethod
    def _resolve(
        database: Path,
        *,
        event_key: str,
        message_id: str,
        proposed_case_id: str,
        content: str = "系统：BOMC",
        raw_content: str = "",
        explicit_case_id: str = "",
        quote_message_id: str = "",
        quoted_text: str = "",
        conversation_key: str = "bot:group:chat-1",
    ):
        return resolve_message_event(
            database,
            event_key=event_key,
            message_id=message_id,
            conversation_key=conversation_key,
            sender_key="wecom_userid:user-1",
            content=content,
            raw_content=raw_content or content,
            proposed_case_id=proposed_case_id,
            explicit_case_id=explicit_case_id,
            quote_message_id=quote_message_id,
            quoted_text=quoted_text,
            received_at="2026-08-12T11:00:00",
        )


if __name__ == "__main__":
    unittest.main()
