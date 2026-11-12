# STAGE 1 — трекер: переезд в Дубай, CI/CD, база качества, документация

> Задание для Claude Code в репозитории **трекера расходов** (`z1ec/VAV-finance-tracker`, проверь через
> `gh repo view`). Работай автономно: не задавай вопросов, допущения записывай в `docs/DEVLOG.md`
> (раздел «Assumptions»). Владелец заранее разрешил доступ к обоим серверам по SSH.
>
> **Серверы** (алиасы уже настроены в `~/.ssh/config` владельца, вход по ключу, пользователь root):
> - `ssh ru-tracker` — российский сервер, где трекер работает **сейчас**. Там же живёт **чужой сайт на порту 443 — не трогать никак**;
> - `ssh dubai` — сервер в Дубае, IP `81.85.92.119`. Там уже работает бот владельца Momentum (`/opt/momentum`, пользователь `deploy`, ufw включён, открыт только SSH). **Трекер переезжает сюда.**
>
> **Домен**: `finance.fomin-vladimir.ru`. Перед запуском владелец перевёл его A-запись на `81.85.92.119` (DNS в reg.ru).

---

## 0. Контекст
- Трекер — мультивалютный учёт расходов (HUF/EUR/RUB, база HUF): FastAPI + SQLAlchemy 2 + Alembic + SQLite (WAL), ванильный фронтенд, Caddy. Сейчас на `ru-tracker` он запущен по варианту A из README: Caddy на портах 80 (ACME) и 8443 (HTTPS).
- Деплой ручной, CI нет, линтера нет, 18 тестов.
- **Почему переезд**: бот и трекер будут на одном сервере и будут общаться по внутренней сети Docker. API для бота не выходит в интернет. Плюс Gemini из России недоступен, а весь ИИ всё равно в боте.
- **Образец** — бот `z1ec/VAV-tg-ai-task-manager`. Склонируй его **только для чтения** в `/tmp/momentum-ref` и возьми оттуда паттерны:
  - `.github/workflows/ci.yml`, `deploy.yml`;
  - `deploy/deploy.sh` (health check + автооткат + чистка образов) и его dry-run тесты `tests/unit/test_deploy_script.py`;
  - `deploy/docker-compose.prod.yml`, `Dockerfile` (non-root, HEALTHCHECK);
  - стиль `CLAUDE.md`, `docs/DEVLOG.md`, ADR, `docs/DEPLOYMENT.md`.
- Это первый из пяти этапов. Дальше: API для бота и подписки, интеграция в боте, виджеты, аудит безопасности.

## 1. Правила работы
1. Перенеси этот файл в `docs/briefs/2026-11-12-stage1-migration-cicd.md` (создай папку), отдельным коммитом.
2. Ветка `feat/stage1-cicd`. **Не пушь в `main` напрямую**: PR → зелёный CI → merge.
3. Коммиты — Conventional Commits на английском. Код и документация — на английском, кроме `*.ru.md`. Интерфейс сайта остаётся русским.
4. Секреты (пароли, ключи, токены, содержимое `.env`) **никогда** не выводи в терминал, логи и коммиты. Ключи `.env` показывать можно, значения — нет. Файлы с секретами передавай между серверами потоком через SSH, не сохраняя их на рабочей машине.
5. **`ru-tracker` (жёстко)**:
   - не изменять firewall, sshd, чужой сайт, его конфиги и сертификаты, чужие контейнеры;
   - со стеком трекера разрешено только: читать, делать бэкапы, `docker compose stop` / `down` **без `-v`**;
   - ничего не удалять.
6. **`dubai` (жёстко)**:
   - не нарушать работу бота: `/opt/momentum`, его контейнер и `.env` не трогать, кроме случаев, прямо описанных ниже;
   - ufw: только **добавлять** разрешения для 80/tcp, 443/tcp, 8443/tcp; правило SSH не трогать;
   - не удалять данные, тома, бэкапы; без `docker system prune`.
