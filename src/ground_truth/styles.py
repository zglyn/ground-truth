CSS = """
:root {
  --bg: #F6F3EE;
  --surface: #FFFFFF;
  --text: #1C1915;
  --muted: #625D55;
  --border: #DDD7CE;
  --link: #2457A7;
  --bull: #17633D;
  --bear: #9C2F2F;
  --neutral: #5F5A52;
  --bull-bg: #E6F1EA;
  --bear-bg: #F6E7E5;
  --neutral-bg: #EEEBE6;
  --warn-bg: #FBF1DD;
  --warn: #7A4B00;
  --sans: system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  --serif: Georgia, "Times New Roman", serif;
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font: 16px/1.5 var(--sans);
}
a { color: var(--link); }
a:hover { text-decoration-thickness: 2px; }
:focus-visible { outline: 3px solid var(--link); outline-offset: 2px; }
.skip { position: absolute; left: -9999px; top: 0; background: var(--surface); padding: 8px 16px; }
.skip:focus { left: 16px; top: 8px; z-index: 10; }

.topbar { background: var(--surface); border-bottom: 1px solid var(--border); }
.topbar .wrap { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 32px; padding-top: 16px; padding-bottom: 16px; }
.brand { font-weight: 700; letter-spacing: 0.08em; font-size: 15px; color: var(--text); text-decoration: none; }
.nav { display: flex; flex-wrap: wrap; gap: 4px 24px; margin: 0; padding: 0; list-style: none; }
.nav a { color: var(--muted); text-decoration: none; padding: 4px 0; border-bottom: 2px solid transparent; }
.nav a:hover { color: var(--text); }
.nav a[aria-current="page"] { color: var(--text); border-bottom-color: var(--text); }

.wrap { max-width: 1200px; margin: 0 auto; padding-left: 16px; padding-right: 16px; }
main.wrap { padding-top: 24px; padding-bottom: 64px; }

.statusline { display: flex; flex-wrap: wrap; gap: 4px 24px; align-items: baseline; color: var(--muted); margin: 0 0 8px; }
.statusline strong { color: var(--text); }
.state { display: inline-flex; align-items: center; gap: 8px; }
.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--neutral); display: inline-block; }
.state-running .dot { background: var(--bull); }
.state-attention .dot, .state-paused .dot { background: var(--bear); }
.state-degraded .dot { background: #B7791F; }
.mode { color: var(--muted); font-size: 14px; margin: 0 0 24px; }

h1, h2, h3 { line-height: 1.25; margin: 0 0 8px; }
h1 { font-size: 32px; }
h2 { font-size: 24px; }
h3 { font-size: 18px; }
.headline { font-family: var(--serif); font-weight: 400; }
.meta { color: var(--muted); font-size: 14px; margin: 0 0 16px; }
p { margin: 0 0 16px; }

.layout { display: grid; grid-template-columns: minmax(0, 1fr); gap: 32px; }
@media (min-width: 900px) {
  .layout { grid-template-columns: minmax(0, 1fr) 320px; gap: 48px; }
}

.panel { background: var(--surface); border: 1px solid var(--border); border-radius: 6px; padding: 24px; margin: 0 0 24px; }
.panel > :last-child { margin-bottom: 0; }
.aside h2 { font-size: 18px; }
.aside section { padding: 0 0 24px; margin: 0 0 24px; border-bottom: 1px solid var(--border); }
.aside section:last-child { border-bottom: 0; }

.notice { border-radius: 6px; padding: 16px; margin: 0 0 24px; border: 1px solid var(--border); background: var(--surface); }
.notice-error { border-color: var(--bear); background: var(--bear-bg); }
.notice-warn { border-color: #E3C27A; background: var(--warn-bg); color: var(--warn); }
.notice-ok { border-color: #A9CDB5; background: var(--bull-bg); }
.notice > :last-child { margin-bottom: 0; }

.badge { display: inline-block; font-size: 13px; font-weight: 600; padding: 2px 8px; border-radius: 999px; border: 1px solid currentColor; white-space: nowrap; }
.badge-bull { color: var(--bull); background: var(--bull-bg); }
.badge-bear { color: var(--bear); background: var(--bear-bg); }
.badge-neutral { color: var(--neutral); background: var(--neutral-bg); }

.thesis { border-top: 1px solid var(--border); padding: 24px 0; }
.thesis:first-of-type { border-top: 0; padding-top: 8px; }
.thesis h2 a { color: inherit; text-decoration: none; }
.thesis h2 a:hover { text-decoration: underline; }
.changes { margin: 0 0 16px; padding-left: 20px; }
.effect { font-size: 14px; color: var(--muted); }
.links { display: flex; flex-wrap: wrap; gap: 8px 24px; align-items: baseline; margin-top: 12px; }

table { width: 100%; border-collapse: collapse; margin: 0 0 16px; }
th, td { text-align: left; padding: 8px 12px 8px 0; border-bottom: 1px solid var(--border); vertical-align: top; }
th { font-size: 13px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: 0.04em; }
td.num, th.num { text-align: right; padding-right: 0; }
@media (max-width: 640px) {
  table.stack thead { position: absolute; left: -9999px; }
  table.stack, table.stack tbody, table.stack tr, table.stack td { display: block; width: 100%; }
  table.stack tr { border-bottom: 1px solid var(--border); padding: 8px 0; }
  table.stack td { border: 0; padding: 4px 0; text-align: left; }
  table.stack td::before { content: attr(data-label); display: block; font-size: 12px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: 0.04em; }
}

.filters { display: flex; flex-wrap: wrap; gap: 16px; align-items: flex-end; margin: 0 0 16px; }
form.inline { display: inline; }
.field { display: flex; flex-direction: column; gap: 4px; margin: 0 0 16px; }
.field label, .field legend { font-weight: 600; font-size: 14px; }
.hint { color: var(--muted); font-size: 14px; }
.error { color: var(--bear); font-size: 14px; font-weight: 600; }
input[type="text"], input[type="number"], select, textarea {
  font: inherit; color: var(--text); background: var(--surface);
  border: 1px solid #A9A296; border-radius: 4px; padding: 8px 10px; min-height: 40px; max-width: 100%;
}
textarea { min-height: 96px; width: 100%; }
.field input[type="text"] { align-self: flex-start; width: 100%; max-width: 320px; }
input[aria-invalid="true"], textarea[aria-invalid="true"] { border-color: var(--bear); border-width: 2px; }
fieldset { border: 0; padding: 0; margin: 0 0 16px; }
.checks { display: flex; flex-wrap: wrap; gap: 8px 16px; }
.checks label { font-weight: 400; display: inline-flex; gap: 6px; align-items: center; }
button {
  font: inherit; font-weight: 600; cursor: pointer; min-height: 40px; padding: 8px 16px;
  border-radius: 4px; border: 1px solid var(--link); background: var(--link); color: #FFFFFF;
}
button.secondary { background: var(--surface); color: var(--link); }
button.quiet { background: transparent; border-color: transparent; color: var(--link); padding-left: 4px; padding-right: 4px; text-decoration: underline; }
.row { display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: flex-end; }
.row .field { margin-bottom: 0; }
.formrow { margin: 0 0 24px; }

.receipt { border-left: 3px solid var(--border); padding: 4px 0 4px 16px; margin: 0 0 24px; }
.receipt blockquote { font-family: var(--serif); margin: 8px 0; padding: 0; }
.receipt .source { font-size: 14px; color: var(--muted); overflow-wrap: anywhere; }
.inference { border-left: 3px dashed var(--border); padding: 4px 0 4px 16px; margin: 0 0 16px; }
.label { font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); }
.timeline { list-style: none; padding: 0; margin: 0; }
.timeline li { padding: 0 0 12px 20px; position: relative; overflow-wrap: anywhere; }
.timeline li::before { content: ""; position: absolute; left: 4px; top: 8px; width: 7px; height: 7px; border-radius: 50%; background: var(--muted); }
.disclaimer { font-size: 14px; color: var(--muted); font-style: italic; }
.wrap-any { overflow-wrap: anywhere; }
.stat { font-size: 32px; font-weight: 600; line-height: 1.1; }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin: 0 0 24px; }
dl.kv { display: grid; grid-template-columns: max-content 1fr; gap: 4px 16px; margin: 0 0 16px; }
dl.kv dt { color: var(--muted); }
dl.kv dd { margin: 0; overflow-wrap: anywhere; }
details > summary { cursor: pointer; color: var(--link); font-weight: 600; }
.steps { padding-left: 20px; }
.steps li { margin: 0 0 8px; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
"""
