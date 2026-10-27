"use client";

import { useEffect, useState } from "react";
import {
  CheckIcon,
  CopyIcon,
  ServerIcon,
  Trash2Icon,
  XIcon,
} from "lucide-react";
import { toast } from "sonner";

import { PasswordInput } from "@/components/auth/password-input";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import {
  api,
  type CredentialsOverview,
  type HostKey,
  type WorkstationCheck,
  type WorkstationInfo,
} from "@/lib/api";

function message(failure: unknown, fallback: string) {
  return failure instanceof Error ? failure.message : fallback;
}

function CheckList({ checks }: { checks: WorkstationCheck[] }) {
  return (
    <ul
      className="grid gap-1.5 rounded-lg border p-3 text-sm"
      aria-label="Checks"
    >
      {checks.map(({ id, label, ok, detail }) => (
        <li key={id} className="flex items-start gap-2">
          {ok ? (
            <CheckIcon className="mt-0.5 size-4 shrink-0 text-green-600" />
          ) : (
            <XIcon className="mt-0.5 size-4 shrink-0 text-destructive" />
          )}
          <span>
            {label}
            <span className="block break-words text-muted-foreground">
              {detail}
            </span>
          </span>
        </li>
      ))}
    </ul>
  );
}

function CopyLine({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid gap-1">
      <span className="text-sm text-muted-foreground">{label}</span>
      <div className="flex items-start gap-2">
        <code className="min-w-0 flex-1 rounded-md bg-muted px-2 py-1.5 text-xs break-all">
          {value}
        </code>
        <Button
          type="button"
          variant="outline"
          size="icon"
          aria-label={`Copy ${label.toLowerCase()}`}
          onClick={() =>
            void navigator.clipboard
              .writeText(value)
              .then(() => toast.success("Copied"))
              .catch(() => toast.error("Couldn't copy"))
          }
        >
          <CopyIcon />
        </Button>
      </div>
    </div>
  );
}

const KEY_HELP = `ssh-keygen -t ed25519 -f ~/.ssh/icarus -N ""
cat ~/.ssh/icarus.pub >> ~/.ssh/authorized_keys
cat ~/.ssh/icarus`;

