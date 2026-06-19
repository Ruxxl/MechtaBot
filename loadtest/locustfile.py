"""
Locustfile для нагрузочного тестирования mechta.kz / стендов.

Идея доработки (по сравнению с первой версией):

1. Раньше каждый виртуальный пользователь последовательно обходил ВСЕ
   ~2500 страниц из pages.txt по кругу. Это не похоже на поведение
   реального посетителя и не позволяет управлять тем, какие страницы
   получают больше нагрузки.

   Теперь страницы раскладываются по типам (товар / категория / бренд /
   FAQ / статика), и каждый "тик" виртуального пользователя — это
   случайный визит на страницу одного из этих типов, с весами в пользу
   товаров и категорий (как и есть в реальном трафике витрины).

2. Раньше все ~2500 URL попадали в статистику Locust как отдельные
   записи (name=каждая конкретная страница) — отчёт/CSV становился
   нечитаемым. Теперь запросы группируются по типу страницы
   (/product/[slug], /section/[slug] и т.д.), и в CSV всего 5 строк +
   Aggregated — отчёт бота читается нормально.

3. Раньше логировался каждый успешный запрос — на серьёзной нагрузке
   (сотни RPS) это сам по себе становится бутылочным горлышком
   (диск/IO). Теперь логируются только ошибки.

4. Добавлена проверка "мягких" ошибок: сайт может ответить 200 OK, но
   с пустой/битой страницей — это не поймает обычная проверка кода
   ответа. Если тело ответа подозрительно маленькое — считаем это
   ошибкой явно (через catch_response).

Веса сценариев и порог "подозрительно короткого" ответа можно менять
через переменные окружения без правки кода — см. LT_WEIGHT_* и
LT_MIN_RESPONSE_BYTES.
"""

import logging
import os
import random

from locust import HttpUser, between, task

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "locust_test.log"),
            mode="w",
            encoding="utf-8",
        ),
    ],
)

# Относительные веса сценариев: чем больше число, тем чаще Locust будет
# выбирать этот тип страницы для очередного запроса. Подобраны исходя из
# типичной структуры трафика витрины (товары и категории — основной
# объем, справочные страницы — небольшая доля).
WEIGHT_PRODUCT = int(os.getenv("LT_WEIGHT_PRODUCT", 5))
WEIGHT_SECTION = int(os.getenv("LT_WEIGHT_SECTION", 4))
WEIGHT_STATIC = int(os.getenv("LT_WEIGHT_STATIC", 2))
WEIGHT_BRAND = int(os.getenv("LT_WEIGHT_BRAND", 1))
WEIGHT_FAQ = int(os.getenv("LT_WEIGHT_FAQ", 1))

# Если тело ответа короче этого значения (байт) — считаем страницу
# "подозрительной" (битый/пустой рендер) и помечаем запрос как ошибку,
# даже если HTTP статус 200.
MIN_RESPONSE_BYTES = int(os.getenv("LT_MIN_RESPONSE_BYTES", 200))


def load_pages_from_file(filename=None):
    if filename is None:
        filename = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pages.txt")
    with open(filename, "r", encoding="utf-8") as f:
        content = f.read().strip()
        pages_dict = {}
        exec(content, {}, pages_dict)
        pages = pages_dict.get("pages", [])
        return list(dict.fromkeys(pages))


def categorize_pages(pages):
    """Раскладывает список URL по типам страниц на основе префикса пути."""
    buckets = {"product": [], "section": [], "brand": [], "faq": [], "static": []}
    for page in pages:
        if page.startswith("/product/"):
            buckets["product"].append(page)
        elif page.startswith("/section/"):
            buckets["section"].append(page)
        elif page.startswith("/brands/"):
            buckets["brand"].append(page)
        elif page.startswith("/faq"):
            buckets["faq"].append(page)
        else:
            buckets["static"].append(page)

    # Главной страницы нет в pages.txt, но в реальном трафике это самая
    # частая точка входа — добавляем её в статические страницы.
    if "/" not in buckets["static"]:
        buckets["static"].insert(0, "/")

    return buckets


ALL_PAGES = load_pages_from_file()
PAGE_BUCKETS = categorize_pages(ALL_PAGES)

logging.info(
    "Загружено страниц: всего=%d, product=%d, section=%d, brand=%d, faq=%d, static=%d",
    len(ALL_PAGES),
    len(PAGE_BUCKETS["product"]),
    len(PAGE_BUCKETS["section"]),
    len(PAGE_BUCKETS["brand"]),
    len(PAGE_BUCKETS["faq"]),
    len(PAGE_BUCKETS["static"]),
)


def visit_random_page(client, bucket_name: str, stat_name: str):
    """Запрашивает случайную страницу из указанного 'ведра' страниц.

    Группирует все запросы этого типа под одним именем в статистике
    Locust (stat_name) и явно проверяет ответ: HTTP-ошибки и подозрительно
    короткие ответы помечаются как failure, даже если соединение прошло
    успешно.
    """
    pages = PAGE_BUCKETS.get(bucket_name)
    if not pages:
        return

    page = random.choice(pages)

    with client.get(page, name=stat_name, catch_response=True, timeout=10) as response:
        if response.status_code >= 400:
            logging.warning(f"HTTP {response.status_code} на {page}")
            response.failure(f"HTTP {response.status_code}")
            return

        try:
            body_len = len(response.content or b"")
        except Exception:
            body_len = None

        if body_len is not None and body_len < MIN_RESPONSE_BYTES:
            logging.warning(f"Подозрительно короткий ответ ({body_len} байт) на {page}")
            response.failure(f"Слишком короткий ответ: {body_len} байт")
            return

        response.success()


class WebsiteUser(HttpUser):
    """Имитирует посетителя витрины: чаще смотрит товары и категории,
    реже — статические/справочные страницы (бренды, FAQ)."""

    wait_time = between(1, 3)

    @task(WEIGHT_PRODUCT)
    def browse_product(self):
        visit_random_page(self.client, "product", "/product/[slug]")

    @task(WEIGHT_SECTION)
    def browse_section(self):
        visit_random_page(self.client, "section", "/section/[slug]")

    @task(WEIGHT_STATIC)
    def browse_static(self):
        visit_random_page(self.client, "static", "/[static]")

    @task(WEIGHT_BRAND)
    def browse_brand(self):
        visit_random_page(self.client, "brand", "/brands/[slug]")

    @task(WEIGHT_FAQ)
    def browse_faq(self):
        visit_random_page(self.client, "faq", "/faq[...]")