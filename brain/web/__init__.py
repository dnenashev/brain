from html import escape
from urllib.parse import quote


_FIELD_LABELS = {
    "task_id": "Задача",
    "candidate_sha": "SHA кандидата",
    "status": "Статус решения",
    "brief": "Бриф",
    "changes": "Изменения",
    "risks": "Риски",
    "sources": "Источники",
    "tests": "Тесты",
    "feedback": "Замечания",
    "impact": "Влияние",
    "queued_at": "Поставлено в очередь",
    "published_change_id": "Набор публикации",
    "compensated_by": "Набор отката",
}
REVIEW_FIELDS = tuple(_FIELD_LABELS)
_QUEUE_DETAILS = ("candidate_sha", "changes", "risks", "sources", "tests", "feedback")

_STYLE = """
    :root { color-scheme: light dark; font-family: system-ui, sans-serif; }
    body { margin: 0; line-height: 1.5; }
    main { max-width: 48rem; margin: 0 auto; padding: 2rem 1rem; }
    h1, h2, p { margin: 0 0 .75rem; }
    a { text-underline-offset: .18em; }
    article, form { border: 1px solid GrayText; border-radius: .5rem; padding: 1rem; }
    article + article, form, nav { margin-bottom: 1rem; }
    article + article { margin-top: 1rem; }
    dl { margin: 0; }
    dt { font-weight: 600; }
    dd { margin: 0 0 .75rem; overflow-wrap: anywhere; }
    dd ul { margin: 0; padding-inline-start: 1.5rem; }
    .value { white-space: pre-wrap; overflow-wrap: anywhere; }
    summary { cursor: pointer; }
    details dl { margin-top: .75rem; }
    form { margin-top: 1.5rem; }
    label, textarea { display: block; }
    textarea { box-sizing: border-box; width: 100%; margin: .25rem 0 1rem; }
    .actions { display: flex; flex-wrap: wrap; gap: .5rem; }
    textarea, button { font: inherit; padding: .4rem .65rem; }
"""


def review_url(task_id):
    return "/review/" + quote(str(task_id), safe="")


def _html(value):
    return escape(str(value), quote=True)


def _page(title, content):
    return (
        '<!doctype html>\n<html lang="ru"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>{_html(title)}</title><style>{_STYLE}</style></head>'
        f'<body><main>{content}</main></body></html>'
    )


def _value(value):
    if value is None or value == "":
        return "Нет данных"
    if isinstance(value, dict):
        if not value:
            return "Нет записей"
        entries = "".join(f"<dt>{_html(key)}</dt><dd>{_value(item)}</dd>" for key, item in value.items())
        return f"<dl>{entries}</dl>"
    if isinstance(value, (list, tuple)):
        if not value:
            return "Нет записей"
        entries = "".join(f"<li>{_value(item)}</li>" for item in value)
        return f"<ul>{entries}</ul>"
    return f'<span class="value">{_html(value)}</span>'


def _fields(review, fields):
    entries = "".join(
        f'<dt>{_FIELD_LABELS[field]}</dt><dd data-field="{field}">{_value(review.get(field))}</dd>'
        for field in fields
    )
    return f"<dl>{entries}</dl>"


def render_queue(reviews, *, ops=False):
    cards = []
    for review in reviews:
        task_id = review["task_id"]
        cards.append(
            '<article>'
            f'<h2><a href="{_html(review_url(task_id))}">{_html(task_id)}</a></h2>'
            f'{_fields(review, ("status", "brief"))}'
            f'<details><summary>Подробности</summary>{_fields(review, _QUEUE_DETAILS)}</details>'
            '</article>'
        )
    content = "".join(cards) or "<p>Очередь пуста.</p>"
    navigation = '<nav><a href="/ops">Состояние стенда</a></nav>' if ops else ""
    return _page("Очередь приёмки", f"{navigation}<h1>Очередь приёмки</h1>{content}")


def render_review(review, *, task_id, form_token):
    title = f"Приёмка {task_id}"
    action = _html(review_url(task_id) + "/decision")
    candidate_sha = _html(review["candidate_sha"])
    return _page(
        title,
        '<nav><a href="/">Очередь приёмки</a></nav>'
        f'<h1>{_html(title)}</h1>{_fields(review, REVIEW_FIELDS)}'
        f'<form method="post" action="{action}">'
        f'<input type="hidden" name="candidate_sha" value="{candidate_sha}">'
        f'<input type="hidden" name="form_token" value="{_html(form_token)}">'
        '<label for="comment">Комментарий (обязателен при возврате)</label>'
        '<textarea id="comment" name="comment" rows="4"></textarea>'
        '<div class="actions">'
        '<button type="submit" name="decision" value="approve">Принять</button>'
        '<button type="submit" name="decision" value="return">Вернуть</button>'
        '</div></form>',
    )


def render_ops(report):
    observer = report["observer"]
    telemetry = report["telemetry"]
    status = {
        "Observer": observer["health"],
        "sequence_gap": observer["sequence_gap"],
        "policy_denied": observer["policy_denied"],
        "recovery_owner": observer["recovery_owner"],
        "queue_lag_seconds": round(telemetry["queue_lag_seconds"], 3),
        "returns": telemetry["returns"],
        "versions": telemetry["versions"],
        "used_bytes": telemetry["used_bytes"],
    }
    steps = {
        name: {"count": values["count"], "avg_ms": round(values["total_ms"] / values["count"], 3),
               "max_ms": round(values["max_ms"], 3)}
        for name, values in telemetry["steps"].items()
    }
    return _page(
        "Состояние стенда",
        '<nav><a href="/">Очередь приёмки</a></nav>'
        f'<h1>Состояние стенда</h1>{_value(status)}'
        f'<h2>Время выполнения</h2>{_value(steps)}'
        f'<h2>Последние события аудита</h2>{_value(report["events"][::-1])}',
    )
