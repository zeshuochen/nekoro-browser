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

    // Auto-attach first
    await autoAttach();

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

async function handleCmd(msg) {
    if (msg.type === 'attach') {
        await tryAttach(msg.tabId);
    } else if (msg.type === 'auto_attach') {
        await autoAttach();
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

// ─── Attach ─────────────────────────────────────────────────────────────

async function autoAttach() {
    const tabs = await chrome.tabs.query({});
    for (const t of tabs) {
        if (await tryAttach(t.id)) return;
    }
    const nt = await chrome.tabs.create({url:'about:blank', active:false});
    await tryAttach(nt.id);
}

function tryAttach(id) {
    return new Promise(resolve => {
        chrome.debugger.attach({tabId:id}, '1.3', () => {
            if (chrome.runtime.lastError) {
                console.warn('[nekoro-browser] attach fail tab',id,':',chrome.runtime.lastError.message);
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
