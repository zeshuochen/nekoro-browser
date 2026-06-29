// nekoro-browser background.js — minimal reliable polling
const PORT = 19825;

let tabId = null;
let running = false;

console.log('[nekoro-browser] v2 loaded');

// ─── Lifecycle ──────────────────────────────────────────────────────────

self.addEventListener('activate', () => {
    console.log('[nekoro-browser] activate → starting');
    startPolling();
});

// Keep alive via alarms
try { chrome.alarms.create('k', {periodInMinutes: 0.5}); } catch(_) {}
try { chrome.alarms.onAlarm.addListener(() => { if(!running) startPolling(); }); } catch(_) {}

// Also try immediately
setTimeout(() => { if(!running) startPolling(); }, 500);
setTimeout(() => { if(!running) startPolling(); }, 2000);
setTimeout(() => { if(!running) startPolling(); }, 5000);

// ─── Polling Loop ───────────────────────────────────────────────────────

async function startPolling() {
    if (running) return;
    running = true;
    console.log('[nekoro-browser] polling started');

    // Auto-attach in background — don't block polling
    autoAttach().catch(e => console.error('[nekoro-browser] autoAttach error:', e));

    let consecutiveErrors = 0;
    while (running) {
        try {
            const resp = await fetch(`http://127.0.0.1:${PORT}/poll`);
            consecutiveErrors = 0;
            if (resp.ok) {
                const text = await resp.text();
                if (text) {
                    const msg = JSON.parse(text);
                    await handleCmd(msg);
                }
            }
        } catch (e) {
            consecutiveErrors++;
            if (consecutiveErrors > 10) {
                // Detach after too many consecutive failures
                if (tabId !== null) {
                    try { chrome.debugger.detach({tabId}); } catch(_) {}
                    tabId = null;
                }
            }
            await sleep(consecutiveErrors > 5 ? 5000 : 2000);
            continue;
        }
        await sleep(1000);
    }
}

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
        if (!el || el.nodeType !== 1) return false;
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) return false;
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
            const r = el.getBoundingClientRect();
            return {x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2)};
        }
        case 'getRectByText': {
            const tx = typeof arg === 'string' ? arg : (arg && arg.text);
            const all = document.querySelectorAll('*');
            for (const e of all) {
                if (e.childNodes.length === 1 && e.childNodes[0].nodeType === 3) {
                    if (e.textContent.trim().includes(tx)) {
                        const r = e.getBoundingClientRect();
                        return {x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2)};
                    }
                }
            }
            return null;
        }
        case 'getRectByIndex': {
            const idx = typeof arg === 'number' ? arg : parseInt(arg);
            if (isNaN(idx) || idx < 0) return null;
            const el = _getElementByIndex(idx);
            if (!el) return null;
            const r = el.getBoundingClientRect();
            return {x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2)};
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
            el.dispatchEvent(new MouseEvent('pointerdown', opts));
            el.dispatchEvent(new MouseEvent('mousedown', opts));
            el.dispatchEvent(new MouseEvent('pointerup', opts));
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
            _el.dispatchEvent(new PointerEvent('pointerdown', _opts));
            _el.dispatchEvent(new MouseEvent('mousedown', _opts));
            _el.dispatchEvent(new PointerEvent('pointerup', _opts));
            _el.dispatchEvent(new MouseEvent('mouseup', _opts));
            _el.dispatchEvent(new MouseEvent('click', _opts));
            return 'clicked:' + _tgt;
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
            return 'typed:' + _tgt;
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
                const tag = (node.tagName || '').toLowerCase();
                const txt = (node.textContent || '').trim();
                // Skip hidden/style/script
                if (['STYLE','SCRIPT','NOSCRIPT','SVG','PATH'].includes(node.tagName)) return '';
                // Headings
                if (/^H[1-6]$/.test(tag)) return '#'.repeat(parseInt(tag[1])) + ' ' + txt + '\n\n';
                // Links
                if (tag === 'A') {
                    const href = node.getAttribute('href') || '';
                    const label = txt || href;
                    return href && !href.startsWith('javascript:') ? '[' + label + '](' + href + ')' : label;
                }
                // Lists
                if (tag === 'LI') return '- ' + txt + '\n';
                if (tag === 'P') return txt + '\n\n';
                if (tag === 'BR') return '\n';
                if (tag === 'HR') return '---\n';
                if (tag === 'BLOCKQUOTE') return '> ' + txt + '\n\n';
                if (tag === 'CODE' || tag === 'PRE') return '`' + txt + '`';
                if (tag === 'IMG') {
                    const alt = node.getAttribute('alt') || '';
                    const src = node.getAttribute('src') || '';
                    return src ? '![' + alt + '](' + src + ')' : '';
                }
                if (tag === 'BUTTON') return '**[Button: ' + txt + ']**\n';
                if (tag === 'INPUT') return '**[Input' + (node.type ? ' ' + node.type : '') + ': ' + (node.getAttribute('placeholder')||txt) + ']**\n';
                if (tag === 'TEXTAREA') return '**[Textarea: ' + (node.getAttribute('placeholder')||'') + ']**\n';
                if (tag === 'SELECT') return '**[Select: ' + txt.slice(0,30) + ']**\n';
                // Recurse children
                let out = '';
                for (const child of node.childNodes) {
                    if (child.nodeType === Node.TEXT_NODE) {
                        const t = (child.textContent || '').replace(/\s+/g, ' ');
                        if (t.trim()) out += t;
                    } else if (child.nodeType === Node.ELEMENT_NODE) {
                        out += toMd(child, depth + 1);
                    }
                }
                // Block elements get newlines
                if (['DIV','SECTION','ARTICLE','MAIN','HEADER','FOOTER','NAV','ASIDE','UL','OL','TABLE','FORM','FIELDSET'].includes(tag)) {
                    out = '\n' + out + '\n';
                }
                return out;
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
            // Poll until no in-flight requests for idle_ms
            const _ncfg = typeof arg === 'string' ? JSON.parse(arg) : (arg || {});
            const _idle = _ncfg.idle || 1000;
            const _nmax = _ncfg.timeout || 15000;
            const _ndeadline = Date.now() + _nmax;
            // Use Performance API to check pending resource loads
            while (Date.now() < _ndeadline) {
                const entries = performance.getEntriesByType('resource');
                const pending = entries.filter(function(e) { return e.duration === 0 && e.startTime > Date.now() - 5000; });
                if (pending.length === 0) return 'idle';
                await new Promise(function(r) { setTimeout(r, _idle / 2); });
            }
            return 'timeout';
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
    } else if (msg.type === 'scripting') {
        // Fallback: use chrome.scripting.executeScript (no CDP needed)
        await handleScripting(msg);
    } else if (msg.method) {
        chrome.debugger.sendCommand(
            {tabId}, msg.method, msg.params || {},
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

async function handleScripting(msg) {
    const {action, target, expression, func, args, url} = msg.params || {};
    try {
        if (action === 'navigate') {
            let tabId = target;
            if (!tabId) {
                const tab = await chrome.tabs.create({url, active:true});
                tabId = tab?.id;
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
            } else {
                await chrome.tabs.update(tabId, {url, active:true});
            }
            await sleep(3000);
            post({id:msg.id, result:{navigated:url, tabId}});
        } else if (action === 'evaluate') {
            // Inject into MAIN world to see React-hydrated DOM
            const {op, sel, arg} = msg.params || {};
            const results = await chrome.scripting.executeScript({
                target: {tabId: target},
                func: runOp,
                args: [op || 'title', sel || null, arg ?? null],
                world: 'MAIN'
            });
            post({id:msg.id, result:{value:results[0]?.result}});
        } else if (action === 'list_tabs') {
            const tabs = await chrome.tabs.query({});
            post({id:msg.id, result:{tabs: tabs.map(t => ({id:t.id, url:t.url, title:t.title, active:t.active}))}});
        } else if (action === 'find_tab') {
            const tabs = await chrome.tabs.query({});
            const found = tabs.find(t => 
                t.url && t.url.toLowerCase().includes((url || 'douyin.com').toLowerCase())
            );
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

async function autoAttach() {
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

function tryAttach(id) {
    return new Promise(resolve => {
        chrome.debugger.attach({tabId:id}, '1.3', () => {
            if (chrome.runtime.lastError) {
                const msg = chrome.runtime.lastError.message;
                console.warn('[nekoro-browser] attach fail tab',id,':',msg);
                post({type:'attach_error', tabId:id, detail:msg});
                resolve(false); return;
            }
            tabId = id;
            console.log('[nekoro-browser] attached tab', id);
            post({type:'attached', tabId});
            resolve(true);
        });
    });
}

// ─── Events ─────────────────────────────────────────────────────────────

chrome.debugger.onEvent.addListener((src,method,params) => {
    post({type:'event', method, params, sessionId:src.sessionId});
});
chrome.debugger.onDetach.addListener((src) => {
    if (tabId === src.tabId) tabId = null;
    post({type:'detached', tabId:src.tabId});
});

// ─── Helpers ────────────────────────────────────────────────────────────

async function post(data) {
    try {
        await fetch(`http://127.0.0.1:${PORT}/result`, {
            method:'POST', headers:{'Content-Type':'application/json'},
            body:JSON.stringify(data)
        });
    } catch(_) {}
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }


