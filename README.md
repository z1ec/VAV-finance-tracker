# Трекер расходов

Мультивалютный веб-трекер личных расходов (HUF/EUR/RUB) с базовой валютой HUF,
дашбордом, бюджетами по категориям и ролевым доступом (admin / user).

## Стек

- **Backend:** Python 3.12, FastAPI, SQLAlchemy 2.x, Pydantic v2, Alembic, APScheduler
- **БД:** SQLite (WAL)
- **Frontend:** ванильные HTML/CSS/JS (ES-модули), Chart.js (локально, без CDN)
- **Инфраструктура:** Docker Compose, Caddy (reverse proxy + статика)

## Запуск в три команды

```bash
cp .env.example .env    # заполните SECRET_KEY и пароли пользователей
docker compose up -d --build
open http://localhost   # или просто откройте в браузере
```

Логины задаются в `.env` (`ADMIN_USERNAME`/`ADMIN_PASSWORD`,
`USER1_USERNAME`/`USER1_PASSWORD`, `USER2_USERNAME`/`USER2_PASSWORD`).
При первом старте backend сам создаст пользователей и стартовый набор категорий.

Значение `DOMAIN=localhost` (по умолчанию) держит всё на обычном HTTP для
локальной разработки. Для деплоя на реальный домен с автоматическим TLS —
см. раздел [«Деплой на сервер»](#деплой-на-сервер-https-на-порту-8443) ниже
(HTTPS отдаётся на порту 8443, а не 443).

## Деплой на сервер (HTTPS на порту 8443)

На сервере порт 443 занят Hysteria (VPN) — его трогать нельзя. Поэтому
Caddy отдаёт HTTPS на **8443**, а порт 80 остаётся свободным и используется
только для ACME HTTP-01 challenge (получение сертификата Let's Encrypt),
без раздачи сайта. `Caddyfile` уже настроен под это (`https_port 8443` +
редирект на `https://{host}:8443` для любого хоста, кроме `localhost`).

1. **Выпустить поддомен.** Нужен реальный домен/поддомен с A-записью,
   указывающей на публичный IP сервера — Let's Encrypt не выдаёт
   сертификат на голый IP, и без домена ACME-проверка невозможна. Например:
   `expenses.example.com A <IP сервера>`. Дождитесь, пока запись разрешится
   (`dig +short expenses.example.com`).

2. **Открыть порты в файрволе** (443 не трогаем — он занят Hysteria):
   ```bash
   sudo ufw allow 80/tcp     # ACME HTTP-01 challenge
   sudo ufw allow 8443/tcp   # сам сайт
   ```

3. **Склонировать репозиторий на сервер и настроить `.env`:**
   ```bash
   git clone <репозиторий> expense-tracker && cd expense-tracker
   cp .env.example .env
   ```
   В `.env` обязательно указать:
   ```
   SECRET_KEY=<сгенерировать: openssl rand -hex 32>
   ADMIN_USERNAME=admin
   ADMIN_PASSWORD=<надёжный пароль>
   USER1_USERNAME=...
   USER1_PASSWORD=<надёжный пароль>
   USER2_USERNAME=...
   USER2_PASSWORD=<надёжный пароль>
   DOMAIN=expenses.example.com
   ```

4. **Запустить:**
   ```bash
   docker compose up -d --build
   ```
   Caddy сам получит сертификат Let's Encrypt при первом запросе к домену
   (через порт 80) и будет отдавать сайт по HTTPS на 8443. Проверить:
   ```bash
   curl -I http://expenses.example.com/          # -> 301 на https://…:8443/
   curl -I https://expenses.example.com:8443/api/health
   docker compose logs caddy --tail 50            # искать "certificate obtained successfully"
   ```
   Сайт открывается в браузере по адресу
   **`https://expenses.example.com:8443`**.

5. **Обновление после изменений в коде:**
   ```bash
   git pull
   docker compose up -d --build
   ```

**Если поддомен пока не готов** — можно временно оставить `DOMAIN=localhost`
в `.env`: сайт будет работать по HTTP на порту 80 (без TLS) и по
самоподписанному сертификату на 8443 (с предупреждением браузера). Как
только DNS-запись появится, поменяйте `DOMAIN` в `.env` и перезапустите
`docker compose up -d` — Caddy сам всё переключит и получит настоящий
сертификат.

---

## Структура проекта

```
expense-tracker/
├── docker-compose.yml
├── Caddyfile
├── .env.example
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── alembic.ini
│   ├── migrations/          # схема БД версионируется через Alembic
│   ├── tests/                # pytest: currency.py + права доступа
│   └── app/
│       ├── main.py           # сборка FastAPI, миграции при старте, обработчики ошибок
│       ├── config.py         # pydantic-settings
│       ├── db.py             # engine/session, WAL
│       ├── models.py         # SQLAlchemy ORM
│       ├── schemas.py        # Pydantic-схемы
│       ├── auth.py           # bcrypt, подписанная cookie-сессия, лимит попыток входа
│       ├── deps.py           # get_current_user / require_admin
│       ├── currency.py       # конвертация, округление, выбор и приоритет курса
│       ├── scheduler.py      # ежедневное обновление курсов и бэкапы (APScheduler)
│       ├── seed.py           # сид пользователей и категорий
│       └── routers/          # auth, expenses, categories, rates, stats, budgets
└── frontend/
    ├── index.html             # Операции
    ├── login.html
    ├── dashboard.html
    ├── settings.html          # только для admin
    ├── css/style.css          # дизайн-система, тёмная тема через CSS-переменные
    ├── js/
    │   ├── api.js             # обёртка над fetch, единая обработка ошибок/401
    │   ├── common.js          # шапка, тема, валюта отображения, форматирование, TZ
    │   ├── expenses.js
    │   ├── dashboard.js
    │   └── settings.js
    └── vendor/chart.umd.min.js
```

## Ключевые принципы реализации

- **HUF — базовая валюта.** Все суммы нормализуются в HUF при сохранении
  (`amount_huf`), вся агрегация в отчётах считается по HUF и уже на фронте
  пересчитывается в выбранную валюту отображения (переключатель в шапке,
  хранится в `localStorage`).
- **Курс фиксируется в момент операции** и хранится в самой записи
  (`rate_to_huf`, `rate_source`) — изменение курсов в будущем не трогает
  исторические отчёты.
- Источник курсов — `@fawazahmed0/currency-api` (без ключа и лимитов,
  поддерживает RUB), с резервом на `open.er-api.com`, если основной недоступен.
- Порядок выбора курса при создании операции: курс на дату операции →
  ближайший предыдущий доступный курс → запрос к API на нужную дату →
  422 с предложением ввести курс вручную. Ручной курс имеет приоритет и не
  перезаписывается планировщиком.
- **Время** хранится в UTC (ISO 8601), отображается в зоне `Europe/Budapest`
  через `Intl.DateTimeFormat`/`Intl.NumberFormat` (`ru-RU`) — учитывается
  переход между UTC+1/UTC+2.
- Роль пользователя проверяется на уровне API (`require_admin`), а не только
  скрытием кнопок в интерфейсе.

## Тесты

```bash
cd backend
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest -q
```

Покрыто: конвертация валют и округление (`HUF` — 0 знаков, `EUR`/`RUB` — 2,
через `Decimal`), выбор курса при отсутствии данных на нужную дату,
приоритет ручного курса над курсом из API, а также права доступа —
`user` получает 403 на всех админских эндпоинтах.

## Бэкапы

Ежедневно в 03:00 UTC создаётся копия БД (`sqlite3 .backup`) в
`/data/backups/expenses-YYYY-MM-DD.db`, хранятся последние 14 копий.
Курсы валют обновляются ежедневно в 04:00 UTC; при старте приложения,
если курса за сегодня ещё нет, он подтягивается сразу.
# VAV-finance-tracker
