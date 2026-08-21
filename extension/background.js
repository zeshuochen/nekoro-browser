// nekoro-browser background.js — persistent WebSocket transport
const DEFAULT_PORT = 28417;  // 避开与同类工具 @jackwener/opencli（19825）撞车

// 端口可改：扩展详情页 → 「扩展程序选项」里设，存 chrome.storage.local.nekoroPort。
// Python 侧对应 --port / NEKORO_PORT，两边必须一致。
// 缓存住是因为 connect() 在 onclose 回调里同步调用，来不及等异步 storage 读取。
let port = DEFAULT_PORT;

function wsUrl() { return `ws://127.0.0.1:${port}/ws`; }

async function loadPort() {
    try {
        const v = (await chrome.storage.local.get('nekoroPort')).nekoroPort;
        const n = parseInt(v, 10);
        if (Number.isInteger(n) && n >= 1 && n <= 65535) port = n;
    } catch (_) { /* storage 不可用就用默认端口 */ }
    return port;
}

// 选项页改了端口 → 断开重连到新端口，不用重载扩展
chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== 'local' || !changes.nekoroPort) return;
    const n = parseInt(changes.nekoroPort.newValue, 10);
    port = (Number.isInteger(n) && n >= 1 && n <= 65535) ? n : DEFAULT_PORT;
    console.log('[nekoro-browser] port changed →', port);
    reconnectDelay = 500;
    if (ws) { try { ws.close(); } catch (_) {} }   // onclose 会安排重连
    else connect();
});

let tabId = null;
let ws = null;
let connecting = false;
let portLoaded = false;
let reconnectDelay = 500;

console.log('[nekoro-browser] v3 (websocket) loaded');

// ─── WebSocket transport ────────────────────────────────────────────────
// Persistent connection: commands arrive instantly (no poll gap), results
// stream back over the same socket. The daemon sends a WS ping every ~20s;
// the resulting socket traffic keeps the MV3 service worker pinned, so the
// alarm keep-alive below is only a reconnect fallback for a killed worker.

function wsOpen() { return ws && ws.readyState === WebSocket.OPEN; }

function connect() {
    if (connecting || wsOpen()) return;
    connecting = true;
    if (!portLoaded) {                    // 首次连接：先取配置端口，再拨号
        portLoaded = true;
        loadPort().then(() => { connecting = false; connect(); })
                  .catch(() => { connecting = false; connect(); });
        return;
    }
    try {
        ws = new WebSocket(wsUrl());
    } catch (e) {
        connecting = false;
        scheduleReconnect();
        return;
    }
    ws.onopen = () => {
        connecting = false;
        reconnectDelay = 500;
        console.log('[nekoro-browser] WS connected');
        autoAttach().catch(e => console.error('[nekoro-browser] autoAttach error:', e));
    };
    ws.onmessage = (ev) => {
        let msg;
        try { msg = JSON.parse(ev.data); } catch (_) { return; }
        handleCmd(msg);
    };
    ws.onclose = () => {
        connecting = false;
        // Detach so a freshly restarted daemon re-attaches cleanly.
        if (tabId !== null) {
            try { chrome.debugger.detach({tabId}); } catch(_) {}
            tabId = null;
        }
        scheduleReconnect();
    };
    ws.onerror = () => { try { ws.close(); } catch(_) {} };
}

function scheduleReconnect() {
    setTimeout(connect, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 2, 5000);  // backoff cap 5s
}

// ─── Lifecycle ──────────────────────────────────────────────────────────

self.addEventListener('activate', () => { console.log('[nekoro-browser] activate'); connect(); });

// 浏览器冷启动（含脚本/cron 拉起 Chrome）时立刻连 daemon，不必等 alarm（最长 30s，
// 空闲还会被节流）。onStartup 是持久事件注册，能让 SW 在 Chrome 启动时被唤醒并跑到这。
chrome.runtime.onStartup.addListener(() => { console.log('[nekoro-browser] onStartup'); connect(); });

// content-script 心跳 Port（keepalive.js）：连接进来即说明 SW 被唤醒（含从 dead/wedged
// 被内容脚本重连叫醒）→ 顺手补 WS。回 pong 让 Port 上有收发，续 SW 空闲计时器。
chrome.runtime.onConnect.addListener((port) => {
    if (port.name !== 'keepalive') return;
    if (!wsOpen()) connect();
    port.onMessage.addListener(() => { try { port.postMessage('pong'); } catch (_) {} });
});

// Alarm keep-alive: revive the worker + reconnect if it was killed while the
// socket was down (active WS otherwise keeps the worker alive on its own).
try { chrome.alarms.create('k', {periodInMinutes: 0.5}); } catch(_) {}
function onAlarmFired() { if (!wsOpen()) connect(); }
try {
    chrome.alarms.onAlarm.removeListener(onAlarmFired);
    chrome.alarms.onAlarm.addListener(onAlarmFired);
} catch(_) {}

setTimeout(connect, 200);

// ─── Pre-defined operations (no eval, no CSP issues) ──────────────────

