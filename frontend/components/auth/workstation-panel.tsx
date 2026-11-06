"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  CheckIcon,
  LoaderCircleIcon,
  PlusIcon,
  SearchIcon,
  ServerIcon,
  Trash2Icon,
  XIcon,
} from "lucide-react";
import { toast } from "sonner";

import { PasswordInput } from "@/components/auth/password-input";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import {
  SetupSteps,
  WorkstationSetup,
} from "@/components/auth/workstation-setup";
import {
  api,
  type CredentialsOverview,
  type HostKey,
  type SetupProgress,
  type WorkstationReport,
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

const KEY_HELP = `ssh-keygen -t ed25519 -f ~/.ssh/icarus -N ""
cat ~/.ssh/icarus.pub >> ~/.ssh/authorized_keys
cat ~/.ssh/icarus`;

/** Adds a machine: its address, a check of its fingerprint, and its password. Icarus makes its
 * own key and adds it with the password, which is used once, and then sets the machine up. */
function ConnectForm({
  onSaved,
  onCancel,
}: {
  onSaved: (report: WorkstationReport) => void;
  /** Left out when there is nothing to go back to. */
  onCancel?: () => void;
}) {
  const [name, setName] = useState("");
  const [username, setUsername] = useState("");
  const [host, setHost] = useState("");
  const [port, setPort] = useState("22");
  const [hostKey, setHostKey] = useState<HostKey | null>(null);
  const [approved, setApproved] = useState(false);
  const [password, setPassword] = useState("");
  const [privateKey, setPrivateKey] = useState("");
  const [passphrase, setPassphrase] = useState("");
  const [workdir, setWorkdir] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const portNumber = Number(port);
  const target =
    username.trim() !== "" &&
    host.trim() !== "" &&
    Number.isInteger(portNumber) &&
    portNumber >= 1 &&
    portNumber <= 65535
      ? { username: username.trim(), host: host.trim(), port: portNumber }
      : null;
  const ownKey = privateKey.trim() !== "";

  // Changing where to connect invalidates the fingerprint that was looked up.
  function edit(change: () => void) {
    change();
    setHostKey(null);
    setApproved(false);
    setError(null);
  }

  async function lookUp() {
    if (!target || hostKey || busy) return;
    setBusy(true);
    setError(null);
    try {
      setHostKey(await api.lookUpHostKey(target.host, target.port));
    } catch (failure) {
      setError(message(failure, "Couldn't reach the machine."));
    } finally {
      setBusy(false);
    }
  }

  async function connect(event: React.FormEvent) {
    event.preventDefault();
    if (!hostKey || !target) return;
    setBusy(true);
    setError(null);
    try {
      const report = await api.saveWorkstation({
        name: name.trim() || undefined,
        host: target.host,
        port: target.port,
        username: target.username,
        password: ownKey ? undefined : password,
        private_key: ownKey ? privateKey : undefined,
        passphrase: ownKey && passphrase ? passphrase : undefined,
        host_key: hostKey.host_key,
        workdir: workdir.trim() || undefined,
      });
      onSaved(report);
    } catch (failure) {
      setError(message(failure, "Couldn't connect."));
    } finally {
      // The password only lives in this form until the request is sent.
      setPassword("");
      setBusy(false);
    }
  }

  const cancel = onCancel && (
    <Button type="button" variant="ghost" onClick={onCancel} disabled={busy}>
      Cancel
    </Button>
  );
  const canConnect =
    approved &&
    !busy &&
    (ownKey ? privateKey.trim().length >= 32 : password !== "");

  return (
    <form
      className="grid gap-4"
      onSubmit={(event) => {
        event.preventDefault();
        if (hostKey) void connect(event);
        else void lookUp();
      }}
    >
      <p className="text-sm text-muted-foreground">
        Connect a machine you own. Icarus reaches it over SSH from the server it
        runs on, so the machine must be reachable from there.
      </p>
      <div className="grid gap-1.5">
        <Label htmlFor="ws-name">Name</Label>
        <Input
          id="ws-name"
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="Lab workstation (optional)"
          autoComplete="off"
        />
      </div>
      <div className="grid grid-cols-[1fr_2fr_5rem] gap-2">
        <div className="grid gap-1.5">
          <Label htmlFor="ws-user">User</Label>
          <Input
            id="ws-user"
            value={username}
            onChange={(event) => edit(() => setUsername(event.target.value))}
            onBlur={() => void lookUp()}
            placeholder="ubuntu"
            autoComplete="off"
            spellCheck={false}
          />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="ws-host">IP or host</Label>
          <Input
            id="ws-host"
            value={host}
            onChange={(event) => edit(() => setHost(event.target.value))}
            onBlur={() => void lookUp()}
            placeholder="192.168.1.20"
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
            onBlur={() => void lookUp()}
            inputMode="numeric"
            autoComplete="off"
          />
        </div>
      </div>

      {hostKey && (
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
      )}

      {approved && (
        <>
          {!ownKey && (
            <div className="grid gap-1.5">
              <Label htmlFor="ws-password">
                Password for {target?.username} on the machine
              </Label>
              <PasswordInput
                id="ws-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                autoComplete="off"
              />
              <p className="text-sm text-muted-foreground">
                Used once, here: Icarus logs in, adds a key of its own so it
                never needs the password again, and uses it for sudo to install
                Docker and the solvers. It is never stored.
              </p>
            </div>
          )}
          <details className="text-sm">
            <summary className="cursor-pointer text-muted-foreground">
              Advanced
            </summary>
            <div className="mt-3 grid gap-4">
              <div className="grid gap-1.5">
                <Label htmlFor="ws-key">Use my own private key instead</Label>
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
                <details className="text-muted-foreground">
                  <summary className="cursor-pointer">Make a key</summary>
                  <p className="mt-1">On that machine, run:</p>
                  <pre className="mt-1 overflow-x-auto rounded-md bg-muted p-2 text-xs">
                    {KEY_HELP}
                  </pre>
                  <p className="mt-1">
                    Then paste what the last command prints. Setup then needs to
                    be started from the machine&apos;s card.
                  </p>
                </details>
              </div>
              {ownKey && (
                <div className="grid gap-1.5">
                  <Label htmlFor="ws-pass">
                    Key passphrase (if it has one)
                  </Label>
                  <PasswordInput
                    id="ws-pass"
                    value={passphrase}
                    onChange={(event) => setPassphrase(event.target.value)}
                    autoComplete="off"
                  />
                </div>
              )}
              <div className="grid gap-1.5">
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
            </div>
          </details>
        </>
      )}

      {error && (
        <p role="alert" className="text-sm break-words text-destructive">
          {error}
        </p>
      )}
      <div className="flex items-center justify-between gap-2">
        {hostKey ? (
          <Button type="submit" disabled={!canConnect}>
            {busy ? "Connecting…" : "Connect"}
          </Button>
        ) : (
          <Button type="submit" disabled={busy || !target}>
            {busy ? "Looking…" : "Continue"}
          </Button>
        )}
        {cancel}
      </div>
    </form>
  );
}

/** One saved machine: what it is, and the buttons to connect, disconnect or remove it. */
function MachineCard({
  machine,
  busy,
  checking,
  onConnect,
  onDisconnect,
  onRemove,
  onRemoveAnyway,
  onCheck,
  onSetUp,
}: {
  machine: WorkstationInfo;
  busy: boolean;
  checking: boolean;
  onConnect: () => void;
  onDisconnect: () => void;
  onRemove: () => void;
  onRemoveAnyway: () => void;
  onCheck: () => void;
  onSetUp: React.ReactNode;
}) {
  const { hardware } = machine;
  const removing = machine.status === "removing";
  const failed = machine.status === "removal_failed";
  const cpu = [
    hardware.cpus ? `${hardware.cpus} cores` : null,
    hardware.cpu_model,
  ].filter(Boolean);
  return (
    <li
      className="grid gap-3 rounded-lg border p-3"
      data-testid="machine-card"
      aria-label={machine.name}
    >
      <div className="grid gap-0.5 text-sm">
        <span className="flex items-center gap-2 font-medium">
          <ServerIcon className="size-4 shrink-0" />
          {machine.name}
        </span>
        {cpu.length > 0 && (
          <span className="text-muted-foreground">{cpu.join(" · ")}</span>
        )}
        {hardware.gpu && (
          <span className="text-muted-foreground">{hardware.gpu}</span>
        )}
      </div>

      {removing && (
        <p
          className="flex items-center gap-2 text-sm text-muted-foreground"
          aria-live="polite"
        >
          <LoaderCircleIcon className="size-4 animate-spin" />
          Removing: {machine.status_message || "starting"}…
        </p>
      )}
      {failed && (
        <p role="alert" className="text-sm break-words text-destructive">
          Couldn&apos;t clean up the machine: {machine.status_message}
        </p>
      )}

      <div className="flex flex-wrap gap-2">
        {!removing && !failed && (
          <Button
            variant={machine.active ? "secondary" : "outline"}
            disabled={busy}
            onClick={machine.active ? onDisconnect : onConnect}
          >
            {machine.active ? "Disconnect" : "Connect"}
          </Button>
        )}
        {machine.active && !removing && !failed && (
          <>
            <Button variant="outline" disabled={busy} onClick={onCheck}>
              {checking ? "Checking…" : "Check again"}
            </Button>
            {onSetUp}
          </>
        )}
        {failed && (
          <>
            <Button variant="outline" disabled={busy} onClick={onRemove}>
              Try again
            </Button>
            <Button variant="outline" disabled={busy} onClick={onRemoveAnyway}>
              Remove anyway
            </Button>
          </>
        )}
        {!removing && !failed && (
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button
                variant="outline"
                size="icon"
                aria-label={`Remove ${machine.name}`}
                disabled={busy}
              >
                <Trash2Icon />
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Remove {machine.name}?</AlertDialogTitle>
                <AlertDialogDescription>
                  Icarus will clean up the machine: stop its containers, delete
                  the solver image, the job folders and its own files, and take
                  its SSH key out of <code>~/.ssh/authorized_keys</code>. Then
                  the connection is deleted here. Simulations still running on
                  it are lost.
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Keep it</AlertDialogCancel>
                <AlertDialogAction onClick={onRemove}>Remove</AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        )}
      </div>
    </li>
  );
}

const POLL_REMOVAL_MS = 3000;
const POLL_SETUP_MS = 3000;

/** The person's own machines: connect one, or add another. Mount it to load the current state. */
export function WorkstationPanel() {
  const [overview, setOverview] = useState<CredentialsOverview | null>(null);
  const [failed, setFailed] = useState(false);
  const [adding, setAdding] = useState(false);
  const [checks, setChecks] = useState<WorkstationCheck[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [checking, setChecking] = useState(false);
  const [query, setQuery] = useState("");
  // How setup of the connected machine is going. Kept here, not in its dialog, so the steps keep
  // updating below the machines after the dialog is closed.
  const [setup, setSetup] = useState<SetupProgress | null>(null);
  // Setup that connecting with a password already started: its dialog opens by itself.
  const [openSetup, setOpenSetup] = useState(false);
  const lastSetupState = useRef<SetupProgress["state"] | null>(null);

  const reload = useCallback(async () => {
    try {
      setOverview(await api.credentials());
    } catch {
      setFailed(true);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- loads the saved machines once
    void reload();
  }, [reload]);

  // While a machine is being cleaned up, its progress comes from the server.
  const removing = overview?.workstations.some((m) => m.status === "removing");
  useEffect(() => {
    if (!removing) return;
    const timer = setInterval(() => void reload(), POLL_REMOVAL_MS);
    return () => clearInterval(timer);
  }, [removing, reload]);

  async function act(work: () => Promise<unknown>, failure: string) {
    setBusy(true);
    try {
      await work();
      await reload();
    } catch (error) {
      toast.error(message(error, failure));
    } finally {
      setBusy(false);
    }
  }

  const recheck = useCallback(async () => {
    setChecking(true);
    try {
      const report = await api.checkWorkstation();
      setChecks(report.checks);
      await reload();
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
      setChecking(false);
    }
  }, [reload]);

  // Every look at setup comes through here, so the end of a run is noticed once.
  const handleSetup = useCallback(
    (next: SetupProgress) => {
      const was = lastSetupState.current;
      lastSetupState.current = next.state;
      setSetup(next);
      if (was === "running" && next.state === "completed") {
        toast.success("The machine is ready");
        void recheck();
      } else if (was === "running" && next.state === "failed") {
        toast.error(`Setup failed: ${next.message}`);
      }
    },
    [recheck],
  );

  // Setup may already be running from before this page was opened.
  const activeId = overview?.workstations.find((m) => m.active)?.id;
  useEffect(() => {
    if (!activeId) return;
    let current = true;
    api.setupProgress().then(
      (progress) => {
        if (current && progress.state === "running") handleSetup(progress);
      },
      () => undefined,
    );
    return () => {
      current = false;
    };
  }, [activeId, handleSetup]);

  const setupRunning = setup?.state === "running";
  useEffect(() => {
    if (!setupRunning) return;
    const timer = setInterval(() => {
      api.setupProgress().then(handleSetup, () => undefined);
    }, POLL_SETUP_MS);
    return () => clearInterval(timer);
  }, [setupRunning, handleSetup]);

  if (failed)
    return (
      <p className="text-sm text-destructive">
        Couldn&apos;t load your machines. Try again.
      </p>
    );
  if (!overview) return <Skeleton className="h-40 rounded-lg" />;

  const machines = overview.workstations;
  const noStorage = !overview.storage_available;
  // Matches what a card shows: its name, CPU and GPU.
  const needle = query.trim().toLowerCase();
  const shown = needle
    ? machines.filter((machine) =>
        [machine.name, machine.hardware.cpu_model, machine.hardware.gpu]
          .filter(Boolean)
          .some((text) => text!.toLowerCase().includes(needle)),
      )
    : machines;

  if (adding || machines.length === 0)
    return (
      <div className="grid gap-4">
        {noStorage && (
          <p role="alert" className="text-sm text-destructive">
            This server can&apos;t store credentials yet: its CREDENTIALS_KEY
            isn&apos;t set.
          </p>
        )}
        <ConnectForm
          onCancel={machines.length > 0 ? () => setAdding(false) : undefined}
          onSaved={(report) => {
            setChecks(report.checks);
            if (report.setup) handleSetup(report.setup);
            setOpenSetup(report.setup !== null);
            setAdding(false);
            void reload();
            toast.success("Machine saved and connected");
            if (report.setup_note) toast.message(report.setup_note);
          }}
        />
      </div>
    );

  return (
    <div className="grid gap-4">
      {noStorage && (
        <p role="alert" className="text-sm text-destructive">
          This server can&apos;t store credentials yet: its CREDENTIALS_KEY
          isn&apos;t set.
        </p>
      )}
      <div className="flex items-center justify-between gap-3">
        <div className="relative w-full max-w-xs">
          <SearchIcon className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search machines"
            aria-label="Search machines"
            className="pl-8"
            autoComplete="off"
          />
        </div>
        <Button
          variant="outline"
          disabled={noStorage}
          onClick={() => setAdding(true)}
          className="shrink-0"
        >
          Add <PlusIcon />
        </Button>
      </div>
      {shown.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No machine matches &ldquo;{query.trim()}&rdquo;.
        </p>
      )}
      <ul className="grid gap-3" aria-label="Machines">
        {shown.map((machine) => (
          <MachineCard
            key={machine.id}
            machine={machine}
            busy={busy || setupRunning}
            checking={checking}
            onConnect={() => {
              setChecks(null);
              setSetup(null);
              lastSetupState.current = null;
              setOpenSetup(false);
              void act(
                () => api.connectWorkstation(machine.id),
                "Couldn't connect it.",
              );
            }}
            onDisconnect={() => {
              setChecks(null);
              setSetup(null);
              lastSetupState.current = null;
              setOpenSetup(false);
              void act(
                () => api.disconnectWorkstation(machine.id),
                "Couldn't disconnect it.",
              );
            }}
            onRemove={() =>
              void act(
                () => api.removeWorkstation(machine.id),
                "Couldn't remove it.",
              )
            }
            onRemoveAnyway={() =>
              void act(
                () => api.removeWorkstation(machine.id, true),
                "Couldn't remove it.",
              )
            }
            onCheck={() => void recheck()}
            onSetUp={
              <WorkstationSetup
                autoOpen={openSetup}
                username={machine.username}
                progress={setup}
                onProgress={handleSetup}
                disabled={
                  busy ||
                  !(
                    setup?.state === "running" ||
                    setup?.state === "failed" ||
                    checks?.some((check) => !check.ok)
                  )
                }
              />
            }
          />
        ))}
      </ul>
      {setup && (setup.state === "running" || setup.state === "failed") ? (
        <SetupSteps progress={setup} />
      ) : (
        checks && <CheckList checks={checks} />
      )}
    </div>
  );
}
