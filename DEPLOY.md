# 🚀 Полный процесс деплоя SABot на удалённый сервер

## Содержание

1. [Требования](#требования)
2. [Подготовка сервера](#1-подготовка-сервера)
3. [Установка бота](#2-установка-бота)
4. [Настройка окружения](#3-настройка-окружения)
5. [Запуск как systemd-сервис](#4-запуск-как-systemd-сервис)
6. [Настройка CI/CD](#5-настройка-cicd-автоматический-деплой)
7. [Обновление бота](#6-обновление-бота)
8. [Мониторинг и логи](#7-мониторинг-и-логи)
9. [Устранение неполадок](#8-устранение-неполадок)

---

## Требования

- **Сервер**: Ubuntu 20.04+ (или Debian 11+) с SSH-доступом
- **Python**: 3.10+
- **Telegram Bot Token**: получить у [@BotFather](https://t.me/BotFather)
- **Telegram ID администратора**: получить у [@userinfobot](https://t.me/userinfobot)

---

## 1. Подготовка сервера

Подключитесь к серверу по SSH:

```bash
ssh root@YOUR_SERVER_IP
```

Обновите систему и установите зависимости:

```bash
apt update && apt upgrade -y
apt install -y python3 python3-pip python3-venv git
```

---

## 2. Установка бота

### Вариант A: Автоматическая установка (рекомендуется)

```bash
# Скачайте и запустите скрипт установки
git clone https://github.com/medvedka347/SAbot.git /root/SABot
cd /root/SABot
bash deploy/setup-server.sh
```

Скрипт автоматически:
- Установит системные зависимости
- Создаст виртуальное окружение
- Установит Python-зависимости
- Скопирует systemd-сервис
- Создаст шаблон `.env` файла

### Вариант B: Ручная установка

```bash
# 1. Клонирование репозитория
mkdir -p /root/SABot
cd /root/SABot
git clone https://github.com/medvedka347/SAbot.git .

# 2. Создание виртуального окружения
python3 -m venv .venv
source .venv/bin/activate

# 3. Установка зависимостей
pip install --upgrade pip
pip install -r requirements.txt
```

---

## 3. Настройка окружения

Создайте файл `.env` из шаблона:

```bash
cp .env.example .env
nano .env
```

Заполните обязательные переменные:

```env
# Токен бота (получить у @BotFather в Telegram)
BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz

# Имя файла базы данных
DB_NAME=user_roles.db

# Telegram ID первого администратора (получить у @userinfobot)
INITIAL_ADMIN_ID=123456789
```

Сохраните файл: `Ctrl+O`, `Enter`, `Ctrl+X`.

### Проверка перед запуском

Убедитесь, что бот запускается вручную:

```bash
cd /root/SABot
source .venv/bin/activate
python main.py
```

Если в консоли появилось `Бот запущен` — всё настроено правильно. Остановите бот (`Ctrl+C`) и переходите к настройке сервиса.

---

## 4. Запуск как systemd-сервис

Systemd обеспечивает автозапуск бота при перезагрузке сервера и автоматический перезапуск при сбоях.

```bash
# 1. Копирование файла сервиса
cp /root/SABot/deploy/sabot.service /etc/systemd/system/

# 2. Перезагрузка конфигурации systemd
systemctl daemon-reload

# 3. Включение автозапуска при загрузке системы
systemctl enable sabot

# 4. Запуск бота
systemctl start sabot

# 5. Проверка статуса
systemctl status sabot
```

Если статус `active (running)` — бот работает.

---

## 5. Настройка CI/CD (автоматический деплой)

CI/CD позволяет автоматически обновлять бота при каждом `git push` в ветку `main`.

### 5.1 Генерация SSH-ключа на сервере

```bash
ssh-keygen -t ed25519 -C "github-actions" -f /root/.ssh/github_actions -N ""
```

Добавьте публичный ключ в `authorized_keys`:

```bash
cat /root/.ssh/github_actions.pub >> /root/.ssh/authorized_keys
```

Скопируйте приватный ключ (понадобится для GitHub):

```bash
cat /root/.ssh/github_actions
```

### 5.2 Добавление секретов в GitHub

Перейдите в репозиторий → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**:

| Имя секрета | Значение |
|---|---|
| `SSH_PRIVATE_KEY` | Содержимое приватного ключа (весь вывод `cat /root/.ssh/github_actions`) |
| `SSH_HOST` | IP-адрес вашего сервера |
| `SSH_USER` | `root` |

### 5.3 Готово

Теперь при каждом `git push` в ветку `main` GitHub Actions автоматически:
1. Подключится к серверу по SSH
2. Выполнит `git pull`
3. Перезапустит сервис бота

Проверить статус деплоя: https://github.com/medvedka347/SAbot/actions

---

## 6. Обновление бота

### При настроенном CI/CD

```bash
# На локальной машине
git add .
git commit -m "Описание изменений"
git push origin main
# Деплой произойдёт автоматически
```

### Ручное обновление на сервере

```bash
cd /root/SABot
git pull origin main
source .venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart sabot
```

---

## 7. Мониторинг и логи

```bash
# Просмотр логов в реальном времени
journalctl -u sabot -f

# Последние 100 строк логов
journalctl -u sabot -n 100

# Логи за сегодня
journalctl -u sabot --since today

# Статус сервиса
systemctl status sabot

# Перезапуск бота
systemctl restart sabot

# Остановка бота
systemctl stop sabot
```

---

## 8. Устранение неполадок

### Бот не запускается

```bash
# Проверьте логи
journalctl -u sabot -n 50 --no-pager

# Попробуйте запустить вручную
cd /root/SABot
source .venv/bin/activate
python main.py
```

### `BOT_TOKEN не найден`

Файл `.env` отсутствует или не содержит `BOT_TOKEN`:

```bash
cat /root/SABot/.env
# Если файла нет:
cp /root/SABot/.env.example /root/SABot/.env
nano /root/SABot/.env
```

### `ModuleNotFoundError`

Зависимости не установлены:

```bash
cd /root/SABot
source .venv/bin/activate
pip install -r requirements.txt
```

### Бот не отвечает на `/start`

Проверьте, что `INITIAL_ADMIN_ID` в `.env` содержит ваш Telegram ID. Если база уже создана с неверным ID, удалите её и перезапустите:

```bash
rm /root/SABot/user_roles.db
systemctl restart sabot
```

### CI/CD не работает

1. Проверьте секреты в GitHub Settings → Secrets
2. Проверьте логи на вкладке Actions в репозитории
3. Убедитесь, что SSH-ключ добавлен в `authorized_keys` на сервере

---

## Примечание по безопасности

По умолчанию бот запускается от имени `root`. Для повышения безопасности рекомендуется создать выделенного пользователя:

```bash
useradd -r -s /bin/false sabot
mkdir -p /opt/SABot
chown -R sabot:sabot /opt/SABot
```

При этом потребуется обновить пути в `sabot.service` и `setup-server.sh`.
