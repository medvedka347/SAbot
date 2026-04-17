# SABot — Низкоуровневый технический анализ архитектуры и кода

> Файл создан для внутреннего использования AI-агентом. Содержит детальный разбор механизмов, схем, связей и особенностей реализации.

---

## 1. Стек и общая архитектура

- **Фреймворк:** aiogram 3.25.0 (async Telegram Bot API)
- **БД:** SQLite через `aiosqlite` 0.22.1, WAL-режим
- **Конфигурация:** `python-dotenv`, переменные в `.env`
- **Точка входа:** `main.py` — инициализация `Bot` + `Dispatcher`, подключение роутеров, polling с graceful shutdown
- **Архитектура handlers:** модульная, каждый модуль = `Router`. Порядок подключения роутеров в `main.py` критичен (`common` последний, т.к. содержит fallback)
- **Состояния:** FSM (`aiogram.fsm.state`) с хранением состояний в памяти (дефолт aiogram)

---

## 2. База данных (SQLite + aiosqlite)

### 2.1. Класс Database (`db_utils.py`)

Создан кастомный класс-обёртка `Database`:

```python
class Database:
    def __init__(self, db_path: str = DB_NAME):
        self.db_path = db_path
        self._lock = asyncio.Lock()  # Сериализация записей
```

**Методы:**
- `execute()` — записи с `asyncio.Lock()` (сериализованы)
- `fetchone()` / `fetchall()` — чтение без блокировки, каждый раз новое соединение
- `_init_connection()` — включает WAL, PRAGMA-настройки

**Важная особенность:** Каждый `fetchone/fetchall` открывает **новое** соединение. Это не пул соединений. Записи идут через `execute()` с глобальным lock.

### 2.2. Схема БД

#### Таблицы:

| Таблица | Назначение |
|---------|------------|
| `user_roles` | Справочник пользователей (`id`, `user_id` BIGINT UNIQUE, `username` TEXT UNIQUE, `created_at`) |
| `roles` | Справочник ролей v2 (`id`, `role_key` UNIQUE, `role_name`, `priority`, `description`) |
| `user_role_assignments` | Many-to-many связь пользователей и ролей (`user_id` → `user_roles.id`, `role_id` → `roles.id`) |
| `materials` | Учебные материалы (`id`, `stage` CHECK IN(...), `title`, `link`, `description`, `created_at`) |
| `events` | События комьюнити (`id`, `event_type`, `event_datetime`, `link`, `announcement`, `created_at`) |
| `bans` | Баны (`id`, `user_id`, `username`, `ban_level` CHECK IN(1,2,3), `banned_until`, `created_at`) |
| `failed_attempts` | Неудачные попытки авторизации (`user_id` UNIQUE, `username` UNIQUE, `attempt_count`, `last_attempt`) |
| `buddy_mentorships` | Наставничество (`id`, `mentor_id` → `user_roles.id`, `mentee_id` → `user_roles.id` nullable, `mentee_full_name`, `mentee_telegram_tag`, `status` CHECK IN('active','completed','paused','dropped'), `assigned_date`, `created_at`) |
| `_migrations` | Отслеживание миграций (`migration_name` UNIQUE) |

#### Индексы:
- `idx_buddy_mentor` / `idx_buddy_mentee`
- `idx_materials_stage`
- `idx_events_datetime`
- `idx_bans_active` (композитный)
- `idx_failed_user`
- `idx_user_roles_user` / `idx_user_roles_role`

### 2.3. Миграционная система

**Версионирование ролей v1 → v2:**
- `user_roles` создаётся **сначала со старым полем `role`** (для совместимости)
- При старте проверяется `_migrations` на наличие `roles_v1_to_v2`
- `_migrate_roles_v1_to_v2()` парсит строки вида `"admin,lion"` и раскладывает в `user_role_assignments`
- После успешной миграции вызывается `_drop_role_column()` — пересоздаётся `user_roles` без поля `role` (через `CREATE TABLE ..._new` → `INSERT` → `DROP` → `ALTER TABLE RENAME`)

**Миграция materials:**
- `_migrate_materials()` — если нет поля `stage`, таблица пересоздаётся со старыми данными в `fundamental`

### 2.4. CRUD-паттерны в БД

