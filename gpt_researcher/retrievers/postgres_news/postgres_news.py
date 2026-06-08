"""PostgreSQL/pgvector news retriever for GPT Researcher."""

from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class PostgresNewsSearch:
    """Retrieve news articles from PostgreSQL using pgvector similarity search."""

    def __init__(self, query: str, headers=None, query_domains=None, **kwargs):
        self.query = query
        self.headers = headers or {}
        self.query_domains = query_domains or []
        self.kwargs = kwargs

        self.dsn = os.getenv("POSTGRES_NEWS_DSN") or os.getenv("PGVECTOR_CONNECTION_STRING")
        if not self.dsn:
            raise ValueError(
                "POSTGRES_NEWS_DSN is required for RETRIEVER=postgres_news "
                "(PGVECTOR_CONNECTION_STRING is accepted as a fallback)."
            )

        self.articles_table = os.getenv("POSTGRES_NEWS_ARTICLES_TABLE", "news_articles")
        self.embeddings_table = os.getenv("POSTGRES_NEWS_EMBEDDINGS_TABLE", "news_article_embeddings")
        self.article_id_column = os.getenv("POSTGRES_NEWS_ARTICLE_ID_COLUMN", "id")
        self.embedding_article_id_column = os.getenv("POSTGRES_NEWS_EMBEDDING_ARTICLE_ID_COLUMN", "article_id")
        self.embedding_column = os.getenv("POSTGRES_NEWS_EMBEDDING_COLUMN", "embedding")
        self.content_column = os.getenv("POSTGRES_NEWS_CONTENT_COLUMN", "content")
        self.title_column = os.getenv("POSTGRES_NEWS_TITLE_COLUMN", "title")
        self.url_column = os.getenv("POSTGRES_NEWS_URL_COLUMN", "url")
        self.published_at_column = os.getenv("POSTGRES_NEWS_PUBLISHED_AT_COLUMN", "published_at")
        self.source_column = os.getenv("POSTGRES_NEWS_SOURCE_COLUMN", "")
        self.connect_timeout = int(os.getenv("POSTGRES_NEWS_CONNECT_TIMEOUT", "10"))

    def search(self, max_results: int = 10):
        """Return news rows in GPT Researcher's retriever result format."""
        query_embedding = self._embed_query()
        rows = self._fetch_rows(self._vector_literal(query_embedding), max_results)
        return [self._format_result(row, index) for index, row in enumerate(rows, 1)]

    def _embed_query(self) -> list[float]:
        from gpt_researcher.config import Config
        from gpt_researcher.memory import Memory

        cfg = Config()
        embeddings = Memory(
            cfg.embedding_provider,
            cfg.embedding_model,
            **cfg.embedding_kwargs,
        ).get_embeddings()
        return list(embeddings.embed_query(self.query))

    def _fetch_rows(self, query_vector: str, max_results: int) -> list[dict[str, Any]]:
        import psycopg
        from psycopg import sql
        from psycopg.rows import dict_row

        select_source = (
            sql.SQL("a.{}").format(sql.Identifier(self.source_column))
            if self.source_column
            else sql.SQL("NULL")
        )
        select_published_at = (
            sql.SQL("a.{}").format(sql.Identifier(self.published_at_column))
            if self.published_at_column
            else sql.SQL("NULL")
        )
        distance_expr = sql.SQL("e.{} <=> %s::vector").format(
            sql.Identifier(self.embedding_column)
        )

        where_clauses = [
            sql.SQL("a.{} IS NOT NULL").format(sql.Identifier(self.content_column)),
            sql.SQL("btrim(a.{}) <> ''").format(sql.Identifier(self.content_column)),
            sql.SQL("e.{} IS NOT NULL").format(sql.Identifier(self.embedding_column)),
        ]
        params: list[Any] = [query_vector]

        domain_filter, domain_params = self._domain_filter_sql(sql)
        if domain_filter is not None:
            where_clauses.append(domain_filter)
            params.extend(domain_params)

        params.append(max_results)

        statement = sql.SQL(
            """
            SELECT
                a.{article_id} AS article_id,
                a.{title} AS title,
                a.{url} AS url,
                a.{content} AS raw_content,
                {published_at} AS published_at,
                {source} AS source,
                {distance} AS distance
            FROM {articles_table} AS a
            JOIN {embeddings_table} AS e
                ON e.{embedding_article_id} = a.{article_id}
            WHERE {where}
            ORDER BY distance ASC
            LIMIT %s
            """
        ).format(
            article_id=sql.Identifier(self.article_id_column),
            title=sql.Identifier(self.title_column),
            url=sql.Identifier(self.url_column),
            content=sql.Identifier(self.content_column),
            published_at=select_published_at,
            source=select_source,
            distance=distance_expr,
            articles_table=self._identifier(sql, self.articles_table),
            embeddings_table=self._identifier(sql, self.embeddings_table),
            embedding_article_id=sql.Identifier(self.embedding_article_id_column),
            where=sql.SQL(" AND ").join(where_clauses),
        )

        with psycopg.connect(
            self.dsn,
            connect_timeout=self.connect_timeout,
            row_factory=dict_row,
        ) as conn:
            self._try_register_vector(conn)
            with conn.cursor() as cursor:
                cursor.execute(statement, params)
                return list(cursor.fetchall())

    def _domain_filter_sql(self, sql_module):
        if not self.query_domains:
            return None, []

        clauses = []
        params = []
        for domain in self.query_domains:
            value = f"%{domain.strip()}%"
            if not domain.strip():
                continue

            domain_clauses = [
                sql_module.SQL("a.{} ILIKE %s").format(sql_module.Identifier(self.url_column)),
            ]
            params.append(value)

            if self.source_column:
                domain_clauses.append(
                    sql_module.SQL("a.{} ILIKE %s").format(sql_module.Identifier(self.source_column))
                )
                params.append(value)

            clauses.append(sql_module.SQL("(") + sql_module.SQL(" OR ").join(domain_clauses) + sql_module.SQL(")"))

        if not clauses:
            return None, []
        return sql_module.SQL("(") + sql_module.SQL(" OR ").join(clauses) + sql_module.SQL(")"), params

    @staticmethod
    def _identifier(sql_module, name: str):
        parts = [part.strip() for part in name.split(".") if part.strip()]
        if not parts:
            raise ValueError("PostgreSQL identifier cannot be empty")
        return sql_module.Identifier(*parts)

    @staticmethod
    def _try_register_vector(conn) -> None:
        try:
            from pgvector.psycopg import register_vector

            register_vector(conn)
        except Exception as exc:
            logger.debug("Could not register pgvector adapter; using vector literal casts: %s", exc)

    @staticmethod
    def _vector_literal(values: list[float]) -> str:
        if not values:
            raise ValueError("Embedding provider returned an empty query vector")
        return "[" + ",".join(f"{float(value):.10g}" for value in values) + "]"

    def _format_result(self, row: dict[str, Any], index: int) -> dict[str, Any]:
        raw_content = row.get("raw_content") or ""
        url = row.get("url") or f"postgres-news://article/{row.get('article_id') or index}"
        published_at = row.get("published_at")
        if hasattr(published_at, "isoformat"):
            published_at = published_at.isoformat()

        source = row.get("source") or self._source_from_url(url)
        title = row.get("title") or source or url
        distance = row.get("distance")

        result = {
            "href": url,
            "url": url,
            "title": title,
            "raw_content": raw_content,
            "body": raw_content[:1000],
            "published_at": published_at,
            "source": source,
        }
        if distance is not None:
            result["distance"] = float(distance)
            result["score"] = 1.0 / (1.0 + float(distance))
        return result

    @staticmethod
    def _source_from_url(url: str) -> str:
        parsed = urlparse(url)
        return parsed.netloc or "postgres_news"
