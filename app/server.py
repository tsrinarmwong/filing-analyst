"""FastAPI app: a streaming chat endpoint plus a dependency-free chat UI that
shows the agent's work.

    uvicorn app.server:app --port 8000   ->   http://localhost:8000

The UI streams the agent's TRACE live -- each tool call, where the data came
from (the SQL database vs. the 10-K filings), the exact SQL / search query, and
the result -- so you can see it route and reason, then read the grounded answer.

The server starts fine WITHOUT a built index or DB -- those load lazily on the
first query and fail soft with a friendly message instead of a 500.
"""
from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from app.agent import answer_question, stream_question
from app.dashboard import router as dashboard_router

app = FastAPI(title="Filing Analyst")
app.include_router(dashboard_router)


class ChatRequest(BaseModel):
    question: str


@app.post("/api/chat")
def chat(req: ChatRequest) -> dict:
    """Non-streaming JSON: {"question": "..."} -> {"answer": ..., "evidence": [...]}."""
    try:
        result = answer_question(req.question)
    except (FileNotFoundError, RuntimeError) as exc:
        return {"answer": f"⚠️ {exc}", "evidence": []}
    except Exception as exc:  # never surface a raw 500
        return {"answer": f"⚠️ Something went wrong: {exc}", "evidence": []}
    return {"answer": result["answer"], "evidence": result.get("evidence", [])}


