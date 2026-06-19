import logging
import os
import random
import re
from locust import HttpUser, task, constant_pacing, SequentialTaskSet

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


def load_pages_from_file(filename=None):
    if filename is None:
        filename = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pages.txt")
    with open(filename, "r", encoding="utf-8") as f:
        content = f.read().strip()
        pages_dict = {}
        exec(content, {}, pages_dict)
        pages = pages_dict.get("pages", [])
        return list(dict.fromkeys(pages))


class UserBehavior(SequentialTaskSet):
    all_pages = load_pages_from_file()

    def on_start(self):
        self.total_users = (
            self.user.environment.runner.user_count
            if self.user.environment.runner
            else 0
        )
        self.remaining_pages = self.all_pages.copy()
        random.shuffle(self.remaining_pages)
        logging.info(f"Новый цикл начат. Всего страниц: {len(self.remaining_pages)}")

    @task
    def visit_page_once(self):
        if not self.remaining_pages:
            self.remaining_pages = self.all_pages.copy()
            random.shuffle(self.remaining_pages)
            logging.info("Все страницы пройдены. Начинаем новый цикл.")
            return

        page = self.remaining_pages.pop(0)

        try:
            response = self.client.get(page, name=page, timeout=10)
        except Exception as e:
            logging.error(f"Ошибка при запросе {page}: {e}")
            return

        match = re.search(r"<title>(.*?)</title>", response.text, re.IGNORECASE | re.DOTALL)
        title = match.group(1).strip() if match else "Title не найден"
        logging.info(f"Посещена страница: {page} | статус: {response.status_code} | title: {title}")


class WebsiteUser(HttpUser):
    wait_time = constant_pacing(2)
    tasks = [UserBehavior]