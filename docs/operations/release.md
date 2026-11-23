# Выпуск Company Brain

Выкладку выполняет принимающий после выполнения критериев O91/O92. Для сборки нужны Git, доверенный репозиторий и полный 40-символьный SHA кандидата. Для приведённых команд используйте Python 3.11 или новее. Для проверки Code Mode используйте macOS с доступным системным песочником.

## Сборка и проверка кандидата

Укажите абсолютный путь к репозиторию и полный SHA из записи приёмки. Начните в новом временном каталоге. Используйте только синтетические данные и учётные записи.

```sh
repo='/absolute/path/to/company-brain'
sha='REPLACE_WITH_FULL_COMMIT_SHA'
work=$(mktemp -d)
cd "$work" && mkdir artifacts &&
  git --no-replace-objects -C "$repo" show "$sha:release/bundle.py" > bundle.py &&
  python3 -B - "$repo" "$sha" <<'PY'
import sys
from pathlib import Path
from bundle import build_bundle, materialize, verify_bundle

repo, revision = sys.argv[1:]
build_bundle(repo, revision, Path('artifacts'))
archive, = Path('artifacts').glob('*.zip')
manifest = Path('artifacts/manifest.json')
verified = verify_bundle(archive, manifest)
if verified['revision'] != revision.lower():
    raise SystemExit('Release revision mismatch')
materialize(archive, manifest, Path('candidate'))
print(verified['revision'], verified['archive_sha256'], archive)
PY
```

Сохраните ZIP и `manifest.json` вместе. Запишите SHA коммита и SHA-256 ZIP из вывода в свидетельство приёмки. При переносе сверяйте оба значения с этой записью.

Из `$work/candidate` выполните [настройку локального профиля](deploy.md) и запустите `python3 -B -m deploy.app --config "$profile/config.json"`. Профиль создайте отдельно от релиза. Укажите `127.0.0.1` или `::1` в `bind`, оставьте тестовые порты равными нулю и возьмите фактические `knowledge_url`, `review_url` и `pid` из `$profile/data/ready.json`.

При старте приложение открывает SQLite. Проверьте схему `$profile/data/knowledge.db` по `brain/storage/migrations/001.sql` и `brain/storage/migrations/002.sql` из кандидата. Выполните `PRAGMA integrity_check` для `knowledge.db` и `audit.db`, ожидаемый результат `ok`. По `knowledge_url` выполните `POST /session` с синтетическим паролем. По `review_url` войдите на `/login` и откройте очередь оператора. Запишите результаты с SHA кандидата, без пароля и токена.

## Выкладка и репетиция отката

Репетируйте эти действия на синтетическом профиле. До переключения сохраните предыдущий проверенный ZIP, его `manifest.json`, SHA коммита и SHA-256 архива.

1. Остановите предыдущий экземпляр через `Ctrl+C` либо через `kill -TERM` с PID из `ready.json`. Дождитесь завершения процесса и удаления `ready.json`.
2. Скопируйте весь остановленный профиль в новый защищённый каталог резервной копии, сохранив права файлов. В копию входят конфигурация, файл учётных данных и каталог `data` с базами, сопутствующими файлами SQLite и очередью. Если конфигурация ссылается на файлы вне профиля, сохраните их вместе с копией. Держите резервную копию вне релиза и каталога артефактов.
3. Сделайте отдельную рабочую копию профиля из бэкапа. Разверните проверенный кандидат в новый каталог, запустите его с рабочей копией и повторите проверку миграций, входа и очереди. Зафиксируйте результат по SHA кандидата.
4. Для отката остановите кандидат тем же способом. В каталоге `$work` запустите проверку предыдущей пары файлов. Укажите ожидаемые SHA коммита и SHA-256 архива из записи приёмки.

```sh
cd "$work" && python3 -B - "$previous_zip" "$previous_manifest" "$previous_sha" "$previous_digest" <<'PY'
import sys
from bundle import materialize, verify_bundle

archive, manifest, revision, digest = sys.argv[1:]
verified = verify_bundle(archive, manifest)
if (verified['revision'], verified['archive_sha256']) != (revision.lower(), digest.lower()):
    raise SystemExit('Previous release mismatch')
materialize(archive, manifest, 'rollback')
PY
```

Восстановите резервную копию профиля в новый каталог. Из `$work/rollback` запустите предыдущую версию с восстановленной конфигурацией. Подтвердите вход оператора, доступность очереди и наличие контрольной записи из состояния до выкладки. Зафиксируйте результат отката и SHA предыдущего релиза.

## Известные ограничения

- Локальный профиль обслуживает только loopback. Code Mode зависит от macOS.
- ZIP содержит закреплённые файлы релиза. Состояние SQLite, учётные данные и журналы обслуживаются отдельно. Откат возвращает состояние на момент бэкапа.
- Контрольные суммы проверяют целостность. Для подтверждения происхождения сверяйте SHA коммита и SHA-256 ZIP с доверенной записью приёмки.
