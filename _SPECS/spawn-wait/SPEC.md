# [READY] Tracked Spawns

## Introduction

Callers need to launch a command in WezTerm and observe when that command finishes without tying completion to the lifetime of a GUI process.
A launcher that opens an interactive session such as `ssh host` illustrates the need: it opens a terminal window, the user interacts with the session, and the caller reacts to the session's exit status.

The caller waits only for completion and exit status; it expects no value back from the session.
A desktop-wide launcher opening a fuzzy finder such as `fzf` is the richer future target, but v1 has neither input nor output propagation, so it cannot yet return a selection.

The waiting semantics should be identical whether WezTerm reuses an existing GUI process or starts a new one; only how the pane is hosted differs.
This design makes every client spawn tracked and adds an opt-in wait that reports the spawn's lifecycle events back to the requesting client.

`wezterm start --wait-pane-exit` uses the same backend behavior as `wezterm cli spawn --wait --propagate=exit-code`.
With an existing GUI it waits for a child whose lifetime is distinct from the GUI process.
With no GUI it becomes the GUI itself, hosts the command in a single restricted window, and waits for that window to close; see No-GUI hosting for tracked start.

## Terminology & Key Concepts

**Tracked spawn**:
A client spawn request whose lifecycle events are reported back to the client that issued it.
Every pane-creating request from a client is tracked; pane creation internal to the GUI is not.

**Spawn ID**:
An identifier for one tracked spawn, returned in `SpawnResponse` and used to correlate its push notifications; it is not a separate completion handle.
It is a per-mux monotonic counter rendered as `spawn-<n>` (for example `spawn-7`).
Consumers treat it as an opaque string.
Scoped to a single mux instance and meaningless across mux instances or restarts.

**Lifecycle Event**:
A lifecycle notification for a tracked spawn, of type `started`, `failed`, or `finished`.
The protocol pushes only `failed` and `finished`; the CLI adds a local `started` when it reports events.

**Command Outcome**:
The terminal result carried by `failed` and `finished` events: an exit code, a signal, an error string, and a duration.
A `started` event has no outcome.

**Push notification**:
A message the mux sends on its own initiative rather than in reply to a request.
It is carried as a serial-0 `unilateral` PDU and broadcast to connected clients.
This spec delivers `failed` and `finished` events as push notifications, correlated by spawn ID.

**Restricted window**:
A window that holds exactly one pane and forbids window and pane management (new windows, tabs, splits, the bindings that create them, and mux navigation); normal terminal input stays allowed.
Used only by the no-GUI host of `wezterm start --wait-pane-exit`; see "No-GUI hosting for tracked start".

## Naming and IDs

This spec adds no new public CLI command.
`SpawnV2` remains the spawn request, and its `SpawnResponse` gains a required `spawn_id` field.
Because `split_pane` shares `SpawnResponse`, splits carry a `spawn_id` and are tracked too; pane creation internal to the GUI is untracked.
The added field is a codec change and requires a protocol version bump.
The term definitions (tracked spawn, spawn ID, lifecycle event, command outcome) live in Terminology & Key Concepts.

A spawn ID correlates events for one tracked spawn; a pane ID identifies the spawned pane, as it does for other pane-oriented CLI operations.

The `failed` and `finished` events are delivered as `NotifySpawnLifecycle` push notifications, and the client that issued the spawn correlates them by spawn ID.
There is no separate subscription API and no way to opt out of tracking in v1.

### Event types

Three event types exist in CLI output: `started`, `failed`, `finished`.
All three share one payload shape.

The protocol pushes only `failed` and `finished`; the CLI synthesizes a `started` event from `SpawnResponse` when it reports events.
A tracked spawn produces exactly one of `failed` or `finished`; `started` precedes it in CLI output.

WARNING: `failed` may occur before a pane exists (spawn error), so `pane_id` is nullable there; in that case the CLI's synthesized `started` is omitted.

## Interface / How to use

### `wezterm cli spawn`

```sh
wezterm cli spawn --wait -- ssh host
wezterm cli spawn --wait                                   # default shell/command
wezterm cli spawn --wait --propagate=exit-code -- ssh host
wezterm cli spawn --wait --json -- ssh host
wezterm cli spawn --wait --jsonl -- ssh host
```

