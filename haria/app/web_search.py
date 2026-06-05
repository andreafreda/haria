"""Ricerca web via DuckDuckGo (ddgs). search() torna [{title,url,snippet}];
supporta gli operatori site:/-site: per whitelist/blacklist fonti."""
import asyncio
import logging
from ddgs import DDGS

logger = logging.getLogger(__name__)


def _is_no_results(e: Exception) -> bool:
    """ddgs alza eccezione sia su zero risultati sia su errori rete/ratelimit.
    Distinguiamo: 'no results' = vuoto legittimo; il resto = errore vero."""
    return "no results" in str(e).lower()


def _log_search_error(fn: str, query: str, e: Exception) -> None:
    if _is_no_results(e):
        logger.info("%s nessun risultato per %r", fn, query)
    else:
        logger.warning("%s errore per %r: %s", fn, query, e)


def _search_sync(query: str, max_results: int) -> list[dict]:
    try:
        with DDGS() as ddgs:
            return list(ddgs.text(query, region="it-it", max_results=max_results))
    except Exception as e:
        _log_search_error("search()", query, e)
        return []


async def search(query: str, max_results: int = 5) -> list[dict]:
    raw = await asyncio.to_thread(_search_sync, query, max_results)
    return [
        {"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", "")}
        for r in raw
    ]


def _news_sync(query: str, max_results: int) -> list[dict]:
    try:
        with DDGS() as ddgs:
            return list(ddgs.news(query, region="it-it", max_results=max_results))
    except Exception as e:
        _log_search_error("search_news()", query, e)
        return []


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
