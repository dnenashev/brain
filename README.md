# Company Brain

**Company Brain** это автономный локальный сервер версионируемой базы знаний, правил и решений для команд и AI-агентов. Репозиторий поставляется как чистый шаблон без секретов, ключей и рабочих баз данных: только исходный код на стандартной библиотеке Python (`stdlib`), схемы миграций SQLite, спецификации контрактов, документация и синтетический набор проверок.

---

## Зачем нужен Company Brain

Когда над проектами работают одновременно люди и AI-агенты, обычные папки с Markdown или векторный поиск быстро деградируют: правила устаревают, агенты читают противоречивые инструкции, несанкционированные правки смешиваются с утверждёнными стандартами, а доказать, на каком именно правиле было основано решение, становится невозможно.

Company Brain решает это как управляемый контур памяти:

- знания и правила хранятся с явным версионированием и оптимистичной блокировкой (`expected_version`);
- доступ изолируется по проектам (`project_id`), ролям (`reader`, `writer`, `operator`) и спискам читателей каждой ревизии (`readers`);
- изменение действующих знаний проходит через карантин черновиков (`POST /drafts`), структурированный `Handoff Packet`, ручную приёмку оператором в Review UI и одноразовое подтверждение публикации (`POST /approval` + `POST /publish`);
- каждый вызов фиксируется в редактированном журнале аудита (`audit.db`) без текста запросов, паролей и токенов;
- агенты подключаются через строгий HTTP API, версионируемый SDK или MCP stdio, а исполнение кода в `CodeMode` изолируется нативной песочницей macOS Seatbelt.

---

## Ключевые возможности и особенности

### 1. Версионируемое хранилище и транзакционные изменения
- **SQLite (`knowledge.db`) с миграциями `001.sql`..`004.sql`:** хранит документы, ревизии, ACL-списки читателей, черновики, очередь ревью и метаданные правил в одной транзакционной базе.
- **Атомарные changesets и откат:** `POST /write` применяет пакет изменений целиком с проверкой `expected_version` (защита от гонок записи, `HTTP 409`). Любой применённый пакет можно компенсировать через `POST /compensate`.
- **Гигиена и квоты до записи (`brain/operations/hygiene.py`):** при записи и сохранении черновиков автоматически проверяются лимиты размера документа (`document_bytes`), числа активных документов (`active_documents`), версий (`versions`), черновиков (`drafts`) и читаемость указанных источников (`sources`).
- **Опциональная память в Git (`brain/git_store.py`):** каноническая раскладка каталогов `active`, `archive`, `shared`, `inbox`, `workspaces` с воспроизводимым импортом Markdown ([docs/memory-layout.md](docs/memory-layout.md)).

### 2. Модель безопасности, RBAC и изоляция
- **Только loopback:** локальный сервер (`deploy.app`) принимает только числовые loopback-адреса (`127.0.0.1` или `::1`); внешний bind и URL с учётными данными отклоняются.
- **Ролевая модель (`brain/policy.py`, `brain/write_access.py`):**
  - `reader`: чтение и поиск доступных ревизий, разрешение правил, анализ влияния (`read`, `search`, `resolve`, `impact`, `check`).
  - `writer`: всё доступное `reader`, плюс создание черновиков, постановка кандидатов в очередь ревью и запись связей проверок (`drafts`, `reviews`, `write` в рамках прав).
  - `operator`: всё доступное `writer`, плюс ручная приёмка в Review UI, выпуск одноразовых подтверждений (`POST /approval`), публикация (`POST /publish`), откат (`POST /compensate`) и операторский аудит (`/ops`, `POST /ops/audit-summary`).
- **Защита учётных данных:** на диске хранятся только SHA-256 хэши паролей (`credentials.json`, владелец процесса, права `0600`). Клиентский `SecretBroker` (`brain/adapters/secret_broker.py`) читает файл ключа с флагами `O_NOFOLLOW | O_NONBLOCK`, проверяет `S_ISREG`, владельца и права `0600`, обменивает ключ на Bearer-токен через `POST /session` и не передаёт секрет в переменные окружения или исключения.
- **Одноразовые подтверждения (`ApprovalRegistry`):** операции публикации и чувствительные правки требуют одноразового токена подтверждения (`POST /approval`), криптографически привязанного к `request_digest` конкретного запроса.

