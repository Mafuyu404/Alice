# Action Tool Module Contract

`kokoro.action.tools` is the capability layer.  CLI code is an entry/runtime
assembly surface: it may call public tool facades to load sessions, create
runtime bundles, and start loops.  Executable behavior, local routing rules,
clients, long-lived resources, and tool-specific refinement belong in a tool
module.

## Directory Shape

Each action tool owns one directory:

```text
tools/<tool_name>/
  __init__.py    # public facade: register(registry) and stable helpers only
  spec.py        # schema constants and ToolSpec registration
  prepare.py     # optional: turn an Action into executable PreparedAction
  execute.py     # execute the prepared action
  after.py       # optional: follow-up behavior after execution
  runtime.py     # optional: long-lived resources or local runtime helpers
  config.py      # optional: tool-owned defaults and config loading
```

Registered action tools must keep `spec.py` and `execute.py` in the tool
directory.  Pure query tools may omit `prepare.py`; tools with no follow-up
behavior may omit `after.py`.  New behavior should not be added to `cli.py`,
`tool_schemas.py`, or `tool_handlers.py`.

Runtime-only capability modules also live under `tools/`, but they are not
registered as `Action` tools.  Examples are input sources, bridges, live
runtimes, and text-CLI helpers.  These directories must be listed in
`RUNTIME_MODULE_NAMES` so the package boundary stays explicit.  Runtime-only
modules must have `__init__.py`, must not expose `register(registry)`, and must
not contain `spec.py`, `prepare.py`, `execute.py`, or `after.py`.

## Lifecycle

Tools are registered through:

```python
def register(registry: ActionToolRegistry) -> None:
    registry.register(ToolSpec(...))
```

`ToolSpec` declares:

- `name`: public tool capability name.
- `actions`: all `Action.action` values handled by this tool.
- `schema`: optional function-call schema, defined in the local `spec.py`.
- `prepare`: optional preparation stage.
- `execute`: required execution stage.
- `after`: optional post-execution hook.
- `default_visibility` and `default_result_policy`: event feedback behavior.

Registered hooks are part of the module contract:

- `execute` must be implemented by the tool's own `execute.py`.
- `prepare`, when present, must be implemented by the tool's own `prepare.py`.
- `after`, when present, must be implemented by the tool's own `after.py`.
- A registered action tool cannot also be listed in `RUNTIME_MODULE_NAMES`.
- Each `Action.action` value is owned by exactly one registered `ToolSpec`.

## Prepare Rules

`prepare` belongs to the tool, not the generic action selector.  Use it when a
tool needs focused work before execution:

- `say`: turn an intention into final speech context or text.
- `search_web`: extract a precise query from the action reason and context.
- `memory`: consolidate event details into memory content.
- `observe_screen`: normalize the visual focus.
- `qq` and stickers: select a target or candidate set.

Extra LLM calls needed for a specific tool should happen in that tool's
preparation stage or a tool-owned helper called by `prepare`.

The action selector decides whether a capability should be used.  Tool-specific
argument refinement, search query extraction, memory consolidation, and final
speech shaping stay inside the selected tool's `prepare` stage so the selector
does not grow tool-specific reasoning branches.

## Import Boundaries

Core action modules may import tool package facades:

```python
from kokoro.action.tools import search_web
```

They should not import tool internals such as
`kokoro.action.tools.search_web.client`.  CLI runtime modules may assemble
resources through public facades.  Top-level legacy modules under
`kokoro.action` should be thin compatibility facades while old imports are being
retired.

Tool modules may import their own internal files.  Cross-tool access must go
through the other tool's package facade:

```python
from kokoro.action.tools import say as say_tool
```

Do not import another tool's internal modules from either implementation files
or package facades.  A tool facade is the public boundary for that tool; it
should not re-export another tool's private implementation details.

## Capability Ownership

Place attached behavior with the capability it serves:

- `say`: proactive/local speech, final speech shaping, TTS, subtitles,
  portrait output, echo filtering, and AEC.
- `speech_input`: microphone, STT, speech turn buffering, overlap handling, and
  speech-derived text events.
- `search_web`: query extraction, web clients, daemon/runtime search helpers,
  and search-result feedback.
- `memory`: memory search, memory writes, and memory-content consolidation.
- `observe_screen`: screen vision, foreground app, screen interest, visual user
  commands, and page/screen caches.
- `qq`: QQ bridge/input/media/sticker support that is runtime or channel-level;
  public message/sticker actions register through action tools.
- `vts`: expressions, motions, VTS controller/runtime, and body driver helpers.
- `live`: live platform input/runtime integration.
- `debug_input`: persistent high-priority text debug input and log-driven debug
  automation.
- `task`: long-running task creation, progress, listing, and cancellation.

## Registry Boundaries

`tools.__init__` owns the registry manifest:

- `TOOL_MODULES`: action tool packages that must expose `spec.py` and
  `register(registry)`.
- `TOOL_ACTIONS`: all registered action names, including non-function-call
  actions such as `say` and `wait`.
- `DEFAULT_ENABLED_TOOL_ACTIONS`: default function-call tools for the legacy
  OpenAI-compatible tool loop.
- `RUNTIME_MODULE_NAMES`: tool-package directories that provide runtime or
  input capabilities but are not registered as action tools.
- `register_all(registry)`: imports tool modules and registers their specs.

`kokoro.action.tool_schemas` is compatibility-only.  New schemas must be placed
in the corresponding tool module.

## Feedback

Every tool result must return a `ToolResult` or string.  `ActionRuntime` writes
started/result events with `cycle_id`, `action_id`, and `causality_id`; tools
should put tool-specific details in `ToolResult.metadata` rather than publishing
parallel ad-hoc result channels.