async function runOp(op, sel, arg) {
    // ── Element Finding Engine (inline — must be inside runOp for executeScript serialization) ──

    /** Only text from direct child text nodes, not descendant elements.
     *  Avoids <div><span>喜欢</span></div> matching the div for "喜欢". */
    function getDirectText(el) {
        let text = '';
        for (const child of el.childNodes) {
            if (child.nodeType === Node.TEXT_NODE) {
                text += child.textContent;
            }
        }
        return text.replace(/\s+/g, ' ').trim();
    }

    /** Checks if element is actually visible. */
    function isVisible(el) {
        // 视口 0×0 = 标签没在渲染（窗口最小化 / 后台标签）。这时**所有块级元素**的
        // rect 宽度都是 0，于是「不可见」和「页面上没有」在结果上无法区分：
        // find_text() 返回空数组、state() 只剩零星几条行内元素——全都带着 ok:true。
        // 无人值守的 agent 会据此认定「页面上没这段文字」然后走错分支。
        //
        // 防护放在 isVisible 里而不是逐个 op 上：受影响的 op 是开放集合（findText、
        // state、dump、clickIndex、waitSelector…），枚举必漏；而它们的共同依赖只有
        // 这一处。不看可见性的 op（title/url/text/html/getMarkdown）不受影响，
        // 窗口最小化时照样能用——这是对的。
        //
        // capture_screenshot 对完全相同的条件早就有 not_rendered 防护并给出可行建议，
        // 它的兄弟函数没有，反而伪造成「成功但没找到」。这里补齐口径。
        if (!window.innerWidth || !window.innerHeight) {
            throw new Error('not_rendered: viewport is 0x0 — 标签未在渲染' +
                '（后台标签或窗口最小化），元素可见性无法判定，结果会是空的而不是错的。' +
                '先 switch_tab / Page.bringToFront 让它可见。');
        }
        if (!el || el.nodeType !== 1) return false;
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) return false;
        // checkVisibility 沿祖先链判定：能捕获父级 opacity:0（opacity 不继承，手写只查
        // 元素自身的 getComputedStyle 会漏）、父级 display:none/visibility:hidden 等。
        // 老 Chrome 无此 API 时回退到自身样式检查（display/visibility 覆盖大多数情形）。
        if (typeof el.checkVisibility === 'function') {
            return el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true});
        }
        const s = getComputedStyle(el);
        if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return false;
        return true;
    }

    /** Walk DOM with TreeWalker, find elements by direct-text match.
     *  Sorted: best matchType desc, then smallest area asc. */
    function findTextElements(text, opts) {
        const exact = !!(opts && opts.exact);
        const limit = (opts && opts.limit) || 20;
        const scopeSel = (opts && opts.scope) || null;
        const searchText = (text || '').replace(/\s+/g, ' ').trim();
        if (!searchText) return [];
        const searchLower = searchText.toLowerCase();
        const results = [];

        const root = scopeSel ? document.querySelector(scopeSel) : document.body;
        if (!root) return [];

        const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
        let el;
        while ((el = walker.nextNode())) {
            if (!isVisible(el)) continue;

            const direct = getDirectText(el);
            if (!direct) continue;
            const directLower = direct.toLowerCase();
            const rect = el.getBoundingClientRect();

            let matchType = 0;
            if (directLower === searchLower) {
                matchType = el.children.length === 0 ? 100 : 80;
            } else if (!exact && directLower.startsWith(searchLower)) {
                matchType = el.children.length === 0 ? 60 : 40;
            } else if (!exact && directLower.includes(searchLower)) {
                matchType = el.children.length === 0 ? 30 : 15;
            }

            if (matchType > 0) {
                results.push({
                    el: el, matchType: matchType,
                    area: rect.width * rect.height,
                    text: direct, tag: el.tagName.toLowerCase(),
                    w: rect.width, h: rect.height
                });
            }
        }

        results.sort(function(a, b) { return b.matchType - a.matchType || a.area - b.area; });
        return results.slice(0, limit);
    }

    function findFirstText(text) {
        var results = findTextElements(text, {limit: 1});
        return results.length > 0 ? results[0].el : null;
    }

    /** Get the Nth visible interactive element (same ordering as state() op). */
    function _getElementByIndex(targetIdx) {
        const MAX = 200;
        const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
        let el, idx = 0;
        while ((el = walker.nextNode()) && idx < MAX) {
            if (!isVisible(el)) continue;
            const tag = el.tagName;
            const interactive = ['A','BUTTON','INPUT','SELECT','TEXTAREA','VIDEO'].includes(tag);
            const hasText = getDirectText(el) && el.children.length === 0;
            if (!interactive && !hasText) continue;
            if (idx === targetIdx) return el;
            idx++;
        }
        return null;
    }

    // ── Ops ──────────────────────────────────────────────────────────────
    switch(op) {
        case 'title': return document.title;
        case 'url': return location.href;
        case 'q': return document.querySelector(sel)?.textContent || null;
        case 'qa': {
            const els = document.querySelectorAll(sel);
            return Array.from(els).map(e => e.textContent?.trim()).slice(0, 50);
        }
        case 'links': {
            const as = document.querySelectorAll('a');
            return Array.from(as).map(a => a.href).filter(h => h).slice(0, 50);
        }
        case 'findLink': {
            const as = document.querySelectorAll('a');
            for (let i = 0; i < as.length; i++) {
                if (as[i].href && as[i].href.includes(arg)) return as[i].href;
            }
            return null;
        }
        case 'scroll': {
            window.scrollBy(0, arg || 500);
            return 'scrolled';
        }
        case 'getRect': {
            const el = document.querySelector(sel);
            if (!el) return null;
            // rect 是视口坐标：元素在视口外时照着点就是点空，而且会一路返回 ok。
            // 滚进来再取——已经看得见的不动页面（IfNeeded 语义）。
            if (el.scrollIntoViewIfNeeded) el.scrollIntoViewIfNeeded(false);
            const r = el.getBoundingClientRect();
            return {x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2)};
        }
        case 'fillInput': {
            // 框架感知填值：用原生 value setter 写值，绕过 React/Vue 对 value 的劫持，
            // 让受控组件的 onChange 收到（直接 el.value= 会被框架的 setter 吞掉不触发）。
            const el = document.querySelector(sel);
            if (!el) return {ok: false, error: 'element not found: ' + sel};
            const text = (arg == null) ? '' : String(arg);
            el.focus();
            if (el.isContentEditable) {
                el.textContent = text;
                el.dispatchEvent(new Event('input', {bubbles: true}));
                return {ok: true, value: el.textContent};
            }
            // 只对 input/textarea 用原生 setter；其他元素（div/select/自定义组件）
            // 返回 not-fillable，让 helper 回退到 CDP 点击+插字符（真实键入）。
            const isText = (el instanceof HTMLInputElement) || (el instanceof HTMLTextAreaElement);
            if (!isText) return {ok: false, error: 'not a fillable input: ' + el.tagName};
            const proto = (el instanceof HTMLTextAreaElement)
                ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
            const desc = Object.getOwnPropertyDescriptor(proto, 'value');
            if (desc && desc.set) desc.set.call(el, text); else el.value = text;
            el.dispatchEvent(new Event('input', {bubbles: true}));
            el.dispatchEvent(new Event('change', {bubbles: true}));
            return {ok: true, value: el.value};
        }
        case 'getRectByText': {
            // 用 findFirstText（= find_text 用的那个引擎：isVisible 过滤 + getDirectText +
            // 按匹配质量和面积排序），不要自己再写一遍朴素查找。原来那版有两个真 bug：
            //   ① 无可见性检查 → display:none 的元素排在前面就被选中，rect 是 0×0，
            //      于是点在 (0,0)、什么也没点到，却一路返回 ok:true（静默成功）
            //   ② 只认 childNodes.length===1 的纯文本叶节点 → `保存 <b>*</b>` 这种
            //      文本与元素混排的按钮整个被跳过，报 text not found
            // 结果是 find_text 找得到、click_text 点不到——两个本该配套的 helper 互相矛盾。
            const tx = typeof arg === 'string' ? arg : (arg && arg.text);
            const el = findFirstText(tx);
            if (el) {
                if (el.scrollIntoViewIfNeeded) el.scrollIntoViewIfNeeded(false);
                const r = el.getBoundingClientRect();
                return {x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2)};
            }
            return null;
        }
        case 'getRectByIndex': {
            const idx = typeof arg === 'number' ? arg : parseInt(arg);
            if (isNaN(idx) || idx < 0) return null;
            const el = _getElementByIndex(idx);
            if (!el) return null;
            if (el.scrollIntoViewIfNeeded) el.scrollIntoViewIfNeeded(false);
            const r = el.getBoundingClientRect();
            const x = Math.round(r.left + r.width / 2);
            const y = Math.round(r.top + r.height / 2);
            // 这个中心点上**实际**是谁？坐标点击打的是屏幕位置，不是元素：被浮层 /
            // sticky 头 / cookie 横幅盖住时，事件落在遮挡物上，而调用方拿到的仍是
            // ok:true —— 什么都没发生却报成功。把命中信息一起回去，让 Python 侧决定
            // 是照常坐标点击（真 isTrusted），还是退回页内点击。
            const at = document.elementFromPoint(x, y);
            const hit = !!at && (at === el || el.contains(at));
            return {x, y, hit};
        }
        case 'click': {
            const el = document.querySelector(sel);
            if (!el) return 'not-found';
            const r = el.getBoundingClientRect();
            const cx = r.left + r.width / 2;
            const cy = r.top + r.height / 2;
            // Find the actual leaf element at click position (handles nested SVG/spans)
            const target = document.elementFromPoint(cx, cy) || el;
            const opts = {
                bubbles: true, cancelable: true, view: window,
                clientX: cx, clientY: cy, screenX: cx, screenY: cy + 80,
                button: 0, buttons: 1, pointerId: 1, pointerType: 'mouse',
                isPrimary: true, pressure: 0.5, detail: 1
            };
            target.dispatchEvent(new PointerEvent('pointerdown', opts));
            target.dispatchEvent(new MouseEvent('mousedown', opts));
            target.dispatchEvent(new PointerEvent('pointerup', opts));
            target.dispatchEvent(new MouseEvent('mouseup', opts));
            target.dispatchEvent(new MouseEvent('click', opts));
            return 'clicked';
        }
        case 'clickAt': {
            const pt = typeof arg === 'string' ? JSON.parse(arg) : (arg || {});
            const ex = pt.x, ey = pt.y;
            if (ex == null || ey == null) return 'no-coords';
            let el = document.elementFromPoint(ex, ey);
            if (!el) return 'no-element';
            // Climb to action bar child
            let tag = el.tagName, climbed = 0;
            while (el && el !== document.body && climbed < 8) {
                tag = el.tagName;
                const parent = el.parentElement;
                if (parent && parent.matches && parent.matches('[class*=action]')) {
                    break; // Found action bar child
                }
                if (tag === 'A' || tag === 'BUTTON' || tag === 'LABEL') break;
                el = parent;
                climbed++;
            }
            if (!el || el === document.body) el = document.elementFromPoint(ex, ey);
            if (!el) return 'no-element';
            // Dispatch full mouse event sequence
            const opts = {bubbles: true, cancelable: true, clientX: ex, clientY: ey, button: 0, view: window};
            el.dispatchEvent(new PointerEvent('pointerdown', opts));
            el.dispatchEvent(new MouseEvent('mousedown', opts));
            el.dispatchEvent(new PointerEvent('pointerup', opts));
            el.dispatchEvent(new MouseEvent('mouseup', opts));
            el.dispatchEvent(new MouseEvent('click', opts));
            return 'clicked:' + ex + ',' + ey + ' <' + tag + '>';
        }
        case 'clickIndex': {
            // Click the Nth interactive element (same ordering as state() op)
            const _tgt = arg != null ? parseInt(arg) : -1;
            if (_tgt < 0) return 'invalid-index';
            const _el = _getElementByIndex(_tgt);
            if (!_el) return 'no-element';
            const _r = _el.getBoundingClientRect();
            const _cx = _r.left + _r.width / 2;
            const _cy = _r.top + _r.height / 2;
            const _opts = {bubbles: true, cancelable: true, view: window,
                clientX: _cx, clientY: _cy, screenX: _cx, screenY: _cy + 80,
                button: 0, buttons: 1, pointerId: 1, pointerType: 'mouse',
                isPrimary: true, pressure: 0.5, detail: 1};
            // 中心点上实际是谁 —— **点击前**问，点完页面一跳 DOM 就变了，问了也白问。
            // 纯诊断：页内点击照样点得到（不受遮挡影响），但调用方发现页面没反应时，
            // 这一位能省掉一轮排查。
            const _at = document.elementFromPoint(_cx, _cy);
            const _covered = !(_at && (_at === _el || _el.contains(_at)));
            _el.dispatchEvent(new PointerEvent('pointerdown', _opts));
            _el.dispatchEvent(new MouseEvent('mousedown', _opts));
            _el.dispatchEvent(new PointerEvent('pointerup', _opts));
            _el.dispatchEvent(new MouseEvent('mouseup', _opts));
            _el.dispatchEvent(new MouseEvent('click', _opts));
            return (_covered ? 'clicked-covered:' : 'clicked:') + _tgt;
        }
        case 'inputIndex': {
            // Type text into the Nth input element (same ordering as state() op)
            const _icfg = typeof arg === 'string' ? JSON.parse(arg) : (arg || {});
            const _itgt = _icfg.index != null ? parseInt(_icfg.index) : (typeof arg === 'number' ? arg : -1);
            const _itext = _icfg.text || '';
            if (_itgt < 0) return 'invalid-index';
            const _iel = _getElementByIndex(_itgt);
            if (!_iel) return 'no-element';
            const _isetter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set
                || Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set;
            if (_isetter) _isetter.call(_iel, _itext);
            else _iel.value = _itext;
            _iel.dispatchEvent(new Event('input', {bubbles: true}));
            _iel.dispatchEvent(new Event('change', {bubbles: true}));
            return 'typed:' + _itgt;
        }
        case 'clickText': {
            // Find by visible text using TreeWalker + direct-text matching
            const el = findFirstText(arg);
            if (el) { el.click(); return 'clicked:' + (el.textContent||'').trim().slice(0,30); }
            return 'not-found';
        }
        case 'findText': {
            // arg: text. sel: optional scope. Returns top matches with metadata
            const _cfg = typeof arg === 'string' ? {text:arg} : (arg || {});
            const _results = findTextElements(_cfg.text || _cfg.t, {
                limit: _cfg.limit || 15,
                scope: _cfg.sel || _cfg.scope || null,
                exact: _cfg.exact || false
            });
            return _results.map(function(r) {
                return {text:r.text.slice(0,40), tag:r.tag, match:r.matchType>=100?'exact':r.matchType>=60?'starts':'contains', w:Math.round(r.w), h:Math.round(r.h)};
            });
        }
        case 'waitForText': {
            // arg = JSON: {text, timeout:15000, interval:500}
            const _wcfg = typeof arg === 'string' ? JSON.parse(arg) : (arg || {});
            const _timeout = _wcfg.timeout || 15000;
            const _interval = _wcfg.interval || 500;
            const _text = _wcfg.text || _wcfg.t;
            if (!_text) return 'no-text';
            const _deadline = Date.now() + _timeout;
            while (Date.now() < _deadline) {
                const _el = findFirstText(_text);
                if (_el) return 'found:' + _text;
                await new Promise(function(r) { setTimeout(r, _interval); });
            }
            return 'timeout:' + _text;
        }
        case 'typeText': {
            // arg = JSON: {sel, text, pressEnter}
            const cfg = typeof arg === 'string' ? JSON.parse(arg) : (arg || {});
            const input = document.querySelector(cfg.sel || 'input[type="text"], input:not([type])');
            if (!input) return 'no-input';
            // Set value natively
            const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
            setter.call(input, cfg.text || '');
            input.dispatchEvent(new Event('input', {bubbles:true}));
            input.dispatchEvent(new Event('change', {bubbles:true}));
            if (cfg.pressEnter) {
                input.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter',code:'Enter',keyCode:13,bubbles:true}));
                input.dispatchEvent(new KeyboardEvent('keyup', {key:'Enter',code:'Enter',keyCode:13,bubbles:true}));
                // Also try submitting the form
                const form = input.closest('form');
                if (form) form.dispatchEvent(new Event('submit', {bubbles:true}));
            }
            return 'typed:' + (cfg.text || '');
        }
        case 'dump': {
            // Legacy dump — use 'state' for new code. Kept for backward compat.
            const root = sel ? document.querySelector(sel) : document.body;
            if (!root) return 'not-found';
            const result = [];
            const MAX_DEPTH = arg || 4;
            const MAX_ITEMS = 120;
            function walk(el, depth, prefix) {
                if (!el || el.nodeType !== 1 || result.length >= MAX_ITEMS || depth > MAX_DEPTH) return;
                const tag = el.tagName.toLowerCase();
                const id = el.id ? '#' + el.id : '';
                const cls = el.className && typeof el.className === 'string'
                    ? '.' + el.className.trim().split(/\s+/).slice(0,3).join('.') : '';
                const label = tag + id + cls;
                const text = (el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 60);
                const interactive = ['A','BUTTON','INPUT','SELECT','TEXTAREA','VIDEO','IMG','SVG'].includes(el.tagName);
                const hasText = text && el.children.length === 0;
                if (interactive || hasText) {
                    let line = prefix + label;
                    if (el.tagName === 'A') line += ' -> ' + (el.href || '').slice(0, 70);
                    else if (el.tagName === 'INPUT') line += '[type=' + (el.type||'text') + ']';
                    else if (el.tagName === 'IMG') line += '[src=' + (el.src||'').slice(0, 40) + ']';
                    else line += ' "' + text + '"';
                    result.push(line);
                }
                for (const child of el.children) {
                    walk(child, depth + 1, prefix + '  ');
                }
            }
            walk(root, 0, '');
            return result.join('\n');
        }
        case 'state': {
            // Indexed element tree — like browser-act's `state`.
            // Returns [{index, changed, tag, text, role, placeholder, href, box:{x,y,w,h}}]
            // `changed: true` (or first call `*` ) for elements new/modified since last state().
            const MAX = (typeof arg === 'number' ? arg : 80);
            const scopeSel = sel || null;
            const root = scopeSel ? document.querySelector(scopeSel) : document.body;
            if (!root) return {error: 'not-found'};

            const prev = window.__nekoro_state_map;
            const curr = {};
            const results = [];

            const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
            let el, idx = 0;
            while ((el = walker.nextNode()) && idx < MAX) {
                if (!isVisible(el)) continue;
                const tag = el.tagName;
                const interactive = ['A','BUTTON','INPUT','SELECT','TEXTAREA','VIDEO'].includes(tag);
                const hasText = getDirectText(el) && el.children.length === 0;
                if (!interactive && !hasText) continue;

                const rect = el.getBoundingClientRect();
                const sig = tag + '|' + getDirectText(el).slice(0,30) + '|' +
                    Math.round(rect.x/20) + ',' + Math.round(rect.y/20);
                const changed = !prev || !prev[sig];
                curr[sig] = idx;

                results.push({
                    index: idx,
                    changed: changed,
                    tag: tag.toLowerCase(),
                    text: getDirectText(el).slice(0, 50),
                    role: el.getAttribute('role') || '',
                    placeholder: el.getAttribute('placeholder') || '',
                    href: tag === 'A' ? (el.getAttribute('href')||'').slice(0, 60) : '',
                    type: tag === 'INPUT' ? (el.type||'text') : '',
                    box: {x: Math.round(rect.x), y: Math.round(rect.y),
                          w: Math.round(rect.width), h: Math.round(rect.height)}
                });
                idx++;
            }

            window.__nekoro_state_map = curr;
            return results;
        }
        case 'waitSelector': {
            // Poll until element matches desired state (visible|hidden|attached|detached)
            const cfg = typeof arg === 'string' ? JSON.parse(arg) : (arg || {});
            const s = cfg.sel || cfg.selector || sel;
            const want = cfg.state || 'visible';
            const _timeout = cfg.timeout || 10000;
            const _interval = cfg.interval || 300;
            if (!s) return 'no-selector';
            const _deadline = Date.now() + _timeout;
            while (Date.now() < _deadline) {
                const el = document.querySelector(s);
                if (want === 'attached' && el) return 'attached';
                if (want === 'detached' && !el) return 'detached';
                if (el) {
                    const vis = isVisible(el);
                    if (want === 'visible' && vis) return 'visible';
                    if (want === 'hidden' && !vis) return 'hidden';
                }
                await new Promise(function(r) { setTimeout(r, _interval); });
            }
            return 'timeout:' + s;
        }
        case 'getMarkdown': {
            // Extract page content as clean markdown (browser-act style)
            function toMd(node, depth) {
                if (!node) return '';
                // tagName 对 HTML 元素本来就是大写，下面每一条规则也都按大写写的。
                // 这里曾经是 toLowerCase() —— 于是从标题到链接到列表，整个格式化层
                // 一条都命不中，全部掉进最后那段纯文本递归：get_markdown() 产出的是
                // 没有任何换行、所有文字粘在一起的纯文本，比 page_text() 还差。
                // 归一到大写（SVG/XML 里的 tagName 可能是小写或混合，所以不能省这一步）。
                const tag = (node.tagName || '').toUpperCase();
                const txt = (node.textContent || '').trim();
                // Skip hidden/style/script
                if (['STYLE','SCRIPT','NOSCRIPT','SVG','PATH'].includes(tag)) return '';

                // ── 叶子：不递归，textContent 就是全部内容 ──
                if (tag === 'BR') return '\n';
                if (tag === 'HR') return '---\n';
                if (tag === 'IMG') {
                    const alt = node.getAttribute('alt') || '';
                    const src = node.getAttribute('src') || '';
                    return src ? '![' + alt + '](' + src + ')' : '';
                }
                if (tag === 'INPUT') return '**[Input' + (node.type ? ' ' + node.type : '') + ': ' + (node.getAttribute('placeholder')||txt) + ']**\n';
                if (tag === 'TEXTAREA') return '**[Textarea: ' + (node.getAttribute('placeholder')||'') + ']**\n';
                if (tag === 'SELECT') return '**[Select: ' + txt.slice(0,30) + ']**\n';
                if (tag === 'CODE' || tag === 'PRE') return '`' + txt + '`';
                // 链接自己就是叶子：标签文字 + href，内部再有结构也没有意义
                if (tag === 'A') {
                    const href = node.getAttribute('href') || '';
                    const label = txt || href;
                    return href && !href.startsWith('javascript:') ? '[' + label + '](' + href + ')' : label;
                }

                // ── 容器：**先递归渲染子节点，再包装** ──
                // 这几条以前用的是 node.textContent，于是 <p><a href=…>Learn more</a></p>
                // 里的链接被压成一句纯文字 —— 而「链接嵌在段落/列表项里」正是网页最常见的
                // 形状，等于 markdown 里基本看不到链接。先递归就能保住内层的所有格式。
                let inner = '';
                for (const child of node.childNodes) {
                    if (child.nodeType === Node.TEXT_NODE) {
                        const t = (child.textContent || '').replace(/\s+/g, ' ');
                        if (t.trim()) inner += t;
                    } else if (child.nodeType === Node.ELEMENT_NODE) {
                        inner += toMd(child, depth + 1);
                    }
                }
                const body = inner.trim();
                if (/^H[1-6]$/.test(tag)) return body ? '#'.repeat(parseInt(tag[1])) + ' ' + body + '\n\n' : '';
                if (tag === 'LI') return body ? '- ' + body + '\n' : '';
                if (tag === 'P') return body ? body + '\n\n' : '';
                if (tag === 'BLOCKQUOTE') return body ? '> ' + body + '\n\n' : '';
                if (tag === 'BUTTON') return '**[Button: ' + body + ']**\n';
                // Block elements get newlines
                if (['DIV','SECTION','ARTICLE','MAIN','HEADER','FOOTER','NAV','ASIDE','UL','OL','TABLE','FORM','FIELDSET'].includes(tag)) {
                    return '\n' + inner + '\n';
                }
                return inner;
            }
            const root = sel ? document.querySelector(sel) : document.body;
            if (!root) return '';
            const md = toMd(root, 0);
            // Clean up: collapse 3+ newlines to 2
            return md.replace(/\n{3,}/g, '\n\n').slice(0, arg || 8000);
        }
        case 'hover': {
            const el = document.querySelector(sel);
            if (!el) return 'not-found';
            const r = el.getBoundingClientRect();
            const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
            el.dispatchEvent(new MouseEvent('mouseenter', {bubbles:true,cancelable:true,clientX:cx,clientY:cy}));
            el.dispatchEvent(new MouseEvent('mouseover', {bubbles:true,cancelable:true,clientX:cx,clientY:cy}));
            el.dispatchEvent(new PointerEvent('pointerenter', {bubbles:true,cancelable:true,clientX:cx,clientY:cy}));
            return 'hovered';
        }
        case 'hoverIndex': {
            const _htgt = arg != null ? parseInt(arg) : -1;
            const _hel = _getElementByIndex(_htgt);
            if (!_hel) return 'no-element';
            const _hr = _hel.getBoundingClientRect();
            const _hcx = _hr.left + _hr.width / 2, _hcy = _hr.top + _hr.height / 2;
            _hel.dispatchEvent(new MouseEvent('mouseenter', {bubbles:true,cancelable:true,clientX:_hcx,clientY:_hcy}));
            _hel.dispatchEvent(new MouseEvent('mouseover', {bubbles:true,cancelable:true,clientX:_hcx,clientY:_hcy}));
            return 'hovered:' + _htgt;
        }
        case 'scrollIntoView': {
            const el = sel ? document.querySelector(sel) : document.elementFromPoint(window.innerWidth/2, window.innerHeight/2);
            if (!el) return 'not-found';
            el.scrollIntoView({behavior: 'smooth', block: 'center'});
            return 'scrolled';
        }
        case 'scrollIntoViewIndex': {
            const _sid = arg != null ? parseInt(arg) : -1;
            const _sel = _getElementByIndex(_sid);
            if (!_sel) return 'no-element';
            _sel.scrollIntoView({behavior: 'smooth', block: 'center'});
            return 'scrolled:' + _sid;
        }
        case 'dialogOff': {
            // Override native dialogs to auto-dismiss them
            window.alert = function() {};
            window.confirm = function() { return true; };
            window.prompt = function() { return ''; };
            return 'dialogs-off';
        }
        case 'waitNetworkIdle': {
            const _ncfg = typeof arg === 'string' ? JSON.parse(arg) : (arg || {});
            const _idle = _ncfg.idle || 500;
            const _nmax = _ncfg.timeout || 15000;
            const _ndeadline = Date.now() + _nmax;
            // Patch XHR + fetch to count in-flight requests (idempotent via __nekoro_patched flag)
            if (!window.__nekoro_patched) {
                window.__nekoro_pending = 0;
                const _origOpen = XMLHttpRequest.prototype.open;
                XMLHttpRequest.prototype.open = function(...a) {
                    window.__nekoro_pending++;
                    this.addEventListener('loadend', function() {
                        window.__nekoro_pending = Math.max(0, window.__nekoro_pending - 1);
                    });
                    return _origOpen.apply(this, a);
                };
                const _origFetch = window.fetch;
                window.fetch = function(...a) {
                    window.__nekoro_pending++;
                    return _origFetch.apply(this, a).finally(function() {
                        window.__nekoro_pending = Math.max(0, window.__nekoro_pending - 1);
                    });
                };
                window.__nekoro_patched = true;
            }
            let _lastActive = Date.now();
            while (Date.now() < _ndeadline) {
                if (window.__nekoro_pending > 0) _lastActive = Date.now();
                if (window.__nekoro_pending === 0 && Date.now() - _lastActive >= _idle) return 'idle';
                await new Promise(function(r) { setTimeout(r, 100); });
            }
            return 'timeout:' + window.__nekoro_pending;
        }
        case 'html': return document.documentElement.outerHTML.slice(0, arg || 500);
        case 'text': return document.body?.innerText?.slice(0, arg || 500) || '';
        case 'ready': return document.readyState;
        case 'has': {
            const el = document.querySelector(sel);
            if (!el) return 'no';
            const tag = el.tagName.toLowerCase();
            const cls = el.className && typeof el.className === 'string' ? el.className.slice(0, 50) : '';
            const txt = (el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 50);
            return `YES: <${tag} class="${cls}"> "${txt}"`;
        }
        case 'box': {
            const el = document.querySelector(sel);
            if (!el) return {found: false};
            const r = el.getBoundingClientRect();
            const style = getComputedStyle(el);
            return {
                x: Math.round(r.x), y: Math.round(r.y),
                w: Math.round(r.width), h: Math.round(r.height),
                visible: r.width > 0 && r.height > 0 && style.display !== 'none' && style.visibility !== 'hidden',
                tag: el.tagName.toLowerCase(),
                text: (el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 40)
            };
        }
        default: return 'unknown op: ' + op;
    }
}