### 3. Карантин знаний, Handoff Packet и Review UI
- **Разделение черновика и публикации:** `POST /drafts` сохраняет неизменяемую ревизию черновика, не затрагивая активные знания.
- **Контракт `Handoff Packet` (`brain/handoff.py`, `specs/contracts/packet.v1.schema.json`):** постановка в очередь `POST /reviews` требует валидный пакет (`schema_version == 1`), 40-символьный `candidate_sha`, ссылку на собственный черновик автора и подтверждение читаемости всех `source`-ссылок (`doc:<id>@<version>`).
- **Встроенный Review UI (`brain/web/__init__.py`, `brain/web_api.py`):** отдельный локальный веб-интерфейс с входом оператора (`/login`), очередью приёмки (`/`), карточкой кандидата (`/review/<task_id>`), обязательным CSRF `form_token`, комментарием при возврате и обогащением карточки данными о влиянии (`impact`).

### 4. Живые правила, граф влияния и иерархия директив
- **Живые правила (`POST /resolve`, `brain/operations/live_rules.py`):** разрешение версионированных правил по `rule_id` с контролем прав доступа и свежести.
- **Граф влияния (`POST /impact`, `POST /check`):** связывает правила, задачи, проверки и прогоны, позволяет найти затронутые артефакты (`affected`), правила для конкретной проверки (`rules_for`) и правила без тестового покрытия (`uncovered_rules`).
- **Пятиуровневая иерархия директив (`brain/instructions.py`, `brain/directive_files.py`):** проверяет согласованность директив разных уровней на каждом рабочем запросе; при конфликте или недоступности источника директив возвращает `HTTP 422` (`OUTCOME_NONE_CLARIFICATION`) и фиксирует событие в аудите.

### 5. Редактированный аудит, наблюдаемость и воспроизводимые релизы
- **Независимый `audit.db` (`brain/audit.py`, `brain/observer.py`):** хранит последовательность шагов (`seq`, `trace_id`, `step`, `outcome`, длительность) без раскрытия содержимого документов, запросов и секретов.
- **Контроль целостности и `GET /ready`:** `Observer` выявляет пропуски последовательности (`sequence_gap`) и отказы политики; при нарушении целостности журнала `GET /ready` отвечает `503`.
- **Страница `/ops` и `POST /ops/audit-summary`:** сводка состояния стенда, лаг очереди, число возвратов, объём хранилища, статистика времени по шагам и последние события аудита.
- **Детерминированные релизные бандлы (`release/bundle.py`):** сборка ZIP-архива с `manifest.json`, проверкой SHA-256 и документированным откатом ([docs/operations/release.md](docs/operations/release.md)).

### 6. Версионируемый SDK, MCP stdio и песочница Code Mode
- **`VersionedSDK` (`brain/sdk/api.py`):** закрепляет редакцию SDK и роль при открытии сессии; открытые сессии продолжают работать на своей редакции даже после регистрации новой ([docs/sdk.md](docs/sdk.md)).
- **Два профиля CLI и MCP stdio (`brain/cli.py`, `brain/cli_factory.py`, `brain/adapters/mcp.py`, `brain/adapters/swarm_mcp.py`):**
  - `SwarmReadOnly` (`build_swarm_readonly`): минимальный read-only интерфейс (`start`, `run`) для безопасного поиска знаний воркерами.
  - `CodeMode` (`build_code_mode`): выполнение Python-сценариев поверх закреплённой сессии SDK с обязательным возвратом `{"value": ..., "grounding_refs": [...]}` и проверкой существования и доступности каждой ссылки.
  - Протокол MCP JSON-RPC поверх `stdio` (`serve_stdio`) с жёстким лимитом сообщения `MAX_MESSAGE_BYTES = 64 KiB`.
- **Нативная песочница macOS Seatbelt (`brain/sandbox/`):** ограничивает выполнение кода в `CodeMode` системным профилем `sandbox-exec`.

---

## Быстрый старт (чистый локальный стенд)

Требуется Python 3.11 или новее (только стандартная библиотека). Для сквозного режима `CodeMode` с нативной песочницей нужен macOS.

### 1. Запуск базовой проверки репозитория

```sh
python3 -W default -m unittest discover -s checks -p 'test_*.py'
```

### 2. Создание чистого локального профиля (вне репозитория)

Никогда не храните ключи, `credentials.json` и рабочие базы `knowledge.db` / `audit.db` внутри Git-репозитория. Создайте отдельный каталог профиля с правами `0700`:

```sh
umask 077
profile=$(mktemp -d "$HOME/company-brain.XXXXXX")

python3 -c '
import getpass, hashlib, json
credential = getpass.getpass("Пароль оператора: ")
digest = hashlib.sha256(credential.encode()).hexdigest()
print(json.dumps([
    {
        "credential_sha256": digest,
        "actor_id": "local-operator",
        "project_id": "demo",
        "role": "operator",
    }
]))
' > "$profile/credentials.json"

cat > "$profile/config.json" <<'JSON'
{
  "bind": "127.0.0.1",
  "http_port": 0,
  "review_port": 0,
  "data_dir": "data",
  "credential_file": "credentials.json"
}
JSON

python3 -B -m deploy.app --config "$profile/config.json"
```

