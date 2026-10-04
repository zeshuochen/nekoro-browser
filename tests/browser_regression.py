"""Real Chrome regression against packaged wheels, including an in-place upgrade.

Requires Node.js (only stdlib), build, and a Chrome for Testing executable.
Uses a temporary profile, localhost fixture, isolated venvs/data dirs, and port 28417.
No daily browser profile, login, or fake extension is used.
"""
import argparse
import json
import os
from pathlib import Path
import queue
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import venv
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
RESULTS = []
HTML = b'''<!doctype html><title>Nekoro regression</title>
<input id="value"><button id="go">Go</button>
<script>window.count=0;document.querySelector('#go').onclick=()=>window.count++;</script>'''


def record(name, **fields):
    item = {"name": name, **fields}
    RESULTS.append(item)
    print(json.dumps(item), flush=True)


def run(args, **kwargs):
    return subprocess.run(list(map(str, args)), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", creationflags=FLAGS,
                          timeout=kwargs.pop("timeout", 120), **kwargs)


class Fixture(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(HTML)))
        self.end_headers()
        self.wfile.write(HTML)


class Browser:
    def __init__(self, chrome, profile, headful):
        self.replies = queue.Queue()
        self.counter = 0
        node = shutil.which("node")
        assert node is not None, "Node.js is required"
        self.proc = subprocess.Popen(
            [node, str(ROOT / "tests/chrome_driver.mjs"),
             str(chrome), str(profile), "headful" if headful else "headless"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", creationflags=FLAGS,
            start_new_session=os.name != "nt")
        threading.Thread(target=self._read, daemon=True).start()
        self.call("Browser.getVersion")

    def _read(self):
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self.replies.put(json.loads(line))

    def call(self, method, params=None, session=None):
        assert self.proc.stdin is not None
        self.counter += 1
        message = {"id": self.counter, "method": method, "params": params or {}}
        if session:
            message["sessionId"] = session
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()
        deadline = time.monotonic() + 30
        while True:
            reply = self.replies.get(timeout=max(0.01, deadline - time.monotonic()))
            if reply.get("event") in ("browser-error", "browser-exit"):
                raise RuntimeError("test browser exited: " + str(reply))
            if reply.get("id") == self.counter:
                if "error" in reply:
                    raise RuntimeError(method + ": " + str(reply["error"]))
                return reply.get("result", {})

    def load(self, extension):
        # Match README installation: Developer mode must be enabled. The test-only
        # CDP loader bypasses this on first load, but Chrome disables it on reload.
        target = self.call("Target.createTarget", {"url": "chrome://extensions/"})["targetId"]
        session = self.call("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
        try:
            deadline = time.monotonic() + 10
            while True:
                r = self.call("Runtime.evaluate", {
                    "expression": "typeof chrome.developerPrivate !== 'undefined'", "returnByValue": True}, session)
                if r.get("result", {}).get("value"):
                    break
                assert time.monotonic() < deadline, "extensions WebUI did not load"
                time.sleep(0.1)
            r = self.call("Runtime.evaluate", {
                "expression": "new Promise(resolve => chrome.developerPrivate.updateProfileConfiguration({inDeveloperMode:true}, () => chrome.developerPrivate.getProfileConfiguration(resolve)))",
                "awaitPromise": True, "returnByValue": True}, session)
            assert r["result"]["value"]["inDeveloperMode"] is True, r
        finally:
            self.call("Target.closeTarget", {"targetId": target})
        return self.call("Extensions.loadUnpacked", {"path": str(extension)})

    def close(self):
        if self.proc.poll() is None:
            try:
                self.call("Browser.close")
            except (RuntimeError, queue.Empty, BrokenPipeError):
                pass
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    run(["taskkill", "/PID", self.proc.pid, "/T", "/F"], timeout=15)
                else:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                self.proc.wait(timeout=10)
        if self.proc.stdin:
            self.proc.stdin.close()
        if self.proc.stdout:
            self.proc.stdout.close()


class Session:
    def __init__(self, directory, package, chrome, headful):
        self.directory = directory
        self.directory.mkdir()
        envdir = directory / "venv"
        venv.EnvBuilder(with_pip=True).create(envdir)
        self.python = envdir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        self.env = os.environ.copy()
        self.env.update(NEKORO_DATA_DIR=str(directory / "runtime"), NEKORO_PORT="28417",
                        NEKORO_DOMAIN_SKILLS=str(directory / "empty-skills"),
                        PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        self.env.pop("NEKORO_ALLOW_DOMAINS", None)
        self.chrome, self.headful, self.browser = chrome, headful, None
        self.install(package)

    def install(self, package):
        p = run([self.python, "-m", "pip", "install", "--disable-pip-version-check", "-q", package],
                env=self.env, cwd=self.directory)
        assert p.returncode == 0, p.stderr

    def command(self, *args, **kwargs):
        # The browser is owned by this test. If process discovery fails, never
        # let --ensure fall back to opening the user's daily Chrome profile.
        code = ("from nekoro_browser import cli;"
                "cli._launch_chrome=lambda ext_dir=None:"
                "'test browser not detected; daily profile launch disabled';cli.main()")
        return run([self.python, "-c", code, *args],
                   env=self.env, cwd=self.directory, **kwargs)

    def exec(self, code):
        p = self.command("-c", code)
        assert p.returncode == 0, p.stderr or p.stdout
        r = json.loads(p.stdout)
        assert r.get("ok"), r.get("error")
        return r

    def helper(self, code):
        r = self.exec(code).get("result")
        assert isinstance(r, dict) and r.get("ok"), r
        return r

    def start_browser(self, load=True):
        self.browser = Browser(self.chrome, self.directory / "profile", self.headful)
        if load:
            p = self.command("--extension-path")
            assert p.returncode == 0, p.stderr
            self.browser.load(Path(p.stdout.strip()).resolve())

    def ensure(self, *args):
        p = self.command("--ensure", *args, timeout=120)
        assert p.returncode == 0, p.stdout + p.stderr

    def versions(self):
        return self.exec("{'daemon': __import__('nekoro_browser').__version__, "
                         "'extension': getattr(daemon.bridge, 'extension_version', None), "
                         "'allow_domains': daemon.allow_domains}")["result"]

    def stop(self):
        p = self.command("--stop", timeout=45)
        assert p.returncode == 0, p.stdout + p.stderr

    def close(self):
        try:
            self.stop()
        finally:
            if self.browser:
                self.browser.close()
                self.browser = None


def check_port_free():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 28417))


def baseline(directory, wheel, chrome, headful, url):
    s = Session(directory, wheel, chrome, headful)
    try:
        s.start_browser()
        s.ensure()
        p = s.command("--doctor")
        assert p.returncode == 0 and "[PASS] Versions" in p.stdout, p.stdout + p.stderr
        record("cold-start-and-versions", **s.versions())
        code = '''import json
verified = 0
for i in range(30):
    assert (await navigate(%r + '?cycle=' + str(i)))['ok']
    assert (await fill_input('#value', 'cycle-' + str(i)))['ok']
    assert (await click_selector('#go'))['ok']
    actual = await js('({count:window.count,value:document.querySelector("#value").value})')
    assert actual['result'] == {'count':1,'value':'cycle-'+str(i)}, actual
    verified += 1
print(json.dumps({'verified': verified}))''' % url
        result = json.loads(s.exec(code)["stdout"])
        assert result["verified"] == 30
        record("navigation-input-click", cycles=30)
        # Initially attached tab remains in the collapsed/background group.
        # No test-side bringToFront: the production screenshot must make it render.
        shot = s.helper("await capture_screenshot()")
        assert shot.get("png_size") == shot.get("css_size"), (shot.get("png_size"), shot.get("css_size"))
        record("background-tab-screenshot", png_size=shot["png_size"])
        for i in range(3):
            p = s.command("--reload-ext")
            if p.returncode:
                assert s.browser is not None
                record("reload-diagnostics", targets=[
                    {"type": t["type"], "url": t["url"]}
                    for t in s.browser.call("Target.getTargets")["targetInfos"]
                    if t["url"].startswith("chrome-extension://")])
            assert p.returncode == 0, p.stdout + p.stderr
            assert s.command("--doctor").returncode == 0
            s.helper("await navigate(" + repr(url) + ")")
            s.helper("await fill_input('#value', 'after-reload')")
            actual = s.helper("await js('document.querySelector(\"#value\").value')")
            assert actual["result"] == "after-reload"
        record("extension-reload-and-page-operations", cycles=3)
        messages = [
            {"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"regression","version":"1"}}},
            {"jsonrpc":"2.0","method":"notifications/initialized"},
            {"jsonrpc":"2.0","id":2,"method":"tools/list"},
            {"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"page_info","arguments":{}}},
            {"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"click_selector","arguments":{"sel":"#missing"}}},
        ]
        p = run([s.python, "-m", "nekoro_browser.mcp_server"], env=s.env, cwd=s.directory,
                input="\n".join(map(json.dumps, messages)) + "\n", timeout=45)
        replies = {r["id"]: r for r in map(json.loads, p.stdout.splitlines())}
        assert p.returncode == 0 and not replies[3]["result"].get("isError"), p.stderr
        assert replies[4]["result"].get("isError") is True
        record("mcp-page-operation-and-error", tools=len(replies[2]["result"]["tools"]))
        assert s.browser is not None
        s.browser.close()
        s.browser = None
        p = s.command("--reload-ext", timeout=45)
        assert p.returncode != 0, "closed Chrome must not report reload success"
        record("closed-browser-reload-rejected", exit=p.returncode)
        s.start_browser(load=False)  # use the installed profile, not a fresh install
        s.ensure()
        assert s.command("--doctor").returncode == 0
        record("browser-restart-reconnection", **s.versions())
        s.stop()
        s.ensure()
        assert s.command("--doctor").returncode == 0
        record("daemon-restart-reconnection", **s.versions())
    finally:
        s.close()
    check_port_free()


def upgrade(directory, wheel, chrome, headful, url, old_version):
    s = Session(directory, "nekoro-browser==" + old_version, chrome, headful)
    try:
        s.start_browser()
        s.ensure("--allow-domains", "127.0.0.1")
        s.helper("await navigate(" + repr(url) + ")")  # warm the old daemon's imports
        before = s.versions()
        assert before["daemon"] == old_version, before
        s.install(wheel)
        p = s.command("--doctor")
        assert p.returncode != 0 and "[FAIL] Versions" in p.stdout, p.stdout + p.stderr
        record("upgrade-old-daemon-detected", before=before, exit=p.returncode)
        s.ensure()
        p = s.command("--doctor")
        assert p.returncode == 0 and "[PASS] Versions" in p.stdout, p.stdout + p.stderr
        versions = s.versions()
        assert versions["daemon"] == versions["extension"] != old_version, versions
        assert versions["allow_domains"] == ["127.0.0.1"], versions
        s.helper("await navigate(" + repr(url) + ")")
        s.helper("await fill_input('#value', 'upgraded')")
        assert s.helper("await js('document.querySelector(\"#value\").value')")["result"] == "upgraded"
        s.helper("await capture_screenshot()")
        record("upgrade-restarts-reloads-preserves-policy", **versions)
    finally:
        s.close()
    check_port_free()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chrome", type=Path, required=True)
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--headful", action="store_true")
    parser.add_argument("--from-version", default="0.3.5")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    assert args.chrome.is_file(), "Chrome executable does not exist"
    assert shutil.which("node"), "Node.js is required for the test-only Chrome pipe bridge"
    check_port_free()  # fail before launching anything if someone owns the port
    started = time.monotonic()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix="nekoro-browser-regression-") as temporary:
            directory = Path(temporary).resolve()
            assert directory.parent == Path(tempfile.gettempdir()).resolve()
            wheel = args.wheel.resolve() if args.wheel else None
            if wheel is None:
                p = run([sys.executable, "-m", "build", "--wheel", "--outdir", directory / "dist"], cwd=ROOT)
                assert p.returncode == 0, p.stdout + p.stderr
                wheel = next((directory / "dist").glob("*.whl"))
            url = f"http://127.0.0.1:{server.server_port}/fixture"
            baseline(directory / "baseline", wheel, args.chrome.resolve(), args.headful, url)
            upgrade(directory / "upgrade", wheel, args.chrome.resolve(), args.headful, url, args.from_version)
        record("ALL OK", seconds=round(time.monotonic() - started, 1))
    except Exception as exc:
        record("FAILED", error=str(exc))
        raise
    finally:
        server.shutdown()
        server.server_close()
        if args.report:
            args.report.write_text(json.dumps(RESULTS, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
