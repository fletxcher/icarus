"""Which tools can run for a user, and why not when one can't."""

import shutil

from ..config import Settings

XFOIL_CAPABILITY = "XFOIL airfoil analysis and airfoil search"
CFD_CAPABILITY = "CFD simulations"

NO_WORKSTATION = "no simulation work station is set up (the user can add one in Settings)"


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
    if not workstation:
        unavailable[CFD_CAPABILITY] = NO_WORKSTATION
    return unavailable
