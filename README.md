# shredder-vpn-bot

## Feedback campaigns

Админ-команды для рассылки feedback-опросов пользователям, которые использовали trial,
подключали подписку, пользовались ей и не оплатили тариф.

### Тестовая рассылка

```text
/feedback_test <telegram_id> <buttons|text> <month|sixmonths|year[,year...]> [min_chars]
```

Отправляет feedback-опрос одному пользователю для теста. Можно запускать много раз
подряд одному и тому же пользователю.

Примеры:

```text
/feedback_test 123456789 buttons month
/feedback_test 123456789 buttons month,year
/feedback_test 123456789 text month,year 60
```

### Продовая рассылка

```text
/feedback_send <count> <buttons|text> <month|sixmonths|year[,year...]> [min_chars]
```

Создает продовую рассылку. Перед отправкой бот покажет админу список TG ID,
которые попадут в рассылку, и попросит подтверждение.

Один и тот же пользователь не попадет в продовую feedback-рассылку повторно,
пока его старая отправка не будет очищена через `/feedback_cleanup`.

Примеры:

```text
/feedback_send 10 buttons month
/feedback_send 50 buttons month,year
/feedback_send 100 text month,sixmonths,year 60
```

### Список продовых запусков

```text
/feedback_runs [limit]
```

Показывает последние продовые feedback-runs. Тестовые запуски не выводятся.
В списке есть `run_id`, тип опроса, статус, аудитория, количество отправленных,
ответивших и купивших пользователей.

Примеры:

```text
/feedback_runs
/feedback_runs 10
/feedback_runs 20
```

### Результаты запуска

```text
/feedback_results <run_id>
```

Показывает результаты конкретного feedback-run.

Для `buttons` выводит статистику по вариантам ответа и скидкам:

```text
Ответы:
Не устроила цена: 4
Не разобрался с подключением: 2

Скидки:
month: выбрали 3, оплатили 1
year: выбрали 2, оплатили 0
```

Для `text` выводит текстовые ответы постранично в одном сообщении. Кнопки
`Назад` и `Далее` редактируют это же сообщение, не создавая новые.

Пример:

```text
/feedback_results 42
```

### Короткий статус запуска

```text
/feedback_status <run_id>
```

Показывает короткий статус запуска: отправлено, ответили, наград выдано, ошибок.

Пример:

```text
/feedback_status 42
```

### Отмена запуска

```text
/feedback_cancel <run_id>
```

Помечает запуск как `cancelled`.

Пример:

```text
/feedback_cancel 42
```

### Очистка старых получателей

```text
/feedback_cleanup <older_than_days>
```

Очищает старых получателей продовых feedback-рассылок, чтобы они снова могли
попасть в новую рассылку.

Примеры:

```text
/feedback_cleanup 30
/feedback_cleanup 90
/feedback_cleanup 180
```

### Аргументы

`survey_type`:

```text
buttons
text
```

`rewards`:

```text
month
sixmonths
year
month,year
month,sixmonths,year
```

`min_chars`:

```text
Только для text. Для buttons не указывать.
```