**Материалы:**
- `add_material()` — возвращает `cursor.lastrowid` через прямое `aiosqlite.connect()`
- `get_materials(stage=None)` — фильтр по stage опциональный
- `get_material(material_id)` — одиночная запись
- `update_material(material_id, **fields)` — `allowed = frozenset({'stage', 'title', 'link', 'description'})`, динамический SQL
- `delete_material(material_id)` — простой DELETE

**События:**
- Аналогично материалам, `allowed = frozenset({'event_type', 'event_datetime', 'link', 'announcement'})`
- `add_event()` валидирует `datetime.fromisoformat()` перед записью
- `get_events(upcoming_only=False)` — фильтр `WHERE event_datetime > datetime('now')`

**Buddy:**
- `add_mentorship()` — проверяет существование `mentor_id` и `mentee_id` в `user_roles`, возвращает `lastrowid`
- `get_mentor_mentees(mentor_id)` — mentor_id = `user_roles.id`, не Telegram ID
- `get_user_mentor(user_id)` — user_id = `user_roles.id`, ищет активное наставничество
- `update_mentorship_status()` / `delete_mentorship()` / `get_mentorship_by_id()`

**Пользователи и роли:**
- `get_user_by_id(user_id)` — Telegram ID → dict с `id` (внутренний), `user_id`, `username`
- `get_user_by_db_id(db_id)` — внутренний ID
- `get_user_by_username(username)` — нормализует (убирает @, lowercase)
- `get_user_roles(user_id=None, username=None)` — JOIN `user_role_assignments` + `roles`, сортировка по `priority DESC`
- `get_user_roles_simple()` — список `role_key`
- `assign_role(user_id, role_key, assigned_by=None)` — добавляет роль
- `revoke_role(user_id, role_key)` — удаляет роль
- `set_user_roles(user_id, role_keys, assigned_by)` — атомарная замена через транзакцию
- `add_or_update_user(user_id, username, role)` — сложная логика дедупликации по ID/username, обновление связей, назначение роли
- `get_all_users()` — GROUP_CONCAT ролей в одном запросе

---

## 3. Система ролей и авторизации

### 3.1. Конфигурация ролей (`config.py`)

```python
ROLE_USER = "user"      # priority 100
ROLE_MENTOR = "mentor"  # priority 200
ROLE_ADMIN = "admin"    # priority 300
ROLE_LION = "lion"      # priority 400 (meta-admin)
```

Приоритеты используются для иерархического доступа.

### 3.2. Мультироли

В БД v2 роли хранятся в нормализованной схеме. Пользователь может иметь несколько ролей одновременно.

**Обратная совместимость:**
- `get_user_role()` — возвращает первую роль из списка (для legacy)
- `add_user_role()` / `remove_user_role()` — алиасы на `assign_role()` / `revoke_role()`

### 3.3. MODULE_ACCESS

```python
MODULE_ACCESS = {
    "materials": 300,   # admin+
    "events": 200,      # mentor+
    "roles": 300,       # admin+
    "bans": 300,        # admin+
    "buddy_lion": 400,  # lion only
    "buddy_mentor": 200,# mentor+
    "mocks": 100,       # user+
    "search": 100,      # user+
}
```

### 3.4. AuthMiddleware (`db_utils.py`)

```python
class AuthMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
```

**Логика:**
- Пропускает только события с `from_user`
- **Исключение:** `/start` всегда пропускается (start_handler сам обрабатывает баны)
- Получает роли через `get_user_roles()`
- Если нет ролей — блокирует, отправляет "❌ У вас нет доступа..."
- Если есть роли — кладёт в `data`:
  - `user_roles` — список dict
  - `user_role` — первая роль (legacy)
  - `user_max_priority` — max priority
  - `user_id`, `username`

**Подключение:** `dp.message.middleware(AuthMiddleware())` и `dp.callback_query.middleware(AuthMiddleware())`

### 3.5. HasRole фильтр

```python
class HasRole:
    def __init__(self, role=None, min_priority=None)
```

**Поведение:**
- Если задан `role` (str или list) — проверяет `any(role in user_role_keys)`
- Если задан `min_priority` — проверяет `user_max_priority >= min_priority`
- Если `role` задан без `min_priority` — вычисляет max priority из ролей автоматически
- Сначала смотрит кеш из `data` (middleware), потом делает запрос в БД

