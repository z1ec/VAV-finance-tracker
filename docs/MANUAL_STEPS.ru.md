# Ручные шаги владельца

## Этап 2a — API для бота и подписки (2026-11-12)

**Обязательных действий нет.** Трекер версии 1.2.0 умеет принимать операции от бота Momentum,
считает дни и месяцы по времени Будапешта и ведёт подписки.

### Токен бота — как устроено
- Токен сгенерирован **на сервере** скриптом `/opt/finance-tracker/bot-token.sh`. Его значение
  не выводилось и нигде не сохранялось, кроме `/opt/momentum/.env` (`FINANCE_API_TOKEN`, рядом
  `FINANCE_API_URL=http://finance-backend:8000`).
- В `/opt/finance-tracker/.env` лежит только SHA-256 токена (`BOT_API_TOKEN_SHA256`),
  пользователь, от имени которого бот пишет операции (`BOT_API_USERNAME` — ваш admin), и подсеть
  сети `vav-internal` (`BOT_API_ALLOWED_NETWORKS=172.20.0.0/16`).
- Токен работает **только изнутри сервера** (контейнер в сети `vav-internal`). Через
  `https://finance.fomin-vladimir.ru` любой запрос с заголовком `Authorization` получает 403.
- Сейчас бот эти переменные ещё не читает — его подключение к трекеру будет на этапе 2b. Стек
  бота не перезапускался.

### Как перевыпустить токен (если он мог утечь)
Одна команда; новый токен сразу заменит старый в обоих `.env`, старый перестанет работать после
перезапуска трекера:
```bash
ssh dubai '/opt/finance-tracker/bot-token.sh && cd /opt/finance-tracker && IMAGE_TAG=$(cat .last_successful_tag) docker compose -p finance-tracker -f docker-compose.prod.yml up -d --force-recreate backend'
```
После этого перезапустите бота так, как это принято в его проекте, чтобы он прочитал новый
`FINANCE_API_TOKEN` (до этапа 2b не нужно). Значение токена никуда копировать не надо.

Проверить, что токен работает (значение не печатается, выводятся только коды ответа):
```bash
ssh dubai 'docker run --rm --network vav-internal --env-file <(grep "^FINANCE_API_TOKEN=" /opt/momentum/.env) curlimages/curl:8.11.1 sh -c "curl -s -o /dev/null -w \"%{http_code}\n\" -H \"Authorization: Bearer \$FINANCE_API_TOKEN\" http://finance-backend:8000/api/categories"'
```
Ожидается `200`.

**Отключить токен совсем:** в `/opt/finance-tracker/.env` оставить `BOT_API_TOKEN_SHA256=` пустым
и перезапустить backend той же командой `docker compose … up -d --force-recreate backend`.

**Если бот получает 429:** было 5 неверных токенов за 15 минут с его адреса, и адрес временно
заблокирован даже для правильного токена. Подождите 15 минут или перезапустите backend.

### Подписки — как пользоваться
- Вкладка «Подписки» на сайте: добавление, редактирование, отключение; блок «Ждут суммы» для
  плавающих платежей (кнопки «Внести сумму» и «Пропустить»); сумма «в месяц».
- Фиксированные подписки записываются в расходы сами — каждый день в 06:00 по Будапешту и при
  каждом перезапуске трекера, с пометкой 🔁 и комментарием «Подписка: …». Пропущенные даты
  догоняются, дубли не создаются.
- Если дата списания 31-го, в коротких месяцах списание будет в последний день месяца.
- Отключённая подписка не списывается. При повторном включении пропущенные за время отключения
  даты не списываются — следующая дата берётся начиная с сегодняшнего дня.
- На дашборде блок «Ближайшие списания (7 дней)».

### DNS: кэш ещё не везде обновился
На 2026-11-12 14:00 UTC резолвер Cloudflare (1.1.1.1) и провайдер рабочей машины всё ещё отдают
старый адрес `157.22.253.65`, поэтому у части клиентов сайт временно отвечает 502. Google DNS уже
отдаёт `81.85.92.119`. Пройдёт само в течение суток; ускорить можно сбросом кэша на
<https://one.one.one.one/purge-cache/> (подробности — раздел 3 этапа 1 ниже).

