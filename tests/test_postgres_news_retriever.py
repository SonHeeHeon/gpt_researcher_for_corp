import os
import unittest
from unittest.mock import patch

from gpt_researcher.config import Config
from gpt_researcher.retrievers.postgres_news import PostgresNewsSearch


class PostgresNewsRetrieverTests(unittest.TestCase):
    def test_formats_pgvector_rows_for_gpt_researcher(self):
        captured = {}

        with patch.dict(os.environ, {"POSTGRES_NEWS_DSN": "postgresql://user:pass@host/db"}, clear=False):
            retriever = PostgresNewsSearch("semiconductor policy")

        retriever._embed_query = lambda: [0.1, 0.2, 0.3]

        def fake_fetch_rows(query_vector, max_results):
            captured["query_vector"] = query_vector
            captured["max_results"] = max_results
            return [{
                "article_id": 42,
                "title": "Chip subsidies expand",
                "url": "https://news.example/chips",
                "raw_content": "A" * 1200,
                "published_at": "2026-06-08T09:00:00",
                "source": "News Example",
                "distance": 0.25,
            }]

        retriever._fetch_rows = fake_fetch_rows

        results = retriever.search(max_results=3)

        self.assertEqual(captured["query_vector"], "[0.1,0.2,0.3]")
        self.assertEqual(captured["max_results"], 3)
        self.assertEqual(results[0]["url"], "https://news.example/chips")
        self.assertEqual(results[0]["href"], "https://news.example/chips")
        self.assertEqual(results[0]["title"], "Chip subsidies expand")
        self.assertEqual(results[0]["raw_content"], "A" * 1200)
        self.assertEqual(results[0]["body"], "A" * 1000)
        self.assertEqual(results[0]["source"], "News Example")
        self.assertEqual(results[0]["published_at"], "2026-06-08T09:00:00")
        self.assertAlmostEqual(results[0]["score"], 0.8)

    def test_invalid_retriever_does_not_fall_back_to_tavily(self):
        with patch.dict(os.environ, {"RETRIEVER": "not_a_real_retriever"}, clear=False):
            with self.assertRaisesRegex(ValueError, "Invalid retriever"):
                Config()


if __name__ == "__main__":
    unittest.main()
