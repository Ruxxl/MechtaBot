"""
web/miniapp_api.py

JSON API для Telegram Mini App (фронт: https://ruxxl.github.io/telegramminiapp/).

Ничего в существующей архитектуре бота не меняет — это отдельный модуль
с набором aiohttp-роутов. Подключается в main.py одной строкой:

    from web.miniapp_api import setup_miniapp_routes
    setup_miniapp_routes(app, services=my_services_dict)

Где `services` — словарь с уже существующими у тебя клиентами/функциями
(Jira, GitHub webhook storage, Confluence-поиск, конфиг стендов и т.д.).
Если конкретный сервис не передан — эндпоинт отдаёт мок-данные, чтобы
Mini App не падал во время поэтапной интеграции.

============================================================================
КОНТРАКТ ЭНДПОИНТОВ (должен точно соответствовать fetch() во фронте)
============================================================================

GET  /api/monthreport
     -> { tasks: int, bugs: int, releases: int,
          days: [str, ...], byDay: [int, ...],
          taskList: [{key, summary, status}, ...] }

GET  /api/release/latest
     -> { version, date, description, url|null }

GET  /api/github/last-event
     -> { actor, branch, commit, time, conclusion: "success"|"failure"|"pending" }

GET  /api/release/next-status
     -> { name, total, done, inProgress, pending }

GET  /api/stands
     -> [ { key, label, url, status: "ok"|"warn"|"danger" }, ... ]

GET  /api/stands/{key}
     -> [ { commit, actor, date }, ... ]   (последние сборки стенда)

POST /api/jira/create
     body: { title, description, priority, component, author }
     -> { key, url }

POST /api/ask
     body: { question }
     -> { answer, sources: [{title, url}, ...] }

GET  /api/help
     -> [ { key, label, text }, ... ]   (text может содержать простой HTML: <code>)

============================================================================
БЕЗОПАСНОСТЬ
============================================================================
Каждый запрос с фронта несёт заголовок X-Telegram-Init-Data — это initData
из Telegram.WebApp. Мы проверяем его подпись (HMAC-SHA256) прямо здесь,
без обращения к Telegram API. Без валидной подписи — 401.

CORS: фронт живёт на github.io, backend — на другом домене, поэтому нужны
CORS-заголовки. Разрешён только домен из ALLOWED_ORIGIN (по умолчанию —
GitHub Pages страница мини-аппа).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime, timedelta
from functools import wraps
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import parse_qsl

from aiohttp import web

logger = logging.getLogger("miniapp_api")

# ============================================================================
# КОНФИГ
# ============================================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ALLOWED_ORIGIN = os.environ.get(
    "MINIAPP_ALLOWED_ORIGIN", "https://ruxxl.github.io"
)
# Сколько секунд считаем initData ещё свежим (Telegram рекомендует ~1 день макс)
INIT_DATA_MAX_AGE = int(os.environ.get("MINIAPP_INITDATA_MAX_AGE", 86400))

# Если True — пропускаем проверку initData (только для локальной разработки!)
DEV_SKIP_AUTH = os.environ.get("MINIAPP_DEV_SKIP_AUTH", "false").lower() == "true"


# ============================================================================
# ПРОВЕРКА TELEGRAM initData
# ============================================================================

def verify_init_data(init_data: str, bot_token: str) -> Optional[dict]:
    """
    Валидирует initData по алгоритму Telegram:
    https://core.telegram.org/bots/webapps#validating-data-received-via-the-web-app

    Возвращает распарсенные данные (dict) если подпись верна и данные свежие,
    иначе None.
    """
    if not init_data or not bot_token:
        return None

    try:
        pairs = dict(parse_qsl(init_data, strict_parsing=True))
    except ValueError:
        return None

    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None

    data_check_string = "\n".join(
        f"{k}={v}" for k, v in sorted(pairs.items())
    )

    secret_key = hmac.new(
        key=b"WebAppData", msg=bot_token.encode(), digestmod=hashlib.sha256
    ).digest()
    computed_hash = hmac.new(
        key=secret_key, msg=data_check_string.encode(), digestmod=hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        return None

    auth_date = pairs.get("auth_date")
    if auth_date:
        age = time.time() - int(auth_date)
        if age > INIT_DATA_MAX_AGE:
            return None

    if "user" in pairs:
        try:
            pairs["user"] = json.loads(pairs["user"])
        except (json.JSONDecodeError, TypeError):
            pass

    return pairs


def require_auth(handler: Callable[[web.Request], Awaitable[web.Response]]):
    """Декоратор: требует валидный X-Telegram-Init-Data на роуте."""

    @wraps(handler)
    async def wrapped(request: web.Request) -> web.Response:
        if DEV_SKIP_AUTH:
            request["tg_user"] = {"id": 0, "first_name": "Dev"}
            return await handler(request)

        init_data = request.headers.get("X-Telegram-Init-Data", "")
        parsed = verify_init_data(init_data, BOT_TOKEN)
        if parsed is None:
            raise web.HTTPUnauthorized(
                text=json.dumps({"error": "invalid_init_data"}),
                content_type="application/json",
            )
        request["tg_user"] = parsed.get("user", {})
        return await handler(request)

    return wrapped


# ============================================================================
# CORS
# ============================================================================

@web.middleware
async def cors_middleware(request: web.Request, handler):
    if request.method == "OPTIONS":
        resp = web.Response()
    else:
        try:
            resp = await handler(request)
        except web.HTTPException as exc:
            resp = exc
    resp.headers["Access-Control-Allow-Origin"] = ALLOWED_ORIGIN
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Telegram-Init-Data"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


# ============================================================================
# РОУТЫ
# ============================================================================

routes = web.RouteTableDef()


def json_response(data: Any, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


# ---------------------------------------------------------------------------
# /api/monthreport
# ---------------------------------------------------------------------------

@routes.get("/api/monthreport")
@require_auth
async def get_monthreport(request: web.Request) -> web.Response:
    services = request.app["miniapp_services"]
    jira = services.get("jira")

    if jira is None:
        return json_response(_mock_monthreport())

    # TODO: подключить реальный источник — например, тот же JQL/логику,
    # что использует существующая команда /monthreport в боте.
    # Пример ожидаемой формы вызова (адаптируй под свой jira-клиент):
    #
    #   today = datetime.now()
    #   start = today.replace(day=1)
    #   issues = await jira.search_issues_created_between(start, today)
    #   report = build_month_report(issues)  # твоя существующая функция
    #
    # и дальше просто скопировать поля report в контракт ниже.
    report = await jira.get_month_report()  # <-- метод твоего клиента
    return json_response(
        {
            "tasks": report.get("tasks", 0),
            "bugs": report.get("bugs", 0),
            "releases": report.get("releases", 0),
            "days": report.get("days", []),
            "byDay": report.get("by_day", []),
            "taskList": report.get("task_list", []),
        }
    )


def _mock_monthreport() -> dict:
    now = datetime.now()
    days = [str(d) for d in range(1, now.day + 1)]
    by_day = [((i * 7) % 5) for i in range(len(days))]
    return {
        "tasks": sum(by_day),
        "bugs": sum(by_day) // 2,
        "releases": max(1, now.day // 10),
        "days": days,
        "byDay": by_day,
        "taskList": [
            {"key": "MECHTA-1201", "summary": "Синхронизация остатков 1С", "status": "Готово"},
            {"key": "MECHTA-1198", "summary": "Ошибка при оформлении рассрочки", "status": "В работе"},
        ],
    }


# ---------------------------------------------------------------------------
# /api/release/latest
# ---------------------------------------------------------------------------

@routes.get("/api/release/latest")
@require_auth
async def get_latest_release(request: web.Request) -> web.Response:
    services = request.app["miniapp_services"]
    releases = services.get("releases")

    if releases is None:
        return json_response(
            {
                "version": "3.22.1",
                "date": datetime.now().strftime("%d.%m.%Y"),
                "description": "Мок-данные: подключи services['releases'] в main.py.",
                "url": None,
            }
        )

    # TODO: releases — твой существующий модуль, который формирует
    # AI-описание релиза для уведомлений в чат. Тут просто берём последнюю запись.
    latest = await releases.get_latest()
    return json_response(
        {
            "version": latest.version,
            "date": latest.date.strftime("%d.%m.%Y"),
            "description": latest.description,
            "url": latest.url,
        }
    )


# ---------------------------------------------------------------------------
# /api/github/last-event
# ---------------------------------------------------------------------------

@routes.get("/api/github/last-event")
@require_auth
async def get_last_github_event(request: web.Request) -> web.Response:
    services = request.app["miniapp_services"]
    github_store = services.get("github_events")

    if github_store is None:
        return json_response(
            {
                "actor": "peaceffuul",
                "branch": "predprod",
                "commit": "fix: мок — подключи services['github_events']",
                "time": datetime.now().strftime("%d.%m.%Y %H:%M"),
                "conclusion": "success",
            }
        )

    # TODO: github_store — то место, куда уже пишутся webhook-события
    # (тот же обработчик, что шлёт уведомление о деплое в чат).
    # Нужно просто прочитать последнюю запись вместо/вместе с отправкой в Telegram.
    event = await github_store.get_last_event()
    return json_response(
        {
            "actor": event["actor"],
            "branch": event["branch"],
            "commit": event["commit_message"],
            "time": event["timestamp"].strftime("%d.%m.%Y %H:%M"),
            "conclusion": event.get("conclusion", "success"),
        }
    )


# ---------------------------------------------------------------------------
# /api/release/next-status
# ---------------------------------------------------------------------------

@routes.get("/api/release/next-status")
@require_auth
async def get_next_release_status(request: web.Request) -> web.Response:
    services = request.app["miniapp_services"]
    jira = services.get("jira")

    if jira is None:
        return json_response(
            {"name": "3.23.0", "total": 14, "done": 9, "inProgress": 3, "pending": 2}
        )

    # TODO: JQL-запрос по задачам будущего релиза (fixVersion = следующий релиз).
    status = await jira.get_next_release_status()
    return json_response(
        {
            "name": status["version"],
            "total": status["total"],
            "done": status["done"],
            "inProgress": status["in_progress"],
            "pending": status["pending"],
        }
    )


# ---------------------------------------------------------------------------
# /api/stands
# ---------------------------------------------------------------------------

@routes.get("/api/stands")
@require_auth
async def get_stands(request: web.Request) -> web.Response:
    services = request.app["miniapp_services"]
    stands_config = services.get("stands")

    if stands_config is None:
        return json_response(
            [
                {"key": "preprod", "label": "PREPROD", "url": "https://pp.yc.mechta.kz/", "status": "ok"},
                {"key": "d2", "label": "D2", "url": "http://d2.im.mdev.kz/", "status": "ok"},
                {"key": "d3", "label": "D3", "url": "http://d3.im.mdev.kz/", "status": "warn"},
                {"key": "production", "label": "PRODUCTION", "url": "https://mechta.kz/", "status": "ok"},
            ]
        )

    # TODO: stands_config — конфиг, который уже используется командой /stands.
    stands = await stands_config.list_with_status()
    return json_response(
        [
            {
                "key": s["key"],
                "label": s["label"],
                "url": s["url"],
                "status": s.get("status", "ok"),
            }
            for s in stands
        ]
    )


@routes.get("/api/stands/{key}")
@require_auth
async def get_stand_builds(request: web.Request) -> web.Response:
    key = request.match_info["key"]
    services = request.app["miniapp_services"]
    stands_config = services.get("stands")

    if stands_config is None:
        return json_response(
            [
                {"commit": f"fix: мок-сборка для {key}", "actor": "nurgi17", "date": "29.07.2026 14:02"},
                {"commit": "chore: обновление зависимостей", "actor": "peaceffuul", "date": "28.07.2026 09:15"},
            ]
        )

    builds = await stands_config.get_recent_builds(key, limit=10)
    if builds is None:
        raise web.HTTPNotFound(
            text=json.dumps({"error": "stand_not_found"}), content_type="application/json"
        )
    return json_response(
        [
            {"commit": b["commit"], "actor": b["actor"], "date": b["date"]}
            for b in builds
        ]
    )


# ---------------------------------------------------------------------------
# /api/jira/create
# ---------------------------------------------------------------------------

@routes.post("/api/jira/create")
@require_auth
async def create_jira_issue(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except json.JSONDecodeError:
        raise web.HTTPBadRequest(
            text=json.dumps({"error": "invalid_json"}), content_type="application/json"
        )

    title = (body.get("title") or "").strip()
    if not title:
        raise web.HTTPBadRequest(
            text=json.dumps({"error": "title_required"}), content_type="application/json"
        )

    description = (body.get("description") or "").strip()
    priority = body.get("priority", "Medium")
    component = (body.get("component") or "").strip()
    author = body.get("author", "Mini App")
    tg_user = request.get("tg_user", {})

    services = request.app["miniapp_services"]
    jira = services.get("jira")

    if jira is None:
        logger.warning("services['jira'] не подключен — эмулирую создание задачи")
        return json_response({"key": "MECHTA-0000", "url": None})

    # TODO: jira — тот же клиент, что уже используется ботом для #bug/#jira.
    # Добавляем в описание, кто именно создал задачу из Mini App (для трассируемости).
    full_description = (
        f"{description}\n\n"
        f"---\nСоздано через Mini App: {author} "
        f"(tg_id={tg_user.get('id', '—')})"
    )
    issue = await jira.create_issue(
        title=title,
        description=full_description,
        priority=priority,
        component=component or None,
    )
    return json_response({"key": issue.key, "url": issue.url})


# ---------------------------------------------------------------------------
# /api/ask
# ---------------------------------------------------------------------------

@routes.post("/api/ask")
@require_auth
async def ask_question(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except json.JSONDecodeError:
        raise web.HTTPBadRequest(
            text=json.dumps({"error": "invalid_json"}), content_type="application/json"
        )

    question = (body.get("question") or "").strip()
    if not question:
        raise web.HTTPBadRequest(
            text=json.dumps({"error": "question_required"}), content_type="application/json"
        )

    services = request.app["miniapp_services"]
    ask_engine = services.get("ask")

    if ask_engine is None:
        return json_response(
            {
                "answer": f"Мок-ответ (подключи services['ask']). Вопрос был: «{question}»",
                "sources": [],
            }
        )

    # TODO: ask_engine — то же, что уже используется командой /ask
    # (поиск по Confluence + генерация ответа).
    result = await ask_engine.answer(question)
    return json_response(
        {
            "answer": result["answer"],
            "sources": [{"title": s["title"], "url": s["url"]} for s in result.get("sources", [])],
        }
    )


# ---------------------------------------------------------------------------
# /api/help
# ---------------------------------------------------------------------------

@routes.get("/api/help")
@require_auth
async def get_help(request: web.Request) -> web.Response:
    services = request.app["miniapp_services"]
    help_data = services.get("help")

    if help_data is not None:
        # TODO: help_data — если категории уже описаны в help_handler.py как
        # структура (список/словарь), просто преобразуй её в этот контракт.
        return json_response(help_data.as_miniapp_list()) # Теперь этот вызов будет работать

    return json_response(
        [
            {
                "key": "jira",
                "label": "🐞 Jira и баги",
                "text": "<code>#bug</code>/<code>#jira</code> в тексте или фото — создаст подзадачу. "
                        "<code>/jira</code> — пошаговая форма. <code>#check</code> — проверка живости.",
            },
            {
                "key": "stress",
                "label": "🚦 Нагрузочное тестирование",
                "text": "<code>/stress</code> — выбери стенд, число пользователей и длительность.",
            },
            {
                "key": "faq",
                "label": "📚 FAQ / документация",
                "text": "<code>/ask вопрос</code> или тег <code>#faq</code> — поиск ответа в Confluence.",
            },
            {
                "key": "team",
                "label": "👥 Команда и спринт",
                "text": "<code>/team</code> — задачи участника в активном спринте.",
            },
            {
                "key": "github",
                "label": "🚀 GitHub и стенды",
                "text": "<code>/stands</code> — статус последней сборки по каждому стенду.",
            },
            {
                "key": "misc",
                "label": "📅 Напоминания, релизы и HR",
                "text": "Утро/вечер — напоминание про Clockster. 30 числа — месячный отчет. "
                        "<code>#hr</code> — HR-темы.",
            },
        ]
    )


# ============================================================================
# ПОДКЛЮЧЕНИЕ К СУЩЕСТВУЮЩЕМУ aiohttp-ПРИЛОЖЕНИЮ
# ============================================================================

def setup_miniapp_routes(app: web.Application, services: Optional[dict] = None) -> None:
    """
    Вызвать один раз в main.py, там же где поднимается aiohttp-сервер бота:

        from web.miniapp_api import setup_miniapp_routes

        setup_miniapp_routes(app, services={
            "jira": jira_client,          # твой существующий Jira-клиент
            "github_events": github_store, # хранилище последних webhook-событий
            "stands": stands_config,       # конфиг/сервис стендов
            "ask": ask_engine,             # confluence-поиск для /ask
            "help": help_data,             # категории help, если структурированы
            "releases": releases_service,  # источник данных о релизах
        })

    Любой сервис можно не передавать — соответствующий эндпоинт тогда
    отдаёт мок-данные вместо ошибки 500, чтобы фронт не разваливался
    во время поэтапного подключения.
    """
    if BOT_TOKEN == "" and not DEV_SKIP_AUTH:
        logger.warning(
            "BOT_TOKEN не задан — проверка initData всегда будет проваливаться. "
            "Установи переменную окружения BOT_TOKEN, либо MINIAPP_DEV_SKIP_AUTH=true для локальной разработки."
        )

    app["miniapp_services"] = services or {}
    app.middlewares.append(cors_middleware)
    app.add_routes(routes)

    # Явный OPTIONS-хендлер для preflight-запросов браузера
    async def options_handler(request: web.Request) -> web.Response:
        return web.Response()

    for path in [
        "/api/monthreport",
        "/api/release/latest",
        "/api/github/last-event",
        "/api/release/next-status",
        "/api/stands",
        "/api/stands/{key}",
        "/api/jira/create",
        "/api/ask",
        "/api/help",
    ]:
        app.router.add_route("OPTIONS", path, options_handler)

    logger.info("Mini App API routes зарегистрированы (%d сервисов подключено)", len(services or {}))