7. Каждое действие на любом сервере записывай в `docs/DEVLOG.md`: что, где, зачем, без секретов.
8. Перед завершением шага: `ruff check`, `ruff format --check`, `mypy`, `pytest` зелёные.

## 2. Быстрый переезд — делается ПЕРВЫМ, до любой работы с кодом
Цель — чтобы трекер как можно скорее снова работал, уже в Дубае. Простой сайта ограничен этим разделом.

1. **DNS.** `dig +short A finance.fomin-vladimir.ru @1.1.1.1` и `@8.8.8.8` → должен быть `81.85.92.119`. Проверь и `AAAA`: если запись есть и указывает не в Дубай, это ручной шаг владельца, запиши его в отчёт.
   - Если A-запись ещё старая — опрашивай раз в 5 минут до 2 часов.
   - Не дождался — остановись: напиши в `MANUAL_STEPS.ru.md`, что поменять в reg.ru, и выведи отчёт.
2. **Разведка `ru-tracker`, только чтение.** Собери и запиши в DEVLOG:
   - каталог стека (метка `com.docker.compose.project.working_dir`), имя compose-проекта, версии Docker;
   - **ключи** (не значения) из `.env`;
   - кто слушает порты (`ss -tlnup`);
   - HTTP-код чужого сайта на 443 (его домен возьми из конфига веб-сервера, только чтение) — это базовая линия.
3. **Разведка `dubai`, только чтение.** Собери и запиши в DEVLOG:
   - `uname -m`, `free -m`, `df -h`, swap, `ufw status`, `ss -tlnup`;
   - состояние бота (`docker ps`, `healthy`).

   Если RAM меньше 2 ГБ и swap меньше 1 ГБ — создай swap-файл на 2 ГБ (запиши в DEVLOG).
4. **Заморозка и бэкап на `ru-tracker`**:
   1. `docker compose stop` стека трекера — чтобы никто не писал в базу во время копирования;
   2. `sqlite3 .backup` в `backups/pre-migration-<дата>.db` рядом со стеком, затем `PRAGMA integrity_check`;
   3. число строк в `users`, `categories`, `expenses`, `exchange_rates`, `budgets`, `settings` и `sha256sum` файла бэкапа — в DEVLOG.
5. **Перенос.** На `dubai` создай `/opt/finance-tracker/{data,backups}`, затем потоком через рабочую машину, без промежуточных файлов (`ssh ru-tracker "tar -C … -czf - …" | ssh dubai "tar -C … -xzf -"`):
   - бэкап БД → `/opt/finance-tracker/data/expenses.db`;
   - `.env` → `/opt/finance-tracker/.env`, права `600`.

   На `dubai`:
   - сверь `sha256sum`;
   - `integrity_check`;
   - число строк.
6. **Временный запуск в Дубае** — тем же способом, что работал раньше, без CI:
   1. код из `main`: `git archive origin/main | ssh dubai "tar -x -C /opt/finance-tracker/bootstrap-src"`;
   2. compose override с портами `80:80`, `443:8443`, `8443:8443` (Caddyfile слушает 8443, поэтому хост 443 ведёт туда же) и данными из `/opt/finance-tracker/data`;
   3. `ufw allow 80/tcp`, `443/tcp`, `8443/tcp`;
   4. `docker compose up -d --build`.
7. **Проверка временного запуска**:
   - Caddy получил сертификат;
   - `https://finance.fomin-vladimir.ru/api/health` и `https://finance.fomin-vladimir.ru:8443/api/health` → `status ok`, `db ok`;
   - число строк совпадает с шагом 4;
   - бот по-прежнему `healthy`.

   Время простоя (от `stop` на `ru-tracker` до первого успешного health) — в DEVLOG.
8. **Если временный запуск не удался за 30 минут — откат:**
   1. `docker compose start` на `ru-tracker`;
   2. в `MANUAL_STEPS.ru.md` и отчёте — просьба вернуть A-запись на старый IP;
   3. остановись.

   Трекер на `ru-tracker` после удачного переезда **остаётся остановленным, но не удалённым**.

