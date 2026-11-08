# 选曲与声音验收

先确定本片音乐承担什么任务：对话感、思考、悬念、行动或情绪转折；结合乐器、速度、能量、是否有人声及片长搜索。不要仅因主题含“AI”就反复选择科技氛围曲，也不要为了去重随机使用不合适的音乐。需要音乐时比较至少三个候选的匹配项和缺失项，再选一首；用户已经指定曲目时直接检查其可用性。

## 免费媒体库与近期记录

```bash
python skills/shared/scripts/openfilm.py get -p <主题> music-history
python skills/shared/scripts/openfilm.py get -p <主题> music-search --prompt "温暖、轻打击乐、对话感、无人声" --sec 35 --limit 5
python skills/shared/scripts/openfilm.py get -p <主题> music-search --take <候选ID>
```

搜索保留 OpenFilm 的相关度排序，同时显示 `outputs/` 下最近 10 个其他项目的音乐。项目以 `film.html` 修改时间排序；只统计时间线实际引用的音乐，不把素材目录里未使用的文件算作已用。当前项目的修改不算一次新的跨项目复用。这个窗口是本机项目记录，不代表平台发布历史或同一账号的完整历史。

取曲时检查 ID；导出前后比较曲目身份和文件 SHA-256，并拦截同一首音乐在相同起点的重复叠加。导出后的检查失败时，文件不视为验收通过，修复时间线后重新导出。裁剪、改名、归一化和淡化后的素材应保留原曲身份，不能通过换文件名假装换曲。固定片头、用户指定主题曲或系列音乐动机可以复用，并在命令中填写原因：

```bash
python skills/shared/scripts/openfilm.py get -p <主题> music-search --take <ID> --allow-music-reuse "用户指定的系列主题曲"
python skills/shared/scripts/openfilm.py render -p <主题> --allow-music-reuse "用户指定的系列主题曲"
```

这是执行者记录已明确的创作意图，不要求每次向用户另行审批。默认重复会返回退出码 2，取曲或导出尚未执行。直接使用 Studio、原生 CLI 或其他合成器不会经过此封装的自动拦截，交付前仍应检查近期记录。

## 来源与处理记录

音乐轨使用 `<audio data-role="music" ...>`。旁白使用 `data-role="voice"` 或 `narration`，音效使用 `data-role="sfx"`。老项目中媒体库曲名、`bgm`、`music`、`bed` 路径会自动识别；任意命名的自备音乐和嵌在镜头页面里的音乐要明确标记或放到主时间线，不能把零条已识别音乐当作完成验收。

每次选曲在项目根保存 `music-selection.json`。机器读取以下四项，其余保留为便于审阅的创作记录：

```json
{
  "file": "assets/music-prepared.mp3",
  "source_id": "openfilm/audio/music/具体曲目.mp3",
  "source_sha256": "原始音频文件的64位SHA-256",
  "prepared_sha256": "时间线所用音频文件的64位SHA-256",
  "source": "OpenFilm library",
  "license": "CC0（按本次素材来源核实）",
  "query": "本片的音乐需求",
  "candidates": ["候选一及取舍", "候选二及取舍", "候选三及取舍"],
  "reason": "所选乐器、速度和情绪如何服务本片",
  "duration_seconds": 35,
  "processing": "裁剪、响度、淡入淡出与时间线音量"
}
```

未加工时两个摘要相同。SHA-256 使用 `artifact_checkpoints.file_digest` 计算。加工后更新文件摘要；旧摘要与实际文件不符时检查会失败。远程 URL 只比较 URL 并提示无法核验内容；优先将音乐合法保存到本项目后检查。文件摘要不是声学指纹，未保留来源的重新编码副本仍需人工识别。

## 时长、响度与交付

- 核对原曲长度与画面时长。曲目不足时，选择更长的候选或在乐句边界平滑循环，不让音乐提前结束。裁剪不能顺带延长画面或改变已有配音速度。
- 根据本片是否有口播决定混音强度。无口播的音乐文字片可以更清楚；有口播时以人声可懂为先，必要时闪避。不要机械地把所有曲目设为同一 `volume`。
- 以 FFmpeg 测量响度和 true peak，依据源曲处理；避免削波。首尾使用短淡入淡出并检查实际文件，不只在脚本里声称已经淡化。
- 运行 `get music-check`；实际播放开头、转折、结尾，检查加载、完整覆盖、断音、突切和口播遮盖。记录客观检测与听感判断各自的验证方式。
- 已有 MP4 换曲时重新导出并检查音轨；仅预览的项目更新可编辑时间线。交付时说明本次曲目、选择理由和导出状态。
