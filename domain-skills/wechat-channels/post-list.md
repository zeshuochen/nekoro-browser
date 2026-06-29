# 视频号创作者平台 — 作品列表

## URL

- 作品列表: `https://channels.weixin.qq.com/platform/post/list?tab=post`
- 粉丝统计: `https://channels.weixin.qq.com/platform/statistic/follower`

## 页面结构

外层是正常页面，数据在 `iframe[name=content]` 里。
读取方式：
```js
(() => {
  var f = document.querySelector('iframe[name=content]');
  if (!f || !f.contentDocument) return '';
  return f.contentDocument.body.innerText || '';
})()
```

## 作品列表数据格式

innerText 按日期块分隔：`2026年06月27日`。
每视频块含纯数字行，顺序：播放量、爱心、评论、转发、点赞。
仅自己可见的视频含 `仅自己可见`，需跳过。

## 粉丝统计页

点击"近30天"按钮加载数据：
```js
(() => {
  var f = document.querySelector('iframe');
  var d = f?.contentDocument;
  var el = [...d.querySelectorAll('*')].find(e => e.innerText?.trim() === '近30天');
  el?.click();
})()
```

涨粉表格正则：`r'(\d{4}/\d{2}/\d{2})\t(\d+)\t\d+\t\d+\t\d+'`
粉丝总数正则：`r'关注者总数\s*\n(\d+)'`

## 已知坑

- iframe 是跨域的，需等 contentDocument ready
- 用 wait_for_iframe_content() 轮询，不要 fixed sleep
