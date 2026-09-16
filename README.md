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

## Деплой на сервер

Есть два сценария в зависимости от того, свободны ли порты 80/443 на хосте.

### Вариант A — сервер выделен под этот проект (порты 80/443 свободны)

Используются `docker-compose.yml` + `Caddyfile` — Caddy сам получает
сертификат Let's Encrypt и отдаёт сайт по HTTPS.

1. Выпустить поддомен с A-записью на IP сервера (на голый IP сертификат не
   выдаётся). Дождаться, пока DNS разрешится: `dig +short expenses.example.com`.
2. `cp .env.example .env`, заполнить `SECRET_KEY` (`openssl rand -hex 32`),
   пароли пользователей и `DOMAIN=expenses.example.com`.
3. `docker compose up -d --build`.
4. Проверить: `docker compose logs caddy --tail 50` — искать
   `certificate obtained successfully`. Сайт: `https://expenses.example.com:8443`
   (HTTPS отдаётся на **8443**, не на 443 — см. Caddyfile; порт 80 нужен
   только для ACME-проверки).

### Вариант B — на сервере уже есть другой сайт на 80/443 (nginx/Apache/Caddy)

Именно этот случай, если на хосте уже висит другой сайт и/или VPN
(например, Hysteria) — трогать существующие 80/443 нельзя. Тогда наш Caddy
вообще не публикует эти порты: он слушает только `127.0.0.1:8080`
(изнутри сервера), TLS не запрашивает, а существующий веб-сервер
проксирует на него и сам занимается сертификатом.

Используются `docker-compose.behind-proxy.yml` + `Caddyfile.behind-proxy`.

1. Выпустить поддомен с A-записью на IP сервера (у регистратора/в вашей DNS-зоне),
   например `finance.example.com A <IP сервера>`.

2. Настроить `.env` как обычно (`cp .env.example .env`, `SECRET_KEY`,
   пароли, `DOMAIN=finance.example.com` — используется backend'ом для
   флага `secure` на cookie-сессии, публичные порты при этом не занимает).

3. Поднять стек на внутреннем порту:
   ```bash
   docker compose -f docker-compose.behind-proxy.yml up -d --build
   curl -I http://127.0.0.1:8080/api/health   # должно быть 200, с самого сервера
   ```

4. Добавить сайт в существующий nginx (замените домен и, если 8080 занят
   чем-то ещё, поменяйте порт — он должен совпадать с `CADDY_INTERNAL_PORT`
   из `.env`, по умолчанию 8080):
   ```nginx
   # /etc/nginx/sites-available/finance.example.com
   server {
       listen 80;
       listen [::]:80;
       server_name finance.example.com;

       location / {
           proxy_pass http://127.0.0.1:8080;
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto $scheme;
       }
   }
   ```
   ```bash
   sudo ln -s /etc/nginx/sites-available/finance.example.com /etc/nginx/sites-enabled/
   sudo nginx -t && sudo systemctl reload nginx
   ```

5. Получить сертификат через certbot (если ещё не установлен:
   `sudo apt install certbot python3-certbot-nginx`):
   ```bash
   sudo certbot --nginx -d finance.example.com
   ```
   Certbot сам допишет в конфиг `listen 443 ssl` и редирект с 80 на 443,
   перезагрузит nginx. Сайт: `https://finance.example.com` (обычный 443,
   которым теперь занимается nginx, а не наш Caddy).

**Обновление после изменений в коде** (для обоих вариантов — используйте
тот же compose-файл, каким поднимали):
```bash
git pull
docker compose up -d --build                              # вариант A
docker compose -f docker-compose.behind-proxy.yml up -d --build   # вариант B
```

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