### 3.6. Бан-система

**Таблица `failed_attempts`:**
- При неудачной авторизации (пользователь не в БД) вызывается `record_failed_attempt()`
- `attempt_count` инкрементируется
- При достижении 3-х вызывается `apply_ban()`

**Таблица `bans`:**
- Уровни: 1 (5 мин), 2 (10 мин), 3 (30 дней)
- `apply_ban()` смотрит предыдущий бан и повышает уровень
- Очищается по таймеру каждый час (`periodic_cleanup()` в `main.py`)
- `get_ban_status()` — активный бан по `user_id` или `username`

---

## 4. FSM (Finite State Machine) — диалоги

### 4.1. StatesGroup по модулям

**MaterialStates** (`materials.py`):
- `menu` → `selecting_stage` → `input_title` → `input_link` → `input_desc`
- `selecting_stage_public` — публичный просмотр
- `selecting_item` — выбор материала для редактирования/удаления
- `editing` — ввод новых данных

**EventStates** (`events.py`):
- `menu` → `input_type` → `input_datetime` → `input_link` → `input_announcement` → `confirm_announce`
- `selecting_item` — выбор события
- `editing`

**RoleStates** (`roles.py`):
- `menu` → `input_users` → `selecting_role`
- `selecting_user_to_delete`

**BuddyStates** (`buddy.py`):
- `menu` → `input_full_name` → `input_telegram_tag` → `input_assigned_date` → `selecting_status`

### 4.2. Кнопка "Назад" (`common.py`)

Реализована через `ENTRY_POINT_MAP` — маппинг текущего состояния → entry point:

```python
ENTRY_POINT_MAP = {
    MaterialStates.input_title: MaterialStates.selecting_stage,
    MaterialStates.input_link: MaterialStates.selecting_stage,
    EventStates.input_type: EventStates.menu,
    BuddyStates.input_full_name: BuddyStates.menu,
    # ...
}
```

**Алгоритм:**
1. Получает `current_state`
2. Ищет в `ENTRY_POINT_MAP` по `state_obj.state == current_state`
3. Если найдено — `state.set_state(entry_point)` + отправляет сообщение entry point
4. Если нет — `main_menu_handler()` (полный сброс)

**Ограничение:** Нет поддержки возврата из `selecting_stage_public` и `selecting_user_to_delete` — всегда в главное меню.

---

## 5. Клавиатуры и UI

### 5.1. Генераторы (`utils.py`)

```python
def kb(buttons: list, back_button: str = None) -> ReplyKeyboardMarkup
```
- Каждая кнопка — отдельный ряд
- `back_button` добавляется последним рядом

```python
def inline_kb(buttons: list[list]) -> InlineKeyboardMarkup
```

### 5.2. Динамическая главная клавиатура

`get_main_keyboard(user_id)` — формирует клавиатуру на основе мультиролей:
- **Пользовательские:** 2×2 сетка (Материалы, События, Мок, Buddy)
- **Управленческие:** разделитель `───── ⚙️ Управление ─────`, затем кнопки управления по одной в ряд
- Условия: `max_priority >= 200` → "Управление событиями", `>= 300` → материалы, роли, баны

### 5.3. Статические клавиатуры

- `user_kb`, `mentor_kb`, `admin_kb` — legacy, всё ещё используются в `start_handler` и `admin_handler`
- `back_kb` — `[🏠 Главное меню]` + `🔙 Назад`
- `stage_kb` — список stage + главное меню + назад

---

## 6. Rate Limiting

### 6.1. Пользовательский (`utils.py`)

In-memory: `_rate_limits = {user_id: [timestamps]}`

```python
RATE_LIMIT_WINDOW = 10.0      # секунд
RATE_LIMIT_MAX_REQUESTS = 20  # запросов за окно
RATE_LIMIT_MIN_GAP = 0.15     # 150ms между кликами
```

**Cleanup:** При превышении 50000 записей удаляется 20% самых старых (LRU).

### 6.2. Групповой (`utils.py`)

In-memory: `_group_rate_limits = {chat_id: {command: {"timestamps": [], "muted_until": 0}}}`

