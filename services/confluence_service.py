import asyncio
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

EXCERPT_CHARS = 1200
PINNED_PAGE_EXCERPT_CHARS = 6000
# Чек-листы часто представляют собой длинные таблицы — обычного EXCERPT_CHARS
# (1200) не хватает, чтобы дойти до нижних строк. Даем больше места на
# страницу, т.к. со страниц parent_page_id обычно берется всего 1-3 штуки.
CHILD_PAGE_EXCERPT_CHARS = 6000


def _clean_html(raw_html: str) -> str:
    soup = BeautifulSoup(raw_html or "", "html.parser")
    text = soup.get_text(separator=" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def _build_cql(query: str, space_key: Optional[str]) -> str:
    safe_query = query.replace('"', "'")
    cql = f'text ~ "{safe_query}"'
    if space_key:
        cql += f' and space = "{space_key}"'
    return cql


async def get_pinned_page(confluence_config: dict) -> Optional[dict]:
    base_url = confluence_config.get('url', '').rstrip('/')
    email = confluence_config.get('email')
    token = confluence_config.get('token')
    page_id = confluence_config.get('page_id')

    if not base_url or not email or not token or not page_id:
        return None

    auth = aiohttp.BasicAuth(email, token)
    headers = {"Accept": "application/json"}
    url = f"{base_url}/rest/api/content/{page_id}"
    params = {"expand": "body.storage"}

    try:
        async with aiohttp.ClientSession(auth=auth, headers=headers) as session:
            async with session.get(url, params=params, ssl=SSL_CONTEXT) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error(f"Confluence get page error: {resp.status} — {body[:300]}")
                    return None
                data = await resp.json()
    except Exception as e:
        logger.exception(f"Ошибка запроса страницы Confluence: {e}")
        return None

    title = data.get("title", "Без названия")
    webui_link = data.get("_links", {}).get("webui", "")
    page_url = f"{base_url}{webui_link}" if webui_link else base_url

    storage_html = data.get("body", {}).get("storage", {}).get("value", "")
    excerpt = _clean_html(storage_html)
    if not excerpt:
        return None

    if len(excerpt) > PINNED_PAGE_EXCERPT_CHARS:
        excerpt = excerpt[:PINNED_PAGE_EXCERPT_CHARS].rsplit(" ", 1)[0] + "…"

    return {"title": title, "url": page_url, "excerpt": excerpt}


# =======================
# Поиск по дереву дочерних страниц (parent_page_id)
# =======================
async def _get_child_page_stubs(confluence_config: dict) -> List[dict]:
    """
    Возвращает базовую инфу (id, title) обо ВСЕХ страницах-потомках
    parent_page_id, на любом уровне вложенности — через CQL 'ancestor = X'.
    Работает "на лету": если под родителем добавят новую страницу, она
    появится в следующем же вызове /ask без изменений в коде.
    """
    base_url = confluence_config.get('url', '').rstrip('/')
    email = confluence_config.get('email')
    token = confluence_config.get('token')
    parent_id = confluence_config.get('parent_page_id')

    if not base_url or not email or not token or not parent_id:
        return []

    auth = aiohttp.BasicAuth(email, token)
    headers = {"Accept": "application/json"}
    search_url = f"{base_url}/rest/api/content/search"
    cql = f'ancestor = {parent_id}'

    pages: List[dict] = []
    start = 0
    page_size = 50

    try:
        async with aiohttp.ClientSession(auth=auth, headers=headers) as session:
            while len(pages) < MAX_CHILD_PAGES:
                params = {"cql": cql, "limit": str(page_size), "start": str(start)}
                async with session.get(search_url, params=params, ssl=SSL_CONTEXT) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.error(f"Confluence ancestor search error: {resp.status} — {body[:300]}")
                        break
                    data = await resp.json()

                results = data.get("results", [])
                pages.extend(results)
                if len(results) < page_size:
                    break
                start += page_size
    except Exception as e:
        logger.exception(f"Ошибка получения дочерних страниц Confluence: {e}")
        return []

    return pages[:MAX_CHILD_PAGES]


async def _fetch_page_full_text(session: aiohttp.ClientSession, base_url: str, page_stub: dict) -> Optional[dict]:
    page_id = page_stub.get("id")
    if not page_id:
        return None

    url = f"{base_url}/rest/api/content/{page_id}"
    params = {"expand": "body.storage"}

    try:
        async with session.get(url, params=params, ssl=SSL_CONTEXT) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
    except Exception as e:
        logger.warning(f"Не удалось получить страницу {page_id}: {e}")
        return None

    title = data.get("title", "Без названия")
    webui_link = data.get("_links", {}).get("webui", "")
    page_url = f"{base_url}{webui_link}" if webui_link else base_url
    storage_html = data.get("body", {}).get("storage", {}).get("value", "")
    full_text = _clean_html(storage_html)

    if not full_text:
        return None

    return {"title": title, "url": page_url, "full_text": full_text}


def _score_page(page: dict, query_words: List[str]) -> int:
    text_lower = (page["title"] + " " + page["full_text"]).lower()
    return sum(text_lower.count(w) for w in query_words)


async def search_confluence_under_parent(confluence_config: dict, query: str, limit: int = 3) -> List[dict]:
    """
    Ищет ответ среди ВСЕХ дочерних страниц parent_page_id (любой уровень
    вложенности). Вместо ненадежного CQL text-поиска (плохо работает со
    словоформами RU) — забирает содержимое всех дочерних страниц и сам
    выбирает наиболее релевантные по простому подсчету вхождений слов
    вопроса. Новые страницы, добавленные под родителем, подхватываются
    автоматически при следующем запросе.
    """
    base_url = confluence_config.get('url', '').rstrip('/')
    email = confluence_config.get('email')
    token = confluence_config.get('token')

    stubs = await _get_child_page_stubs(confluence_config)
    if not stubs:
        return []

    auth = aiohttp.BasicAuth(email, token)
    headers = {"Accept": "application/json"}

    try:
        async with aiohttp.ClientSession(auth=auth, headers=headers) as session:
            tasks = [_fetch_page_full_text(session, base_url, stub) for stub in stubs]
            fetched = await asyncio.gather(*tasks)
    except Exception as e:
        logger.exception(f"Ошибка получения содержимого дочерних страниц Confluence: {e}")
        return []

    pages = [p for p in fetched if p]
    if not pages:
        return []

    query_words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 2]

    if query_words:
        scored = sorted(pages, key=lambda p: _score_page(p, query_words), reverse=True)
        top = [p for p in scored if _score_page(p, query_words) > 0][:limit]
    else:
        top = []

    if not top:
        # ничего явно не совпало по словам — отдаем первые страницы,
        # пусть AI сам решит, что из этого relevant (лучше, чем пустой ответ)
        top = pages[:limit]

    results = []
    for p in top:
        excerpt = p["full_text"]
        if len(excerpt) > CHILD_PAGE_EXCERPT_CHARS:
            excerpt = excerpt[:CHILD_PAGE_EXCERPT_CHARS].rsplit(" ", 1)[0] + "…"
        results.append({"title": p["title"], "url": p["url"], "excerpt": excerpt})

    return results


async def search_confluence(confluence_config: dict, query: str, limit: int = 3) -> List[dict]:
    """
    Приоритет источников:
    1. parent_page_id — поиск по всем дочерним страницам дерева (новый режим).
    2. page_id — одна закрепленная страница целиком.
    3. Обычный CQL text-поиск по всему Confluence / space.
    """
    if confluence_config.get('parent_page_id'):
        return await search_confluence_under_parent(confluence_config, query, limit=limit)

    base_url = confluence_config.get('url', '').rstrip('/')
    email = confluence_config.get('email')
    token = confluence_config.get('token')
    space_key = confluence_config.get('space')
    page_id = confluence_config.get('page_id')

    if not base_url or not email or not token:
        logger.error("Confluence не настроен: отсутствует url, email или token")
        return []

    if page_id:
        pinned = await get_pinned_page(confluence_config)
        return [pinned] if pinned else []

    auth = aiohttp.BasicAuth(email, token)
    headers = {"Accept": "application/json"}
    cql = _build_cql(query, space_key)

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