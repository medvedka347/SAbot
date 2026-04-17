# AGENTS.md — Руководство для AI-агентов

## 🤖 О проекте

**SABot** — Telegram-бот для управления учебным комьюнити системного анализа.
Реализован на **Python + python-telegram-bot (PTB) v20+ + aiosqlite** (async SQLite, WAL mode).

---

## 📁 Архитектура

```
SABot/
├── main.py                 # Точка входа, Application, JobQueue, регистрация хендлеров
├── config.py               # Константы, granular роли, ROLE_BUNDLES, can_access()
├── db_utils.py             # Database класс, CRUD, авторизация, автоматические миграции
├── utils.py                # Клавиатуры, rate limit, formatters
├── audit_logger.py         # Audit logging для безопасности
└── handlers/               # Модули обработчиков (PTB MessageHandler / CallbackQueryHandler)
    ├── common.py           # /start, /help, 🔙 Назад, fallback, Buddy entry
    ├── materials.py        # CRUD материалов + confirmations
    ├── events.py           # CRUD событий + flexible dates
    ├── roles.py            # Управление ролями + confirmations
    ├── bans.py             # Просмотр/снятие банов
    ├── mocks.py            # Запись на мок
    ├── search.py           # /search, групповые команды
    ├── buddy.py            # Buddy system + отчёты
    └── conversation_utils.py  # Легковесный FSM: _state + _history
```

---

## ⚠️ Критически важно — состояние проекта (as is)

### Фреймворк
- **python-telegram-bot (PTB) v20+**, а **НЕ aiogram**
- Нет `Router`, `StatesGroup`, `FSMContext`, `BaseMiddleware`
- Хендлеры регистрируются напрямую через `application.add_handler(...)` в `main.py`
- Для фильтрации по состоянию используется кастомный фильтр `in_state(state)` в `main.py`

### FSM (легковесный custom)
Состояния хранятся в `context.user_data`:
- `context.user_data["_state"]` — текущее состояние (строка)
- `context.user_data["_history"]` — стек предыдущих состояний (список строк)

**API:**
- `set_user_state(context, new_state)` — переключает состояние и пушит текущее в `_history`
- `get_user_state(context)` → `str | None`
- `clear_user_state(context)` — сбрасывает `_state` и `_history`
- `back_handler(update, context)` — попает предыдущее состояние и восстанавливает его

### Кнопка "Назад" (🔙)
**Поведение:**
- Если `_history` непустой → `pop()` последнего состояния и возврат к нему
- Если история пуста → вызов `main_menu_handler()` (сброс в главное меню)

**Где:** `handlers/conversation_utils.py` → `back_handler()`

**Важно:** Кнопка работает **автоматически** для любого диалога без необходимости обновлять `STATE_MAP`.

### Capability-based роли
Роли гранулярные (атомарные):
- `user` — базовый пользователь
- `mentor` — может вести менти
- `manager` — CRUD материалов/событий, назначение бадди
- `analyst` — статистика и отчёты
- `admin` — полный доступ (founder)

**ROLE_BUNDLES:**
```python
ROLE_BUNDLES = {
    "manager": {"mentor", "manager", "analyst"},
}
```
В базе хранятся **только** атомарные роли. Bundle — это чисто UI/конфиг концепция.

**Проверка доступа:**
```python
can_access("materials_crud", role_keys)   # admin / manager
can_access("buddy_analytics", role_keys)  # admin / analyst
```

### Авторизация
Нет middleware-чёрного ящика. Проверка **явная** в начале защищённых хендлеров:
```python
from db_utils import require_any_role, require_role

user = await require_any_role(update, context, {"admin", "manager"})
```

### 🤝 Buddy (Система наставничества)
**Модуль:** `handlers/buddy.py`

**Для Админов / Менеджеров / Аналитиков:**
- 📊 Отчёты — статистика по всей системе
- ➕ Назначить бадди — назначение менти конкретному ментору
- 📋 Все менти — полный список (доступ зависит от capability)

**Для Менторов (`buddy_mentor`):**
- 📋 Список менти — просмотр всех менти с ФИО, датой, статусом
- ➕ Добавить менти — FSM: ФИО → @username → дата (ДД.ММ.ГГ)
- Управление: изменение статуса (`active`/`completed`/`paused`/`dropped`), удаление

**Для Пользователей:**
- Проверка наличия назначенного ментора
- Если есть — показывает контакты ментора
- Если нет — сообщение "Тебе пока не назначен бадди"