// ─── Handle Commands ──────────────────────────────────────────────────

async function handleCmd(msg) {
    if (msg.type === 'attach') {
        await tryAttach(msg.tabId);
    } else if (msg.type === 'auto_attach') {
        await autoAttach();
    } else if (msg.type === 'list_tabs') {
        // grouped=false 时 listManagedTabs 退化成「所有非 chrome:// 标签」——里面混着
        // 用户自己的标签。sweep_tabs 靠这个标志决定拒不拒绝真关。
        post({id: msg.id, result: {tabs: await listManagedTabs(),
                                   grouped: managedGroupId != null}});
    } else if (msg.type === 'last_dialog') {
        // 返回最近被自动处置的原生对话框（读后清，下次只报新的）
        post({id: msg.id, result: lastDialog});
        lastDialog = null;
    } else if (msg.type === 'switch_tab') {
        const id = msg.tabId;
        let ok;
        if (managedTabIds.has(id)) {
            // 我们已 attach 的标签 → 直接切指针，别重复 attach。用 managedTabIds
            // 而非全局 getOccupiedTabs：后者含 DevTools 等他方 attach 的标签，
            // 那种切过去 sendCommand 会失败，tryAttach 才会诚实报 attached:false。
            tabId = id;
            rememberTab(id);
            post({type:'attached', tabId});
            ok = true;
        } else {
            ok = await tryAttach(id);
            if (ok) managedTabIds.add(id);
        }
        post({id: msg.id, result: {attached: ok, tabId: ok ? tabId : id}});
    } else if (msg.type === 'scripting') {
        // tab/DOM 操作（navigate/find_tab/evaluate…）。evaluate 经 CDP Runtime.evaluate
        // 执行 runOp（见 handleScripting），不再用 chrome.scripting.executeScript。
        await handleScripting(msg);
    } else if (msg.method) {
        // 每条命令可自带 tabId 指定目标（helpers 的 tab= 参数）。不带就打当前指针，
        // 与原行为一致。指名了却没 attach 就报错——**绝不退回指针**：调用方说了要
        // 打 A，悄悄打到 B 上再回 ok，是这个项目最不能接受的一类错误。
        if (msg.tabId && !managedTabIds.has(msg.tabId)) {
            post({id: msg.id, error: {code: -32000,
                message: `tab ${msg.tabId} not attached (switch_tab/new_tab first)`}});
            return;
        }
        const target = msg.tabId || tabId;
        // Fire-and-forget: Input events Chrome doesn't respond to — skip waiting for callback.
        const FIRE_AND_FORGET = [
            "Input.dispatchMouseEvent",
            "Input.dispatchKeyEvent",
            "Input.insertText",
            "Input.dispatchTouchEvent",
        ];
        if (FIRE_AND_FORGET.includes(msg.method)) {
            chrome.debugger.sendCommand({tabId: target}, msg.method, msg.params || {}, (result) => {
                if (chrome.runtime.lastError) {
                    console.warn("[nekoro] CDP fire-and-forget error:", msg.method,
                        chrome.runtime.lastError.message);
                }
            });
            post({id: msg.id, result: {}});
            return;
        }
        chrome.debugger.sendCommand(
            {tabId: target}, msg.method, msg.params || {},
            (result) => {
                if (chrome.runtime.lastError) {
                    post({id:msg.id, error:{message:chrome.runtime.lastError.message, code:-32000}});
                } else {
                    post({id:msg.id, result});
                }
            }
        );
    }
}