```python
GROUP_RATE_LIMIT_WINDOW = 30.0  # сек
GROUP_RATE_LIMIT_MAX = 3        # команд
GROUP_RATE_LIMIT_MUTE = 60      # сек мут
```

Применяется в групповых командах: `/events`, `/material`, `/sabot_help`.

---

## 7. Низкоуровневые механизмы модулей

### 7.1. Materials (`handlers/materials.py`)

**Админский CRUD:**
- Доступ через `HasRole(min_priority=MODULE_ACCESS["materials"])` (=300)
- **Защита от reply-атаки:** почти во всех хендлерах проверка:
  ```python
  if message.reply_to_message and message.reply_to_message.from_user.id != message.from_user.id:
      await message.answer("❌ Нет прав.")
      return
  ```
- Добавление: FSM с выбором stage → title → link → desc
- Редактирование: inline-клавиатура с callback `edit_mat:{id}`, затем ввод `название\n\nссылка\n\nописание`, `.` = пропуск
- Удаление: inline-клавиатура `del_mat:{id}` → подтверждение `conf_del_mat:{id}` / `cancel_del_mat`
- **Audit log:** `log_material_create()`, `log_material_delete()` — вызываются в хендлерах

**Публичный просмотр:**
- `public_materials_select()` — `MaterialStates.selecting_stage_public`
- Не требует специальной роли (доступ через AuthMiddleware = любой авторизованный)
- После показа материалов состояние остаётся `selecting_stage_public` (можно переключать разделы)

### 7.2. Events (`handlers/events.py`)

**CRUD:**
- Доступ `>= 200` (ментор+)
- Добавление: FSM тип → дата/время (гибкий парсинг `parse_datetime_flexible()`) → ссылка → анонс → подтверждение публикации
- Если заданы `ANNOUNCEMENT_GROUP_ID` и `ANNOUNCEMENT_TOPIC_ID` — возможность постинга анонса в группу с inline-кнопками "Иду/Не иду"
- Редактирование: формат `тип\n\nдата\n\nссылка\n\nописание`

**Публичный просмотр:**
- `public_events_show()` — `upcoming_only=True`, без FSM состояния

### 7.3. Roles (`handlers/roles.py`)

**Доступ:** admin+ (300+)

**Просмотр пользователей:**
- `get_all_users()` → группировка по ролям → плоский список → пагинация по 25 на страницу
- Inline-клавиатура с навигацией `users_page:{page}`

**Назначение ролей:**
- Парсинг ввода через `parse_users_input()` (utils.py) — поддерживает ID, @username, оба значения
- Сохраняется в state: `users_to_assign`
- Выбор роли: inline `set_role:{role}`
- Подтверждение: `conf_set_role` → вызов `set_users_batch()`
- **Важно:** `set_users_batch()` добавляет роль к существующим, не заменяет все

**Удаление пользователя:**
- Inline-список grouped by role (максимум 10 на роль)
- Callback формат: `del_user:id:{user_id}` или `del_user:un:{username}`

### 7.4. Bans (`handlers/bans.py`)

**Доступ:** admin+ (300+)
- `bans_menu()` — показывает активные баны с inline-кнопками "🔓 Разбан"
- Callback `unban:{ban_id}` → находит бан по ID записи → `unban_user(user_id, username)`

### 7.5. Mocks (`handlers/mocks.py`)

**Доступ:** любой авторизованный
- Конфигурация менторов в `config.MOCK_MENTORS` (hardcoded)
- `booking_handler()` — показывает инструкцию + доступных менторов
- `mock_select_handler()` — ловит `F.text`, проверяет `text.endswith(name)` для каждого ментора
- Если available + cal_link → отправляет ссылку на календарь

### 7.6. Search (`handlers/search.py`)

**ЛС:** `/search <запрос>` — `search_materials()` по title/description, max 20 результатов
**Группа:**
- `/events` — предстоящие события
- `/material <запрос>` — `search_materials_by_title()`, max 5
- `/sabot_help` — справка
- `/off`, `/remove_kb` — `ReplyKeyboardRemove()`

### 7.7. Buddy (`handlers/buddy.py`)

**Доступ:**
- `MODULE_ACCESS["buddy_mentor"]` = 200 (ментор+)
- `MODULE_ACCESS["buddy_lion"]` = 400 (лев)

