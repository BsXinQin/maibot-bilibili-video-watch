# 更新日志

「沁の刷B站视频」插件的版本变更记录。

版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)，格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。
`_manifest.json` 的 `version` 与 `config.py` 里的 `config_version` 始终一致。

## [1.4.0] - 2026-11-08

新增「学梗入库」：从视频里学到梗，整理成梗卡写进麦麦的黑话库（`Jargon` 表），之后聊天里命中这个梗麦麦会自然用上。默认关闭，按需打开。

### 新增

- **`[meme]` 配置节**（默认 `enabled = false`）：`enabled` 总开关、`write_to_db` 是否写库、`auto_learn` 梗类视频自动学、`llm_task` 用哪个模型任务（留空沿用 `[interest].llm_task_name`）、`min_confidence` 置信度门槛（默认 `0.35`）、`reject_taboo_strong` 拒收高敏感梗、`comment_limit` 喂几条热评、`burst_window_seconds` 弹幕分桶窗口（默认 10 秒）、`max_subtitle_chars` 字幕截断长度。
- **`bili_learn_meme` 工具**（`visibility="visible"` + `core_tool`，对 Planner 直接可见）：参数 `url` 必填、`hint` 可选。读视频的字幕、弹幕爆发点、热评，让模型整理成梗卡（梗名 / 含义 / 用法 / 例句 / 来历 / 时效 / 敏感度 / 置信度），再按配置写进黑话库。
- **`meme.py` 纯逻辑模块**：`clean_danmaku` 过滤纯情绪弹幕、`danmaku_bursts` 按 N 秒找弹幕峰值、`select_comments` 按「长度 + 点赞 + 解释 / 纠错信号」挑热评、`score_source` 给候选视频打分、`build_meme_prompt` 生成提示词（含「来历只能来自字幕」「例句必须是原句」「材料不足置信度压到 0.3 以下」「敏感梗不许判 none」等硬规则）、`parse_meme_json` 容忍代码块、`normalize_meme` 做入库校验、`compose_meaning` 拼释义、`format_meme_card` 渲染梗卡。
- **自动学梗**：自动读视频时如果标题命中「什么梗 / 梗指南 / 是什么梗 / 梗科普 / 梗百科 / 梗来源」，且 `[meme].auto_learn` 打开，顺手学一次并把梗卡塞进会话上下文；同一个 BV 只学一次。

### 变更

- `LlmJudge` 新增公开方法 `generate_text(prompt, task_name, max_tokens)`，学梗复用同一条模型链路。
- `_manifest.json` 的 `capabilities` 新增 `database.query`（写黑话库需要），`version` 升到 `1.4.0`。

### 说明

- 写库保护：同名条目如果不是本插件写的（`created_by` 不是 `AI`，或证据里没有 B 站来源），只提示「保留原记录」、绝不覆盖；本插件写的同名条目只更新 `meaning` 与证据，保留 `count` 使用次数。
- 宿主没有 `database.query` 能力（老版本麦麦）时，梗卡照样返回，只是不入库、并给出降级说明。
- 高敏感（`strong`）梗与低于 `min_confidence` 的梗一律不入库，避免污染黑话库。

## [1.3.2] - 2026-11-08

修「群里贴 `b23.tv` 短链读视频报 `短链解析失败：HTTP 412`」，并补上短链解析的单元测试。

### 修复

- **短链解析 412**：`resolve_redirect` 以前用 `follow_redirects=True` 一路跟到 `www.bilibili.com/video/BV...`，那个 HTML 页面对机房 IP 常被 B 站 WAF 拦成 HTTP 412，于是短链明明能用却报「解析失败」。现在改成手动跟随跳转、只读响应头里的 `Location`，落到视频页地址就立刻返回，**跳转后的页面一个请求都不发**（拿到 BV 号后走 api 接口，匿名也能读）。
- **短链服务自身 412**：撞上 412 时先申请一次 buvid3/buvid4 Cookie 再重试；仍失败则抛出带「填 B 站 Cookie」提示的可操作报错，不再只丢一个 `HTTP 412`。
- 跳转缺少 `Location`、跳转次数超过 5 次、非 4xx/5xx 之外的异常响应都会给出明确的错误文案。

### 新增

