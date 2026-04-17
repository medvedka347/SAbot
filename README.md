# 🤖 SABot — Бот для комьюнити системного анализа

Telegram-бот для управления учебным комьюнити системного анализа. Production-ready версия на `python-telegram-bot` с capability-based ролями, аудитом и улучшенным UX.

---

## ✨ Возможности

### 👥 Ролевая модель (мультироли + capability-based)
- **User** — доступ к материалам, событиям, записи на мок
- **Mentor** — панель ментора + управление менти
- **Manager** — CRUD материалов и событий, аналитика, назначение бадди
- **Analyst** — просмотр статистики и аналитических отчётов
- **Admin** — полный контроль: роли, баны, все CRUD операции

**Bundle-роли:** `manager` в UI автоматически разворачивается в набор `{mentor, manager, analyst}`. В базе хранятся только гранулярные (атомарные) роли.

### 📚 Материалы по стадиям обучения
- 📖 **Фундаментальная теория** — базовые концепции
- 🔧 **Практическая теория** — применение знаний
- 📝 **Практические задания** — задачи для отработки
- 🗺️ **Прочие гайды** — дополнительные материалы

CRUD для Admin/Manager, публичный просмотр для всех.

### 📅 Управление событиями
- CRUD для вебинаров, митапов, квизов
- Гибкий ввод даты: `2024-12-31 18:00`, `31.12.2024 18:00`, `сегодня 18:00`
- Автоматическая публикация анонсов в группу
- Просмотр предстоящих событий

### 🤝 Buddy System (Наставничество)
- **Менторы** — управление менти, статусами, прогрессом
- **Менеджеры** — назначение менти менторам, отчёты по системе
- **Аналитики** — просмотр статистики по менторам
- **Пользователи** — просмотр контактов своего ментора
- Гибкий парсинг дат: `15.03.26`, `15,03,2026`, `сегодня`

### ⏱️ Запись на мок-интервью
- Календари менторов (интеграция с cal.com)
- Конфигурация через `config.py` (без изменения кода)
- Проверка доступности
- Контакты собеседующих

### 🔍 Поиск
- `/search <запрос>` — в личных сообщениях
- `/material <название>` — в группах
- Поиск по названию и описанию

### 🛡️ Система безопасности
- Бан после 3 неудачных попыток авторизации
- Прогрессивные наказания: 5 мин → 10 мин → 30 дней
- Автоматическая очистка истекших банов

---

## 🚀 Установка и запуск

### 1. Клонирование и зависимости

```bash
git clone https://github.com/medvedka347/SABot.git
cd SABot
pip install -r requirements.txt
```

### 2. Настройка окружения

```bash
cp .env.example .env
```

Отредактируйте `.env`:

```env
BOT_TOKEN=your_bot_token_from_botfather
DB_NAME=user_roles.db
INITIAL_ADMIN_ID=your_telegram_id
DEBUG_MODE=False  # True только для разработки

# Опционально: для публикации анонсов
ANNOUNCEMENT_GROUP_ID=-1001234567890
ANNOUNCEMENT_TOPIC_ID=123
```

