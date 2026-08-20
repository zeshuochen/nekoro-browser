"""test_ensure.py — CLI --ensure 自愈链。无需浏览器/daemon：
monkeypatch cli._alive / _healthy / _chrome_running / _launch_chrome 与
lifecycle.spawn_daemon / existing_daemon_pid / stop_daemon。

假件用「状态 + 副作用」建模（spawn 让 daemon 活、reload 让扩展响应），不用
「第 N 次调用返回 X」的调用序列——序列一旦被无关改动挪了顺序就假红/假绿。

关注点是静默坏掉就没人发现的分支：daemon 假死时到底有没有真去 spawn、spawn 前
有没有先清存量（不清就是两个 daemon 抢同一端口）、扩展无响应时 reload 只重试一次
而不是转圈、以及任一步没修好时退出码必须非 0。
"""
import contextlib
import io
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from nekoro_browser import cli
from nekoro_browser import lifecycle

_WAITS = ("ENSURE_CHROME_WAIT", "ENSURE_DAEMON_WAIT", "ENSURE_DAEMON_GRACE",
          "ENSURE_EXT_WAIT", "ENSURE_EXT_WAIT_COLD", "ENSURE_RELOAD_WAIT")
_CLI_ORIG = {n: getattr(cli, n) for n in
             ("_alive", "_healthy", "_post", "_chrome_running", "_launch_chrome",
              "extension_dir", "_port_in_use", "_pid_file_is_ours",
              "_port_bind_denied") + _WAITS}
_LC_ORIG = {n: getattr(lifecycle, n) for n in
            ("spawn_daemon", "existing_daemon_pid", "stop_daemon", "identify",
             "cleanup_pid", "set_port",
             # URL 不是函数而是模块状态：_ensure_daemon 会经 set_port 改写它，
             # 不还原的话后面每个用例都指着上一个用例的端口（顺序一变就出鬼）
             "URL")}


def _restore():
    for n, v in _CLI_ORIG.items():
        setattr(cli, n, v)
    for n, v in _LC_ORIG.items():
        setattr(lifecycle, n, v)


class _World:
    """被 ensure 操作的那个世界的最小模型。默认全绿，各 case 只改自己关心的那几位。"""

    def __init__(self, chrome=True, alive=True, healthy=True, stale_pid=None,
                 launch_error=None, spawn_serves=True, reload_heals=True,
                 stale_starts_serving=False, dies_on_probe=None, served_pid=None,
                 port_held=None, pid_file_is_ours=True,
                 wakes_on_probe=None, probe_error=None):
        self.chrome, self.alive, self.healthy = chrome, alive, healthy
        self.stale_pid = stale_pid
        self.launch_error = launch_error
        self.spawn_serves = spawn_serves          # spawn 出来的 daemon 会不会真服务
        self.reload_heals = reload_heals          # reload 后扩展会不会活过来
        self.stale_starts_serving = stale_starts_serving  # 存量进程只是还在启动
        self.dies_on_probe = dies_on_probe        # 第 N 次探扩展时 daemon 已自己退出
        self.served_pid = served_pid              # daemon 经 /pid 自报的 pid
        # 端口上有没有人监听。默认跟 alive 一致（活着的 daemon 当然占着端口）；
        # 显式传 True + alive=False 就是「有人占着却不应答」那个状态。
        self.port_held = alive if port_held is None else port_held
        # pid 文件是不是本端口的。真函数要读端口文件——不钉住的话用例会跟着
        # 本机上真有没有 daemon 漂移。
        self.pid_file_is_ours = pid_file_is_ours
        # 睡着的 MV3 service worker：第 N 次 page_info 探活才答得上（前面几次正是把它
        # 叫醒的那几次）。建模成「世界在第 N 次探活后变健康」，不是「第 N 次返回 X」——
        # 后者一旦被无关改动挪了顺序就假红。
        self.wakes_on_probe = wakes_on_probe
        self.probe_error = probe_error            # 探活直接失败时的原话
        self.probes = 0
        self.page_info_probes = 0                 # 只数 doctor 那条 _post 探活
        self.acts = []                            # 按序记录动手行为

    def install(self):
        cli._alive = lambda: self.alive
        cli._healthy = self._probe_health
        cli._chrome_running = self._chrome_running
        cli._launch_chrome = self._launch
        cli._post = self._post
        # 真 extension_dir 返回 Path，假件也必须是 Path——返回 str 今天只是被
        # f-string 吃掉，等哪天代码用上 .name / `/` 拼接就会静默走偏
        cli.extension_dir = lambda: Path("E:/fake/extension")
        lifecycle.existing_daemon_pid = self._existing_pid
        lifecycle.stop_daemon = self._stop
        lifecycle.cleanup_pid = self._cleanup_pid
        lifecycle.spawn_daemon = self._spawn
        # 真 identify() 会往 127.0.0.1:28417 发请求——本机真有 daemon 在跑时
        # 测试就会读到它，结果随环境漂移。钉死。
        lifecycle.identify = lambda timeout=2.0: self.served_pid
        # 真 _port_in_use 会去连本机 28417——本机真有 daemon 时测试结果随环境漂
        cli._port_in_use = self._port_used
        cli._pid_file_is_ours = lambda port=None: self.pid_file_is_ours
        # 真函数会去 bind 本机端口，结果随环境漂
        cli._port_bind_denied = lambda port=None: False
        for n in _WAITS:                          # 测分支走向，不测耐心
            setattr(cli, n, 0.0)
        return self

    def _probe_health(self, timeout=8):
        self.probes += 1
        if self.probes == self.dies_on_probe:
            # 探到一半 daemon 自己退了 —— 进程没了，端口自然也就放开了
            self.alive = self.port_held = False
        return self.healthy

    def _port_used(self, port=None):
        if self.stale_starts_serving:
            self.alive = True          # 端口早就 bind 上了，这会儿刚开始应答
        return self.port_held

    def _chrome_running(self):
        self.acts.append("scan-chrome")
        return self.chrome

    def _launch(self, ext_dir=None):        # 跟真 _launch_chrome 的默认值保持一致
        self.acts.append(f"launch-chrome:{ext_dir}")
        if self.launch_error:
            return self.launch_error
        self.chrome = True
        return None

    def _existing_pid(self):
        return self.stale_pid

    def _cleanup_pid(self):
        self.acts.append("cleanup-pid")
        self.stale_pid = None

    def _stop(self):
        self.acts.append("stop-daemon")
        self.alive = self.port_held = False
        self.stale_pid = None

    def _spawn(self, port=None, allow_domains=None):
        # 签名必须跟真 spawn_daemon 一致，否则假件挡住的是调用约定本身
        self.acts.append(f"spawn-daemon:{port}")
        self.alive = self.spawn_serves
        self.port_held = True          # 起来了就占着端口（服不服务另说）
        return 4242

    def _post(self, path, data="", timeout=30):
        self.acts.append(f"post:{data}")
        if "reload_extension" in data:
            self.healthy = self.reload_heals
            return {"ok": True}
        if "page_info" in data:
            # doctor 直接用 _post 探扩展（不经 _healthy），所以这条路要单独建模。
            # 形状必须跟真 daemon 一致：不通时是 ok=True 而 result 里没 url
            # （get_page_info 吞异常），不是 ok=False —— 假件把它做成 ok=False
            # 就等于替被测代码挡掉了「只信 ok 会误报 PASS」那个真分支。
            self.page_info_probes += 1
            if self.probe_error is not None:
                return {"ok": False, "error": self.probe_error}
            if (self.wakes_on_probe is not None
                    and self.page_info_probes >= self.wakes_on_probe):
                self.healthy = True
            return {"ok": True,
                    "result": {"url": "https://example.test/"} if self.healthy else {}}
        return {"ok": True}


def _run_ensure(port=None):
    """跑一次 _ensure，返回 (退出码, 输出)。"""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cli._ensure(port)
    return rc, buf.getvalue()