### Хвосты этапа 1 — закрыты
- `DEPLOY_USER` и `DEPLOY_PORT` перенесены из секретов в переменные окружения `production`,
  поэтому логи деплоя больше не прячут слова «deploy» и «22» под `***`.
- Тома `finance-bootstrap_caddy_data`, `finance-bootstrap_caddy_config` и образ
  `finance-bootstrap-backend` удалены после проверки, что их не использует ни один контейнер.

---

# Этап 1 — ручные шаги владельца

Состояние на 2026-11-12, 12:30 UTC: трекер работает в Дубае на CI/CD
(`https://finance.fomin-vladimir.ru`, версия 1.1.0, образы `sha-22e0d1c`). Данные сверены.
Простой: 50 секунд при переезде и 3 минуты 40 секунд при переключении на CD. Трекер на
`ru-tracker` остановлен, но не удалён.

## 1. CI/CD — сделано, для справки
- Ключ `finance-tracker-deploy` добавлен в `/home/deploy/.ssh/authorized_keys` на `dubai`
  (ключ бота `momentum-deploy` не тронут). Локальная копия приватного ключа удалена, он хранится
  только в секрете `DEPLOY_SSH_KEY`.
- В окружении `production` заданы секреты `DEPLOY_HOST`, `DEPLOY_SSH_KEY`,
  `DEPLOY_KNOWN_HOSTS` и переменные `DEPLOY_USER=deploy`, `DEPLOY_PORT=22` (с этапа 2a — не
  секреты). Переменные репозитория: `DEPLOY_PLATFORM=linux/amd64`, `DEPLOY_ENABLED=true`.
- Каждый merge в `main` теперь автоматически выкатывается на сервер. Временно отключить
  автодеплой: `gh variable set DEPLOY_ENABLED -R z1ec/VAV-finance-tracker --body false`.
- Отозвать доступ GitHub Actions: удалить строку `finance-tracker-deploy` из
  `/home/deploy/.ssh/authorized_keys`.

## 2. Новые пароли пользователей — заберите их
Пароли `ADMIN` (10 символов) и `USER1` (11 символов) были короче 12, а новая версия на реальном
домене с такими не стартует. На сервере сгенерированы новые пароли по 24 символа и записаны
в `/opt/finance-tracker/.env`. В базе они **уже действуют**: применены командой
`python -m app.manage set-password --from-env ADMIN USER1`, вход проверен. Старые пароли больше
не работают. Пароль `USER2` и `SECRET_KEY` не менялись, сессии остались действительными.
Копия новых паролей лежит в `/root/finance-tracker-new-passwords.txt` (права 600, только root).

**Как меняются пароли.** Пароли из `.env` используются только при **первом** создании
пользователя (seed). Если поменять их в `.env`, у существующих пользователей ничего не
изменится. Для смены служит управляющая команда трекера (сначала сделайте бэкап базы):
```bash
# пароли ADMIN и USER1 из текущего .env
docker exec finance-tracker-backend-1 python -m app.manage set-password --from-env ADMIN USER1
# любой пользователь: пароль идёт через stdin (не виден в ps); read -rs, чтобы не попал в историю
read -rs NEWPW
printf "%s" "$NEWPW" | docker exec -i finance-tracker-backend-1 \
  python -m app.manage set-password --username <логин>
```
Команда отклоняет пароли короче 12 символов и стандартные значения. Она меняет всё или ничего
и не печатает пароли.

**Что сделать:** заберите пароли (`ssh dubai cat /root/finance-tracker-new-passwords.txt`), сохраните в
менеджер паролей, передайте пользователю `USER1` и **удалите файл**:
`ssh dubai shred -u /root/finance-tracker-new-passwords.txt`.
Бэкапы до смены паролей: `/opt/finance-tracker/backups/env-before-rotation-20261112T113256Z`
(старый `.env`, 600) и `pre-set-password-20261112.db`.