**Ментор:**
- `buddy_list_mentees()` — получает `user_roles.id` текущего пользователя, вызывает `get_mentor_mentees(mentor_db_id)`
- Добавление: FSM ФИО → @username (или пропустить) → дата (`parse_date_flexible`)
- Детали менти: callback `buddy_mentee:{id}` → inline-кнопки "Изменить статус", "Удалить"
- Статусы: `active`, `completed`, `paused`, `dropped`

**Лев:**
- `lion_panel()` — клавиатура: Список менторов, Все менти, Назначить бадди
- `lion_list_mentors()` — статистика по каждому ментору (active/completed/dropped)
- `lion_assign_start()` — выбор ментора через inline, сохраняет `selected_mentor_id` в state
- Далее тот же FSM, но `mentor_id` берётся из state, а не текущего пользователя
- `lion_show_mentor_details()` — отчёт по ментору со списком всех менти

**Важная деталь:** В `buddy_show_mentee()` проверяется `is_owner` (mentee['mentor_id'] == current_user['id']) **ИЛИ** `is_lion` — это защита от доступа к чужим менти.

---

## 8. Обработка ошибок

### 8.1. Global error handler (`utils.py`)

```python
async def error_handler(event: ErrorEvent):
```

- Логирует с `exc_info=True`
- `DEBUG_MODE` из env — если True, показывает детали пользователю
- Иначе generic message
- Пытается ответить через `event.message` или `event.callback_query`

### 8.2. Graceful shutdown (`main.py`)

- Обработка `SIGTERM` / `SIGINT`
- Создаётся `asyncio.Event()` `shutdown_event`
- Polling запускается как `asyncio.create_task()`, ждётся либо polling, либо shutdown
- При shutdown — cancel polling, закрытие сессии бота, удаление PID-файла

### 8.3. Защита от двойного запуска

PID-файл `/tmp/sabot_bot.pid` — проверка существования процесса через `/proc/{pid}`. **Windows-несовместимо** (проект на Windows, но путь Unix).

---

## 9. Audit Logger (`audit_logger.py`)

- `RotatingFileHandler` на 10MB, 5 backup файлов
- Формат: `timestamp | user_id=X | action=Y | {JSON details}`
- Удобные функции для типовых операций
- `_sanitize_details()` — редэктит sensitive keys

**Использование:**
- Материалы: create, delete
- События: не используется в handlers (есть функции, но не вызываются)
- Роли: не используется в handlers
- Buddy: не используется в handlers
- Security: не используется

---

## 10. Зависимости и окружение

```
aiogram==3.25.0
python-dotenv==1.2.1
aiohttp==3.13.3
aiosqlite==0.22.1
```

- Python 3.10+ (используется `str | None` синтаксис)
- `.env` файл с `BOT_TOKEN`, `DB_NAME`, `INITIAL_ADMIN_ID`, `ANNOUNCEMENT_GROUP_ID`, `ANNOUNCEMENT_TOPIC_ID`

---

## 11. Выявленные особенности, риски и несоответствия

### 11.1. Потенциальные проблемы

1. **PID-файл на Windows:** `PID_FILE = "/tmp/sabot_bot.pid"` — Windows не имеет `/proc/{pid}`, `check_single_instance()` может работать некорректно или падать
2. **Отсутствие пула соединений БД:** Каждое чтение = новое соединение SQLite. При высокой нагрузке это bottleneck
3. **N+1 в Buddy для Льва:** `lion_list_mentors()` делает `get_mentor_stats()` отдельным запросом для КАЖДОГО ментора
4. **Дублирование rate limit проверок:** `check_rate_limit()` вызывается вручную почти в каждом хендлере; нет единого middleware
5. **Reply-защита дублируется:** Одинаковый блок `if message.reply_to_message...` скопирован в десятки хендлеров
6. **FSM state не сбрасывается после некоторых операций:** В `materials.py` public view не сбрасывает state при выходе
7. **mocks.py — ловушка `F.text`:** `mock_select_handler` ловит ВСЕ сообщения (`@router.message(F.text)`), проверяет `text.endswith(name)`. Это может перехватывать нерелевантные сообщения, но работает благодаря порядку роутеров (mocks перед common)

### 11.2. Неиспользуемый код