def _run_doctor():
    """跑一次 _doctor，返回 (退出码, 输出)。"""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cli._doctor()
    return rc, buf.getvalue()


def test_all_green_touches_nothing():
    """已经端到端通的时候，ensure 不该去扫进程、更不该拉起任何东西。"""
    w = _World().install()
    try:
        rc, out = _run_ensure()
        assert rc == 0, out
        assert w.acts == [], f"快路径不该动手: {w.acts}"
        assert out.count("[PASS]") == 4, out          # 三项 + Ready
    finally:
        _restore()


def test_daemon_down_gets_spawned():
    w = _World(alive=False).install()
    try:
        rc, out = _run_ensure()
        assert rc == 0, out
        assert w.acts == ["scan-chrome", "spawn-daemon:None"], w.acts
        assert "[FIX ] Daemon" in out and "pid 4242" in out, out
    finally:
        _restore()


def test_reports_the_pid_that_actually_serves():
    """venv 的 python.exe 常是个 trampoline：它 spawn 出真解释器后自己留在中间，
    Popen 拿到的 pid 不是那个在服务的进程（本机实测 5740 → 4072）。
    打印的 pid 必须是能拿去 kill 的那个。"""
    w = _World(alive=False, served_pid=4072).install()
    try:
        rc, out = _run_ensure()
        assert rc == 0, out
        assert "pid 4072" in out and "pid 4242" not in out, out
    finally:
        _restore()


def test_port_is_passed_through_to_the_spawned_daemon():
    """--port 30500 --ensure 起的 daemon 必须监听 30500，否则起了个连不上的。"""
    w = _World(alive=False).install()
    try:
        rc, out = _run_ensure(30500)
        assert rc == 0, out
        assert "spawn-daemon:30500" in w.acts, w.acts
    finally:
        _restore()


def test_stale_pid_file_cleared_before_spawn():
    """端口没人监听（= 没有活着的 daemon）但 pid 文件还留着：清掉文件再起。
    清的只是文件，没杀任何东西——措辞也要这么写。"""
    w = _World(alive=False, port_held=False, stale_pid=999).install()
    try:
        rc, out = _run_ensure()
        assert rc == 0, out
        assert w.acts == ["scan-chrome", "cleanup-pid", "spawn-daemon:None"], w.acts
        assert "stop-daemon" not in w.acts, "端口空着说明进程早没了，没什么可停的"
        assert "cleared stale pid file (pid 999)" in out, out
    finally:
        _restore()


def test_never_stacks_a_second_daemon_on_a_held_port():
    """端口有人监听却不应答 —— **绝不 spawn**。

    真机测过：一条阻塞 6 秒的 exec 就能让一个完全健康的 daemon 变成
    `_alive()=False` / `identify()=None`（默认 exec 超时是 120 秒，忙上几分钟很正常）。
    从外面分不清「在忙」和「真僵」，而猜错的代价完全不对称：
    往上叠一个 daemon → Windows 的 SO_REUSEADDR 让它照样绑得上 → 两个进程抢同一端口，
    新的那个还会轮换共享令牌把老的打成 403。正是这条命令当初要防的事。
    """
    w = _World(alive=False, port_held=True, stale_pid=27620, served_pid=27620).install()
    try:
        rc, out = _run_ensure()
        assert rc == 1, out
        assert not any(a.startswith("spawn-daemon") for a in w.acts), \
            f"端口被占着就不许 spawn: {w.acts}"
        assert "cleanup-pid" not in w.acts and "stop-daemon" not in w.acts, \
            f"更不许去清一个活着的 daemon 的 pid 文件: {w.acts}"
        assert "[FAIL] Daemon" in out and "27620" in out, out
        assert "--stop" in out, "修不了就要给出人工处置办法"
        assert "was down" not in out, "它没死，别这么写"
    finally:
        _restore()


def test_an_unidentifiable_holder_is_not_blamed_on_some_other_daemon():
    """占着端口的东西认不出身份时，**不许**报一个从 pid 文件里捞出来的号码。

    pid 文件不认端口：真机上验过，用一个哑 socket 占住 28941，输出却是
    「pid 13308 holds :28941」—— 那是跑在 28417 上的健康 daemon。文档还教人
    照着这个 pid 去处置，等于指着无辜的进程说「杀它」。
    """
    w = _World(alive=False, port_held=True, stale_pid=13308, served_pid=None,
               pid_file_is_ours=False).install()
    try:
        rc, out = _run_ensure()
        assert rc == 1, out
        assert "13308" not in out, f"这个 pid 与该端口无关，不能报出来: {out}"
        # 而且要给一条真的走得通的路：--stop 在这个状态下只回「No daemon running.」
        assert "--port" in out, "认不出占用者时必须给出换端口这条出路"
    finally:
        _restore()


def test_a_busy_own_daemon_gets_the_long_budget_not_the_grace():
    """认得出是自家 daemon（identify 应答）时，等的是 25s 而不是 4s。

    这时「有个健康 daemon 在」的证据最强——它多半只是在跑一条长 exec（默认超时
    120 秒）。拿 4 秒判它死，等于每次有兄弟任务在跑就给一次假红。
    """
    seen = []
    real_wait = cli._wait
    w = _World(alive=False, port_held=True, served_pid=27620).install()
    cli.ENSURE_DAEMON_GRACE, cli.ENSURE_DAEMON_WAIT = 4.0, 25.0
    try:
        cli._wait = lambda pred, timeout, interval=0.5, note="": seen.append(timeout) or False
        with contextlib.redirect_stdout(io.StringIO()):
            cli._ensure_daemon()
        assert seen and seen[0] == 25.0, f"认得出是自家 daemon 就该给足耐心: {seen}"
    finally:
        cli._wait = real_wait
        _restore()


def test_a_busy_daemon_known_only_from_the_pid_file_still_gets_the_long_budget():
    """**忙碌 daemon 的 /pid 也是超时的**（真机量过：identify() → None），所以那条
    「认得出是自家 daemon」的判断在真实场景里唯一的来源就是 pid 文件回退。

    只用 served_pid 建夹具的话，把整段回退删掉都不会红（mutation 验过）——
    等于 N3 那个修复可以被静默删除。
    """
    seen = []
    real_wait = cli._wait
    w = _World(alive=False, port_held=True, served_pid=None,
               stale_pid=27620, pid_file_is_ours=True).install()
    cli.ENSURE_DAEMON_GRACE, cli.ENSURE_DAEMON_WAIT = 4.0, 25.0
    try:
        cli._wait = lambda pred, timeout, interval=0.5, note="": seen.append(timeout) or False
        with contextlib.redirect_stdout(io.StringIO()):
            cli._ensure_daemon()
        assert seen and seen[0] == 25.0, f"pid 文件认出自家 daemon 时也要给足耐心: {seen}"
    finally:
        cli._wait = real_wait
        _restore()


def test_an_unverified_pid_is_not_called_a_nekoro_daemon():
    """pid 来自 pid 文件（不认端口、不防 pid 复用）时，不能说成「已确认是 nekoro daemon」。
    真机复现过：端口文件缺失 + pid 文件里躺着个无关的 python 进程 → 输出把它点名成
    占着这个端口的 nekoro daemon，还叫人去 --stop。"""
    w = _World(alive=False, port_held=True, served_pid=None,
               stale_pid=12492, pid_file_is_ours=True).install()
    try:
        rc, out = _run_ensure()
        assert rc == 1, out
        assert "unverified" in out and "12492" in out, out
        assert "nekoro daemon (pid 12492)" not in out, "证据不够，别点名"
    finally:
        _restore()


