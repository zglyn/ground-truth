"""Server-rendered HTML pages for the dashboard. Reads only the typed view model."""

from __future__ import annotations

import html
from dataclasses import dataclass, field

from ground_truth.styles import CSS
from ground_truth.views import STAGE_NAMES, Dashboard, ThesisCard, TopicRow, hostname, percent, safe_url, when

INLINE = 'class="inline"'
NAV = (("/", "Overview"), ("/portfolio", "Portfolio"), ("/track-record", "Track Record"), ("/settings", "Settings"))

SAVED = {
    "holding": "Holding saved.",
    "holding-removed": "Holding removed.",
    "suggested": "Watch Topic suggestions updated.",
    "topic-accepted": "Watch Topic accepted. The Scout will watch it from the next check.",
    "topic-rejected": "Suggestion rejected.",
    "topic": "Watch Topic saved.",
    "settings": "Settings saved.",
    "run": "A check has started. Refresh in a minute to see the results.",
    "running": "A check is already running.",
    "demo": "Staged story injected. The swarm grouped it, researched it and wrote the Thesis below.",
    "demo-canned": "The recorded run failed, so the canned fallback Thesis is shown.",
    "injected": "Test headline injected. A check is running with live You.com calls; refresh in a minute.",
    "demo-already": "The staged story was already injected. Restart with a fresh demo database to run it again.",
}


@dataclass
class FormState:
    """A submitted form that failed validation, so the page can show it again as typed."""

    form: str
    values: dict[str, list[str]] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    def value(self, name: str, default: str = "") -> str:
        values = self.values.get(name)
        return values[0] if values else default

    def checked(self, name: str) -> set[str]:
        return set(self.values.get(name, []))


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _badge(stance: str) -> str:
    kind = {"Bull": "bull", "Bear": "bear"}.get(stance, "neutral")
    return f'<span class="badge badge-{kind}">{esc(stance)}</span>'


def _ext_link(url: str | None, text: str) -> str:
    target = safe_url(url)
    if target is None:
        return esc(text)
    return f'<a href="{esc(target)}" rel="noopener noreferrer">{esc(text)}</a>'


def _form_open(form_id: str, action: str, back: str, *, extra: str = "") -> str:
    return (
        f'<form method="post" action="/" id="{esc(form_id)}" {extra}>'
        f'<input type="hidden" name="action" value="{esc(action)}">'
        f'<input type="hidden" name="return" value="{esc(back)}">'
        f'<input type="hidden" name="form" value="{esc(form_id)}">'
    )


def _active(state: FormState | None, form_id: str) -> FormState | None:
    return state if state is not None and state.form == form_id else None


def _input(
    form_id: str,
    name: str,
    label: str,
    value: str,
    state: FormState | None,
    *,
    hint: str = "",
    inputmode: str = "",
    required: bool = False,
    size: int = 0,
) -> str:
    current = _active(state, form_id)
    shown = current.value(name, value) if current else value
    error = current.errors.get(name, "") if current else ""
    field_id = f"{form_id}-{name}"
    described = []
    parts = [f'<div class="field"><label for="{esc(field_id)}">{esc(label)}</label>']
    if hint:
        parts.append(f'<span class="hint" id="{esc(field_id)}-hint">{esc(hint)}</span>')
        described.append(f"{field_id}-hint")
    if error:
        parts.append(f'<span class="error" id="{esc(field_id)}-error">{esc(error)}</span>')
        described.append(f"{field_id}-error")
    attrs = [
        'type="text"',
        f'id="{esc(field_id)}"',
        f'name="{esc(name)}"',
        f'value="{esc(shown)}"',
    ]
    if inputmode:
        attrs.append(f'inputmode="{esc(inputmode)}"')
    if required:
        attrs.append("required")
    if size:
        attrs.append(f'size="{size}"')
    if described:
        attrs.append(f'aria-describedby="{esc(" ".join(described))}"')
    if error:
        attrs.append('aria-invalid="true"')
    parts.append(f"<input {' '.join(attrs)}></div>")
    return "".join(parts)


def _textarea(form_id: str, name: str, label: str, value: str, state: FormState | None, *, hint: str = "") -> str:
    current = _active(state, form_id)
    shown = current.value(name, value) if current else value
    error = current.errors.get(name, "") if current else ""
    field_id = f"{form_id}-{name}"
    described = []
    parts = [f'<div class="field"><label for="{esc(field_id)}">{esc(label)}</label>']
    if hint:
        parts.append(f'<span class="hint" id="{esc(field_id)}-hint">{esc(hint)}</span>')
        described.append(f"{field_id}-hint")
    if error:
        parts.append(f'<span class="error" id="{esc(field_id)}-error">{esc(error)}</span>')
        described.append(f"{field_id}-error")
    attrs = [f'id="{esc(field_id)}"', f'name="{esc(name)}"']
    if described:
        attrs.append(f'aria-describedby="{esc(" ".join(described))}"')
    if error:
        attrs.append('aria-invalid="true"')
    parts.append(f"<textarea {' '.join(attrs)}>{esc(shown)}</textarea></div>")
    return "".join(parts)