// 等 tab 加载完成。用 chrome.tabs.onUpdated 的 status==='complete' 事件驱动，
// 不轮询 readyState——那样会读到上一页残留的 complete（stale-complete 竞态，
// 正是被硬编码 sleep(3000) 掩盖的问题）。timeout 兜底，短于调用方传输超时。
// 返回 'complete' | 'timeout' | 'no-tab'。
// checkNow：create 路径专用——监听器在 tabs.create（导航已开始）之后才挂，可能
// 错过秒开页（about:blank/data:/缓存）的 complete，故补查一次当前状态。update 路径
// 绝不能开：那时导航还没触发，补查会读到上一页残留的 complete（stale-complete）。
function waitTabLoad(tabId, checkNow = false, timeout = 10000) {
    if (!tabId) return Promise.resolve('no-tab');
    return new Promise((resolve) => {
        let done = false;
        const finish = (v) => {
            if (done) return;
            done = true;
            chrome.tabs.onUpdated.removeListener(onUpd);
            clearTimeout(timer);
            resolve(v);
        };
        const onUpd = (id, info) => {
            if (id === tabId && info.status === 'complete') finish('complete');
        };
        chrome.tabs.onUpdated.addListener(onUpd);
        const timer = setTimeout(() => finish('timeout'), timeout);
        if (checkNow) {
            chrome.tabs.get(tabId, (t) => {
                if (!chrome.runtime.lastError && t && t.status === 'complete') finish('complete');
            });
        }
    });
}

