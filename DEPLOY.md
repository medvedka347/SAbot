# 🚀 Деплой SABot

## Быстрый старт (автоматический деплой)

```bash
# На сервере (Ubuntu/Debian) выполнить один раз:
curl -sSL https://raw.githubusercontent.com/medvedka347/SAbot/main/deploy/setup-server.sh | bash
```

Затем:
1. Отредактировать `/opt/sabot/.env` (добавить BOT_TOKEN и INITIAL_ADMIN_ID)
2. Запустить: `systemctl start sabot`

---

## Ручная настройка

### 1. Требования

- Ubuntu 20.04+ / Debian 11+
- Python 3.10+
- Git

### 2. Установка

```bash
# Подключиться к серверу
ssh root@YOUR_SERVER_IP

# Запустить скрипт установки
cd /root
curl -sSL https://raw.githubusercontent.com/medvedka347/SAbot/main/deploy/setup-server.sh -o setup-server.sh
bash setup-server.sh
```

### 3. Настройка окружения

```bash
# Отредактировать .env
nano /opt/sabot/.env
```

Заполнить:
```env
BOT_TOKEN=your_bot_token_here
INITIAL_ADMIN_ID=your_telegram_id
```

### 4. Запуск

```bash
systemctl start sabot
systemctl status sabot
```

---

## Настройка GitHub Actions (CI/CD)

### На сервере (один раз)

```bash
# Создать SSH ключ для GitHub Actions
ssh-keygen -t ed25519 -C "github-actions" -f /root/.ssh/github_actions -N ""
cat /root/.ssh/github_actions.pub >> /root/.ssh/authorized_keys

# Скопировать приватный ключ (для GitHub)
cat /root/.ssh/github_actions
```

### В GitHub (Settings → Secrets and variables → Actions)

| Name | Value |
|------|-------|
| `SSH_PRIVATE_KEY` | Приватный ключ из команды выше |
| `SSH_HOST` | IP сервера |
| `SSH_USER` | `root` |

Теперь при каждом `git push` в main бот автоматически обновится.

---

## Управление ботом

```bash
# Статус
systemctl status sabot

# Логи в реальном времени
journalctl -u sabot -f

# Последние 100 строк
journalctl -u sabot -n 100 --no-pager

# Перезапуск
systemctl restart sabot

# Остановка
systemctl stop sabot
```

---

## Автовосстановление

Бот автоматически перезапускается при:
- Краше приложения
- Перезагрузке сервера
- Обновлении через CI/CD

Если бот падает 3 раза за минуту — systemd делает паузу на 60 секунд.

---

## Backup базы данных

Автоматический backup создаётся при каждом деплое:
```
/opt/sabot/data/user_roles.db.backup.YYYYMMDD_HHMMSS
```

Ручной backup:
```bash
cp /opt/sabot/data/user_roles.db /opt/sabot/data/user_roles.db.manual.$(date +%Y%m%d)
```

---

## Решение проблем

### Бот не запускается

```bash
# Проверить логи
journalctl -u sabot -n 50

# Проверить .env
ls -la /opt/sabot/.env
cat /opt/sabot/.env

# Запустить вручную для диагностики
cd /opt/sabot
source .venv/bin/activate
python main.py
```

### CI/CD не работает

```bash
# Проверить SSH ключ
ls -la /root/.ssh/
grep "github-actions" /root/.ssh/authorized_keys

# Проверить права на .ssh
chmod 700 /root/.ssh
chmod 600 /root/.ssh/authorized_keys
```

---

## Локальная разработка (Docker)

Для локальной разработки можно использовать Docker:

```bash
# Скопировать .env
cp .env.example .env
# Отредактировать .env

# Запустить
docker compose up -d

# Логи
docker compose logs -f
```

**⚠️ Docker не используется для production деплоя — только systemd.**
