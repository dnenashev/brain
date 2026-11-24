# Company Brain

Company Brain хранит версионированные правила и связывает их с задачами, проверками и ревью. В репозитории находятся сервер SQLite, интерфейс оператора, CLI, нативная песочница macOS Seatbelt и синтетические сценарии проверки.

- [Запуск локального стенда](docs/operations/deploy.md)
- [Плейбук Company Brain](docs/project/company-brain-playbook-v2.html)
- [Чек-лист сборки](docs/project/company-brain-build-checklist.md)
- [Исследование и источники](docs/project/company-brain-research.md)
- [Пакет запуска Swarm](docs/project/company-brain-swarm/README.md) и [плейбук Swarm](docs/project/provider-agnostic-swarm-playbook.html)
- [Результат приёмки](docs/project/release-acceptance.md)

Проверки запускаются из корня репозитория.

```sh
python3 -W default -m unittest discover -s checks -p 'test_*.py'
```

Для сквозного прогона с Seatbelt нужен macOS. Исходники принятого релиза закреплены тегом o91-accepted-2950df3853e5.