// runOp 经 CDP Runtime.evaluate 在页面 MAIN world 执行，返回其结果值。
// returnByValue：直接拿 JSON 可序列化的返回值；awaitPromise：runOp 是 async。
// 页内抛异常 → reject(带描述)，由 handleScripting 的 catch 统一 post error。
function cdpEval(tabId, expression) {
    return new Promise((resolve, reject) => {
        chrome.debugger.sendCommand(
            {tabId}, 'Runtime.evaluate',
            {expression, returnByValue: true, awaitPromise: true},
            (res) => {
                if (chrome.runtime.lastError) {
                    reject(new Error(chrome.runtime.lastError.message));
                } else if (res && res.exceptionDetails) {
                    const ex = res.exceptionDetails;
                    const desc = (ex.exception && (ex.exception.description || ex.exception.value))
                        || ex.text || 'eval error';
                    reject(new Error(String(desc)));
                } else {
                    resolve(res && res.result ? res.result.value : undefined);
                }
            }
        );
    });
}

// runOp 源码只需序列化一次（~20KB），避免每次 evaluate 重复 toString。
const RUN_OP_SRC = runOp.toString();

async function handleScripting(msg) {
    const {action, target, expression, url} = msg.params || {};
    try {
        if (action === 'navigate') {
            let tabId = target, created = false;
            if (!tabId) {
                const tab = await chrome.tabs.create({url, active:true});
                tabId = tab?.id;
                created = true;
                // Ensure tab is in our group (create group if needed)
                if (tabId && chrome.tabs.group) {
                    try {
                        if (managedGroupId == null) {
                            managedGroupId = await chrome.tabs.group({tabIds: [tabId]});
                            if (chrome.tabGroups) {
                                await chrome.tabGroups.update(managedGroupId, {
                                    title: 'nekoro', color: 'blue', collapsed: true
                                });
                            }
                        } else {
                            await chrome.tabs.group({groupId: managedGroupId, tabIds: [tabId]});
                        }
                    } catch(_) {}
                }
            }
            // 监听器先挂再触发导航（update 路径），避免快页面在挂监听前就 complete。
            // 等真实加载完成而非硬编码 3s：快页面即时返回，慢页面等够，10s 兜底。
            const loadP = waitTabLoad(tabId, created);
            if (!created) await chrome.tabs.update(tabId, {url, active:true});
            const load = await loadP;
            post({id:msg.id, result:{navigated:url, tabId, load}});
        } else if (action === 'evaluate') {
            // runOp 跑在页面 MAIN world（要看到 React 水合后的 DOM）。原先走
            // chrome.scripting.executeScript(world:'MAIN')，但在 debugger-attached tab
            // 上它会静默挂死（Chrome 149：scripting 的 MAIN 世界注入在调试会话下不返回）。
            // CDP Runtime.evaluate 同样跑在页面 MAIN world，且在 attached tab 上稳定，
            // 故把 runOp 序列化后经 CDP 执行，绕开 scripting 传输层的挂死。
            const {op, sel, arg} = msg.params || {};
            const expr = '(' + RUN_OP_SRC + ')('
                + JSON.stringify(op || 'title') + ','
                + JSON.stringify(sel ?? null) + ','
                + JSON.stringify(arg ?? null) + ')';
            const value = await cdpEval(target || tabId, expr);
            post({id:msg.id, result:{value}});
        } else if (action === 'list_tabs') {
            const tabs = await chrome.tabs.query({});
            post({id:msg.id, result:{tabs: tabs.map(t => ({id:t.id, url:t.url, title:t.title, active:t.active}))}});
        } else if (action === 'find_tab') {
            const tabs = await chrome.tabs.query({});
            const found = url
                ? tabs.find(t => t.url && t.url.toLowerCase().includes(url.toLowerCase()))
                : tabs.find(t => t.url && !t.url.startsWith('chrome://') && !t.url.startsWith('about:'));
            if (found) {
                await chrome.tabs.update(found.id, {active: true});
                await chrome.windows.update(found.windowId, {focused: true});
            }
            post({id:msg.id, result:{tabId: found?.id, url: found?.url, windowId: found?.windowId}});
        } else if (action === 'close_tab') {
            await chrome.tabs.remove(target);
            post({id:msg.id, result:{closed: target}});
        } else if (action === 'reload_extension') {
            // chrome.runtime.reload() restarts the service worker from disk.
            // Note: may occasionally fail to restart — if so, toggle manually in chrome://extensions.
            post({id:msg.id, result:{reloading: true}});
            await sleep(200);
            chrome.runtime.reload();
        }
    } catch(e) {
        console.error('[nekoro-browser] scripting error:', e);
        post({id:msg.id, error:{message:e.message, code:-32000}});
    }
}