def _checks(
    form_id: str, name: str, legend: str, options: list[str], selected: set[str], state: FormState | None
) -> str:
    current = _active(state, form_id)
    chosen = current.checked(name) if current else selected
    error = current.errors.get(name, "") if current else ""
    field_id = f"{form_id}-{name}"
    if not options:
        return f'<p class="hint">{esc(legend)}: add a Holding first.</p>'
    boxes = []
    for index, option in enumerate(options):
        box_id = f"{field_id}-{index}"
        mark = " checked" if option in chosen else ""
        boxes.append(
            f'<label for="{esc(box_id)}"><input type="checkbox" id="{esc(box_id)}" name="{esc(name)}" '
            f'value="{esc(option)}"{mark}> {esc(option)}</label>'
        )
    described = f' aria-describedby="{esc(field_id)}-error"' if error else ""
    error_html = f'<span class="error" id="{esc(field_id)}-error">{esc(error)}</span>' if error else ""
    return (
        f'<fieldset class="field"{described}><legend>{esc(legend)}</legend>{error_html}'
        f'<div class="checks">{"".join(boxes)}</div></fieldset>'
    )


def _form_error_summary(state: FormState | None, form_id: str) -> str:
    current = _active(state, form_id)
    if not current or not current.errors:
        return ""
    general = current.errors.get("_form")
    message = general or "Check the highlighted fields."
    return f'<div class="notice notice-error" role="alert"><p>{esc(message)}</p></div>'


def page(dash: Dashboard, title: str, path: str, body: str, *, notice: str = "") -> str:
    items = []
    for href, label in NAV:
        current = ' aria-current="page"' if href == path else ""
        items.append(f'<li><a href="{href}"{current}>{label}</a></li>')
    nav = "".join(items)
    live = f'<div class="notice notice-ok" role="status"><p>{esc(notice)}</p></div>' if notice else '<div role="status"></div>'
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} · Ground Truth</title>
<style>{CSS}</style>
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<header class="topbar">
  <div class="wrap">
    <a class="brand" href="/">GROUND TRUTH</a>
    <nav aria-label="Main"><ul class="nav">{nav}</ul></nav>
  </div>
