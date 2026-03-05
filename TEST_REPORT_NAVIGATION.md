# 🧭 FSM Navigation Bug Report — SABot

> **Дата:** 2026-03-05  
> **QA Engineer:** AI Agent  
> **Тип тестирования:** Сквозное FSM с ожиданием навигации "Назад"  
> **Методология:** Тесты ожидают возврат в предыдущее состояние, фиксируем фактическое поведение

---

## 📊 Сводка

| Метрика | Значение |
|---------|----------|
| Тестов сценариев навигации | 7 |
| Ожидаемое поведение (возврат назад) | 0/7 (0%) |
| Фактическое поведение (сброс) | 7/7 (100%) |
| Потеря данных пользователя | 100% случаев |

---

## 🐛 Найденные баги навигации

### BUG-NAV-001: Полный сброс при нажатии "Назад" в диалоге создания материала
**Статус:** ⚠️ UX Bug  
**Приоритет:** Medium  
**Модуль:** `handlers/common.py` → `back_handler()`

#### Сценарий воспроизведения:
```
1. Пользователь: "📦 Управление материалами"
2. Пользователь: "➕ Добавить"
3. Пользователь: Выбирает раздел "📚 Фундаментальная теория"
4. Пользователь: Вводит название "REST API Guide"
5. Пользователь: Вводит ссылку "https://example.com"
6. Пользователь: Нажимает "🔙 Назад" (хочет исправить ссылку)
```

#### Ожидаемое поведение:
- Возврат в состояние `input_link`
- Сохранение данных: `{"stage": "fundamental", "title": "REST API Guide"}`
- Возможность ввести ссылку заново

#### Фактическое поведение:
- Полный сброс состояния (`state.clear()`)
- **Потеря всех введенных данных**
- Возврат в главное меню
- Пользователь должен начинать сначала

#### Почему это проблема:
- Пользователь теряет время на повторный ввод
- Негативный UX — неожиданное поведение
- Не соответствует паттерну "Назад" в других интерфейсах

---

### BUG-NAV-002: Нет сохранения истории состояний
**Статус:** ⚠️ Architecture Limitation  
**Приоритет:** Medium  
**Модуль:** Все FSM-диалоги

#### Проблема:
Текущая реализация FSM не сохраняет историю переходов. Каждый `set_state()` перезаписывает текущее состояние без возможности вернуться.

#### Воспроизведение во всех диалогах:
1. **Создание события:** `input_type → input_datetime → [🔙] → ❌ Сброс`
2. **Назначение роли:** `input_users → selecting_role → [🔙] → ❌ Сброс`
3. **Редактирование:** `editing → [🔙] → ❌ Сброс`

---

### BUG-NAV-003: Невозможность отмены неправильного выбора
**Статус:** ⚠️ UX Issue  
**Приоритет:** Low-Medium

#### Сценарий:
Пользователь выбрал не тот раздел в `selecting_stage`. Нажимает "Назад" ожидая вернуться к списку разделов, но попадает в главное меню.

---

## 📋 Детальный разбор тестов

| ID | Сценарий | Текущее состояние | Нажатие "Назад" | Ожидаемо | Фактически |
|----|----------|-------------------|-----------------|----------|------------|
| T1 | Создание материала | `input_title` | 🔙 | `selecting_stage` | `None` (сброс) |
| T2 | Создание материала | `input_link` | 🔙 | `input_title` | `None` (сброс) |
| T3 | Создание материала | `input_desc` | 🔙 | `input_link` | `None` (сброс) |
| T4 | Создание события | `input_datetime` | 🔙 | `input_type` | `None` (сброс) |
| T5 | Назначение роли | `selecting_role` | 🔙 | `input_users` | `None` (сброс) |
| T6 | Редактирование | `editing` | 🔙 | `selecting_item` | `None` (сброс) |
| T7 | Публичный просмотр | `selecting_stage_public` | 🔙 | `selecting_stage_public` | ✅ (только этот ок) |

