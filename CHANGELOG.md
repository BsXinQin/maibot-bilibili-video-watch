# 更新日志

「沁の刷B站视频」插件的版本变更记录。

版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)，格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。
`_manifest.json` 的 `version` 与 `config.py` 里的 `config_version` 始终一致。

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
