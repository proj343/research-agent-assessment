from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from agent.core import ResearchAgent
from agent.llm import create_llm
from agent.tools.arxiv import ArxivTool
from agent.tools.fred import FREDTool
from agent.tools.wikipedia import WikipediaTool

load_dotenv()


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host


limiter = Limiter(key_func=_client_ip)
app = FastAPI(title="Research Agent API")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


class QuestionRequest(BaseModel):
    question: str


class AnswerResponse(BaseModel):
    answer: str
    sources: list[dict]
    tools_used: list[str]
    steps: int
    duration_ms: float


_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Research Agent</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: #f5f5f5; color: #1a1a1a; min-height: 100vh; }
  .wrap { max-width: 740px; margin: 0 auto; padding: 48px 24px 80px; }
  h1 { font-size: 1.6rem; font-weight: 700; letter-spacing: -0.02em; }
  .sub { color: #555; margin-top: 4px; font-size: 0.95rem; }
  .card { background: #fff; border-radius: 12px; padding: 24px;
          box-shadow: 0 1px 4px rgba(0,0,0,.08); margin-top: 28px; }
  textarea { width: 100%; border: 1.5px solid #ddd; border-radius: 8px;
             padding: 12px 14px; font-size: 1rem; font-family: inherit;
             resize: vertical; min-height: 80px; outline: none;
             transition: border-color .15s; }
  textarea:focus { border-color: #3b6fd4; }
  .examples { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
  .chip { background: #f0f4ff; color: #3b6fd4; border: none; border-radius: 20px;
          padding: 5px 13px; font-size: 0.82rem; cursor: pointer;
          transition: background .15s; }
  .chip:hover { background: #dde8ff; }
  button#submit { margin-top: 14px; background: #3b6fd4; color: #fff;
                  border: none; border-radius: 8px; padding: 11px 28px;
                  font-size: 1rem; font-weight: 600; cursor: pointer;
                  transition: background .15s; }
  button#submit:hover { background: #2d5ab8; }
  button#submit:disabled { background: #94aee0; cursor: not-allowed; }
  #result { margin-top: 28px; display: none; }
  .label { font-size: 0.72rem; font-weight: 700; letter-spacing: .07em;
           text-transform: uppercase; color: #888; margin-bottom: 8px; }
  .answer { font-size: 1rem; line-height: 1.7; white-space: pre-wrap; }
  .sources { margin-top: 20px; }
  .source-item { font-size: 0.88rem; color: #555; padding: 3px 0; }
  .source-item a { color: #3b6fd4; text-decoration: none; }
  .source-item a:hover { text-decoration: underline; }
  .badge { display: inline-block; font-size: 0.72rem; background: #f0f0f0;
           border-radius: 4px; padding: 1px 6px; margin-right: 4px;
           color: #555; font-weight: 600; text-transform: uppercase; }
  .meta { margin-top: 20px; font-size: 0.82rem; color: #999;
          display: flex; gap: 18px; flex-wrap: wrap; }
  .spinner { display: inline-block; width: 18px; height: 18px;
             border: 2px solid #fff; border-top-color: transparent;
             border-radius: 50%; animation: spin .7s linear infinite;
             vertical-align: middle; margin-right: 6px; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .error { color: #c0392b; font-size: 0.95rem; margin-top: 16px; }
</style>
</head>
<body>
<div class="wrap">
  <h1>Research Agent</h1>
  <p class="sub">Banking &amp; finance research assistant — powered by Wikipedia, arXiv, and FRED</p>

  <div class="card">
    <textarea id="q" placeholder="Ask a research question…"></textarea>
    <div class="examples">
      <button class="chip" onclick="fill(this)">What is the Federal Reserve discount rate?</button>
      <button class="chip" onclick="fill(this)">Recent ML papers on credit risk</button>
      <button class="chip" onclick="fill(this)">Current US unemployment rate</button>
      <button class="chip" onclick="fill(this)">What is the yield curve inversion?</button>
    </div>
    <br>
    <button id="submit" onclick="ask()">Research</button>
  </div>

  <div id="result">
    <div class="card">
      <div class="label">Answer</div>
      <div class="answer" id="answer"></div>
      <div class="sources" id="sources"></div>
      <div class="meta" id="meta"></div>
    </div>
  </div>
  <div class="error" id="error"></div>
</div>

<script>
function fill(btn) {
  document.getElementById('q').value = btn.textContent;
}

async function ask() {
  const q = document.getElementById('q').value.trim();
  if (!q) return;
  const btn = document.getElementById('submit');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>Researching…';
  document.getElementById('result').style.display = 'none';
  document.getElementById('error').textContent = '';

  try {
    const res = await fetch('/ask', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({question: q})
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(res.status === 429 ? 'Rate limit reached — please wait a moment.' : text);
    }
    const data = await res.json();

    document.getElementById('answer').textContent = data.answer;

    const srcEl = document.getElementById('sources');
    if (data.sources.length) {
      srcEl.innerHTML = '<div class="label" style="margin-top:20px">Sources</div>' +
        data.sources.map(s =>
          `<div class="source-item"><span class="badge">${s.type}</span>` +
          (s.url ? `<a href="${s.url}" target="_blank">${s.title}</a>` : s.title) +
          '</div>'
        ).join('');
    } else { srcEl.innerHTML = ''; }

    document.getElementById('meta').innerHTML =
      `<span>Tools: ${data.tools_used.join(', ') || 'none'}</span>` +
      `<span>Steps: ${data.steps}</span>` +
      `<span>Time: ${(data.duration_ms/1000).toFixed(1)}s</span>`;

    document.getElementById('result').style.display = 'block';
  } catch(e) {
    document.getElementById('error').textContent = e.message;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Research';
  }
}

document.getElementById('q').addEventListener('keydown', e => {
  if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) ask();
});
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def index():
    return _HTML


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/ask", response_model=AnswerResponse)
@limiter.limit("20/hour")
def ask(request: Request, req: QuestionRequest):
    llm = create_llm()
    tools = [WikipediaTool(), ArxivTool(), FREDTool()]
    agent = ResearchAgent(tools=tools, llm=llm)
    response = agent.run(req.question)
    return AnswerResponse(
        answer=response.answer,
        sources=response.sources,
        tools_used=response.tools_used,
        steps=len(response.steps),
        duration_ms=response.total_duration_ms,
    )