Every spawn is tracked; without `--wait`, `cli spawn` still returns as it does today, including its existing pane-ID output on stdout, and ignores the lifecycle events.
`--wait` keeps the CLI alive until the tracked spawn emits `failed` or `finished`.
`--wait` does not require an explicit `PROG`; without one it uses the default shell or command, as `cli spawn` does today.
`--wait` without `--json`/`--jsonl` prints nothing to stdout; human-readable events go to stderr.

`--propagate` accepts only `exit-code` in v1; `outputs` and any other value are rejected and reserved for a future revision.

Reject `--propagate` without `--wait`.
Reject `--json` and `--jsonl` with any `--propagate` value.
Reject `--json` and `--jsonl` without `--wait`.
Reject `--json` together with `--jsonl`.

### Propagation

- `exit-code`: exit with the child's exit code.
- Input handling is not supported; see Global Open Questions.

With `exit-code`, every outcome that is not a normal child exit code (signal termination, spawn failure, lost connection) yields CLI exit code 125.
The CLI exit code therefore cannot distinguish a launcher failure from a child exiting with that same code.
Callers needing the distinction use `--json` or `--jsonl`.

Without `exit-code`, only a child exit is an observed outcome: a nonzero child exit yields CLI exit code 0, while a `failed` event or a lost connection yields CLI exit code 125.
A waiting CLI writes nothing to stdout outside JSON modes.

### Event payload

Every event uses this payload:

```json
{"event":"started","spawn_id":"spawn-7","pane_id":42,"domain":"local","outcome":null}
{"event":"finished","spawn_id":"spawn-7","pane_id":42,"domain":"local","outcome":{"exit_code":0,"signal":null,"error":null,"duration":1.234}}
{"event":"failed","spawn_id":"spawn-7","pane_id":null,"domain":"local","outcome":{"exit_code":null,"signal":null,"error":"spawn failed: ...","duration":0.012}}
```

This payload is the CLI's own output struct, assembled from `SpawnResponse` and `NotifySpawnLifecycle`; it is not a protocol PDU verbatim.
`domain` is the resolved domain name supplied by the mux, not a CLI guess.
`duration` is seconds with up to three decimal places, measured by the mux from spawn start to event.
`command` and `workspace` are not included.

`signal` is the platform termination string (for example `SIGTERM`) or `null`; `error` is non-null only for launch or tracking failures.

### Output modes

| Mode | stdout | Events shown |
|---|---|---|
| `--wait` | nothing | all, human-readable text (stderr) |
| `--wait --json` | one JSON object | last event only |
| `--wait --jsonl` | one JSON object per line, streamed | all, as they arrive |

Text-format events are for humans and are not a stable interface.
`--json` prints nothing until the last event, then exactly one object.
`--jsonl` flushes each line when its event arrives, including the synthesized `started`.

### `wezterm start`

```sh
wezterm start --wait-pane-exit -- ssh host
```

Require an explicit `PROG` with `--wait-pane-exit`.

With an existing GUI, use the same spawn tracking and `exit-code` propagation as `wezterm cli spawn --wait --propagate=exit-code`.
The pane is placed by `start` rules (current window, or a new tab or window); only the wait path is shared.

With no GUI, become the GUI in-process and host the command in a single restricted window, blocking until that window closes; see No-GUI hosting for tracked start.

Only `--wait-pane-exit` creates a restricted window; ordinary `wezterm start` keeps its normal placement and startup behavior.
Do not introduce `--wait-gui-exit` in this design.

## Tracked spawn delivery

Start tracking before launching the child so a short-lived command cannot exit before tracking exists.
`SpawnV2` is answered by `SpawnResponse` (carrying `spawn_id`) immediately, then terminal events arrive as `NotifySpawnLifecycle` push notifications correlated by spawn ID.
Those notifications reach every connected client, so delivery is by correlation rather than per-connection privacy.

The mux emits `finished` when the child exits, or `failed` if spawn or tracking fails.
Events for one spawn ID arrive in order, including when the child exits immediately after the spawn response.

