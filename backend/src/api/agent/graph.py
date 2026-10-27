"""The LangGraph agent: a model node that may call tools, looping until it answers."""

from collections.abc import Mapping

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from ..languages import LANGUAGES
from .prompts import build_system_prompt


def build_agent(
    models: Mapping[str, BaseChatModel],
    tools: list[BaseTool],
    *,
    default_model: str,
    system_prompt: str | None = None,
) -> CompiledStateGraph:
    """Compiles the agent graph.

    `models` maps model ids to chat models; pick one per run with
    `config={"configurable": {"model": "<id>"}}`. The default system prompt lists `tools`.
    """
    prompt = SystemMessage(system_prompt or build_system_prompt(tools, unavailable={}))
    bound = {model_id: model.bind_tools(tools) for model_id, model in models.items()}

    async def call_model(state: MessagesState, config: RunnableConfig) -> dict:
        model_id = config.get("configurable", {}).get("model", default_model)
        model = bound.get(model_id, bound[default_model])
        language = config.get("configurable", {}).get("language")
        # The user's chosen reply language comes after the system prompt, per request.
        preface = [prompt]
        if language in LANGUAGES:
            preface.append(
                SystemMessage(
                    f"Reply in {LANGUAGES[language]}, whatever language the user writes in. "
                    "Keep tool names, units and code as they are."
                )
            )
        response = await model.ainvoke([*preface, *state["messages"]], config)
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