def test_unverified_pid_gets_the_hedged_advice_too():
    """标题说了「unverified」，底下的建议就不能还是笃定那一套。

    「它多半在忙，实在不行 --stop」这句是给 identify() 核实过的那个 daemon 的。
    证据只有 pid 文件时，占用者可能压根不是 nekoro —— 这时 --stop 会回一句
    「No daemon running.」，把人堵死，所以要走给出路的那条分支。
    """
    w = _World(alive=False, port_held=True, served_pid=None,
               stale_pid=12492, pid_file_is_ours=True).install()
    try:
        rc, out = _run_ensure()
        assert rc == 1, out
        assert "unverified" in out, out
        assert "--port" in out, "没核实身份就得给换端口这条出路"
        assert "确认是真僵了再" not in out, "证据不够，别给笃定建议"
    finally:
        _restore()


def test_a_daemon_on_another_port_is_left_alone():
    """`--ensure --port 31999` 时，端口文件记着 28417 —— 那份 pid 文件属于另一个
    daemon。清它就是把人家的生命周期机制拆了，而人家还跑得好好的。"""
    import tempfile
    from nekoro_browser import config
    real_env = os.environ.get("NEKORO_DATA_DIR")
    w = _World(alive=False, port_held=False, stale_pid=7156).install()
    cli._pid_file_is_ours = _CLI_ORIG["_pid_file_is_ours"]   # 这条测的就是真判据本身
    try:
        # ignore_cleanup_errors：子进程可能还攥着日志句柄，Windows 删不掉
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            os.environ["NEKORO_DATA_DIR"] = d
            config.write_port_file(28417)
            _run_ensure(31999)
            assert "cleanup-pid" not in w.acts, f"别碰别人的 pid 文件: {w.acts}"
            assert any(a.startswith("spawn-daemon") for a in w.acts), w.acts
    finally:
        _restore()
        if real_env is None:
            os.environ.pop("NEKORO_DATA_DIR", None)
        else:
            os.environ["NEKORO_DATA_DIR"] = real_env


def test_starting_daemon_is_not_killed():
    """存量 daemon 只是还在启动：bind 已经完成（端口连得上）、还没开始应答。
    宽限期内它就服务了 —— 不该被当成僵尸清掉再起一个。

    注意 bind 之前那一小段（进程刚 spawn、端口还没起来）**谁也认不出来**：
    pid 文件也是 bind 之后才写的。两个 --ensure 掐在那一瞬间并发仍会各起一个，
    要根治得上锁文件，这里不做。"""
    w = _World(alive=False, port_held=True, stale_pid=555,
               stale_starts_serving=True).install()
    try:
        rc, out = _run_ensure()
        assert rc == 0, out
        assert w.acts == ["scan-chrome"], f"正在启动的 daemon 不该被动: {w.acts}"
        assert "was busy or still starting" in out, out
    finally:
        _restore()


def test_spawn_that_never_serves_fails_honestly():
    w = _World(alive=False, spawn_serves=False).install()
    try:
        rc, out = _run_ensure()
        assert rc == 1, out
        assert "[FAIL] Daemon" in out and "4242" in out, out
        assert "daemon.log" in out, "起不来时要指向日志，否则没线索"
        assert "[SKIP] Extension/SW" in out, "没有 daemon 就没法问扩展，别装作问过"
        assert not any(a.startswith("post:") for a in w.acts), w.acts
    finally:
        _restore()


def test_extension_unresponsive_reloaded_once():
    w = _World(healthy=False).install()
    try:
        rc, out = _run_ensure()
        assert rc == 0, out
        assert w.acts == ["scan-chrome", "post:await reload_extension()"], w.acts
        assert "reloaded service worker" in out, out
    finally:
        _restore()


def test_extension_dead_after_reload_fails_with_hint():
    w = _World(healthy=False, reload_heals=False).install()
    try:
        rc, out = _run_ensure()
        assert rc == 1, out
        posts = [a for a in w.acts if a.startswith("post:")]
        assert len(posts) == 1, f"reload 只重试一次，不转圈: {w.acts}"
        assert "[FAIL] Extension/SW" in out, out
        assert "chrome://extensions" in out, "修不好要给人工出口"
    finally:
        _restore()


def test_chrome_down_is_launched_with_extension():
    w = _World(chrome=False, alive=False).install()
    try:
        rc, out = _run_ensure()
        assert rc == 0, out
        assert f"launch-chrome:{Path('E:/fake/extension')}" in w.acts,             f"有扩展目录时要把它带上（旧版 Chrome/Chromium 上这个开关还有效）: {w.acts}"
        assert "[FIX ] Chrome" in out, out
    finally:
        _restore()


def test_chrome_launch_failure_is_not_green():
    """Chrome 起不来 → 扩展也必然连不上（它活在 Chrome 里）→ 退出码非 0。

    `healthy=False` 是这个世界的必要条件，不是装饰：Chrome 可执行文件都不存在、
    扩展却答得出 CDP 往返，这种世界物理上不存在。用它当夹具会把「进程扫描说了算」
    这个错误行为钉死（原来就是这么写的）。
    """
    w = _World(chrome=False, alive=False, healthy=False, reload_heals=False,
               launch_error="chrome executable not found").install()
    try:
        rc, out = _run_ensure()
        assert rc == 1, out
        assert "not found" in out, out
        assert "[FAIL] Extension/SW" in out, out
    finally:
        _restore()


def test_blind_process_scan_does_not_fail_a_working_stack():
    """进程扫不到 Chrome，但扩展答了一次真实 CDP 往返 —— 它就是在跑。

    扫描会瞎的真实原因不少：tasklist 被策略拦、机器上浏览器叫 chromium、
    容器里 pgrep 看不到宿主进程。把扫描算进结论 = 把好好的环境判死，
    而 agent 拿 `--ensure` 当闸门时会直接放弃任务。
    """
    w = _World(chrome=False, alive=True, healthy=False,
               launch_error="chrome executable not found").install()
    # 扩展在第二次探活时答上来（首次探活失败才会走到逐项检查）
    def _probe(timeout=8):
        w.probes += 1
        w.healthy = w.probes >= 2
        return w.healthy
    cli._healthy = _probe
    try:
        rc, out = _run_ensure()
        assert rc == 0, out
        assert "[WARN] Chrome" in out, "扫不到只能是 WARN，不能是 FAIL"
        assert "[PASS] Ready." in out, out
        assert "没扫到" in out, "要说清结论为什么仍然是绿的"
    finally:
        _restore()


def test_chrome_is_launched_even_without_an_extension_dir():
    """包里没有扩展目录也照样把 Chrome 拉起来。

    扩展**不是**命令行带进去的：Chrome 137 起 `--load-extension` 被停用，实测 151 上
    连最小合法 MV3 扩展都装不进去（干净 profile 里只剩内置扩展）。扩展靠的是它已经
    以「加载已解压」常驻在 profile 里。所以「没有扩展目录 → 启动也没意义」这个推断
    是错的，据此不启动等于白白少修一件能修的事。
    """
    w = _World(chrome=False, alive=True, healthy=False, reload_heals=True).install()
    cli.extension_dir = lambda: None
    try:
        rc, out = _run_ensure()
        assert rc == 0, out
        assert any(a.startswith("launch-chrome") for a in w.acts), \
            f"没有扩展目录也要启动 Chrome: {w.acts}"
    finally:
        _restore()


def test_daemon_that_exits_while_waiting_is_respawned():
    """冷启动真实场景：daemon 先起、Chrome 还在开，扩展迟迟不连 → daemon 自己退出
    （start() 里 auto_attach 抛 RuntimeError）。要认出来并补起一次。"""
    # 探活 #1 是 _ensure 的快路径（daemon 还活着，只是扩展不通）；#2 是扩展步——
    # 到这一步 daemon 已经因为等不到扩展而退了。
    w = _World(healthy=False, dies_on_probe=2).install()

    def _spawn(port=None, allow_domains=None):
        w.acts.append(f"spawn-daemon:{port}")
        w.alive, w.healthy = True, True       # 补起的这个赶上了扩展
        return 606
    lifecycle.spawn_daemon = _spawn
    try:
        rc, out = _run_ensure()
        assert rc == 0, out
        assert "spawn-daemon:None" in w.acts, f"daemon 中途退出必须补起: {w.acts}"
        assert not any(a.startswith("post:") for a in w.acts), \
            "补起后已经通了，不该再多发一次 reload"
        assert "after daemon respawn" in out, out
    finally:
        _restore()


