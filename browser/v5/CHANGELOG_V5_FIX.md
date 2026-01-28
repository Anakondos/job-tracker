# V5 Engine Fix - Changelog

## Session: 2025-01-27

### Проблемы выявленные при ручном тестировании:

1. **_fill_autocomplete использует глобальные селекторы** 
   - Было: `.select__menu`, `.select__option` (глобально)
   - Проблема: на странице несколько dropdowns, кликаем не тот
   - Решение: использовать `aria-controls` для нахождения ПРАВИЛЬНОГО listbox

2. **Нет Escape перед кликом на dropdown**
   - Проблема: конфликты с уже открытыми dropdowns
   - Решение: `page.keyboard.press('Escape')` перед каждым dropdown

3. **Нет scroll_into_view_if_needed в _fill_autocomplete**
   - Проблема: элемент может быть за пределами viewport
   - Решение: добавить scroll перед click

4. **Location autocomplete требует ждать API**
   - Проблема: опции появляются только после API response
   - Решение: time.sleep(2) после ввода текста для Location

5. **Enter может отправить форму**
   - Проблема: Enter в Location может submit form
   - Решение: использовать Tab вместо Enter для fallback

---

## Изменения:

### FIX 1: _fill_autocomplete - aria-controls метод ✅
- Переписана функция полностью
- Добавлен aria-controls для поиска правильного listbox
- Добавлен Escape перед click
- Добавлен scroll_into_view_if_needed
- Добавлена проверка is_location для увеличенного wait time (2 сек)
- Заменён Enter на Tab в fallback

### FIX 2: fill_repeatable_entry - aria-controls для combobox ✅
- Добавлен aria-controls метод
- Добавлен Escape перед click
- Добавлен scroll_into_view_if_needed
- Исправлен fallback для "0 - Other" (школы)
- Заменён Enter на Tab

### NEXT: Тестирование на форме 7402016
