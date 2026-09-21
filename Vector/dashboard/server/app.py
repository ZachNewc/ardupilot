#!/usr/bin/env python3
"""
AP_FLAKE8_CLEAN
Starlette host for the Vector dashboard.

Serves the built web app, exposes the documentation as JSON so the Docs page can
render it, and runs one WebSocket carrying telemetry out and commands in.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from typing import Any, AsyncIterator, Dict, Set

from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from . import board
from . import config as vconfig
from . import kinematics as kin
from . import state as vstate
from .bench import Bench
from .controller import LevelController
from .protocol import Session, command_catalogue, dispatch, list_serial_ports

HERE = os.path.dirname(os.path.abspath(__file__))
WEB_DIST = os.path.normpath(os.path.join(HERE, "..", "web", "dist"))
DOCS_DIR = os.path.normpath(os.path.join(HERE, "..", "..", "docs"))

# Telemetry push rate to the browser. The UI coalesces to one repaint per frame, so
# going faster than this only costs bandwidth.
BROADCAST_HZ = 20.0

MISSING_BUILD = """<!doctype html>
<html><head><meta charset="utf-8"><title>Vector</title>
<style>
 body{background:#05070d;color:#e8eefb;font:15px/1.7 ui-monospace,monospace;padding:3rem;max-width:44rem}
 code{background:#161e2e;padding:.2rem .45rem;border-radius:4px}
 h1{font-size:1.4rem}
</style></head>
<body><h1>Vector dashboard is not built</h1>
<p>Build the web app, then reload this page:</p>
<p><code>cd Vector/dashboard/web &amp;&amp; npm install &amp;&amp; npm run build</code></p>
<p>Or run <code>Vector/start.sh</code>, which installs and builds when needed.</p>
</body></html>"""


class Hub:
    """Fans telemetry snapshots out to every open browser tab."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.clients: Set[WebSocket] = set()
        self.lock = asyncio.Lock()
        self.task: asyncio.Task | None = None

    async def add(self, socket: WebSocket) -> None:
        async with self.lock:
            self.clients.add(socket)

    async def remove(self, socket: WebSocket) -> None:
        async with self.lock:
            self.clients.discard(socket)

    async def broadcast(self, payload: Dict[str, Any]) -> None:
        async with self.lock:
            targets = list(self.clients)
        if not targets:
            return
        text = json.dumps(payload)
        for socket in targets:
            try:
                await socket.send_text(text)
            except Exception:
                await self.remove(socket)

    async def run(self) -> None:
        period = 1.0 / BROADCAST_HZ
        while True:
            await asyncio.sleep(period)
            async with self.lock:
                if not self.clients:
                    continue
            try:
                data = await asyncio.to_thread(
                    vstate.snapshot, self.session.bench, self.session.controller
                )
            except Exception as exc:
                # Keep the socket alive and say what broke; a dead broadcast loop
                # would leave the UI showing stale numbers with no indication why.
                data = {
                    "link": {"connected": False, "lastError": str(exc)},
                    "events": [
                        {"id": 0, "at": time.time(), "kind": "error", "text": str(exc)}
                    ],
                }
            await self.broadcast({"type": "state", "data": data})


def build_session() -> Session:
    cfg = vconfig.load()
    bench = Bench(cfg)
    return Session(bench=bench, controller=LevelController(bench))


session = build_session()
hub = Hub(session)


def config_payload() -> Dict[str, Any]:
    cfg = session.bench.config
    return {
        **vconfig.to_dict(cfg),
        "workspaces": {arm.id: kin.workspace_payload(arm) for arm in cfg.arms},
        "commands": command_catalogue(),
        "outputMap": board.payload(cfg),
        # The on-disk document, so the Setup page can edit it without the UI's
        # camelCase view having to be a lossless mirror of the file format.
        "document": cfg.raw,
    }


async def websocket_endpoint(socket: WebSocket) -> None:
    await socket.accept()
    await hub.add(socket)

    try:
        await socket.send_text(json.dumps({"type": "config", "data": config_payload()}))
        await socket.send_text(json.dumps({"type": "ports", "data": list_serial_ports()}))
        await socket.send_text(json.dumps({
            "type": "state",
            "data": vstate.snapshot(session.bench, session.controller),
        }))

        while True:
            raw = await socket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(msg, dict):
                continue

            reply = await dispatch(session, msg)
            if reply.get("reloadConfig"):
                await hub.broadcast({"type": "config", "data": config_payload()})
            if reply:
                await socket.send_text(json.dumps(reply))
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        await hub.remove(socket)
        # A closed tab must not leave the servos chasing a stale drag target, a
        # circling sweep, or the levelling loop running with nobody watching.
        session.bench.end_live_aim()
        async with hub.lock:
            last_tab_closed = not hub.clients
        if last_tab_closed:
            session.controller.stop()
            session.bench.stop_oscillate()


async def index(_request: Any) -> Any:
    path = os.path.join(WEB_DIST, "index.html")
    if not os.path.exists(path):
        return HTMLResponse(MISSING_BUILD, status_code=503)
    with open(path, "r", encoding="utf-8") as handle:
        return HTMLResponse(handle.read())


async def api_ports(_request: Any) -> Any:
    return JSONResponse(list_serial_ports())


async def api_config(_request: Any) -> Any:
    return JSONResponse(config_payload())


async def api_docs(_request: Any) -> Any:
    """
    Serve the documentation set so the Docs page can render it in the dashboard.

    Markdown is returned as-is and rendered client side; the docs stay plain files
    that read fine in an editor or on the repository page.
    """
    entries = []
    if os.path.isdir(DOCS_DIR):
        for name in sorted(os.listdir(DOCS_DIR)):
            if not name.endswith(".md"):
                continue
            path = os.path.join(DOCS_DIR, name)
            with open(path, "r", encoding="utf-8") as handle:
                body = handle.read()
            title = name
            for line in body.splitlines():
                if line.startswith("# "):
                    title = line[2:].strip()
                    break
            entries.append({"slug": name[:-3], "file": name, "title": title, "body": body})
    return JSONResponse(entries)


@contextlib.asynccontextmanager
async def lifespan(_app: Starlette) -> AsyncIterator[None]:
    hub.task = asyncio.create_task(hub.run())
    try:
        yield
    finally:
        if hub.task is not None:
            hub.task.cancel()
        session.controller.stop()
        session.bench.shutdown()


routes = [
    Route("/", index),
    Route("/api/ports", api_ports),
    Route("/api/config", api_config),
    Route("/api/docs", api_docs),
    WebSocketRoute("/ws", websocket_endpoint),
]

app = Starlette(routes=routes, lifespan=lifespan)

# The explicit routes above win for "/" and "/ws"; this mount serves hashed assets.
if os.path.isdir(WEB_DIST):
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="static")