## 3. DNS: устаревший кэш
Авторитетные NS reg.ru и публичные резолверы уже отдают `81.85.92.119`, AAAA-записи нет. Но
у старой записи TTL был 86400 (сутки). Клиенты с закэшированным старым IP до суток попадают
на остановленный старый сервер и видят 502. Можно ускорить: сбросить кэш на
<https://one.one.one.one/purge-cache/> и <https://dns.google/cache> для
`finance.fomin-vladimir.ru`, а на своих устройствах — перезапустить сеть или роутер. На будущее
перед переездом снижайте TTL до 300 за сутки.

## 4. Через 14 дней (не раньше 2026-11-26): удалить старый стек на `ru-tracker`
Сначала убедитесь, что в Дубае всё в порядке и свежие бэкапы есть
(`ssh dubai ls -la /opt/finance-tracker/data/backups`). Затем:
```bash
ssh ru-tracker
cd /usr/local/etc/finance/VAV-finance-tracker
docker-compose -f docker-compose.behind-proxy.yml down -v      # контейнеры, сеть и тома Caddy трекера
docker image rm vav-finance-tracker_backend
cd / && rm -rf /usr/local/etc/finance/VAV-finance-tracker      # код, data/, backups/ (включая pre-migration-2026-11-12.db)
```
Необязательно, на ваше усмотрение: nginx на этом сервере общий с сайтом `fomin-vladimir.ru`.
Можно убрать vhost трекера и его сертификат, а после этого обязательно проверить, что сайт
отвечает 200:
```bash
rm /etc/nginx/sites-enabled/finance.fomin-vladimir.ru && nginx -t && systemctl reload nginx
certbot delete --cert-name finance.fomin-vladimir.ru
curl -sI https://fomin-vladimir.ru | head -1
```

## 5. Как откатиться
- **Релиз:** GitHub → Actions → Deploy → Run workflow → `image_tag` =
  предыдущий тег (`ssh dubai cat /opt/finance-tracker/.previous_successful_tag`; файл появится
  после второго деплоя). Неудачный деплой `deploy.sh` откатывает сам.
- **Обратно на `ru-tracker` (крайний случай):** остановить трекер в Дубае, снять бэкап
  `expenses.db`, перенести его потоком через SSH в
  `/usr/local/etc/finance/VAV-finance-tracker/data/expenses.db` (старые `expenses.db-wal`/`-shm`
  предварительно убрать в сторону), затем
  `docker-compose -f docker-compose.behind-proxy.yml start` и вернуть A-запись в reg.ru на
  `157.22.253.65`. Без переноса базы потеряются операции, внесённые после 2026-11-12 11:22 UTC.

## 6. Где лежат бэкапы
| Где | Что |
|---|---|
| `dubai:/opt/finance-tracker/backups/pre-migration-2026-11-12.db` | снимок на момент переезда (sha256 `b5a84398…18e0d`) |
| `dubai:/opt/finance-tracker/backups/pre-password-rotation-20261112T113256Z.db`, `env-before-rotation-…` | до ротации паролей в `.env` |
| `dubai:/opt/finance-tracker/backups/pre-cd-switch-20261112.db` | перед переключением на CD (sha256 `9bd47d7f…beb6e0`) |
| `dubai:/opt/finance-tracker/backups/pre-set-password-20261112.db` | перед сменой паролей в базе |
| `dubai:/opt/finance-tracker/data/backups/expenses-YYYY-MM-DD.db` | ежедневно в 03:00 UTC, хранятся 14 (каталог появится после первого ночного бэкапа) |
| `dubai:/opt/finance-tracker/data/backups/pre-migrate-<UTC>.db` | перед миграциями БД (с версии 1.1.0), хранятся 10 |
| `ru-tracker:/usr/local/etc/finance/VAV-finance-tracker/backups/pre-migration-2026-11-12.db` | исходный снимок на старом сервере |
| `ru-tracker:/usr/local/etc/finance/VAV-finance-tracker/data/backups/` | старые ежедневные бэкапы |

## 7. Остатки временного стека — удалены
Тома `finance-bootstrap_caddy_data`, `finance-bootstrap_caddy_config` и образ
`finance-bootstrap-backend` удалены 2026-11-12 на этапе 2a, после проверки, что их не использует
ни один контейнер.