- `tests/test_bilibili.py` 新增 5 个用例：只读 `Location` 不发页面请求、多跳短链、412 补 buvid 后重试、412 无 buvid 报可操作错误、短链直接返回 200 的兜底。
## [1.3.1] - 2026-11-08

白名单外会话里贴链接既读不到、也看不到原因，补上一个开关和几条能看见的日志。

### 新增

- `[read].auto_read_all_streams`（默认 `false`）：打开后不再要求会话在 `[[streams]]` 里，机器人所在的任何会话有人贴 B 站链接都会自动读。群聊仍只做 brief、私聊仍按 `full_in_private` 判断深浅。

### 变更

- 链接落在白名单外的会话时，日志由 debug 改为 INFO 并带上会话 id 与群号，直接说明是白名单没命中、以及怎么让它生效。
- 自动读视频失败（短链解析失败、HTTP 412 风控、接口报错等）由 debug 改为 WARNING，报错原文进日志，不再静默跳过。

## [1.3.0] - 2026-11-08

按实际使用反馈清理配置、去掉用不上的模块，并修好「别人贴链接麦麦不自动看」。

### 移除

- 删除「分享格式」里实际不起作用的选项：`[share].casual_mode`、`intro_enabled`、`intro_max_chars`、`send_as_parts`、`part_delay_seconds`、`narrative_enabled`、`narrative_max_chars`。分享固定为一条消息「随口一句 + 【标题】链接」，只保留 `casual_max_chars` 与 `include_stats_line`；LLM 提示词也只要求 `narrative` 一个字段，不再生成 `intro`。
- 删除「重发」模块：`[reshare]` 整节（`enabled`、`trigger_keywords`、`history_keywords`、`match_window_days`、`min_match_score`、`reply_enabled`）、`_maybe_reshare` 钩子逻辑、`Store.find_by_query` 查询，以及「再发一遍」的确认话术。想再发一次直接重新贴链接即可。

### 修复

- 别人在群里贴 B 站链接时麦麦不自动看：自动读原来挂在 `chat.receive.before_process`，那时消息还没 `process()`，取不到正文；改挂 `chat.receive.after_process`（`timeout_ms=180000`、`error_policy=log`）。文本提取现在也认 `raw_message` 里 `data` 为字符串的文本段、JSON 结构化段与平台卡片（`platform_card_payloads`），链接藏在卡片里也能识别。
- 新群第一条消息也认：白名单判定改为「会话 ID 命中」或「消息里的 group_id / user_id 命中已启用的 `[[streams]]`」，不再依赖宿主先把会话登记进 `get_all_streams()`。
- 会话 ID 取不到时用钩子传入的 `stream_id` / `session_id` 兜底。

### 变更

- 读视频的缓存与去重键仍按 brief / full 区分，私聊默认 full 的行为不变。

## [1.2.1] - 2026-11-08

修「麦麦认真看视频时只看文字、不看画面」：抽帧识图原来只挂在贴链接自动读那条链路上，麦麦自己调 `bili_read` 传 `detail="full"` 时并不经过它。

### 修复

- `bili_read` 的 `full` 现在也会抽帧交给视觉模型：返回内容末尾追加 `【画面描述（抽帧）】` 段落，和自动读的效果一致（仍需 `[read].full_vision_enabled` 与可用的 `vlm` 任务）。
- `detail="brief"` 仍然完全不调视觉模型，省 token 的行为不变。

### 新增

- 画面描述按 `bvid` 缓存 `[read].cache_seconds` 秒，自动读和工具读共用，同一个视频不会重复抽帧。
- `[read]` 新增 `full_in_private`（默认 `true`）：白名单里按「私聊用户 ID」配置的会话，贴链接默认按 full 深度读——私聊没有 @，贴链接本身就是特意让麦麦看。关掉则私聊只认 @ 与 `full_trigger_keywords`。

### 变更

- 白名单解析时记录哪些会话属于私聊（按 `user_id` 配置且没填 `group_id`），供上面的默认深度读判断使用。

## [1.2.0] - 2026-11-07

按实际使用反馈做一次减法：聊天指令在 QQ 平台会被拦截，扫码登录用不上，读视频也要区分「随便看看」和「认真看看」。

### 移除

