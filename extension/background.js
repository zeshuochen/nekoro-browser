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
                        await tryAttach(msg.tabId);
                    } else if (msg.type === 'auto_attach') {
                        await autoAttach();
                    } else if (msg.method) {
                        const result = await executeCdp(msg);
                        await postResult({ id: msg.id, result: result });
                    }
                }
            }
        } catch (_) {
            // Daemon disconnected — detach debugger so next start works
            if (tabId !== null) {
                console.log('[nekoro-browser] daemon gone, detaching tab', tabId);
                chrome.debugger.detach({ tabId: tabId });
                tabId = null;
            }
            await sleep(2000);
        }
        await sleep(POLL_MS);
    }
}

async function autoAttach() {
    console.log('[nekoro-browser] autoAttach: querying tabs...');
    const tabs = await chrome.tabs.query({});
    console.log('[nekoro-browser] autoAttach: found', tabs.length, 'tabs');

    for (const t of tabs) {
        console.log('[nekoro-browser] autoAttach: trying tab', t.id, t.url?.slice(0,50));
        if (await tryAttach(t.id)) return;
    }

    // Create new window with fresh tab
    console.log('[nekoro-browser] autoAttach: creating new window');
    try {
        const win = await chrome.windows.create({ url: 'about:blank' });
        if (win.tabs && win.tabs[0]) {
            await sleep(1000); // wait for tab to be ready
            if (await tryAttach(win.tabs[0].id)) return;
        }
    } catch (e) {
        console.error('[nekoro-browser] autoAttach: window create failed:', e);
    }

    console.error('[nekoro-browser] autoAttach: ALL attempts failed');
}

function tryAttach(id) {
    return new Promise(resolve => {
        chrome.debugger.attach({ tabId: id }, '1.3', () => {
            if (chrome.runtime.lastError) {
                console.warn('[nekoro-browser] attach failed on tab', id, ':', chrome.runtime.lastError.message);
                resolve(false);
                return;
            }
            tabId = id;
            console.log('[nekoro-browser] attached tab', id);
            postResult({ type: 'attached', tabId: id });
            resolve(true);
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
