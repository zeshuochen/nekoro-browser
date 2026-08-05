# nekoro-browser × ego-lite 借鉴实现文档

> 状态：进行中（批次 0 已完成，其余分批推进）
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
| backendNodeId ref 跨轮次稳定定位 | state() 序号，DOM 一变即失效 | 批次 3 |
| box-model 守卫（非零尺寸才可点） | `if not rect.get("x")` 脆弱判空 | 批次 2 |
| 下载管理 waitForEvent("download") | 无 | 批次 4 |

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

### 批次 4：backendNodeId ref 跨轮次稳定定位（⏳ 后续，需真实浏览器）

- 目标：让 state() 输出的元素句柄在 DOM 小变化后仍可定位（ego `@N` ref 思路）
- 方案（待细化）：state() 输出附带 backendNodeId；click 时 DOM.resolveNode +
  getBoxModel 定位；map 空/失效时自动降级（重 state 或按文本语义回退）
- 前置：确认 chrome.debugger 下 DOM domain 可用（需真实浏览器 MVP 验证，
  遵循「新功能先验证核心链路」）
- 验收：真实浏览器用例——state() → 页面局部刷新 → 点击仍命中
- 风险：中高（跨扩展+daemon 改动，需真实浏览器回归）

### 批次 5：下载管理（⏳ 后续，需真实浏览器 MVP 验证）

- 目标：`wait_for_download()` helper（ego `page.waitForEvent("download")` 思路）
- 方案（待细化）：扩展 attach 后 `Browser.enable` + `Browser.setDownloadBehavior`，
  下载事件经现有 onEvent 转发缓存；daemon 侧 helper 轮询
- 前置：MVP 验证 Browser 域命令在 chrome.debugger tab attach 下可用
- 验收：真实浏览器触发下载，helper 返回落盘路径
- 风险：中（跨扩展改动，需真实浏览器）

### 批次 6：类型注解卫生（风格级，可选）

- 目标：消灭 helpers.py 的 pyright 严格模式噪音
- 方案 A：补齐 `dict[str, ...]` / `list[dict[str, ...]]` 泛型注解（量大）
- 方案 B：项目 pyproject 声明 pyright 配置（basic 模式），对齐 CI 现状
- 验收：LSP 对 helpers.py 无 error；全量测试通过
- 风险：低（纯注解/配置）

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
