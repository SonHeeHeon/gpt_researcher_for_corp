"""Cron-friendly company news batch runner."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import traceback
from typing import Any

from dotenv import load_dotenv

from gpt_researcher.corp.db import ClaimedTopic, CorpBatchStore, dumps_metadata, json_ready
from gpt_researcher.utils.enum import ReportSource, ReportType, Tone

logger = logging.getLogger(__name__)

TONE_BY_KEY = {
    "objective": Tone.Objective,
    "formal": Tone.Formal,
    "analytical": Tone.Analytical,
    "persuasive": Tone.Persuasive,
    "informative": Tone.Informative,
    "explanatory": Tone.Explanatory,
    "descriptive": Tone.Descriptive,
    "critical": Tone.Critical,
    "comparative": Tone.Comparative,
    "speculative": Tone.Speculative,
    "reflective": Tone.Reflective,
    "narrative": Tone.Narrative,
    "humorous": Tone.Humorous,
    "optimistic": Tone.Optimistic,
    "pessimistic": Tone.Pessimistic,
    "simple": Tone.Simple,
    "casual": Tone.Casual,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run company GPT Researcher batch topics from PostgreSQL.")
    parser.add_argument(
        "--limit",
        type=int,
        default=int(os.getenv("CORP_BATCH_LIMIT", "10")),
        help="Maximum number of due topics to claim in one run. Use 0 to only check configuration.",
    )
    parser.add_argument(
        "--init-db",
        action="store_true",
        help="Create the default corp schema/tables and exit.",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop after the first topic failure and exit non-zero.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose GPT Researcher logs.",
    )
    return parser


async def run_batch(
    store: CorpBatchStore,
    limit: int,
    *,
    verbose: bool = False,
    fail_fast: bool = False,
) -> int:
    claims = store.claim_due_topics(limit)
    if not claims:
        logger.info("No due corp research topics found.")
        return 0

    failures = 0
    for claim in claims:
        try:
            await run_topic(store, claim, verbose=verbose)
        except Exception as exc:
            failures += 1
            logger.error("Topic %s failed: %s", claim.id, exc)
            if fail_fast:
                raise

    return failures


async def run_topic(store: CorpBatchStore, claim: ClaimedTopic, *, verbose: bool = False) -> None:
    logger.info("Running topic_id=%s run_id=%s topic=%s", claim.id, claim.run_id, claim.topic)

    researcher = build_researcher(claim, verbose=verbose)

    try:
        context = await researcher.conduct_research()
        report = await researcher.write_report()

        store.mark_success(
            claim=claim,
            report_markdown=report,
            context_text=context_to_text(context),
            source_urls=[str(url) for url in researcher.get_source_urls()],
            research_sources=sanitize_research_sources(researcher.get_research_sources()),
            costs={
                "total": researcher.get_costs(),
                "steps": researcher.get_step_costs(),
            },
        )
        logger.info(
            "Completed topic_id=%s run_id=%s costs=%s",
            claim.id,
            claim.run_id,
            dumps_metadata({"total": researcher.get_costs(), "steps": researcher.get_step_costs()}),
        )
    except Exception as exc:
        error_message = "".join(traceback.format_exception_only(type(exc), exc)).strip()
        store.mark_failure(claim, error_message)
        raise


def build_researcher(claim: ClaimedTopic, *, verbose: bool = False):
    from gpt_researcher import GPTResearcher

    return GPTResearcher(
        query=claim.prompt,
        report_type=normalize_report_type(claim.report_type),
        report_source=ReportSource.Web.value,
        tone=normalize_tone(claim.tone),
        verbose=verbose,
    )


def normalize_report_type(report_type: str | None) -> str:
    value = (report_type or ReportType.ResearchReport.value).strip()
    valid_values = {item.value for item in ReportType}
    if value in valid_values:
        return value
    logger.warning("Invalid report_type '%s'; using research_report.", value)
    return ReportType.ResearchReport.value


def normalize_tone(tone: str | None) -> Tone:
    value = (tone or "objective").strip()
    key = value.lower()
    if key in TONE_BY_KEY:
        return TONE_BY_KEY[key]

    for item in Tone:
        if key in {
            item.name.lower(),
            item.value.lower(),
            item.value.split(" ", 1)[0].lower(),
        }:
            return item

    logger.warning("Invalid tone '%s'; using objective.", value)
    return Tone.Objective


def context_to_text(context: Any) -> str:
    if isinstance(context, list):
        return "\n\n".join(str(item) for item in context)
    return str(context or "")


def sanitize_research_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sanitized = []
    for source in sources:
        sanitized.append({
            "url": json_ready(source.get("url")),
            "title": json_ready(source.get("title")),
            "source": json_ready(source.get("source")),
            "published_at": json_ready(source.get("published_at")),
        })
    return sanitized


def main() -> int:
    load_dotenv()
    logging.basicConfig(
        level=os.getenv("LOGGING_LEVEL", "INFO"),
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    )

    parser = build_parser()
    args = parser.parse_args()

    store = CorpBatchStore()
    if args.init_db:
        store.initialize_schema()
        logger.info("Initialized corp batch schema.")
        return 0

    failures = asyncio.run(
        run_batch(
            store,
            args.limit,
            verbose=args.verbose,
            fail_fast=args.fail_fast,
        )
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
