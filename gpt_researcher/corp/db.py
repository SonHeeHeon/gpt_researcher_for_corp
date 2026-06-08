"""PostgreSQL storage helpers for company batch runs."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ClaimedTopic:
    """A topic claimed for one batch run."""

    id: int
    topic: str
    prompt: str
    report_type: str
    tone: str
    run_id: uuid.UUID
    report_id: int


class CorpBatchStore:
    """Read due topics and persist report run results in PostgreSQL."""

    def __init__(
        self,
        dsn: str | None = None,
        schema: str | None = None,
        default_limit: int | None = None,
        connect_timeout: int | None = None,
        lock_timeout_seconds: int | None = None,
        connect_factory: Callable[..., Any] | None = None,
    ):
        self.dsn = (
            dsn
            or os.getenv("CORP_BATCH_DSN")
            or os.getenv("POSTGRES_NEWS_DSN")
            or os.getenv("PGVECTOR_CONNECTION_STRING")
        )
        if not self.dsn:
            raise ValueError(
                "CORP_BATCH_DSN is required for corp batch runs "
                "(POSTGRES_NEWS_DSN is accepted as a fallback)."
            )

        self.schema = schema or os.getenv("CORP_BATCH_SCHEMA", "corp")
        self.default_limit = (
            default_limit if default_limit is not None else int(os.getenv("CORP_BATCH_LIMIT", "10"))
        )
        self.connect_timeout = (
            connect_timeout if connect_timeout is not None else int(os.getenv("CORP_BATCH_CONNECT_TIMEOUT", "10"))
        )
        self.lock_timeout_seconds = (
            lock_timeout_seconds
            if lock_timeout_seconds is not None
            else int(os.getenv("CORP_BATCH_LOCK_TIMEOUT_SECONDS", "0"))
        )
        self._connect_factory = connect_factory

    def claim_due_topics(self, limit: int | None = None) -> list[ClaimedTopic]:
        """Claim due topics and create one running report row for each topic."""
        limit = self.default_limit if limit is None else int(limit)
        if limit <= 0:
            return []

        from psycopg import sql
        from psycopg.rows import dict_row

        claims: list[ClaimedTopic] = []
        with self._connect(row_factory=dict_row) as conn:
            with conn.transaction():
                with conn.cursor() as cursor:
                    if self.lock_timeout_seconds > 0:
                        cursor.execute("SET LOCAL lock_timeout = %s", (f"{self.lock_timeout_seconds}s",))

                    cursor.execute(
                        sql.SQL(
                            """
                            SELECT
                                t.id,
                                t.topic,
                                t.prompt,
                                COALESCE(NULLIF(t.report_type, ''), 'research_report') AS report_type,
                                COALESCE(NULLIF(t.tone, ''), 'objective') AS tone
                            FROM {topics} AS t
                            WHERE t.enabled IS TRUE
                              AND (t.next_run_at IS NULL OR t.next_run_at <= now())
                              AND NOT EXISTS (
                                  SELECT 1
                                  FROM {reports} AS r
                                  WHERE r.topic_id = t.id
                                    AND r.status = 'running'
                              )
                            ORDER BY COALESCE(t.next_run_at, t.created_at), t.id
                            FOR UPDATE SKIP LOCKED
                            LIMIT %s
                            """
                        ).format(
                            topics=self._table(sql, "research_topics"),
                            reports=self._table(sql, "research_reports"),
                        ),
                        (limit,),
                    )
                    rows = cursor.fetchall()

                    for row in rows:
                        run_id = uuid.uuid4()
                        cursor.execute(
                            sql.SQL(
                                """
                                INSERT INTO {reports}
                                    (topic_id, run_id, status, query, started_at)
                                VALUES
                                    (%s, %s, 'running', %s, now())
                                RETURNING id
                                """
                            ).format(reports=self._table(sql, "research_reports")),
                            (row["id"], run_id, row["prompt"]),
                        )
                        report_id = cursor.fetchone()["id"]
                        claims.append(
                            ClaimedTopic(
                                id=int(row["id"]),
                                topic=row["topic"],
                                prompt=row["prompt"],
                                report_type=row["report_type"],
                                tone=row["tone"],
                                run_id=run_id,
                                report_id=int(report_id),
                            )
                        )

        return claims

    def mark_success(
        self,
        claim: ClaimedTopic,
        report_markdown: str,
        context_text: str,
        source_urls: list[str],
        research_sources: list[dict[str, Any]],
        costs: dict[str, Any],
    ) -> None:
        """Persist a successful report run and update the topic last run time."""
        from psycopg import sql
        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            with conn.transaction():
                with conn.cursor() as cursor:
                    cursor.execute(
                        sql.SQL(
                            """
                            UPDATE {reports}
                            SET status = 'success',
                                report_markdown = %s,
                                context_text = %s,
                                source_urls = %s,
                                research_sources = %s,
                                costs = %s,
                                finished_at = now(),
                                error_message = NULL
                            WHERE id = %s
                              AND run_id = %s
                            """
                        ).format(reports=self._table(sql, "research_reports")),
                        (
                            report_markdown,
                            context_text,
                            Jsonb(json_ready(source_urls)),
                            Jsonb(json_ready(research_sources)),
                            Jsonb(json_ready(costs)),
                            claim.report_id,
                            claim.run_id,
                        ),
                    )
                    cursor.execute(
                        sql.SQL(
                            """
                            UPDATE {topics}
                            SET last_run_at = now(),
                                updated_at = now()
                            WHERE id = %s
                            """
                        ).format(topics=self._table(sql, "research_topics")),
                        (claim.id,),
                    )

    def mark_failure(self, claim: ClaimedTopic, error_message: str) -> None:
        """Persist a failed report run."""
        from psycopg import sql

        with self._connect() as conn:
            with conn.transaction():
                with conn.cursor() as cursor:
                    cursor.execute(
                        sql.SQL(
                            """
                            UPDATE {reports}
                            SET status = 'failed',
                                finished_at = now(),
                                error_message = %s
                            WHERE id = %s
                              AND run_id = %s
                            """
                        ).format(reports=self._table(sql, "research_reports")),
                        (error_message[:8000], claim.report_id, claim.run_id),
                    )
                    cursor.execute(
                        sql.SQL(
                            """
                            UPDATE {topics}
                            SET updated_at = now()
                            WHERE id = %s
                            """
                        ).format(topics=self._table(sql, "research_topics")),
                        (claim.id,),
                    )

    def initialize_schema(self) -> None:
        """Create the default corp batch schema and tables if they do not exist."""
        from psycopg import sql

        with self._connect() as conn:
            with conn.transaction():
                with conn.cursor() as cursor:
                    cursor.execute(
                        sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(self.schema))
                    )
                    cursor.execute(
                        sql.SQL(
                            """
                            CREATE TABLE IF NOT EXISTS {topics} (
                                id bigserial PRIMARY KEY,
                                topic text NOT NULL,
                                prompt text NOT NULL,
                                enabled boolean NOT NULL DEFAULT true,
                                report_type text NOT NULL DEFAULT 'research_report',
                                tone text NOT NULL DEFAULT 'objective',
                                next_run_at timestamptz,
                                last_run_at timestamptz,
                                created_at timestamptz NOT NULL DEFAULT now(),
                                updated_at timestamptz NOT NULL DEFAULT now()
                            )
                            """
                        ).format(topics=self._table(sql, "research_topics"))
                    )
                    cursor.execute(
                        sql.SQL(
                            """
                            CREATE TABLE IF NOT EXISTS {reports} (
                                id bigserial PRIMARY KEY,
                                topic_id bigint NOT NULL REFERENCES {topics}(id),
                                run_id uuid NOT NULL,
                                status text NOT NULL,
                                query text,
                                report_markdown text,
                                context_text text,
                                source_urls jsonb NOT NULL DEFAULT '[]'::jsonb,
                                research_sources jsonb NOT NULL DEFAULT '[]'::jsonb,
                                costs jsonb NOT NULL DEFAULT '{}'::jsonb,
                                started_at timestamptz NOT NULL DEFAULT now(),
                                finished_at timestamptz,
                                error_message text,
                                CONSTRAINT research_reports_status_check
                                    CHECK (status IN ('running', 'success', 'failed'))
                            )
                            """
                        ).format(
                            reports=self._table(sql, "research_reports"),
                            topics=self._table(sql, "research_topics"),
                        )
                    )
                    cursor.execute(
                        sql.SQL(
                            """
                            CREATE UNIQUE INDEX IF NOT EXISTS {run_idx}
                            ON {reports} (run_id)
                            """
                        ).format(
                            run_idx=sql.Identifier(f"{self.schema}_research_reports_run_id_idx"),
                            reports=self._table(sql, "research_reports"),
                        )
                    )
                    cursor.execute(
                        sql.SQL(
                            """
                            CREATE INDEX IF NOT EXISTS {topic_due_idx}
                            ON {topics} (enabled, next_run_at, id)
                            """
                        ).format(
                            topic_due_idx=sql.Identifier(f"{self.schema}_research_topics_due_idx"),
                            topics=self._table(sql, "research_topics"),
                        )
                    )
                    cursor.execute(
                        sql.SQL(
                            """
                            CREATE INDEX IF NOT EXISTS {running_idx}
                            ON {reports} (topic_id)
                            WHERE status = 'running'
                            """
                        ).format(
                            running_idx=sql.Identifier(f"{self.schema}_research_reports_running_idx"),
                            reports=self._table(sql, "research_reports"),
                        )
                    )

    def _connect(self, **kwargs):
        if self._connect_factory is not None:
            return self._connect_factory(self.dsn, connect_timeout=self.connect_timeout, **kwargs)

        import psycopg

        return psycopg.connect(self.dsn, connect_timeout=self.connect_timeout, **kwargs)

    def _table(self, sql_module, table_name: str):
        return sql_module.Identifier(self.schema, table_name)


def dumps_metadata(value: Any) -> str:
    """Return a compact JSON string for logs or fallback diagnostics."""
    return json.dumps(value, ensure_ascii=False, default=str)


def json_ready(value: Any) -> Any:
    """Convert common Python values into JSONB-safe primitives."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, uuid.UUID):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_ready(item) for item in value]
    return str(value)
