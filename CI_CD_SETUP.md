# 🚀 CI/CD Setup Guide

Автоматический деплой бота на сервер при каждом `git push`.

## 📋 Что делает CI/CD

1. Ты делаешь `git push` в ветку `main`
2. GitHub автоматически подключается к серверу по SSH
3. Обновляет код (`git pull`)
4. Устанавливает новые зависимости (если requirements.txt изменился)
5. Перезапускает бота (`systemctl restart sabot`)
6. Проверяет статус сервиса

---

## ⚙️ Быстрая настройка (автоматический скрипт)

```bash
# На сервере выполни:
curl -sSL https://raw.githubusercontent.com/medvedka347/SAbot/main/deploy/setup-server.sh | bash
```

Или вручную по шагам ниже ↓

---

## ⚙️ Ручная настройка

### Шаг 1: Настройка сервера

Подключись к серверу:
```bash
ssh root@ТВОЙ_PUBLIC_IP
```

Выполни:
```bash
# Обновление системы
apt update && apt upgrade -y

# Установка пакетов
apt install -y python3 python3-pip python3-venv git

# Создание директории
mkdir -p /root/SABot && cd /root/SABot

# Клонирование
git clone https://github.com/medvedka347/SAbot.git .

# Создание venv
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Создание .env
nano .env
```

**Содержимое .env:**
```env
BOT_TOKEN=your_bot_token_here
DB_NAME=user_roles.db
INITIAL_ADMIN_ID=your_telegram_id_here
```

Сохрани: `Ctrl+O`, `Enter`, `Ctrl+X`

```bash
# Установка systemd сервиса
cp deploy/sabot.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable sabot
systemctl start sabot
systemctl status sabot
```

---

### Шаг 2: Firewall (если включён)

```bash
# Разрешить SSH для GitHub Actions
ufw allow 22/tcp
# или
iptables -A INPUT -p tcp --dport 22 -j ACCEPT
```

---

### Шаг 3: SSH ключ для GitHub

На сервере:
```bash
# Создать ключ
ssh-keygen -t ed25519 -C "github-actions" -f /root/.ssh/github_actions -N ""

# Добавить в authorized_keys
cat /root/.ssh/github_actions.pub >> /root/.ssh/authorized_keys

# Показать приватный ключ (скопируй ВЕСЬ вывод)
cat /root/.ssh/github_actions
```

---

### Шаг 4: GitHub Secrets

Открой: `https://github.com/medvedka347/SAbot/settings/secrets/actions`

Добавь 3 секрета:

| Name | Value |
|------|-------|
| `SSH_PRIVATE_KEY` | Приватный ключ (весь текст из `cat /root/.ssh/github_actions`) |
| `SSH_HOST` | Твой Public IP |
| `SSH_USER` | `root` |

---

## 🎉 Тестирование

```bash
# Локально сделай изменение и push
git add .
git commit -m "Test deploy"
git push origin main
```

Смотри статус деплоя: https://github.com/medvedka347/SAbot/actions

---

## 🐛 Troubleshooting

### Проблема: "Permission denied (publickey)"

**Решение:**
```bash
# На сервере проверь права
chmod 700 /root/.ssh
chmod 600 /root/.ssh/authorized_keys
chmod 600 /root/.ssh/github_actions

# Проверь что ключ добавлен
grep "github-actions" /root/.ssh/authorized_keys
```

### Проблема: "Failed to restart sabot.service"

**Решение:**
```bash
# Проверь статус
systemctl status sabot

# Проверь логи
journalctl -u sabot -n 50

# Проверь .env файл
ls -la /root/SABot/.env
```

### Проблема: Бот не запускается после деплоя

**Решение:**
```bash
# Проверь зависимости
source /root/SABot/.venv/bin/activate
pip install -r /root/SABot/requirements.txt

# Перезапусти вручную
systemctl restart sabot
journalctl -u sabot -f
```

### Проблема: "Could not resolve host github.com"

**Решение:**
```bash
# Проверь DNS
echo "nameserver 8.8.8.8" >> /etc/resolv.conf

# Или проверь интернет
ping -c 3 github.com
```

---

## 💾 Backup базы данных

Перед важными изменениями:

```bash
# Ручной backup
cp /root/SABot/user_roles.db /root/SABot/user_roles.db.backup.$(date +%Y%m%d)

# Автоматический backup (добавь в crontab)
0 3 * * * cp /root/SABot/user_roles.db /root/backups/sabot_$(date +\%Y\%m\%d).db
```

---

## 📊 Полезные команды

```bash
# Логи в реальном времени
journalctl -u sabot -f

# Последние 100 строк логов
journalctl -u sabot -n 100 --no-pager

# Логи за последний час
journalctl -u sabot --since "1 hour ago"

# Перезапуск
systemctl restart sabot

# Полная остановка
systemctl stop sabot

# Проверка что бот работает
systemctl is-active sabot
ps aux | grep SABot
```

---

## 🔒 Безопасность

1. **Никогда не комить `.env`** — он в `.gitignore`
2. **Никогда не комить `user_roles.db`** — база данных на сервере
3. **Ограничь SSH доступ** по IP если возможно
4. **Регулярно обновляй систему:** `apt update && apt upgrade`

---

## 🔄 Обновление вручную (если CI/CD не работает)

```bash
ssh root@ТВОЙ_IP
cd /root/SABot
git pull origin main
source .venv/bin/activate
pip install -r requirements.txt
systemctl restart sabot
```
