# Dashboard

A bench tool for a vehicle that does not fly yet: aim gimbals, watch telemetry, verify
signs, edit the config, and read these docs — all against real hardware over MAVLink.

## Running it

```bash
Vector/start.sh
```

Then open <http://localhost:8765>. The script checks Python dependencies, attaches the
flight controller over usbipd on WSL (including Shared-but-not-Attached after a
reboot — a leftover `/dev/ttyACM*` is not treated as success), builds the web app if
it is stale, and serves. `Vector/start.sh` is a thin wrapper around
`Vector/tools/dashboard.sh`; either works. It exits if USB cannot be recovered;
use `--no-usb` to skip that.

| Command | Effect |
|---|---|
| `Vector/start.sh` | Attach USB (recover Shared-not-Attached), build if stale, serve |
| `Vector/start.sh --no-usb` | Skip the WSL USB step |
| `Vector/start.sh --dev` | Vite dev server alongside, hot reload |
| `PORT=9000 Vector/start.sh` | Serve on a different port |

Dependencies, if the script reports them missing:

```bash
python3 -m pip install --user pyserial pymavlink starlette uvicorn
```

### WSL USB

The flight controller appears in WSL only after usbipd attaches it. `wsl-usb.sh`
handles this, including the `Device Descriptor Request Failed` state that needs an
elevated reset — that is what `usb-reset.ps1` is for.

For **flashing**, leave the board attached to Windows and do *not* usbipd-attach it;
`./waf --upload` uses Windows COM ports. For the **dashboard**, attach it. The two want
the device in different places, which is the single most common source of confusion when
switching between the two tasks.

## Architecture

```text
browser  ──WebSocket /ws──►  Starlette (app.py)
                                  │
                             Session
                            ┌─────┴──────┐
                        Bench        LevelController
                            │
                       OutputArbiter  ─── one owner at a time
                            │
                       MavlinkLink  ──USB serial──► flight controller
```

One process, one WebSocket, one config. Every browser tab sees the same state because
there is only one copy of it.

| Module | Responsibility |
|---|---|
| [`app.py`](../dashboard/server/app.py) | HTTP routes, WebSocket, broadcast loop |
| [`protocol.py`](../dashboard/server/protocol.py) | Command table and dispatch |
| [`config.py`](../dashboard/server/config.py) | Load, validate, persist `vector.json` |
| [`kinematics.py`](../dashboard/server/kinematics.py) | Pure geometry, no state |
| [`link.py`](../dashboard/server/link.py) | MAVLink connection, telemetry, events |
| [`bench.py`](../dashboard/server/bench.py) | Aiming, motor tests, output mapping |
| [`outputs.py`](../dashboard/server/outputs.py) | Ownership arbitration and slew ramps |
| [`controller.py`](../dashboard/server/controller.py) | Bench levelling loop |
| [`state.py`](../dashboard/server/state.py) | Shapes the JSON the browser consumes |

### The wire protocol

Browser to server, one object per command:

```json
{ "command": "aim", "id": 12, "arms": ["north"], "forward": 10, "right": 0 }
```

Server to browser, four message types discriminated by `type`: `config`, `state`,
`ports`, `ack`. Every reply to a command echoes the `command` name and the `id`, which
is how the browser knows which control to re-enable.

Telemetry is pushed at 20 Hz. Commands are answered individually, except `live_aim` and
`live_end`, which are sent at pointer rate and answered with silence — an ack per drag
frame would flood the feed and add a round trip to every mouse move.

Adding a command means adding one row to `COMMANDS` in `protocol.py`. The table is also
what the developer view lists, so there is nothing to keep in sync.

### Contract tests

The Python payload and the TypeScript interfaces are two hand-written descriptions of
the same JSON, and nothing at runtime checks they agree. A rename on either side
produces a UI full of `undefined` rather than an error.

Two test files close that gap:

