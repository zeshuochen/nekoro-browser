# nekoro-browser × ego-lite 借鉴实现文档

> 状态：批次 0-6 全部完成；PR review 的整改（可见性歧义、nth 边界、refs 往返、pyright 口径）见 §6
> 创建：2026-08-05
> 关联仓库：https://github.com/zeshuochen/nekoro-browser

## 1. 背景与动机

学习 [ego-lite](https://github.com/citrolabs/ego-lite)（citrolabs，7.8k stars，为 AI agent 构建的浏览器）
后，对照 nekoro-browser 现状提炼出差距。ego-lite 最有价值的设计在 `element-resolver.ts`
（统一 locator + transient/permanent 错误分类）、`ref-map`（backendNodeId 跨轮次稳定定位）、
`task-spaces`（空间隔离 + 人机交接）、`learning/`（站点经验 manifest 结构）。

其中大部分思路 nekoro 已有更优或等价实现（code-based 脚本、domain-skills 经验推送、
openOrReuseTab、agent_helpers 热加载），真正有差距的是**元素定位与错误处理**：

| ego-lite 机制 | nekoro 现状 | 差距 |
|---|---|---|
| 统一 locator 字符串（css:/text:/xpath=/role:…） | click_selector / click_text / click_index 三个 helper 各管一种 | 已补（批次 0） |
| transient/permanent 错误分类（重试/放弃决策信号） | 一律 "element not found"，无分类 | 已补（批次 0） |
| 多匹配即歧义 → permanent，不静默点第一个 | 扩展 getRect 用 querySelector 静默取第一个 | 已补（批次 0，css/xpath/placeholder） |
| backendNodeId ref 跨轮次稳定定位 | state() 序号，DOM 一变即失效 | 已补（批次 4） |
| box-model 守卫（非零尺寸才可点） | `if not rect.get("x")` 脆弱判空 | 已补（批次 2） |
| 下载管理 waitForEvent("download") | 无 | 已补（批次 5） |

## 2. 现状核查结论（2026-08-05）

细心核查 LSP 报错 + explore 全量梳理后确认以下**真实问题**（非噪音）：

1. **循环导入（真问题）**：`helpers.py:15 from . import site_notes` ↔
   `site_notes.py:116 from . import helpers as h`。运行时侥幸未炸（site_notes 的 import 在
   `_inject_helpers()` 函数内部惰性执行），但属设计缺陷，pyright 报 cycle。
2. **脆弱判空 ×3**：`click_selector:936` / `click_text:980` / `click_index:995` 都用
   `if not rect or not rect.get("x")` 判断矩形（x==0 时误判为未找到）。新 `click()` 已用
   `rect.get("x") is None`（helpers.py:900），旧三处待统一。
3. **无错误分类**：旧定位 helper 失败一律 `{"ok": false, "error": "..."}`，agent 无法判断
   「重试有用」（transient）还是「换策略」（permanent）。仅新 `click()` 带 `kind`。
4. **wait_selector 语义含糊**：扩展侧返回裸字符串（`'visible'|'timeout:sel'|'no-selector'`），
   helpers 原样透传，不区分「瞬时未找到（可重试）」vs「选择器本身无效」。
5. **定位 op 返回形状不一致**：getRect* → `null`、state → `{error:'not-found'}`、
   findText → `[]`、box → `{found:false}`——同为元素定位，四种空态表达。
6. **类型注解缺失（风格级）**：helpers.py 大量 `-> dict` / `-> list[dict]` 裸注解，
   pyright 严格模式全报 `Expected type arguments`。非运行时错误，CI 未跑 pyright，
   但按「不傲慢对待报错」原则列入批次 6 处理。

## 3. 分批实施计划

原则：**有差距的分批次改，每批可独立验证、可独立合入**，不搞一次大改。

### 批次 0（✅ 已完成，2026-08-05）

- `click(loc)` 统一 locator 点击：css:/text:/index:/xpath:/placeholder: + `nth:N;` 前缀
- 错误分类：`transient`（未找到，可重试）/ `permanent`（多匹配歧义、非法选择器、index 非数字）
- `ensure_tab(url)`：ego `openOrReuseTab` 移植（复用优先）
- 测试：`tests/test_click_loc.py`（16 用例）；全量 26 测试通过
- 文档：README 中英致谢、SKILL.md×2 命令表
- 教训：`nth:` 解析边界 bug（`5 < semi` 应为 `semi >= 5`），由测试捕获

### 批次 1：拆循环导入（✅ 已完成，2026-08-05）

- 目标：消灭 `helpers ↔ site_notes` 循环依赖
- 落地：`site_notes._inject_helpers(mod, helpers_module)` 参数注入；`load_functions(helpers_module)`
  必传，由 daemon（`_on_exec` 里已有的 `h`）传入。site_notes.py 不再有任何对 helpers 的 import
- 验收：`python -c "import nekoro_browser.helpers"` 干净；pyright 不再报 cycle；全量测试通过 ✅
- 风险：低（纯结构重组；test_site_notes.py 4 处调用同步加参）

### 批次 2：判空修正 + box-model 守卫（✅ 已完成，2026-08-05）

- 目标：定位点击前验证元素非零尺寸，杜绝静默点空位/误判
- 落地：`click_selector` / `click_text` / `click_index` 的 `not rect.get("x")` →
  `rect.get("x") is None`（x==0 合法，与 `click()` 统一入口一致）
- 验收：x==0/y==0 元素可点击（test_click_loc 补 3 用例）；全量测试通过 ✅
- 风险：低

### 批次 3：旧 click_* 错误分类补全 + wait_selector 语义澄清（✅ 已完成，2026-08-05）

- 目标：让**所有**定位 helper 的错误都带 `kind` 决策信号，不只新 `click()`
- 落地：
  - `click_selector` / `click_text` / `click_index` 失败返回补 `kind: "transient"`
  - `wait_selector`：扩展返回 `'timeout:sel'` → `kind: "transient"`；`'no-selector'` →
    `kind: "permanent"`；正常状态（visible 等）不带 kind。结构不变（ok/result 与旧版一致）
- 未做：定位 op 空态归一（box/state/findText 的空结果是**正常查询语义**，不是错误，保持）
- 验收：test_click_loc 补 6 用例（x==0 ×3、kind ×3 + wait_selector ×3）；全量测试通过 ✅
- 风险：中（错误返回加字段向后兼容——agent 只读 ok/error 不受影响；已确认无断言错误 dict 恰等某形状的测试）

### 批次 4：backendNodeId ref 跨轮次稳定定位（✅ 已完成，2026-08-05）

- 目标：让元素句柄在 DOM 小变化后仍可定位（ego `@N` ref 思路）
- MVP：真实浏览器验证 `DOM.getDocument`/`querySelectorAll`/`describeNode`/`resolveNode`/
  `getBoxModel` 在 chrome.debugger tab attach 下全部可用，且 **backendNodeId 在 DOM 前插
  元素后仍解析到同一元素**（跨轮次稳定 ✅）。data URL 导航被 Chrome 拦截（MVP 教训：用真实 URL）
- 落地（纯 Python 层，不动扩展）：
  - `refs()`：DOM 域枚举可交互元素 → [{ref(backendNodeId), tag, text}]
  - `click_ref(ref)`：resolveNode + getBoxModel 中心坐标 → click_at_xy；失效 → kind:transient
- 验收：端到端（真实浏览器）——refs 拿 ref → click_ref 点击按钮 onclick 生效 → DOM 前插
  后 click_ref 仍命中 ✅；单元测试 7 用例（test_refs_downloads.py）✅
- 风险：中（MVP 已验证 CDP 链路；无需改扩展）

### 批次 5：下载管理（✅ 已完成，2026-08-05）

- 目标：`wait_for_download()` helper（ego `page.waitForEvent("download")` 思路）
- MVP：`Browser.setDownloadBehavior`/`Browser.enable` 被 tab attach 拒绝（browser-level 命令
  -32601/-32000）——**路径无法自定义**；但 `Page.downloadWillBegin`/`downloadProgress` 事件
  经扩展 onEvent 转发已可用（Chrome 同源自动下载拦截是 MVP 干扰项，换源验证通过）
- 落地：`wait_for_download(timeout)` 轮询 drain_events，等 downloadWillBegin（filename/url）→
  downloadProgress completed → 返回 {url, filename, bytes}；canceled → permanent；超时 → transient
- 验收：端到端（真实浏览器）——真实点击下载链接 → wait_for_download 返回
  {filename, bytes} ✅；单元测试 5 用例 ✅
- 风险：中（MVP 已验证事件链；下载落 Chrome 默认目录，路径不可自定义——已在 docstring 说明）

### 批次 6：类型注解卫生（✅ 已完成，2026-08-05）

- 目标：消灭 pyright 严格模式报错（不降级配置，通过修代码达成）
- 落地：
  - helpers.py：45+ 处 `-> dict` → `-> dict[str, Any]`；13 处 `tab: int = None` →
    `tab: int | None = None`（sel/url/selector 同理）；动态构建的 res/out 显式标注；
    `list_site_actions` 漏传 helpers_module 修复（批次 1 漏网）
  - site_notes.py：`spec_from_file_location` 返回 Optional——None 时记错误跳过
    （此前 spec.loader 会炸的真隐患）
  - daemon.py：`_site_errors` 在 __init__ 初始化；Queue/list/dict 补泛型
  - pyproject.toml：`[tool.pyright]` 严格模式 + `extraPaths=["src"]`
    （tests 的 sys.path hack 由静态配置解析，非降级）
  - tests：FakeDaemon 重构——真实 bridge 类替代运行时动态挂属性
    （删掉未触发的 `__post_init__` 死代码）
- 验收：全项目 pyright 零 error（src ×3 + tests）✅；27 测试全过 ✅

## 4. 批次间依赖

```
批次 0（定位入口） ──▶ 批次 2（判空守卫，增强 click 系列）
批次 1（结构）      ──▶ 独立
批次 2（判空）      ──▶ 批次 3（错误分类，同一批函数顺手补）
批次 3（分类）      ──▶ 批次 4（ref 依赖判空语义）
批次 5（下载）      ──▶ 独立
批次 6（类型）      ──▶ 最后做（其余批次完成后统一）
```

## 5. 验收与复盘

每批合入前：全量测试（`for f in tests/test_*.py; do uv run python $f; done`）+
LSP 无新增 error。批次完成后在本文件追加复盘（实际产出 vs 预期、偏差原因、下次改进）。

### 复盘（2026-08-05）

**实际产出 vs 预期**：批次 0-6 全部完成，功能与计划一致。偏差一处——批次 3 原计划的
「定位 op 空态归一」没做：查证后 box/state/findText 的空结果（`[]`/`{found:false}`）是
**正常查询语义**不是错误，强改成错误返回反而是错的，保留原状。

**偏差原因（三条根因）**：
1. **批次 0 只做了 `ensure_tab` 被批"学得太浅"**——根因是只看了 ego-lite 的 README 没读
   源码，真正的价值（element-resolver 的统一 locator / transient-permanent 分类 /
   backendNodeId ref）全在实现文件里。用户一句"不可能吧？我不是本来就有这功能吗"点醒。
2. **误报 6 个测试失败**——根因是 PowerShell 的 `-notmatch` 对数组返回过滤结果而非布尔值，
   检测脚本 `$out -notmatch "ALL OK"` 永远为真。换成退出码判断后正常。
3. **MVP 环境干扰**——`data:` URL 导航被 Chrome 拦截、同源多次自动下载被拦。根因是对
   Chrome 的自动下载策略（同一源限次）没预期，换真实 URL / 换源验证通过。

**下次改进（行为规则级）**：
- 学习开源项目先读 `AGENTS.md` + 核心实现文件再下结论，README 只是门面
- 测试"全过"的判断用退出码，不用字符串匹配
- 新功能先 MVP 验证核心链路（批次 4/5 先验 CDP 再写完整 helper，零返工）

## 6. PR #1 review 整改（2026-08-06）

review 挖出 4 类问题，都不是"再加个功能"，而是**已落地的东西没兑现自己的承诺**：

| 问题 | 症状 | 处置 |
|---|---|---|
| `nth:N;` 对 text/index 静默失效 | `click("nth:2;text:登录")` 点的是第 **1** 个，还返回 `ok:True` | 报 permanent。扩展 op 只回第一个匹配，兑现不了就别装作兑现了 |
| 歧义判定不看可见性 | 移动端+桌面端各一份导航（一个 display:none）→ 直接 permanent，逼调用方数 nth | 先按可见性过滤，再判歧义。ego-lite 本来就是这么做的 |
| `refs()` 3×N 次串行往返 | 50 个元素 = 153 次来回，且 `resolveNode` 产出的 RemoteObject 从不释放 | describeNode 并发 + 文本一条 evaluate；不再产生 objectId，也就无需 releaseObject |
| 视口外元素点空报 ok | box/rect 都是视口坐标，元素在视口外照着点等于点空 | `click()`/`click_ref()` 取坐标前 scrollIntoViewIfNeeded |

另外把异常分支补上了 `kind`（桥抖 = transient），别让唯一没有决策信号的分支是最需要它的那个。

**没做的两条，都卡在同一条线上——要改扩展：**

- `click(loc, tab=...)`：扩展只有 op 白名单，没有"带 target 的任意 JS 求值"能力
- 自定义下载目录：browser-level 的 `setDownloadBehavior` 被 tab attach 拒绝

不值得为这两个功能让所有用户重装扩展。`click()` 的活动标签语义已写进 docstring 和 SKILL.md；下载目录的限制也已注明（`Page.setDownloadBehavior` 或许可行，但**没在真机上验过，不写没验证过的代码**）。

### 关于"pyright 严格模式零 error"

原批次 6 的这句声明不成立：仓库根目录跑 `pyright` 1.1.411（用的就是提交进来的 strict 配置）是 **2211 error**，装好依赖也一样，不是环境问题。

根因是**口径不同**：编辑器里的 basedpyright 把 `reportUnknown*` 这类算 warning，pyright CLI 在 strict 下算 error。真要归零，得给整条 CDP 链写 TypedDict——那正是这个薄封装项目刻意不做的事。

处置：改挂 `standard`（现在是真·零 error，src + tests 共 39 文件），并加 CI job 把口径钉死成 pyright CLI。**挂一个 CI 守得住的标准，比挂一个谁也过不了的标准有用。**

## 7. 真机验证与两条挂起项的了结（2026-08-06）

§6 的整改此前只有单测。这一轮全部上真机（Chrome + 扩展 + 受控测试页）跑过：

| 验证项 | 真机结果 |
|---|---|
| 可见性歧义（一显一隐的 `.login-btn`） | 点中可见那个；两个都可见时仍报 permanent（没有过度放宽） |
| 视口外元素 | `click("css:#far")` 在 scrollY=0 点中 2501px 外的按钮 |
| **旧路径静默点空** | 对照实测：`click_selector("#far2")` 返回 `ok:True` 但 onclick **从未触发** |
| `refs()` 提速 | 同页同元素数：355ms → 116ms（**3.1×**），旧路径每次泄漏 50 个 objectId |
| `click_ref()` 滚动 | 从空 hits → 命中，2500px 外的元素真点中 |

**关于 3.1× 而不是 30×**：§6 说的「153 → 5 次往返」是**命令数**。墙钟只有 3×——
`asyncio.gather` 的 50 个 describeNode 在 `chrome.debugger` 那头基本仍是串行处理的。
还试过 `DOM.getDocument(depth=-1)` 一次拿全树、本地映射 nodeId→backendNodeId（零
per-element 调用），实测 **164ms 反而更慢**：大 payload 的序列化代价盖过了 50 次并发
小调用。当前实现是量过之后的最优解，不是想当然。

### 下载目录：验证为不可行，不是「待验证」

真机实测两条都被拒：

```
Browser.setDownloadBehavior → -32601 'Browser.setDownloadBehavior' wasn't found
Page.setDownloadBehavior    → -32000 Cannot not access browser-level commands
```

`Page.` 那条虽已废弃，Chrome 仍把它路由到 browser 层处理器，在 `chrome.debugger` 的
tab attach 下照样拒。而扩展只能 attach 到 tab、拿不到 browser target——**这条路彻底堵死**，
不是「换个写法再试试」。真要自定义落盘位置，只剩「`Fetch` 拦截下载请求 + daemon 侧自己
写盘」这一条，那是另一个功能，不在本文范围。

### `click(loc, tab=...)`：扩展 3 行搞定，但差点又造出一个静默错误

原始 CDP 通道本来就转发任意 method、`msg.tabId` 也一直在消息里，只是 dispatch 分支
不读它。改动就是读一下。

**第一版写成「tabId 不在 managedTabIds 就退回活动指针」——错的。** 真机上恰好撞见：
扩展重载后 `managedTabIds` 被清空，`click(tab=A)` 悄悄打到了 B 上。调用方明说要打 A，
结果打了 B 还回 ok，正是这个 PR 全程在消灭的那类错误。改成指名却没 attach 直接报
`tab N not attached (switch_tab/new_tab first)`。

顺带修了扩展的 `getRect` / `getRectByText` / `getRectByIndex`：三个 op 都不滚视口，
`click_selector` / `click_text` / `click_index` 因此会静默点空——真机对照已复现。

**教训**：单测把「用例定义在 `__main__` runner 之后」这种事盖得严严实实（定义时 runner
早跑完，永不执行）。是 mutation 检验把它揪出来的——把修复改回旧行为，用例本该转红却
全绿。全绿不等于测得到。