---

## 💡 Варианты решения

### Вариант 1: Хранение предыдущего состояния (Рекомендуемый)

**Сложность:** Низкая  
**Время реализации:** 30 минут  
**Изменения:** Минимальные

#### Реализация:
```python
# В каждом state handler перед set_state сохраняем предыдущее состояние
@router.message(MaterialStates.input_title, HasRole(ROLE_ADMIN))
async def material_add_title(message: Message, state: FSMContext):
    if not message.text:
        return
    if len(message.text) > 200:
        await message.answer("❌ Название слишком длинное")
        return
    
    await state.update_data(title=message.text)
    
    # Сохраняем предыдущее состояние для навигации назад
    await state.update_data(_prev_state="input_title")
    
    await state.set_state(MaterialStates.input_link)
    await message.answer("Введите ссылку:", reply_markup=back_kb)
```

```python
# Улучшенный back_handler
@router.message(F.text.in_(["🔙 Назад", "Назад"]))
async def back_handler(message: Message, state: FSMContext):
    data = await state.get_data()
    prev_state = data.get("_prev_state")
    
    if prev_state:
        # Возвращаемся в предыдущее состояние
        state_map = {
            "input_title": MaterialStates.input_title,
            "input_link": MaterialStates.input_link,
            "selecting_stage": MaterialStates.selecting_stage,
            # ...
        }
        await state.set_state(state_map.get(prev_state))
        await message.answer("Вернулся на шаг назад. Продолжайте:", reply_markup=back_kb)
    else:
        # Нет истории — сбрасываем в главное меню
        await state.clear()
        role = await get_user_role(user_id=message.from_user.id)
        welcome = f"Привет! Роль: *{role}*"
        kb = await get_main_keyboard(message.from_user.id)
        await message.answer(welcome, parse_mode="Markdown", reply_markup=kb)
```

**Плюсы:**
- Простая реализация
- Нет изменений в архитектуре
- Пользователь не теряет данные

**Минусы:**
- Только один шаг назад (но это приемлемо)
- Нужно добавлять в каждый handler

---

### Вариант 2: Стек состояний (Stack-based)

**Сложность:** Средняя  
**Время реализации:** 2-3 часа  
**Изменения:** Требует middleware

#### Реализация:
```python
# Middleware для отслеживания истории
class StateHistoryMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        state = data.get("state")
        if state:
            current_state = await state.get_state()
            # Сохраняем в стек
            state_data = await state.get_data()
            history = state_data.get("_state_history", [])
            if current_state and (not history or history[-1] != current_state):
                history.append(current_state)
                await state.update_data(_state_history=history[-5:])  # Храним последние 5
        
        return await handler(event, data)

# Back handler использует стек
@router.message(F.text.in_(["🔙 Назад", "Назад"]))
async def back_handler(message: Message, state: FSMContext):
    data = await state.get_data()
    history = data.get("_state_history", [])
    
    if len(history) >= 2:
        history.pop()  # Убираем текущее
        prev_state = history.pop()  # Берем предыдущее
        await state.set_state(prev_state)
        await state.update_data(_state_history=history)
        await message.answer("Вернулся на шаг назад.", reply_markup=back_kb)
    else:
        # В главное меню
        await state.clear()
        ...
```

**Плюсы:**
- Множественные шаги назад
- Централизованная логика через middleware

**Минусы:**
- Сложнее в реализации
- Middleware увеличивает накладные расходы

---

### Вариант 3: Отдельная кнопка "Отмена" + "Назад" для диалогов

**Сложность:** Низкая  
**Время реализации:** 1 час

#### Концепция:
- "🔙 Назад" — возврат в главное меню (текущее поведение)
- "❌ Отмена" — отмена текущего шага, возврат на предыдущий

