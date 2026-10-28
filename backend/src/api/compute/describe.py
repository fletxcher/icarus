"""How a simulation is described in words: for the agent's tools and for its notices."""

from datetime import UTC, datetime
from typing import Any

from .schemas import Simulation

HISTORY_POINTS_FOR_MODEL = 40


def result_for_model(result: dict[str, Any]) -> dict[str, Any]:
    """The coefficient history is long; give the model a thinned copy it can still plot."""
    history = result.get("history")
    if not isinstance(history, dict):
        return result
    count = max((len(values) for values in history.values()), default=0)
    step = max(1, -(-count // HISTORY_POINTS_FOR_MODEL))
    return {**result, "history": {name: values[::step] for name, values in history.items()}}


def format_simulation(simulation: Simulation) -> str:
    case = simulation.case.model_dump(exclude_none=True)
    lines = [f"Job {simulation.id}: {simulation.status}", f"Case: {case}"]
    if simulation.result:
        lines.append(f"Result: {result_for_model(simulation.result)}")
    if simulation.error:
        lines.append(f"Error: {simulation.error}")
    return "\n".join(lines)


def duration_text(seconds: float) -> str:
    seconds = max(0, round(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours} h {minutes} min"
    return f"{minutes} min {secs} s" if minutes else f"{secs} s"


def finished_notice(simulation: Simulation) -> str:
    """The note left in a chat when its simulation ends, so the agent knows without asking."""
    took = (simulation.updated_at - simulation.created_at).total_seconds()
    verb = "completed" if simulation.status == "completed" else "failed"
    return (
        f"Simulation {simulation.id} finished: it {verb} after about {duration_text(took)}. "
        "No need to check on it; these are its final details.\n\n" + format_simulation(simulation)
    )


def running_note(simulations: list[Simulation], now: datetime | None = None) -> str | None:
    """What the agent is told each turn about this chat's simulations that are still running."""
    now = now or datetime.now(UTC)
    if not simulations:
        return None
    lines = []
    for simulation in simulations:
        age = duration_text((now - simulation.created_at).total_seconds())
        lines.append(f"- {simulation.id} ({simulation.case.kind}), running for {age}")
    return (
        "Simulations started in this chat that are still running:\n"
        + "\n".join(lines)
        + "\nYou'll be told here when each one finishes. Don't call get_simulation_status to "
        "wait for them; if the user asks, say it's still running."
    )
