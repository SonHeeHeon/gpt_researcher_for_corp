"""Retriever factory and utilities for GPT Researcher.

This module provides functions to instantiate and manage various
search retriever implementations.
"""


def get_retriever(retriever: str):
    """Get a retriever class by name.

    Args:
        retriever: The name of the retriever to get (e.g., 'postgres_news', 'google', 'duckduckgo').

    Returns:
        The retriever class if found, None otherwise.

    Supported retrievers:
        - google: Google Custom Search
        - searx: SearX search engine
        - searchapi: SearchAPI service
        - serpapi: SerpAPI service
        - serper: Serper API
        - duckduckgo: DuckDuckGo search
        - bing: Bing search
        - arxiv: arXiv academic search
        - tavily: Tavily search API
        - postgres_news: PostgreSQL/pgvector-backed internal news search
        - exa: Exa search
        - semantic_scholar: Semantic Scholar academic search
        - pubmed_central: PubMed Central medical literature
        - openalex: OpenAlex scholarly works catalog
        - custom: Custom user-defined retriever
        - mcp: Model Context Protocol retriever
        - xquik: Xquik X/Twitter search
    """
    match retriever:
        case "google":
            from gpt_researcher.retrievers import GoogleSearch

            return GoogleSearch
        case "searx":
            from gpt_researcher.retrievers import SearxSearch

            return SearxSearch
        case "searchapi":
            from gpt_researcher.retrievers import SearchApiSearch

            return SearchApiSearch
        case "serpapi":
            from gpt_researcher.retrievers import SerpApiSearch

            return SerpApiSearch
        case "serper":
            from gpt_researcher.retrievers import SerperSearch

            return SerperSearch
        case "duckduckgo":
            from gpt_researcher.retrievers import Duckduckgo

            return Duckduckgo
        case "bing":
            from gpt_researcher.retrievers import BingSearch

            return BingSearch
        case "bocha":
            from gpt_researcher.retrievers import BoChaSearch

            return BoChaSearch
        case "arxiv":
            from gpt_researcher.retrievers import ArxivSearch

            return ArxivSearch
        case "tavily":
            from gpt_researcher.retrievers import TavilySearch

            return TavilySearch
        case "postgres_news":
            from gpt_researcher.retrievers import PostgresNewsSearch

            return PostgresNewsSearch
        case "exa":
            from gpt_researcher.retrievers import ExaSearch

            return ExaSearch
        case "semantic_scholar":
            from gpt_researcher.retrievers import SemanticScholarSearch

            return SemanticScholarSearch
        case "pubmed_central":
            from gpt_researcher.retrievers import PubMedCentralSearch

            return PubMedCentralSearch
        case "custom":
            from gpt_researcher.retrievers import CustomRetriever

            return CustomRetriever
        case "mcp":
            from gpt_researcher.retrievers import MCPRetriever

            return MCPRetriever
        case "xquik":
            from gpt_researcher.retrievers import XquikSearch

            return XquikSearch
        case "openalex":
            from gpt_researcher.retrievers import OpenAlexSearch

            return OpenAlexSearch

        case _:
            return None


def get_retrievers(headers: dict[str, str], cfg):
    """
    Determine which retriever(s) to use based on headers, config, or default.

    Args:
        headers (dict): The headers dictionary
        cfg: The configuration object

    Returns:
        list: A list of retriever classes to be used for searching.
    """
    # Check headers first for multiple retrievers
    if headers.get("retrievers"):
        retrievers = headers.get("retrievers").split(",")
    # If not found, check headers for a single retriever
    elif headers.get("retriever"):
        retrievers = [headers.get("retriever")]
    # If not in headers, check config for multiple retrievers
    elif cfg.retrievers:
        # Handle both list and string formats for config retrievers
        if isinstance(cfg.retrievers, str):
            retrievers = cfg.retrievers.split(",")
        else:
            retrievers = cfg.retrievers
        # Strip whitespace from each retriever name
        retrievers = [r.strip() for r in retrievers]
    # If not found, check config for a single retriever
    elif cfg.retriever:
        retrievers = [cfg.retriever]
    # If still not set, use default retriever
    else:
        retrievers = ["postgres_news"]

    retrievers = [retriever.strip() for retriever in retrievers if retriever.strip()]
    if not retrievers:
        raise ValueError("No retriever configured. Set RETRIEVER to a supported retriever name.")

    retriever_classes = []
    invalid_retrievers = []
    for retriever in retrievers:
        retriever_class = get_retriever(retriever)
        if retriever_class is None:
            invalid_retrievers.append(retriever)
        else:
            retriever_classes.append(retriever_class)

    if invalid_retrievers:
        raise ValueError(
            f"Invalid retriever(s): {', '.join(invalid_retrievers)}. "
            "Set RETRIEVER to a supported retriever name."
        )
    
    return retriever_classes


def get_default_retriever():
    """Get the default retriever class.

    Returns:
        The PostgresNewsSearch retriever class as the default search provider.
    """
    from gpt_researcher.retrievers import PostgresNewsSearch

    return PostgresNewsSearch
