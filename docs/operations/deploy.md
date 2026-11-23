# Локальный профиль Company Brain

Из корня релиза создайте отдельный каталог профиля и задайте пароль оператора. Файл учётных данных принадлежит пользователю запуска, имеет режим `0600` и содержит SHA-256 пароля.

```sh
umask 077
profile=$(mktemp -d "$HOME/company-brain.XXXXXX")
python3 -c '
import getpass, hashlib, json
credential = getpass.getpass("Пароль оператора: ")
digest = hashlib.sha256(credential.encode()).hexdigest()
print(json.dumps([dict(credential_sha256=digest, actor_id="local-operator",
                       project_id="local", role="operator")]))
' > "$profile/credentials.json"
cat > "$profile/config.json" <<'JSON'
{"bind":"127.0.0.1","http_port":0,"review_port":0,"data_dir":"data","credential_file":"credentials.json"}
JSON
python3 -B -m deploy.app --config "$profile/config.json"
```

Допустимы только числовые loopback-адреса IPv4 и IPv6, например `127.0.0.1` и `::1`. Внешний bind исключён. Нулевые порты назначает ОС, фактические `knowledge_url`, `review_url` и `pid` доступны в `data/ready.json`. Относительные пути отсчитываются от файла конфигурации.

Откройте `<review_url>/login` и введите пароль оператора. Очередь доступна роли `operator` в её проекте. Для API передайте `credential` в JSON на `POST /session` по `knowledge_url`. Права на `/read`, `/search` и `/write` проверяет `brain.policy` по роли и проекту. Для начального наполнения очереди задайте `review_seed` с путём к доверенному JSON-массиву карточек с `project_id`. При повторном запуске используется `data/review.json` с сохранённой очередью и решениями.

Остановите процесс через `Ctrl+C` или `kill -TERM <pid>` и дождитесь удаления `data/ready.json`. Для перезапуска используйте тот же конфиг. `data/knowledge.db` хранит знания и наборы изменений, `data/audit.db` хранит журнал операций без содержимого запросов, паролей и токенов. Схемы открываются командами `sqlite3 "$profile/data/knowledge.db" .schema` и `sqlite3 "$profile/data/audit.db" .schema`.
