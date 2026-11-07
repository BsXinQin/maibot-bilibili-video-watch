# 更新日志

「沁の刷B站视频」插件的版本变更记录。

版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)，格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。
`_manifest.json` 的 `version` 与 `config.py` 里的 `config_version` 始终一致。

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