#### Реализация:
```python
# Добавляем кнопку отмены в промежуточные состояния
step_back_kb = kb([], "❌ Отмена шага")

@router.message(F.text == "❌ Отмена шага")
async def step_back_handler(message: Message, state: FSMContext):
    # Логика возврата на шаг назад
    ...
```

**Плюсы:**
- Ясность для пользователя
- Не ломает существующее поведение "Назад"

**Минусы:**
- Две кнопки вместо одной
- Не решает проблему ожиданий пользователя

---

### Вариант 4: Без изменений (Document as Feature)

**Решение:** Оставить как есть, но задокументировать.

```
"🔙 Назад" всегда возвращает в главное меню и отменяет текущую операцию.
Для изменения предыдущего ввода — начните процедуру заново.
```

**Плюсы:**
- Нет затрат на разработку
- Предсказуемо после прочтения документации

**Минусы:**
- Плохой UX
- Пользователи не читают документацию
- Негативные отзывы

---

## 🎯 Рекомендации

### Краткосрочно (быстрый фикс):
**Вариант 1** — добавить `_prev_state` в каждый handler. 
- Затраты: 30 минут
- Результат: Пользователь не теряет данные

### Долгосрочно:
**Вариант 2** — реализовать стек состояний через middleware.
- Затраты: 2-3 часа
- Результат: Полноценная навигация назад, лучший UX

### Не рекомендуется:
**Вариант 4** — оставить как есть. Это создаст негативный опыт пользователей.

---

## 📝 Пример реализации Варианта 1

```python
# handlers/materials.py

@router.message(MaterialStates.selecting_stage, HasRole(ROLE_ADMIN))
async def handle_stage_selection_admin(message: Message, state: FSMContext):
    """Обработка выбора stage в админке."""
    stage = get_stage_key(message.text)
    if not stage:
        return
    
    data = await state.get_data()
    action = data.get("action")
    
    if action == "add_material":
        await state.update_data(stage=stage, _prev_state="selecting_stage")
        await state.set_state(MaterialStates.input_title)
        await message.answer("Введите название:", reply_markup=back_kb)
        return
    
    # ... остальные действия


# handlers/common.py

@router.message(F.text.in_(["🔙 Назад", "Назад"]))
async def back_handler(message: Message, state: FSMContext):
    """Обработчик 'Назад' — возврат на предыдущий шаг или в главное меню."""
    data = await state.get_data()
    prev_state_key = data.get("_prev_state")
    
    if prev_state_key:
        # Возврат на предыдущий шаг
        state_map = {
            "selecting_stage": MaterialStates.selecting_stage,
            "input_title": MaterialStates.input_title,
            "input_link": MaterialStates.input_link,
            "input_desc": MaterialStates.input_desc,
            # ... другие состояния
        }
        
        prev_state = state_map.get(prev_state_key)
        if prev_state:
            await state.set_state(prev_state)
            # Убираем _prev_state, чтобы не зациклиться
            await state.update_data(_prev_state=None)
            await message.answer("Вернулся на шаг назад.", reply_markup=back_kb)
            return
    
    # Нет истории — в главное меню
    await state.clear()
    role = await get_user_role(user_id=message.from_user.id, username=message.from_user.username)
    welcome = f"Привет, {message.from_user.first_name}! 👋\n\nРоль: *{role}*"
    kb = await get_main_keyboard(message.from_user.id) if message.chat.type == "private" else None
    await message.answer(welcome, parse_mode="Markdown", reply_markup=kb)
```

---

## 📎 Приложения

- [test_fsm_navigation_expected.py](test_fsm_navigation_expected.py) — Тесты, показывающие ожидаемое поведение
- [TEST_REPORT.md](TEST_REPORT.md) — Основной QA-репорт
- [TEST_REPORT_FSM.md](TEST_REPORT_FSM.md) — FSM Integration Test Report

---

*Report generated by AI Agent*  
*Test methodology: Expected vs Actual behavior analysis*
