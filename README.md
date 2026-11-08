# 沁の刷B站视频（MaiBot 插件）

[![version](https://img.shields.io/badge/version-1.5.1-ff69b4)](CHANGELOG.md)
[![license](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![platform](https://img.shields.io/badge/platform-MaiBot-00a1d6)](https://github.com/MaiBot-chan/MaiBot)
[![python](https://img.shields.io/badge/python-3.10%2B-3776ab)](https://www.python.org/)

定时「刷」B 站，按白名单聊天流里大家的兴趣挑**真的有趣**的视频分享出来。

插件会读视频的标签、字幕、弹幕和热门评论，必要时**下载视频抽几帧看画面**，再让 LLM 判断「这视频到底讲了什么、值不值得分享」，最后用**符合人设口吻的自然语言**说出来，像群友随口安利那样。

反过来它也能**读群友贴的视频**：别人贴链接就自动快速读一遍（brief，不花识图钱），有人 @ 它或者说「详细看看」时才深度读（full，带字幕时间轴、弹幕时间线，并抽帧看画面）。

> 当前版本 **v1.5.1** · 全部配置项已中文化 · 活跃时段可多段（能给午休留空档）· 分享可选文字 / 封面图文 / QQ 小程序卡片 · 读视频分 brief / full 两档 · 能把视频里的梗学进黑话库
> 逐版本变更见 [CHANGELOG.md](CHANGELOG.md) · 从旧版（上游 id）换过来请看[安装说明](#1-安装)

## 目录

- [功能一览](#功能一览)
- [分享效果](#分享效果)
- [快速开始](#快速开始)
- [配置说明](#配置说明)
  - [`[plugin]` 插件开关](#plugin-插件开关)
  - [`[schedule]` 刷取频率](#schedule-刷取频率)
  - [`[account]` 账号登录](#account-账号登录)
  - [`[sources]` 视频来源](#sources-视频来源)
  - [`[[categories]]` 内容类别](#categories-内容类别)
  - [`[interest]` 兴趣与语义判断](#interest-兴趣与语义判断)
  - [`[vision]` 抽帧识图](#vision-抽帧识图)
  - [`[danmaku]` 弹幕与热门评论](#danmaku-弹幕与热门评论)
  - [`[filter]` 硬过滤](#filter-硬过滤)
  - [`[scoring]` 打分](#scoring-打分)
  - [`[persona]` 人设语气](#persona-人设语气)
  - [`[share]` 分享格式](#share-分享格式)
  - [`[card]` 卡片直发](#card-卡片直发)
  - [`[shiwei]` 赤石视频](#shiwei-赤石视频)
  - [`[read]` 读视频](#read-读视频)
  - [`[meme]` 学梗](#meme-学梗)
  - [`[advanced]` 运行参数](#advanced-运行参数)
  - [`[[streams]]` 白名单聊天流](#streams-白名单聊天流)
- [工具](#工具)
- [工作原理](#工作原理)
- [数据文件](#数据文件)
- [目录结构](#目录结构)
- [测试与自检](#测试与自检)
- [常见问题](#常见问题)
- [已知限制](#已知限制)
- [版本](#版本)

---

## 功能一览

| 能力 | 说明 |
| --- | --- |
| **可调频率** | `[schedule].interval_minutes` 支持 1 分钟 ~ 24 小时，另有随机抖动与多段活跃时段（可以给午休留空档） |
| **白名单** | 只有 `[[streams]]` 里列出的聊天流会收到分享，每个流可单独配兴趣、类别、数量 |
| **兴趣来源** | ① 配置里手写兴趣 ② 自动从该流近期聊天提取高频词 ③ 没有兴趣时用通用的「人设初始兴趣」兜底 ④ 视频标签/标题/简介/分区/UP 名 |
| **兴趣可见可改** | 自动提取的兴趣会**写回配置**的 `auto_interests`，在设置页能看到、也能直接改；手动改过就锁定，插件不再覆盖 |
| **手动登录** | 在 WebUI 的「账号登录 → B 站 Cookie」里粘贴浏览器 Cookie 即可；登录后能读字幕、排行榜更稳 |
| **多来源抓取** | 关键词搜索（WBI 签名，免登录可用）、热门列表、排行榜，全部可开关 |
| **内容类别** | 新鲜资讯 / 社区创作 / 玩家投稿 / 外网搬运，类别与关键词都可增删改 |
| **深度理解** | 字幕 + 弹幕 + 热门评论 + **抽帧识图** + LLM 语义判断与自然语言介绍 |
| **噪声过滤** | 硬过滤（时长/播放/点赞/屏蔽词/官方内容）+ AI 鱼灾式模板弹幕评论过滤 |
| **去重** | 分享过的视频记入名单，默认 30 天内不再自动分享 |
| **主动搜索** | 麦麦可以自己调 `bili_search` 工具按关键词搜一条并分享，独立冷却（默认 5 分钟），不影响自动频率 |
| **赤石视频** | 麦麦可以调 `bili_shiwei` 工具抓一个抽象整活视频，只发标题+链接，不带介绍 |
| **能读视频** | `bili_read` 工具 + 白名单会话里**贴链接自动读**（brief：简介、分P、字幕节选、热评、弹幕，不调视觉模型）；有人 @ 麦麦或说「详细看看」时自动切 full：字幕时间轴、弹幕时间线、更多热评，并**抽帧看画面**；私聊里贴链接默认就走 full |
| **能学梗** | `bili_learn_meme` 工具：读视频的字幕、**10 秒弹幕爆发点**、热评，让模型整理成一张梗卡（梗名 / 含义 / 用法 / 例句 / 来历 / 时效 / 敏感度），写进麦麦的**黑话库**（`Jargon` 表），之后聊天里命中这个梗它会自然用上；标题像「什么梗 / 梗指南 / 梗科普」的视频被自动读到时也会顺手学一次（默认关闭） |
| **人设语气** | 推荐语与介绍按 MaiBot 的**麦麦人设**口吻生成，可覆盖、可关 |
| **分享形式** | `[share].style` 可选：`text` 一条文字（默认）、`image` 封面图 + 文字、`card` QQ 小程序卡片（直连 QQ 客户端的 OneBot HTTP 发 json 卡片，发不出去自动退回图文 → 文字） |
| **全中文配置** | 每个配置项都有中文名和中文说明，WebUI 表单直接看得懂 |

---

## 分享效果

默认（`[share].style = "text"`）每条分享是**一条消息**：一句人设口吻的随口评价 + 【标题】链接。

```
呜哇这个我也刷到了，几套新配队思路比我自己瞎搭的强多了
【【原神】5.2 深渊满星配队实测】 https://www.bilibili.com/video/BV1xxxxxxxxx
```

想带上数据，把 `[share].include_stats_line` 打开，会在链接后面补一行播放量 / 点赞 / UP 主：

```
来了来了，这个我之前刷到过
【【原神】5.2 深渊满星配队实测】 https://www.bilibili.com/video/BV1xxxxxxxxx
🔥 36.9万 播放 · 2.8万 点赞 · UP：无优i1
```

那句随口话由 LLM 按 MaiBot 的人设口吻生成，**不是模板套话**；模型会被告知不要用「推荐」「分享」「安利」「值得一看」「大家快看」这类词，可以带吐槽、情绪或疑问。没有可用 LLM 时退回视频简介原文（同样截到「随口一句字数上限」），不会发不出去。

把 `[share].style` 改成 `image`，会先发视频封面图、再发上面那条文字，看起来像个正常的图文消息：

```
（封面图）
呜哇这个我也刷到了，几套新配队思路比我自己瞎搭的强多了
【【原神】5.2 深渊满星配队实测】 https://www.bilibili.com/video/BV1xxxxxxxxx
```

改成 `card`，则直接发一张 **QQ 小程序卡片**（封面 + 标题 + 跳转链接），点一下就能进 B 站；卡片需要额外在 `[card]` 里填 QQ 客户端的 OneBot HTTP 地址，具体见 [`[card]` 卡片直发](#card-卡片直发)。

---

## 快速开始

### 1. 安装

把整个目录放到 MaiBot 的 `plugins/` 下，目录名必须和插件 ID 一致：

```
<MaiBot 实例>/plugins/github.BsXinQin.bilibili_video_watch/
```

从插件市场安装会自动放到这个位置。装好后重启 MaiBot，或在 WebUI 插件页启用 `github.BsXinQin.bilibili_video_watch`。

> **从旧版换过来**：旧版的插件 id 是 `github.TommyAAKK.bilibili_interest_share`，这个版本改成了 `github.BsXinQin.bilibili_video_watch`，宿主会当成一个新插件。想保留原来的配置与已刷名单，先把 `data/plugins/github.TommyAAKK.bilibili_interest_share/` 整个目录改名成 `data/plugins/github.BsXinQin.bilibili_video_watch/`，再卸载旧插件、安装这个版本。

### 2. 登录 B 站（推荐，可选）

登录不是必须的，但登录后能读**字幕**、排行榜和搜索都更稳。

在 WebUI 里打开插件配置 → `[account]` → `cookie`，把浏览器里的 B 站 Cookie 粘进去（形如 `SESSDATA=...; bili_jct=...`），保存即生效。

获取方式：浏览器登录 bilibili.com → F12 开发者工具 → Network → 随便点一个请求 → 复制请求头里的 `Cookie` 整行。

> Cookie 属于敏感凭证，只存在插件目录的 `config.toml` 里，别提交到公开仓库。

### 3. 配置白名单

首次启动后 MaiBot 会在插件目录生成 `config.toml`，**至少填一个 `[[streams]]`**，否则不会分享给任何会话：

```toml
[[streams]]
name = "原神群"                      # 备注名，随便写
enabled = true
group_id = "123456789"               # 群号；也可以直接填 stream_id
interests = ["原神", "抽卡", "机械键盘"]  # 手写兴趣（可留空）
auto_interest = true                 # 允许从群聊记录里自动提取兴趣
auto_interests = []                  # 自动提取的兴趣，插件会自己写，也可以手动改
```

找不到群号？插件启动时会打印解析到的白名单会话；也可以直接把 `stream_id` 填上（优先级最高），或者用 `group_id` / `user_id` 让宿主自动反查。

### 4. 确认在跑

- 插件加载后日志里会打出下一轮时间；把 `[advanced].debug` 设为 `true` 能看到每轮抓取、过滤、打分的细节
- 想立刻分享一条：在白名单群里让麦麦「去 B 站搜个 XX 视频分享一下」，它会调 `bili_search` 工具
- 想验证读视频：在群里贴一条 B 站链接，看它能不能接着聊视频内容

> 仓库里的 `config.example.toml` 是**带完整中文注释的配置模板**，每个键上方都有一行说明，值等于内置默认值，可以整份复制成 `config.toml` 再改。

### 运行环境

- **运行时不依赖额外 pip 包**：HTTP 用宿主自带的 `httpx`，配置回写用 `tomlkit`。
- **抽帧识图需要 ffmpeg**（可选）：优先 `[advanced].ffmpeg_path`，其次 `imageio-ffmpeg`，最后查系统 `PATH`；都没有就自动跳过抽帧，其余流程正常。
- `_manifest.json` 把 `imageio-ffmpeg` 声明成 `python_package` 依赖（`>=0.4.9`），缺了宿主会自动装（等于自带一份 ffmpeg）。完全离线部署时也可以手动 `pip install imageio-ffmpeg` 或直接在 `PATH` 放一个 `ffmpeg`，宿主检测到已满足就不会联网。

---

## 配置说明

配置按 section 分组，WebUI 会自动渲染成中文表单；`config.example.toml` 里每个键上方也有同一套中文说明。

> 改完 `config.toml` 保存即可，MaiBot 会自动重载配置，不用重启。

### `[plugin]` 插件开关

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 是否启用插件（`false` 时完全不工作） |
| `config_version` | `"1.5.1"` | 配置版本号，由插件维护，一般不要手动改 |

### `[schedule]` 刷取频率

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `interval_minutes` | `60` | 每隔多少分钟刷一次，**1 ~ 1440**（1 分钟 ~ 24 小时） |
| `jitter_seconds` | `120` | 每轮叠加的随机抖动，避免每天固定时刻 |
| `run_on_start` | `true` | 插件加载后先刷一轮 |
| `active_hours` | `""` | 允许自动分享的时段，**可以用逗号分成多段**给午休留空档，如 `08:00-11:30, 13:00-21:00`；单段跨午夜写成 `22:00-02:00`；留空 = 全天 |
| `max_share_per_round` | `1` | 每个流每轮最多分享几个 |
| `search_cooldown_seconds` | `300` | **主动搜索**的独立冷却，与自动频率无关 |

### `[account]` 账号登录

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `cookie` | `""` | B 站 Cookie，手动粘贴（形如 `SESSDATA=...; bili_jct=...`）；留空则匿名访问 |
| `check_on_start` | `true` | 启动时请求一次 nav 接口检查登录状态，Cookie 失效会在日志里提示 |

登录后能做什么：

- 读**字幕**（未登录时 B 站不返回字幕内容，只能靠标题/简介/弹幕/画面）
- 排行榜、搜索接口更稳，被风控（`-352`）的概率更低

> Cookie 会以明文存在插件目录的 `config.toml` 里，**不要提交到公开仓库**（仓库的 `.gitignore` 已忽略 `config.toml`）。旧版本把 Cookie 放在 `[advanced].cookie`，仍然兼容，但建议迁移到 `[account].cookie`。

### `[sources]` 视频来源

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `search_enabled` | `true` | 按关键词搜索（推荐，命中率最高） |
| `search_pages` | `1` | 每个关键词搜几页 |
| `search_keywords_per_round` | `3` | 每轮最多用几个兴趣词搜索 |
| `search_order` | `""` | `""` 综合 / `pubdate` 最新 / `click` 播放 / `dm` 弹幕 |
| `search_delay_seconds` | `1.2` | 关键词之间的间隔，避免请求过密 |
| `popular_enabled` | `false` | 热门列表（以官方/大 UP 为主，默认关掉以避开主流官方内容） |
| `popular_page_size` | `20` | 抓热门时每页多少条 |
| `popular_pages` | `1` | 抓热门时抓几页 |
| `ranking_enabled` | `false` | 排行榜（风控严，未配 Cookie 常失败） |
| `ranking_rids` | `[0]` | 排行榜分区号，`[0]` = 全站 |

### `[[categories]]` 内容类别

用于按类别主动搜索，默认四类，可增删改。类别名会被 `[[streams]].categories` 引用。

```toml
[[categories]]
name = "新鲜资讯"                 # 类别名
enabled = true                   # 是否启用
keywords = ["新游", "版本更新", "资讯", "首曝", "开箱"]  # 该类别的搜索关键词
search_order = "pubdate"         # 该类别排序：留空用 [sources].search_order
max_share_per_round = 0          # 该类别每轮最多几个，0 = 不单独限制
min_play = 0                     # 该类别最低播放量，0 = 沿用 [filter].min_play
```

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `name` | — | 类别名，`[[streams]].categories` 引用这个名字 |
| `enabled` | `true` | 是否启用这个类别 |
| `keywords` | — | 这个类别用来搜索的关键词 |
| `search_order` | `""` | 该类别搜索排序；留空用 `[sources].search_order` |
| `max_share_per_round` | `0` | 该类别每轮最多几个，`0` = 不单独限制（与 `[schedule]`、`[[streams]]` 的同名项取最严的） |
| `min_play` | `0` | 该类别最低播放量，`0` = 沿用 `[filter].min_play` |

### `[interest]` 兴趣与语义判断

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `use_recent_chat` | `true` | 从近期聊天自动提取兴趣 |
| `lookback_hours` | `48` | 回看最近多少小时的聊天 |
| `max_messages_per_stream` | `200` | 每个流最多读多少条消息 |
| `top_keywords` | `12` | 自动提取最多取前几个词 |
| `keyword_min_count` | `2` | 一个词至少出现几次才算兴趣 |
| `explicit_weight` | `3.0` | 手写兴趣的权重 |
| `auto_weight` | `1.0` | 自动提取兴趣的权重 |
| `persist_auto_interests` | `true` | 把自动提取的兴趣**写回配置**的 `[[streams]].auto_interests` |
| `persona_seed_enabled` | `true` | 一点兴趣都没有时，按人设让 LLM 生成一份初始兴趣 |
| `persona_seed_count` | `8` | 初始兴趣生成几个（3 ~ 20） |
| `persona_seed_interests` | `[]` | **人设初始兴趣（通用配置，所有流共用）**；留空时插件会按人设**自动生成一次**并填进来，也可以直接改 |
| `exclude_user_ids` | `[]` | 不参与兴趣提取的账号（填 QQ 号），用来挡其他 bot |
| `use_tags` | `true` | 视频标签参与打分 |
| `tag_weight` | `0.7` | 标签命中的权重 |
| `use_subtitles` | `true` | 读字幕（**需要登录**，未登录时 B 站不返回字幕内容） |
| `use_llm_judge` | `true` | LLM 语义判断 + 生成自然语言介绍 |
| `llm_task_name` | `planner` | 文本 LLM 用的模型任务名 |
| `llm_max_candidates` | `5` | 每轮最多让 LLM 判断几个候选（控成本） |
| `llm_temperature` | `0.3` | LLM 温度，越低越稳定保守 |
| `extra_stopwords` | `[]` | 额外停用词，提兴趣时忽略，如 `["哈哈", "什么"]` |

**关于自动兴趣（重点）**

1. 每轮会从该流最近 `lookback_hours` 小时的聊天里提取高频词；
2. 提取结果会写进 `config.toml` 里对应 `[[streams]]` 的 `auto_interests`，**设置页刷新就能看到**；
3. 想改就改——手动改过之后，插件认为你接管了，**不再自动覆盖**（相当于锁定）；
4. 想恢复自动维护：把 `auto_interests` 清空即可，下一轮会重新写；
5. `[interest].persona_seed_interests`（**通用配置，所有流共用**）留空时，插件会按 MaiBot 人设让 LLM **自动生成一次**并填进来——不用等某个流没兴趣才触发；某个流**既没有手写兴趣、聊天里也提不出词**时，也用它兜底。生成后可以直接改。
6. 提取时会自动丢掉「没有兴趣含义」的高频词（`用户`、`主人`、`消息`、`https`、`ww` 这类），以及**群里喊 bot 的名字和别名**（比如人设叫「小鲸鱼」，那 `小鲸鱼` / `鲸鱼` 不会被当成群的兴趣）；嫌不够可以用 `extra_stopwords` 自己补。宿主环境装了 [jieba](https://github.com/fxsjy/jieba) 时会用它分词（只保留名词类词，词更像真实兴趣）；没装就退回字符窗口统计，功能一样但词汇会碎一些。
7. **bot 消息不参与提取**：插件自己的账号会自动跳过；其他 bot 没法可靠地自动认出来（按昵称猜会误伤真人，所以不做），要挡哪个就把它的 QQ 号填进 `exclude_user_ids`。

> 写配置用的是 `tomlkit`，会保留你手写的注释和格式；写完只影响文件，插件会同步内存里的配置，不需要重启。写回动作会**攒到这一轮跑完再落盘**——因为宿主的 `config.toml` 一变就会重载插件，中途写盘会把正在跑的这轮打断。

### `[vision]` 抽帧识图

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 是否抽帧看画面（只对最终入选的视频做） |
| `task_name` | `vlm` | 识图模型任务名，**必须支持视觉**（`planner` 通常不支持图片） |
| `frame_count` | `4` | 抽几帧 |
| `max_width` | `640` | 帧宽（越小越省 token） |
| `max_download_mb` | `80` | 下载体积上限 |
| `timeout_seconds` | `90` | 下载 + 抽帧总超时 |

### `[danmaku]` 弹幕与热门评论

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `danmaku_enabled` | `true` | 是否抓弹幕 |
| `danmaku_fetch_limit` | `400` | 最多抓多少条原始弹幕 |
| `danmaku_limit` | `40` | 过滤后最多送多少条弹幕给 LLM |
| `comments_enabled` | `true` | 是否抓热门评论 |
| `comments_limit` | `10` | 最多送多少条热门评论给 LLM |
| `filter_ai_spam` | `true` | 过滤 AI 鱼灾式内容（模板化「感谢分享，学到了」、AI 生成声明、纯符号刷屏等） |

### `[filter]` 硬过滤

不符合的视频直接丢掉，不进打分。

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `min_duration_seconds` | `60` | 视频最短时长（秒） |
| `max_duration_seconds` | `1800` | 视频最长时长（秒） |
| `min_play` | `5000` | 最低播放量 |
| `min_like` | `100` | 最低点赞数 |
| `max_age_days` | `30` | 只分享最近多少天内发布的，`0` = 不限 |
| `block_keywords` | 见配置 | 标题/简介命中即跳过（广告、带货、抽奖、互粉、三连、付费课程） |
| `block_up_names` | `[]` | 这些 UP 主的视频不分享 |
| `exclude_partitions` | `[]` | 这些分区的视频不分享，如 `["资讯", "影视"]` |
| `exclude_official` | `true` | 排除主流官方内容 |
| `official_keywords` | 见配置 | 命中标题/简介即排除：官方、预告、PV、正片、发布会、前瞻、速览、兑换码…… |

### `[scoring]` 打分

```
总分 = 兴趣分 × interest_weight + 热度分 × hot_weight + 新鲜度分 × freshness_weight
```

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `interest_weight` | `1.0` | 兴趣命中分的权重 |
| `title_match_boost` | `2.0` | 标题命中兴趣词时的额外加成倍数 |
| `hot_weight` | `1.0` | 播放/点赞热度的权重 |
| `freshness_weight` | `0.6` | 新鲜度的权重 |
| `freshness_days` | `7` | 新鲜度按多少天内线性衰减 |
| `min_score` | `4.0` | 入选最低分（纯规则分；开启 LLM 判断后还要通过 LLM） |
| `require_interest_match` | `true` | 必须命中至少一个兴趣词（关掉更容易分享但容易跑题） |

兴趣命中按字段给不同权重：标题 `title_match_boost`（默认 2.0）、简介 0.8、分区 0.6、UP 名 0.4、标签 `tag_weight`。

### `[persona]` 人设语气

分享文案（推荐语 / 自然语言介绍）按 MaiBot 主程序里配置的**麦麦人设**来写，而不是固定模板语气。

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 是否启用人设语气 |
| `name` | `""` | 手动覆盖人设名字；留空用宿主的 |
| `personality` | `""` | 手动覆盖人设描述；留空用宿主的 |
| `reply_style` | `""` | 手动覆盖说话风格；留空用宿主的 |
| `style_hint` | `""` | 追加给 LLM 的额外要求，如「多提一句视频看点」 |
| `cache_seconds` | `120` | 人设缓存时间（秒），过期后下次分享前自动重读 |

人设通过宿主提供的 `config.get` 能力读取，字段为 `bot.nickname`、`bot.alias_names`、`personality.personality`、`personality.reply_style`、`personality.behavior_style`（即实例 `config/bot_config.toml` 里 `[bot]` 与 `[personality]` 两节）。读不到时退回中性语气，不影响分享。

> 人设只影响「怎么说」，不影响「分享什么」：筛选与打分仍由 `[interest]` / `[scoring]` 决定。

### `[share]` 分享格式

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `style` | `"text"` | 分享形式：`text` 一条文字、`image` 封面图 + 文字、`card` QQ 小程序卡片 |
| `card_caption` | `true` | `style = "card"` 时，卡片前面要不要先发那句随口话 |
| `casual_max_chars` | `60` | 那句随口话的字数上限（`20`~`160`） |
| `include_stats_line` | `false` | 链接后面是否附一行播放量 / 点赞 / UP 主 |

`text` 下分享是**一条消息**「随口一句 + 【标题】链接」，不分条、也不再单独发推荐语或长介绍——那几个开关在「随口一提」下本来就被短路，留着只会让人以为改了有用，所以 1.3.0 全部删掉了。

字数上限管两头：LLM 生成的那句话会被截到这个长度；没有 LLM 可用时，退回的视频简介也会被截到这个长度。

`image` 和 `card` 都依赖视频封面（B 站接口返回的 `pic` 字段，插件会自动补成 `https://`）。封面下载失败时不会空转：`image` 退回 `text`，`card` 先退回 `image`（`fallback_to_image = true` 时）再退回 `text`，所以**最差情况就是退化成原来的纯文字分享**，不会丢消息。

### `[card]` 卡片直发

`[share].style = "card"` 时，插件会自己拼一张 OneBot 的 `json` 段（小程序卡片）发给 QQ。

**为什么不能走麦麦自带的发送能力？** 麦麦主程序与 SnowLuma / NapCat 适配器的出站编码器都只认 `text` / `image` / `at` / `reply` 这些基础段，`json` 段会被降级成一行 `[unsupported:json]` 文本（入站方向它们倒是能解析卡片）。所以插件改成**直接连 QQ 客户端的 OneBot HTTP 接口**发这一条，绕开出站白名单。

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `api_base` | `""` | QQ 客户端的 OneBot HTTP 地址，如 `http://172.23.0.2:3000`；留空则卡片不启用（`card` 自动退回图文） |
| `access_token` | `""` | 上面接口的 Token，要和 QQ 客户端里配的一致 |
| `card_kind` | `"miniapp"` | `miniapp` = 小程序卡片（需要封面）；`news` = 通用图文卡片（没有封面也能发） |
| `fallback_to_image` | `true` | 卡片发不出去时，改发「封面图 + 文字」 |
| `timeout_seconds` | `8.0` | 请求 OneBot 接口的超时（`1`~`30` 秒） |

怎么填 `api_base`：

- **NapCat**：看它配置里 HTTP 服务监听的 `host` 与 `port`（默认端口 `3000`），地址就是 `http://<宿主机或容器 IP>:<端口>`。
- **SnowLuma**：默认就开着 OneBot HTTP：`host` `0.0.0.0`、`port` `3000`、`path` `/`，所以地址一般就是 `http://<SnowLuma 容器 IP>:3000`。Token 在它的配置里：`config/onebot.json`（全局默认）或 `config/onebot_<uin>.json`（账号级覆盖）的 `accessToken`，授权用 `Authorization: Bearer` 或 `?access_token=` 都行。要改端口就在这两个 JSON 的 `httpServers` 里改 `port`，改完重启 SnowLuma。
- 容器里的 SnowLuma 记得把 `3000` 映射出来（官方 compose 是 `-p 3000:3000`，WebUI 是 `5099`、WS 是 `3001`）；插件和它在同一个 Docker 网络时可以直接用容器 IP，跨机器就用宿主机 IP + 映射端口。
- 插件和 QQ 客户端不在同一台机器 / 同一个 Docker 网络时，`127.0.0.1` 是**不通**的，要填对方容器的地址（`docker inspect -f "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}" <容器名>`）或宿主机可达的地址。
- 填了 Token 时，插件先试 `Authorization: Bearer <token>`，被 401 / 403 挡回会自动改走 `?access_token=`。

群聊先走 `/send_group_msg`（`group_id`），私聊先走 `/send_private_msg`（`user_id`），群号 / QQ 号会转成整数再发，从宿主枚举到的聊天流里取；取不到就按卡片失败处理、走上面的回退链。

**SnowLuma 的兼容处理**：SnowLuma 画像只实现通用 `/send_msg`（要带 `message_type`），不认 `send_group_msg` / `send_private_msg`。插件第一次发如果收到「不认识的动作」（`retcode` 为 `1404`，或提示里有 `unknown action` / `不支持`），会自动换成 `/send_msg` + `message_type: group|private` 再发一次；其它失败（比如风控）不会重发，避免重复发消息。

> 安全提示：这个地址只填内网里的 QQ 客户端，**不要暴露到公网**，Token 也当密码对待。

### `[shiwei]` 赤石视频

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 是否启用赤石视频工具（由麦麦自己决定什么时候整活） |
| `cooldown_seconds` | `600` | 该工具的冷却（秒） |
| `max_share` | `1` | 一次最多分享几个 |
| `keywords` | 见配置 | 搜索关键词（抽象/整活/鬼畜/离谱/翻车/整蛊/土味/迷惑行为…） |
| `search_keywords_per_round` | `2` | 每次用几个关键词去搜 |
| `min_play` | `20000` | 最低播放量 |
| `min_duration_seconds` | `10` | 视频最短时长（秒） |
| `max_duration_seconds` | `900` | 视频最长时长（秒） |
| `block_keywords` | 见配置 | 命中即跳过（官方/预告/发布会/宣传片） |

麦麦调用 `bili_shiwei` 工具时只发「【标题】链接」，**不带介绍文案**，也不影响普通分享的去重与冷却。这条路径同样会过 `[filter]` 的全局过滤（`block_keywords`、`block_up_names`、`exclude_official`），并且**只对白名单会话生效**。

### `[read]` 读视频

这个插件不只是「往外发」，也能「往里读」。同一套接口数据，分两档深度：

- **brief（默认）**：别人贴链接时自动读这一档——标题 / UP 主 / 分区 / 时长 / 数据 / 发布时间 / 分P / 标签 / 简介 / CC 字幕节选 / 热评 / 弹幕采样，全文上限 `max_chars`，**不调视觉模型**，只够「大概知道这视频讲什么」，最省 token。
- **full（有人特意让你看时）**：字幕按 90 秒分块带 `[分:秒]` 时间轴、弹幕按分钟聚合成时间线、热评 15 条、分P 全列、简介 800 字、整体上限 9000 字；`full_vision_enabled` 打开时还会**下载视频抽 4 帧交给视觉模型**，把画面内容一起写进上下文。这条对「贴链接自动读」和「麦麦自己调 `bili_read` 工具」都成立——工具传 `detail="full"` 时返回内容里会多出 `【画面描述（抽帧）】` 一段。

什么时候自动切 full：消息里 **@ 了麦麦**（at 段）、文字命中 `full_trigger_keywords`（默认「详细看看 / 仔细看看 / 认真看 / 深度看 / 完整看 / 展开说说 / 细讲 / 逐段 / 画面讲了什么…」），或者**在私聊里贴链接**（`full_in_private` 打开时；私聊没有 @，对着麦麦贴链接本身就是「特意让它看」）。其他人只是在群里丢个链接时走 brief。两档的去重键是分开的——同一个视频先被 brief 读过，之后再被 @ 一次仍会重新 full 读。

两条入口共用同一套读取逻辑：

1. **工具 `bili_read`**：把「读取 B 站视频内容」作为工具交给麦麦。你贴链接、或者问「这视频讲了什么」「值不值得看」时，它会自己调用工具，拿到的是接口里的真实数据，而不是靠标题猜。参数是 `url`（链接 / BV 号 / av 号都行）和可选的 `detail`（`brief` 默认、省 token；`full` 深度读）。

   这个工具对 Planner **直接可见**（声明里的 `visibility="visible"`），不需要先 `tool_search`，所以模型不用为了「看见工具」去派子代理；一次调用通常 1 秒内返回。代价是 Planner 每轮多一条工具说明（不到 100 token）。
2. **贴链接自动读**：白名单会话里（`[[streams]]`）有人贴了 B 站链接，插件会立刻读一遍，把内容写进当前会话上下文（`maisaka.context.append`），麦麦下一条回复就能自然聊起来，不用等它主动调工具。

读到的内容包括：标题 / UP 主 / 分区 / 时长 / 播放·弹幕·评论·点赞·投币·收藏 / 发布时间 / 分P 列表 / 标签 / 简介 / CC 字幕节选 / 热评 / 弹幕采样。任何一项拿不到就跳过，不会因为缺字幕整条失败。

**brief 的截断规则**：简介 300 字、字幕节选、热评 8 条、弹幕采样 24 条、分P 最多列 8 个，全文上限 `max_chars`。**full 不走这几个配置**（内部固定 15 条热评 / 120 条弹幕 / 12000 字幕字 / 9000 输出字），适合「把这期视频讲清楚」这类问题；日常聊天用 brief 更省 token。匿名没有 CC 字幕时，full 也只能靠弹幕时间线 + 热评 + 画面推断内容。

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 是否启用 `bili_read` 工具（关掉后麦麦就调不到它了） |
| `auto_read_enabled` | `true` | 白名单会话里贴链接时是否自动读 |
| `auto_read_max_links` | `1` | 一条消息最多自动读几个链接（`1`~`3`，防止刷屏） |
| `auto_read_all_streams` | `false` | 关掉白名单限制：机器人所在的**任何会话**有人贴链接都自动读（群聊仍只做 brief，私聊仍按 `full_in_private`）。`[[streams]]` 没覆盖到的群靠它生效 |
| `full_in_private` | `true` | 白名单里按「私聊用户 ID」配置的会话，贴链接默认按 full 深度读（含抽帧看画面）；关掉则私聊也只认 @ 和 `full_trigger_keywords` |
| `full_trigger_keywords` | 见配置 | 命中这些词就自动切 full 深度读（@ 麦麦也会触发）。默认「详细看看 / 仔细看看 / 认真看 / 深度看 / 完整看 / 展开说说 / 细讲 / 逐段 / 画面讲了什么 / 画面内容」 |
| `full_vision_enabled` | `true` | full 读时是否抽帧交给视觉模型看画面（还要 `[vision].enabled` 同时打开、`vlm` 任务可用） |
| `comment_limit` | `8` | 读几条热评（`0`~`20`） |
| `danmaku_limit` | `30` | 采样多少条弹幕（`0`~`100`） |
| `use_subtitle` | `true` | 是否读 CC 字幕（需要登录 Cookie） |
| `max_chars` | `3500` | 交给模型的内容上限，太长会按顺序截断 |
| `cache_seconds` | `900` | 同一个视频多少秒内复用缓存（也用作自动读的防重复窗口） |

同一条视频在 `cache_seconds` 内被重复读到会直接走缓存，不会反复打 B 站接口；缓存按读取参数分键，brief 与 full 各存一份。短链（`b23.tv` / `bili2233.cn`）会先跟随跳转再解析。

### `[meme]` 学梗

从视频里学梗：读字幕 + 弹幕爆发点 + 热评，让模型整理成一张梗卡，需要时写进麦麦的黑话库（`Jargon` 表）。

写库后的效果：麦麦聊天时如果群友提到这个梗，宿主的黑话匹配会把释义塞进上下文，它就能自然用上，不需要你教第二遍。

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | `false` | 是否启用学梗（默认关闭；关着时 `bili_learn_meme` 工具会回「学梗功能没开」） |
| `write_to_db` | `true` | 学到梗是否写进黑话库；关掉只把梗卡返回给模型，不改数据库 |
| `auto_learn` | `true` | 自动读视频时，标题像梗类视频（`什么梗` / `梗指南` / `梗科普` 等）就顺手学一次；同一个 BV 只学一次 |
| `llm_task` | `""` | 学梗用哪个模型任务；留空用 `[interest].llm_task_name` |
| `min_confidence` | `0.35` | 模型给出的置信度低于这个值就丢弃（`0`~`1`），避免把瞎猜的梗写进库 |
| `reject_taboo_strong` | `true` | 判定为高敏感（`strong`）的梗不入库，防止把地域 / 民族 / 性别之类的攻击性词条写进去 |
| `comment_limit` | `14` | 学梗时挑几条热评喂给模型（`0`~`30`） |
| `burst_window_seconds` | `10` | 弹幕峰值按多少秒分桶（`5`~`60`）；弹幕在同一段里炸开的地方通常就是梗 |
| `max_subtitle_chars` | `6000` | 字幕最多截多少字进提示词（`500`~`20000`） |

写库有几条保护：

- **同名不覆盖手写梗**：黑话库里已有的同名条目如果不是本插件写的（`created_by` 不是 `AI`，或证据里没有 B 站来源），只提示「保留原记录」，绝不改它。
- **本插件写的会更新释义**：同名条目是本插件写的，只更新 `meaning` 与证据，**保留 `count` 使用次数**。
- **没有 `database.query` 能力时降级**：老版本麦麦没有数据库能力，梗卡照样返回，只是不入库。

一次学梗要多花一次模型请求（提示词约 800~1500 字，字幕长的视频更多），所以默认关着，按需打开。

### `[advanced]` 运行参数

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `data_dir` | `""` | 状态文件目录；留空用宿主给的数据目录 `data/plugins/<插件ID>` |
| `request_timeout_seconds` | `12.0` | 单次 B 站请求超时（秒） |
| `user_agent` | 见配置 | 请求用的浏览器标识 |
| `cookie` | `""` | **（旧版）** Cookie，已迁移到 `[account].cookie`，仅为兼容保留 |
| `ffmpeg_path` | `""` | 自定义 ffmpeg 路径；留空自动查找（imageio-ffmpeg / PATH） |
| `dedupe_days` | `30` | 同一视频多少天内不重复分享 |
| `max_seen_records` | `3000` | 最多保留多少条已分享记录（超过淘汰最旧的） |
| `debug` | `false` | 是否输出调试日志（排查问题时打开） |

### `[[streams]]` 白名单聊天流

想分享到哪个群 / 私聊，就加一段 `[[streams]]`，可以写多段。

```toml
[[streams]]
name = "原神群"                    # 备注名
enabled = true                    # 是否启用
stream_id = ""                    # 会话 ID，填了优先级最高
group_id = "123456789"            # 群号
user_id = ""                      # 私聊用户 ID
interests = ["原神", "机械键盘"]     # 手写兴趣
auto_interests = []               # 自动提取的兴趣（插件写，可手改）
categories = []                   # 启用的内容类别名，留空=全部启用的类别
auto_interest = true              # 是否从该流近期聊天自动提取兴趣
max_share_per_round = 0           # 该流每轮最多几个，0 = 用 [schedule] 的值
```

| 键 | 说明 |
| --- | --- |
| `name` | 备注名 |
| `enabled` | 是否启用 |
| `stream_id` | 聊天流 ID（session_id），填了优先级最高 |
| `group_id` | 群号，未填 `stream_id` 时按群号反查 |
| `user_id` | 私聊用户 ID，前两者都空时按用户反查 |
| `interests` | 手写兴趣词（权重 `[interest].explicit_weight`） |
| `auto_interests` | 自动提取的兴趣，插件会写回这里；手动改过即锁定，清空可恢复自动维护 |
| `categories` | 该流启用的类别名，留空 = 全部启用的类别 |
| `auto_interest` | 是否从该流近期聊天自动提取兴趣 |
| `max_share_per_round` | 该流每轮最多几个，`0` = 用 `[schedule]` 的值 |

---

## 工具

聊天指令已经全部移除：QQ 平台侧会拦截 `/` 开头的消息，实测发不出去。现在只保留工具，由麦麦自己判断什么时候用：

| 工具 | 参数 | 说明 |
| --- | --- | --- |
| `bili_read` | `url` 必填；`detail` 可选（`brief` / `full`） | 读一个 B 站视频：标题、UP、简介、分P、字幕、热评、弹幕；`full` 另带字幕时间轴、弹幕时间线与画面描述 |
| `bili_search` | `query` 必填；`count` 可选 | 按关键词搜 B 站并挑一条分享到当前会话；受 `search_cooldown_seconds` 冷却，**只在白名单会话里生效** |
| `bili_shiwei` | 无 | 抓一个抽象整活视频（赤石），只发标题+链接；受 `[shiwei].cooldown_seconds` 冷却，**只在白名单会话里生效** |
| `bili_learn_meme` | `url` 必填；`hint` 可选 | 从视频里学梗并写进麦麦黑话库；`hint` 是用户点名要学的梗。受 `[meme].enabled` 控制，默认关闭 |

四个工具都对 Planner 直接可见（`core_tool` + `visibility="visible"`），不需要先 `tool_search`，也不用派子代理空转等待。

---

## 工作原理

```
定时触发（interval + 抖动）
  ├─ 检查：插件启用 / 在活跃时段 / 有白名单流
  ├─ 对每个白名单流：
  │    ├─ 解析会话（同群多套会话时自动兜底到能发出去的那个）
  │    └─ 构建兴趣画像：手写兴趣 + 聊天提取（写回 auto_interests）+ 人设初始兴趣
  ├─ 收集搜索关键词（兴趣词 + 启用的内容类别关键词）
  ├─ 抓候选：关键词搜索 / 热门 / 排行榜 → 合并去重
  ├─ 硬过滤：时长、播放、点赞、发布天数、屏蔽词、官方内容
  ├─ 去重：踢掉已分享过的 bvid
  ├─ 打分排序 → 取前 N（min_score / require_interest_match）
  ├─ 对候选做深度理解：
  │    ├─ 读标签 / 字幕（需登录）
  │    ├─ 读弹幕（过滤 AI 鱼灾）+ 热门评论
  │    ├─ 下载视频抽 4 帧（vlm 任务识图）
  │    └─ 读人设 → LLM 判断是否贴合兴趣 → 按人设口吻生成推荐语 + 介绍
  ├─ 发送：默认一条简短消息（标题+链接+一句随口评价）；打开分条后按推荐语 / 标题+链接 / 介绍发
  └─ 写入 state.json（去重名单 + 分享记录 + 冷却时间）
```

---

## 数据文件

状态存在 `<data_dir>/state.json`（`data_dir` 默认取宿主给插件的数据目录，一般是 `data/plugins/github.BsXinQin.bilibili_video_watch/`）：

```json
{
  "version": 2,
  "seen": { "BV1xx": { "title": "...", "shared_at": 1760000000, "kind": "auto" } },
  "runs": [ { "at": 1760000000, "fetched": 120, "shared": 1 } ],
  "cooldowns": { "search": 1760000000, "shiwei": 1760000000 },
  "auto_interests": { "group_id:123456789": ["原神", "机械键盘"] }
}
```

- 名单里的视频**不会再被自动抓取/分享**
- 每条分享记录保存了当时的 `narrative`（发出去的那句随口话）；旧版本留下的 `summary` / `points` / `intro` 仍兼容读取
- `auto_interests` 记录「插件上次写了什么」，用来判断你有没有手动改过
- 删除 `state.json` 等于「忘掉所有历史」，下次会重新分享

---

## 目录结构

```
github.BsXinQin.bilibili_video_watch/
├── _manifest.json        # 插件清单（宿主识别用）
├── CHANGELOG.md          # 版本变更记录
├── README.md             # 本文档
├── config.example.toml   # 带中文注释的配置模板（可复制成 config.toml）
├── config.py             # 配置模型（WebUI 表单由它生成，全中文）
├── config_edit.py        # 把自动兴趣写回 config.toml
├── plugin.py             # 插件主体：调度、抓取、打分、分享、工具、Hook
├── bilibili.py           # B 站接口客户端（WBI 签名、搜索、详情、弹幕、评论、字幕）
├── interest.py           # 兴趣关键词提取与打分
├── textfilter.py         # AI 鱼灾弹幕/评论过滤
├── media.py              # 取流下载 + ffmpeg 抽帧
├── llm_judge.py          # LLM 语义判断、自然语言介绍、推荐语、人设兴趣种子
├── persona.py            # 通过宿主 config.get 能力读人设，提供人设提示词
├── store.py              # 去重名单、分享历史、冷却状态、自动兴趣缓存（state.json）
├── tools/
│   ├── live_check.py     # 真实联网自检（可选抽帧/弹幕/LLM/赤石）
│   ├── probe_persona.py  # 人设自检：打印读到的人设 + 注入后的 prompt（不调模型）
│   └── probe_vision.py   # 单独验证「下载 + 抽帧」链路
├── tests/                # unittest 单元测试（280 个用例）
├── build.py              # 打发布包：dist/github.BsXinQin.bilibili_video_watch-<版本>.zip
└── .github/workflows/    # GitHub Actions：每次 push 自动跑单元测试
```

---

## 测试与自检

单元测试（280 个用例，不需要联网）：

```powershell
# 用 MaiBot 所在环境的 python
& "<MaiBot>\.onekey\environments\<env>\python.exe" -X utf8 -m unittest discover -s tests
```

如果环境里没装依赖，先补上（`imageio-ffmpeg` 只在抽帧时用到，测试里会跳过）：

```powershell
& "<MaiBot>\.onekey\environments\<env>\python.exe" -m pip install maibot-plugin-sdk httpx segno tomlkit imageio-ffmpeg
```

打发布包（产物在 `dist/`，顶层目录就是插件 id，版本号读自 `_manifest.json`）：

```powershell
python build.py
```

CI：`.github/workflows/tests.yml` 在 push / PR 时用 Python 3.13 装依赖并跑同一套 `unittest`。

覆盖：兴趣提取与打分、B 站客户端（httpx.MockTransport）、读视频（brief / full 两档、缓存、超长截断）、自动读（brief 不调视觉、@ 与关键词触发 full、去重按模式分键）、弹幕/评论解析与鱼灾过滤、抽帧帧选择、LLM 判断降级、人设读取与提示词注入、自动兴趣写回与锁定、人设兴趣种子、插件完整轮次、去重、冷却、赤石（含全局屏蔽词）、工具（白名单与参数校验）、类别级每轮上限、下载地址与 bvid 安全校验、配置模板与文档一致性，以及用宿主真实 `ManifestValidator` / `PluginLoader` 校验打包。

真实联网自检：

```powershell
# 基本一轮：搜索「机械键盘」「原神」，分享 2 个
python tools/live_check.py 机械键盘 原神 --limit 2

# 带弹幕/评论 + 分条格式
python tools/live_check.py 原神 --limit 2 --danmaku --parts

# 走完整 LLM 判断（需要宿主环境能调 planner）
python tools/live_check.py 原神 --limit 1 --llm

# 验证赤石（bili_shiwei）
python tools/live_check.py --shiwei --limit 2 --parts

# 单独验证下载 + 抽帧（不调 LLM）
python tools/probe_vision.py --save ..\bili-frames

# 人设自检：打印读到的人设和注入后的 prompt（不调模型）
python tools/probe_persona.py --data-dir "<MaiBot>\data\plugins\github.BsXinQin.bilibili_video_watch"
```

> `probe_persona.py` 是开发自检脚本，直接解析实例里的 `config/bot_config.toml` 来模拟宿主的 `config.get` 返回值；插件运行时并不直接读文件。

---

## 常见问题

**Q：Cookie 填了还是读不到字幕？**
先确认 Cookie 没有写错、没有多余换行；再把 `[advanced].debug` 打开，看启动时 nav 检查打出的登录名。字幕也不是每个视频都有——B 站只对上传过字幕的视频返回内容。

**Q：登录成功了，但字幕还是读不到？**
字幕是 B 站按视频和登录态返回的，不是每个视频都有（很多视频根本没上传字幕）。登录能显著提高覆盖率，但不能保证 100%。

**Q：自动兴趣一直不更新？**
两种可能：① 你把 `auto_interests` 手动改过了，插件认为你接管了——清空它即可恢复自动维护；② 该流 `auto_interest = false` 或 `use_recent_chat = false`。

**Q：某个群一直不分享？**
先看 `[[streams]]` 白名单是否包含它、`enabled` 是否为 `true`；再把 `[advanced].debug` 打开，看候选是不是都被过滤或分数不够。`min_score` 过高、`require_interest_match = true` 且兴趣词太少都会导致选不出视频；也可以让麦麦用 `bili_search` 手动搜一条来验证链路。

**Q：配置页显示的版本号对不上 GitHub 最新 Release？**
配置页显示的是**当前安装的插件包**里的 `config_version`。宿主升级插件包后会自动把配置版本迁移到新版；如果市场还没收录新版本，需要等审核通过后从市场更新。

**Q：读视频报「B 站接口返回 HTTP 412」？**
412 是 B 站 WAF 层的风控拦截，不是接口参数错。常见原因是服务器（云主机 / 机房 IP）被风控，或者匿名请求没带 `buvid` Cookie。1.0.1 起插件会先自动申请 `buvid3`/`buvid4` 并重试一次；仍然 412 就在 WebUI 的 `[account] cookie` 里填上浏览器 Cookie，登录态能绕过大部分风控。填了 Cookie 还报 412，说明是该出口 IP 被限制，需要换网络或走代理。

**Q：选了卡片，但发出来还是图文 / 纯文字？**
按顺序看：① `[card].api_base` 填了没、从插件所在的容器能不能访问到（`docker exec <麦麦容器> python -c "import socket; socket.create_connection(('<IP>', <端口>), 3)"` 试一下）；② `access_token` 是否和 QQ 客户端一致；③ 日志里搜「卡片」，失败原因会写在那里（带 HTTP 状态或接口返回的报错）。另外 `miniapp` 需要封面，没封面的视频会自动降级成 `news` 图文卡片，这也算正常。

**Q：换连接器 / 适配器后发不出去？**
换账号后同一个群可能同时存在旧账号和新账号两套会话，宿主接口可能返回已掉线的那套。插件会枚举同群/同用户的会话，发送失败时自动换一个重试并记住可用的那个。

---

## 已知限制

- **只用公开接口**，不登录也能工作；但 B 站有风控：请求过密可能返回业务码 `-352`（自动重取 WBI 密钥重试一次），WAF 层则可能直接返回 HTTP `412`（自动补 `buvid` Cookie 重试一次）。两者都失败时请配置 Cookie 或换网络。
- **字幕需要登录**：未登录时 B 站不返回字幕内容，只能靠标题/简介/弹幕/画面判断。
- **排行榜接口**风控较严，未配 Cookie 时基本拿不到数据，所以默认关闭。
- **抽帧依赖 ffmpeg**：都没有时自动跳过识图，其余流程正常。
- **识图需要支持视觉的模型**：自动分享入选视频与 full 读都会调它，默认 `vlm` 任务；没有就把 `[vision].task_name` 改成支持图片的模型任务名，或关掉 `[vision].enabled`（full 读仍可用，只是没有画面描述）。
- **没有聊天指令**：平台会拦截 `/` 开头的消息，插件只提供 `bili_read` / `bili_search` / `bili_shiwei` / `bili_learn_meme` 四个工具，由麦麦自己决定什么时候调用。
- **LLM 判断有成本**：`llm_max_candidates` 控制每轮最多判断几个，默认 5。
- **人设依赖宿主配置**：通过 `config.get` 读取；读不到时退回中性语气，不影响分享。
- 分享的是**文字 / 封面图 / 小程序卡片**，不会把视频文件发到群里。
- **小程序卡片要额外开一个口子**：麦麦与两个 QQ 适配器都不透传出站 `json` 段，所以卡片由插件直连 QQ 客户端的 OneBot HTTP 发送；没配 `[card].api_base`、或 QQ 客户端那边没开 HTTP 服务时，只会发出图文 / 文字。
- **Cookie 以明文存盘**：写在插件目录的 `config.toml` 里，注意不要泄露或提交到公开仓库。
- 请遵守 B 站服务条款，不要把频率调得过低（`interval_minutes` 建议 ≥ 10）。

---

## 版本

- **v1.5.1**：卡片直发兼容 SnowLuma。SnowLuma 画像只认通用 `/send_msg` + `message_type`，插件在 `send_group_msg` / `send_private_msg` 被回「不认识的动作」时自动改用 `/send_msg` 重发一次（其它失败不重发，避免重复）；群号 / QQ 号发送前统一转整数；README 补上 SnowLuma 的 OneBot HTTP 默认端口与 `accessToken` 位置。
- **v1.5.0**：两件事。① `[schedule].active_hours` 支持**多段**（逗号隔开），能给午休留空档，如 `08:00-11:30, 13:00-21:00`，单段仍可跨午夜；某段写错时整轮按全天处理（宁可多发，也不因为一个笔误把分享全关）。② 新增 `[share].style`：`text` / `image` / `card`，`card` 直接发 **QQ 小程序卡片**——因为麦麦和两个 QQ 适配器的出站编码器都会把 OneBot `json` 段降级成 `[unsupported:json]` 文本，所以卡片由插件用 `httpx` 直连 QQ 客户端的 OneBot HTTP（`[card].api_base` + `access_token`，`card_kind` 可选 `miniapp` / `news`）；失败时逐级回退 卡片 → 图文 → 文字。视频数据模型补上封面字段 `cover`。
- **v1.4.0**：新增**学梗入库**（`[meme]` 配置节 + `bili_learn_meme` 工具，默认关闭）——读视频的字幕、10 秒弹幕爆发点与热评，让模型整理成梗卡写进麦麦黑话库（`Jargon` 表）；标题像「什么梗 / 梗指南 / 梗科普」的视频被自动读到时会顺手学一次，同一个 BV 只学一次。防污染：同名手写梗绝不覆盖，本插件写的只更新释义并保留使用次数，高敏感（`strong`）与低置信度一律不入库；宿主缺少 `database.query` 能力时只返回梗卡、不写库。
- **v1.3.2**：修「贴 `b23.tv` 短链读视频报 `短链解析失败：HTTP 412`」。短链解析改成手动跟随跳转、只读 `Location` 头，**不再去请求跳转后的视频页**——那个 HTML 页面对机房 IP 常被 B 站 WAF 拦成 412（短链本身没坏，坏的是那次多余的页面请求）；短链服务自身撞 412 时会先补一次 buvid Cookie 再试，仍失败则报出可操作提示。
- **v1.3.1**：修「白名单没覆盖的群里贴链接不自动读也没任何提示」。新增 `[read].auto_read_all_streams`（默认关）：打开后机器人所在的任何会话有人贴 B 站链接都会自动读，不再要求先写进 `[[streams]]`；关着时如果链接落在白名单外的会话，日志会打一条 INFO 说明是白名单没命中（以前是 debug，看不见）。自动读失败（短链解析失败、412 风控、B 站接口报错等）从 debug 改成 WARNING，报错原因直接进日志。
- **v1.3.0**：清理与修复。删掉「分享格式」里实际不起作用的七个选项（`casual_mode` / `intro_enabled` / `intro_max_chars` / `send_as_parts` / `part_delay_seconds` / `narrative_enabled` / `narrative_max_chars`），分享固定为一条「随口一句 + 【标题】链接」；删掉整个「重发」模块（`[reshare]` 配置、钩子里的重发逻辑、历史记录模糊匹配）。修好「别人在群里贴 B 站链接麦麦不自动看」：自动读改挂 `chat.receive.after_process`（原来挂在 `before_process`，那时消息还没 `process()`，取不到正文），并支持 `data` 为字符串的文本段、JSON 结构化段与平台卡片；白名单判定也接受「消息里的 group_id / user_id 命中 `[[streams]]`」，新群第一条消息就能生效。
- **v1.2.1**：修「麦麦调 `bili_read` 传 `detail="full"` 时不看画面」——深度读现在和自动读一样会抽帧交给视觉模型，返回内容里带 `【画面描述（抽帧）】`，同一个视频的画面描述会缓存 `cache_seconds` 秒避免重复抽帧；新增 `[read].full_in_private`（默认开），白名单里的私聊会话贴链接直接按 full 深度读。
- **v1.2.0**：删掉扫码登录与全部聊天指令（平台会拦截），读视频改成 brief / full 两档——别人贴链接默认 brief、不调视觉模型；@ 麦麦或说「详细看看」时切 full，带字幕时间轴、弹幕时间线并抽帧识图。分享默认只发一条简短消息（`intro_enabled` / `send_as_parts` / `narrative_enabled` 默认关闭）；主动搜索与赤石改成 `bili_search` / `bili_shiwei` 工具，白名单与冷却照旧。
- **v1.1.0**：`bili_read` 增加 `detail="full"` 深度读——字幕带 `[分:秒]` 时间轴、弹幕按分钟聚合成时间线、热评 15 条、分P 全列、简介 800 字，输出上限 9000 字；同时修掉弹幕时间戳解析错误（以前全挤在 00:00）。
- **v1.0.1**：修服务器 IP 撞上 B 站风控时读视频报 HTTP 412 的问题（自动申请 buvid Cookie 重试 + 可操作报错）。
- **v1.0.0**：首个发布版本（本项目从上游「麦麦刷B站」2.1.2 起独立维护，版本号重新从 1.0.0 计；插件 id 不变，可直接覆盖升级）。
  - **定时刷 B 站**：抓热门 / 排行榜 / 搜索候选，LLM 按当前会话兴趣挑选后生成分享卡片发到白名单会话；兴趣可手写、可从聊天自动提取、可用人设初始兴趣兜底。
  - **能读视频**：`bili_read` 工具，支持链接 / BV / av / 短链，返回简介、分P、CC 字幕、热评、弹幕采样；白名单会话里贴链接还会自动读进上下文。
  - **稳定性**：包内相对导入 + 顶层导入兜底、`get_components()` 提升 `chat_scope`、`bili_read` 对 Planner 直接可见（不再被派给子代理空转等待）。
  - 上游 2.x 的历史记录见 [CHANGELOG-upstream.md](CHANGELOG-upstream.md)，本版完整说明见 [CHANGELOG.md](CHANGELOG.md)。
