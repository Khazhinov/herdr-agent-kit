# herdr-agent-kit

[English](README.md) | Русский

Переносимые компоненты для работы агентов поверх [herdr-core](https://github.com/herdrdev/herdr-core).
Каждый компонент - концепция, которую можно перенести в собственную агентскую среду, и её рабочая
реализация. Ни один компонент не привязан к одной среде: по обе стороны вызова работают Claude Code,
Codex, OpenCode и любая команда, которой управляет herdr-core. Работает вместе с
[herdr-gsd-template](https://github.com/Khazhinov/herdr-gsd-template) (`orch.py tools`, `tools-link`,
`tool-ask`), но от него не зависит. Прежнее название - `herdr-tool-agents`.

## Каталог

| Компонент | Что даёт | Концепция | Реализация |
|---|---|---|---|
| Реестр MCP | проект пользуется MCP-серверами других папок, не загружая их схемы и не храня их токены | [concepts/mcp-registry.ru.md](concepts/mcp-registry.ru.md) | `tool_ask.py ask`, `mcp_probe.py` |
| Служебные репозитории | проект вызывает типизированные функции другого репозитория (документы, отчёты, проверки), каждую в среде, модели и с усилием, выбранными этим репозиторием | [concepts/service-repos.ru.md](concepts/service-repos.ru.md) | `tool_ask.py call`, `agent-service.toml` |
| Вызовы через herdr | единый протокол для всех исполнителей: видимый терминал, сброс, правила в запросе, файл ответа, сигналы завершения, коды выхода | [concepts/herdr-calls.ru.md](concepts/herdr-calls.ru.md) | `tool_ask.py` |
| Подключённые проекты | проект получает перечень подключённых модулей, файл с описанием каждого модуля и журнал изменений; `sync` поддерживает их в актуальном состоянии | [concepts/linked-projects.ru.md](concepts/linked-projects.ru.md) | `tool_ask.py link`, `sync` |

Оба вида модулей используют один реестр, один формат карточек и один способ подключения проектов.
Новый компонент получает собственную папку верхнего уровня со своим README и строку в этой таблице.

## Состав

| Путь | В git | Назначение |
|---|---|---|
| `tool_ask.py`, `mcp_probe.py` | да | CLI, чтение конфигураций MCP и клиент `tools/list` (только стандартная библиотека) |
| `registry.example.toml` | да | примеры записей реестра обоих видов с пояснениями |
| `examples/<module>/` | да | образец конфигурации MCP или манифеста служебного репозитория |
| `concepts/` | да | концепции, изложенные так, чтобы их можно было применить без этого кода |
| `AGENTS.md` | да | порядок работы агента, который ведёт реестр |
| `local/registry.toml` | нет | модули этой машины |
| `local/cards/<module>.md`, `.log` | нет | сгенерированные карточки (инструменты MCP или функции) и журнал их изменений |
| `local/modules/<module>.md` | нет | дополнительные правила исполнителя модуля |
| `local/links.json` | нет | подключённые проекты и их модули |

## Настройка на машине

Порядок одинаков для автора и для того, кто клонировал репозиторий; свежий клон начинается с пустого
`local/`.

1. Установить herdr-core (`hc`), Herdr App (если нужно видеть исполнителей) и среды агентов, которые
   используются в папках модулей.
2. `mkdir -p local && cp registry.example.toml local/registry.toml`, удалить примеры записей.
3. `./tool_ask.py scan <root>` перечисляет папки с конфигурациями MCP (стандартные места Claude Code,
   Codex, OpenCode, Gemini CLI, Cursor, VS Code) и папки с `agent-service.toml`.
4. Зарегистрировать нужные папки (вручную или поручить агенту в этой папке: он следует `AGENTS.md`).
5. `./tool_ask.py refresh --all`, затем `./tool_ask.py list` и `./tool_ask.py card <module>`.
6. Пробный вызов: `ask <module> "<узкий запрос>"` или `call <module> <function> --arg name=value`.
   Первый запуск исполнителя может ждать диалога доверия или входа: ответить в Herdr App.
7. Подключить проекты: `./tool_ask.py link /path/to/project <module>...` (см.
   [concepts/linked-projects.ru.md](concepts/linked-projects.ru.md)).

## Переменные окружения

| Переменная | По умолчанию | Прежнее имя, читается |
|---|---|---|
| `HERDR_AGENT_KIT_REGISTRY` | `local/registry.toml` | `HERDR_TOOL_AGENTS_REGISTRY` |
| `HERDR_AGENT_KIT_CACHE` | `~/.cache/herdr-agent-kit` (ответы, задачи, `audit.jsonl`) | `HERDR_TOOL_AGENTS_CACHE` |
| `HERDR_AGENT_KIT_GRACE` | `20`: секунды ожидания файла ответа после окончания хода исполнителя | - |
| `ORCH_HC` | `hc` | - |

herdr-gsd-template находит эту папку через `HERDR_AGENT_KIT` (прежнее имя `HERDR_TOOL_AGENTS`) или
`[tools] hub` в `routing.local.toml`.

## Проверки

```bash
python3 tests/test_tool_ask.py
./scripts/self-check.sh        # тесты и отсутствие путей машины и личных имён в файлах под git
```

## Лицензия

MIT.
