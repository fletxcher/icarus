"""The LangGraph agent: a model node that may call tools, looping until it answers."""

from collections.abc import Mapping

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from api.agent.prompts import SYSTEM_PROMPT


def build_agent(
    models: Mapping[str, BaseChatModel], tools: list[BaseTool], *, default_model: str
) -> CompiledStateGraph:
    """Compiles the agent graph.

    `models` maps model ids to chat models; pick one per run with
    `config={"configurable": {"model": "<id>"}}`.
    """
    bound = {model_id: model.bind_tools(tools) for model_id, model in models.items()}

    async def call_model(state: MessagesState, config: RunnableConfig) -> dict:
        model_id = config.get("configurable", {}).get("model", default_model)
        model = bound.get(model_id, bound[default_model])
        response = await model.ainvoke([SystemMessage(SYSTEM_PROMPT), *state["messages"]], config)
        return {"messages": [response]}

    graph = StateGraph(MessagesState)
    graph.add_node("agent", call_model)
    # Report every tool failure back to the model (the default re-raises anything but
    # ToolException, which would abort the turn); tracebacks are logged by the callbacks.
    graph.add_node("tools", ToolNode(tools, handle_tool_errors=True))
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", tools_condition)
    graph.add_edge("tools", "agent")
    return graph.compile()