def test_respawn_failure_does_not_ask_a_dead_daemon_to_reload():
    """daemon 退了又补不起来时，再去 /exec 发 reload 只会拿到一句
    『Daemon not running』当扩展的错因——误导。直接停在这里。"""
    w = _World(healthy=False, dies_on_probe=2, spawn_serves=False).install()
    try:
        rc, out = _run_ensure()
        assert rc == 1, out
        assert not any(a.startswith("post:") for a in w.acts), w.acts
        assert "could not be restarted" in out, out
    finally:
        _restore()


def test_daemon_dying_after_the_reload_attempt_is_still_respawned():
    """热路径的真实时序：daemon 要等满 ~20s（auto-attach 10s + _emit 的 ready_timeout
    10s）才因为等不到扩展而自杀，而热路径的探活预算只有 10s。所以它常常是**在
    reload 发出去之后**才死的——「daemon 没了吗」这一问必须放在 reload 之后，
    放前面就永远问不到，只会以 FAIL 收场。"""
    # 探活 #1 快路径、#2 扩展步首探、#3 是 reload 之后那一探——daemon 死在这一刻
    w = _World(healthy=False, reload_heals=False, dies_on_probe=3).install()

    def _spawn(port=None, allow_domains=None):
        w.acts.append(f"spawn-daemon:{port}")
        w.alive, w.healthy = True, True
        return 707
    lifecycle.spawn_daemon = _spawn
    try:
        rc, out = _run_ensure()
        assert rc == 0, out
        assert "post:await reload_extension()" in w.acts, "还活着的时候该先试 reload"
        assert "spawn-daemon:None" in w.acts, f"reload 之后死掉也要补起: {w.acts}"
        assert "after daemon respawn" in out, out
    finally:
        _restore()


def test_allow_domains_reaches_the_spawned_daemon():
    """`--allow-domains a.com --ensure` 起的 daemon 必须真的带着白名单。

    子进程读不到父进程的模块全局 `_ALLOW_DOMAINS`，漏传的话闸门被静默摘掉、
    ensure 还报绿——安全护栏无声降级，正是 AGENTS.md「不伪造成功」要挡的那类。
    """
    real = (cli._ALLOW_DOMAINS, lifecycle.spawn_detached)
    seen = []
    try:
        cli._ALLOW_DOMAINS = ["jd.com", "*.taobao.com"]
        lifecycle.spawn_detached = lambda cmd, log_path=None: seen.append(cmd) or 1
        w = _World(alive=False).install()
        cli._ALLOW_DOMAINS = ["jd.com", "*.taobao.com"]   # install() 不碰它，这里重申意图
        lifecycle.spawn_daemon = _LC_ORIG["spawn_daemon"]  # 用真的 spawn_daemon 拼命令行
        _run_ensure()
        assert seen, "没调到 spawn_detached"
        code = seen[0][-1]          # 按位置取容易被新加的 flag 挪走，取最后一个
        assert "['jd.com', '*.taobao.com']" in code, code
    finally:
        _restore()
        cli._ALLOW_DOMAINS, lifecycle.spawn_detached = real


def test_spawned_daemon_binds_the_port_the_client_probes():
    """端口文件里留着上一个 daemon 的非默认端口（它被硬杀、没来得及清）时，
    父进程探 30500、子进程按 daemon_port 算出 28417 —— 起了个自己都连不上的 daemon。
    子命令行里的端口必须是客户端口径算出来的那个。"""
    import tempfile
    from nekoro_browser import config
    real = (os.environ.get("NEKORO_DATA_DIR"), os.environ.get("NEKORO_PORT"),
            lifecycle.spawn_detached)
    seen = []
    try:
        # ignore_cleanup_errors：子进程可能还攥着日志句柄，Windows 删不掉
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            os.environ["NEKORO_DATA_DIR"] = d
            os.environ.pop("NEKORO_PORT", None)
            config.write_port_file(30500)
            lifecycle.spawn_detached = lambda cmd, log_path=None: seen.append(cmd) or 1
            lifecycle.spawn_daemon()                  # 不传 port，全靠解析
            assert "_run(30500)" in seen[0][-1], seen[0][-1]
    finally:
        lifecycle.spawn_detached = real[2]
        for k, v in (("NEKORO_DATA_DIR", real[0]), ("NEKORO_PORT", real[1])):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_launch_chrome_argv():
    """`_launch_chrome` 真正拼出来的命令行。这三件事错一件，登录态就没了或者根本起不来，
    而它们在 ensure 的测试里全被假件挡住了，从来没被验过。"""
    from pathlib import Path
    real = (cli.chrome_path, cli._chrome_profile_dir, lifecycle.spawn_detached)
    seen = []
    try:
        lifecycle.spawn_detached = lambda cmd, log_path=None: seen.append(cmd) or 1

        # 1) 找不到可执行文件 → 据实返回原因，且**不许**去 spawn
        cli.chrome_path = lambda: None
        assert cli._launch_chrome("E:/ext") == "chrome executable not found"
        assert seen == [], seen

        # 2) profile 目录不存在 → 干脆不传 --user-data-dir。传一个不存在的路径
        #    会让 Chrome 新建空 profile，登录态全丢。
        cli.chrome_path = lambda: Path("/fake/chrome")
        cli._chrome_profile_dir = lambda exe=None: None
        assert cli._launch_chrome("E:/ext") is None
        assert any(a.startswith("--load-extension=") for a in seen[0]), seen[0]
        assert not any(a.startswith("--user-data-dir") for a in seen[0]), seen[0]

        # 3) profile 目录在 → 必须传，否则开出来的是个没登录的新 profile
        prof = Path("/fake/profile")
        cli._chrome_profile_dir = lambda exe=None: prof
        assert cli._launch_chrome("E:/ext") is None
        assert f"--user-data-dir={prof}" in seen[1], seen[1]   # 分隔符跟平台走
    finally:
        (cli.chrome_path, cli._chrome_profile_dir, lifecycle.spawn_detached) = real


def test_chrome_profile_dir_requires_the_directory_to_exist():
    """按当前平台真跑一遍：目录不存在返回 None，建出来才返回它。"""
    import tempfile
    from pathlib import Path
    if sys.platform == "win32":
        var, sub = "LOCALAPPDATA", Path("Google") / "Chrome" / "User Data"
    elif sys.platform == "darwin":
        var, sub = "HOME", Path("Library") / "Application Support" / "Google" / "Chrome"
    else:
        var, sub = "HOME", Path(".config") / "google-chrome"
    real = os.environ.get(var)
    try:
        # ignore_cleanup_errors：子进程可能还攥着日志句柄，Windows 删不掉
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            os.environ[var] = d
            assert cli._chrome_profile_dir() is None, "目录不存在时必须返回 None"
            (Path(d) / sub).mkdir(parents=True)
            got = cli._chrome_profile_dir()
            assert got is not None and got.is_dir(), got
    finally:
        if real is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = real


