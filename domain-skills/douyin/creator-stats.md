# 抖音创作者中心 — 数据统计

## URL

- 内容管理: `https://creator.douyin.com/creator-micro/content/manage`
- 粉丝列表: `https://creator.douyin.com/creator-micro/data/following/follower`
- ~~粉丝统计~~: `https://creator.douyin.com/creator-micro/statistic/home/personal`（⚠️ 2026-06 已失效，显示"未找到相关页面"，被重定向到数据总览）

## 粉丝增长数据

**方案：差值法**（2026-06 启用）。粉丝统计页 API 已失效，改用内容管理页的总粉丝数做日环比：

```
涨粉 = 今天粉丝总数 − 上次记录的粉丝总数
```

**State 文件**（路径由调用方决定，例如 `.douyin_fans_state.json`）：
```json
{"fans": 1234, "date": "2026-06-23"}
```
每次 KPI 爬取后自动更新。首次运行时 `prev_fans=0`，涨粉=0（需跑两次才有差值）。

**内容管理页粉丝数正则**：`r'粉丝\s*([\d.]+万?)'` 或 `r'粉丝数\s*([\d.]+万?)'`

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

## React 导航

`<Link>` 组件用 `click_at_xy(x, y)`（CDP compositor 级）或 `js_click(selector)`（JS dispatch）均可。
CDP `Input.dispatchMouseEvent type=click` 不可用（需加 fire-and-forget 修复后才能用）。

## 已知坑

- 数据延迟约 6 天，爬取时 target_date = today - 6
- 首次运行差值法涨粉=0，需跑两次才有有效差值
- 粉丝统计页 URL `statistic/home/personal` 已失效（2026-06 起显示"未找到相关页面"）