/** Adds a machine: look up its host key, confirm it, then connect with a private key. */
function ConnectForm({
  onSaved,
  onCancel,
}: {
  onSaved: (info: WorkstationInfo, checks: WorkstationCheck[]) => void;
  onCancel: () => void;
}) {
  const [host, setHost] = useState("");
  const [port, setPort] = useState("22");
  const [username, setUsername] = useState("");
  const [hostKey, setHostKey] = useState<HostKey | null>(null);
  const [approved, setApproved] = useState(false);
  const [privateKey, setPrivateKey] = useState("");
  const [passphrase, setPassphrase] = useState("");
  const [workdir, setWorkdir] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const portNumber = Number(port);
  const addressOk =
    host.trim() !== "" &&
    Number.isInteger(portNumber) &&
    portNumber >= 1 &&
    portNumber <= 65535;

  // Changing where to connect invalidates the host key that was looked up.
  function edit(change: () => void) {
    change();
    setHostKey(null);
    setApproved(false);
    setError(null);
  }

  async function lookUp(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setHostKey(await api.lookUpHostKey(host.trim(), portNumber));
    } catch (failure) {
      setError(message(failure, "Couldn't reach the machine."));
    } finally {
      setBusy(false);
    }
  }

  async function connect(event: React.FormEvent) {
    event.preventDefault();
    if (!hostKey) return;
    setBusy(true);
    setError(null);
    try {
      const report = await api.saveWorkstation({
        host: host.trim(),
        port: portNumber,
        username: username.trim(),
        private_key: privateKey,
        passphrase: passphrase || undefined,
        host_key: hostKey.host_key,
        workdir: workdir.trim() || undefined,
      });
      onSaved(report.workstation, report.checks);
    } catch (failure) {
      setError(message(failure, "Couldn't connect."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid gap-4">
      <p className="text-sm text-muted-foreground">
        Icarus connects over SSH from the server it runs on, so the machine must
        be reachable from there. This can be the computer you&apos;re using now
        when the server runs on it too (use <code>127.0.0.1</code>).
      </p>
      <form onSubmit={lookUp} className="grid gap-3">
        <div className="grid grid-cols-[1fr_6rem] gap-2">
          <div className="grid gap-1.5">
            <Label htmlFor="ws-host">Host</Label>
            <Input
              id="ws-host"
              value={host}
              onChange={(event) => edit(() => setHost(event.target.value))}
              placeholder="192.168.1.20 or my-machine.local"
              autoComplete="off"
              spellCheck={false}
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="ws-port">Port</Label>
            <Input
              id="ws-port"
              value={port}
              onChange={(event) => edit(() => setPort(event.target.value))}
              inputMode="numeric"
              autoComplete="off"
            />
          </div>
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="ws-user">User name</Label>
          <Input
            id="ws-user"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            placeholder="the account on that machine"
            autoComplete="off"
            spellCheck={false}
          />
        </div>
        {!hostKey && (
          <div>
            <Button type="submit" disabled={busy || !addressOk}>
              {busy ? "Looking…" : "Look up the machine"}
            </Button>
          </div>
        )}
      </form>

      {hostKey && (
        <form onSubmit={connect} className="grid gap-4">
          <div className="grid gap-2 rounded-lg border p-3 text-sm">
            <span>
              Is this the machine&apos;s fingerprint? Icarus will only ever
              connect to a machine presenting this key.
            </span>
            <code className="text-xs break-all">
              {hostKey.key_type} {hostKey.fingerprint}
            </code>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={approved}
                onChange={(event) => setApproved(event.target.checked)}
              />
              Yes, that&apos;s my machine
            </label>
          </div>

          {approved && (
            <>
              <div className="grid gap-1.5">
                <Label htmlFor="ws-key">Private key</Label>
                <Textarea
                  id="ws-key"
                  value={privateKey}
                  onChange={(event) => setPrivateKey(event.target.value)}
                  placeholder={"-----BEGIN OPENSSH PRIVATE KEY-----\n…"}
                  rows={5}
                  className="font-mono text-xs"
                  autoComplete="off"
                  spellCheck={false}
                />
                <details className="text-sm text-muted-foreground">
                  <summary className="cursor-pointer">
                    Make a key for Icarus
                  </summary>
                  <p className="mt-1">On that machine, run:</p>
                  <pre className="mt-1 overflow-x-auto rounded-md bg-muted p-2 text-xs">
                    {KEY_HELP}
                  </pre>
                  <p className="mt-1">
                    Then paste what the last command prints.
                  </p>
                </details>
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor="ws-pass">Key passphrase (if it has one)</Label>
                <PasswordInput
                  id="ws-pass"
                  value={passphrase}
                  onChange={(event) => setPassphrase(event.target.value)}
                  autoComplete="off"
                />
              </div>
              <details className="text-sm">
                <summary className="cursor-pointer text-muted-foreground">
                  Advanced
                </summary>
                <div className="mt-2 grid gap-1.5">
                  <Label htmlFor="ws-dir">Where Icarus keeps its files</Label>
                  <Input
                    id="ws-dir"
                    value={workdir}
                    onChange={(event) => setWorkdir(event.target.value)}
                    placeholder="/home/you/icarus (the default)"
                    autoComplete="off"
                    spellCheck={false}
                  />
                </div>
              </details>
            </>
          )}

          {error && (
            <p role="alert" className="text-sm break-words text-destructive">
              {error}
            </p>
          )}
          {approved && (
            <div>
              <Button
                type="submit"
                disabled={
                  busy ||
                  username.trim() === "" ||
                  privateKey.trim().length < 32
                }
              >
                {busy ? "Connecting…" : "Connect and save"}
              </Button>
            </div>
          )}
        </form>
      )}

      {!hostKey && error && (
        <p role="alert" className="text-sm break-words text-destructive">
          {error}
        </p>
      )}
      <div>
        <Button
          type="button"
          variant="ghost"
          onClick={onCancel}
          disabled={busy}
        >
          Cancel
        </Button>
      </div>
    </div>
  );
}

/** The person's own simulation machine; mount it to load the current state. */
export function WorkstationPanel() {
  const [overview, setOverview] = useState<CredentialsOverview | null>(null);
  const [failed, setFailed] = useState(false);
  const [adding, setAdding] = useState(false);
  const [checks, setChecks] = useState<WorkstationCheck[] | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let current = true;
    api
      .credentials()
      .then((result) => current && setOverview(result))
      .catch(() => current && setFailed(true));
    return () => {
      current = false;
    };
  }, []);

  async function recheck() {
    setBusy(true);
    try {
      const report = await api.checkWorkstation();
      setChecks(report.checks);
    } catch (failure) {
      setChecks([
        {
          id: "connection",
          label: "Connection",
          ok: false,
          detail: message(failure, "Failed"),
        },
      ]);
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    try {
      await api.removeWorkstation();
      setOverview((current) => current && { ...current, workstation: null });
      setChecks(null);
      toast.success("Work station removed");
    } catch (failure) {
      toast.error(message(failure, "Couldn't remove it."));
    } finally {
      setBusy(false);
    }
  }

  if (failed)
    return (
      <p className="text-sm text-destructive">
        Couldn&apos;t load your work station. Try again.
      </p>
    );
  if (!overview) return <Skeleton className="h-40 rounded-lg" />;

  if (adding)
    return (
      <ConnectForm
        onCancel={() => setAdding(false)}
        onSaved={(workstation, newChecks) => {
          setOverview({ ...overview, workstation });
          setChecks(newChecks);
          setAdding(false);
          toast.success("Work station saved");
        }}
      />
    );

  const own = overview.workstation;
  return (
    <div className="grid gap-4">
      {!overview.storage_available && (
        <p role="alert" className="text-sm text-destructive">
          This server can&apos;t store credentials yet: its CREDENTIALS_KEY
          isn&apos;t set.
        </p>
      )}
      {own ? (
        <section className="grid gap-3 rounded-lg border p-3">
          <div className="flex items-center gap-2 text-sm">
            <ServerIcon className="size-4" />
            <span data-testid="workstation-address">
              {own.username}@{own.host}
              {own.port !== 22 && `:${own.port}`}
            </span>
          </div>
          <p className="text-sm text-muted-foreground">
            Your simulations and XFOIL runs use this machine. Files are kept in{" "}
            <code>{own.workdir}</code>.
          </p>
          <CopyLine label="Host fingerprint" value={own.fingerprint} />
          <CopyLine label="Public key" value={own.public_key} />
          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              onClick={() => void recheck()}
              disabled={busy}
            >
              {busy ? "Checking…" : "Check again"}
            </Button>
            <Button
              variant="outline"
              onClick={() => setAdding(true)}
              disabled={busy}
            >
              Replace
            </Button>
            <Button
              variant="outline"
              onClick={() => void remove()}
              disabled={busy}
            >
              <Trash2Icon />
              Remove
            </Button>
          </div>
          <p className="text-sm text-muted-foreground">
            Jobs already running stay on the machine they started on.
          </p>
        </section>
      ) : (
        <section className="grid gap-3 rounded-lg border p-3">
          <p className="text-sm" data-testid="workstation-default">
            {overview.default_workstation
              ? "You're using the shared work station Icarus provides."
              : "No work station is set up. Add one to run XFOIL and CFD."}
          </p>
          <div>
            <Button
              variant="outline"
              disabled={!overview.storage_available}
              onClick={() => setAdding(true)}
            >
              <ServerIcon />
              Use my own machine
            </Button>
          </div>
        </section>
      )}
      {checks && <CheckList checks={checks} />}
    </div>
  );
}
