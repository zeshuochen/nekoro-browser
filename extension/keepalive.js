// nekoro-browser keepalive — content-script 心跳，独立于 SW 的保活/唤醒向量。
//
// 跑在页面进程，不受 service worker 生死影响：长连 Port + 20s ping 把 SW 焐热；
// SW 被回收时 Port onDisconnect → backoff 重连，重连触发 SW 的 onConnect 事件 =
// 叫醒 dead/wedged SW（这是 chrome.alarms 之外的独立唤醒向量，比 alarm 更硬）。
//
// 极简可审计：只 runtime.connect + postMessage，零 DOM 访问、零页面数据。
// 权衡：任一标签开着时 20s ping 让 SW 基本常热（有意压制 MV3 回收，换 daemon 可达）——
// 轻微电池/资源代价，是产品要求 daemon 常可达的取舍。
(function () {
  function beat() {
    // 扩展被重载（如 --reload-ext → chrome.runtime.reload）后，本页内容脚本上下文失效，
    // chrome.runtime.id 变 undefined，永远连不上。此时 bail，别 1/秒死循环空转；
    // 页面下次导航会重新注入新脚本自愈。
    if (!chrome.runtime || !chrome.runtime.id) return;
    let port;
    try {
      port = chrome.runtime.connect({ name: 'keepalive' });
    } catch (_) {
      setTimeout(beat, 1000 + Math.random() * 500);   // SW 尚未就绪，稍后重试
      return;
    }
    const timer = setInterval(function () {
      try { port.postMessage('ping'); } catch (_) {}   // 每次收发都重置 SW 空闲计时器
    }, 20000);
    port.onDisconnect.addListener(function () {
      // SW 被回收 → Port 断。重连即触发 SW onConnect → 唤醒它（dead/wedged 的实际叫醒机制）。
      // 少了这段，Port 断一次就死到下次 navigate，达不成"立刻叫醒"。抖动避免多标签同时重连。
      clearInterval(timer);
      setTimeout(beat, 500 + Math.random() * 500);
    });
  }
  beat();
})();
