# Дежурство Company Brain

## Разбор сбоя

1. Возьмите `failure.trace_id` из сводки. `failure.step` указывает шаг сбоя, `reason` содержит код `failed` или `policy_denied`, а `last_success` показывает ближайший предшествующий успешный шаг той же трассы. Значение `null` означает, что до сбоя в трассе нет успешного шага.
2. Откройте `data/audit.db` активного профиля в режиме чтения. Выполните запрос с `:trace_id`, привязанным к значению из сводки. Читайте события по `seq`. Если после сбоя есть `ok`, он относится к более поздней попытке.

   ```sql
   SELECT seq, step, outcome, timestamp, details
   FROM audit_events
   WHERE trace_id = :trace_id
   ORDER BY seq;
   ```

3. Сопоставьте `count` и `total_ms` по шагам со счётчиками `returns` и `policy_denials`. При `policy_denied` проверьте роль оператора и проект операции. При росте `queue_lag_seconds` проверьте, продвигается ли очередь. Сопоставьте `used_bytes` со свободным местом на файловой системе профиля, а `versions` с версиями запущенного релиза.

## Остановка и восстановление

1. Остановите процесс через `Ctrl+C` в терминале запуска или отправьте `kill -TERM` процессу с `pid` из `data/ready.json`. Дождитесь удаления `data/ready.json`.
2. Устраните причину сбоя и запустите сервис из корня релиза с прежним `config.json`. Используйте тот же каталог данных, чтобы сохранить очередь, решения и журнал. Команда запуска и настройки профиля описаны в инструкции развёртывания. [Локальный профиль Company Brain](deploy.md)
3. Дождитесь нового `data/ready.json`. Войдите по адресу `<review_url>/login`, проверьте доступ к очереди своего проекта и повторите контрольную операцию с новым `trace_id`. Убедитесь, что проблемный шаг завершился с `ok`, а в новой трассе нет `failed` и `policy_denied`. При наличии ожидающей работы проверьте, что очередь снова продвигается.

## Синтетическая инъекция отказа

Запустите из корня релиза. Пример записывает отказ во временный журнал и выводит сведения для разбора трассы.

```sh
python3 -B - <<'PY'
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from brain.audit import AuditLog
from brain.operations.telemetry import summarize

with TemporaryDirectory() as directory:
    audit = AuditLog(Path(directory) / "audit.db")
    try:
        audit.append(trace_id="demo", step="load", outcome="ok")
        audit.append(trace_id="demo", step="persist", outcome="failed")
        audit.append(trace_id="other", step="review", outcome="ok")
        summary = summarize(
            audit.read_after(),
            now="2026-01-01T00:00:00+00:00",
            versions={},
            used_bytes=0,
        )
        print(json.dumps(summary["failure"]))
    finally:
        audit.close()
PY
```

Ожидаемая запись для трассы `demo`.

```json
{"trace_id": "demo", "step": "persist", "reason": "failed", "last_success": "load"}
```

В рабочем журнале такой результат направляет диагностику на `persist`. После устранения причины примените процедуру восстановления и подтвердите `ok` на `persist` в новой трассе.