</header>
<main id="main" class="wrap" tabindex="-1">
{_statusline(dash)}
{live}
{body}
</main>
</body>
</html>
"""


def _statusline(dash: Dashboard) -> str:
    status = dash.status
    return (
        '<div class="statusline">'
        f'<span class="state state-{esc(status.state)}"><span class="dot" aria-hidden="true"></span>'
        f"Research status: <strong>{esc(status.label)}</strong></span>"
        f"<span>Last checked: {esc(status.last_checked)}</span>"
        "</div>"
        f'<p class="mode">{esc(dash.runtime_label)}</p>'
    )


def _failure_banner(dash: Dashboard, back: str) -> str:
    status = dash.status
    if status.state == "paused":
        return (
            '<div class="notice notice-error" role="alert">'
            "<h2>Research is paused</h2>"
            f"<p>{esc(status.detail)}</p>"
            "<p>Content below is from the last successful run. Fix the recording check "
            '(see <a href="/settings#recording">Settings</a>) and restart the app to resume.</p></div>'
        )
    if status.state in ("attention", "degraded"):
        heading = "A stage failed" if status.state == "attention" else "Some sources had problems"
        return (
            f'<div class="notice {"notice-error" if status.state == "attention" else "notice-warn"}" role="alert">'
            f"<h2>{heading}</h2><p>{esc(status.detail)}</p>"
            "<p>Content below is from the last successful run.</p>"
            f'{_form_open("retry", "run_now", back)}<button class="secondary">Run checks now</button></form></div>'
        )
    return ""


# ----- Overview -----------------------------------------------------------


def _impact_table(card: ThesisCard, *, detail: bool = False) -> str:
    head = "<tr><th scope='col'>Holding</th><th scope='col'>Stance</th><th scope='col'>Action</th><th scope='col'>Confidence</th>"
    if detail:
        head += "<th scope='col'>Market reaction</th><th scope='col'>Expected effect</th>"
    head += "</tr>"
    rows = []
    for impact in card.impacts:
        cells = (
            f'<td data-label="Holding"><strong data-ticker-impact="{esc(impact.ticker)}">{esc(impact.ticker)}</strong></td>'
            f'<td data-label="Stance">{_badge(impact.stance)}</td>'
            f'<td data-label="Action">{esc(impact.action)}</td>'
            f'<td data-label="Confidence">{esc(impact.confidence)}</td>'
        )
        if detail:
            cells += (
                f'<td data-label="Market reaction">Day move {esc(impact.day_move)}<br>'
                f'<span class="hint">Volume {esc(impact.volume)}</span></td>'
                f'<td data-label="Expected effect">{esc(impact.expected)} '
                f'<span class="hint">Direction: {esc(impact.direction)}.</span></td>'
            )
        rows.append(f"<tr>{cells}</tr>")
    return f'<table class="stack"><thead>{head}</thead><tbody>{"".join(rows)}</tbody></table>'


def _effect_line(card: ThesisCard) -> str:
    return (
        f'<p class="effect">Portfolio effect: <strong>{esc(card.lean)}</strong> '
        f"(score {card.net:+.2f}). A weighted Stance measure: each Holding's weight times its confidence, "
        "positive for Bull and negative for Bear. It is not an expected return.</p>"
    )


def _thesis_summary(card: ThesisCard) -> str:
    changes = ""
    if card.version > 1 and card.changes:
        items = "".join(f"<li>{esc(change)}</li>" for change in card.changes)
        changes = f'<p><strong>What changed since version {card.version - 1}</strong></p><ul class="changes">{items}</ul>'
    missing = ""
    if card.missing:
        missing = f'<p class="hint">Missing evidence: {esc(", ".join(card.missing))}</p>'
    receipts = f"{card.receipt_count} Receipt{'s' if card.receipt_count != 1 else ''}"
    return (
        f'<article class="thesis" id="thesis-{esc(card.id)}" data-thesis="{esc(card.id)}" data-effect="{esc(card.color)}">'
        f'<h2 class="headline"><a href="/thesis/{esc(card.id)}">{esc(card.headline)}</a></h2>'
        f'<p class="meta">Updated {esc(when(card.created))} · Version {card.version}</p>'
        f"{changes}{_impact_table(card)}{_effect_line(card)}{missing}"
        f'<p class="disclaimer">{esc(card.disclaimer)}</p>'
        f'<div class="links"><a href="/thesis/{esc(card.id)}">Read Thesis <span aria-hidden="true">→</span></a>'
        f"<span>{receipts}</span>"
        f"{'<span>' + str(len(card.inferences)) + ' Inference' + ('s' if len(card.inferences) != 1 else '') + '</span>' if card.inferences else ''}"
        "</div></article>"
    )


def _empty_overview(dash: Dashboard) -> str:
    if not dash.holdings:
        return (
            '<div class="panel"><h2>Set up your Portfolio</h2><ol class="steps">'
            '<li><a href="/portfolio#holdings">Add your Holdings</a> with their weights.</li>'
            '<li><a href="/portfolio#topics">Accept or add Watch Topics</a> so the Scout knows what to watch.</li>'
            "<li>Wait for the first Thesis. The Scout checks free news sources every 5 minutes.</li>"
            "</ol></div>"
        )
    if not dash.active_topics():
        return (
            '<div class="panel"><h2>No Watch Topics yet</h2>'
            '<p>The Scout has nothing to watch. <a href="/portfolio#topics">Suggest Watch Topics from your Holdings</a> '
            "and accept the ones you want.</p></div>"
        )
    scout = dash.stages.get("scout")
    if scout and scout["state"] == "failed":
        return (
            '<div class="panel"><h2>The Scout could not check the news</h2>'
            f"<p>Last attempt {esc(when(scout['last_run_at']))}: {esc(scout['detail'])}</p>"
            "<p>No Thesis yet, because no Sightings could be collected.</p></div>"
        )
    if not scout:
        return '<div class="panel"><h2>Waiting for the first check</h2><p>The Scout has not run yet.</p></div>'
    return (
        '<div class="panel"><h2>No relevant Events yet</h2>'
        f"<p>The Scout last checked at {esc(when(scout['last_run_at']))} and found nothing that affects your Holdings. "
        "This is normal on a quiet day.</p></div>"
    )


def _filters(dash: Dashboard, holding: str, stance: str) -> str:
    holding_options = ['<option value="">All Holdings</option>'] + [
        f'<option value="{esc(t)}"{" selected" if t == holding else ""}>{esc(t)}</option>' for t in dash.tickers()
    ]
    stance_options = ['<option value="">All Stances</option>'] + [
        f'<option value="{s}"{" selected" if s == stance else ""}>{s}</option>' for s in ("Bull", "Bear", "Neutral")
    ]
    return (
        '<form method="get" action="/" class="filters" role="search" aria-label="Filter Theses">'
        f'<div class="field"><label for="f-holding">Holding</label><select id="f-holding" name="holding">{"".join(holding_options)}</select></div>'
        f'<div class="field"><label for="f-stance">Stance</label><select id="f-stance" name="stance">{"".join(stance_options)}</select></div>'
        '<div class="field"><button class="secondary">Apply filters</button></div>'
        "</form>"
    )


def _aside(dash: Dashboard) -> str:
    holdings = "".join(
        f'<tr><td data-ticker="{esc(h.ticker)}">{esc(h.ticker)}</td><td class="num">{esc(h.percent)}</td></tr>'
        for h in dash.holdings
    )
    holdings_html = (
        f'<table><tbody>{holdings}</tbody></table><p class="hint">Total allocated: {esc(percent(dash.total_weight))}</p>'
        if dash.holdings
        else '<p class="hint">No Holdings yet.</p>'
    )
    errors = dash.activity.event_errors
    error_items = "".join(
        f"<li>{esc(item['title'])}: {esc(item['error'])}</li>" for item in errors
    )
    stage_items = "".join(
        f"<li>{esc(STAGE_NAMES.get(item['stage'], item['stage']))}: {esc(item['detail'])}</li>"
        for item in dash.activity.stage_errors
    )
    count = len(errors) + len(dash.activity.stage_errors)
    error_html = f'<ul class="hint">{error_items}{stage_items}</ul>' if count else ""
    return (
        '<aside class="aside" aria-label="Portfolio and research activity">'
        f'<section><h2>Portfolio</h2>{holdings_html}<a href="/portfolio#holdings">Manage Holdings</a></section>'
        "<section><h2>Watch Topics</h2>"
        f"<p>{len(dash.active_topics())} active · {len(dash.suggested_topics())} suggested</p>"
        '<a href="/portfolio#topics">Manage Watch Topics</a></section>'
        "<section><h2>Research activity</h2>"
        '<dl class="kv">'
        f"<dt>Events awaiting research</dt><dd>{dash.activity.awaiting_research}</dd>"
        f"<dt>Events awaiting analysis</dt><dd>{dash.activity.awaiting_analysis}</dd>"
        f"<dt>Errors requiring attention</dt><dd>{count}</dd>"
        f"</dl>{error_html}</section>"
        "</aside>"
    )


def _inject_panel(dash: Dashboard, state: FormState | None) -> str:
    if not dash.inject_enabled:
        return ""
    form_id = "inject"
    topics = dash.active_topics()
    if not topics:
        return (
            '<div class="notice notice-warn"><h2>Test injection</h2>'
            '<p><a href="/portfolio#topics">Accept a Watch Topic</a> first. An injected headline is filed under one.</p></div>'
        )
    current = _active(state, form_id)
    chosen = current.value("topic") if current else topics[0].name
    error = current.errors.get("inject_topic", "") if current else ""
    options = "".join(
        f'<option value="{esc(topic.name)}"{" selected" if topic.name == chosen else ""}>'
        f'{esc(topic.name)} ({esc(", ".join(topic.holdings))})</option>'
        for topic in topics
    )
    error_html = f'<span class="error" id="inject-topic-error">{esc(error)}</span>' if error else ""
    return (
        '<div class="notice notice-warn"><h2>Test injection</h2>'
        "<p>Feeds a headline into the live pipeline as if the Scout had found it. "
        "Corroboration, research, prices and alerts are real, and You.com calls cost money. "
        "Injected Sightings are labelled as injected.</p>"
        f"{_form_error_summary(state, form_id)}{_form_open(form_id, 'inject_headline', '/')}"
        f'{_input(form_id, "headline", "Headline", "Chile lithium mine strike cuts supply", state)}'
        f'<div class="field"><label for="inject-topic">Watch Topic</label>{error_html}'
        f'<select id="inject-topic" name="topic">{options}</select></div>'
        f'{_input(form_id, "url", "Article address (optional)", "", state, hint="A real article lets the Receipt check verify quotes against it.")}'
        "<button>Inject headline</button></form></div>"
    )


def overview(
    dash: Dashboard, *, holding: str = "", stance: str = "", notice: str = "", state: FormState | None = None
) -> str:
    cards = dash.latest_per_event()
    if holding:
        cards = [card for card in cards if any(impact.ticker == holding for impact in card.impacts)]
    if stance:
        cards = [
            card
            for card in cards
            if any(impact.stance == stance and (not holding or impact.ticker == holding) for impact in card.impacts)
        ]
    if dash.theses:
        listing = "".join(_thesis_summary(card) for card in cards) or '<p>No Theses match these filters.</p>'
        main = f'<section aria-labelledby="latest"><h1 id="latest">Latest Theses</h1>{_filters(dash, holding, stance)}{listing}</section>'
    else:
        main = f'<section aria-labelledby="latest"><h1 id="latest">Latest Theses</h1>{_empty_overview(dash)}</section>'
    dismissed = "".join(
        f"<li>{_ext_link(item['url'], item['title'])}: {esc(item['reason'])}</li>" for item in reversed(dash.dismissed[-50:])
    )
    dismissed_html = (
        f'<section id="dismissed" class="panel"><details><summary>Dismissed Sightings ({len(dash.dismissed)})</summary>'
        f'<ul class="wrap-any">{dismissed or "<li>None yet.</li>"}</ul></details></section>'
    )
    demo = ""
    if dash.demo:
        demo = (
            '<div class="notice notice-warn"><h2>Demo mode</h2>'
            "<p>Inputs are staged: no live news, recorded You.com responses and staged prices. "
            "The Supervisor, Researcher, Analyst, Receipt checks and alerts run for real.</p>"
            f'{_form_open("demo", "demo_inject", "/")}<button>Inject breaking news: Chile lithium mine strike</button></form></div>'
        )
    body = (
        f"{demo}{_inject_panel(dash, state)}{_failure_banner(dash, '/')}"
        f'<div class="layout"><div>{main}{dismissed_html}</div>{_aside(dash)}</div>'
    )
    return page(dash, "Overview", "/", body, notice=notice)


# ----- Thesis detail --------------------------------------------------------


def thesis_detail(dash: Dashboard, thesis_id: str, *, notice: str = "") -> str | None:
    card = dash.thesis(thesis_id)
    if card is None:
        return None
    versions = dash.versions(card.event_id)
    latest = versions[-1]
    event = dash.event(card.event_id) or {}
    parts = ['<p><a href="/">← Overview</a></p>']
    parts.append(f'<article id="thesis-{esc(card.id)}" data-thesis="{esc(card.id)}" data-effect="{esc(card.color)}">')
    parts.append(f'<h1 class="headline">{esc(card.headline)}</h1>')
    meta = f"Published {esc(when(card.created))} · Version {card.version} of {latest.version}"
    if card.previous_id:
        meta += f' · <a href="/thesis/{esc(card.previous_id)}">Previous version</a>'
    parts.append(f'<p class="meta">{meta}</p>')
    if latest.id != card.id:
        parts.append(
            f'<div class="notice notice-warn"><p>This is an older version. '
            f'<a href="/thesis/{esc(latest.id)}">Read version {latest.version}</a>.</p></div>'
        )
    if card.version > 1:
        items = "".join(f"<li>{esc(change)}</li>" for change in card.changes) or "<li>No changes were recorded.</li>"
        parts.append(f'<section class="panel"><h2>What changed since version {card.version - 1}</h2><ul class="changes">{items}</ul></section>')
    parts.append(
        '<section class="panel" aria-labelledby="impacts"><h2 id="impacts">Holding Impacts</h2>'
        f"{_impact_table(card, detail=True)}"
        f'<p class="disclaimer">{esc(card.disclaimer)} Actions are suggestions; nothing is traded.</p>'
        f"{_effect_line(card)}</section>"
    )
    evidence = []
    for item in card.evidence:
        evidence.append(
            '<div class="receipt">'
            f"<p><strong>{esc(item.text)}</strong></p>"
            f"<blockquote>“{esc(item.passage)}”</blockquote>"
            f'<p class="source">{esc(item.host)} · retrieved {esc(item.retrieved)} · {_ext_link(item.url, "Open source")}</p>'
            "</div>"
        )
    parts.append(
        '<section class="panel" aria-labelledby="claims"><h2 id="claims">Claims and Receipts</h2>'
        + ("".join(evidence) if evidence else "<p>No claim in this Thesis has a Receipt.</p>")
        + '<p class="hint">A Receipt shows the quoted passage appears word-for-word on the source page. '
        "It does not by itself prove the effect the Analyst draws from it.</p></section>"
    )
    inferences = "".join(
        f'<div class="inference"><span class="label">Inference</span><p>{esc(text)}</p></div>' for text in card.inferences
    )
    missing = "".join(f"<li>{esc(item)}</li>" for item in card.missing)
    parts.append(
        '<section class="panel" aria-labelledby="inferences"><h2 id="inferences">Inferences and missing evidence</h2>'
        "<p class=\"hint\">Inferences are the Analyst's reasoning. They have no Receipt.</p>"
        + (inferences or "<p>No Inferences.</p>")
        + (f"<h3>Missing evidence</h3><ul>{missing}</ul>" if missing else "<p>No missing evidence was recorded.</p>")
        + "</section>"
    )
    parts.append(_timeline(dash, card, event, versions))
    parts.append(_dossier(card, event))
    parts.append("</article>")
    return page(dash, card.headline, f"/thesis/{card.id}", "".join(parts), notice=notice)


def _timeline(dash: Dashboard, card: ThesisCard, event: dict, versions: list[ThesisCard]) -> str:
    entries: list[tuple[str, str]] = []
    if event:
        entries.append((event["opened_at"], f"Event opened. Corroboration: {esc(event['corroboration'])}."))
    for sighting in dash.sightings:
        if sighting["event_id"] == card.event_id:
            entries.append(
                (
                    sighting.get("posted_at", ""),
                    f"Sighting from {esc(sighting['source'])}: {_ext_link(sighting['url'], sighting['title'])}",
                )
            )
    for version in versions:
        label = f'<a href="/thesis/{esc(version.id)}">Thesis version {version.version}</a>'
        if version.id == card.id:
            label += " (this page)"
        entries.append((version.created, label))
    if event and event.get("status") == "closed":
        entries.append((event["last_sighting_at"], "Event closed after 72 hours without a new Sighting."))
    entries.sort(key=lambda entry: entry[0])
    items = "".join(f"<li><span class=\"hint\">{esc(when(at))}</span><br>{text}</li>" for at, text in entries)
    return f'<section class="panel" aria-labelledby="timeline"><h2 id="timeline">Event timeline</h2><ul class="timeline">{items}</ul></section>'


def _dossier(card: ThesisCard, event: dict) -> str:
    dossiers = event.get("dossiers") or []
    chosen = next((item for item in dossiers if item.get("version") == card.dossier_version), None)
    if chosen is None and dossiers:
        chosen = dossiers[-1]
    if chosen is None:
        return '<section class="panel"><h2>Dossier</h2><p>No Dossier is available for this Thesis.</p></section>'
    answers = chosen.get("answers") or {}
    rows = "".join(
        f"<dt>{esc(question.capitalize())}</dt><dd>{esc(answers.get(question) or 'Not answered')}</dd>"
        for question in ("exposure", "magnitude", "timing", "counter-evidence")
    )
    return (
        '<section class="panel" aria-labelledby="dossier"><h2 id="dossier">Dossier</h2>'
        f'<p class="meta">Version {esc(chosen.get("version"))} · session {esc(chosen.get("session_id"))} · '
        f"written {esc(when(chosen.get('created_at')))}</p>"
        f'<dl class="kv">{rows}</dl></section>'
    )


# ----- Portfolio and Watch Topics -------------------------------------------


def portfolio(dash: Dashboard, state: FormState | None = None, *, notice: str = "") -> str:
    back = "/portfolio"
    rows = []
    for holding in dash.holdings:
        form_id = f"holding-{holding.ticker}"
        rows.append(
            "<tr>"
            f'<td data-label="Holding"><span data-ticker="{esc(holding.ticker)}">{esc(holding.ticker)}</span></td>'
            f'<td data-label="">{_form_open(form_id, "add_holding", back)}'
            f'<input type="hidden" name="ticker" value="{esc(holding.ticker)}">'
            f'<div class="row">{_input(form_id, "weight", f"Weight of {holding.ticker} (%)", f"{holding.weight * 100:g}", state, inputmode="decimal", size=6)}'
            '<button class="secondary">Save</button></div></form></td>'
            f'<td data-label="">{_form_open(f"remove-{holding.ticker}", "remove_holding", back, extra=INLINE)}'
            f'<input type="hidden" name="ticker" value="{esc(holding.ticker)}">'
            f'<button class="quiet">Remove {esc(holding.ticker)}</button></form></td>'
            "</tr>"
        )
    total = dash.total_weight
    if total > 1.0001:
        allocation = f'<div class="notice notice-warn"><p>Total allocated is {esc(percent(total))}, above 100%. Theses still run; check your weights.</p></div>'
    elif dash.holdings and total < 0.9999:
        allocation = f'<p class="hint">Total allocated: {esc(percent(total))}. The rest counts as unallocated.</p>'
    else:
        allocation = f'<p class="hint">Total allocated: {esc(percent(total))}.</p>' if dash.holdings else ""
    table = (
        '<table class="stack"><thead><tr><th scope="col">Holding</th><th scope="col">Weight</th><th scope="col"><span class="sr-only">Remove</span></th></tr></thead>'
        f'<tbody id="holdings">{"".join(rows)}</tbody></table>'
        if rows
        else '<p id="holdings">No Holdings yet. Add your first one below.</p>'
    )
    add = (
        f'{_form_error_summary(state, "add-holding")}{_form_open("add-holding", "add_holding", back)}'
        '<div class="row">'
        f'{_input("add-holding", "ticker", "Ticker", "", state, hint="For example TSLA", required=True, size=8)}'
        f'{_input("add-holding", "weight", "Weight (%)", "", state, hint="0 to 100", inputmode="decimal", required=True, size=6)}'
        '<div class="field"><button>Add Holding</button></div></div></form>'
    )
    holdings_section = (
        '<section class="panel" aria-labelledby="holdings-h" id="holdings-section">'
        '<h1 id="holdings-h">Portfolio</h1>'
        f"{table}{allocation}<h2>Add a Holding</h2>{add}</section>"
    )
    return page(dash, "Portfolio", back, holdings_section + _topics_section(dash, state), notice=notice)


def _topic_feeds(topic: TopicRow) -> str:
    return "\n".join(topic.feeds)


def _topics_section(dash: Dashboard, state: FormState | None) -> str:
    back = "/portfolio"
    tickers = dash.tickers()
    active = []
    for topic in dash.active_topics():
        form_id = f"topic-{topic.id}"
        options = sorted(set(tickers) | set(topic.holdings))
        active.append(
            f'<li class="panel" data-topic="{esc(topic.id)}" data-status="active">'
            f"<h3>{esc(topic.name)}</h3>"
            f'<p class="hint">Affects {esc(", ".join(topic.holdings) or "no Holdings")}. '
            f'Feeds: {esc(str(len(topic.feeds)) + " extra" if topic.feeds else "Google News and SEC filings only")}.</p>'
            f"<details><summary>Edit Holdings and feeds</summary>{_form_error_summary(state, form_id)}"
            f'{_form_open(form_id, "update_topic", back)}<input type="hidden" name="id" value="{esc(topic.id)}">'
            f'{_checks(form_id, "holdings", "Affected Holdings", options, set(topic.holdings), state)}'
            f'{_textarea(form_id, "feeds", "Extra news feeds", _topic_feeds(topic), state, hint="One RSS feed address per line. Leave empty for none.")}'
            "<button>Save Watch Topic</button></form></details></li>"
        )
    suggested = []
    for topic in dash.suggested_topics():
        form_id = f"accept-{topic.id}"
        options = sorted(set(tickers) | set(topic.holdings))
        suggested.append(
            f'<li class="panel" data-topic="{esc(topic.id)}" data-status="suggested">'
            f"{_form_error_summary(state, form_id)}"
            f'{_form_open(form_id, "accept_topic", back)}<input type="hidden" name="id" value="{esc(topic.id)}">'
            f'{_input(form_id, "name", "Watch Topic name", topic.name, state)}'
            f'{_checks(form_id, "holdings", "Affected Holdings", options, set(topic.holdings), state)}'
            '<div class="row"><button>Accept</button></div></form>'
            f'{_form_open(f"reject-{topic.id}", "reject_topic", back, extra=INLINE)}'
            f'<input type="hidden" name="id" value="{esc(topic.id)}"><button class="quiet">Reject {esc(topic.name)}</button></form>'
            "</li>"
        )
    rejected = dash.rejected_topics()
    rejected_html = ""
    if rejected:
        items = "".join(
            f'<li data-topic="{esc(topic.id)}" data-status="rejected">{esc(topic.name)}</li>' for topic in rejected
        )
        rejected_html = f"<details><summary>Rejected suggestions ({len(rejected)})</summary><ul>{items}</ul></details>"
    add = (
        f'{_form_error_summary(state, "add-topic")}{_form_open("add-topic", "add_topic", back)}'
        f'{_input("add-topic", "name", "Name", "", state, hint="For example: lithium supply", required=True)}'
        f'{_checks("add-topic", "holdings", "Affected Holdings", tickers, set(), state)}'
        f'{_textarea("add-topic", "feeds", "Extra news feeds (optional)", "", state, hint="One RSS feed address per line.")}'
        "<button>Add Watch Topic</button></form>"
    )
    return (
        '<section aria-labelledby="topics-h" id="topics">'
        '<h1 id="topics-h">Watch Topics</h1>'
        "<p>The Scout searches Google News for each active Watch Topic and checks SEC filings for its Holdings every 5 minutes.</p>"
        f'<h2>Active ({len(active)})</h2><ul class="topic-list" style="list-style:none;padding:0">{"".join(active) or "<li>No active Watch Topics.</li>"}</ul>'
        f'<h2>Suggestions ({len(suggested)})</h2>'
        f'{_form_open("suggest", "suggest_topics", back)}<p><button class="secondary">Suggest from Holdings</button></p></form>'
        f'<ul style="list-style:none;padding:0">{"".join(suggested) or "<li>No suggestions waiting.</li>"}</ul>'
        f"{rejected_html}"
        f'<section class="panel"><h2>Add a Watch Topic</h2>{add}</section>'
        "</section>"
    )


# ----- Track Record -------------------------------------------------------


def track_record(dash: Dashboard, *, notice: str = "") -> str:
    panels = []
    for horizon in dash.horizons:
        rate = f"{horizon.correct / horizon.graded:.0%}" if horizon.graded else "—"
        panels.append(
            f'<div class="panel" data-horizon="{horizon.days}"><h2>{horizon.days} trading day{"s" if horizon.days != 1 else ""}</h2>'
            f'<p class="stat">{esc(rate)}</p>'
            f"<p>{horizon.correct} correct of {horizon.graded} graded · {horizon.pending} pending</p></div>"
        )
    rows = "".join(
        "<tr>"
        f'<td data-label="Holding">{esc(grade.ticker)}</td>'
        f'<td data-label="Stance">{_badge(grade.stance)}</td>'
        f'<td data-label="Horizon">{grade.horizon} days</td>'
        f'<td data-label="Price move" class="num">{grade.move:+.2%}</td>'
        f'<td data-label="Result">{"Correct" if grade.correct else "Incorrect"}</td>'
        f'<td data-label="Thesis"><a href="/thesis/{esc(grade.thesis_id)}">{esc(grade.headline or "Open Thesis")}</a></td>'
        "</tr>"
        for grade in dash.grades
    )
    table = (
        '<table class="stack" id="grades"><thead><tr><th scope="col">Holding</th><th scope="col">Stance</th>'
        '<th scope="col">Horizon</th><th scope="col" class="num">Price move</th><th scope="col">Result</th>'
        f'<th scope="col">Thesis</th></tr></thead><tbody>{rows}</tbody></table>'
        if rows
        else '<p id="grades">No Holding Impact has reached a grading date yet.</p>'
    )
    body = (
        '<h1>Track Record</h1>'
        "<p>Each Holding Impact is graded against the Holding's closing price 1, 5 and 20 trading days after its Thesis. "
        "Bull is correct if the price rose, Bear if it fell, Neutral if it moved less than 1%. "
        "The horizons are kept apart because they answer different questions.</p>"
        f'<div class="stats">{"".join(panels)}</div>'
        f'<section class="panel"><h2>Every grade</h2>{table}</section>'
    )
    return page(dash, "Track Record", "/track-record", body, notice=notice)


# ----- Settings -----------------------------------------------------------


def settings(dash: Dashboard, state: FormState | None = None, *, notice: str = "") -> str:
    back = "/settings"
    costs = dash.costs
    stage_rows = "".join(
        "<tr>"
        f'<td data-label="Stage">{esc(STAGE_NAMES.get(name, name))}</td>'
        f'<td data-label="State">{esc(info["state"])}</td>'
        f'<td data-label="Last run">{esc(when(info["last_run_at"]))}</td>'
        f'<td data-label="Last success">{esc(when(info["last_ok_at"]))}</td>'
        f'<td data-label="Detail" class="wrap-any">{esc(info["detail"] or "—")}</td>'
        "</tr>"
        for name, info in sorted(dash.stages.items(), key=lambda item: list(STAGE_NAMES).index(item[0]) if item[0] in STAGE_NAMES else 99)
    )
    stages = (
        '<table class="stack"><thead><tr><th scope="col">Stage</th><th scope="col">State</th><th scope="col">Last run</th>'
        f'<th scope="col">Last success</th><th scope="col">Detail</th></tr></thead><tbody>{stage_rows}</tbody></table>'
        if stage_rows
        else "<p>No stage has run yet.</p>"
    )
    form_id = "settings"
    body = (
        "<h1>Settings</h1>"
        '<section class="panel" id="recording" aria-labelledby="recording-h"><h2 id="recording-h">Recording diagnostics</h2>'
        '<dl class="kv">'
        f"<dt>Recording check</dt><dd>{'Passed' if dash.recording_ok else 'Not passed'}: {esc(dash.recording_detail)}</dd>"
        f"<dt>Runtime mode</dt><dd>{esc(dash.runtime_label)}</dd>"
        f"<dt>Researcher harness</dt><dd>{esc(dash.harness.get('researcher', ''))}</dd>"
        f"<dt>Analyst harness</dt><dd>{esc(dash.harness.get('analyst', ''))}</dd>"
        f"</dl><h3>Pipeline stages</h3>{stages}"
        f'{_form_open("retry-settings", "run_now", back)}<button class="secondary">Run checks now</button></form></section>'
        '<section class="panel" aria-labelledby="spend-h"><h2 id="spend-h">Spend</h2>'
        f'<p id="spend">{esc(costs.month_line())}</p><p class="hint">{esc(costs.lifetime_line())}</p></section>'
        f'<section class="panel" aria-labelledby="prefs-h"><h2 id="prefs-h">Notifications and budget</h2>'
        f'{_form_error_summary(state, form_id)}{_form_open(form_id, "save_settings", back)}'
        f'{_input(form_id, "alert_weight", "Discord alert threshold (%)", f"{dash.alert_weight * 100:g}", state, hint="Push only for high-confidence Bull or Bear Holding Impacts on Holdings at or above this weight.", inputmode="decimal", size=6)}'
        f'{_input(form_id, "you_search_usd", "You.com search price per call ($)", costs.search_rate, state, hint="Leave blank if unknown. Spend and the budget need both prices.", inputmode="decimal", size=8)}'
        f'{_input(form_id, "you_research_usd", "You.com research price per call ($)", costs.research_rate, state, inputmode="decimal", size=8)}'
        f'{_input(form_id, "monthly_budget_usd", "Monthly budget ($)", f"{costs.budget:g}", state, hint="Paid calls stop for the rest of the month once this is reached.", inputmode="decimal", size=8)}'
        "<button>Save settings</button></form></section>"
    )
    return page(dash, "Settings", back, body, notice=notice)


def not_found(dash: Dashboard, what: str) -> str:
    body = f'<h1>Not found</h1><p>{esc(what)}</p><p><a href="/">Back to the Overview</a></p>'
    return page(dash, "Not found", "", body)


__all__ = [
    "FormState",
    "SAVED",
    "hostname",
    "not_found",
    "overview",
    "portfolio",
    "settings",
    "thesis_detail",
    "track_record",
]
