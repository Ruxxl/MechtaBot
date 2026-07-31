import logging
import os
from typing import List, Optional

import asyncpg

logger = logging.getLogger("bot.db")

_pool: Optional[asyncpg.Pool] = None

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS stand_builds (
    id SERIAL PRIMARY KEY,
    stand_key TEXT NOT NULL,
    stand_label TEXT NOT NULL,
    commit_message TEXT,
    actor TEXT,
    build_date TEXT,
    url TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_stand_builds_stand_key_created_at
    ON stand_builds (stand_key, created_at DESC);

CREATE TABLE IF NOT EXISTS monthly_report_state (
    id BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (id),
    last_sent_month TEXT
);
"""


async def init_db():
    """Создает пул соединений и таблицу (если её еще нет). Вызывается один раз при
    старте бота. Если DATABASE_URL не задан — бот продолжает работать как раньше,
    только на in-memory кэше (latest_builds в webhook_handler.py)."""
    global _pool
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        logger.warning("DATABASE_URL не задан — история заливок в БД сохраняться не будет.")
        return

    try:
        _pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=5)
        async with _pool.acquire() as conn:
            await conn.execute(CREATE_TABLE_SQL)
        logger.info("✅ Подключение к Postgres установлено, таблица stand_builds готова.")
    except Exception as e:
        logger.error(f"❌ Не удалось подключиться к Postgres: {e}")
        _pool = None


async def close_db():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


async def save_build(stand_key: str, stand_label: str, commit_message: str, actor: str, build_date: str, url: str):
    """Сохраняет запись об успешной заливке на стенд."""
    if not _pool:
        return
    try:
        async with _pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO stand_builds (stand_key, stand_label, commit_message, actor, build_date, url)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                stand_key, stand_label, commit_message, actor, build_date, url,
            )
    except Exception as e:
        logger.error(f"Ошибка сохранения заливки в БД (stand_key={stand_key}): {e}")


async def get_latest_builds(stand_key: str, limit: int = 5) -> List[dict]:
    """Последние N заливок по конкретному стенду, свежие первыми.
    Формат записей совпадает с тем, что уже используется в latest_builds
    (webhook_handler.py) и в fetch_latest_build_from_api (GitHub API),
    поэтому main.py может использовать этот результат так же, как и остальные."""
    if not _pool:
        return []
    try:
        async with _pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT commit_message, actor, build_date, url
                FROM stand_builds
                WHERE stand_key = $1
                ORDER BY created_at DESC
                LIMIT $2
                """,
                stand_key, limit,
            )
        return [
            {"commit": r["commit_message"], "actor": r["actor"], "date": r["build_date"], "url": r["url"]}
            for r in rows
        ]
    except Exception as e:
        logger.error(f"Ошибка чтения истории заливок из БД (stand_key={stand_key}): {e}")
        return []


async def get_last_sent_report_month() -> Optional[str]:
    """Месяц (формат "YYYY-MM"), за который ежемесячный отчет уже был отправлен.
    Хранится в БД (а не в памяти процесса), чтобы рестарт/редеплой бота в
    отчетный день не приводил к повторной отправке отчета."""
    if not _pool:
        return None
    try:
        async with _pool.acquire() as conn:
            row = await conn.fetchrow("SELECT last_sent_month FROM monthly_report_state LIMIT 1")
        return row["last_sent_month"] if row else None
    except Exception as e:
        logger.error(f"Ошибка чтения статуса ежемесячного отчета из БД: {e}")
        return None


async def set_last_sent_report_month(month_key: str):
    """Отмечает месяц как отправленный."""
    if not _pool:
        return
    try:
        async with _pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO monthly_report_state (id, last_sent_month) VALUES (TRUE, $1)
                ON CONFLICT (id) DO UPDATE SET last_sent_month = EXCLUDED.last_sent_month
                """,
                month_key,
            )
    except Exception as e:
        logger.error(f"Ошибка сохранения статуса ежемесячного отчета в БД: {e}")