## 3. База качества кода (backend)
- `backend/pyproject.toml`: метаданные проекта, версия `1.1.0`, конфиги ruff, mypy и pytest. `requirements.txt` остаётся для образа, dev-зависимости — в `requirements-dev.txt`.
- **ruff**:
  - `line-length = 100`, правила `E, F, I, B, UP, S, ASYNC, RUF, SIM`;
  - игнор `RUF001-003` (кириллица);
  - для FastAPI — `extend-immutable-calls = ["fastapi.Depends", "fastapi.Query"]` или переход на `Annotated`;
  - почини все замечания, `ruff format` по всему backend.
- **mypy**: обычный, не strict (`check_untyped_defs = true`, `ignore_missing_imports` для сторонних без типов). Должен проходить.
- **pytest**: отчёт покрытия.
- Поведение API и интерфейса не меняется, кроме раздела 4.

## 4. Безопасный старт и образы
1. **Отказ стартовать с секретами по умолчанию**: если `DOMAIN != localhost`, а `SECRET_KEY` пустой, равен значению по умолчанию или короче 32 символов, или пароль любого заданного пользователя равен значению по умолчанию (`admin`, `user1`, `user2`) или короче 12 символов — падение при старте с понятной ошибкой **без значений**. На `localhost` — предупреждение. Тесты на обе ветки.

   **Сначала проверь прод-`.env` в Дубае** (без вывода значений: только длины и совпадение с дефолтами). Если секреты слабые — сгенерируй новые прямо на сервере. Новые пароли пользователей положи в `/root/finance-tracker-new-passwords.txt` (`600`, только root), а в отчёте попроси владельца забрать их и удалить файл.
2. **Бэкап перед миграциями**: в `lifespan` перед `alembic upgrade head`, если есть непримёненные миграции, — копия через sqlite3 backup API в `/data/backups/pre-migrate-<UTC>.db`, хранить 10 последних. Тест.
3. **Образ backend** (`backend/Dockerfile`):
   - multi-stage, non-root (uid 10001, владелец `/data`);
   - `HEALTHCHECK` на `/api/health` через python `urllib`;
   - build args `APP_VERSION` и `GIT_SHA` → env, в лог при старте и в `/api/health` как `version`;
   - `backend/.dockerignore`.
4. **Образ web** (`web/Dockerfile`):
   - `FROM caddy:2-alpine`, внутрь `frontend/` и новый Caddyfile, `caddy validate` при сборке;
   - Caddy слушает **443** (основной адрес — `https://finance.fomin-vladimir.ru`) и 80 (ACME, редирект);
   - **`:8443` отвечает постоянным редиректом** на тот же путь без порта, чтобы старые закладки работали.
5. Оба образа публикуются в GHCR: `ghcr.io/z1ec/finance-tracker-backend` и `ghcr.io/z1ec/finance-tracker-web`, **один тег на оба** (`sha-<7>` + `latest`). Платформа — по `uname -m` дубайского сервера, в переменную репозитория `DEPLOY_PLATFORM`.
6. **Внутренняя сеть для бота** (понадобится на этапе 2):
   - в `deploy/docker-compose.prod.yml` backend подключён ещё и к внешней Docker-сети `vav-internal` с алиасом `finance-backend`;
   - сеть создаётся один раз на сервере (`docker network create vav-internal`, если её нет);
   - `deploy.sh` создаёт её при отсутствии;
   - наружу backend по-прежнему не публикуется.

## 5. CI — `.github/workflows/ci.yml`
- **На каждый push и PR**:
  - ruff, ruff format, mypy, pytest с покрытием;
  - сборка обоих образов без публикации.
- **Smoke-тест**:
  - backend-контейнер стартует с тестовым `.env` (длинные случайные секреты, `DOMAIN=localhost`) → `/api/health` 200;
  - для web — `caddy validate`.
