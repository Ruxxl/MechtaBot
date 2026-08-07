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

# Сколько символов очищенного текста страницы отдаем в промпт AI при обычном
# CQL text-поиске по всему Confluence (см. search_confluence).
EXCERPT_CHARS = 1200

# Для закрепленной страницы (CONFLUENCE_PAGE_ID) отдаем больше текста, т.к. это
# единственный источник контекста и делить бюджет с другими страницами не нужно.
PINNED_PAGE_EXCERPT_CHARS = 6000

# Чек-листы под parent_page_id часто представляют собой длинные таблицы —
# обычного EXCERPT_CHARS (1200) не хватает, чтобы дойти до нижних строк.
# Даем больше места на страницу, т.к. со страниц дерева обычно берется 1-3 шт.
CHILD_PAGE_EXCERPT_CHARS = 6000

# Сколько дочерних страниц максимум забираем под родителем — защита от
# случайно огромного дерева страниц.
MAX_CHILD_PAGES = 200


def _clean_html(raw_html: str) -> str:
    """Убирает HTML-разметку Confluence storage format, оставляя чистый текст."""
    soup = BeautifulSoup(raw_html or "", "html.parser")
    text = soup.get_text(separator=" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def _build_cql(query: str, space_key: Optional[str]) -> str:
    # Экранируем кавычки в вопросе пользователя, чтобы не сломать CQL-запрос
    safe_query = query.replace('"', "'")
    cql = f'text ~ "{safe_query}"'
    if space_key:
        cql += f' and space = "{space_key}"'
    return cql


# =======================
# Закрепленная страница (CONFLUENCE_PAGE_ID)
# =======================
async def get_pinned_page(confluence_config: dict) -> Optional[dict]:
    """
    Получает содержимое одной конкретной страницы Confluence по ID
    (CONFLUENCE_PAGE_ID) напрямую, БЕЗ CQL text-поиска.

    Нужно это потому, что CQL text-поиск в Confluence Cloud ненадежно работает
    со словоформами русского языка (стемминг) — вопрос "промокода" может не
    совпасть с текстом "Промокод"/"промокоду" на странице, даже если страница
    явно релевантна. Когда вся нужная информация лежит на одной известной
    странице, проще и надежнее просто всегда отдавать её целиком в AI и дать
    ему самому решить, отвечает ли она на вопрос.
    """
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
    parent_page_id, на любом уровне вложенности — через CQL 'ancestor=X'.
    Работает "на лету": если под родителем добавят новую страницу, она
    появится в следующем же вызове /ask без изменений в коде.
    """
    base_url = confluence_config.get('url', '').rstrip('/')
    email = confluence_config.get('email')
    token = confluence_config.get('token')
    parent_id = confluence_config.get('parent_page_id')

    if not base_url or not email or not token or not parent_id:
        logger.warning(
            f"Поиск по дереву страниц пропущен: не хватает конфига "
            f"(base_url={bool(base_url)}, email={bool(email)}, "
            f"token={bool(token)}, parent_id={parent_id})"
        )
        return []

    auth = aiohttp.BasicAuth(email, token)
    headers = {"Accept": "application/json"}
    search_url = f"{base_url}/rest/api/content/search"
    cql = f'ancestor={parent_id}'

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
                        logger.error(
                            f"Confluence ancestor search error: {resp.status}, "
                            f"cql={cql}, url={resp.url}, body={body[:300]}"
                        )
                        break
                    data = await resp.json()

                results = data.get("results", [])
                pages.extend(results)
                if len(results) < page_size:
                    break
                start += page_size
    except Exception as e:
        logger.exception(f"Ошибка получения дочерних страниц Confluence (parent_id={parent_id}): {e}")
        return []

    logger.info(f"Confluence ancestor={parent_id}: найдено дочерних страниц — {len(pages)}")
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
                body = await resp.text()
                logger.warning(f"Не удалось получить страницу {page_id}: {resp.status} — {body[:200]}")
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
    logger.info(f"Confluence: успешно загружено {len(pages)} из {len(stubs)} дочерних страниц")
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


# =======================
# Точка входа
# =======================
async def search_confluence(confluence_config: dict, query: str, limit: int = 3) -> List[dict]:
    """
    Источники объединяются, а не выбираются по принципу "первый непустой":
    1. page_id — закрепленная страница (например "Информация") — если задана,
       включается ВСЕГДА гарантированным первым слотом. Раньше, если был
       также задан parent_page_id, закрепленная страница вообще никогда не
       использовалась: поиск по дереву дочерних страниц почти всегда
       возвращает непустой результат (см. фоллбэк "ничего не совпало —
       отдаем первые страницы" в search_confluence_under_parent) и полностью
       перекрывал собой page_id.
    2. parent_page_id — поиск по всем дочерним страницам дерева, заполняет
       оставшиеся слоты (limit минус уже занятый закрепленной страницей).
    3. Обычный CQL text-поиск по всему Confluence / space — запасной
       вариант, если ни закрепленная страница, ни дерево ничего не дали.
    """
    results: List[dict] = []

    if confluence_config.get('page_id'):
        pinned = await get_pinned_page(confluence_config)
        if pinned:
            results.append(pinned)

    remaining = limit - len(results)
    if remaining > 0 and confluence_config.get('parent_page_id'):
        child_results = await search_confluence_under_parent(confluence_config, query, limit=remaining)
        pinned_urls = {p['url'] for p in results}
        results.extend(p for p in child_results if p['url'] not in pinned_urls)

    if results:
        return results

    logger.warning(
        "Ни закрепленная страница (page_id), ни дерево parent_page_id ничего "
        "не дали — пробую обычный CQL text-поиск как запасной вариант."
    )

    base_url = confluence_config.get('url', '').rstrip('/')
    email = confluence_config.get('email')
    token = confluence_config.get('token')
    space_key = confluence_config.get('space')

    if not base_url or not email or not token:
        logger.error("Confluence не настроен: отсутствует url, email или token")
        return []

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