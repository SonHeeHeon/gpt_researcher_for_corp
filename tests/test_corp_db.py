import sys
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

from gpt_researcher.corp.db import ClaimedTopic, CorpBatchStore, json_ready


class _SqlFragment(str):
    def format(self, *args, **kwargs):
        return self


class _FakeSql:
    @staticmethod
    def SQL(value):
        return _SqlFragment(value)

    @staticmethod
    def Identifier(*parts):
        return ".".join(parts)


class _Jsonb:
    def __init__(self, value):
        self.value = value


class _FakeCursor:
    def __init__(self, rows=None, one_rows=None):
        self.rows = rows or []
        self.one_rows = list(one_rows or [])
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, statement, params=None):
        self.calls.append((str(statement), params))

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.one_rows.pop(0)


class _FakeConnection:
    def __init__(self, cursor):
        self.cursor_obj = cursor

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def transaction(self):
        return self

    def cursor(self):
        return self.cursor_obj


def _patch_psycopg_modules():
    return patch.dict(
        sys.modules,
        {
            "psycopg": SimpleNamespace(sql=_FakeSql),
            "psycopg.rows": SimpleNamespace(dict_row=object()),
            "psycopg.types": SimpleNamespace(),
            "psycopg.types.json": SimpleNamespace(Jsonb=_Jsonb),
        },
    )


class CorpBatchStoreTests(unittest.TestCase):
    def test_uses_news_dsn_as_batch_dsn_fallback(self):
        with patch.dict("os.environ", {"POSTGRES_NEWS_DSN": "postgresql://news-db"}, clear=True):
            store = CorpBatchStore()

        self.assertEqual(store.dsn, "postgresql://news-db")
        self.assertEqual(store.schema, "corp")

    def test_claim_due_topics_creates_running_report_row(self):
        cursor = _FakeCursor(
            rows=[{
                "id": 7,
                "topic": "daily market news",
                "prompt": "오늘 시장 뉴스 요약",
                "report_type": "research_report",
                "tone": "objective",
            }],
            one_rows=[{"id": 77}],
        )
        connection = _FakeConnection(cursor)
        store = CorpBatchStore(
            dsn="postgresql://batch-db",
            connect_factory=lambda *args, **kwargs: connection,
        )
        run_id = UUID("00000000-0000-0000-0000-000000000001")

        with _patch_psycopg_modules(), patch("uuid.uuid4", return_value=run_id):
            claims = store.claim_due_topics(limit=1)

        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0].id, 7)
        self.assertEqual(claims[0].report_id, 77)
        self.assertEqual(claims[0].run_id, run_id)
        self.assertIn("FOR UPDATE SKIP LOCKED", cursor.calls[0][0])
        self.assertEqual(cursor.calls[0][1], (1,))
        self.assertIn("INSERT INTO", cursor.calls[1][0])
        self.assertEqual(cursor.calls[1][1], (7, run_id, "오늘 시장 뉴스 요약"))

    def test_limit_zero_does_not_connect(self):
        store = CorpBatchStore(
            dsn="postgresql://batch-db",
            connect_factory=lambda *args, **kwargs: self.fail("should not connect"),
        )

        self.assertEqual(store.claim_due_topics(limit=0), [])

    def test_mark_success_jsonb_values_are_json_ready(self):
        cursor = _FakeCursor()
        connection = _FakeConnection(cursor)
        store = CorpBatchStore(
            dsn="postgresql://batch-db",
            connect_factory=lambda *args, **kwargs: connection,
        )
        claim = ClaimedTopic(
            id=1,
            topic="topic",
            prompt="prompt",
            report_type="research_report",
            tone="objective",
            run_id=UUID("00000000-0000-0000-0000-000000000002"),
            report_id=2,
        )
        published_at = datetime(2026, 6, 8, tzinfo=timezone.utc)

        with _patch_psycopg_modules():
            store.mark_success(
                claim=claim,
                report_markdown="# report",
                context_text="context",
                source_urls=["postgres-news://article/1"],
                research_sources=[{"published_at": published_at}],
                costs={"run_id": claim.run_id},
            )

        update_params = cursor.calls[0][1]
        self.assertEqual(update_params[2].value, ["postgres-news://article/1"])
        self.assertEqual(update_params[3].value, [{"published_at": "2026-06-08T00:00:00+00:00"}])
        self.assertEqual(update_params[4].value, {"run_id": str(claim.run_id)})

    def test_json_ready_converts_nested_values(self):
        value = json_ready({"when": datetime(2026, 6, 8, tzinfo=timezone.utc)})

        self.assertEqual(value, {"when": "2026-06-08T00:00:00+00:00"})


if __name__ == "__main__":
    unittest.main()
