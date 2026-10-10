<p align="center">
  <img src="assets/light_logo2.png" alt="bili2text logo" width="360" />
</p>

<p align="center">
  <a href="README.en.md">English</a>
  ·
  <a href="CHANGELOG.md">更新日志</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/bilibili-视频转文字-fb7299?style=flat&logo=bilibili&logoColor=white" />
  <img src="https://img.shields.io/github/stars/lanbinleo/bili2text?style=flat&logo=github&color=yellow" alt="Stars" />
  <img src="https://img.shields.io/github/forks/lanbinleo/bili2text?style=flat&logo=github&color=blue" alt="Forks" />
  <img src="https://img.shields.io/github/license/lanbinleo/bili2text?style=flat&color=green" alt="License" />
  <img src="https://img.shields.io/github/v/release/lanbinleo/bili2text?style=flat&color=orange" alt="Release" />
  <img src="https://img.shields.io/github/last-commit/lanbinleo/bili2text?style=flat&color=purple" alt="Last Commit" />
</p>

# bili2text

**bili2text** 是一个把 Bilibili 视频转成文字的命令行工具。

贴一个 Bilibili 链接或 BV 号进去，它会自动下载视频、提取音频、跑语音识别，最后输出一份文字稿。支持多种转写引擎，可以在本地离线跑，也可以接云端服务。

除了命令行，还附带了简单的 Web 界面和桌面窗口，方便不习惯终端的用户使用。

![截图](assets/new_v_sc.png)

*PS：这个是老的界面截图*

## 支持的转写引擎

| 引擎 | 类型 | 说明 |
| --- | --- | --- |
| **Whisper** | 本地模型 | OpenAI 开源的语音识别模型，离线运行，通用性强 |
| **SenseVoice** | 本地模型 | 阿里云开源本地语音识别模型，中文识别效果好 |
| **火山引擎** | 云端 API | 字节跳动旗下的商用语音识别服务，识别很准很推荐 |

## 快速开始

### 安装

