"""FastAPI web interface with Server-Sent Events (SSE) streaming.

Run with: uvicorn app:app --reload
Endpoints:
  GET /           — chat UI (single-page HTML)
  GET /api/info   — version + model metadata
  GET /api/chat?q — SSE stream of agent responses (includes HITL interrupts)
  POST /api/approve — resume after HITL interrupt (approve/edit/reject)
  GET /api/reports — list saved reports
  GET /api/reports/{filename} — read a report
  POST /api/reset — reset conversation
"""

import asyncio
import json
import logging
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse, StreamingResponse
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command

from config import APP_VERSION, Settings
from supervisor import supervisor

Path("logs").mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler("logs/agent.log", maxBytes=5_000_000, backupCount=3),
    ],
)
logger = logging.getLogger("research_agent")

settings = Settings()
app = FastAPI(title="Multi-Agent Research System (MCP+ACP)", version=APP_VERSION)

# Session state
current_thread_id: str = str(uuid.uuid4())
pending_interrupt: dict | None = None


def _format_tool_event(msg) -> dict | None:
    """Extract tool call info from an AIMessage for the SSE stream."""
    if not isinstance(msg, AIMessage) or not msg.tool_calls:
        return None
    tc = msg.tool_calls[0]
    name = tc.get("name", "?")
    args = tc.get("args", {})
    primary = args.get("request", args.get("query", args.get("findings", args.get("filename", ""))))
    if isinstance(primary, str) and len(primary) > 80:
        primary = primary[:80] + "..."
    return {"tool": name, "args": primary or ""}


def _format_tool_result(msg) -> dict | None:
    """Format a ToolMessage for the SSE stream."""
    if not isinstance(msg, ToolMessage):
        return None
    content = msg.content
    detail = content[:150] + "..." if len(content) > 150 else content
    return {"detail": detail}


def _sync_stream(thread_id: str, input_data):
    """Run supervisor.stream() synchronously, yielding SSE-ready events."""
    global pending_interrupt
    sid = thread_id[:8]
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 100}

    for chunk in supervisor.stream(input_data, config=config, stream_mode="updates"):
        for node_name, node_output in chunk.items():
            if node_name == "__interrupt__":
                # HITL interrupt — send to client for approval
                for intr in node_output:
                    pending_interrupt = {
                        "thread_id": thread_id,
                        "value": intr.value,
                    }
                    yield {
                        "type": "interrupt",
                        "filename": intr.value.get("filename", "unknown"),
                        "content_preview": intr.value.get("content_preview", ""),
                    }
                return

            messages = node_output.get("messages", [])
            for msg in messages:
                # Tool call events
                tool_info = _format_tool_event(msg)
                if tool_info:
                    logger.info(
                        "[%s] tool_call: %s(%s)",
                        sid,
                        tool_info["tool"],
                        tool_info.get("args", "")[:60],
                    )
                    yield {"type": "tool_call", **tool_info}

                # Tool result events
                tool_result = _format_tool_result(msg)
                if tool_result:
                    logger.info("[%s] tool_result: %s", sid, tool_result["detail"][:80])
                    yield {"type": "tool_result", **tool_result}

                # Final agent message
                if (
                    isinstance(msg, AIMessage)
                    and msg.content
                    and not getattr(msg, "tool_calls", None)
                ):
                    yield {"type": "message", "content": msg.content}

    yield {"type": "done"}