**Получение Telegram ID:** напишите [@userinfobot](https://t.me/userinfobot)

### 3. Запуск

```bash
python main.py
```

При первом запуске бот автоматически создаст администратора с ID из `INITIAL_ADMIN_ID`.

---

## 📁 Структура проекта

```
SABot/
├── main.py                 # Точка входа, регистрация хендлеров, JobQueue
├── config.py               # Константы, capability-матрица, ROLE_BUNDLES
├── db_utils.py             # Database класс, CRUD, авторизация, миграции
├── utils.py                # Клавиатуры, rate limit, formatters
├── audit_logger.py         # Audit logging для безопасности
├── requirements.txt        # Зависимости
├── .env                    # Переменные окружения
├── .env.example            # Шаблон переменных
├── PROJECT_ANALYSIS.md     # 📄 Архитектурный анализ проекта
│
├── handlers/               # Модули обработчиков
│   ├── common.py           # /start, /help, навигация, fallback
│   ├── materials.py        # CRUD материалов + confirmations
│   ├── events.py           # CRUD событий + flexible dates
│   ├── roles.py            # Управление ролями + confirmations
│   ├── buddy.py            # Buddy system + analytics
│   ├── mocks.py            # Запись на мок (динамические менторы)
│   ├── bans.py             # Просмотр и снятие банов
│   ├── search.py           # /search + команды для групп
│   └── conversation_utils.py  # Легковесный FSM: _state + _history
│
└── deploy/                 # Docker + CI/CD конфигурация
```

---

## 🏗️ Архитектура

### Фреймворк

В проекте используется **python-telegram-bot (PTB) v20+** с `Application`, `JobQueue` и пользовательским фильтром `in_state` для маршрутизации диалогов. Отказ от `ConversationHandler` в пользу собственного легковесного FSM на базе `context.user_data["_state"]` и `context.user_data["_history"]`.

### Авторизация

Вместо middleware-«чёрного ящика» авторизация выполняется **явно** в начале защищённых хендлеров:

```python
from db_utils import require_any_role

async def admin_handler(update, context):
    user = await require_any_role(update, context, {"admin", "manager"})
    ...
```

### Capability-based доступ

Права описаны в `config.py` через множества ролей (`MODULE_ACCESS`).

```python
can_access("materials_crud", role_keys)   # admin / manager
can_access("buddy_analytics", role_keys)  # admin / analyst
```

`ROLE_BUNDLES` позволяют в UI назначать "менеджера", а в БД хранить только атомарные роли: `mentor`, `manager`, `analyst`.

### FSM (легковесный)

- `set_user_state(context, state)` — переключает состояние и пушит предыдущее в `_history`
- `back_handler(update, context)` — возвращает на предыдущий шаг через `pop()` из `_history`
- `clear_user_state(context)` — сброс диалога

Кнопка **🔙 Назад** работает универсально для всех диалогов без необходимости обновлять карту переходов.

---

## 🎯 Использование

### Для пользователей

| Команда/Кнопка | Описание |
|----------------|----------|
| `/start` | Авторизация и получение клавиатуры |
| `/help` | Список доступных команд |
| `📚 Материалы` | Просмотр материалов по разделам |
| `📅 События комьюнити` | Предстоящие мероприятия |
| `⏱️ Записаться на мок` | Запись на пробное собеседование |
| `🤝 Buddy` | Система взаимопомощи |
| `/search <запрос>` | Поиск по материалам |

### Для администраторов / менеджеров

**Управление материалами:**
```
⚙️ Управление → 📦 Управление материалами
→ 📖 Просмотреть | ➕ Добавить | ✏️ Редактировать | 🗑️ Удалить | 📊 Статистика
```

При удаление/редактирование — подтверждение с предпросмотром.

**Управление событиями:**
```
⚙️ Управление → 📋 Управление событиями
→ 📖 Просмотреть | ➕ Добавить | ✏️ Редактировать | 🗑️ Удалить
```

Дата: поддержка форматов `2024-12-31 18:00`, `31.12.2024 18:00`, `сегодня 18:00`.

**Управление пользователями (только Admin):**
```
⚙️ Управление → 👥 Управление ролями
→ 📋 Список | ➕ Назначить роль | 🗑️ Удалить пользователя
```

Подтверждение перед назначением роли + preview списка.

Форматы добавления:
- `123456789` — только ID
- `@ivan_petrov` — только username
- `123456789 @ivan_petrov` — связать ID и username
- `@ivan, @petr, 123456789` — несколько пользователей

### Для менторов

```
🤝 Buddy → 📋 Список менти
→ Просмотр статуса | 🔄 Изменить статус | 🗑️ Удалить
```

Статусы: `active`, `completed`, `paused`, `dropped`.

### Для менеджеров / аналитиков

```
🤝 Buddy → 📊 Отчёты
→ Общая статистика по менторам и менти
```

### Команды в группах

| Команда | Описание |
|---------|----------|
| `/sabot_help` | Справка по командам |
| `/events` | Предстоящие события |
| `/material <название>` | Поиск материала |
| `/off` или `/remove_kb` | Убрать клавиатуру |

---

## 🛡️ Безопасность и Audit

### Audit Logging

Все критичные операции логируются в `audit.log`:
- Создание/удаление материалов
- Создание/удаление событий
- Назначение ролей
- Удаление пользователей
- Изменения в Buddy системе

Формат:
```
2026-03-08 18:00:25 | user_id=123456 | action=material_delete | {"mat_id": 5, "title": "..."}
```

### Input Validation

- Защита от null bytes (`\x00`)
- Защита от RTL override (`\u202E`)
- Защита от control characters
- Проверка длины входных данных
- Валидация callback данных

### Rate Limiting

- Per-user: 20 запросов за 10 секунд
- Per-group: 3 одинаковые команды → мут на 60 сек
- Memory protection: LRU cleanup при 50K записях

### Безопасность ошибок

- `DEBUG_MODE=False` — скрытие stack traces
- Логирование полных ошибок для диагностики
- Безопасные сообщения для пользователей

---

## 🗄️ База данных

### Таблицы

**roles** — справочник ролей
```sql
id | role_key
```

**users** — пользователи
```sql
id | user_id | username | created_at
```

**user_role_assignments** — many-to-many связь ролей
```sql
user_id | role_id
```

**materials** — материалы
```sql
id | stage | title | link | description | created_at
```

**events** — события
```sql
id | event_type | event_datetime | link | announcement | created_at
```

**bans** — блокировки
```sql
id | user_id | username | ban_level | banned_until | created_at
```

**failed_attempts** — неудачные попытки входа
```sql
id | user_id | username | attempt_count | last_attempt
```

**buddy_mentorships** — наставничества
```sql
id | mentor_id | mentee_id | mentee_full_name | mentee_telegram_tag | status | assigned_date | created_at
```

### Автоматические миграции

При старте `Database.init_db()` автоматически применяет:
1. **v1 → v2:** переход от строки `role` в `user_roles` к нормализованной many-to-many схеме (`users` + `roles` + `user_role_assignments`)
2. **lion → capabilities:** замена устаревшей роли `lion` на гранулярный набор `{mentor, manager, analyst}`

Ручное вмешательство не требуется — существующие базы данных обновляются прозрачно.

---

## ⚙️ Технические детали

### Стек
- **Python 3.11+**
- **python-telegram-bot[job-queue]>=20.0** — асинхронный фреймворк
- **aiosqlite==0.22.1** — асинхронный SQLite (WAL mode)
- **python-dotenv==1.2.1** — переменные окружения

### Особенности
- ✅ **Capability-based роли** — никаких magic priorities
- ✅ **Bundle-роли** — "manager" = {mentor, manager, analyst}
- ✅ **Audit logging** — полный трек операций
- ✅ **Input validation** — защита от injection
- ✅ **Flexible dates** — естественный язык
- ✅ **Confirmation dialogs** — подтверждение опасных операций
- ✅ **Typing indicators** — UX при загрузке
- ✅ **Memory protection** — LRU cleanup

### Безопасность
- ✅ SQL инъекции — параметризованные запросы
- ✅ XSS — экранирование Markdown
- ✅ Input injection — sanitize_input()
- ✅ Callback injection — validate_callback_data()
- ✅ DoS — rate limiting + memory limits
- ✅ Info disclosure — DEBUG_MODE

---

## 🐛 Отладка

### Ручная очистка бана

```python
from db_utils import unban_user
await unban_user(user_id=123456789)
```

### Просмотр audit логов

```bash
tail -f audit.log
```

### Сброс БД

Удалите `user_roles.db` и `audit.log` — при следующем запуске создадутся новые.

---

## 🧪 Тестирование

```bash
# Синтаксическая проверка всех модулей
python -m py_compile main.py
python -c "from handlers import *"
```

---

## 🐳 Docker

```bash
docker-compose up -d
```

---

## 📝 Лицензия

MIT License

---

## 📄 Документация

- `PROJECT_ANALYSIS.md` — Архитектурный анализ и обоснование решений
- `DEPLOY.md` — Инструкции по деплою
- `AGENTS.md` — Гайд для AI-агентов, работающих с кодовой базой

---

**SABot — Production Ready** 🚀🛡️