- 删除扫码登录模块（`bilibili_login.py`、`/扫码登录` 指令、`[account].qrcode_timeout_seconds`、`send.image` 能力）：登录改为在 WebUI 手动粘贴 `[account].cookie`，启动时用 `x/web-interface/nav` 检查登录态。
- 删除全部聊天指令（`/bili-share`、`/bili-status`、`/bili-test`、`/bili-search`、`/读视频`、`/赤石视频`、`/扫码登录`）及 `Command` 相关代码：平台侧会拦截 `/` 开头的消息，实测发不出去。

### 新增

- 读视频分 brief / full 两档：别人贴链接默认 `brief`（摘要，不调视觉模型）；消息 @ 了机器人或命中 `[read].full_trigger_keywords` 时自动 `full`（字幕时间轴、弹幕时间线、更多热评）。
- `full` 读会下载视频抽 4 帧交给视觉模型（`[read].full_vision_enabled`，还需 `[vision].enabled` 与可用的 `vlm` 任务），把画面描述一并写进上下文。
- 自动读的去重键按模式区分：同一视频先被 brief 读过，之后被 @ 仍会重新 full 读一次。
- 新增 `bili_search` 工具（原 `/bili-search`）：按关键词搜一条并分享，白名单与冷却照旧。
- 新增 `bili_shiwei` 工具（原 `/赤石视频`）：抓一条抽象整活视频，只发标题+链接。
- `[read]` 新增 `full_trigger_keywords`、`full_vision_enabled` 两个配置项。

### 变更

- 分享默认只发一条简短消息：`[share].intro_enabled`、`send_as_parts`、`narrative_enabled` 默认改为 `false`，想发长介绍再手动打开。
- `bili_read` / `bili_search` / `bili_shiwei` 都声明 `core_tool=True` + `visibility="visible"`，对 Planner 直接可见。
- 所有工具只对 `[[streams]]` 白名单会话生效，非白名单调用直接返回原因。

### 修复

- 重写读视频段落与配置表，补上 brief / full 的差异、触发条件与抽帧开关。
- `_check_login_on_start` 改用 nav 接口，不再依赖扫码模块。

## [1.1.0] - 2026-11-07

让 `bili_read` 能「详细看完」一个视频：以前只有摘要，很多细节会被截掉。

### 新增

- `bili_read` 增加 `detail` 参数：默认 `brief`（行为不变、省 token），传 `full` 进入深度读。
- 深度读的字幕按 90 秒分块并带 `[分:秒]` 时间轴，能看出某句话大概在第几分钟说的。
- 深度读的弹幕按分钟聚合成时间线（`[分:秒] N 条：代表弹幕`），条数密集处即高能段落。
- 深度读输出上限 9000 字、热评 15 条、弹幕 120 条、字幕上限 12000 字，分P 全列、简介放宽到 800 字。
- 深度读使用独立的缓存键（带上字幕上限），不会和摘要档互相污染。
- `bili_read` 工具描述里提醒模型：想看细节时传 `detail=full`。

### 修复

- 弹幕时间戳解析错误：`list.so` 的时间字段是「秒（浮点）」，以前按整数解析失败会退化成 0，导致所有弹幕挤在 00:00、弹幕时间线没有任何区分度。

### 说明

- 深度读不走配置里的 `comment_limit` / `danmaku_limit` / `max_chars`，档位内部固定；想调整请改常量 `DEEP_READ_*`。
- 匿名访问仍然没有 CC 字幕（B 站要求登录），此时深度读只能依靠弹幕时间线 + 热评 + 简介。
## [1.0.1] - 2026-11-07

修一个真机上才暴露的问题：服务器 IP 撞上 B 站风控时，读视频会直接失败。

### 修复

- `bili_read` 报「B 站接口返回 HTTP 412」时不再直接放弃：先向 `x/frontend/finger/spi` 申请 `buvid3`/`buvid4` 补进 Cookie 再重试一次，匿名访问的 412 大多就此恢复。
- 已有 `SESSDATA` 的配置不会被覆盖，只补缺失的 buvid 项。
- 仍然 412 时给出可操作的报错：提示去配置里填 Cookie，并说明这属于服务器 IP 被风控、需要换网络或代理。
- 请求头补上 `Accept-Language`，降低匿名请求被判为脚本的概率。
- buvid 只在真的撞上 412 时才申请一次，不会给每次调用增加额外请求。

## [1.0.0] - 2026-11-07

本项目的首个发布版本：功能对齐上游「麦麦刷B站」2.1.2，并改名为「沁の刷B站视频」，版本号重新从 1.0.0 起算。上游 2.x 的历史记录保留在 [CHANGELOG-upstream.md](CHANGELOG-upstream.md)。