- **Качество конфигов**: `actionlint`, `shellcheck deploy/*.sh`, dry-run тесты `deploy.sh` по образцу бота.
- `permissions: contents: read`, `concurrency` как у бота.

## 6. CD — `.github/workflows/deploy.yml` + `deploy/`
- **Схема как у бота**:
  - push в `main` → CI → build & push обоих образов → deploy по SSH;
  - деплой — только если `vars.DEPLOY_ENABLED == 'true'`;
  - `workflow_dispatch` с `image_tag` — для отката.
- **Окружение и доступ**: Environment `production`, `concurrency: deploy-production`, `StrictHostKeyChecking=yes` с `DEPLOY_KNOWN_HOSTS`, токен GHCR через stdin, логаут в конце.
- **`deploy/docker-compose.prod.yml`**:
  - `backend` и `web` из GHCR по `${IMAGE_TAG}`;
  - `env_file: .env`, `./data:/data`, `restart: unless-stopped`, ротация логов;
  - у web порты `80`, `443`, `8443`;
  - тома Caddy — свои, compose-проект `finance-tracker`;
  - backend в сетях `default` и `vav-internal`.
- **`deploy/deploy.sh`** по образцу бота, для двух сервисов:
  - `pull` → `up -d`;
  - ожидание: `healthy` у backend, а у web — ответ `https://finance.fomin-vladimir.ru/api/health` с сервера (`--resolve` на 127.0.0.1);
  - при неудаче — откат обоих на прошлый тег;
  - хранить текущий и предыдущий тег образов.
- **Сервер `dubai`**:
  - каталог `/opt/finance-tracker`, пользователь **`deploy`** (уже есть, в группе docker);
  - сгенерируй **на рабочей машине во временном каталоге** отдельную пару ed25519 для этого репозитория и добавь публичную часть в `authorized_keys` пользователя `deploy` (ключ бота не трогай);
  - секреты через `gh secret set --env production`: `DEPLOY_HOST=81.85.92.119`, `DEPLOY_USER=deploy`, `DEPLOY_PORT=22`, `DEPLOY_SSH_KEY` (через stdin), `DEPLOY_KNOWN_HOSTS` (`ssh-keyscan`, сверенный с отпечатком из `~/.ssh/known_hosts`);
  - удали локальную копию приватного ключа;
  - права: `/opt/finance-tracker` принадлежит `deploy`, а `data/` — uid 10001.

## 7. Переключение с временного стека на CD
Простой ~1–2 минуты:
1. `sqlite3 .backup` + `integrity_check` + число строк;
2. `docker compose down` временного стека **без `-v`**;
3. переменная `DEPLOY_ENABLED=true`;
4. merge PR → `gh run watch` для Deploy;
5. **проверка**:
   - `https://finance.fomin-vladimir.ru/api/health` → `ok` с новой версией;
   - `:8443` редиректит;
   - страница входа отдаёт 200;
   - число строк совпадает;
   - бот `healthy`;
   - чужой сайт на `ru-tracker` отвечает тем же кодом, что в базовой линии;
6. **при неудаче**: `docker compose up -d` временного стека, разбор, повтор;
7. после успеха удали `/opt/finance-tracker/bootstrap-src` и override. Бэкапы оставь.

## 8. Документация (английский, кроме `*.ru.md`)
- **`CLAUDE.md`** в стиле бота:
  - команды, карта модулей, конвенции, чеклист в конце задачи;
  - подводные камни: сервер общий с ботом; сеть `vav-internal`; редирект с `:8443`; время в UTC и Europe/Budapest; SQLite с одним писателем; на `ru-tracker` трекер остановлен, а чужой сайт трогать нельзя.
