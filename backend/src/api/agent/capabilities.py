"""Which tools can run for a user, and why not when one can't."""

import shutil

from ..config import Settings

XFOIL_CAPABILITY = "XFOIL airfoil analysis and airfoil search"
DESIGN_CAPABILITY = "CAD design of parts (build123d)"
PYTHON_CAPABILITY = "Running Python code (numpy, scipy, pandas, scikit-learn, Cantera)"
WEB_CAPABILITY = "Web search and reading web pages"

NO_SEARCH_KEY = "no search API key is set on the server (TAVILY_API_KEY or BRAVE_API_KEY)"

NO_WORKSTATION = "the compute host isn't set up on the server"


def xfoil_unavailable(settings: Settings, *, workstation: bool) -> str | None:
    """Why XFOIL can't run with this setup, or None if it can."""
    if settings.xfoil_runner == "ws":
        return None if workstation else NO_WORKSTATION
    if shutil.which(settings.xfoil_path) is None:
        return "XFOIL isn't installed on the server"
    if settings.xfoil_display == "xvfb" and shutil.which("xvfb-run") is None:
        return "xvfb isn't installed on the server, which XFOIL needs to run"
    return None


def unavailable_capabilities(settings: Settings, *, workstation: bool) -> dict[str, str]:
    unavailable = {}
    if reason := xfoil_unavailable(settings, workstation=workstation):
        unavailable[XFOIL_CAPABILITY] = reason
    if not settings.web_search_configured:
        unavailable[WEB_CAPABILITY] = NO_SEARCH_KEY
    if not workstation:
        unavailable[DESIGN_CAPABILITY] = NO_WORKSTATION
        unavailable[PYTHON_CAPABILITY] = NO_WORKSTATION
    return unavailable
