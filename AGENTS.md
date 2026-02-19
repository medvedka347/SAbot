# AGENTS.md — Руководство для AI-агентов

## 🤖 О проекте

**SABot** — Telegram-бот для управления учебным комьюнити системного анализа.
Реализован на Python + aiogram 3.x + SQLite.

---

## 📁 Архитектура

```
SABot/
├── main.py           # Точка входа: инициализация бота, регистрация хендлеров, запуск polling
├── config.py         # Константы: токен, имя БД, роли (user/mentor/admin), стадии материалов
├── db_utils.py       # Вся работа с БД: CRUD для users, materials, events, bans
├── admin_module.py   # FSM-обработчики, клавиатуры, логика команд
├── requirements.txt  # Зависимости Python
└── .env.example      # Шаблон переменных окружения
```

### Ключевые компоненты

| Модуль | Ответственность |
|---|---|
| `config.py` | Роли (`ROLE_USER/MENTOR/ADMIN`), стадии (`STAGE_FUNDAMENTAL` и т.д.) |
| `db_utils.py` | `Database` класс, CRUD-функции, rate limiting / bans, фильтры `IsAuthorizedUser` |
| `admin_module.py` | `Form` (FSM), клавиатуры (`user_kb`, `admin_kb`...), все async-обработчики |
| `main.py` | `Bot` + `Dispatcher`, регистрация хендлеров, `asyncio.run(main())` |

### Роли и доступ
- **user** — материалы, события, запись на мок, buddy
- **mentor** — то же + кнопка "⚙️ Админка"
- **admin** — полный CRUD: материалы, события, роли пользователей, управление банами

### Стадии материалов (STAGES в config.py)
- `fundamental` → 📚 Фундаментальная теория
- `practical_theory` → 🔧 Практическая теория
- `practical_tasks` → 📝 Практические задания
- `roadmap` → 🗺️ Roadmap (info)

---

## 🗄️ Схема базы данных

```sql
user_roles(user_id, username, role, created_at)
materials(id, stage, title, link, description, created_at)
events(id, event_type, event_datetime, link, announcement, created_at)
bans(id, user_id, username, ban_level, banned_until, created_at)
failed_attempts(id, user_id, username, attempt_count, last_attempt)
```

---

## ⚙️ Запуск и тестирование

```bash
# Установка зависимостей
pip install -r requirements.txt

# Настройка окружения
cp .env.example .env
# Задайте BOT_TOKEN и INITIAL_ADMIN_ID в .env

# Запуск
python main.py
```

Тестов нет — проверяйте поведение вручную через реального бота или через `db_utils.py` напрямую.

---

## 🚀 5 Предложенных и реализованных улучшений

### 1. 🐛 Исправлен баг расчёта времени бана (`apply_ban` в `db_utils.py`)
**Проблема:** `now.replace(minute=now.minute + 5)` вызывало `ValueError` при `minute >= 55`.  
**Решение:** заменено на `now + timedelta(minutes=5)` / `timedelta(days=30)`.

### 2. 🔍 Поиск по материалам (`/search`)
**Что:** команда `/search <запрос>` ищет по названию и описанию материалов.  
**Где:** `db_utils.py` → `search_materials()`, `admin_module.py` → `search_handler()`, `main.py` → регистрация.  
**Польза:** когда материалов много, находить нужное стало значительно быстрее.

### 3. 🚫 Панель управления банами для админа
**Что:** новый раздел "🚫 Управление банами" в меню администратора.  
**Где:** `admin_module.py` → `bans_menu()`, `bans_list()`, `ban_unban_callback()`.  
**Польза:** раньше разбанить пользователя можно было только через код; теперь — через бота.

### 4. ⏰ Периодическая очистка истёкших банов
**Что:** фоновая `asyncio`-задача, которая вызывает `cleanup_expired_bans()` каждый час.  
**Где:** `main.py` → `periodic_cleanup()` + `asyncio.create_task()` при старте.  
**Польза:** истёкшие баны удаляются автоматически, а не только при `/start`.

### 5. ❓ Команда `/help` с учётом роли пользователя
**Что:** команда `/help` показывает список доступных функций в зависимости от роли.  
**Где:** `main.py` → `help_handler()` + регистрация через `CommandHelp`.  
**Польза:** новые пользователи сразу понимают, что умеет бот.

---

## 📝 Советы для AI-агентов

1. **FSM:** при добавлении нового многошагового диалога — добавить `State` в `Form` (`admin_module.py`) и зарегистрировать хендлеры в `register_handlers()`.
2. **БД:** используйте `db.execute()` / `db.fetchone()` / `db.fetchall()` — они сами управляют соединением через контекстный менеджер.
3. **Клавиатуры:** вспомогательная функция `kb(buttons, back_button)` строит `ReplyKeyboardMarkup`. Для inline — `inline_kb(buttons)`.
4. **Роли:** всегда проверяйте роль через `get_user_role(user_id=..., username=...)` — поддерживает оба идентификатора.
5. **Rate limit:** в начале каждого хендлера вызывайте `check_rate_limit(message.from_user.id)`.
6. **Безопасность:** не добавляйте имена столбцов напрямую в SQL-строки (используйте `frozenset` для whitelist, как в `update_material` / `update_event`).