@app.post("/api/chat/stream")
def chat_stream(req: ChatRequest) -> StreamingResponse:
    """Server-Sent Events: streams the agent's trace events, then the answer."""
    def gen():
        try:
            for event in stream_question(req.question):
                yield f"data: {json.dumps(event)}\n\n"
        except (FileNotFoundError, RuntimeError) as exc:
            yield f"data: {json.dumps({'type': 'answer', 'text': f'⚠️ {exc}', 'evidence': []})}\n\n"
        except Exception as exc:  # noqa: BLE001
            yield f"data: {json.dumps({'type': 'answer', 'text': f'⚠️ Something went wrong: {exc}', 'evidence': []})}\n\n"
        yield 'data: {"type": "done"}\n\n'

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Filing Analyst</title>
<style>
  :root {
    --bg:#0f1115; --panel:#171a21; --raise:#1d212b; --ink:#e7e9ee; --muted:#9aa3b2;
    --line:#262b36; --accent:#5b9dff; --user:#2b4a7a; --code:#0c0e12;
    --sql:#5b9dff; --search:#b182ff; --ok:#56d19a; --err:#ff6b6b;
  }
  @media (prefers-color-scheme: light) {
    :root { --bg:#f5f6f8; --panel:#ffffff; --raise:#f3f5f9; --ink:#1b1f27; --muted:#5f6878;
            --line:#e4e7ec; --accent:#2563eb; --user:#2563eb; --code:#f2f3f6;
            --sql:#2563eb; --search:#7c3aed; --ok:#128a5a; --err:#c0392b; }
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
         font:15px/1.6 system-ui,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }
  .wrap { max-width:860px; margin:0 auto; height:100vh; display:flex; flex-direction:column; }
  header { padding:20px 22px 14px; border-bottom:1px solid var(--line); }
  header h1 { margin:0; font-size:18px; font-weight:650; letter-spacing:-0.01em; }
  header .htop { display:flex; align-items:baseline; justify-content:space-between; gap:12px; }
  header .navlink { color:var(--accent); text-decoration:none; font-size:13px; font-weight:600; }
  header p { margin:6px 0 0; color:var(--muted); font-size:13px; }
  header b { color:var(--ink); font-weight:600; }
  #log { flex:1; overflow-y:auto; padding:18px 22px; }
  .row { display:flex; margin:14px 0; }
  .row.user { justify-content:flex-end; }
  .stack { display:flex; flex-direction:column; gap:8px; max-width:94%; width:100%; }
  .row.user .stack { align-items:flex-end; }
  .bubble { padding:11px 15px; border-radius:14px; border:1px solid var(--line);
            background:var(--panel); overflow-wrap:anywhere; }
  .user .bubble { background:var(--user); color:#fff; border-color:transparent;
                  white-space:pre-wrap; max-width:88%; }
  .muted { color:var(--muted); }
  .thinking { animation:pulse 1.1s ease-in-out infinite; }
  @keyframes pulse { 0%,100%{opacity:.45} 50%{opacity:1} }

  /* --- agent trace --- */
  .trace { display:flex; flex-direction:column; gap:8px; }
  .status { color:var(--muted); font-size:12.5px; display:flex; align-items:center; gap:7px; }
  .dot { width:7px; height:7px; border-radius:50%; background:var(--accent); }
  .plan { color:var(--muted); font-size:12.5px; font-style:italic;
          border-left:2px solid var(--line); padding-left:9px; }
  .step { border:1px solid var(--line); border-radius:12px; background:var(--raise);
          padding:9px 12px; }
  .step .head { display:flex; align-items:center; gap:8px; flex-wrap:wrap; font-size:12.5px; }
  .tag { font-size:10px; font-weight:800; letter-spacing:.06em; color:#fff;
         border-radius:5px; padding:2px 7px; }
  .tag.sql { background:var(--sql); } .tag.search { background:var(--search); }
  .step .label { font-weight:600; }
  .step .src { color:var(--muted); font-size:11.5px; }
  .step .src::before { content:"→ "; }
  .step pre { margin:8px 0 0; padding:9px 11px; background:var(--code); border-radius:8px;
              overflow-x:auto; font-size:12px;
              font-family:ui-monospace,SFMono-Regular,Menlo,monospace; color:var(--sql); }
  .step .query { margin-top:7px; color:var(--search);
                 font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12.5px; }
  .step .result { margin-top:8px; font-size:12px; color:var(--muted); }
  .step .result .ok { color:var(--ok); font-weight:600; }
  .step .rows { margin-top:5px; font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
                font-size:11.5px; color:var(--muted); white-space:pre; overflow-x:auto; }
  .step .srclist { margin:5px 0 0; padding-left:16px; font-size:11.5px; color:var(--muted); }
  .step .snippet { margin-top:6px; font-size:11.5px; color:var(--muted); font-style:italic; }
  .step.err pre, .step.err .result { color:var(--err); }

  /* --- markdown answer --- */
  .md :first-child { margin-top:0; } .md :last-child { margin-bottom:0; }
  .md p { margin:8px 0; } .md h1,.md h2,.md h3 { margin:12px 0 6px; font-size:15px; }
  .md ul,.md ol { margin:6px 0; padding-left:22px; } .md li { margin:3px 0; }
  .md code { background:var(--code); padding:1px 5px; border-radius:5px;
             font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:90%; }
  .md pre { background:var(--code); padding:10px 12px; border-radius:8px; overflow-x:auto; }
  .md pre code { background:none; padding:0; }
  .md table { border-collapse:collapse; margin:10px 0; font-size:13px; display:block; overflow-x:auto; }
  .md th,.md td { border:1px solid var(--line); padding:5px 9px; text-align:left; }
  .md th { background:var(--code); }

  form { display:flex; gap:8px; padding:14px 22px; border-top:1px solid var(--line); }
  textarea { flex:1; resize:none; padding:11px 13px; border:1px solid var(--line); border-radius:12px;
             font:inherit; background:var(--panel); color:var(--ink); }
  button { padding:0 20px; border:none; border-radius:12px; background:var(--accent); color:#fff;
           font-weight:600; cursor:pointer; }
  button:disabled { opacity:.5; cursor:default; }
  .hint { text-align:center; color:var(--muted); font-size:11px; padding:0 0 12px; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="htop"><h1>Filing Analyst</h1><a class="navlink" href="/dashboard">Dashboard →</a></div>
    <p>Grounded Q&amp;A over <b>NVIDIA, AMD, and Intel</b> 10-K filings. Watch it route between a
       read-only <b>SQL</b> tool (exact figures from <b>financials.db</b>) and <b>semantic search</b>
       over the <b>filing text</b> — every step of its work is shown.</p>
  </header>
  <div id="log"></div>
  <form id="form">
    <textarea id="q" rows="2" placeholder="e.g. How did NVIDIA's data-center revenue change, and what risks does it cite?"></textarea>
    <button id="send" type="submit">Ask</button>
  </form>
  <div class="hint">Enter to send &middot; Shift+Enter for a newline</div>
</div>
<script>
const log = document.getElementById('log');
const form = document.getElementById('form');
const input = document.getElementById('q');
const send = document.getElementById('send');

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}
function addRow(role) {
  const row = el('div', 'row ' + role);
  const stack = el('div', 'stack');
  row.appendChild(stack); log.appendChild(row);
  log.scrollTop = log.scrollHeight;
  return stack;
}
function esc(s) { return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

// --- Safe Markdown subset. Model output is HTML-escaped first; code spans /
// fenced blocks are shielded with @@C#@@ / @@F#@@ sentinels so they never
// collide with content like "$215.9B" or "65.5%". ---
function inline(t) {
  const code = [];
  t = t.replace(/`([^`]+)`/g, (_, c) => { code.push(c); return '@@C' + (code.length-1) + '@@'; });
  t = t.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  t = t.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
  t = t.replace(/@@C(\d+)@@/g, (_, i) => '<code>' + code[+i] + '</code>');
  return t;
}
function cells(line) { return line.replace(/^\s*\|/,'').replace(/\|\s*$/,'').split('|').map(c=>c.trim()); }
const isSep = l => /^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)+\|?\s*$/.test(l);
const isLi = l => /^\s*([-*+]|\d+\.)\s+/.test(l);
function md(src) {
  src = esc(src).replace(/\r\n/g,'\n');
  const fences = [];
  src = src.replace(/```[^\n]*\n([\s\S]*?)```/g, (_, c) => {
    fences.push('<pre><code>' + c.replace(/\n$/,'') + '</code></pre>'); return '@@F' + (fences.length-1) + '@@';
  });
  const L = src.split('\n'); const out = []; let i = 0;
  while (i < L.length) {
    const line = L[i];
    if (!line.trim()) { i++; continue; }
    let m = line.match(/^@@F(\d+)@@$/); if (m) { out.push(fences[+m[1]]); i++; continue; }
    m = line.match(/^(#{1,3})\s+(.*)$/); if (m) { out.push('<h'+m[1].length+'>'+inline(m[2])+'</h'+m[1].length+'>'); i++; continue; }
    if (line.includes('|') && i+1 < L.length && isSep(L[i+1])) {
      const th = cells(line).map(c=>'<th>'+inline(c)+'</th>').join(''); i += 2; const rows = [];
      while (i < L.length && L[i].includes('|') && L[i].trim()) { rows.push('<tr>'+cells(L[i]).map(c=>'<td>'+inline(c)+'</td>').join('')+'</tr>'); i++; }
      out.push('<table><thead><tr>'+th+'</tr></thead><tbody>'+rows.join('')+'</tbody></table>'); continue;
    }
    if (isLi(line)) {
      const ol = /^\s*\d+\.\s+/.test(line); const items = [];
      while (i < L.length && isLi(L[i])) { items.push('<li>'+inline(L[i].replace(/^\s*([-*+]|\d+\.)\s+/,''))+'</li>'); i++; }
      out.push(ol ? '<ol>'+items.join('')+'</ol>' : '<ul>'+items.join('')+'</ul>'); continue;
    }
    const para = [];
    while (i < L.length && L[i].trim() && !isLi(L[i]) && !/^(#{1,3})\s+/.test(L[i]) && !/^@@F(\d+)@@$/.test(L[i])) { para.push(L[i]); i++; }
    out.push('<p>'+inline(para.join('\n')).replace(/\n/g,'<br>')+'</p>');
  }
  return out.join('\n').replace(/@@F(\d+)@@/g, (_, k) => fences[+k]);
}

// --- trace rendering ---
function toolCard(ev) {
  const step = el('div', 'step');
  const head = el('div', 'head');
  const isSql = ev.tool === 'query_financials';
  head.appendChild(el('span', 'tag ' + (isSql ? 'sql' : 'search'), isSql ? 'SQL' : 'SEARCH'));
  head.appendChild(el('span', 'label', ev.label || ev.tool));
  if (ev.source) head.appendChild(el('span', 'src', ev.source));
  step.appendChild(head);
  if (isSql) {
    step.appendChild(el('pre', null, ev.sql || '(no query)'));
  } else {
    const filt = [ev.company, ev.year ? 'FY' + ev.year : null].filter(Boolean);
    step.appendChild(el('div', 'query', '"' + (ev.query || '') + '"' + (filt.length ? '  [' + filt.join(', ') + ']' : '')));
  }
  return step;
}
function fillResult(step, ev) {
  if (!step) return;
  const r = el('div', 'result');
  if (ev.tool === 'query_financials') {
    step.classList.toggle('err', !!ev.error);
    if (ev.error) { r.textContent = '✗ ' + ev.error; step.appendChild(r); return; }
    const n = ev.row_count || 0;
    r.innerHTML = '<span class="ok">✓ ' + n + ' row' + (n===1?'':'s') + '</span> &nbsp;' +
                  (ev.columns||[]).map(esc).join(', ');
    step.appendChild(r);
    if ((ev.preview||[]).length) {
      const rows = ev.preview.map(row => Object.entries(row).map(([k,v]) => k+'='+v).join('   '));
      step.appendChild(el('div', 'rows', rows.join('\n')));
    }
  } else {
    const srcs = ev.sources || [];
    r.innerHTML = '<span class="ok">✓ ' + srcs.length + ' passage' + (srcs.length===1?'':'s') + '</span>';
    step.appendChild(r);
    const ul = el('ul', 'srclist');
    srcs.forEach(s => ul.appendChild(el('li', null,
      s.ticker + ' FY' + s.fiscal_year + ' · ' + (s.section||'') + ' · chunk ' + s.chunk + ' · ' + s.score)));
    step.appendChild(ul);
    if (ev.top_snippet) step.appendChild(el('div', 'snippet', ev.top_snippet));
  }
}

let busy = false;
async function ask() {
  const question = input.value.trim(); if (!question || busy) return;
  busy = true; send.disabled = true;
  addRow('user').appendChild(Object.assign(el('div','bubble'), { textContent: question }));
  input.value = '';

  const stack = addRow('bot');
  const trace = el('div', 'trace'); stack.appendChild(trace);
  const status = el('div', 'status thinking');
  status.appendChild(el('span', 'dot')); status.appendChild(el('span', null, 'Planning the approach…'));
  trace.appendChild(status);
  const answer = el('div', 'bubble md'); answer.style.display = 'none'; stack.appendChild(answer);
  let lastStep = null;
  const setStatus = t => { status.lastChild.textContent = t; };

  function handle(ev) {
    if (ev.type === 'plan') {
      const p = el('div', 'plan', ev.text); trace.insertBefore(p, status); setStatus('Working…');
    } else if (ev.type === 'tool_call') {
      lastStep = toolCard(ev); trace.insertBefore(lastStep, status);
      setStatus(ev.tool === 'query_financials' ? 'Querying the database…' : 'Searching the filings…');
    } else if (ev.type === 'tool_result') {
      fillResult(lastStep, ev);
    } else if (ev.type === 'synthesizing') {
      setStatus('Synthesizing the answer…');
    } else if (ev.type === 'answer') {
      status.remove();
      answer.style.display = ''; answer.innerHTML = md(ev.text || '(no answer)');
    }
    log.scrollTop = log.scrollHeight;
  }

  try {
    const res = await fetch('/api/chat/stream', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question }) });
    const reader = res.body.getReader(); const dec = new TextDecoder();
    let buf = '';
    while (true) {
      const { done, value } = await reader.read(); if (done) break;
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        const chunk = buf.slice(0, idx); buf = buf.slice(idx + 2);
        if (!chunk.startsWith('data:')) continue;
        const ev = JSON.parse(chunk.slice(5).trim());
        if (ev.type !== 'done') handle(ev);
      }
    }
    if (status.isConnected) status.remove();
  } catch (err) {
    setStatus('Error: ' + err); status.classList.remove('thinking');
  } finally {
    busy = false; send.disabled = false; log.scrollTop = log.scrollHeight;
  }
}
form.addEventListener('submit', e => { e.preventDefault(); ask(); });
input.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(); } });
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return PAGE
