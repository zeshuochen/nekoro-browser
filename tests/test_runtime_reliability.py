"""Runtime upgrade/reload failures must never be reported as healthy."""
import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from nekoro_browser import cli, config, helpers
from nekoro_browser.bridge import ExtensionBridge


def test_health_requires_matching_runtime_versions():
    info: dict[str, str | None] = {"url": "https://example.test/", "daemon_version": cli.__version__,
            "extension_version": cli.__version__}
    with patch.object(cli, "_post", return_value={"ok": True, "result": info}):
        assert cli._healthy()
        info["daemon_version"] = "0.3.5"
        assert not cli._healthy(), "upgraded files do not update a running daemon"
        info["daemon_version"] = cli.__version__
        info["extension_version"] = None
        assert not cli._healthy(), "a pre-handshake extension must be reloaded"


def test_upgrade_preserves_discovered_port_and_allowlist():
    calls = []
    recorded_port: list[int | None] = [30500]
    def post(path, data="", timeout=30):
        assert cli._EXPLICIT_PORT == 30500
        calls.append(path)
        recorded_port[0] = None  # daemon shutdown removes the discovery file
        return {"ok": True}
    def ensure(port=None):
        assert port == 30500 and cli._EXPLICIT_PORT == 30500
        assert cli._ALLOW_DOMAINS == ["example.test"]
        calls.append("restart")
        return True
    with patch.object(cli, "_EXPLICIT_PORT", None), patch.object(cli, "_ALLOW_DOMAINS", None), \
         patch.object(config, "read_port_file", side_effect=lambda: recorded_port[0]), \
         patch.dict(os.environ, {}, clear=True), patch.object(cli, "_post", side_effect=post), \
         patch.object(cli, "_port_in_use", return_value=False), \
         patch.object(cli, "_ensure_daemon", side_effect=ensure):
        assert cli._restart_outdated_daemon(None, {"version": "0.3.5", "allow_domains": ["example.test"]})
        assert cli._EXPLICIT_PORT is None and cli._ALLOW_DOMAINS is None
    assert calls == ["/shutdown", "restart"]


def test_upgrade_wont_spawn_when_old_daemon_keeps_port():
    with patch.object(cli, "_post", return_value={"ok": True}), \
         patch.object(cli, "_port_in_use", return_value=True), \
         patch.object(cli, "ENSURE_DAEMON_WAIT", 0), \
         patch.object(cli, "_ensure_daemon") as spawn:
        assert not cli._restart_outdated_daemon(30500, {"version": "0.3.5"})
        spawn.assert_not_called()


def test_version_probe_failure_wont_shutdown_other_daemon():
    with patch.object(cli, "_alive", return_value=True), \
         patch.object(cli, "_post", return_value={"ok": False, "error": "Forbidden: bad/missing token"}) as post:
        assert not cli._ensure_daemon(30500)
        assert post.call_count == 1 and post.call_args.args[0] == "/exec"


async def test_reload_needs_ack_new_connection_and_page_response():
    class Bridge:
        def __init__(self, mode):
            self.connection_generation = 1
            self.attached = asyncio.Event()
            self.mode = mode
        async def send_scripting(self, params, timeout):
            if self.mode == "disconnected":
                raise RuntimeError("extension not connected (WS)")
            if self.mode == "bad-ack":
                return {}
            if self.mode == "healthy":
                self.connection_generation += 1
                self.attached.set()
            return {"reloading": True}
        async def send(self, method, params, timeout):
            return {"result": {"value": "https://example.test/"}}
    with patch.object(helpers, "_RELOAD_TIMEOUT", 0.01):
        for mode in ("disconnected", "bad-ack", "never-reconnects", "healthy"):
            r = await helpers.reload_extension(SimpleNamespace(bridge=Bridge(mode)))
            assert r["ok"] == (mode == "healthy"), (mode, r)


def test_bridge_records_extension_runtime_version():
    bridge = ExtensionBridge(0)
    bridge._dispatch({"type": "hello", "version": "0.3.5"})
    assert bridge.extension_version == "0.3.5"
    bridge._dispatch({"type": "hello", "version": None})
    assert bridge.extension_version is None


async def test_cancelled_reload_transport_does_not_leave_pending_commands():
    bridge = ExtensionBridge(0)
    async def emit(msg, ready_timeout=10.0):
        pass  # command accepted, extension never answers
    bridge._emit = emit
    for request in (lambda: bridge.send("Runtime.evaluate"),
                    lambda: bridge.send_scripting({"action": "reload_extension"})):
        try:
            async with asyncio.timeout(0.01):
                await request()
        except TimeoutError:
            pass
        assert not bridge._pending


if __name__ == "__main__":
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_"):
            asyncio.run(fn()) if asyncio.iscoroutinefunction(fn) else fn()
    print("ALL OK")
