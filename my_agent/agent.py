from google.adk.agents.llm_agent import Agent
from my_agent.instructions import rag_instruction
from my_agent.retrieval_tool import search_report

def make_agent(model: str) -> Agent:
    return Agent(
        model=model,
        name='tanya_dewi',
        description='A helpful assistant for user questions.',
        instruction=rag_instruction,
        tools=[search_report],
    )

root_agent = make_agent("gemini-2.5-flash")

