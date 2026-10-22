"""FastAPI app: a JSON chat endpoint plus a dependency-free chat UI.

    uvicorn app.server:app --port 8000   ->   http://localhost:8000

The server starts fine WITHOUT a built index or DB -- those load lazily on the
first query and fail soft with a friendly message instead of a 500.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.agent import answer_question

app = FastAPI(title="Filing Analyst")


class ChatRequest(BaseModel):
    question: str


@app.post("/api/chat")
def chat(req: ChatRequest) -> dict:
    """Contract: {"question": "..."} in, {"answer": "...", "evidence": [...]} out."""
    try:
        result = answer_question(req.question)
    except FileNotFoundError as exc:          # index / DB not built yet
        return {"answer": f"⚠️ {exc}", "evidence": []}
    except RuntimeError as exc:                # e.g. missing API key
        return {"answer": f"⚠️ {exc}", "evidence": []}
    except Exception as exc:                   # never surface a raw 500 to the UI
        return {"answer": f"⚠️ Something went wrong: {exc}", "evidence": []}
    return {"answer": result["answer"], "evidence": result.get("evidence", [])}


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Filing Analyst</title>
<style>
  :root {
    --bg:#0f1115; --panel:#171a21; --ink:#e7e9ee; --muted:#9aa3b2;
    --line:#262b36; --accent:#5b9dff; --user:#2b4a7a; --code:#0c0e12; --err:#ff6b6b;
  }
  @media (prefers-color-scheme: light) {
    :root { --bg:#f5f6f8; --panel:#ffffff; --ink:#1b1f27; --muted:#5f6878;
            --line:#e4e7ec; --accent:#2563eb; --user:#2563eb; --code:#f2f3f6; --err:#c0392b; }
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
         font:15px/1.6 system-ui,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }
  .wrap { max-width:820px; margin:0 auto; height:100vh; display:flex; flex-direction:column; }
  header { padding:20px 22px 14px; border-bottom:1px solid var(--line); }
  header h1 { margin:0; font-size:18px; font-weight:650; letter-spacing:-0.01em; }
  header p { margin:6px 0 0; color:var(--muted); font-size:13px; }
  header b { color:var(--ink); font-weight:600; }
  #log { flex:1; overflow-y:auto; padding:18px 22px; }
  .row { display:flex; margin:14px 0; }
  .row.user { justify-content:flex-end; }
  .stack { display:flex; flex-direction:column; gap:8px; max-width:92%; }
  .bubble { padding:11px 15px; border-radius:14px; border:1px solid var(--line);
            background:var(--panel); overflow-wrap:anywhere; }
  .user .bubble { background:var(--user); color:#fff; border-color:transparent; white-space:pre-wrap; }
  .muted { color:var(--muted); }
  .thinking { animation:pulse 1.1s ease-in-out infinite; }
  @keyframes pulse { 0%,100%{opacity:.5} 50%{opacity:1} }
  details.ev { font-size:12.5px; color:var(--muted); border-top:1px dashed var(--line); padding-top:6px; }
  details.ev summary { cursor:pointer; user-select:none; color:var(--accent); }
  .ev-item { margin:8px 0 0; padding-left:10px; border-left:2px solid var(--line); }
  .tag { display:inline-block; font-size:10.5px; font-weight:700; letter-spacing:.03em;
         color:#fff; background:var(--muted); border-radius:5px; padding:1px 6px; margin-right:6px; }
  .ev-item pre { margin:6px 0 0; padding:8px 10px; background:var(--code); border-radius:8px;
                 overflow-x:auto; font-size:12px; }
  .ev-item ul { margin:6px 0 0; padding-left:18px; }
  .ev-err { color:var(--err); margin-top:4px; }
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
    <h1>Filing Analyst</h1>
    <p>Grounded Q&amp;A over <b>NVIDIA, AMD, and Intel</b> 10-K filings. Routes to a read-only
       <b>SQL</b> tool for exact figures and <b>semantic search</b> over filing text for narrative,
       and shows the evidence behind every answer.</p>
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

function addRow(role) {
  const row = document.createElement('div'); row.className = 'row ' + role;
  const stack = document.createElement('div'); stack.className = 'stack';
  const bubble = document.createElement('div'); bubble.className = 'bubble';
  stack.appendChild(bubble); row.appendChild(stack); log.appendChild(row);
  log.scrollTop = log.scrollHeight;
  return { bubble, stack };
}

function esc(s) { return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

// Safe Markdown subset. ALL model output is HTML-escaped first, then a limited
// set of constructs is re-applied -- no raw HTML can get through. Code spans and
// fenced blocks are shielded with @@C#@@ / @@F#@@ sentinels so they never
// collide with real content like "$215.9B" or "65.5%".
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
    fences.push('<pre><code>' + c.replace(/\n$/,'') + '</code></pre>');
    return '@@F' + (fences.length-1) + '@@';
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
    while (i < L.length && L[i].trim() && !isLi(L[i]) && !/^(#{1,3})\s+/.test(L[i]) && !/^@@F\d+@@$/.test(L[i])) { para.push(L[i]); i++; }
    out.push('<p>'+inline(para.join('\n')).replace(/\n/g,'<br>')+'</p>');
  }
  return out.join('\n').replace(/@@F(\d+)@@/g, (_, k) => fences[+k]);
}

function renderEvidence(stack, evidence) {
  if (!Array.isArray(evidence) || !evidence.length) return;
  const det = document.createElement('details'); det.className = 'ev';
  const sum = document.createElement('summary');
  sum.textContent = 'Evidence · ' + evidence.length + ' tool call' + (evidence.length>1?'s':'');
  det.appendChild(sum);
  evidence.forEach(ev => {
    const item = document.createElement('div'); item.className = 'ev-item';
    if (ev.tool === 'query_financials') {
      item.innerHTML = '<span class="tag">SQL</span>';
      const pre = document.createElement('pre'); pre.textContent = ev.sql || '(no query)'; item.appendChild(pre);
      if (ev.error) { const e = document.createElement('div'); e.className='ev-err'; e.textContent='error: '+ev.error; item.appendChild(e); }
    } else if (ev.tool === 'search_filings') {
      item.innerHTML = '<span class="tag">SEARCH</span>';
      item.appendChild(document.createTextNode(ev.query || ''));
      const ul = document.createElement('ul');
      (ev.sources || []).forEach(s => {
        const li = document.createElement('li');
        li.textContent = s.ticker + ' FY' + s.fiscal_year + ' · ' + (s.section||'') + ' · chunk ' + s.chunk + ' · ' + s.score;
        ul.appendChild(li);
      });
      item.appendChild(ul);
    } else { item.textContent = JSON.stringify(ev); }
    det.appendChild(item);
  });
  stack.appendChild(det);
}

let busy = false;
async function ask() {
  const question = input.value.trim(); if (!question || busy) return;
  busy = true; send.disabled = true;
  addRow('user').bubble.textContent = question;
  input.value = '';
  const pending = addRow('bot');
  pending.bubble.className = 'bubble muted thinking';
  const t0 = Date.now();
  const timer = setInterval(() => { pending.bubble.textContent = 'Thinking… ' + Math.floor((Date.now()-t0)/1000) + 's'; }, 400);
  try {
    const res = await fetch('/api/chat', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ question }) });
    const data = await res.json();
    clearInterval(timer);
    pending.bubble.className = 'bubble md';
    pending.bubble.innerHTML = md(data.answer || '(no answer)');
    renderEvidence(pending.stack, data.evidence);
  } catch (err) {
    clearInterval(timer);
    pending.bubble.className = 'bubble ev-err'; pending.bubble.textContent = 'Error: ' + err;
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
