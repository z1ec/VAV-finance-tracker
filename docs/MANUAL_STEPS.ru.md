# Ручные шаги владельца — этап 1

Состояние на 2026-11-12, 12:30 UTC: трекер работает в Дубае на CI/CD
(`https://finance.fomin-vladimir.ru`, версия 1.1.0, образы `sha-22e0d1c`). Данные сверены.
Простой: 50 секунд при переезде и 3 минуты 40 секунд при переключении на CD. Трекер на
`ru-tracker` остановлен, но не удалён.

## 1. CI/CD — сделано, для справки
- Ключ `finance-tracker-deploy` добавлен в `/home/deploy/.ssh/authorized_keys` на `dubai`
  (ключ бота `momentum-deploy` не тронут). Локальная копия приватного ключа удалена, он хранится
  только в секрете `DEPLOY_SSH_KEY`.
- В окружении `production` заданы секреты `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_PORT`,
  `DEPLOY_SSH_KEY`, `DEPLOY_KNOWN_HOSTS`. Переменные: `DEPLOY_PLATFORM=linux/amd64`,
  `DEPLOY_ENABLED=true`.
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

## 7. Можно удалить позже (необязательно)
Тома и образ временного стека больше не используются: `finance-bootstrap_caddy_data`,
`finance-bootstrap_caddy_config` и образ `finance-bootstrap-backend`. Агент их не удалял, потому
что удалять тома ему запрещено. Удалить, если хотите:
`ssh dubai 'docker volume rm finance-bootstrap_caddy_data finance-bootstrap_caddy_config; docker image rm finance-bootstrap-backend'`.
