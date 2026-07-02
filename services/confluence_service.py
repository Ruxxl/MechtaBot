import logging
import re
import ssl
from typing import List, Optional

import aiohttp
from bs4 import BeautifulSoup

logger = logging.getLogger("bot.confluence")

SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.check_hostname = False
SSL_CONTEXT.verify_mode = ssl.CERT_NONE

# Сколько символов очищенного текста страницы отдаем в промпт AI.
# Держим небольшим, т.к. на вопрос обычно берем несколько страниц сразу
# (см. MAX_PAGES в handlers/faq_handler.py) и не хотим раздувать промпт.
EXCERPT_CHARS = 1200


def _clean_html(raw_html: str) -> str:
    """Убирает HTML-разметку Confluence storage format, оставляя чистый текст."""
    soup = BeautifulSoup(raw_html or "", "html.parser")
    text = soup.get_text(separator=" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def _build_cql(query: str, space_key: Optional[str], page_id: Optional[str]) -> str:
    # Экранируем кавычки в вопросе пользователя, чтобы не сломать CQL-запрос
    safe_query = query.replace('"', "'")
    cql = f'text ~ "{safe_query}"'
    if space_key:
        cql += f' and space = "{space_key}"'
    if page_id:
        # Ищем и в самой странице, и в её подстраницах
        cql += f' and (id = "{page_id}" or ancestor = "{page_id}")'
    return cql


async def search_confluence(confluence_config: dict, query: str, limit: int = 3) -> List[dict]:
    """
    Ищет релевантные страницы в Confluence по тексту вопроса (CQL text search)
    и возвращает список {title, url, excerpt} с уже очищенным от HTML содержимым.

    Возвращает [] при ошибке или отсутствии результатов — вызывающий код сам
    решает, что сказать пользователю в этом случае.
    """
    base_url = confluence_config.get('url', '').rstrip('/')
    email = confluence_config.get('email')
    token = confluence_config.get('token')
    space_key = confluence_config.get('space')
    page_id = confluence_config.get('page_id')

    if not base_url or not email or not token:
        logger.error("Confluence не настроен: отсутствует url, email или token")
        return []

    auth = aiohttp.BasicAuth(email, token)
    headers = {"Accept": "application/json"}
    cql = _build_cql(query, space_key, page_id)

    search_url = f"{base_url}/rest/api/content/search"
    params = {
        "cql": cql,
        "limit": str(limit),
        "expand": "body.storage",
    }

    try:
        async with aiohttp.ClientSession(auth=auth, headers=headers) as session:
            async with session.get(search_url, params=params, ssl=SSL_CONTEXT) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error(f"Confluence search error: {resp.status} — {body[:300]}")
                    return []
                data = await resp.json()
    except Exception as e:
        logger.exception(f"Ошибка запроса к Confluence: {e}")
        return []

    results = []
    for item in data.get("results", []):
        title = item.get("title", "Без названия")
        webui_link = item.get("_links", {}).get("webui", "")
        page_url = f"{base_url}{webui_link}" if webui_link else base_url

        storage_html = item.get("body", {}).get("storage", {}).get("value", "")
        excerpt = _clean_html(storage_html)
        if not excerpt:
            continue

        if len(excerpt) > EXCERPT_CHARS:
            excerpt = excerpt[:EXCERPT_CHARS].rsplit(" ", 1)[0] + "…"

        results.append({"title": title, "url": page_url, "excerpt": excerpt})

    return results