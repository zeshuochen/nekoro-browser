# 抖音创作者中心 — 数据统计

## URL

- 内容管理: `https://creator.douyin.com/creator-micro/content/manage`
- 粉丝统计: `https://creator.douyin.com/creator-micro/statistic/home/personal`

## 页面结构

SPA (React)，需要 wait_for_load() 后再 poll body.innerText（SPA 加载后内容才出现）。
登录检测: `if 'login' in js("location.href").lower()`

## 内容管理页数据格式

innerText 按日期块分隔：`2026年06月27日` 后跟视频数据。
每个视频块格式（换行分隔）：
```
播放
1136
点赞
24
评论
2
分享
0
```

正则：`r'播放\n([\d.]+万?)'`，`r'点赞\n([\d.]+万?)'` 等。

## 粉丝统计页涨粉正则

尝试顺序（页面布局因账号等级有差异）：
1. `r'昨[日天](?:新增|涨粉)[^\d]*([-\d]+)'`
2. `r'新增关注\s*([-\d]+)'`
3. 表格行：`r'(\d{4}[-/]\d{2}[-/]\d{2})\t([-\d]+)'`

## React 导航

`<Link>` 组件用 `click_at_xy(x, y)`（CDP compositor 级）或 `js_click(selector)`（JS dispatch）均可。
CDP `Input.dispatchMouseEvent type=click` 不可用（需加 fire-and-forget 修复后才能用）。

## 已知坑

- 粉丝统计页正则值为 0 不代表爬取失败——当天可能真的无涨粉
- 数据延迟约 6 天，爬取时 target_date = today - 6