def test_chrome_proc_pattern_follows_the_resolved_binary():
    """只装了 Chromium 的机器上，认进程不能拿 "chrome" 去匹配——
    "chromium" 里没有 "chrome" 这个子串，扫描永远落空 → 每跑一次 ensure
    就多开一个窗口，还每次都判失败。

    **必须把三个平台分支都跑一遍**：只测当前平台的话，POSIX 那段在这台
    Windows 机器上永远执行不到——把它改成写死 "chrome" 测试照样全绿（实测过）。
    """
    from pathlib import Path
    real_path, real_platform = cli.chrome_path, sys.platform
    try:
        cli.chrome_path = lambda: Path("/usr/bin/chromium")
        for plat in ("win32", "darwin", "linux"):
            sys.platform = plat
            pat = cli._chrome_proc_pattern()
            # darwin 上进程名是显示名（"Chromium" / "Google Chrome"），
            # 但同样要跟着解析到的可执行文件走，不能不看是哪个浏览器
            assert "chromium" in pat.lower(), (plat, pat)
        # Chrome（非 Chromium）时 POSIX 侧要两种写法都覆盖：which 到的是
        # google-chrome，实际进程 cmdline 却是 /opt/google/chrome/chrome
        cli.chrome_path = lambda: Path("/usr/bin/google-chrome")
        sys.platform = "darwin"
        assert cli._chrome_proc_pattern() == "Google Chrome", cli._chrome_proc_pattern()
        sys.platform = "linux"
        pat = cli._chrome_proc_pattern()
        assert "chrom(e|ium)" in pat, pat
        # 锚到路径分隔符/词尾：不然 chromedriver、开着 chrome.md 的编辑器、
        # 别的 agent 命令行里那句 --load-extension 都算「Chrome 在跑」，
        # 于是该拉起浏览器的时候不拉起。
        import re
        assert re.search(pat, "/opt/google/chrome/chrome --type=renderer"), pat
        assert re.search(pat, "/usr/bin/chromium"), pat
        assert not re.search(pat, "/usr/bin/chromedriver --port=9515"), pat
        assert not re.search(pat, "nvim /home/u/notes/chrome.md"), pat
    finally:
        sys.platform = real_platform
        cli.chrome_path = real_path


def test_chromium_is_not_pointed_at_google_chromes_profile():
    """Chromium 与 Google Chrome 的 profile 不通用。把 `~/.config/google-chrome`
    传给 chromium，轻则被拒开不起来，重则跨渠道写坏用户真正的 Chrome profile。"""
    import tempfile
    from pathlib import Path
    # Path.home() 走的是**真实 OS** 的 expanduser：Windows 只认 USERPROFILE、
    # 不认 HOME。伪造 sys.platform 只改被测分支，改不了这一点，两个都得设。
    _HOME_VARS = ("HOME", "USERPROFILE")
    real_platform = sys.platform
    real_home = {k: os.environ.get(k) for k in _HOME_VARS}
    try:
        sys.platform = "linux"
        # ignore_cleanup_errors：子进程可能还攥着日志句柄，Windows 删不掉
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            for k in _HOME_VARS:
                os.environ[k] = d
            (Path(d) / ".config" / "google-chrome").mkdir(parents=True)
            # chromium：Chrome 的 profile 在那儿摆着也不许用（它自己那份不存在 → None）
            assert cli._chrome_profile_dir(Path("/usr/bin/chromium")) is None
            (Path(d) / ".config" / "chromium").mkdir(parents=True)
            got = cli._chrome_profile_dir(Path("/usr/bin/chromium"))
            assert got is not None and got.name == "chromium", got
            # google-chrome 仍然拿 Chrome 那份
            got = cli._chrome_profile_dir(Path("/usr/bin/google-chrome"))
            assert got is not None and got.name == "google-chrome", got
    finally:
        sys.platform = real_platform
        for k, v in real_home.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_wait_probes_at_least_once_and_gives_up():
    calls = []
    assert cli._wait(lambda: calls.append(1) or True, 0.0) is True
    assert calls == [1], "timeout=0 也必须探一次"
    assert cli._wait(lambda: False, 0.05, interval=0.01) is False


def test_wait_actually_keeps_probing():
    """`_wait` 得真的是个「等」——不是「探一次就走」。

    所有 ensure 用例都把六个预算调成 0.0（测分支不测耐心），于是把 `_wait` 换成
    `return pred()` 全套照样绿（reviewer 用 mutation 证明过）。冷启动能成立全靠这份耐心，
    得有一条用例专门压着它。
    """
    calls = []
    assert cli._wait(lambda: calls.append(1) or len(calls) >= 3, 2.0, interval=0.01) is True
    assert len(calls) == 3, f"第一次为假之后必须接着探: {calls}"


def test_cold_start_gets_the_longer_extension_budget():
    """刚拉起 Chrome 时要给 service worker 更长时间连上来（45s vs 10s）。

    这条链路（`_ensure_chrome` 返回 cold → `_ensure` → `_ensure_extension`）整条删掉
    也没有用例会红（mutation 验过）——而它正是冷启动最吃紧的那一段。
    """
    seen = []
    real = cli._wait
    try:
        cli._wait = lambda pred, timeout, interval=0.5, note="": seen.append(timeout) or False
        cli.ENSURE_EXT_WAIT, cli.ENSURE_EXT_WAIT_COLD = 11.0, 47.0
        cli._alive = lambda: True
        cli._post = lambda *a, **kw: {"ok": True}
        cli._healthy = lambda timeout=8: False
        with contextlib.redirect_stdout(io.StringIO()):
            cli._ensure_extension(cold=False)
            cli._ensure_extension(cold=True)
        assert 11.0 in seen and 47.0 in seen, seen
    finally:
        cli._wait = real
        _restore()


def test_port_probe_survives_a_holder_that_never_accepts():
    """整条「绝不叠第二个 daemon」的闸门都压在这个原语上，而它在 ensure 的用例里
    永远被假件顶掉 —— 于是它自己坏掉时没有任何东西会红。

    connect 式探测在这里会**反向失灵**：内核把连接直接塞进 accept 队列，应用没
    accept 也算成功；队列一满 connect 就失败，「有人占着」被读成「没人占」。
    而队列填满恰恰发生在最需要认出占用者的那两种状态（wedged / 阻塞在长 exec，
    都不 accept）。实测 backlog=5 时第 6 次探测就翻车。
    """
    import socket
    srv = socket.socket()
    try:
        # 复刻 bridge.py 的 asyncio.start_server(..., reuse_address=True)：
        # 持有者带 SO_REUSEADDR，探测方必须仍然判得出「占着」
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(5)                      # 只 listen，从不 accept
        port = srv.getsockname()[1]
        for i in range(1, 13):             # 远超 backlog：探多少次都必须说「占着」
            assert cli._port_in_use(port) is True, f"第 {i} 次探测就把占用者读丢了"
    finally:
        srv.close()
    assert cli._port_in_use(port) is False, "占用者消失后必须说「空着」"


def test_output_survives_a_non_utf8_console():
    """输出被重定向到非 UTF-8 编码的流时不能崩。

    CLI 的提示里有 `→` 和中文。Windows 的控制台代码页默认不是 UTF-8（英文机 cp1252），
    重定向时 Python 按 locale 编码写 → UnicodeEncodeError 直接打断整条命令。而
    **用管道抓输出正是 agent 调这个 CLI 的常规姿势**。开发机 ACP=65001 永远撞不到，
    是 CI 的 cp1252 runner 把它照出来的（cli.py 里有 18 处 `→`，--doctor/setup 同样中招）。
    """
    import io as _io
    real_out, real_err = sys.stdout, sys.stderr
    buf = _io.BytesIO()
    wrapper = _io.TextIOWrapper(buf, encoding="cp1252", newline="")
    try:
        sys.stdout = sys.stderr = wrapper
        cli._make_output_encoding_safe()
        print("       → 多半是它正忙着跑一条长 exec")   # 不加保护这行直接抛
        wrapper.flush()
        written = buf.getvalue()          # 必须在 detach 前取：wrapper 关掉会连带关 buf
    finally:
        sys.stdout, sys.stderr = real_out, real_err
        try:
            wrapper.detach()              # 摘掉底层流，免得 GC 时把 buf 一起关了
        except ValueError:
            pass
    assert b"exec" in written, "内容要真的写出去，不能只是没报错"


