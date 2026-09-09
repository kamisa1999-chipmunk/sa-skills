# Источники Jira и Confluence для SA-review

Читать **на шаге минимального контекста**, до карты изменения. Не читать в начале прогона «на всякий случай». Не меняет критерии ревью, applicability, coverage, reconciliation и presentation.

Способ чтения не привязан к конкретной локальной реализации. Skill одинаково работает, если Jira/Confluence доступны через MCP / connector / встроенный tool **или** через локальный Python CLI из репозитория.

## Выбор источника

Перед первым чтением Jira и перед первым чтением Confluence в этом прогоне — отдельно для каждой системы:

1. Использовать уже доступный в окружении источник (MCP, connector, встроенный tool), если он умеет прочитать нужный объект.
2. Если подходящего источника нет — локальный fallback из репозитория.

Не смешивать несколько источников одного артефакта без необходимости. Источник системы выбрать один раз за прогон и не менять без причины. Не перечитывать один артефакт повторно: после чтения сразу свернуть в evidence summary (правила — в `evidence-and-context.md`).

Если в workspace есть локальный демо-стенд (`DEMO-XXX` / `sa-demo/`) — живую Jira/Confluence не читать. Файлы кейса — в `evidence-and-context.md`. Иначе демо игнорировать.

Токены не читать и не показывать. Страницы и задачи этим скиллом не изменять.

## Какие данные нужны ревью

Независимо от способа чтения должны быть доступны те же данные.

**Jira issue:** summary, description, type, links / children / related issues; comments — если нужны для аналитики, прошлых ревью или уровня 4. Parent/epic — при необходимости.

**Confluence:** title, URL, актуальное содержимое; version / date — при наличии; labels / тип страницы — при наличии.

По связанным Jira- и Confluence-ссылкам переходить тем же правилом выбора источника. Открывать только то, что нужно текущему уровню / критерию, не все ссылки подряд.

## Неполные данные источника

Если выбранный MCP / connector / tool не отдаёт часть обязательных полей:

- точечно добрать недостающее другим доступным способом (другой tool той же системы или local CLI fallback);
- не перечитывать уже полученные поля и не подменять весь артефакт без нужды;
- если добрать нельзя — `NEEDS_CLARIFICATION`, не считать ветку проверенной и не ставить `ISSUE` / `checked` / `not applicable` молча.

Отсутствие поля в ответе источника ≠ «поля нет в Jira/Confluence».

## Local CLI fallback

Только если подходящего MCP / connector / tool нет или им нельзя добрать обязательные поля.

Клиенты не входят в этот репозиторий. Нужен соседний [`jira-write`](https://github.com/kamisa1999-chipmunk/jira-write) и `JIRA_WRITE_REPO_DIR` на его корень (или запуск из его рабочей директории).

Jira:

```bash
python3 "$JIRA_WRITE_REPO_DIR/scripts/cli/get_issue.py" CAT2-XXXX
```

Confluence по `pageId`:

```bash
python3 ~/.cursor/skills/sa-review/scripts/dump_confluence_page.py PAGE_ID
```

История задачи — только уровень 4 в `evidence-and-context.md`: скилл `jira-history` или, если его нет, `$JIRA_WRITE_REPO_DIR/scripts/cli/get_issue_history.py`.

Сырые выгрузки, если CLI их создаёт, — в `reports/` репозитория `jira-write` или во временную папку (не в git). После прогона удалить файлы этого запуска. Не коммитить их.

## Способы установки

Skill одинаково работает в двух режимах доступа к Jira/Confluence. Режим не меняет критерии ревью.

### MCP / connector mode

Python-клиенты и локальные токены для **чтения** Jira/Confluence не обязательны. Используются MCP, connector или встроенные tools окружения. Репозиторий `jira-write` для чтения не нужен.

### local CLI fallback

Клиенты и `.env` — из [`jira-write`](https://github.com/kamisa1999-chipmunk/jira-write):

- Jira: `scripts/cli/get_issue.py` и `jira_client` (`JIRA_*`);
- Confluence: `dump_confluence_page.py` из этого скилла + `confluence_client` из `jira-write` (`CONFLUENCE_*`).

Запись в Jira по-прежнему только через скилл `jira-write` после подтверждения. Этот скилл сам ничего не публикует.
