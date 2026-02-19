# 🚀 CI/CD Setup Guide

Автоматический деплой бота на сервер при каждом `git push`.

## 📋 Что делает CI/CD

1. Ты делаешь `git push` в ветку `main`
2. GitHub автоматически подключается к серверу по SSH
3. Обновляет код (`git pull`)
4. Перезапускает бота (`systemctl restart sabot`)

---

## ⚙️ Настройка (3 шага)

### Шаг 1: Настройка сервера (выполнить на сервере)

Подключись к серверу по SSH:
```bash
ssh root@ТВОЙ_PUBLIC_IP
```

Выполни команды:

```bash
# 1. Обновление системы
apt update && apt upgrade -y

# 2. Установка необходимых пакетов
apt install -y python3 python3-pip python3-venv git

# 3. Создание директории для бота
mkdir -p /root/SABot
cd /root/SABot

# 4. Клонирование репозитория
git clone https://github.com/medvedka347/SAbot.git .

# 5. Создание виртуального окружения
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 6. Создание .env файла
nano .env
```

Вставь в .env:
```env
BOT_TOKEN=your_bot_token_here
DB_NAME=user_roles.db
INITIAL_ADMIN_ID=your_telegram_id_here
```

Сохрани: `Ctrl+O`, `Enter`, `Ctrl+X`

```bash
# 7. Копирование systemd сервиса
cp deploy/sabot.service /etc/systemd/system/

# 8. Запуск и включение автозапуска
systemctl daemon-reload
systemctl enable sabot
systemctl start sabot

# 9. Проверка статуса
systemctl status sabot
```

---

### Шаг 2: Настройка SSH ключа для GitHub

На сервере сгенерируй ключ:

```bash
ssh-keygen -t ed25519 -C "github-actions" -f /root/.ssh/github_actions
```

(нажми Enter 3 раза, без пароля)

Покажи приватный ключ:
```bash
cat /root/.ssh/github_actions
```

**Скопируй вывод целиком** (начинается с `-----BEGIN OPENSSH PRIVATE KEY-----`)

---

### Шаг 3: Добавление секретов в GitHub

1. Открой https://github.com/medvedka347/SAbot
2. Перейди в **Settings** → **Secrets and variables** → **Actions**
3. Нажми **New repository secret**
4. Добавь 3 секрета:

| Name | Value |
|------|-------|
| `SSH_PRIVATE_KEY` | Приватный ключ (скопированный на шаге 2) |
| `SSH_HOST` | Твой Public IP адрес |
| `SSH_USER` | `root` |

---

## 🎉 Готово!

Теперь при каждом `git push` в ветку `main`:

```bash
git add .
git commit -m "Обновление"
git push origin main
```

GitHub автоматически обновит код на сервере и перезапустит бота!

Проверить можно тут: https://github.com/medvedka347/SAbot/actions

---

## 🐛 Полезные команды на сервере

```bash
# Просмотр логов бота
journalctl -u sabot -f

# Перезапуск бота вручную
systemctl restart sabot

# Остановка бота
systemctl stop sabot

# Проверка статуса
systemctl status sabot
```

---

## ⚠️ Важно

1. **Никогда не комить `.env` файл** — он в `.gitignore` и должен оставаться только на сервере
2. **База данных** (`user_roles.db`) тоже не в git — она создаётся на сервере автоматически
3. Если CI/CD упал — проверь логи: https://github.com/medvedka347/SAbot/actions
