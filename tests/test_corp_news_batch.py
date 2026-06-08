import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from gpt_researcher.corp.db import ClaimedTopic
from gpt_researcher.corp.news_batch import (
    context_to_text,
    normalize_tone,
    run_topic,
    sanitize_research_sources,
)
from gpt_researcher.utils.enum import Tone


class CorpNewsBatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_topic_uses_gpt_researcher_pipeline_and_stores_success(self):
        claim = ClaimedTopic(
            id=1,
            topic="daily news",
            prompt="오늘 주요 뉴스",
            report_type="research_report",
            tone="objective",
            run_id=uuid4(),
            report_id=10,
        )
        store = MagicMock()
        researcher = SimpleNamespace(
            conduct_research=AsyncMock(return_value=["context one", "context two"]),
            write_report=AsyncMock(return_value="# report"),
            get_source_urls=MagicMock(return_value=["postgres-news://article/1"]),
            get_research_sources=MagicMock(return_value=[{
                "url": "postgres-news://article/1",
                "title": "title",
                "source": "corp",
                "published_at": "2026-06-08T00:00:00",
                "raw_content": "x" * 500,
            }]),
            get_costs=MagicMock(return_value=1.25),
            get_step_costs=MagicMock(return_value={"research": 0.25, "report_writing": 1.0}),
        )

        with patch("gpt_researcher.corp.news_batch.build_researcher", return_value=researcher) as factory:
            await run_topic(store, claim)

        factory.assert_called_once()
        researcher.conduct_research.assert_awaited_once()
        researcher.write_report.assert_awaited_once()
        store.mark_success.assert_called_once()
        kwargs = store.mark_success.call_args.kwargs
        self.assertEqual(kwargs["report_markdown"], "# report")
        self.assertEqual(kwargs["context_text"], "context one\n\ncontext two")
        self.assertEqual(kwargs["source_urls"], ["postgres-news://article/1"])
        self.assertEqual(kwargs["research_sources"][0]["title"], "title")
        self.assertNotIn("raw_content", kwargs["research_sources"][0])
        self.assertEqual(kwargs["costs"]["total"], 1.25)

    async def test_run_topic_stores_failure(self):
        claim = ClaimedTopic(
            id=1,
            topic="daily news",
            prompt="오늘 주요 뉴스",
            report_type="research_report",
            tone="objective",
            run_id=uuid4(),
            report_id=10,
        )
        store = MagicMock()
        researcher = SimpleNamespace(
            conduct_research=AsyncMock(side_effect=RuntimeError("boom")),
        )

        with patch("gpt_researcher.corp.news_batch.build_researcher", return_value=researcher):
            with self.assertRaises(RuntimeError):
                await run_topic(store, claim)

        store.mark_failure.assert_called_once()
        self.assertIn("boom", store.mark_failure.call_args.args[1])


class CorpNewsBatchHelperTests(unittest.TestCase):
    def test_context_to_text_joins_list_context(self):
        self.assertEqual(context_to_text(["a", "b"]), "a\n\nb")

    def test_sanitize_research_sources_drops_raw_content(self):
        published_at = datetime(2026, 6, 8, tzinfo=timezone.utc)
        self.assertEqual(
            sanitize_research_sources([{
                "url": "u",
                "raw_content": "secret",
                "title": "t",
                "published_at": published_at,
            }]),
            [{
                "url": "u",
                "title": "t",
                "source": None,
                "published_at": "2026-06-08T00:00:00+00:00",
            }],
        )

    def test_normalize_tone_accepts_short_and_enum_values(self):
        self.assertEqual(normalize_tone("formal"), Tone.Formal)
        self.assertEqual(normalize_tone(Tone.Analytical.value), Tone.Analytical)
        self.assertEqual(normalize_tone("unknown-tone"), Tone.Objective)


if __name__ == "__main__":
    unittest.main()
