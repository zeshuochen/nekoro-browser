// Test-only CDP pipe bridge. No npm packages; never opens the user's profile.
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';

const [executable, profile, mode] = process.argv.slice(2);
const chrome = spawn(executable, [
    '--remote-debugging-pipe', '--enable-unsafe-extension-debugging',
    '--no-first-run', '--no-default-browser-check', '--disable-sync',
    '--disable-background-networking', '--disable-component-update',
    '--window-size=1280,900', `--user-data-dir=${profile}`,
    ...(mode === 'headful' ? [] : ['--headless=new']), 'about:blank',
], {stdio: ['ignore', 'ignore', 'ignore', 'pipe', 'pipe'], windowsHide: true});

function emit(value) { process.stdout.write(JSON.stringify(value) + '\n'); }
emit({event: 'browser-started', pid: chrome.pid});
let buffer = '';
chrome.stdio[4].setEncoding('utf8');
chrome.stdio[4].on('data', chunk => {
    buffer += chunk;
    let end;
    while ((end = buffer.indexOf('\0')) !== -1) {
        const packet = buffer.slice(0, end);
        buffer = buffer.slice(end + 1);
        if (packet) {
            const message = JSON.parse(packet);
            if (message.id) emit(message); // unsolicited events aren't needed here
        }
    }
});
chrome.on('error', error => { emit({event: 'browser-error', error: error.message}); process.exitCode = 1; });
chrome.on('exit', code => { emit({event: 'browser-exit', code}); process.exit(); });
createInterface({input: process.stdin}).on('line', line => {
    chrome.stdio[3].write(line + '\0');
}).on('close', () => chrome.stdio[3].end());
