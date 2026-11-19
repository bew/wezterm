---
created-at: 20260930T1922
---

# [DRAFT] Restricted Windows

> IMPORTANT: Before any drafting/planning/editing of this spec,
> agents MUST load one of the spec-writing skills first.

## Introduction

A restricted window is a terminal window whose allowed actions are constrained, typically to host a transient launcher such as `fzf`.
It always creates a new window, possibly in an already-running GUI process, and does not require a new GUI process.
The restrictions themselves and the window's relationship to child-process completion are not yet specified.
This spec was split out of the tracked-spawn design (`<spec:spawn-wait>`), which now covers only `wezterm cli spawn --wait` and `wezterm start --wait-pane-exit`.

## Window placement and scope

A future `--restricted-window` always creates a new window, possibly in an already-running GUI process.
It does not require a new GUI process and does not define when a tracked `--wait` finishes.
The `fzf` launcher motivates a constrained, transient window, but the restrictions themselves are not yet specified.
Do not infer GUI-process exit from restricted-window closure; ordinary `wezterm start` process waiting is unchanged.

### Open Questions

1. Which actions must a restricted window disallow (tabs, splits, pane moves, Lua-created panes/windows)?
   Blocking. A single initial pane is not necessarily restricted.
2. Should a restricted window close after its tracked child exits even if exit behavior would hold the pane?
   Blocking. Transient launchers and general restricted windows may differ.
3. Does `--restricted-window` apply to both `start` and `cli spawn`, and how is a GUI started when `cli spawn` finds no running mux?
   Blocking. `cli spawn` targets a running GUI or mux today.

## Global Open Questions

1. **Terminology & Key Concepts** (TKC) — Whether this spec needs a separate terminology section for restricted window.
   Non-blocking. Definitions are colocated with the scope section to limit new terminology.
2. A window configuration profile system that could replace a hard-coded restricted-window preset.
   Non-blocking followup.
