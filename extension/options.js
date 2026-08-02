// options.js — 端口设置。MV3 的 CSP 不允许内联脚本，必须独立文件。
const DEFAULT_PORT = 28417;
const input = document.getElementById('port');
const status = document.getElementById('status');

function show(msg, isError) {
    status.textContent = msg;
    status.className = isError ? 'err' : '';
    if (!isError) setTimeout(() => { status.textContent = ''; }, 2000);
}

chrome.storage.local.get('nekoroPort').then(({ nekoroPort }) => {
    input.value = Number.isInteger(nekoroPort) ? nekoroPort : DEFAULT_PORT;
});

document.getElementById('save').addEventListener('click', async () => {
    const n = parseInt(input.value, 10);
    if (!Number.isInteger(n) || n < 1 || n > 65535) {
        show('Port must be 1–65535', true);
        return;
    }
    // 存成 number，background 里 storage.onChanged 会立即断开重连到新端口
    await chrome.storage.local.set({ nekoroPort: n });
    show('Saved — reconnecting');
});