- `log_event_create/delete`, `log_role_assign`, `log_user_delete`, `log_mentee_*`, `log_lion_action`, `log_security_event` — определены, но **не вызываются** в handlers
- `IsAuthorizedUser` — определён, но не используется (вся авторизация через middleware)
- `get_user_primary_role()` — определён, используется редко
- `update_material()` импортируется в `materials.py`, но `log_material_update` не вызывается при редактировании

### 11.3. Структурные особенности

- **Модули handlers независимы:** Каждый имеет свой `Router`, `StatesGroup`, клавиатуры, helpers
- **Паттерн "Menu → Action → Stage → Input"** повторяется во всех CRUD-модулях
- **Callback data** строго типизирован через префиксы: `edit_mat:`, `del_mat:`, `conf_del_mat:`, `buddy_mentee:`, `lion_mentor:` и т.д.
- **Валидация callback** через `validate_callback_data()` в `utils.py` — используется выборочно

### 11.4. Групповые команды

- Работают **вне AuthMiddleware** для callback_query? Нет, middleware применяется ко всем message/callback_query, но `/start` — единственное исключение. Групповые команды (`/events`, `/material`, `/sabot_help`) обрабатываются только если пользователь авторизован (т.е. есть в БД).

---

## 12. Карта данных (Data Flow)

### /start (new user)
```
start_handler
  → cleanup_expired_bans()
  → get_ban_status()
  → get_user_roles()
    → get_user_by_id() / get_user_by_username()
    → JOIN user_role_assignments + roles
  → update_user_id_by_username() (если username без ID)
  → clear_failed_attempts()
```

### Добавление материала (admin)
```
materials_menu → state=menu
  → "➕ Добавить" → state=selecting_stage, action="add_material"
  → stage выбран → state=input_title
  → title → state=input_link
  → link → state=input_desc
  → desc → add_material() → log_material_create() → materials_menu
```

### Назначение роли (admin)
```
roles_menu → state=menu
  → "➕ Назначить роль" → state=input_users
  → parse_users_input() → state.update_data(users_to_assign)
  → state=selecting_role
  → inline "set_role:{role}" → state.update_data(selected_role)
  → inline "conf_set_role" → set_users_batch() → state.clear()
```

### Buddy — добавление менти (ментор)
```
buddy_add_start → state=input_full_name
  → _process_full_name() → state=input_telegram_tag
  → _process_telegram_tag() → state=input_assigned_date
  → buddy_add_date() → get_user_by_id() → add_mentorship() → state.clear()
```

---

## 13. Сводка по критичным файлам

| Файл | Ответственность | Ключевые сущности |
|------|-----------------|-------------------|
| `main.py` | Entry point, роутинг, lifecycle | `check_single_instance()`, `periodic_cleanup()`, `main()` |
| `config.py` | Константы, роли, приоритеты | `ROLE_*`, `MODULE_ACCESS`, `STAGES`, `MOCK_MENTORS` |
| `db_utils.py` | Всё, что связано с БД + auth | `Database`, `AuthMiddleware`, `HasRole`, CRUD функции |
| `utils.py` | Утилиты, клавиатуры, rate limit | `check_rate_limit()`, `kb()`, `get_main_keyboard()`, парсеры дат |
| `audit_logger.py` | Аудит (используется частично) | `AuditLogger`, `log_material_create/delete` |
| `handlers/common.py` | /start, /help, Назад, Buddy entry | `ENTRY_POINT_MAP`, `back_handler`, `buddy_handler` |
| `handlers/materials.py` | CRUD материалов + public view | `MaterialStates`, inline callbacks материалов |
| `handlers/events.py` | CRUD событий + public view | `EventStates`, анонсы в группу |
| `handlers/roles.py` | Управление пользователями/ролями | `RoleStates`, пагинация users |
| `handlers/bans.py` | Просмотр/снятие банов | active bans list, unban callbacks |
| `handlers/mocks.py` | Запись на мок-интервью | `MOCK_MENTORS`, `build_mock_kb()` |
| `handlers/search.py` | Поиск + групповые команды | `/search`, `/material`, `/events`, `/sabot_help` |
| `handlers/buddy.py` | Система наставничества | `BuddyStates`, lion panel, mentee CRUD |
