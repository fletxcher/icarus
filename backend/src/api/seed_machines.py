"""Puts a few made-up machines on an account, to see how the Work station settings look.

    uv run seed-machines you@example.com             add them
    uv run seed-machines you@example.com --remove    take them away again

They are saved like real machines (their keys are sealed with CREDENTIALS_KEY) but point at
`*.example.invalid` hosts that don't exist, so connecting to one fails. None is connected.
Remove one in the app with "Remove anyway", or all of them with --remove.
"""

import argparse
import asyncio
import sys

import asyncssh
from pymongo import AsyncMongoClient

from .catalog import USERS
from .config import get_settings
from .credentials.repository import CredentialRepository
from .credentials.schemas import Hardware, WorkstationDetails
from .credentials.vault import Vault
from .credentials.workstation import public_key_line

DEMO_HOST_SUFFIX = ".example.invalid"

MACHINES = [
    (
        "Lab workstation",
        "lab-workstation",
        32,
        "AMD EPYC 7543 32-Core Processor",
        "NVIDIA RTX A6000",
    ),
    (
        "Cloud CPU box",
        "cloud-cpu",
        16,
        "Intel(R) Xeon(R) Platinum 8375C CPU @ 2.90GHz",
        None,
    ),
    (
        "University cluster node",
        "cluster-node",
        64,
        "AMD EPYC 7763 64-Core Processor",
        "NVIDIA A100-SXM4-40GB",
    ),
]


async def seed(email: str, *, remove: bool = False) -> int:
    """Adds (or, with `remove`, deletes) the made-up machines; returns how many changed."""
    settings = get_settings()
    client: AsyncMongoClient = AsyncMongoClient(settings.mongodb_uri, tz_aware=True)
    try:
        db = client[settings.mongodb_db]
        user = await db[USERS].find_one({"email": email.strip().lower()})
        if user is None:
            raise LookupError(f"no account with the email {email}")
        credentials = CredentialRepository(db, Vault(settings.credentials_key))
        if not credentials.vault.available:
            raise RuntimeError("CREDENTIALS_KEY isn't set, so nothing can be saved.")
        existing = await credentials.list_workstations(user["_id"])
        demo = [m for m in existing if m.host.endswith(DEMO_HOST_SUFFIX)]
        if remove:
            for machine in demo:
                await credentials.delete_workstation(user["_id"], machine.id)
            return len(demo)
        taken = {m.host for m in demo}
        added = 0
        for name, host, cpus, cpu_model, gpu in MACHINES:
            host += DEMO_HOST_SUFFIX
            if host in taken:
                continue
            key = asyncssh.generate_private_key("ssh-ed25519")
            server_key = asyncssh.generate_private_key("ssh-ed25519")
            details = WorkstationDetails(
                name=name,
                host=host,
                port=22,
                username="ubuntu",
                workdir="/home/ubuntu/icarus",
                host_key=public_key_line(server_key),
                fingerprint=server_key.get_fingerprint("sha256"),
                public_key=public_key_line(key),
                hardware=Hardware(cpus=cpus, cpu_model=cpu_model, gpu=gpu),
            )
            await credentials.add_workstation(
                user["_id"], details, key.export_private_key("openssh").decode()
            )
            added += 1
        # Adding connects the newest; the demo shows the cards with nothing connected.
        await credentials.disconnect_workstations(user["_id"])
        return added
    finally:
        await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("email", help="the account to add them to")
    parser.add_argument("--remove", action="store_true", help="delete the made-up machines")
    args = parser.parse_args()
    try:
        changed = asyncio.run(seed(args.email, remove=args.remove))
    except (LookupError, RuntimeError) as error:
        sys.exit(str(error))
    print(f"{'removed' if args.remove else 'added'} {changed} made-up machine(s) for {args.email}")


if __name__ == "__main__":
    main()
