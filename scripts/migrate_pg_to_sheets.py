"""Одноразовый перенос данных из Postgres в Google Таблицу "MechtaBot DB".

    pip install asyncpg aiohttp
    DATABASE_URL=... GSHEETS_DB_URL=... GSHEETS_DB_SECRET=... python scripts/migrate_pg_to_sheets.py

Листы в таблице должны быть пустыми (кроме заголовков), иначе данные задублируются.
"""

import asyncio
import os
from datetime import timezone

import aiohttp
import asyncpg

TABLES = {
    "stand_builds": "SELECT stand_key, stand_label, commit_message, actor, build_date, url, created_at "
                    "FROM stand_builds ORDER BY created_at",
    "monthly_report_state": "SELECT 1 AS id, last_sent_month FROM monthly_report_state",
    "autotest_bug_subtasks": "SELECT subtask_key, parent_key, created_at FROM autotest_bug_subtasks ORDER BY created_at",
    "autotest_runs": "SELECT run_id, run_number, status, conclusion, branch, actor, commit_message, url, run_date, "
                     "tests, passing, failing, pending, skipped, created_at FROM autotest_runs ORDER BY created_at",
    "autotest_run_specs": "SELECT run_id, spec_name, duration, tests, passing, failing, pending, skipped "
                          "FROM autotest_run_specs ORDER BY id",
    "aplaut_notified_reviews": "SELECT review_id, created_at FROM aplaut_notified_reviews ORDER BY created_at",
}


def _plain(value):
    if value is None:
        return None
    if hasattr(value, "astimezone"):
        # тот же формат, что у new Date().toISOString() в Apps Script — для сортировки строкой
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"
    return str(value)


async def main():
    url, secret = os.environ["GSHEETS_DB_URL"], os.environ["GSHEETS_DB_SECRET"]
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=300)) as http:
        for sheet, query in TABLES.items():
            try:
                records = await conn.fetch(query)
            except asyncpg.UndefinedTableError:
                print(f"{sheet}: таблицы нет в Postgres, пропуск")
                continue
            rows = [{k: _plain(v) for k, v in r.items()} for r in records]
            for i in range(0, len(rows), 500):
                async with http.post(url, json={"secret": secret, "action": "append", "sheet": sheet,
                                                "rows": rows[i:i + 500]}) as resp:
                    data = await resp.json(content_type=None)
                if not data.get("ok"):
                    raise RuntimeError(f"{sheet}: {data.get('error')}")
            print(f"{sheet}: перенесено {len(rows)}")
    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
