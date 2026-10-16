import logging
from typing import Optional

import aiohttp

logger = logging.getLogger("bot.aplaut")

APLAUT_API_URL = "https://api.aplaut.io/v4"


async def fetch_latest_review(api_token: str) -> Optional[dict]:
    """Последний (самый свежий по created_at) отзыв через Aplaut Platform API v4.

    Ответ приводится к тому же виду, что тело вебхука create_review (поля
    отзыва в корне + вложенный product), чтобы форматировать его тем же кодом.
    Документация: https://aplaut.com/docs/api-references/platform/index.html"""
    headers = {"Authorization": f"Bearer {api_token}", "Accept": "application/json"}
    params = {"sort": "created_at:desc", "per_page": 5, "page": 1, "include": "product"}
    try:
        async with aiohttp.ClientSession(
            headers=headers, timeout=aiohttp.ClientTimeout(total=30)
        ) as session:
            async with session.get(f"{APLAUT_API_URL}/reviews", params=params) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error(f"Aplaut API /reviews: HTTP {resp.status} — {body[:300]}")
                    return None
                payload = await resp.json()
    except Exception as e:
        logger.error(f"Ошибка запроса последнего отзыва из Aplaut API: {e}")
        return None

    items = payload.get("data") or []
    if not items:
        return None
    item = items[0]

    review = dict(item.get("attributes") or {})
    review["id"] = item.get("id")

    product_ref = ((item.get("relationships") or {}).get("product") or {}).get("data") or {}
    for included in payload.get("included") or []:
        if included.get("type") == "products" and included.get("id") == product_ref.get("id"):
            review["product"] = included.get("attributes") or {}
            break
    return review