| Test | Asserts |
|---|---|
| `tests/test_state_contract.py` | Every key `state.py` emits appears in `types.ts`, units are suffixed, `events` is a flat time-ordered list |
| `tests/test_protocol.py` | The request field is `command` on both sides, every ack echoes it, and every `send('...')` call in the web app names a real command |

These exist because both mismatches actually happened during development. The command
name was read from the wrong field, which made every button in the UI silently do
nothing.

`tests/test_config.py` covers the config document itself: that
`vector.schema.json` still describes `vector.json`, that the schema rejects a typo
rather than letting it fall back to a default, and that a rejected save leaves the file
on disk untouched.

### Front-end tests

Run with `npm test` in `dashboard/web`. Three suites, each pinning something that
broke once:

| Test | Asserts |
|---|---|
| `lib/kinematics.test.ts` | The TypeScript port agrees with the Python golden vectors |
| `lib/markdown.test.ts` | Every link form used in `Vector/docs` renders as something clickable in the app |
| `lib/store.test.ts` | Store data can be detached for editing without `DataCloneError` |

The last two are worth explaining, because both bugs were invisible rather than loud.

The docs are written to read correctly with the repository open, so they link to
sibling `.md` files and to source files by relative path. Rendered in a single-page app
on a hash route, both forms lead nowhere. `lib/markdown.ts` translates them: sibling
documents become in-app routes, with any heading anchor carried in a query parameter
because the fragment is already spent on the route; repository paths render as inline
code instead of a link that would 404. In-page anchors are handled by a click listener
for the same reason — assigning to `location.hash` would replace the route.

`lib/store.ts` exports `detached()` because `structuredClone` throws `DataCloneError`
on a Vue reactive proxy, and everything reachable from the store is proxied. The Setup
page cloned the config document that way and rendered with no fields at all: no error
banner, no missing panel, just an empty form.

## The pages

### Overview

Link state, armed state, attitude, a top-down frame diagram showing where each arm's
thrust is pointing, and ESC telemetry. The frame diagram is the fastest way to see that
a sign is wrong: ask for forward lean and watch whether all four vectors agree.

### Arms

The main working page. Per arm, or all arms together:

- **Aim pad** — drag to set a thrust direction. The envelope drawn behind it is the real
  reachable outline, traced server-side, not an assumed circle.
- **Axis sliders** — drive one gimbal axis directly, in geometric degrees.
- **Kinematics readout** — commanded tilt, resulting servo angle, pulse width, and
  whether an axis is at its limit.
- **Motor test** — every ticked motor on every selected arm spins together, at one
  throttle for one duration. Bounded by `bench_limits`, refused for any motor whose
  `function` is unset, and refused entirely while the vehicle is armed.

Planned arms are shown but not commandable.

### Stabilize

The bench levelling demo. Tilt the rig by hand and the gimbals counter-rotate to hold
thrust at world vertical.

The page states on screen that this is not flight stabilisation and cannot become it.
[Control](04-control.md) explains why: it commands lateral force, which does not
stabilise attitude, and it runs at 25 Hz over USB with unbounded latency.

Controls are `level_gain`, `lead_time_s`, `max_tilt_fraction`, and the two invert flags.
The page shows a live preview of what the law *would* command even while it is stopped,
which makes it safe to reason about before committing anything to the servos.

### Telemetry

Attitude, rates, battery, ESC data, and servo outputs, with a short rolling history kept
in the browser. Cell voltage is shown as pack voltage divided by six, because that is
the number that matters for a li-ion pack.

Two panels earn their place:

- **Serial budget.** Eight servos streamed at speed will not fit on a telemetry radio,
  though USB has ample room. The panel says how much of the link the current
  configuration would use, and warns before it becomes a mystery.
- **Message counts.** A missing MAVLink stream shows up as an absence, which is
  otherwise very hard to notice.

The servo output table shows pulse widths **as the flight controller reports them**, not
as the dashboard last asked for. Divergence means something else is writing to those
outputs.

### 3D World

A three.js scaffold: dark grid, orbit-style camera, and a vehicle model built from the
config — correct arm count, azimuths and proportions.