// ─── Attach ─────────────────────────────────────────────────────────────

let managedGroupId = null;
let managedTabIds = new Set();
// 最近一次被 CDP 层自动处置的原生对话框（读后清）。原生 alert/confirm/prompt/beforeunload
// 会冻结页面 JS 线程 → 任何 Runtime.evaluate 挂死；attach 后 Page.enable + 拦截处置来防挂。
let lastDialog = null;

// 记住当前 attach 的标签，供 SW 死后重启时接回（chrome.storage.session：survives SW
// 重启，浏览器关时自动清 = 冷启无残留）。fire-and-forget，失败无碍。
function rememberTab(id) {
    try { chrome.storage.session.set({ lastTab: id }); } catch (_) {}
}

async function autoAttach() {
    // SW-4：优先重挂 SW 死前驱动的标签，别漂到组内遗留 about:blank（伤自愈重启/长任务连续性）。
    // 先 tabs.get 确认标签还在（避免对已关标签白跑 tryAttach 的重试），并顺带恢复 managedGroupId。
    try {
        const { lastTab } = await chrome.storage.session.get('lastTab');
        if (lastTab) {
            const t = await chrome.tabs.get(lastTab).catch(() => null);
            if (t && await tryAttach(lastTab)) {
                managedTabIds.add(lastTab);
                if (managedGroupId == null && t.groupId > 0) managedGroupId = t.groupId;
                return;
            }
        }
    } catch (_) {}

    // Find existing nekoro group first
    if (managedGroupId == null && chrome.tabGroups) {
        try {
            const allTabs = await chrome.tabs.query({});
            for (const t of allTabs) {
                if (t.groupId > 0) {
                    try {
                        const g = await chrome.tabGroups.get(t.groupId);
                        if (g && g.title === 'nekoro') {
                            managedGroupId = t.groupId;
                            break;
                        }
                    } catch(_) {}
                }
            }
        } catch(_) {}
    }

    // Reuse existing group if we have one
    if (managedGroupId != null) {
        try {
            const tabs = await chrome.tabs.query({groupId: managedGroupId});
            for (const t of tabs) {
                if (await tryAttach(t.id)) {
                    managedTabIds.add(t.id);
                    return;
                }
            }
        } catch(_) {}
        // All existing tabs busy — add a new tab to this group
        try {
            const tab = await chrome.tabs.create({url:'about:blank', active:false});
            await chrome.tabs.group({groupId: managedGroupId, tabIds: [tab.id]});
            if (await tryAttach(tab.id)) {
                managedTabIds.add(tab.id);
                return;
            }
        } catch(_) {}
    }

    // No existing group — create first tab + group
    try {
        const tab = await chrome.tabs.create({url:'about:blank', active:false});
        if (chrome.tabs.group) {
            managedGroupId = await chrome.tabs.group({tabIds: [tab.id]});
        }
        if (managedGroupId != null && chrome.tabGroups) {
            try {
                await chrome.tabGroups.update(managedGroupId, {
                    title: 'nekoro',
                    color: 'blue',
                    collapsed: true
                });
            } catch(_) {}
        }
        if (await tryAttach(tab.id)) {
            managedTabIds.add(tab.id);
            return;
        }
    } catch(e) {
        console.error('[nekoro-browser] group create failed:', e);
    }

    // Fallback: try any untouched tab
    const occupied = await getOccupiedTabs();
    const tabs = await chrome.tabs.query({});
    for (const t of tabs) {
        if (t.url && t.url.startsWith('chrome://')) continue;
        if (occupied.has(t.id)) continue;
        if (await tryAttach(t.id)) {
            managedTabIds.add(t.id);
            return;
        }
    }

    console.error('[nekoro-browser] autoAttach: all attempts failed');
}