После старта фактические адреса (`knowledge_url`, `review_url`) и `pid` появятся в `$profile/data/ready.json`.

Подробные инструкции по эксплуатации: [docs/operations/deploy.md](docs/operations/deploy.md), [docs/operations/maintenance.md](docs/operations/maintenance.md), [docs/operations/recovery.md](docs/operations/recovery.md).

---

## Типовые сценарии работы

### Сценарий 1. Подключение клиента или оркестратора по HTTP API

1. Проверьте готовность сервиса: `GET <knowledge_url>/ready` (`200 {"status": "ready"}`).
2. Откройте сессию по секретному ключу (значение из файла с правами `0600`):
   `POST /session` с `{"credential": "..."}` -> получите Bearer-токен.
3. Используйте заголовок `Authorization: Bearer <token>` и опциональный `X-Trace-Id`:
   - `POST /identity` -> проверить `actor_id`, `project_id`, `role`;
   - `POST /search` -> поиск по доступным документам проекта;
   - `POST /read` -> чтение актуальной или конкретной версии документа;
   - `POST /resolve` -> получение актуального правила по `rule_id`;
   - `POST /impact` -> просмотр затронутых задач и непокрытых правил.
4. Закройте сессию через `POST /logout`.

### Сценарий 2. Карантинное изменение правила с ручной приёмкой владельца

1. Автор (`writer`) отправляет черновик через `POST /drafts` (`draft_id`, `change` с `doc_id`, `text`, `expected_version`, `readers`, `sources`). Активная база при этом не меняется.
2. Автор ставит кандидата в очередь через `POST /reviews`, передавая `task_id`, 40-символьный `candidate_sha`, `draft_id`, `revision` и заполненный `Handoff Packet`.
3. Владелец (`operator`) открывает `<review_url>/login`, входит в Review UI, изучает карточку `/review/<task_id>` (дифф, источники, тесты, влияние `impact`) и нажимает «Принять» или «Вернуть» с обязательным комментарием.
4. Для публикации оператор получает одноразовое подтверждение через `POST /approval` и вызывает `POST /publish`.
5. При необходимости отката оператор применяет компенсирующий changeset через `POST /compensate`.

### Сценарий 3. Подключение AI-агентов и Swarm через CLI / MCP stdio

`brain.cli` умеет работать в режимах `start`, `run` и `stdio` (MCP JSON-RPC) поверх двух встроенных фабрик из `brain.cli_factory`:

- **Read-only поиск для воркеров (`build_swarm_readonly`):**

```sh
BRAIN_KNOWLEDGE_URL="http://127.0.0.1:<port>" \
BRAIN_PROJECT_ID="demo" \
BRAIN_CREDENTIAL_FILE="$profile/agent-reader.credential" \
python3 -m brain.cli --factory brain.cli_factory:build_swarm_readonly stdio
```

- **Песочница `CodeMode` с проверкой `grounding_refs` (`build_code_mode`):**

```sh
BRAIN_KNOWLEDGE_URL="http://127.0.0.1:<port>" \
BRAIN_PROJECT_ID="demo" \
BRAIN_CREDENTIAL_FILE="$profile/agent-writer.credential" \
python3 -m brain.cli --factory brain.cli_factory:build_code_mode stdio
```

Адаптер [brain/adapters/swarm.py](brain/adapters/swarm.py) дополнительно формирует валидированный `TaskSpec` из снимка `Handoff Packet` (`public_task_spec`) и синхронизирует статусы приёмки (`review_state`).

### Сценарий 4. Мониторинг, аудит и воспроизводимая выкладка

1. В Review UI откройте `/ops` (или вызовите `POST /ops/audit-summary` от имени `operator`): проверьте здоровье `Observer`, отсутствие `sequence_gap`, число отказов политики и задержки по шагам.
2. Перед обновлением остановите процесс (`SIGTERM`), дождитесь удаления `data/ready.json`, сделайте резервную копию каталога профиля и проверьте базы через `PRAGMA integrity_check`.
3. Соберите проверяемый релизный архив и `manifest.json` через [release/bundle.py](release/bundle.py) по инструкции [docs/operations/release.md](docs/operations/release.md).

---

## Карта HTTP API и Review UI

### Knowledge HTTP API (`brain/server.py`, `brain/live_workflow.py`)