**Таблицы БД:**
- `buddy_mentorships` (`id`, `mentor_id`, `mentee_id`, `mentee_full_name`, `mentee_telegram_tag`, `status`, `assigned_date`, `created_at`)
- `users`, `roles`, `user_role_assignments` — для связи `mentor_id` с пользователем и его ролями

### Мультироли
**Поддержка нескольких ролей:** Пользователь может иметь несколько атомарных ролей одновременно.

**Формат хранения:** Many-to-many через `users` + `roles` + `user_role_assignments`.

**Функции (в `db_utils.py`):**
- `get_user_roles_simple(user_id)` → `list[str]` (атомарные роли)
- `add_user_role(user_id, role_key)`
- `remove_user_role(user_id, role_key)`
- `remove_all_user_roles(user_id)`

**Примеры комбинаций:**
- `admin` — только админ
- `admin,mentor` — админ + ментор
- `manager` (bundle) → в БД: `mentor`, `manager`, `analyst`

### БД и миграции
- `Database.init_db()` создаёт таблицы при первом запуске
- Автоматические миграции:
  - `_migrate_roles_v1_to_v2()` — переход от единой строки `role` в `user_roles` к many-to-many схеме
  - `_migrate_lion_to_capabilities()` — замена устаревшей роли `lion` на `{mentor, manager, analyst}`
- Все функции БД — **async**, требуют `await`
- Rate limiting (`utils.py`) и bans (`db_utils.py`) — разные системы

### Чего НЕТ в проекте
- ❌ Юнит-тестов — проверка только вручную через бота
- ❌ `admin_module.py` — был разделён на handlers/*.py
- ❌ `aiogram` — проект полностью перешёл на PTB
- ❌ `AuthMiddleware`, `HasRole` — авторизация явная через `require_*`

---

## 📝 Принципы работы AI-агента

> Применяй эти принципы **по умолчанию**, если в промте не сказано иное.

### 1. Аккуратность
- Не ломать существующий функционал
- Не удалять код без явного разрешения
- Проверять импорты после изменений

### 2. Спрашивать подтверждение
Если решение вызывает сомнения — **остановиться и спросить**.  
Примеры:
- Изменение архитектуры
- Удаление функционала
- Изменение поведения кнопок/команд
- Рефакторинг без явной просьбы

### 3. Масштабные доработки
Если доработка затрагивает несколько файлов или меняет логику:
1. Объяснить план пользователю
2. Получить подтверждение
3. Только потом реализовывать

### 4. Связанные изменения
После правок проверить:
- Остались ли неактуальные комментарии?
- Остался ли неиспользуемый код?
- Нужно ли обновить документацию?
- Нужно ли обновить импорты в других файлах?

### 5. Проверка целостности
После изменений:
- `python -c "from handlers import *"` — импорты работают?
- `python -m py_compile main.py` — синтаксис корректен?
- Нет ли дублирующихся определений?

---

## 🛠️ Типичные задачи

### Добавить новый диалог (FSM)
1. Определить строковые состояния в соответствующем модуле `handlers/`
2. Добавить хендлеры с фильтром `in_state("STATE_NAME")` (регистрируются в `main.py`)
3. Использовать `set_user_state(context, "STATE_NAME")` для переходов
4. В начале защищённых хендлеров вызывать `await require_any_role(update, context, {"admin", ...})`
5. Кнопка "Назад" работает автоматически — ничего дописывать не нужно

### Добавить новую команду
1. Выбрать подходящий модуль handlers/
2. Добавить `async def my_cmd(update, context):` с декоратором-регистрацией в `main.py`:
   ```python
   application.add_handler(CommandHandler("my_cmd", common.my_cmd))
   ```
3. Добавить rate limit: `check_rate_limit(update.effective_user.id)`
4. Если нужна клавиатура — использовать `kb()` или `inline_kb()` из utils

### Изменить БД
1. Новые таблицы — в `Database.init_db()`
2. Миграции существующих — в отдельный метод `_migrate_*()`
3. CRUD-функции — `async`, принимают/возвращают `dict` или примитивы

---

## 🗄️ Схема БД

```sql
roles(id, role_key)
users(id, user_id UNIQUE, username UNIQUE, created_at)
user_role_assignments(user_id, role_id)
materials(id, stage, title, link, description, created_at)
events(id, event_type, event_datetime, link, announcement, created_at)
bans(id, user_id, username, ban_level, banned_until, created_at)
failed_attempts(id, user_id UNIQUE, username UNIQUE, attempt_count, last_attempt)
buddy_mentorships(id, mentor_id, mentee_id, mentee_full_name, mentee_telegram_tag, status, assigned_date, created_at)
```