async def _stream_response(prompt: str):
    """Bridge sync streaming into async SSE."""
    global current_thread_id
    current_thread_id = str(uuid.uuid4())
    sid = current_thread_id

    logger.info("[%s] New session — query: %s", sid[:8], prompt[:80])

    loop = asyncio.get_event_loop()
    queue: asyncio.Queue = asyncio.Queue()

    async def _produce():
        def _run():
            try:
                for event in _sync_stream(
                    sid,
                    {"messages": [("user", prompt)]},
                ):
                    event["session_id"] = sid
                    loop.call_soon_threadsafe(queue.put_nowait, event)
            except Exception as e:
                logger.exception("[%s] Error during agent turn", sid[:8])
                loop.call_soon_threadsafe(
                    queue.put_nowait, {"type": "message", "content": f"Error: {e}"}
                )
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        await loop.run_in_executor(None, _run)

    task = asyncio.create_task(_produce())

    while True:
        try:
            event = await asyncio.wait_for(queue.get(), timeout=5.0)
        except TimeoutError:
            # SSE heartbeat — keeps connection alive during long sub-agent processing
            yield ": heartbeat\n\n"
            continue
        if event is None:
            break
        yield f"data: {json.dumps(event)}\n\n"

    await task
    yield f"data: {json.dumps({'type': 'done'})}\n\n"


async def _stream_resume(decision: dict):
    """Stream the resumed supervisor after HITL decision."""
    global pending_interrupt
    thread_id = pending_interrupt["thread_id"] if pending_interrupt else current_thread_id
    pending_interrupt = None

    loop = asyncio.get_event_loop()
    queue: asyncio.Queue = asyncio.Queue()

    async def _produce():
        def _run():
            try:
                for event in _sync_stream(thread_id, Command(resume=decision)):
                    loop.call_soon_threadsafe(queue.put_nowait, event)
            except Exception as e:
                logger.exception("Error during resume")
                loop.call_soon_threadsafe(
                    queue.put_nowait, {"type": "message", "content": f"Error: {e}"}
                )
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        await loop.run_in_executor(None, _run)

    task = asyncio.create_task(_produce())

    while True:
        try:
            event = await asyncio.wait_for(queue.get(), timeout=5.0)
        except TimeoutError:
            yield ": heartbeat\n\n"
            continue
        if event is None:
            break
        yield f"data: {json.dumps(event)}\n\n"

    await task
    yield f"data: {json.dumps({'type': 'done'})}\n\n"


@app.get("/", response_class=HTMLResponse)
async def index():
    return CHAT_HTML


@app.get("/api/info")
async def info():
    return {
        "app": settings.app_name,
        "version": APP_VERSION,
        "model_powerful": settings.model_powerful,
        "model_fast": settings.model_fast,
    }


@app.post("/api/reset")
async def reset():
    global current_thread_id, pending_interrupt
    current_thread_id = str(uuid.uuid4())
    pending_interrupt = None
    logger.info("Session reset by user")
    return {"status": "ok"}


@app.get("/api/chat")
async def chat(q: str):
    logger.info("User: %s", q)
    return StreamingResponse(
        _stream_response(q),
        media_type="text/event-stream",
    )


@app.post("/api/approve")
async def approve(decision: dict):
    """Resume after HITL interrupt. Body: {"type": "approve"|"edit"|"reject", ...}"""
    if not pending_interrupt:
        raise HTTPException(status_code=400, detail="No pending interrupt")
    logger.info("HITL decision: %s", decision)
    return StreamingResponse(
        _stream_resume(decision),
        media_type="text/event-stream",
    )


@app.get("/api/reports")
async def reports():
    output = Path(settings.output_dir)
    if not output.exists():
        return []
    files = sorted(
        (f for f in output.glob("*.md") if f.name[:1].isdigit()),
        key=lambda f: f.name,
        reverse=True,
    )
    return [{"name": f.name, "size": f.stat().st_size} for f in files]


@app.get("/api/reports/{filename}")
async def report_content(filename: str):
    filepath = Path(settings.output_dir) / filename
    if not filepath.resolve().is_relative_to(Path(settings.output_dir).resolve()):
        raise HTTPException(status_code=403, detail="Access denied")
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="Report not found")
    return PlainTextResponse(filepath.read_text(encoding="utf-8"))


