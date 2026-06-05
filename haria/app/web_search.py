"""Ricerca web via DuckDuckGo (ddgs). search() torna [{title,url,snippet}];
supporta gli operatori site:/-site: per whitelist/blacklist fonti."""
import asyncio
import logging
from ddgs import DDGS

logger = logging.getLogger(__name__)


def _search_sync(query: str, max_results: int) -> list[dict]:
    with DDGS() as ddgs:
        return list(ddgs.text(query, region="it-it", max_results=max_results))


async def search(query: str, max_results: int = 5) -> list[dict]:
    raw = await asyncio.to_thread(_search_sync, query, max_results)
    return [
        {"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", "")}
        for r in raw
    ]


def _news_sync(query: str, max_results: int) -> list[dict]:
    with DDGS() as ddgs:
        return list(ddgs.news(query, region="it-it", max_results=max_results))


async def search_news(query: str, max_results: int = 5) -> list[dict]:
    """Come search() ma usa l'endpoint news: include data di pubblicazione e fonte."""
    raw = await asyncio.to_thread(_news_sync, query, max_results)
    return [
        {
            "title": r.get("title", ""),
            "url": r.get("url", "") or r.get("href", ""),
            "snippet": r.get("body", ""),
            "date": r.get("date", ""),
            "source": r.get("source", ""),
        }
        for r in raw
    ]
