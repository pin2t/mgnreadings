# Передача показаний счётчиков на mgn-city.ru

Скрипты отправляют показания на сайте mgn-city.ru так же, как это делается вручную на странице
«Передача показаний приборов учета»: ввод лицевого счёта → «Проверить» → «Новое показание» у
нужных счётчиков → «Отправить показания».

| Скрипт | Что передаёт | Страница на сайте |
|---|---|---|
| `wmpost.py` (water meter post) | показания счётчиков воды | [system=SN](https://mgn-city.ru/TransferReadings/Account?system=SN) |
| `elpost.py` (electricity post) | показания счётчика электроэнергии, ООО «МЭК» | [system=PS, МЭК](https://mgn-city.ru/TransferReadings/Account?system=PS&serviceCode=147&providerId=5&groupId=15) |
| `gaspost.py` (gas post) | показания счётчика газа, ООО «НОВАТЭК-Челябинск» | [system=PS, НОВАТЭК](https://mgn-city.ru/TransferReadings/Account?system=PS&serviceCode=4&providerId=6&groupId=1) |

Общий код всех скриптов лежит в `mgncity.py`. Этот файл должен находиться в той же папке.
Нужен только Python 3.9+ (сторонние библиотеки не используются).

## Запуск вручную

```sh
./wmpost.py -account 123456789 -meter "25-1111222=42" -meter "25-2222333=55"
./elpost.py -account 123456789 -meter 011111111111110000=17650
./gaspost.py -account 12345678 -meter 15
```

Параметры у всех скриптов одинаковые:

| Параметр | Описание |
|---|---|
| `-account СЧЁТ` | номер лицевого счёта |
| `-meter НОМЕР=ЗНАЧЕНИЕ` | `wmpost.py`, `elpost.py`: номер счётчика (как на сайте, без «№») и новое показание; повторяется для каждого счётчика |
| `-meter ЗНАЧЕНИЕ` | `gaspost.py`: только новое показание. У счётчика газа на сайте нет номера, значение вписывается в первое поле «Новое показание» |
| `--dry-run` | только найти счётчики и показать, что будет отправлено, ничего не отправляя |
| `--force` | отправить, даже если не прошла проверка (см. ниже) |

- Перед отправкой скрипт проверяет, что новое показание не меньше последнего принятого. Для
  воды он также проверяет, что расход не превышает лимит сайта (50 м³). Если проверка не
  прошла, ничего не отправляется, пока не указан `--force`.
- Счётчик, которого нет на этом лицевом счёте, пропускается с сообщением `not found on this account, skipped`; остальные показания отправляются.
- Показание: число ≥ 0, не больше 3 знаков после запятой (`46`, `46.5`, `46,5`).
- Код выхода: `0` — всё отправлено, `1` — ошибка (сайт отклонил показание, неверный счёт, нет сети и т. п.), `2` — неверные параметры.

## Home Assistant: автоматическая отправка 15-го числа каждого месяца

Показания берутся из сенсоров:

| Сенсор Home Assistant | Скрипт | Счётчик на сайте |
|---|---|---|
| `sensor.waterius_12345678_ch0` | `wmpost.py` | № 25-1111122 (ГВС) |
| `sensor.waterius_12345678_ch1` | `wmpost.py` | № 25-2222233 (ХВС) |
| `sensor.electricity_meter` — **замените на свой** | `elpost.py` | № 01111111111100000 |
| `sensor.gas_meter` — **замените на свой** | `gaspost.py` | «Газ» (первый счётчик на счёте) |

Сенсоры электроэнергии и газа должны показывать **полное показание счётчика**, как на его
табло (кВт·ч, м³), а не расход за день или месяц. Если сенсор электроэнергии считает в Вт·ч,
поделите значение на 1000 в шаблоне (`el | float / 1000`).

Требуется Home Assistant 2024.10 или новее (OS, Supervised или Container). Python в нём уже есть,
ничего устанавливать не нужно.

**О формате примеров.** Все примеры ниже записаны в JSON. JSON является подмножеством YAML,
поэтому Home Assistant читает их везде, где ожидается YAML: в `configuration.yaml`, в редакторе
автоматизаций и в «Панели разработчика». Отступы в JSON не важны, но:

- строки только в двойных кавычках и только в одну строку; перенос строки внутри текста — `\n`;
- элементы разделяются запятыми, после последнего элемента в `{ }` и `[ ]` запятой нет;
- комментариев нет;
- после двоеточия ставьте пробел (`"ключ": "значение"`) — так требует YAML.

### 1. Скопировать скрипты

Положите `mgncity.py`, `wmpost.py`, `elpost.py` и `gaspost.py` в папку `/config/mgnreadings/`
(в Samba-шаре это `config/mgnreadings`) любым удобным способом: аддон Samba share, File editor,
Studio Code Server или Terminal & SSH. Права на выполнение не нужны: скрипты запускаются через
`python3`.

### 2. Добавить команды в `configuration.yaml`

Добавьте в `configuration.yaml` отдельным блоком, начиная с первой колонки (строки команд
длинные, переносить их нельзя):

```json
shell_command: {
  "wmpost": "python3 /config/mgnreadings/wmpost.py -account 12345678 {% set ch0 = states('sensor.waterius_12345678_ch0') %}{% set ch1 = states('sensor.waterius_12345678_ch1') %}{% if ch0 | is_number %} -meter 25-123456={{ ch0 | float | round(0, 'floor') | int }}{% endif %}{% if ch1 | is_number %} -meter 25-123456={{ ch1 | float | round(0, 'floor') | int }}{% endif %}{% if dry_run is defined and dry_run %} --dry-run{% endif %}",
  "elpost": "python3 /config/mgnreadings/elpost.py -account 123456789 {% set el = states('sensor.electricity_meter') %}{% if el | is_number %} -meter 011111111111000={{ el | float | round(0, 'floor') | int }}{% endif %}{% if dry_run is defined and dry_run %} --dry-run{% endif %}",
  "gaspost": "python3 /config/mgnreadings/gaspost.py -account 1234567 {% set gas = states('sensor.gas_meter') %}{% if gas | is_number %} -meter {{ gas | float | round(0, 'floor') | int }}{% endif %}{% if dry_run is defined and dry_run %} --dry-run{% endif %}"
}
```

`configuration.yaml` остаётся YAML-файлом, поэтому ключ `shell_command:` пишется как в YAML, а
его значение — в JSON. Если `shell_command:` в файле уже есть, добавьте строки `"wmpost": "…"`,
`"elpost": "…"` и `"gaspost": "…"` в существующий блок, второй `shell_command:` не создавайте.

Что делают команды:

- Значения сенсоров округляются **вниз до целых** м³ и кВт·ч (`46.237` → `46`), как обычно и
  передают показания. Чтобы отправлять с дробной частью (сайт принимает до 3 знаков), замените
  `round(0, 'floor') | int` на `round(3, 'floor')`.
- Если сенсор недоступен (`unavailable` / `unknown`), этот счётчик не передаётся. Остальные
  счётчики отправляются как обычно.
- Переменная `dry_run` позволяет проверить команду без отправки (см. шаг 3).
- Команды с шаблонами Home Assistant запускает без оболочки, поэтому `~`, `|`, `>` в них не
  работают; здесь они и не нужны. Время выполнения ограничено 60 секундами, а скрипт обычно
  укладывается в 2–5 секунд.

После правки проверьте конфигурацию («Панель разработчика» → «YAML» → «Проверить конфигурацию»)
и **перезапустите Home Assistant**: `shell_command` применяется только после перезапуска.

### 3. Проверить без отправки

1. «Панель разработчика» → «Шаблон»: вставьте текст команды из шага 2 (то, что между кавычками
   после `"wmpost":`, `"elpost":` или `"gaspost":`) и убедитесь, что получилась строка вида
   `python3 /config/mgnreadings/gaspost.py -account 1234567 -meter 15`.
2. «Панель разработчика» → «Действия» → «Перейти в режим YAML», вставьте и выполните по очереди:

   ```json
   {"action": "shell_command.wmpost", "data": {"dry_run": true}}
   ```

   ```json
   {"action": "shell_command.elpost", "data": {"dry_run": true}}
   ```

   ```json
   {"action": "shell_command.gaspost", "data": {"dry_run": true}}
   ```

   В ответе должны быть `returncode: 0` и в `stdout` таблица счётчиков с последними и новыми
   показаниями и строка `Dry run, not submitting`. На сайт при этом ничего не отправляется.

### 4. Создать автоматизацию

«Настройки» → «Автоматизации и сцены» → «Создать автоматизацию» → «Создать новую автоматизацию» →
меню ⋮ → «Редактировать в YAML», замените всё содержимое редактора на текст ниже и сохраните:

```json
{
  "alias": "Показания счётчиков на mgn-city.ru",
  "description": "15-го числа каждого месяца отправляет показания воды, электроэнергии и газа на mgn-city.ru",
  "triggers": [
    {"trigger": "time", "at": "10:00:00"}
  ],
  "conditions": [
    {"condition": "template", "value_template": "{{ now().day == 15 }}"}
  ],
  "actions": [
    {
      "action": "shell_command.wmpost",
      "response_variable": "water",
      "continue_on_error": true
    },
    {
      "action": "persistent_notification.create",
      "data": {
        "title": "{{ 'Показания воды отправлены' if water is defined and water.returncode == 0 else 'Ошибка передачи показаний воды' }}",
        "message": "{% if water is defined %}```\n{{ water.stdout }}\n{{ water.stderr }}\n```{% else %}Команда не выполнилась (возможно, истекли 60 секунд). Подробности в журнале Home Assistant.{% endif %}"
      }
    },
    {
      "action": "shell_command.elpost",
      "response_variable": "electricity",
      "continue_on_error": true
    },
    {
      "action": "persistent_notification.create",
      "data": {
        "title": "{{ 'Показания электроэнергии отправлены' if electricity is defined and electricity.returncode == 0 else 'Ошибка передачи показаний электроэнергии' }}",
        "message": "{% if electricity is defined %}```\n{{ electricity.stdout }}\n{{ electricity.stderr }}\n```{% else %}Команда не выполнилась (возможно, истекли 60 секунд). Подробности в журнале Home Assistant.{% endif %}"
      }
    },
    {
      "action": "shell_command.gaspost",
      "response_variable": "gas",
      "continue_on_error": true
    },
    {
      "action": "persistent_notification.create",
      "data": {
        "title": "{{ 'Показания газа отправлены' if gas is defined and gas.returncode == 0 else 'Ошибка передачи показаний газа' }}",
        "message": "{% if gas is defined %}```\n{{ gas.stdout }}\n{{ gas.stderr }}\n```{% else %}Команда не выполнилась (возможно, истекли 60 секунд). Подробности в журнале Home Assistant.{% endif %}"
      }
    }
  ],
  "mode": "single"
}
```

После сохранения Home Assistant сам переведёт автоматизацию в обычный YAML; так и должно быть.

Автоматизация срабатывает каждый день в 10:00 (по часовому поясу Home Assistant), но выполняется
только 15-го числа. Показания отправляются по очереди: вода, электроэнергия, газ. По каждому
скрипту приходит отдельное уведомление: вывод скрипта с показаниями и ответом сайта. Если
показания какого-то поставщика нужно передавать в другой день, перенесите его пару действий
(команда и уведомление) в отдельную автоматизацию с другим числом в условии.

Чтобы получать результат ещё и на телефон, добавьте в массив `"actions"` ещё один элемент (не
забудьте запятую после предыдущего `}` и подставьте имя своего устройства из приложения
Home Assistant):

```json
{
  "action": "notify.mobile_app_my_phone",
  "data": {
    "title": "Показания счётчиков",
    "message": "{% if water is defined %}{{ water.stdout }}\n{{ water.stderr }}{% else %}Вода: команда не выполнилась{% endif %}\n\n{% if electricity is defined %}{{ electricity.stdout }}\n{{ electricity.stderr }}{% else %}Электроэнергия: команда не выполнилась{% endif %}\n\n{% if gas is defined %}{{ gas.stdout }}\n{{ gas.stderr }}{% else %}Газ: команда не выполнилась{% endif %}"
  }
}
```

> **Внимание:** «Выполнить действия» в меню автоматизации пропускает условие «15-е число» и
> **отправляет показания на сайт сразу**. Для проверки используйте `dry_run` из шага 3.

Если 15-го числа Home Assistant был выключен, запуск пропускается. Отправьте показания вручную
через «Панель разработчика» → «Действия»: `{"action": "shell_command.wmpost"}`,
`{"action": "shell_command.elpost"}` и `{"action": "shell_command.gaspost"}`.

### Если пришло уведомление об ошибке

| Сообщение | Что делать |
|---|---|
| `new reading … is lower than last reading …` | показание сенсора меньше принятого сайтом: сверьте сенсор (Waterius, счётчики электроэнергии и газа) с самим счётчиком и поправьте значение в настройках сенсора. Скрипт такие показания не отправляет |
| `consumption … exceeds the site's limit of 50` | (вода) расход за период больше 50 м³: сайт такое не принимает. Проверьте показания |
| `account …: По указанным данным информация не найдена` или `account …: Лицевой счет не найден` | неверный номер лицевого счёта в `-account` |
| `not found on this account, skipped` | на счёте нет счётчика с таким номером: проверьте номер на сайте |
| `the following arguments are required: -meter` (код 2) | все сенсоры этого скрипта недоступны |
| `network error …` / `HTTP 5xx …` | сайт или сеть недоступны: повторите позже вручную |

Подробности ошибок запуска команды есть в «Настройки» → «Система» → «Журналы».
