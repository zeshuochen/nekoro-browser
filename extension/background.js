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
        case 'html': return document.documentElement.outerHTML.slice(0, arg || 500);
        case 'text': return document.body?.innerText?.slice(0, arg || 500) || '';
        case 'ready': return document.readyState;
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
                console.log('[nekoro-browser] tabs.create returned:', JSON.stringify({id:tab?.id, url:tab?.url, pendingUrl:tab?.pendingUrl}));
                tabId = tab?.id;
            } else {
                await chrome.tabs.update(tabId, {url, active:true});
            }
            console.log('[nekoro-browser] navigate tabId=', tabId);
            await sleep(3000);
            post({id:msg.id, result:{navigated:url, tabId}});
        } else if (action === 'evaluate') {
            // Pre-defined ops — no eval, bypasses all CSP
            const {op, sel, arg} = msg.params || {};
            const results = await chrome.scripting.executeScript({
                target: {tabId: target},
                func: runOp,
                args: [op, sel, arg]
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

async function autoAttach() {
    // Try existing tabs first
    const tabs = await chrome.tabs.query({});
    for (const t of tabs) {
        if (await tryAttach(t.id)) return;
    }

    // Create NEW TAB in current window — shares cookies
    try {
        const tab = await chrome.tabs.create({url:'about:blank', active:false});
        await sleep(500);
        if (await tryAttach(tab.id)) return;
    } catch(e) {
        console.error('[nekoro-browser] tab create failed:', e);
    }
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
