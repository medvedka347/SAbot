# AGENTS.md — Руководство для AI-агентов

## 🤖 О проекте

**SABot** — Telegram-бот для управления учебным комьюнити системного анализа.
Реализован на Python + aiogram 3.x + aiosqlite (async SQLite).

---

## 📁 Архитектура

```
SABot/
├── main.py                 # Точка входа, роутеры, polling
├── config.py               # Константы: токен, роли (ROLE_ADMIN, etc), стадии
├── db_utils.py             # Database класс, CRUD, фильтр HasRole, AuthMiddleware
├── utils.py                # Клавиатуры, rate limit, formatters
└── handlers/               # Модули хендлеров (aiogram Router)
    ├── common.py           # /start, /help, 🔙 Назад, fallback, 🤝 Buddy (entry)
    ├── materials.py        # MaterialStates, CRUD материалов
    ├── events.py           # EventStates, CRUD событий
    ├── roles.py            # RoleStates, управление ролями
    ├── bans.py             # Просмотр/снятие банов
    ├── mocks.py            # Запись на мок
    ├── search.py           # /search, групповые команды
    └── buddy.py            # BuddyStates, система наставничества
```

---

## ⚠️ Критически важно — состояние проекта (as is)

### FSM States
Каждый модуль имеет **свой** StatesGroup:
- `MaterialStates` — в `materials.py` (menu, selecting_stage, input_title, etc)
- `EventStates` — в `events.py` (menu, input_type, input_datetime, etc)
- `RoleStates` — в `roles.py` (menu, input_users, selecting_role, etc)
- `BuddyStates` — в `buddy.py` (menu, input_full_name, input_telegram_tag, input_assigned_date, selecting_status)

### Кнопка "Назад" (🔙)
**Поведение:** 
- Если есть `_prev_state` в данных FSM и он есть в STATE_MAP → возвращает на предыдущий шаг диалога
- Если нет истории или ошибка → сбрасывает `state.clear()` и возвращает в главное меню

**Где:** `handlers/common.py` → `back_handler()`

**Поддерживаемые переходы назад:**
- **Materials:** input_title → selecting_stage, input_link → input_title, selecting_item → selecting_stage, editing → selecting_item
- **Events:** input_datetime → input_type, input_link → input_datetime, input_announcement → input_link, confirm_announce → input_announcement, editing → selecting_item
- **Roles:** input_users → menu, selecting_role → input_users

**Не поддерживается:**
- Публичный просмотр материалов (selecting_stage_public) — всегда в главное меню
- Удаление пользователя (selecting_user_to_delete) — всегда в главное меню

### 🤝 Buddy (Система наставничества)
**Модуль:** `handlers/buddy.py`

**Для Льва (ROLE_LION) — мета-админ:**
- 🦁 Панель Льва — главное меню управления
- 📊 Список менторов — все менторы с статистикой (активно/завершено/брошено)
- 📋 Все менти — полный список менти во всей системе
- ➕ Назначить бадди — назначение менти конкретному ментору
- 📊 Отчеты по ментору — детальная статистика и список менти

**Для Менторов (ROLE_MENTOR):**
- 📋 Список менти — просмотр всех менти с ФИО, датой, статусом
- ➕ Добавить менти — FSM: ФИО → @username → дата (ДД.ММ.ГГ)
- Управление: изменение статуса (active/completed/paused/dropped), удаление

**Для Пользователей:**
- Проверка наличия назначенного ментора
- Если есть — показывает контакты ментора
- Если нет — сообщение "Тебе пока не назначен бадди"

**Таблицы БД:**
- `buddy_mentorships` (id, mentor_id, mentee_id, mentee_full_name, mentee_telegram_tag, status, assigned_date, created_at)
- `user_roles` — для связи mentor_id с пользователем

### Мультироли
**Поддержка нескольких ролей:** Пользователь может иметь несколько ролей одновременно.

**Формат хранения:** В БД роли хранятся как `admin,lion,mentor` (через запятую).

**Функции:**
- `get_user_roles()` — получить список всех ролей
- `has_role(user_id, role)` — проверить наличие роли
- `add_user_role()` — добавить роль пользователю
- `remove_user_role()` — удалить роль у пользователя

**Примеры комбинаций:**
- `admin,lion` — админ с правами Льва
- `admin,mentor` — админ + ментор
- `lion,mentor` — Лев + ментор
- `user` — только пользователь (может быть менти)

**HasRole фильтр:** Теперь проверяет наличие роли в списке, а не точное совпадение.

### Права доступа
- Используется фильтр `HasRole(ROLE_ADMIN)` из `db_utils.py`
- `IsAuthorizedUser` есть в коде, но **не используется** в handlers
- Основная проверка — `AuthMiddleware` (проверяет всех кроме /start)

### БД и миграции
- `db_utils.py` содержит `_migrate_user_roles()` и `_migrate_materials()` для совместимости со старыми схемами
- Все функции БД — **async**, требуют `await`
- Rate limiting (utils.py) и bans (db_utils.py) — разные системы

### Чего НЕТ в проекте
- ❌ Юнит-тестов — проверка только вручную через бота
- ❌ `admin_module.py` — был разделён на handlers/*.py
- ❌ `IsAdmin`, `IsMentor` фильтров — используется только `HasRole(ROLE_...)`

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
- `python -m py_compile file.py` — синтаксис корректен?
- Нет ли дублирующихся определений?

---

## 🛠️ Типичные задачи

### Добавить новый FSM-диалог
1. Добавить StatesGroup в соответствующий модуль handlers/
2. Добавить хендлеры с фильтрами состояний
3. Использовать `HasRole(ROLE_ADMIN)` если нужна защита
4. Кнопка "Назад" уже работает (сбросит в главное меню)

### Добавить новую команду
1. Выбрать подходящий модуль handlers/
2. Добавить `@router.message(Command("..."))` или `@router.message(F.text == "...")`
3. Добавить rate limit: `check_rate_limit(message.from_user.id)`
4. Если нужна клавиатура — использовать `kb()` или `inline_kb()` из utils

### Изменить БД
1. Новые таблицы — в `Database.init_tables()`
2. Миграции существующих — в отдельный метод `_migrate_*()`
3. CRUD-функции — async, принимают/возвращают dict

---

## 🗄️ Схема БД

```sql
user_roles(id, user_id UNIQUE, username UNIQUE, role, created_at)
materials(id, stage, title, link, description, created_at)
events(id, event_type, event_datetime, link, announcement, created_at)
bans(id, user_id, username, ban_level, banned_until, created_at)
failed_attempts(id, user_id UNIQUE, username UNIQUE, attempt_count, last_attempt)
```