- **`docs/DEVLOG.md`**: «Assumptions», краткая история по `git log`, подробная запись этапа: переезд, время простоя, все действия на серверах.
- **`docs/PLAN.md`**: чеклист этапа 1, место для этапов 2a и 3b.
- **`docs/ARCHITECTURE.md`**: компоненты, сеть с ботом (Mermaid), модель данных (ER), курсы, бэкапы.
- **`docs/DEPLOYMENT.md`**: пайплайн (Mermaid), секреты, откат через `workflow_dispatch`, ручной деплой без CI, восстановление из бэкапа, перенос на другой сервер (по опыту этого переезда).
- **`docs/adr/`**: `0001` — стек (задним числом), `0002` — CI/CD (два образа, GHCR, SSH, health check, откат), `0003` — переезд в Дубай и общий сервер с ботом (сеть `vav-internal`, 443 + редирект с 8443).
- **`CHANGELOG.md`**: `1.0.0` — исходное состояние, `1.1.0` — этот этап.
- **`README.md`**: на английском, в стиле README бота (портфолио).
- **`docs/MANUAL_STEPS.ru.md`** — что осталось владельцу:
  - забрать новые пароли, если они создавались;
  - через 14 дней удалить старый стек и бэкапы на `ru-tracker` (точные команды);
  - как откатиться;
  - где лежат бэкапы.

## 9. Definition of Done
- [ ] Трекер работает в Дубае на `https://finance.fomin-vladimir.ru`, данные сверены; трекер на `ru-tracker` остановлен, бэкапы на месте.
- [ ] Локально `ruff check`, `ruff format --check`, `mypy`, `pytest`, `actionlint`, `shellcheck`, dry-run `deploy.sh` — зелёные.
- [ ] PR смержен, Deploy `success`; проверки раздела 7 пройдены, их вывод есть в сессии.
- [ ] Документация раздела 8 в `main`.
- [ ] Отчёт владельцу по-русски: время простоя, что изменилось на серверах, где бэкапы, как откатиться, что сделать руками.

---

## Команда для запуска (режим Auto)
```
/goal Выполнено задание из STAGE1 (после переноса — docs/briefs/2026-11-12-stage1-migration-cicd.md) в репозитории трекера. Подтверждается выводом команд в этой сессии: (1) данные трекера перенесены с ru-tracker на dubai: sha256 и PRAGMA integrity_check совпали, число строк в users, categories, expenses, exchange_rates, budgets и settings в Дубае равно числу на ru-tracker на момент остановки; трекер на ru-tracker остановлен, но не удалён, бэкап на месте; (2) в backend ruff check, ruff format --check, mypy и pytest завершились с кодом 0; actionlint, shellcheck deploy/*.sh и dry-run тесты deploy.sh зелёные; (3) PR из feat/stage1-cicd в main смержен, gh pr checks показал все проверки успешными; (4) gh run view показал success для workflow Deploy на коммите из main; (5) curl https://finance.fomin-vladimir.ru/api/health вернул status ok и db ok с новой версией, адрес с :8443 отвечает редиректом, страница входа отдаёт 200, после переключения на CD число строк не изменилось; (6) контейнер бота на dubai healthy, а чужой сайт на ru-tracker отвечает тем же HTTP-кодом, что и до начала работ; (7) CLAUDE.md, docs/DEVLOG.md с временем простоя, docs/PLAN.md, docs/ARCHITECTURE.md, docs/DEPLOYMENT.md, ADR 0001-0003, CHANGELOG.md, README.md и docs/MANUAL_STEPS.ru.md закоммичены в main; (8) владельцу выведен отчёт на русском. Запрещено: на ru-tracker менять firewall, sshd, чужой сайт и чужие контейнеры, удалять что-либо; на dubai трогать стек и .env бота, удалять правила ufw; удалять данные, тома и бэкапы; выводить или коммитить секреты. Если пункт невозможен только из-за действия, которое может сделать лишь владелец (DNS, доступ, оплата, решение), опиши его в docs/MANUAL_STEPS.ru.md и отчёте — такой пункт считается закрытым, всё остальное должно быть выполнено.
```
