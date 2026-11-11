"""Checks an address a user gives (their Ollama server) before the server connects to it."""

import asyncio
import ipaddress
import socket


class AddressProblem(Exception):
    """The address can't be used; the message says why, for the person."""


async def check_address(host: str, port: int, *, allow_private: bool) -> None:
    """Refuses addresses that aren't a machine of the user's own when private ones are off."""
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise AddressProblem(f"Couldn't find a machine called {host}.") from None
    for info in infos:
        ip = ipaddress.ip_address(str(info[4][0]).split("%")[0])
        if ip.is_unspecified or ip.is_multicast:
            raise AddressProblem(f"{host} isn't an address of a machine.")
        if not allow_private and (ip.is_private or ip.is_loopback or ip.is_link_local):
            raise AddressProblem(
                f"{host} is on the server's own network. This server only connects to machines "
                "on the public internet."
            )