| Метод и путь | Минимальная роль | Назначение |
| --- | --- | --- |
| `GET /health` | без авторизации | Базовая проверка живости процесса |
| `GET /ready` | без авторизации | Проверка готовности и целостности журнала аудита (`200` / `503`) |
| `POST /session` | по ключу | Обмен `credential` на Bearer-токен сессии |
| `POST /logout` | любая | Отзыв текущего Bearer-токена |
| `POST /identity` | `reader` | Возврат `actor_id`, `project_id`, `role` текущей сессии |
| `POST /read` | `reader` | Чтение документа по `doc_id` и опциональной `version` с проверкой ACL |
| `POST /search` | `reader` | Поиск по доступным документам проекта |
| `POST /resolve` | `reader` | Разрешение актуального правила по `rule_id` |
| `POST /impact` | `reader` | Анализ влияния правил и поиск непокрытых правил (`uncovered_rules`) |
| `POST /check` | `reader` / `writer` | Сводка прогонов проверок; запись связей `task_id` / `rule_ids` требует `writer`+ |
| `POST /write` | `writer` | Применение атомарного changeset с проверкой прав и квот |
| `POST /drafts` | `writer` | Сохранение неизменяемой ревизии черновика в карантине |
| `POST /reviews` | `writer` | Постановка черновика и `Handoff Packet` в очередь приёмки |
| `POST /approval` | `operator` | Выпуск одноразового подтверждения для `write` или `publish` |
| `POST /publish` | `operator` | Публикация принятого черновика по одноразовому `approval` |
| `POST /compensate` | `operator` | Компенсирующий откат ранее применённого `change_id` |
| `POST /ops/audit-summary` | `operator` | Сводка аудита, телеметрии и проверок `Observer` |

### Review UI (`brain/web/__init__.py`, `brain/web_api.py`)

- `GET /login`, `POST /login`: вход оператора по учётным данным проекта.
- `GET /`: очередь приёмки с кратким обзором и деталями карточек.
- `GET /review/<task_id>`: полная карточка кандидата, источников, тестов и влияния.
- `POST /review/<task_id>/decision`: фиксация решения (`approve` / `return`) с проверкой CSRF `form_token` и `candidate_sha`.
- `GET /ops`: панель состояния стенда, метрик времени и последних событий аудита.

---

## Структура репозитория

```text
brain/
  adapters/     # MCP stdio (CodeMode), SwarmReadOnly, SecretBroker, адаптер Swarm, источники
  operations/   # Гигиена и квоты, живые правила, телеметрия, восстановление
  sandbox/      # Нативная песочница macOS Seatbelt и раннер CodeMode
  sdk/          # VersionedSDK с закреплением редакций по сессиям
  storage/      # SQLite-хранилище, changesets, review_state, импорт Git, миграции 001..004.sql
  web/          # HTML-шаблоны Review UI и панели /ops
  server.py     # KnowledgeService и HTTP-сервер (loopback, X-Trace-Id, лимит JSON)
  live_workflow.py # Бизнес-логика правил, черновиков, ревью, публикаций и аудита
  policy.py     # RBAC, проектные границы и request_digest
  cli.py        # CLI и MCP stdio точка входа
deploy/         # Локальный запуск сервиса (deploy.app), конфиг и логин
release/        # Детерминированная сборка и проверка релизных ZIP-бандлов (bundle.py)
checks/         # 78 модульных, интеграционных, ACL, HTTP и macOS Seatbelt тестов
specs/          # Схемы контрактов (entities.v1, packet.v1) и поведенческие спецификации
docs/           # Операционные руководства, SDK, раскладка памяти и проектные материалы
```

---

## Безопасность шаблона

- В репозитории **нет** реальных паролей, токенов, пользовательских баз знаний, логов или привязки к конкретной инфраструктуре.
- Каталоги `.local/`, файлы `.env` и `.env.*` исключены через `.gitignore`.
- Все тесты в `checks/` используют только синтетические данные во временных каталогах.

---

## Документация

- [Смысловое ядро и роли](docs/core.md)
- [Запуск локального профиля](docs/operations/deploy.md)
- [Сборка релиза и репетиция отката](docs/operations/release.md)
- [Обслуживание](docs/operations/maintenance.md), [восстановление](docs/operations/recovery.md) и [дежурный регламент](docs/operations/oncall.md)
- [Версионируемый SDK](docs/sdk.md)
- [Память команды в Git](docs/memory-layout.md)
- [Плейбук Company Brain](docs/project/company-brain-playbook-v2.html)
- [Чек-лист сборки](docs/project/company-brain-build-checklist.md)
- [Исследование и источники](docs/project/company-brain-research.md)
- [Пакет запуска Swarm](docs/project/company-brain-swarm/README.md) и [плейбук Swarm](docs/project/provider-agnostic-swarm-playbook.html)
- [Результат приёмки](docs/project/release-acceptance.md) (исходники принятого релиза закреплены тегом `o91-accepted-2950df3853e5`)
