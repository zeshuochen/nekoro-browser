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
        case 'click': {
            const el = document.querySelector(sel);
            if (el) { el.click(); return 'clicked'; }
            return 'not-found';
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
            // Walk DOM under sel, return interactive elements with selectors
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
            if (!el) return 'no';
            const r = el.getBoundingClientRect();
            const style = getComputedStyle(el);
            return JSON.stringify({
                x: Math.round(r.x), y: Math.round(r.y),
                w: Math.round(r.width), h: Math.round(r.height),
                visible: r.width > 0 && r.height > 0 && style.display !== 'none' && style.visibility !== 'hidden',
                tag: el.tagName.toLowerCase(),
                text: (el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 40)
            });
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
            // Pre-defined ops — no eval, bypasses all CSP
            const {op, sel, arg} = msg.params || {};
            const results = await chrome.scripting.executeScript({
                target: {tabId: target},
                func: runOp,
                args: [op || 'title', sel || null, arg ?? null]
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
    }

    // Create a new tab in our own group
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
