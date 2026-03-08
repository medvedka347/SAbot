# E2E Тест-репорт: SABot

**Дата:** 2026-03-07  
**Версия бота:** main@c71af90  
**QA Engineer:** AI Agent

---

## 1. Обновления документации

### AGENTS.md
- Обновлен раздел "Кнопка Назад" - теперь отражает реальное поведение с STATE_MAP
- Добавлен список поддерживаемых переходов назад

### TEST_CASES.md  
- Добавлены детальные тест-кейсы навигации (TC-NAV-001...009)
- Исправлена нумерация тестов
- Учтены реальные пути навигации из кода

---

## 2. Анализ поведения кнопки "Назад"

### Реальная логика (из common.py)
```python
if prev_state_key and prev_state_key in STATE_MAP:
    # Возврат на предыдущий шаг
    prev_state = STATE_MAP[prev_state_key]()
    await state.set_state(prev_state)
else:
    # В главное меню
    await state.clear()
```

### STATE_MAP содержит:
| Ключ | Работает? | Примечание |
|------|-----------|------------|
| selecting_stage | ✅ | Есть в _prev_state |
| selecting_stage_public | ❌ | НЕ сохраняется в _prev_state |
| input_title | ✅ | Есть в _prev_state |
| input_link | ✅ | Есть в _prev_state |
| input_desc | ❌ | НЕ сохраняется в _prev_state |
| selecting_item | ✅ | Есть в _prev_state |
| editing | ❌ | НЕ сохраняется в _prev_state |
| input_type | ✅ | Есть в _prev_state |
| input_datetime | ✅ | Есть в _prev_state |
| input_link_evt | ✅ | Есть в _prev_state |
| input_announcement | ✅ | Есть в _prev_state |
| confirm_announce | ✅ | Есть в _prev_state |
| input_users | ✅ | Есть в _prev_state |
| selecting_role | ❌ | НЕ сохраняется в _prev_state |
| selecting_user_to_delete | ❌ | НЕ сохраняется в _prev_state |

### Работающие цепочки навигации:

**Materials:**
- selecting_stage → input_title → input_link (input_desc нет в STATE_MAP)
- selecting_item → editing (editing нет в _prev_state!)

**Events:**
- input_type → input_datetime → input_link_evt → input_announcement → confirm_announce

**Roles:**
- menu → input_users (selecting_role нет в _prev_state!)

---

## 3. Найденные проблемы

### 🔴 Баг 1: editing в materials не имеет _prev_state
**Файл:** handlers/materials.py:250  
**Код:** `await state.update_data(edit_id=mat_id, edit_item=mat, _prev_state="selecting_item")`  
**Проблема:** editing сохраняет _prev_state="selecting_item", но selecting_item сохраняет _prev_state="selecting_stage"  
**Результат:** При нажатии "Назад" из editing возвращаемся к selecting_item, но там уже нет _prev_state  
**Ожидаемо:** Двойное нажатие "Назад" должно вернуть к selecting_stage

### 🔴 Баг 2: selecting_role не сохраняет _prev_state
**Файл:** handlers/roles.py:251  
**Код:** `await state.update_data(users_to_assign=users, _prev_state="input_users")`  
**Проблема:** selecting_role есть в STATE_MAP, но не сохраняется при переходе  
**Результат:** Из selecting_role "Назад" всегда в главное меню

### 🟡 Баг 3: selecting_stage_public не имеет истории
**Файл:** handlers/materials.py:352-369  
**Проблема:** Публичный просмотр материалов не сохраняет _prev_state  
**Результат:** Всегда возврат в главное меню (ожидаемо по дизайну)

---

## 4. Рекомендуемые исправления

### Для исправления Bug 1 (editing):
```python
# В materials.py при переходе в editing
await state.update_data(
    edit_id=mat_id, 
    edit_item=mat, 
    _prev_state="selecting_item",
    _prev_state_chain=["selecting_stage"]  # Добавить цепочку
)
```

### Для исправления Bug 2 (selecting_role):
```python
# В roles.py после выбора роли
await state.update_data(
    users_to_assign=users, 
    _prev_state="input_users"
)
# А при показе selecting_role нужно добавить:
await state.update_data(_prev_state="selecting_role")  # Добавить это
```

### Альтернатива - упростить до AGENTS.md:
Если сложная навигация не нужна, упростить back_handler до:
```python
@router.message(F.text.in_(["🔙 Назад", "Назад"]))
async def back_handler(message: Message, state: FSMContext):
    await state.clear()
    # ... показать главное меню
```

---

## 5. Тесты для ручного прогона

### TC-NAV-001: Materials - переход назад (ОЖИДАЕТСЯ: ✅)
1. 📦 Управление материалами
2. ➕ Добавить
3. Выбрать раздел
4. Ввести название "Test"
5. 🔙 Назад
**Ожидаемо:** "🔙 Введите название:"

### TC-NAV-002: Materials - двойной переход (ОЖИДАЕТСЯ: ⚠️)
1. Продолжить после TC-NAV-001
2. Ввести название снова
3. Ввести ссылку "https://test.com"
4. 🔙 Назад
5. 🔙 Назад снова
**Ожидаемо:** "🔙 Введите название:" → "🔙 Выберите раздел:"
**Фактически:** Может уйти в главное меню на 2-м шаге

### TC-NAV-003: Events - цепочка (ОЖИДАЕТСЯ: ✅)
1. 📋 Управление событиями
2. ➕ Добавить
3. Ввести тип "Test"
4. Ввести дату "2025-12-31 18:00:00"
5. 🔙 Назад
**Ожидаемо:** "🔙 Введите дату..."

### TC-NAV-004: Roles - selecting_role (ОЖИДАЕТСЯ: ❌)
1. 👥 Управление ролями
2. ➕ Назначить роль
3. Ввести "@testuser"
4. (показывается выбор роли)
5. 🔙 Назад
**Ожидаемо:** "🔙 Введите пользователей..."
**Фактически:** Главное меню (баг 2)

---

## 6. Вывод

**Статус:** Требуется ручной прогон для подтверждения багов

**Критичность:**
- Баг 1: Medium (неконсистентное поведение)
- Баг 2: Medium (неконсистентное поведение)
- Баг 3: Low (по дизайну)

**Рекомендация:** 
1. Провести ручной прогон TC-NAV-001...004
2. Если баги подтвердятся - решить: исправить или упростить до "всегда в главное меню"
3. Обновить AGENTS.md финальным решением
