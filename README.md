# MechtaBot

Telegram-бот на aiogram, интегрированный с Jira, GitHub Actions и Groq AI.
Деплоится на Render как worker-процесс (см. `Procfile`).

## Структура проекта

```
.
├── main.py                     # точка входа, регистрация хендлеров, запуск фоновых задач
├── Procfile
├── requirements.txt
│
├── web/                        # aiohttp веб-роуты (health-check, вебхуки, админка)
│   ├── admin_handler.py        # GET /admin — дашборд со статусами и логом ошибок
│   └── webhook_handler.py      # POST /webhook/notify — приём вебхуков GitHub Actions
│
├── services/                   # переиспользуемые сервисы, не привязанные к конкретным сообщениям
│   ├── ai_service.py           # обёртка над Groq (текст + vision)
│   ├── calendar_service.py     # мониторинг ICS-календаря, уведомления о встречах
│   └── translator_service.py   # перевод RU → KK в теме переводчика
│
├── handlers/                   # обработка входящих сообщений/команд от пользователей
│   ├── jira_fsm.py             # команда /jira (создание дефекта через диалог) + create_jira_issue
│   ├── photo_handler.py        # фото с тегом #bug/#jira → задача в Jira
│   ├── text_handler.py         # текст с тегом #bug/#jira → задача в Jira
│   ├── vision_handler.py       # анализ скриншотов в теме VISION_THREAD_ID
│   └── daily_reminder.py       # утреннее/вечернее напоминание + кнопка статуса релиза
│
├── monitors/                   # фоновые задачи, которые сами ходят в Jira по таймеру
│   ├── code_review_handler.py  # мониторинг задач в статусе "Код ревью"
│   ├── release_notifier.py     # уведомление о выходе релиза
│   └── monthly_report.py       # ежемесячный отчет по релизам/задачам/багам
│
├── data/                       # статичные данные/конфиги без логики
│   └── hr_topics.py            # тексты HR-меню (#hr)
│
└── assets/                     # картинки для сообщений бота
    ├── release.jpg
    ├── event.jpg
    └── monthryreport.jpg
```

## Принципы (см. также комментарии в коде)

- Состояние между запусками фоновых задач хранится в памяти процесса
  (`processed_issues`, `notified_versions`, `_last_sent_month` и т.д.) —
  осознанно принятый риск дублирования при перезапуске Render-дайно.
- Свежие данные по Jira запрашиваются каждый раз заново, а не кэшируются
  в локальные JSON/БД.
- Все HTTP-вызовы — через `aiohttp`, AI-вызовы — через `AsyncGroq`.
- Часовой пояс везде — `Asia/Almaty`.

## Запуск

```bash
pip install -r requirements.txt
python3 main.py
```

Переменные окружения (Render → Environment): `BOT_TOKEN`, `GROQ_API_KEY`,
`JIRA_EMAIL`, `JIRA_API_TOKEN`, `JIRA_PROJECT_KEY`, `JIRA_PARENT_KEY`,
`JIRA_URL`, `GITHUB_TOKEN`, `GITHUB_REPO`, `ICS_URL`, `CALENDAR_CHECK_INTERVAL`.