// List nekoro-managed tabs (or all non-chrome tabs if no group yet) with
// active/attached flags — feeds list_tabs.
async function listManagedTabs() {
    const occupied = await getOccupiedTabs();
    let tabs = [];
    try {
        tabs = managedGroupId != null
            ? await chrome.tabs.query({groupId: managedGroupId})
            : await chrome.tabs.query({});
    } catch(_) {}
    return tabs
        .filter(t => !(t.url || '').startsWith('chrome://'))
        .map(t => ({
            tabId: t.id, url: t.url || '', title: t.title || '',
            active: t.id === tabId, attached: occupied.has(t.id),
        }));
}

// Helper: get set of tab IDs that already have debugger attached
async function getOccupiedTabs() {
    const occupied = new Set();
    try {
        const targets = await new Promise(resolve =>
            chrome.debugger.getTargets(resolve));
        for (const t of (targets || [])) {
            if (t.attached) occupied.add(t.tabId);
        }
    } catch(_) {}
    return occupied;
}

function tryAttach(id, retries = 5, delay = 500) {
    return new Promise(resolve => {
        function attempt(remaining) {
            chrome.debugger.attach({tabId: id}, '1.3', () => {
                if (chrome.runtime.lastError) {
                    if (remaining > 0) {
                        setTimeout(() => attempt(remaining - 1), delay);
                    } else {
                        const msg = chrome.runtime.lastError.message;
                        console.error('[nekoro-browser] attach failed after retries tab', id, ':', msg);
                        post({type:'attach_error', tabId: id, detail: msg});
                        resolve(false);
                    }
                    return;
                }
                tabId = id;
                rememberTab(id);
                console.log('[nekoro-browser] attached tab', id);
                // Page.enable：让原生对话框走 CDP Page.javascriptDialogOpening 事件（由
                // onEvent 拦截处置），而非弹原生 UI 冻结页面。失败无碍（chrome:// 等受限页）。
                chrome.debugger.sendCommand({tabId: id}, 'Page.enable', {},
                    () => { void chrome.runtime.lastError; });
                post({type:'attached', tabId});
                resolve(true);
            });
        }
        attempt(retries);
    });
}