def test_port_probe_ignores_time_wait_corpses():
    """`--stop` 之后端口上会留下一串 TIME_WAIT，那时**没有人在监听**，必须判「空着」。

    daemon 的 HTTP 响应带 `Connection: close`（bridge.py），每次调用都是 daemon 先关，
    于是 TIME_WAIT 落在 daemon 侧、local port 就是 28417。POSIX 上裸 bind 撞上它们
    直接 EADDRINUSE —— `--stop` 后最长一分钟（2MSL）内 --ensure 都会拒绝启动，
    而 daemon 自己（bridge 传了 reuse_address=True）本来起得来。Windows 无此问题。

    这条用例在 Windows 上恒绿、在 CI 的 ubuntu/macOS 上才真正吃劲——正因如此它必须
    存在：本机测不出来的东西，只能靠 CI 拦。
    """
    import socket
    import time as _t
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    port = srv.getsockname()[1]
    c = socket.socket()
    c.connect(("127.0.0.1", port))
    conn, _ = srv.accept()
    conn.close()            # server 先关 → TIME_WAIT 的 local port 就是 port
    c.close()
    srv.close()
    _t.sleep(0.3)
    assert cli._port_in_use(port) is False,         "只剩 TIME_WAIT、没人监听时必须判『空着』（POSIX 上要靠探测方设 SO_REUSEADDR）"


def test_probe_sets_reuseaddr_on_posix_and_not_on_windows():
    """两个平台语义相反，而开发机只有一个平台 —— 上面那条 TIME_WAIT 用例在 Windows 上
    恒绿（改坏了也不会红），所以必须另有一条直接盯住这个分支。

    POSIX 要设 SO_REUSEADDR：跳过 TIME_WAIT 的尸体，同时仍盖不过活着的 LISTEN。
    Windows 不能设：那边设了反而能**抢占**别人正在监听的端口，占用者会被读成不存在。
    """
    import socket
    real_socket, real_platform = socket.socket, sys.platform
    calls = []

    class _S:
        def setsockopt(self, level, opt, val):
            calls.append((level, opt, val))
        def bind(self, addr):
            raise OSError("boom")     # 走哪条分支才是重点，绑不绑得上无所谓
        def close(self):
            pass
    try:
        socket.socket = lambda *a, **kw: _S()
        for plat, want in (("linux", True), ("darwin", True), ("win32", False)):
            calls.clear()
            sys.platform = plat
            cli._port_in_use(28417)
            got = any(o == socket.SO_REUSEADDR for _, o, _ in calls)
            assert got is want, f"{plat}: SO_REUSEADDR 应为 {want}，实际 {got}"
    finally:
        socket.socket, sys.platform = real_socket, real_platform


def test_occupied_port_is_not_mistaken_for_a_denied_one():
    """「有人占着」和「系统不让绑」要分开：前者去找占用者，后者只能换端口。

    EACCES 那一侧本机实测过（Windows 保留段 3336/2869 → PermissionError
    errno=13 winerror=10013 → _port_bind_denied True），但保留段因机器而异、
    不适合写死进用例；这里钉住的是**不能把 EADDRINUSE 误分类成 EACCES**——
    分错了就会叫人去换端口，而其实只要停掉那个 daemon 就行。
    """
    import socket
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    port = srv.getsockname()[1]
    try:
        assert cli._port_in_use(port) is True
        assert cli._port_bind_denied(port) is False, "被占 ≠ 不让绑"
    finally:
        srv.close()
    assert cli._port_in_use(port) is False
    assert cli._port_bind_denied(port) is False


def test_denied_port_says_switch_ports_not_hunt_for_a_holder():
    """系统不让绑时（保留段 / 特权端口），没有占用者可找——别把人支去找。"""
    w = _World(alive=False, port_held=False).install()
    cli._port_bind_denied = lambda port=None: True
    try:
        rc, out = _run_ensure()
        assert rc == 1, out
        assert "reserved or privileged" in out, out
        assert "--port" in out, "唯一的出路是换端口"
        assert not any(a.startswith("spawn-daemon") for a in w.acts), w.acts
    finally:
        _restore()


def test_ensure_points_lifecycle_at_the_port_it_probes():
    """identify()/stop_daemon 走 lifecycle.URL（导入时冻结），_alive()/_port_in_use()
    每次现算。不同步的话，identify() 答的是另一个端口上的 daemon，而我们拿它的 pid
    去说「占着我这个端口」。"""
    seen = []
    real = lifecycle.set_port
    w = _World(alive=False).install()
    try:
        lifecycle.set_port = lambda port: seen.append(port)
        _run_ensure(30500)
        assert seen and seen[0] == 30500, f"探哪个端口就要把 lifecycle 指向哪个: {seen}"
    finally:
        lifecycle.set_port = real
        _restore()


def test_pid_file_ownership_when_the_port_file_is_missing():
    """端口文件不在时「这份 pid 是谁的」根本不知道。

    显式传了 `--port N` 就一律按「不是我的」处理：pid 文件本来就是 advisory，
    猜错的代价却是去清另一个活着的 daemon 的文件。没传端口时才当作自己的
    （默认端口上崩掉的 daemon 就会留下这种状态）。
    """
    import tempfile
    from nekoro_browser import config
    real_env = os.environ.get("NEKORO_DATA_DIR")
    try:
        # ignore_cleanup_errors：子进程可能还攥着日志句柄，Windows 删不掉
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            os.environ["NEKORO_DATA_DIR"] = d          # 空目录 = 没有端口文件
            # 端口文件缺失 = 不知道那份 pid 属于谁，两种情况一律不碰
            assert cli._pid_file_is_ours(None) is False, "不知道 = 不是我的"
            assert cli._pid_file_is_ours(31999) is False
            config.write_port_file(31999)
            assert cli._pid_file_is_ours(31999) is True
            assert cli._pid_file_is_ours(28417) is False
    finally:
        if real_env is None:
            os.environ.pop("NEKORO_DATA_DIR", None)
        else:
            os.environ["NEKORO_DATA_DIR"] = real_env


def test_spawn_detached_survives_a_missing_data_dir():
    """全新安装、第一条命令就是 `--ensure` 时数据目录还不存在。

    spawn_detached 拿它当 cwd，不先建就直接 NotADirectoryError —— Chrome 那步
    以一句「launch failed: [WinError 267]」告吹，跑第二次又莫名其妙好了
    （因为期间 daemon 起过一次把目录建了）。CI 上更直接：全新 runner 的家目录里
    没有这个目录，这条用例自己就会红。
    """
    import tempfile
    import time
    from pathlib import Path
    real_env = os.environ.get("NEKORO_DATA_DIR")
    try:
        # ignore_cleanup_errors：子进程可能还攥着日志句柄，Windows 删不掉
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            os.environ["NEKORO_DATA_DIR"] = str(Path(d) / "never" / "created")
            log = Path(d) / "sub.log"
            pid = lifecycle.spawn_detached(
                [sys.executable, "-c", "import os;print(os.getcwd())"], log)
            assert isinstance(pid, int) and pid > 0
            import time as _t
            from nekoro_browser import paths
            want, got = paths.data_dir(), ""
            for _ in range(50):
                got = log.read_text(errors="replace").strip() if log.is_file() else ""
                if got:
                    break
                _t.sleep(0.1)
            # 同时钉住两件事：目录被建出来了，且子进程**真的落在这个目录里**
            # ——不接管 cwd 的话，daemon 会攥着调用方的目录（Windows 上锁死它）
            # macOS 的 /var 是 /private/var 的 symlink，两边都 resolve 再比
            assert Path(got).resolve() == want.resolve(),                 f"子进程 cwd 应为 {want}，实际 {got!r}"
    finally:
        if real_env is None:
            os.environ.pop("NEKORO_DATA_DIR", None)
        else:
            os.environ["NEKORO_DATA_DIR"] = real_env


