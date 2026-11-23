# Память команды в Git

`KnowledgeStore(root)` хранит Markdown в пяти каталогах под заданным корнем. `ensure_layout()` создаёт их и возвращает словарь путей по именам областей.

| Область | Назначение |
| --- | --- |
| `active` | Действующие решения и инструкции |
| `archive` | Исторические RFC |
| `shared` | Общие материалы команды |
| `inbox` | Входящие черновики |
| `workspaces` | Рабочая песочница автора |

Новый материал поступает в `inbox`. Автор дорабатывает его в `workspaces`, а после согласования публикует в `active` или `shared`. При замене решения сохраните прежний RFC в `archive` и добавьте в актуальный документ относительную ссылку на него.

## Воспроизводимый импорт

Запустите пример из корня репозитория. Он создаёт синтетический RFC и действующую запись со ссылкой на него, импортирует оба файла в `memory` и читает RFC из архива.

```sh
python3 - <<'PY'
from pathlib import Path
from tempfile import TemporaryDirectory

from brain.git_store import KnowledgeStore

store = KnowledgeStore(Path("memory"))
store.ensure_layout()

with TemporaryDirectory() as directory:
    source = Path(directory)
    rfc = source / "0001-example.md"
    rfc.write_bytes(b"# RFC 0001\n\nReview interval: 30 days.\n")
    guide = source / "review.md"
    guide.write_bytes(
        b"# Review schedule\n\nReview interval: 14 days.\n\n"
        b"[Previous decision](../archive/0001-example.md)\n"
    )
    store.import_markdown(rfc, "archive")
    store.import_markdown(guide, "active")

print(store.read_markdown("archive", "0001-example.md"))
PY
```

Импорт сохраняет имя и исходные байты UTF-8. Повторный запуск с теми же байтами возвращает те же пути. Если файл с таким именем уже содержит другие байты, импорт вызывает `ValueError`.

## Фиксация дерева и поиск RFC

Добавьте дерево памяти в индекс, просмотрите изменения и зафиксируйте их.

```sh
git add -- memory/
git diff --cached -- memory/
git commit -m "Record team memory"
```

Git отслеживает файлы. После клонирования `ensure_layout()` восстановит пустые каталоги.

После запуска примера действующая запись в `memory/active/review.md` ссылается на [архивный RFC 0001](../memory/archive/0001-example.md).

Чтобы прочитать RFC из последнего коммита и найти более раннюю версию, используйте путь архива в Git. Для чтения выбранной версии подставьте хеш из `git log` вместо `HEAD`.

```sh
git show HEAD:memory/archive/0001-example.md
git log -- memory/archive/0001-example.md
```
