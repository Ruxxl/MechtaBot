import asyncio
import hmac
import html
import json
import logging
from collections import deque
from datetime import datetime
from typing import Optional, Set
from zoneinfo import ZoneInfo

from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.types import Message

from services.aplaut_service import fetch_latest_review
from services.db_service import has_aplaut_notified_reviews, mark_aplaut_review_notified
from web.admin_handler import monitor

logger = logging.getLogger("bot.aplaut")

APLAUT_REVIEW_URL = "https://app.aplaut.io/b/reviews/{review_id}"
TIMEZONE = ZoneInfo("Asia/Almaty")
TELEGRAM_TEXT_LIMIT = 4096
TELEGRAM_CAPTION_LIMIT = 1024

STATE_LABELS = {
    "published": "✅ Опубликован",
    "banned": "🚫 Забанен",
    "on_hold": "⏸ На удержании",
    "held": "⏸ На удержании",
    "pending": "🕓 На модерации",
    "new": "🕓 На модерации",
}
MONTHS_SHORT = ["янв.", "февр.", "мар.", "апр.", "мая", "июн.",
                "июл.", "авг.", "сент.", "окт.", "нояб.", "дек."]


def _ru_text(value) -> str:
    """Aplaut хранит часть названий как JSON-строку с переводами
    ('{"kk": "...", "ru": "..."}') — достаем русский вариант, если он есть."""
    if isinstance(value, dict):
        return str(value.get("ru") or next(iter(value.values()), "") or "")
    if isinstance(value, str) and value.strip().startswith("{"):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return _ru_text(parsed)
        except ValueError:
            pass
    return str(value or "")