There is no separate subscription operation, no way to opt out of tracking, and no reconnect support in v1.
Do not keep a global history of completed exits; free a spawn's tracking at its terminal event or when its connection ends.

Clean up tracking state after the terminal event is delivered.
Free a connection's tracked spawns when that connection ends, reusing the client unregistration the mux already performs in `SessionHandler::drop`.
Client disconnection must not kill the pane.
Mux or GUI disconnection before a terminal event is a CLI failure, not a fabricated child exit code.

Concurrent tracked spawns keep independent identities.
For v1, accept only local-domain commands; reject unsupported domains before spawning when possible.
The Mux that hosts the local domain is authoritative for child status and emits the events, whether it runs in the GUI process or a standalone mux-server.

GUI and pane display behavior stay governed by existing exit behavior, independent of when the CLI returns.

## No-GUI hosting for tracked start

This applies only to `wezterm start --wait-pane-exit` when no GUI is discovered; ordinary `wezterm start` is unaffected.

The invoking process becomes the GUI in-process, so there is no detached host and no cross-process handoff.
It hosts the command in a single restricted window: one window with exactly one pane, where closing the window closes the pane.
Because window and pane lifetimes coincide, waiting for the pane to exit and waiting for the window to close are the same event.

The restricted window forbids window and pane management:

- opening new windows,
- opening new tabs,
- splitting the pane,
- bindings whose action creates a pane, tab, or window,
- attaching to other domains or navigating the mux outside this window.

It stays otherwise interactive: typing, paste, and resize are allowed.

Lifecycle:

- On normal completion the pane exits, the window closes, the GUI process exits, and the CLI returns with the child's exit code; the GUI process exit status carries the code.
- If the user closes the window before the child exits, the pane is killed; the outcome reports the termination signal and the CLI exits `125`.
- If the child cannot be tracked, the CLI exits `125`.

Because the GUI owns the pane directly, this mode needs neither spawn tracking nor the push-notification correlation used when an existing GUI is reused.

The restricted-window feature is defined in its own spec; see Related artifacts.
This spec pins only the subset it depends on: a single pane, window-equals-pane lifetime, the forbidden actions above, and applicability to `--wait-pane-exit` only.

## Alternatives and tradeoffs

Simplest alternative: extend PR 8203's pane-ID-based `WaitPaneExit` request with globally retained exit codes.
It has a smaller protocol surface.
It retains state for untracked panes, needs eviction and competing-waiter rules, and carries no per-spawn events.

Tracking every client spawn instead reuses the existing spawn request, adding `spawn_id` to `SpawnResponse` and ordered terminal events, at the cost of a codec change and a fast-exit-safe lifecycle.
Prefer the simpler path only if the feature stays limited to one local waiter with no per-spawn events.

Do not return results through a newly launched GUI's stdout: it is not a reliable control channel.
Do not add a GUI-exit flag merely to get a fresh window; placement and process lifetime are separate.

The no-GUI case could instead spawn a detached GUI host and connect to it as a client.
That avoids tying the caller to the GUI lifetime, but needs a detached-spawn mechanism, a socket handshake, and a hold to keep an empty host alive.
The in-process restricted window reaches the same exit semantics with no cross-process step.

## Related artifacts

- PR 8203: implements `start --wait-exit`, initial-pane tracking, a pane-ID wait RPC, and a stdout-based new-GUI report path; this spec replaces those mechanisms.
- Restricted windows: a separate spec at `_WIP_SPECS/restricted-window/`; still DRAFT, and the no-GUI host of `wezterm start --wait-pane-exit` depends on the subset pinned here.

## Global Open Questions

1. Reconnectable tracked-spawn delivery.
   Non-blocking followup. V1 has no reconnect; events are pushed only while the issuing client is connected.
2. Input handling (for example an `inputs` propagate flag).
   Non-blocking followup. Skipped for v1; terminal input is not automatically the CLI's stdin.
3. Output propagation (a v2 `outputs` flag and stdout/stderr distinction).
   Non-blocking followup. Removed from v1; only `exit-code` propagation is specified here.
   The carrier is undecided; the existing render-change push is not raw program output and merges stdout and stderr.
