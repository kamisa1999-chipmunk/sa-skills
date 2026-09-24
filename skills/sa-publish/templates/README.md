# Снимки space-шаблонов Confluence

Живой источник: `GET /rest/experimental/template/{id}`.

Эти JSON — запасной вариант, если API шаблона недоступен. Тело (`body`) — storage XML, как в шаблоне BIZ.

Карта типов: `../space-templates.json`.

Обновить снимки — выгрузить каждый `templateId` тем же experimental API и перезаписать файл.
