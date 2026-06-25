from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from agent.core import ResearchAgent
from agent.llm import create_llm
from agent.tools.arxiv import ArxivTool
from agent.tools.fred import FREDTool
from agent.tools.wikipedia import WikipediaTool

load_dotenv()

app = FastAPI(title="Research Agent API")


class QuestionRequest(BaseModel):
    question: str


class AnswerResponse(BaseModel):
    answer: str
    sources: list[dict]
    tools_used: list[str]
    steps: int
    duration_ms: float


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/ask", response_model=AnswerResponse)
def ask(req: QuestionRequest):
    try:
        llm = create_llm()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"LLM unavailable: {e}")

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
