// nekoro-browser background.js — HTTP polling bridge
// GET /poll for commands, POST /result for responses

const PORT = 9230;
const POLL_MS = 1000;

let tabId = null;
let running = false;

console.log('[nekoro-browser] loaded');

self.addEventListener('activate', () => { start(); });
setTimeout(start, 200);

async function start() {
    if (running) return;
    running = true;

    await autoAttach();

    while (running) {
        try {
            const resp = await fetch(`http://127.0.0.1:${PORT}/poll`);
            if (resp.ok) {
                const text = await resp.text();
                if (text) {
                    const msg = JSON.parse(text);
                    if (msg.type === 'attach') {
                        await doAttachAsync(msg.tabId);
                    } else if (msg.method) {
                        const result = await executeCdp(msg);
                        await postResult({ id: msg.id, result: result });
                    }
                }
            }
        } catch (_) {
            await sleep(2000);
        }
        await sleep(POLL_MS);
    }
}

async function autoAttach() {
    const tabs = await chrome.tabs.query({});
    const t = tabs.find(x => x.url && !x.url.startsWith('chrome://')) || tabs[0];
    if (t) {
        await doAttachAsync(t.id);
    } else {
        // No usable tab — create one
        console.log('[nekoro-browser] creating new tab');
        const nt = await chrome.tabs.create({ url: 'about:blank', active: false });
        await doAttachAsync(nt.id);
    }
}

function doAttachAsync(id) {
    return new Promise(resolve => {
        chrome.debugger.attach({ tabId: id }, '1.3', () => {
            if (chrome.runtime.lastError) { resolve(); return; }
            tabId = id;
            console.log('[nekoro-browser] attached tab', id);
            postResult({ type: 'attached', tabId: id }).then(resolve);
        });
    });
}

function executeCdp(msg) {
    return new Promise(resolve => {
        chrome.debugger.sendCommand(
            { tabId: tabId }, msg.method, msg.params || {},
            (result) => {
                if (chrome.runtime.lastError) {
                    resolve({ error: { message: chrome.runtime.lastError.message, code: -32000 } });
                } else {
                    resolve(result);
                }
            }
        );
    });
}

async function postResult(data) {
    try {
        await fetch(`http://127.0.0.1:${PORT}/result`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
    } catch (_) {}
}

chrome.debugger.onEvent.addListener((src, method, params) => {
    postResult({ type: 'event', method, params, sessionId: src.sessionId });
});

chrome.debugger.onDetach.addListener((src) => {
    if (tabId === src.tabId) tabId = null;
    postResult({ type: 'detached', tabId: src.tabId, reason: 'external' });
});

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