def test_oversized_daemon_log_is_trimmed_at_spawn():
    """daemon.log 每次冷启动失败都追加一段 traceback，没人裁就无限长。"""
    import tempfile
    from pathlib import Path
    real_env = os.environ.get("NEKORO_DATA_DIR")
    try:
        # ignore_cleanup_errors：子进程可能还攥着日志句柄，Windows 删不掉
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            os.environ["NEKORO_DATA_DIR"] = d
            log = lifecycle.daemon_log_path()
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_bytes(b"x" * (lifecycle.LOG_MAX_BYTES + 1))
            lifecycle.spawn_detached([sys.executable, "-c", "pass"], log)
            assert log.stat().st_size <= lifecycle.LOG_MAX_BYTES, log.stat().st_size
            # 没超的时候不许乱动（日志是排障线索，不是缓存）
            log.write_bytes(b"keep me")
            lifecycle.spawn_detached([sys.executable, "-c", "pass"], log)
            assert b"keep me" in log.read_bytes(), log.read_bytes()[:40]
    finally:
        if real_env is None:
            os.environ.pop("NEKORO_DATA_DIR", None)
        else:
            os.environ["NEKORO_DATA_DIR"] = real_env


def test_chrome_running_asks_the_os_about_the_right_process():
    """`_chrome_running` 决定要不要开浏览器，却在所有 ensure 用例里都被假件顶掉了。
    这里盯住它真正下的那条命令 + 判定方式。

    `chrome_path` 必须一起钉住：只伪造 sys.platform 的话，模式串仍来自这台机器上
    真实解析到的可执行文件 —— 在 ubuntu runner（CI 矩阵里有）上会解析成
    `google-chrome`，于是 `"google-chrome" in 'chrome.exe,…'` 为假，用例在 CI 上红。
    """
    import subprocess as sp
    from pathlib import Path
    real_run, real_platform, real_path = sp.run, sys.platform, cli.chrome_path
    # 只能用「在两个平台上 .name 都等于 chrome.exe」的写法：POSIX 上反斜杠不是分隔符，
    # 传 Windows 全路径的话 .name 会是整条字符串，模式串就跟 tasklist 输出对不上了
    cli.chrome_path = lambda: Path("chrome.exe")
    calls = []

    class _R:
        def __init__(self, out="", rc=0):
            self.stdout, self.returncode = out, rc
    try:
        sys.platform = "win32"
        sp.run = lambda cmd, **kw: calls.append(cmd) or _R(out="chrome.exe,\"1234\"")
        assert cli._chrome_running() is True
        assert calls[0][0] == "tasklist" and any("IMAGENAME eq" in a for a in calls[0]), calls[0]
        # tasklist 没匹配上时会打印一句本地化的「没有运行的任务」，不含进程名
        sp.run = lambda cmd, **kw: _R(out="信息: 没有运行的任务匹配指定标准。")
        assert cli._chrome_running() is False
        # 探测本身炸了（权限/策略拦截）→ 当作没在跑，绝不能抛出去
        sp.run = lambda cmd, **kw: (_ for _ in ()).throw(OSError("blocked"))
        assert cli._chrome_running() is False

        sys.platform = "linux"
        calls.clear()
        sp.run = lambda cmd, **kw: calls.append(cmd) or _R(rc=1)
        assert cli._chrome_running() is False, "pgrep 返回 1 = 没找到"
        assert calls[0][0] == "pgrep" and calls[0][1] == "-f", calls[0]
    finally:
        sp.run, sys.platform = real_run, real_platform
        cli.chrome_path = real_path


def test_token_mismatch_is_blamed_on_the_daemon_not_the_extension():
    """403 令牌不匹配是 daemon 侧的事（多半有第二个 daemon 轮换了共享令牌），
    扩展是无辜的。指错方向的代价很实在：人会跑去 chrome://extensions 反复重载
    一个根本没坏的扩展。--doctor 有这条分支，ensure 不能比它还差。"""
    w = _World(healthy=False).install()
    cli._post = lambda *a, **kw: {"ok": False,
                                  "error": "Forbidden: bad/missing token (restart daemon?)"}
    try:
        rc, out = _run_ensure()
        assert rc == 1, out
        assert "[FAIL] Daemon" in out and "token" in out.lower(), out
        assert "chrome://extensions" not in out, "别把人指去修没坏的东西"
        assert "--stop" in out, "要给出真正管用的处置办法"
    finally:
        _restore()


def test_main_routes_to_ensure():
    called = []
    real, argv = cli._ensure, sys.argv
    cli._ensure = lambda port=None: (called.append(port), 7)[1]
    sys.argv = ["nekoro-browser", "--ensure"]
    try:
        try:
            cli.main()
            assert False, "--ensure 应 sys.exit"
        except SystemExit as e:
            assert e.code == 7, f"退出码应来自 _ensure，got {e.code}"
        assert called == [None], called
    finally:
        sys.argv = argv
        cli._ensure = real


def test_stop_does_not_claim_no_daemon_while_the_port_is_held():
    """--stop 有和 --doctor 同一个盲区，而 doctor 的新提示正好把人指过来。

    忙碌的 daemon 答不上 ping，但端口占着。`--stop` 只看 ping 时会回一句
    「No daemon running.」并 exit 0——而 doctor 的 WARN 说的是「确认是真僵了再
    nekoro-browser --stop」。两条提示接起来是死路：诊断说被占着、停止说没在跑，
    daemon 其实还活着。实测复现过（netstat 全程 LISTENING，--stop 仍报没在跑）。

    所以判据要和 doctor / ensure 统一成 bind 探测，且这种情况必须非 0 退出。
    """
    w = _World(alive=False, port_held=True, stale_pid=8828).install()
    argv = sys.argv
    sys.argv = ["nekoro-browser", "--stop"]
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            try:
                cli.main()
                assert False, "端口被占着却没有非 0 退出"
            except SystemExit as e:
                assert e.code == 1, f"应以 1 退出，got {e.code}"
        out = err.getvalue()
        assert "No daemon running" not in out, f"端口占着还说没在跑: {out}"
        assert "8828" in out and "held" in out, out
        assert "stop-daemon" not in w.acts, "应答不了 HTTP 的 daemon 优雅停不掉，别装作停了"
    finally:
        sys.argv = argv
        _restore()


def test_stop_still_says_no_daemon_when_the_port_is_really_free():
    """端口真空着时不能被上面那条防护带偏——照常报「没在跑」并正常退出。"""
    w = _World(alive=False, port_held=False).install()
    argv = sys.argv
    sys.argv = ["nekoro-browser", "--stop"]
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            cli.main()            # 不该抛 SystemExit
        assert "No daemon running" in err.getvalue(), err.getvalue()
    finally:
        sys.argv = argv
        _restore()


def test_doctor_stays_diagnostic_only():
    """--doctor 是纯诊断：不许因为加了 ensure 就顺手动手修。

    世界必须是「daemon 活着但扩展不响应」——正是 doctor 最有可能被顺手加上修复的那个
    分支。用 alive=False 是测不出来的：doctor 会在第一个 if 就早退，后面塞什么都跑不到
    （原来就是这么写的，往 doctor 里插一句 stop_daemon() 照样 ALL OK）。
    """
    w = _World(alive=True, healthy=False).install()
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            cli._doctor()
        # doctor 自己那次 page_info 探活是诊断，不算动手；除此之外一个都不许有
        repairs = [a for a in w.acts if not a.startswith("post:await page_info()")]
        assert repairs == [], f"doctor 不该动手: {w.acts}"
    finally:
        _restore()


def test_doctor_retries_a_sleeping_worker():
    """MV3 的 SW 空闲会被 Chrome 回收，第一次探活正是把它叫醒的那一次，往往赶不上
    自己那 8 秒窗口。只报第一次的结果 = 把健康环境判成「扩展没连上」，人就去
    chrome://extensions 反复重载一个没坏的扩展（实测冷启动六轮四轮首探失败，
    紧接着的第二次六轮全过）。"""
    w = _World(alive=True, healthy=False, wakes_on_probe=2).install()
    try:
        rc, out = _run_doctor()
        assert rc == 0, out
        assert "[PASS] Extension/SW" in out, out
        assert w.page_info_probes == 2, f"该探两次: {w.page_info_probes}"
    finally:
        _restore()


