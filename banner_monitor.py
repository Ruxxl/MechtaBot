# banner_monitor.py
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import logging

SIZE_LIMIT = 1 * 1024 * 1024 * 1024  # 1 GB
CAROUSEL_SELECTOR = 'div[role="region"]'


async def check_banner_images(
    bot,
    chat_id: int,
    site_url: str,
    logger: logging.Logger
):
    """
    Проверяет баннеры в карусели и уведомляет в Telegram,
    если размер картинки превышает 1 GB
    """

    try:
        response = requests.get(site_url, timeout=15)
        response.raise_for_status()
    except Exception as e:
        logger.error("❌ Ошибка загрузки страницы %s: %s", site_url, e)
        return

    soup = BeautifulSoup(response.text, "html.parser")
    carousel = soup.select_one(CAROUSEL_SELECTOR)

    if not carousel:
        logger.warning("⚠️ Карусель не найдена")
        return

    images = carousel.find_all("img")
    logger.info("Найдено баннеров: %s", len(images))

    for img in images:
        img_url = (
            img.get("src")
            or img.get("data-src")
            or img.get("data-lazy")
        )

        if not img_url:
            continue

        img_url = urljoin(site_url, img_url)

        try:
            head = requests.head(img_url, allow_redirects=True, timeout=10)
            size = int(head.headers.get("Content-Length", 0))
        except Exception as e:
            logger.warning("Ошибка при HEAD %s: %s", img_url, e)
            continue

        if size > SIZE_LIMIT:
            size_gb = size / 1024 / 1024 / 1024
            text = (
                "🚨 <b>Слишком большой баннер</b>\n\n"
                f"🖼 <a href='{img_url}'>Открыть картинку</a>\n"
                f"📦 Размер: <b>{size_gb:.2f} GB</b>"
            )

            await bot.send_message(chat_id, text, disable_web_page_preview=True)
            logger.warning("Обнаружен баннер > 1GB: %s", img_url)