### 定时刷 B 站

- 按 `[schedule]` 的间隔抓取热门 / 排行榜 / 搜索候选，交给 LLM 判断与当前会话兴趣是否相关，命中后生成分享卡片发到白名单会话（`[[streams]]`）。
- 兴趣有三条来源：手写 `interests`、从聊天记录自动提取 `auto_interests`、人设初始兴趣 `persona_seed_interests`。自动提取的词会写回配置，用户在设置页改过之后插件不再覆盖。
- 分享的是文本卡片 + 链接，不会把视频文件发到群里；可选抽帧 + 视觉模型识别画面（`[vision]`，依赖 `imageio-ffmpeg`）。

### 能读视频

- **`bili_read` 工具**：接受完整链接、`b23.tv` / `bili2233.cn` 短链、纯 BV 号、av 号，自动解析分P（`?p=2`）。直接打 `api.bilibili.com` 的结构化接口，返回标题 / UP 主 / 分区 / 时长 / 播放·弹幕·评论·点赞·投币·收藏 / 发布时间 / 分P 列表 / 标签 / 简介（≤300 字）/ CC 字幕节选 / 热评 / 弹幕采样。
- **`/读视频` 指令**（别名 `/bili-read`）：手动读一个视频并把摘要发回当前会话。
- **贴链接自动读**：白名单会话里有人贴 B 站链接时自动读一遍，通过 `maisaka.context.append` 写进会话上下文，麦麦下一条回复就能自然接上。同一视频在防重复窗口内只读一次，单条消息最多读 `auto_read_max_links` 个链接。
- **`[read]` 配置节**：`enabled`、`auto_read_enabled`、`auto_read_max_links`、`comment_limit`、`danmaku_limit`、`use_subtitle`、`max_chars`、`cache_seconds`。
- **`VideoCache`**：按「BV + 分P + 读取参数」缓存解析结果，`cache_seconds` 内重复读取不再打接口。

### 指令

- `/bili-share`（别名 `/b站分享`）：立即跑一轮分享；`/bili-status`：查看插件状态；`/bili-test`：发一条测试消息；`/bili-search`：手动搜候选；`/赤石视频`：随机发一个候选视频。
- `/扫码登录`（别名 `/bili-login`、`/扫码登陆`）：仅私聊可用，手机 B 站客户端扫码确认后自动把 Cookie 写进 `[account].cookie` 并立即生效，无需重启。

### 兼容与修复

- **插件 id 改为 `github.BsXinQin.bilibili_video_watch`**（原 `github.TommyAAKK.bilibili_interest_share`），插件名改为「沁の刷B站视频」，分发包顶层目录与压缩包名改用插件 id `github.BsXinQin.bilibili_video_watch`；作者信息改为 BsXinQin，`LICENSE` 追加 fork 版权行。
- **这是一次全新安装**：宿主按 id 识别插件，改了 id 就是另一条插件记录，旧版的配置与已刷名单不会自动继承。要沿用旧数据，把 `data/plugins/github.TommyAAKK.bilibili_interest_share/` 改名成 `data/plugins/github.BsXinQin.bilibili_video_watch/` 再安装。
- 兄弟模块导入改为「包内相对导入 + 顶层导入兜底」双路径，宿主按顶层模块加载时不会再报 `attempted relative import with no known parent package`。
- 覆写 `get_components()`，把 `Tool` 的 `chat_scope` 提到组件顶层（SDK 的 `collect_components` 只对 `ReplyExtension` 做这个提升）。
- `bili_read` 声明 `visibility="visible"` 且 `core_tool=True`，Planner 直接可见，不会把「读一个视频」派给子代理再每 30 秒等一次。
- 自动读视频加了 20 秒超时；`self.ctx` 还没注入时安静跳过，不影响其它 Hook。
- `_manifest.json` 与 `[plugin].config_version` 同步为 `1.0.0`。

### 已知限制

- **CC 字幕要登录**：未配 Cookie 时 B 站不返回字幕，其它内容照常。
- **热评/弹幕可能为空**：关闭评论区或接口风控（`-352`）时会静默降级为空列表。
- **自动读只对白名单会话生效**：私聊和没配 `[[streams]]` 的群不会触发。