CHAT_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Multi-Agent Research System (MCP+ACP)</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         background: #f7f7f8; color: #1a1a1a; display: flex; height: 100vh; }
  .sidebar { width: 260px; min-width: 260px; background: #1e1e2e; color: #cdd6f4; padding: 20px;
             display: flex; flex-direction: column; gap: 16px; overflow-y: auto; }
  .sidebar h2 { font-size: 16px; color: #89b4fa; }
  .sidebar h3 { font-size: 13px; color: #89b4fa; margin-top: 4px; }
  .sidebar .meta { font-size: 12px; color: #6c7086; }
  .reports-list { display: flex; flex-direction: column; gap: 4px; }
  .report-item { font-size: 11px; color: #a6adc8; background: #313244; border-radius: 6px;
                 padding: 6px 8px; cursor: pointer; word-break: break-all; text-decoration: none; }
  .report-item:hover { background: #45475a; color: #cdd6f4; }
  .main { flex: 1; display: flex; flex-direction: column; }
  .messages { flex: 1; overflow-y: auto; padding: 20px; display: flex;
              flex-direction: column; gap: 12px; }
  .msg { max-width: 80%; padding: 12px 16px; border-radius: 12px; line-height: 1.5; }
  .msg.user { align-self: flex-end; background: #2563eb; color: white; }
  .msg.assistant { align-self: flex-start; background: white; border: 1px solid #e5e7eb; }
  .msg.assistant pre { background: #f3f4f6; padding: 8px; border-radius: 6px;
                       overflow-x: auto; font-size: 13px; margin: 8px 0; }
  .tool-log { font-size: 12px; padding: 4px 16px; font-family: 'SF Mono', Monaco, Consolas, monospace; }
  .tool-log .badge { display: inline-block; padding: 1px 6px; border-radius: 4px; font-size: 10px;
                     font-weight: 600; color: white; margin-right: 4px; }
  .tool-log.delegate_to_planner { color: #8b5cf6; }
  .tool-log.delegate_to_planner .badge { background: #8b5cf6; }
  .tool-log.delegate_to_researcher { color: #2563eb; }
  .tool-log.delegate_to_researcher .badge { background: #2563eb; }
  .tool-log.delegate_to_critic { color: #ea580c; }
  .tool-log.delegate_to_critic .badge { background: #ea580c; }
  .tool-log.save_report { color: #059669; }
  .tool-log.save_report .badge { background: #059669; }
  .tool-log.result { color: #6b7280; font-style: italic; }
  .session-id { font-family: 'SF Mono', Monaco, Consolas, monospace; font-size: 10px;
                color: #6c7086; background: #313244; padding: 2px 6px; border-radius: 4px; }
  .input-bar { padding: 16px 20px; background: white; border-top: 1px solid #e5e7eb;
               display: flex; gap: 8px; }
  .input-bar input { flex: 1; padding: 10px 14px; border: 1px solid #d1d5db;
                     border-radius: 8px; font-size: 14px; outline: none; }
  .input-bar input:focus { border-color: #2563eb; }
  .input-bar button { padding: 10px 20px; background: #2563eb; color: white;
                      border: none; border-radius: 8px; cursor: pointer; font-size: 14px; }
  .input-bar button:disabled { background: #93c5fd; cursor: not-allowed; }
  .btn-reset { width: 100%; padding: 8px; background: #45475a; color: #cdd6f4; border: none;
               border-radius: 8px; cursor: pointer; font-size: 13px; }
  .btn-reset:hover { background: #585b70; }
  /* HITL approval dialog */
  .hitl-dialog { background: #fef3c7; border: 2px solid #f59e0b; border-radius: 12px;
                 padding: 16px; max-width: 100%; align-self: stretch; box-sizing: border-box; }
  .hitl-dialog h4 { color: #92400e; margin-bottom: 8px; }
  .hitl-dialog pre { background: #fff; padding: 8px; border-radius: 6px;
                     font-size: 12px; max-height: 50vh; overflow: auto; margin: 8px 0;
                     white-space: pre-wrap; word-wrap: break-word; }
  .hitl-dialog .actions { display: flex; gap: 8px; margin-top: 12px; }
  .hitl-dialog button { padding: 8px 16px; border: none; border-radius: 6px;
                        cursor: pointer; font-size: 13px; font-weight: 500; }
  .hitl-dialog .btn-approve { background: #059669; color: white; }
  .hitl-dialog .btn-edit { background: #2563eb; color: white; }
  .hitl-dialog .btn-reject { background: #dc2626; color: white; }
  .hitl-dialog textarea { width: 100%; padding: 8px; border: 1px solid #d1d5db;
                          border-radius: 6px; font-size: 13px; resize: vertical;
                          min-height: 60px; display: none; margin-top: 8px; }
</style>
</head>
<body>
<div class="sidebar">
  <h2>Multi-Agent Research (MCP+ACP)</h2>
  <button class="btn-reset" onclick="resetSession()">New Session</button>
  <div class="meta" id="meta">Loading...</div>
  <div class="meta">Session: <span class="session-id" id="session-id">—</span></div>
  <h3>Reports</h3>
  <div class="reports-list" id="reports">Loading...</div>
</div>
<div class="main">
  <div class="messages" id="messages"></div>
  <div class="input-bar">
    <input type="text" id="input" placeholder="Ask a research question..." autofocus />
    <button id="send" onclick="send()">Send</button>
  </div>
</div>
<script>
  const msgs = document.getElementById('messages');
  const input = document.getElementById('input');
  const btn = document.getElementById('send');

  fetch('/api/info').then(r=>r.json()).then(d=>{
    document.getElementById('meta').innerHTML =
      `v${d.version}<br><b>Powerful:</b> ${d.model_powerful}<br><b>Fast:</b> ${d.model_fast}`;
  });

  function loadReports() {
    fetch('/api/reports').then(r=>r.json()).then(files=>{
      const el = document.getElementById('reports');
      if (!files.length) { el.textContent = 'No reports yet'; return; }
      el.innerHTML = files.map(f =>
        `<a class="report-item" href="/api/reports/${encodeURIComponent(f.name)}" target="_blank">${f.name}</a>`
      ).join('');
    });
  }
  loadReports();

  input.addEventListener('keydown', e => { if(e.key==='Enter' && !btn.disabled) send(); });

  function resetSession() {
    fetch('/api/reset', {method:'POST'}).then(r=>r.json()).then(()=>{
      msgs.innerHTML = '';
      loadReports();
      input.focus();
    });
  }

  function addMsg(role, html) {
    const d = document.createElement('div');
    d.className = 'msg ' + role;
    d.innerHTML = html;
    msgs.appendChild(d);
    msgs.scrollTop = msgs.scrollHeight;
    return d;
  }

  const toolLabels = {
    delegate_to_planner: 'PLAN', delegate_to_researcher: 'RESEARCH',
    delegate_to_critic: 'CRITIQUE', save_report: 'SAVE'
  };
  let lastToolName = '';

  function addTool(text, toolName, isResult) {
    const d = document.createElement('div');
    const cls = isResult ? (lastToolName || '') : (toolName || '');
    d.className = 'tool-log ' + cls + (isResult ? ' result' : '');
    if (!isResult && toolName && toolLabels[toolName]) {
      d.innerHTML = `<span class="badge">${toolLabels[toolName]}</span> ${text}`;
      lastToolName = toolName;
    } else {
      d.textContent = text;
    }
    msgs.appendChild(d);
    msgs.scrollTop = msgs.scrollHeight;
  }

  function formatMd(text) {
    return text
      .replace(/```(\\w*)\\n([\\s\\S]*?)```/g, '<pre><code>$2</code></pre>')
      .replace(/\\*\\*(.+?)\\*\\*/g, '<strong>$1</strong>')
      .replace(/\\*(.+?)\\*/g, '<em>$1</em>')
      .replace(/`(.+?)`/g, '<code>$1</code>')
      .replace(/^### (.+)$/gm, '<h4>$1</h4>')
      .replace(/^## (.+)$/gm, '<h3>$1</h3>')
      .replace(/^# (.+)$/gm, '<h2>$1</h2>')
      .replace(/^- (.+)$/gm, '&bull; $1<br>')
      .replace(/\\n/g, '<br>');
  }

  function showHITL(filename, preview) {
    const d = document.createElement('div');
    d.className = 'hitl-dialog';
    d.innerHTML = `
      <h4>Action Requires Approval</h4>
      <p><b>save_report</b> &rarr; ${filename}</p>
      <pre>${preview}</pre>
      <textarea id="hitl-feedback" placeholder="Your feedback (for edit)..."></textarea>
      <div class="actions">
        <button class="btn-approve" onclick="hitlDecision('approve', this)">Approve</button>
        <button class="btn-edit" onclick="toggleFeedback(this)">Edit</button>
        <button class="btn-reject" onclick="hitlDecision('reject', this)">Reject</button>
      </div>`;
    msgs.appendChild(d);
    msgs.scrollTop = msgs.scrollHeight;
    return d;
  }

  function toggleFeedback(btnEl) {
    const dialog = btnEl.closest('.hitl-dialog');
    const ta = dialog.querySelector('textarea');
    if (ta.style.display === 'block') {
      // Submit feedback
      const feedback = ta.value.trim();
      if (!feedback) { ta.focus(); return; }
      hitlDecision('edit', btnEl, feedback);
    } else {
      ta.style.display = 'block';
      ta.focus();
      btnEl.textContent = 'Send Feedback';
    }
  }

  function hitlDecision(type, btnEl, feedback) {
    const dialog = btnEl.closest('.hitl-dialog');
    dialog.querySelectorAll('button').forEach(b => b.disabled = true);

    const decision = { type };
    if (type === 'edit') decision.feedback = feedback || '';
    if (type === 'reject') decision.message = 'User rejected';

    const statusEl = document.createElement('p');
    statusEl.innerHTML = `<em>${type === 'approve' ? 'Approved!' : type === 'edit' ? 'Sending feedback...' : 'Rejected.'}</em>`;
    dialog.appendChild(statusEl);

    const el = addMsg('assistant', '<em>Processing...</em>');

    fetch('/api/approve', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(decision),
    }).then(r => {
      const reader = r.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      function read() {
        reader.read().then(({done, value}) => {
          if (done) { btn.disabled = false; input.focus(); loadReports(); return; }
          buffer += decoder.decode(value, {stream: true});
          const lines = buffer.split('\\n');
          buffer = lines.pop();
          for (const line of lines) {
            if (!line.startsWith('data: ')) continue;
            const d = JSON.parse(line.slice(6));
            if (d.type === 'message') el.innerHTML = formatMd(d.content);
            if (d.type === 'tool_call') addTool(`${d.tool}(${d.args || ''})`, d.tool, false);
            if (d.type === 'tool_result') addTool(`  \\u2190 ${d.detail}`, '', true);
            if (d.type === 'interrupt') showHITL(d.filename, d.content_preview);
            if (d.type === 'done') { btn.disabled = false; input.focus(); loadReports(); }
          }
          read();
        });
      }
      read();
    });
  }

  async function send() {
    const q = input.value.trim();
    if (!q) return;
    input.value = '';
    btn.disabled = true;
    addMsg('user', q);
    const el = addMsg('assistant', '<em>Researching...</em>');

    const es = new EventSource('/api/chat?q=' + encodeURIComponent(q));

    es.onmessage = e => {
      const d = JSON.parse(e.data);
      if (d.session_id) document.getElementById('session-id').textContent = d.session_id.slice(0,8);
      if (d.type === 'message') el.innerHTML = formatMd(d.content);
      if (d.type === 'tool_call') addTool(`${d.tool}(${d.args || ''})`, d.tool, false);
      if (d.type === 'tool_result') addTool(`  \\u2190 ${d.detail}`, '', true);
      if (d.type === 'interrupt') { es.close(); showHITL(d.filename, d.content_preview); return; }
      if (d.type === 'done') { es.close(); btn.disabled = false; input.focus(); loadReports(); }
      msgs.scrollTop = msgs.scrollHeight;
    };
    es.onerror = () => { es.close(); btn.disabled = false; };
  }
</script>
</body>
</html>
"""
