# Ручные шаги владельца — этап 1

Состояние на 2026-11-12: трекер **уже работает в Дубае** (`https://finance.fomin-vladimir.ru`)
на временном стеке `finance-bootstrap`, данные сверены, простой составил 50 секунд. Трекер на
`ru-tracker` остановлен, но не удалён. Переключение на CI/CD ждёт вашего решения (шаг 1).

## 1. Ключ деплоя для GitHub Actions — нужно ваше решение
Агенту запретили добавлять новый SSH-ключ в `/home/deploy/.ssh/authorized_keys` на `dubai`,
потому что это постоянный доступ к серверу. Без ключа CD не может зайти на сервер.

**Вариант А (рекомендую)** — разрешить агенту этот шаг (правило в настройках Claude Code или
ответ в сессии) и попросить продолжить: он сгенерирует отдельную пару ed25519 во временном
каталоге, добавит публичную часть пользователю `deploy`, запишет секреты окружения `production`,
удалит локальный приватный ключ и выполнит переключение (раздел 7 брифа).

**Вариант Б** — сделать вручную на рабочей машине:
```bash
d=$(mktemp -d) && ssh-keygen -q -t ed25519 -N "" -C finance-tracker-deploy -f "$d/key"
ssh dubai 'cat >> /home/deploy/.ssh/authorized_keys' < "$d/key.pub"
R=z1ec/VAV-finance-tracker
gh api -X PUT "repos/$R/environments/production" >/dev/null
gh secret set DEPLOY_HOST --env production -R $R --body 81.85.92.119
gh secret set DEPLOY_USER --env production -R $R --body deploy
gh secret set DEPLOY_PORT --env production -R $R --body 22
gh secret set DEPLOY_SSH_KEY --env production -R $R < "$d/key"
ssh-keyscan -t ed25519 81.85.92.119 2>/dev/null > "$d/kh"
ssh-keygen -lf "$d/kh"; ssh-keygen -lF 81.85.92.119      # отпечатки должны совпасть
gh secret set DEPLOY_KNOWN_HOSTS --env production -R $R < "$d/kh"
gh variable set DEPLOY_PLATFORM -R $R --body linux/amd64
rm -rf "$d"
```
Затем переключение (простой 1–2 минуты):
```bash
ssh dubai 'cd /opt/finance-tracker/bootstrap-src && docker compose -p finance-bootstrap --env-file /opt/finance-tracker/.env down'   # без -v
ssh dubai 'chown -R 10001:10001 /opt/finance-tracker/data'      # backend теперь работает как uid 10001
gh variable set DEPLOY_ENABLED -R z1ec/VAV-finance-tracker --body true
gh pr merge 1 -R z1ec/VAV-finance-tracker --merge   # PR №1 → Deploy
gh run watch -R z1ec/VAV-finance-tracker
curl -s https://finance.fomin-vladimir.ru/api/health            # "version":"1.1.0"
```
Перед `down` сделайте бэкап (`sqlite` backup API, как в `docs/DEPLOYMENT.md`). Если деплой не
прошёл: `docker compose -p finance-bootstrap … up -d` в `bootstrap-src` возвращает временный стек.
После успеха можно удалить `/opt/finance-tracker/bootstrap-src` (бэкапы не трогать).

## 2. Новые пароли пользователей
Пароли `ADMIN` (10 символов) и `USER1` (11 символов) короче 12, а новая версия на реальном домене
с такими не стартует. Агент сгенерировал на сервере новые пароли по 24 символа и записал их в
`/opt/finance-tracker/.env`. Копия лежит в `/root/finance-tracker-new-passwords.txt` (права 600,
только root). Пароль `USER2` и `SECRET_KEY` не менялись.

**Важно:** обновить хэши в базе агенту не разрешили. Поэтому сейчас **работают старые пароли**,
а новые ещё не действуют. Чтобы включить новые пароли (на `dubai` под root; контейнер —
`finance-bootstrap-backend-1` до переключения на CD или `finance-tracker-backend-1` после):
```bash
C=$(docker ps --format '{{.Names}}' | grep -E '^finance-(bootstrap|tracker)-backend-1$')
grep -v '^#' /root/finance-tracker-new-passwords.txt | docker exec -i "$C" python -c '
import sys
from sqlalchemy import select
from app.auth import hash_password
from app.db import SessionLocal
from app.models import User
db = SessionLocal(); n = 0
for line in sys.stdin:
    if not line.strip(): continue
    u, p = line.rstrip("\n").split("\t", 1)
    user = db.execute(select(User).where(User.username == u)).scalar_one_or_none()
    if user: user.password_hash = hash_password(p); n += 1
db.commit(); print("updated", n)'
```
Затем заберите пароли (`ssh dubai cat /root/finance-tracker-new-passwords.txt`), сохраните в
менеджер паролей, передайте пользователю `USER1` и **удалите файл**:
`ssh dubai shred -u /root/finance-tracker-new-passwords.txt`.
Бэкапы до ротации: `/opt/finance-tracker/backups/env-before-rotation-20261112T113256Z` (старый
`.env`, 600) и `pre-password-rotation-20261112T113256Z.db`.

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
- **Релиз (после включения CD):** GitHub → Actions → Deploy → Run workflow → `image_tag` =
  предыдущий тег (`ssh dubai cat /opt/finance-tracker/.previous_successful_tag`). Неудачный деплой
  `deploy.sh` откатывает сам.
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
| `dubai:/opt/finance-tracker/backups/pre-password-rotation-20261112T113256Z.db`, `env-before-rotation-…` | до ротации паролей |
| `dubai:/opt/finance-tracker/data/backups/expenses-YYYY-MM-DD.db` | ежедневно в 03:00 UTC, хранятся 14 |
| `dubai:/opt/finance-tracker/data/backups/pre-migrate-<UTC>.db` | перед миграциями БД (с версии 1.1.0), хранятся 10 |
| `ru-tracker:/usr/local/etc/finance/VAV-finance-tracker/backups/pre-migration-2026-11-12.db` | исходный снимок на старом сервере |
| `ru-tracker:/usr/local/etc/finance/VAV-finance-tracker/data/backups/` | старые ежедневные бэкапы |
