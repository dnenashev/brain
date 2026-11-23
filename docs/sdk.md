# Версионируемый SDK

`VersionedSDK` из `brain.sdk.api` закрепляет редакцию SDK и роль при открытии сессии. После публикации новой редакции открытые сессии продолжают использовать прежние обработчики. Новые сессии получают последнюю зарегистрированную редакцию.

## Регистрация

`register(revision, handlers)` публикует редакцию. Доверенный оператор передаёт отображение имён операций `read`, `search`, `check`, `draft`, `task` в вызываемые обработчики. SDK сохраняет собственную копию отображения. Изменение исходного отображения после регистрации не меняет опубликованную редакцию. Повторная регистрация того же идентификатора вызывает `ValueError`.

## Сессии и операции

`open_session(session_id, role)` открывает сессию и возвращает закреплённую редакцию. Повторное открытие с тем же идентификатором и ролью возвращает уже закреплённую редакцию. Запрос на повторное открытие с другой ролью отклоняется.

| Роль | Разрешённые операции |
| --- | --- |
| `reader` | `read`, `search`, `check` |
| `writer` | `read`, `search`, `check`, `draft` |
| `operator` | `read`, `search`, `check`, `draft`, `task` |

`describe(session_id)` возвращает словарь с ключами `revision` и `operations`. Список разрешённых операций отсортирован по имени. `call(session_id, operation, **parameters)` проверяет доступ, передаёт именованные параметры обработчику из закреплённой редакции и возвращает его результат. Недоступная операция вызывает `PermissionError`. `close_session(session_id)` отзывает доступ по идентификатору сессии.

## Пример

```python
from brain.sdk.api import VersionedSDK

sdk = VersionedSDK()


def empty_result(**parameters):
    return None


handlers = {
    "read": lambda: "sample v1",
    "search": empty_result,
    "check": empty_result,
    "draft": empty_result,
    "task": empty_result,
}
sdk.register("v1", handlers)
assert sdk.open_session("first", "reader") == "v1"

handlers["read"] = lambda: "sample v2"
sdk.register("v2", handlers)
assert sdk.open_session("second", "reader") == "v2"
assert sdk.open_session("first", "reader") == "v1"
assert sdk.describe("first") == {
    "revision": "v1",
    "operations": ["check", "read", "search"],
}
assert sdk.call("first", "read") == "sample v1"
assert sdk.call("second", "read") == "sample v2"
```
