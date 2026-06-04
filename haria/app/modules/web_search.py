"""Modulo web_search: ricerca web via DuckDuckGo (ddgs)."""
import json
import web_search

NAME = "web_search"

TOOLS = [
    {
        "name": "search_web",
        "description": "Cerca informazioni aggiornate sul web (notizie, fatti recenti, dati che non conosci). Restituisce titolo, url e snippet dei risultati.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Query di ricerca"},
                "max_results": {"type": "integer", "description": "Numero risultati (default 5)"},
            },
            "required": ["query"],
        },
    },
]

PROMPT = (
    "\n- Per informazioni aggiornate o che non conosci (notizie, eventi recenti, dati attuali) usa search_web, poi rispondi citando le fonti."
)


async def handle(name: str, inputs: dict, user_id: str) -> str:
    if name == "search_web":
        results = await web_search.search(inputs["query"], inputs.get("max_results", 5))
        return json.dumps(results, ensure_ascii=False) if results else "Nessun risultato."
    return f"Tool sconosciuto nel modulo {NAME}: {name}"
