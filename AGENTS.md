# AGENTS.md — Руководство для AI-агентов

## 🤖 О проекте

**SABot** — Telegram-бот для управления учебным комьюнити системного анализа.
Реализован на Python + aiogram 3.x + aiosqlite (async SQLite).

---

## 📁 Архитектура (модульная)

```
SABot/
├── main.py                 # Точка входа: инициализация бота, роутеры, polling
├── config.py               # Константы: токен, роли, стадии материалов
├── db_utils.py             # Вся работа с БД: CRUD, фильтры, bans
├── utils.py                # Клавиатуры, rate limit, formatters, helpers
│
└── handlers/               # Модули хендлеров (aiogram Router)
    ├── __init__.py
    ├── common.py           # /start, /help, ⚙️ Админка, 🔙 Назад, 🤝 Buddy
    ├── materials.py        # CRUD материалов + публичный просмотр
    ├── events.py           # CRUD событий + анонсы в группу
    ├── roles.py            # Управление ролями пользователей
    ├── bans.py             # Просмотр и снятие банов
    ├── mocks.py            # Запись на мок-интервью
    └── search.py           # /search, /material (group), /sabot_help
```

### Ключевые компоненты

| Модуль | Ответственность |
|---|---|
| `config.py` | Роли (`ROLE_USER/MENTOR/ADMIN`), стадии (`STAGE_FUNDAMENTAL` и т.д.) |
| `db_utils.py` | `Database` класс, CRUD-функции, rate limiting / bans, фильтр `HasRole` |
| `utils.py` | Клавиатуры (`kb`, `back_kb`), rate limit, formatters (`escape_md`, `format_material`) |
| `handlers/common.py` | Общие команды, кнопка "Назад", меню админки |
| `handlers/materials.py` | `MaterialStates` (FSM), CRUD материалов |
| `handlers/events.py` | `EventStates` (FSM), CRUD событий, анонсы в группу |
| `handlers/roles.py` | `RoleStates` (FSM), управление ролями, пагинация |
| `handlers/bans.py` | Просмотр и снятие банов |
| `handlers/mocks.py` | Запись на мок (календари) |
| `handlers/search.py` | Поиск по материалам, групповые команды |
| `main.py` | Подключение роутеров, middleware, polling с таймаутами |

### Роли и доступ
- **user** — материалы, события, запись на мок, buddy
- **mentor** — то же + кнопка "⚙️ Админка"
- **admin** — полный CRUD: материалы, события, роли, баны

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

---

## 📝 Советы для AI-агентов

1. **FSM States:** У каждого модуля свои StatesGroup:
   - `MaterialStates` в `materials.py`
   - `EventStates` в `events.py`
   - `RoleStates` в `roles.py`

2. **Роутеры:** Каждый модуль создаёт `Router(name="...")` и регистрируется в `main.py`.

3. **Права доступа:** Используйте фильтр `HasRole(ROLE_ADMIN)`:
   ```python
   @router.message(F.text == "📦 Управление материалами", HasRole(ROLE_ADMIN))
   ```

4. **Кнопка "Назад":** Централизована в `handlers/common.py` — просто сбрасывает state и возвращает в главное меню.

5. **Rate limit:** Вызывайте `check_rate_limit(user_id)` в начале хендлеров.

6. **БД:** ВСЕ функции асинхронные! Используйте `await`:
   - `await get_user_role()` / `await add_material()` и т.д.

7. **Клавиатуры:**
   - `kb(buttons, back_button)` — ReplyKeyboardMarkup
   - `inline_kb(buttons)` — InlineKeyboardMarkup
   - `back_kb` — пустая клавиатура с кнопкой "Назад"
   - `stage_kb` — клавиатура с разделами материалов