// ─── Events ─────────────────────────────────────────────────────────────

chrome.debugger.onEvent.addListener((src,method,params) => {
    if (method === 'Page.javascriptDialogOpening') {
        // 原生对话框冻结页面 JS 线程 → evaluate 系 helper 挂死。立即在 CDP 层处置：
        // beforeunload 放行（accept:true，别卡住导航），alert/confirm/prompt 取消（accept:false）。
        const info = {kind: params.type, message: params.message || '',
                      url: params.url || '', defaultPrompt: params.defaultPrompt || ''};
        lastDialog = info;
        const accept = params.type === 'beforeunload';
        chrome.debugger.sendCommand({tabId: src.tabId}, 'Page.handleJavaScriptDialog',
            {accept}, () => { void chrome.runtime.lastError; });
        post({type:'dialog', ...info, handled: accept ? 'accept' : 'dismiss', tabId: src.tabId});
        return;                       // 已处置，不再当普通 event 转发
    }
    post({type:'event', method, params, sessionId:src.sessionId, tabId:src.tabId});
});
chrome.debugger.onDetach.addListener((src, reason) => {
    const wasActive = (tabId === src.tabId);
    if (wasActive) tabId = null;
    managedTabIds.delete(src.tabId);
    post({type:'detached', tabId:src.tabId, reason});
    // canceled_by_user（用户点调试横幅取消）：清掉被取消标签的 lastTab 记忆，否则后续
    // autoAttach 会优先重挂它、又弹调试横幅。只清「被取消的正是记住的那个」，别误伤别的。
    if (reason === 'canceled_by_user') {
        try {
            chrome.storage.session.get('lastTab', ({ lastTab }) => {
                if (lastTab === src.tabId) chrome.storage.session.remove('lastTab');
            });
        } catch (_) {}
    }
    // 用户关掉当前活动标签 → 自动重连到另一个可用标签，避免命令失效。
    // canceled_by_user 不重连，否则会反复弹横幅。
    if (wasActive && wsOpen() && reason !== 'canceled_by_user') {
        autoAttach().catch(e => console.error('[nekoro-browser] reattach failed:', e));
    }
});

// ─── Helpers ────────────────────────────────────────────────────────────

function post(data) {
    if (!wsOpen()) return;
    try { ws.send(JSON.stringify(data)); } catch(_) {}
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }


