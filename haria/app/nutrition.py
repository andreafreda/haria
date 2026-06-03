"""Lookup valori nutrizionali da fonti gratuite, con cache su DB.

Strategia:
  1. cache locale (food_cache) — istantanea, niente rete.
  2. OpenFoodFacts — barcode (prodotti confezionati) o ricerca per nome.
  3. USDA FoodData Central — alimenti grezzi (richiede fdc_key, fallback DEMO_KEY).
  4. fallback: None → Claude stima da solo.

Valori normalizzati per 100 g: kcal, protein_g, carbs_g, fat_g.
"""
import logging
import aiohttp
import config as cfg
from memory import get_food_cache, set_food_cache

logger = logging.getLogger(__name__)

OFF_PRODUCT = "https://world.openfoodfacts.org/api/v2/product/{code}.json"
OFF_SEARCH = "https://world.openfoodfacts.org/cgi/search.pl"
USDA_SEARCH = "https://api.nal.usda.gov/fdc/v1/foods/search"

_TIMEOUT = aiohttp.ClientTimeout(total=8)


def _off_nutriments(n: dict) -> dict | None:
    kcal = n.get("energy-kcal_100g")
    if kcal is None:
        return None
    return {
        "kcal": round(float(kcal), 1),
        "protein_g": round(float(n.get("proteins_100g", 0) or 0), 1),
        "carbs_g": round(float(n.get("carbohydrates_100g", 0) or 0), 1),
        "fat_g": round(float(n.get("fat_100g", 0) or 0), 1),
        "per": "100g",
    }


async def lookup_barcode(code: str) -> dict | None:
    cache_key = f"barcode:{code}"
    cached = await get_food_cache(cache_key)
    if cached:
        return cached
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as s:
            async with s.get(OFF_PRODUCT.format(code=code)) as r:
                data = await r.json()
    except Exception as e:
        logger.warning("OFF barcode %s fallito: %s", code, e)
        return None
    if data.get("status") != 1:
        return None
    prod = data.get("product", {})
    nutr = _off_nutriments(prod.get("nutriments", {}))
    if not nutr:
        return None
    nutr["name"] = prod.get("product_name") or prod.get("generic_name") or code
    nutr["brand"] = prod.get("brands", "")
    await set_food_cache(cache_key, nutr, "openfoodfacts")
    return nutr


async def _off_search(query: str) -> dict | None:
    params = {
        "search_terms": query, "json": 1, "page_size": 1,
        "fields": "product_name,nutriments",
    }
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as s:
            async with s.get(OFF_SEARCH, params=params) as r:
                data = await r.json()
    except Exception as e:
        logger.warning("OFF search '%s' fallito: %s", query, e)
        return None
    prods = data.get("products") or []
    if not prods:
        return None
    nutr = _off_nutriments(prods[0].get("nutriments", {}))
    if nutr:
        nutr["name"] = prods[0].get("product_name") or query
    return nutr


async def _usda_search(query: str) -> dict | None:
    key = cfg.get("fdc_key", "") or "DEMO_KEY"
    params = {"query": query, "pageSize": 1, "api_key": key}
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as s:
            async with s.get(USDA_SEARCH, params=params) as r:
                data = await r.json()
    except Exception as e:
        logger.warning("USDA search '%s' fallito: %s", query, e)
        return None
    foods = data.get("foods") or []
    if not foods:
        return None
    nmap = {}
    for n in foods[0].get("foodNutrients", []):
        nm = (n.get("nutrientName") or "").lower()
        val = n.get("value")
        if val is None:
            continue
        if "energy" in nm and "kcal" in (n.get("unitName", "").lower() or "kcal"):
            nmap["kcal"] = val
        elif nm == "protein":
            nmap["protein_g"] = val
        elif "carbohydrate" in nm:
            nmap["carbs_g"] = val
        elif nm.startswith("total lipid"):
            nmap["fat_g"] = val
    if "kcal" not in nmap:
        return None
    return {
        "name": foods[0].get("description", query),
        "kcal": round(nmap.get("kcal", 0), 1),
        "protein_g": round(nmap.get("protein_g", 0), 1),
        "carbs_g": round(nmap.get("carbs_g", 0), 1),
        "fat_g": round(nmap.get("fat_g", 0), 1),
        "per": "100g",
    }


async def lookup_food(query: str) -> dict | None:
    """Cerca valori per 100 g di un alimento. None se nessuna fonte risponde."""
    cache_key = f"food:{query.strip().lower()}"
    cached = await get_food_cache(cache_key)
    if cached:
        return cached
    result = await _off_search(query) or await _usda_search(query)
    if result:
        await set_food_cache(cache_key, result, result.get("_source", "api"))
    return result