def _number(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt_rating(value: float) -> str:
    """5.0 → '5,0' — как в кабинете Aplaut."""
    return f"{value:.1f}".replace(".", ",")


def _fmt_date(value: Optional[str]) -> str:
    """'2026-10-15T19:14:30+03:00' → '15 окт. 2026' (по Алматы)."""
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if dt.tzinfo:
        dt = dt.astimezone(TIMEZONE)
    return f"{dt.day} {MONTHS_SHORT[dt.month - 1]} {dt.year}"


def _photo_urls(review: dict) -> list:
    urls = []
    for photo in review.get("photos") or []:
        if isinstance(photo, dict):
            photo = photo.get("url") or photo.get("large") or photo.get("original_url")
        if isinstance(photo, str) and photo.startswith("http"):
            urls.append(photo)
    return urls


def build_review_message(review: dict, limit: int = TELEGRAM_TEXT_LIMIT) -> str:
    """Текст уведомления (HTML) в том же порядке, что карточка отзыва в
    кабинете Aplaut: статус → рейтинг и кол-во отзывов товара → категория /
    товар → автор и дата → оценка отзыва → комментарий → теги.

    Если текст не влезает в limit, обрезается комментарий, а не шапка."""
    esc = html.escape
    product = review.get("product") or {}
    review_id = review.get("id") or ""

    header = [STATE_LABELS.get(review.get("state") or "", f"📌 {esc(str(review.get('state') or 'Новый'))}")]

    product_rating = _number(product.get("rating"))
    reviews_count = product.get("reviews_count")
    stats = []
    if product_rating is not None:
        stats.append(f"⭐ {_fmt_rating(product_rating)}")
    if reviews_count is not None:
        stats.append(f"💬 {reviews_count}")
    if stats:
        header.append("  ".join(stats))

    category = _ru_text(product.get("category_name") or review.get("category_name"))
    product_name = esc(_ru_text(product.get("name") or review.get("product_name")) or "Без названия")
    if product.get("url"):
        product_name = f'<a href="{esc(product["url"], quote=True)}">{product_name}</a>'
    header.append(f"{esc(category)} / {product_name}" if category else product_name)

    header.append("")
    author = esc(review.get("author_name") or "Аноним")
    date = _fmt_date(review.get("created_at"))
    header.append(f"👤 <b>{author}</b>" + (f"  ·  {esc(date)}" if date else ""))

    rating = _number(review.get("rating"))
    if rating is not None:
        filled = max(0, min(5, int(rating + 0.5)))
        header.append(f"{'★' * filled}{'☆' * (5 - filled)} / {_fmt_rating(rating)}")

    footer = []
    tags = [t for t in (review.get("tags") or review.get("tag_names") or []) if isinstance(t, str)]
    if tags:
        footer.append("🏷 " + ", ".join(esc(t) for t in tags))
    if review_id:
        footer.append(f'🔗 <a href="{APLAUT_REVIEW_URL.format(review_id=esc(review_id))}">Открыть в Aplaut</a>')

    sections = []
    for label, key in (("Комментарий", "body"), ("Достоинства", "pros"), ("Недостатки", "cons")):
        text = (review.get(key) or "").strip()
        if text:
            sections.append((label, text))

    def render(sections_) -> str:
        parts = ["\n".join(header)]
        for label, text in sections_:
            parts.append(f"<b>{label}:</b>\n{esc(text)}")
        if footer:
            parts.append("\n".join(footer))
        return "\n\n".join(parts)

    message = render(sections)
    overflow = len(message) - limit
    # Обрезаем самый длинный блок текста, пока сообщение не влезет в лимит.
    while overflow > 0 and sections:
        idx = max(range(len(sections)), key=lambda i: len(sections[i][1]))
        label, text = sections[idx]
        keep = len(text) - overflow - 1
        if keep <= 0:
            sections.pop(idx)
        else:
            sections[idx] = (label, text[:keep].rstrip() + "…")
        message = render(sections)
        overflow = len(message) - limit
    return message[:limit]


class AplautWebhookHandler:
    """Принимает исходящие вебхуки Aplaut (раздел «Интеграции» → «Исходящий
    вебхук») и шлет в чат уведомление о новом отзыве.

    Формат: https://aplaut.com/docs/reference/integrations/webhook-events/
    Aplaut ждет ответ 2xx за таймаут подключения (1–5 сек) и иначе повторяет
    доставку до 52 раз, поэтому отвечаем сразу, а отправку в Telegram делаем
    в фоне. Повторы отсекаются по id отзыва (таблица aplaut_notified_reviews)."""

    def __init__(
        self,
        bot: Bot,
        target_group_id: int,
        target_thread_id: Optional[int],
        secret: Optional[str] = None,
        secret_header: str = "X-Api-Key",
        events: Optional[Set[str]] = None,
        api_token: Optional[str] = None,
    ):
        self.bot = bot
        self.target_group_id = target_group_id
        self.target_thread_id = target_thread_id
        self.secret = secret or None
        self.secret_header = secret_header
        self.events = events or {"create_review"}
        self.api_token = api_token or None
        # Запасная дедупликация в памяти на случай, если таблица БД недоступна.
        self._recent_ids: deque = deque(maxlen=1000)

    def _is_authorized(self, request: web.Request, data: dict) -> bool:
        if not self.secret:
            return True
        provided = request.headers.get(self.secret_header) or data.get("secret") or ""
        return hmac.compare_digest(str(provided), self.secret)

    async def _is_new_review(self, review_id: str) -> bool:
        is_new = await mark_aplaut_review_notified(review_id)
        if is_new is None:
            # БД недоступна — дедуплицируем хотя бы в рамках процесса.
            if review_id in self._recent_ids:
                return False
            self._recent_ids.append(review_id)
            return True
        return is_new

    async def _notify(self, review: dict):
        """Отзыв с фото уходит фото с подписью (как в карточке Aplaut),
        без фото — обычным сообщением. Если Telegram не смог скачать фото,
        отправляем хотя бы текст."""
        kwargs = dict(
            chat_id=self.target_group_id,
            message_thread_id=self.target_thread_id,
            parse_mode=ParseMode.HTML,
        )
        photos = _photo_urls(review)
        try:
            if photos:
                try:
                    await self.bot.send_photo(
                        photo=photos[0],
                        caption=build_review_message(review, TELEGRAM_CAPTION_LIMIT),
                        **kwargs,
                    )
                except Exception as e:
                    logger.warning(f"Не удалось отправить фото отзыва Aplaut {review.get('id')}: {e}")
                    photos = []
            if not photos:
                await self.bot.send_message(
                    text=build_review_message(review),
                    disable_web_page_preview=True,
                    **kwargs,
                )
            monitor.update_status("Aplaut Reviews", "OK")
            logger.info(f"✅ Уведомление об отзыве Aplaut {review.get('id')} отправлено")
        except Exception as e:
            logger.error(f"❌ Ошибка отправки уведомления об отзыве Aplaut {review.get('id')}: {e}")
            monitor.update_status("Aplaut Reviews", "ERROR")

    async def send_latest_review(self) -> str:
        """Шлет в чат последний отзыв из Aplaut (Platform API). Возвращает
        короткий текст результата для ответа на команду /lastreview."""
        if not self.api_token:
            return "APLAUT_API_TOKEN не задан — не могу получить последний отзыв."
        review = await fetch_latest_review(self.api_token)
        if not review or not review.get("id"):
            monitor.update_status("Aplaut Reviews", "ERROR")
            return "Не удалось получить последний отзыв из Aplaut API (подробности в логах)."
        # Помечаем как отправленный, чтобы вебхук этого же отзыва не продублировал его.
        await self._is_new_review(str(review["id"]))
        logger.info(f"📝 Отправляю последний отзыв Aplaut {review['id']}")
        await self._notify(review)
        return "Последний отзыв отправлен."

    async def send_latest_on_first_run(self):
        """Самый первый запуск (в БД еще нет ни одного отправленного отзыва):
        один раз шлем в чат последний отзыв из Aplaut, чтобы было видно, что
        интеграция работает. Дальше отзывы приходят только вебхуком.

        Без БД пропускаем — иначе последний отзыв уходил бы при каждом рестарте
        (free-инстанс Render засыпает и перезапускается часто). В этом случае
        последний отзыв можно отправить вручную командой /lastreview."""
        if not self.api_token:
            logger.info("APLAUT_API_TOKEN не задан — последний отзыв при первом запуске не отправляется.")
            return
        has_any = await has_aplaut_notified_reviews()
        if has_any is None:
            logger.info("БД недоступна — последний отзыв Aplaut при старте не шлю, есть команда /lastreview.")
            return
        if not has_any:
            logger.info(await self.send_latest_review())


    async def handle(self, request: web.Request):
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"status": "error", "message": "Invalid JSON"}, status=400)
        if not isinstance(data, dict):
            return web.json_response({"status": "error", "message": "Invalid payload"}, status=400)

        if not self._is_authorized(request, data):
            logger.warning("Вебхук Aplaut отклонен: неверный секрет")
            return web.json_response({"status": "error", "message": "Unauthorized"}, status=401)

        event = data.get("event") or request.headers.get("X-Aplaut-Event", "")
        if event not in self.events:
            return web.json_response({"status": "ignored", "event": event})

        review_id = str(data.get("id") or "")
        if not review_id:
            return web.json_response({"status": "ignored", "message": "Missing review id"})

        if not await self._is_new_review(review_id):
            return web.json_response({"status": "duplicate", "id": review_id})

        logger.info(f"📝 Новый отзыв Aplaut {review_id} (событие {event})")
        asyncio.create_task(self._notify(data))
        return web.json_response({"status": "accepted", "id": review_id})


def register_aplaut_handlers(dp: Dispatcher, aplaut_handler: AplautWebhookHandler):
    """/lastreview — вручную отправить в топик отзывов последний отзыв из Aplaut."""

    @dp.message(F.text.regexp(r"^/lastreview(@\w+)?$"))
    async def last_review_command(message: Message):
        result = await aplaut_handler.send_latest_review()
        same_topic = (
            message.chat.id == aplaut_handler.target_group_id
            and message.message_thread_id == aplaut_handler.target_thread_id
        )
        # В топике отзывов сам отзыв и есть ответ; в остальных местах и при
        # ошибке — коротко сообщаем результат.
        if not same_topic or not result.startswith("Последний"):
            await message.reply(result)