def test_doctor_retry_is_one_extra_probe_not_a_loop():
    """扩展是真没连上时，重试只加一次，不许转圈——每次探活是 8 秒，
    转圈就把诊断变成了挂起。"""
    w = _World(alive=True, healthy=False).install()
    try:
        rc, out = _run_doctor()
        assert rc == 1, out
        assert "[FAIL] Extension/SW" in out, out
        assert w.page_info_probes == 2, f"只该探两次: {w.page_info_probes}"
    finally:
        _restore()


def test_doctor_does_not_retry_a_token_mismatch():
    """令牌对不上是确定性失败（多半是第二个 daemon 轮换了共享令牌），
    重试只是白等 8 秒；而且要指向 daemon，不能把人指去查扩展。"""
    w = _World(alive=True, healthy=False,
               probe_error="Forbidden: bad/missing token (restart daemon?)").install()
    try:
        rc, out = _run_doctor()
        assert rc == 1, out
        assert "[FAIL] Token" in out, out
        assert "[FAIL] Extension/SW" not in out, out
        assert w.page_info_probes == 1, f"不该重试: {w.page_info_probes}"
    finally:
        _restore()


def test_doctor_does_not_blame_the_token_for_any_error_mentioning_it():
    """判据是 _post 为 403 造的那句原话，不是宽泛的 "token" 子串——daemon 侧任何
    提到 token 的 traceback 都会被误判成令牌问题（doctor 原来就是这么写的），
    然后把人指去 --stop 重启，而真正坏的是别的东西。"""
    w = _World(alive=True, healthy=False,
               probe_error="RuntimeError: tokenizer failed on page text").install()
    try:
        rc, out = _run_doctor()
        assert rc == 1, out
        assert "[FAIL] Token" not in out, out
        assert "[FAIL] Extension/SW" in out, out
    finally:
        _restore()


def test_doctor_reports_the_extension_even_without_a_daemon():
    """daemon 没跑时也要给扩展那一格——早退只打一行，会让人以为诊断只有 daemon
    一项，而「装完第一次跑」最常见的恰恰是扩展那半边没弄好。"""
    w = _World(alive=False).install()
    try:
        rc, out = _run_doctor()
        assert rc == 1, out
        assert "Extension/SW" in out, out
        assert w.page_info_probes == 0, "没有 daemon 可问，不该发探活"
    finally:
        _restore()


def test_doctor_explains_the_extension_error_badge_when_the_daemon_is_down():
    """daemon 不在时扩展一直重连，每次失败 Chrome 都记一条，chrome://extensions 上
    就是红色「错误」徽章。那条消息由网络栈发出、扩展侧抑制不了，人看到的第一反应
    永远是「扩展坏了」→ 去卸载重装。诊断必须当场把它解释掉。"""
    _World(alive=False).install()
    try:
        rc, out = _run_doctor()
        assert rc == 1, out
        assert "chrome://extensions" in out and "不是扩展坏了" in out, out
    finally:
        _restore()


def test_doctor_does_not_call_a_busy_daemon_dead():
    """ping 不应答 ≠ 没在跑。一条阻塞的 exec 就能占死单线程事件循环——daemon 活着、
    端口占着，只是这几秒答不上 ping。只看 ping 的话 doctor 会给出**反向结论**
    （not running + 建议 start），而再起一个会让两个 daemon 抢同一端口、轮换令牌，
    把原来那个打成 403。干净环境实测过：netstat 明明 LISTENING，doctor 说 not running。"""
    w = _World(alive=False, port_held=True, stale_pid=777).install()
    try:
        rc, out = _run_doctor()
        assert rc == 1, out
        assert "not running" not in out, f"端口占着还说没在跑，是反向结论: {out}"
        assert "held" in out and "777" in out, out
        assert w.page_info_probes == 0, "答不上 ping 的 daemon 不必再去探扩展"
    finally:
        _restore()


def test_doctor_exit_code_follows_the_verdict():
    """退出码恒 0 时，拿 doctor 当就绪门禁的脚本/agent（README 收尾那步就是这么用的）
    永远看到绿灯。三种世界必须给出三种口径一致的退出码。"""
    try:
        _World(alive=True, healthy=True).install()
        assert _run_doctor()[0] == 0
        _restore()
        _World(alive=True, healthy=False).install()
        assert _run_doctor()[0] != 0
        _restore()
        _World(alive=False).install()
        assert _run_doctor()[0] != 0
    finally:
        _restore()


def test_doctor_does_not_call_a_broken_page_info_healthy():
    """ok=True 但 result 里没 url = get_page_info 吞了异常。只信 ok 就会误报 PASS。"""
    w = _World(alive=True, healthy=False).install()
    try:
        assert w._post("/exec", "await page_info()") == {"ok": True, "result": {}}, \
            "假件得复现真 daemon 的形状，否则挡掉的正是被测分支"
        rc, out = _run_doctor()
        assert rc == 1 and "[PASS] Extension/SW" not in out, out
    finally:
        _restore()


def test_existing_daemon_pid_ignores_dead_pid():
    """pid 文件里躺着一个早死的 pid：必须报『没有存量』，否则永远不敢 spawn。"""
    real_identify, real_read = lifecycle.identify, lifecycle.read_pid_file
    try:
        lifecycle.identify = lambda timeout=2.0: None
        lifecycle.read_pid_file = lambda: 2 ** 31 - 5      # 不可能存在的 pid
        assert lifecycle.existing_daemon_pid() is None
        lifecycle.read_pid_file = lambda: os.getpid()      # 活着的进程 → 认它
        assert lifecycle.existing_daemon_pid() == os.getpid()
    finally:
        lifecycle.identify, lifecycle.read_pid_file = real_identify, real_read


def test_spawn_detached_really_starts_a_process():
    """真起一个子进程：detached 不阻塞、日志能落盘。只用 python -c 打一行，不碰 daemon。"""
    import tempfile
    import time
    from pathlib import Path
    # ignore_cleanup_errors：子进程可能还攥着日志句柄，Windows 删不掉
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        log = Path(d) / "sub.log"
        pid = lifecycle.spawn_detached(
            [sys.executable, "-c", "print('nekoro-detached-ok')"], log)
        assert isinstance(pid, int) and pid > 0
        for _ in range(50):
            if log.is_file() and "nekoro-detached-ok" in log.read_text(errors="replace"):
                break
            time.sleep(0.1)
        else:
            seen = log.read_text(errors="replace") if log.is_file() else "(无文件)"
            assert False, f"子进程没写出日志: {seen}"


def test_spawn_daemon_command_does_not_hit_pipe_mode():
    """detached daemon 的 stdin 不是 tty；若走 `-m nekoro_browser.cli`，CLI 会落进
    管道模式读到空输入直接退出——daemon 根本起不来。命令行必须直接调 _run。"""
    real = lifecycle.spawn_detached
    seen = []
    try:
        lifecycle.spawn_detached = lambda cmd, log_path=None: seen.append(cmd) or 1
        lifecycle.spawn_daemon(30500)
        cmd = seen[0]
        assert cmd[0] == sys.executable and cmd[-2] == "-c", cmd
        assert "_run(30500)" in cmd[-1], cmd[-1]
        assert "-m" not in cmd, cmd
        # -P：别把调用方的工作目录塞进 daemon 的 sys.path[0]。少了它，从 src/ 下
        # 跑一次 --ensure，daemon 就静默用源码树而不是装好的包。
        assert "-P" in cmd, cmd
    finally:
        lifecycle.spawn_detached = real


if __name__ == "__main__":
    # **自动发现，不写清单。** 手写清单漏登记一个，用例就永远不执行、却照报 ALL OK
    # ——本文件真栽过：新增的 doctor 用例只是忘了加进清单，mutation 把修复改回旧行为
    # 时全绿，差点当成「测过了」。仓库里另外两个测试文件早就是这个写法。
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_"):
            _fn()
    print("ALL OK")