**Not wired to telemetry.** It is a placeholder for the simulated world, present so the
page and its build plumbing exist. Because the model is generated from `vector.json`,
adding an arm to the config makes it appear here too.

### Setup

Edits `vector.json` from the browser: arm list, channel map, servo centring, limits,
signs. Channel **0** disables that output — the pin is unclaimed and nothing is written
to the flight controller. The document is validated before anything is written, so a
bad edit leaves the file on disk untouched.

Mapping debug drives one SERVO pin as a motor (1 s at the bench throttle cap) or as a
servo (deflect, then centre). It names the pin, not the arm, which is how a wrong
channel shows up instead of a guess.

The page edits a copy of the raw on-disk document rather than the camelCase view the
rest of the UI reads, so keys the UI does not model survive a round trip instead of
being silently dropped.

### Docs

Renders these files, served straight from `Vector/docs/`. Editing a file and reloading
the page shows the change — the docs are plain Markdown that also reads fine in an
editor or on a repository page.

## Frontend

Vue 3 with the composition API, Vite, TypeScript, `vue-router`, and `three` for the
World page. No component framework and no CSS framework: design tokens in
`styles/tokens.css`, a small set of shared components, and plain scoped CSS.

```text
web/src/
  components/     PanelCard, StatTile, StatusPill, SliderRow, ToggleRow,
                  AimPad, FrameDiagram, AttitudeDisc, EventFeed, Sparkline,
                  LinkBar, AppNav, NavIcon
  pages/          one file per route
  lib/
    store.ts      the single reactive store, the only WebSocket
    types.ts      mirrors the Python payloads
    kinematics.ts port of the Python kinematics module
    format.ts     number and angle formatting
    markdown.ts   docs rendering, and the link rewriting it needs
    scene.ts      three.js scene construction
  router.ts       routes and the sidebar metadata
```

The shell is a fixed sidebar with the content pane scrolling inside it. Below 820 px the
nav stacks above the content as a wrapping bar and the document becomes the scroll
container, because a viewport-height shell with `overflow: hidden` would otherwise put
the nav on screen and clip the whole page below it with nothing left to scroll.

State lives in one reactive store. Pages read from it and call `send()`; nothing else
opens a socket or keeps its own copy of vehicle state, so there is exactly one place
where the UI and the vehicle can disagree.

Reconnection is automatic and silent — a dashboard left open across a server restart
picks the link back up on its own, and pending flags are cleared on disconnect so
controls do not stay disabled.

### Why the kinematics are duplicated in TypeScript

The aim pad needs to show the reachable envelope and the resulting servo angles while
you drag, at pointer rate. Round-tripping to the server for that would make the
interaction feel broken.

The duplication is safe because `kinematics.test.ts` asserts the TypeScript
implementation matches `tests/golden_kinematics.json`, generated from the Python module.
A sign flip cannot land in one language only.

## Safety behaviour

| Situation | Behaviour |
|---|---|
| No link | Every vehicle command is refused with a plain message, not queued |
| No arms live | Commands refused; the reason is stated |
| Browser tab closed | Live aiming released, levelling loop stopped |
| Two features want the servos | Arbiter grants one; the loser stops immediately |
| Stop pressed | Takes ownership, centres everything, cancels motor tests |
| Motor test | Bounded in percent and seconds by `bench_limits` |
| Unset `function` | That output is Disabled, so that motor cannot be spun at all |
| Vehicle armed | Spinning is refused; the mixer owns the outputs |

The controls that matter — Stop and Center — sit in the top bar on every page, not
buried in whichever page happens to be open.

## Tests

```bash
python3 -m unittest discover -s Vector/tests    # 107 tests
cd Vector/dashboard/web && npm test             # 46 tests
cd Vector/dashboard/web && npm run typecheck    # vue-tsc
```

`npm run build` runs the typecheck first, so a type error cannot ship into `dist/`.

The Python suite needs no third-party packages — it is `unittest`, not `pytest`.
`jsonschema` is the one optional dependency: without it the schema tests skip rather
than fail.