需要 Python 3.10–3.12 和 [uv](https://docs.astral.sh/uv/)。

`uv` 是一个现代化的 Python 包管理工具，速速扔掉你手中的 Conda、Anaconda、venv和pip吧！

```bash
git clone https://github.com/lanbinleo/bili2text.git
cd bili2text
uv sync
```

这只会安装核心依赖。转写引擎和额外功能需要通过 extras 安装，比如要用 Whisper 和 Web 界面：

```bash
uv sync --extra whisper --extra web
```

可选的 extras：`whisper`、`sensevoice`、`volcengine`、`web`、`server`。可以暂时不用安装，详看下方的初始化文档。

### 初始化配置

第一次运行时会自动弹出配置向导，也可以手动运行：

```bash
uv run bili2text init
```

向导会引导你选择语言、转写引擎和额外功能，最后告诉你需要运行什么安装命令。

### 转写视频

默认先检查 B 站平台字幕：有可访问字幕时提取文字，仅保存 `.txt` 文字稿和任务元数据，
不下载视频、不提取音频、不加载转写模型。优先使用中文字幕，自动字幕也可使用，弹幕不算字幕。
仅在确认无字幕时下载视频并转写；字幕需要登录、网络失败或字幕解析失败时停止该条任务，不下载视频。
本地文件仍直接转写。
部分字幕需要登录，可通过 `B2T_COOKIE_FILE` 或工作目录中的 `cookies.txt` 提供登录 Cookie。

### 扫码登录 B 站

在终端运行，用哔哩哔哩 App 扫描二维码并在手机上确认：

```bash
uv run --no-sync bili2text login
```

登录后会验证登录状态，再将 Cookie 保存为 `.b2t/cookies.txt`，文件权限为仅当前用户可读写。
字幕获取和视频下载会自动使用该文件，无需浏览器导出 Cookie。
命令不需要安装转写引擎，也不会启动下载或转写任务。

检查登录状态，或指定工作目录：

```bash
uv run --no-sync bili2text login --status
uv run --no-sync bili2text login --workspace .b2t
```

设置 `B2T_COOKIE_FILE` 时，登录命令会保存到该变量指定的路径，与下载器读取路径一致。
二维码最长等待 180 秒，可用 `--timeout` 缩短；过期后重新运行登录命令。
取消登录、接口失败或登录验证不通过时不会覆盖旧 Cookie。
已有 Web 服务和批次中失败的任务需要重新提交，之后的新请求会读取保存的 Cookie。

如果当前环境尚未安装二维码依赖，又需要保留 `.venv` 中的 yt-dlp 临时补丁，可只安装二维码依赖：

```bash
uv pip install --python .venv/bin/python 'qrcode>=8.0'
```

Cookie 含登录凭据，请留在本机，不要提交或贴入日志。

### 获取文字稿

```bash
uv run bili2text tx "https://www.bilibili.com/video/BV1kfDTBXEfu"
```

也可以传本地文件：

```bash
uv run bili2text tx ./my-video.mp4
```

指定引擎和模型：

```bash
uv run bili2text tx "BV1kfDTBXEfu" --provider whisper --model medium
```

批量提交多条输入：

```bash
uv run bili2text batch "BV1kfDTBXEfu" "https://www.bilibili.com/video/BV1xx411c7XD"
```

也可以用文本文件，每行一个 BV、链接或本地文件路径：

```bash
uv run bili2text batch --file sources.txt
```

### 仅下载视频与断点续传

从已有任务记录中补齐视频下载，自动按 BV 和分 P 去重：

```bash
uv run bili2text download --from-tasks
```

先预览哪些文件会跳过、哪些需要下载：

```bash
uv run bili2text download --from-tasks --dry-run
```

也可以指定视频或输入文件：

```bash
uv run bili2text download BV1kfDTBXEfu BV1xx411c7XD
uv run bili2text download --file sources.txt --workspace .b2t
```

此命令只下载视频，不加载转写模型。完整且包含音视频轨道的 MP4 会跳过，
未完成的 `.part` 文件会续传；下载后检查音视频轨道和时长。
遇到连接中断会自动重试（每个视频默认最多 3 次，可用 `--attempts` 调整），
单条失败后继续处理其他视频，结束时返回非零退出码。
按 Ctrl+C 停止会保留部分文件，再次运行同一命令即可继续。
产物位于工作目录的 `downloads/`，默认是 `.b2t/downloads/`。

`download` 可缩写为 `dl`。如果本地对 `.venv` 中的 yt-dlp 安装过临时补丁，
可以用 `uv run --no-sync bili2text download --from-tasks` 保持当前依赖环境；重建环境会覆盖此类补丁。

### 优先字幕，没有字幕再下载转写

从历史任务逐条获取字幕，确认无字幕时续传下载并转写：

```bash
uv run --no-sync bili2text download --from-tasks --transcribe
```

可指定模型或先预览清单：

```bash
uv run --no-sync bili2text download --from-tasks --transcribe --provider whisper --model small
uv run --no-sync bili2text download --from-tasks --transcribe --audio-language zh
uv run --no-sync bili2text download --from-tasks --transcribe --dry-run
```

该模式在下载前检查字幕。有字幕时只保存字幕和文字稿，完整视频和音频也无需处理。
确认无字幕时复用本地完整视频或续传下载，然后转写；失败的条目不会阻塞后续视频。
字幕接口要求登录或获取失败时会明确报错，保留任务记录，配置 Cookie 或恢复网络后可重跑。
已有平台字幕稿会跳过；语音转写稿按同一引擎、同一模型判断是否跳过。
转写逐条执行，同一批复用一个模型实例，仅在需要语音转写时初始化模型。
Ctrl+C 会保留视频并将当前任务标记为取消；再次执行会跳过完成的文字稿，未完成的转写从头开始。
文字稿位于 `.b2t/transcripts/original/`，元数据位于 `.b2t/metadata/`。

Whisper 默认使用配置中的中文 `zh`，避免根据开头 30 秒误判语音语言。
`--audio-language` 也支持 `tx` 和 `batch`；传入 `auto` 自动检测，或 `en` 等代码指定其他语言。
指定语言后，语音转写稿需匹配该语言才会跳过；平台字幕稿不受语音模型和语言参数影响。
转写进度会显示已处理音频时长与总时长；总进度 55% 是音频提取结束，
识别语音占后面的 55%–90%，因此初期总百分比增长较慢。

### Whisper 分段换行

正常转写流程默认使用中文提示词、简体转换和分段换行，无需再运行修复脚本。
当前配置保存在 `.b2t/config.json` 的 `whisper` 节点中：

```json
"whisper": {
  "audio_language": "zh",
  "device": "auto",
  "initial_prompt": "以下是普通话的句子。",
  "simplified": true
}
```

`auto` 使用可用的 CUDA，否则使用 CPU。`tx`、`batch`、`download --transcribe`
支持 `--device cpu`、`--device mps` 等临时覆盖；MPS 需显式选择。
`--prompt` 覆盖默认提示词；`simplified: false` 可保留识别时的繁简体。
设置其他语音语言时，默认中文提示词不会自动套用。
修改配置后重启已运行的网页或窗口进程。

Whisper 文字稿优先按句末标点一行一句，跨时间段的未完句会合并。
无句末标点时按时间段换行；长达 30 秒仍未见句末的片段也按时间边界拆开。
时间段不一定是完整的语义句子；元数据会保留每段的 `start`、`end` 和 `text`。
中文转写可传入 `--prompt "以下是普通话的句子。"` 引导简体和标点风格。

修复旧文字稿（保留原件，另存 `.分段.txt` 和包含时间轴的 `.分段.json`）：

```bash
uv run --no-sync python scripts/repair_transcripts.py "旧文字稿.txt" --retranscribe
```

脚本优先使用已保存的时间段。没有时间轴时，`--retranscribe` 使用元数据中记录的
本地音频重新识别，不下载视频；默认中文、原任务模型及提示词“以下是普通话的句子。”。
重新识别的文字可能与旧稿不同。去掉 `--retranscribe` 时缺少时间轴会直接报错。
输出已存在时停止，避免覆盖结果。
可添加 `--device mps` 显式使用 Apple GPU，或 `--device cpu` 使用 CPU。
MPS 需要可用的 PyTorch Metal 后端；不同硬件和模型的速度应实际对比。
提示词不能保证全篇都是简体。需要清除残余繁体时，先安装
`uv pip install --python .venv/bin/python -r scripts/requirements-transcript-repair.txt`，
再为修复脚本添加 `--simplified`；识别原文和时间轴仍保存在 JSON 中。

## 命令一览

| 命令 | 缩写 | 说明 |
| --- | --- | --- |
| `bili2text transcribe` | `tx` | 转写视频或音频 |
| `bili2text batch` | - | 批量转写多条输入 |
| `bili2text download` | `dl` | 下载视频、跳过完整文件并断点续传 |
| `bili2text login` | - | 扫码登录 B 站，`--status` 检查登录状态 |
| `bili2text bootstrap` | `init` | 配置向导 |
| `bili2text web` | `ui` | 启动 Web 界面 |
| `bili2text server` | `srv` | 启动服务模式 |
| `bili2text window` | `win` | 启动桌面窗口 |
| `bili2text doctor` | `diag` | 检查运行环境 |
| `bili2text language` | `lang` | 切换界面语言 |

```bash
uv run bili2text --help
```

## Web 界面 & 服务模式

启动 Web 界面（浏览器访问）：

```bash
uv run bili2text ui
```

以服务模式运行（适合 Docker 或局域网部署）：

```bash
uv run bili2text srv --host 0.0.0.0 --port 8000
```

*注意，项目暂时未对Docker或服务器类型的长时间运行做任何优化，请暂时使用本地端*

## 开发

- [开发文档](docs/DEVELOPMENT.md)
- [更新日志](CHANGELOG.md)

## 许可证

MIT License

## 使用须知

使用本工具时，请遵守你所在地区的版权法律与平台规则。确保你有权下载和转写相关视频内容。

开发者不对任何非法使用行为负责。
