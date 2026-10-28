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

**bili2text** 把 Bilibili 视频、本地视频或音频转成文字稿。

传入一个 Bilibili 链接、BV 号或本地文件。程序先读取可用的平台字幕；没有字幕时才下载视频、提取音频并运行语音识别。你可以在终端、浏览器或桌面窗口中使用同一套工作目录和结果库。

## 支持的转写引擎

| 引擎 | 类型 | 说明 |
| --- | --- | --- |
| **Whisper** | 本地模型 | OpenAI 开源的语音识别模型，离线运行，通用性强 |
| **SenseVoice** | 本地模型 | 阿里云开源本地语音识别模型，中文识别效果好 |
| **火山引擎** | 云端 API | 字节跳动旗下的商用语音识别服务，识别很准很推荐 |

## 从一条视频开始

需要 Python 3.10 至 3.12 和 [uv](https://docs.astral.sh/uv/)。

1. 下载项目并安装基础依赖。

```bash
git clone https://github.com/lanbinleo/bili2text.git
cd bili2text
uv sync
```

2. 安装你要使用的引擎或界面。常见组合如下。

| 目标 | 安装命令 |
| --- | --- |
| 本地 Whisper 转写 | `uv sync --extra whisper` |
| 浏览器界面 | `uv sync --extra web` |
| 桌面窗口，包含 Web 依赖 | `uv sync --extra window` |
| Whisper 和浏览器界面 | `uv sync --extra whisper --extra web` |

可选 extras 为 `whisper`、`sensevoice`、`volcengine`、`web`、`server` 和 `window`。

3. 转写一条视频。

```bash
uv run bili2text tx "https://www.bilibili.com/video/BV1kfDTBXEfu"
```

首次运行会打开配置向导。也可以单独运行向导：

```bash
uv run bili2text init
```

向导会保存语言、默认引擎和模型配置到 `.b2t/config.json`。

## 转写输入

对于 Bilibili 输入，程序优先保存平台字幕。平台字幕可用时，程序不下载视频、不提取音频，也不加载转写模型。平台字幕不可用时，程序下载或复用本地视频，然后运行所选转写引擎。本地音视频文件始终直接转写。

使用默认配置：

```bash
uv run bili2text tx "https://www.bilibili.com/video/BV1kfDTBXEfu"
```

转写本地文件：

```bash
uv run bili2text tx ./my-video.mp4
```

临时指定引擎和模型：

```bash
uv run bili2text tx "BV1kfDTBXEfu" --provider whisper --model medium
```

批量转写可直接传入输入，或使用每行一条输入的文本文件：

```bash
uv run bili2text batch "BV1kfDTBXEfu" "https://www.bilibili.com/video/BV1xx411c7XD"
uv run bili2text batch --file sources.txt
```

`batch` 在固定终端行更新每个输入的阶段、总体进度和已识别音频时长。它不会为每次进度更新增加一行。

## 恢复中断的批量转写

使用原输入清单和 `--resume` 恢复任务。该选项会去重输入，并且仅在文字稿、元数据、数据库版本、文件哈希和内容长度都匹配时跳过已完成项。任何对应关系不完整的输入都会重新转写，而不是相信旧任务状态。

```bash
rtk proxy .venv/bin/python -u -m b2t batch --file sources.txt --resume --workspace .b2t --provider whisper --model small --device auto --audio-language zh
```

先查看工作目录的一致性报告，再恢复任务：

```bash
uv run bili2text audit --workspace .b2t
```

`audit` 只读检查文字稿、metadata、SQLite 索引、版本和文件哈希。它不会创建数据库，也不会修改文件。

在另一个终端监看任务。监看只读取任务记录，退出不会停止转写：

```bash
uv run --no-sync python scripts/watch_tasks.py --file sources.txt
```

使用 RTK 时，以 `rtk proxy uv run` 开头保留实时输出。`rtk uv run` 会缓存输出。给监看命令加 `--once` 可获取一次性文本快照，不输出光标控制字符。按 Ctrl+C 会停止当前批量，但会保留已下载视频。

## 登录以获取受限字幕

部分字幕和下载需要 Bilibili 登录。在终端运行以下命令，并用 Bilibili App 扫描二维码：

```bash
uv run --no-sync bili2text login
```

登录命令把 Cookie 保存到 `.b2t/cookies.txt`，并验证登录状态。字幕获取和下载会自动读取该文件。用以下命令检查登录状态：

```bash
uv run --no-sync bili2text login --status
```

Cookie 是登录凭据。将它留在本机，不要提交到仓库或写入日志。设置 `B2T_COOKIE_FILE` 时，登录命令和下载器都使用该路径。

## 下载视频与断点续传

从历史任务补齐视频下载。命令按 BV 和分 P 去重：

```bash
uv run bili2text download --from-tasks
```

先预览计划：

```bash
uv run bili2text download --from-tasks --dry-run
```

也可以直接指定视频或输入文件：

```bash
uv run bili2text download BV1kfDTBXEfu BV1xx411c7XD
uv run bili2text download --file sources.txt --workspace .b2t
```

`download` 不加载转写模型。它跳过完整的 MP4，续传 `.part` 文件，并在下载后检查音视频轨道和时长。每个视频默认重试三次，可通过 `--attempts` 调整。单条失败不会停止其他视频，命令会以非零状态退出。按 Ctrl+C 后再次运行同一命令即可续传。

下载文件保存在 `.b2t/downloads/`。`download` 的缩写是 `dl`。

### 清理已下载的媒体

先预览会删除的文件数量和总大小：

```bash
uv run bili2text clean-downloads --workspace .b2t
```

确认后清空 `.b2t/downloads/` 的全部文件和子目录：

```bash
uv run bili2text clean-downloads --yes --workspace .b2t
```

命令保留下载目录本身，不删除文字稿、元数据、音频或数据库。有任务仍在运行或排队时，命令会拒绝清理。

### 从历史任务下载并转写

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

该模式先检查字幕。有字幕时只保存文字稿和元数据。没有字幕时，它复用完整视频或续传下载，然后转写。已有平台字幕稿会跳过。语音转写稿需要使用相同引擎、模型和语言才会跳过。文字稿保存在 `.b2t/transcripts/original/`，metadata 保存在 `.b2t/metadata/`。

## 配置 Whisper

Whisper 默认使用中文 `zh`，并使用中文提示词、简体转换和分段换行。配置位于 `.b2t/config.json` 的 `whisper` 节点：

```json
"whisper": {
  "audio_language": "zh",
  "device": "auto",
  "initial_prompt": "以下是普通话的句子。 ignore noise, white space, musical background sounds, and transcribe the part that speaks.",
  "simplified": true
}
```

`auto` 依次选择 CUDA、Mac MPS 和 CPU。CPU 与 MPS 使用 FP32。MPS 不可用时，自动模式会警告并回退到 CPU。`tx`、`batch` 和 `download --transcribe` 支持 `--device cpu`、`--device mps` 等临时覆盖。

网页和窗口中的 Whisper 任务依次执行，避免本地模型争用设备。模型加载和识别会显示独立阶段。进度中的 55% 表示音频提取完成，识别过程位于 55% 至 90%。

用 `--prompt` 覆盖默认提示词。设置 `simplified: false` 可保留原始繁简体。修改配置后，重启正在运行的网页或桌面窗口。

Whisper 会优先按句末标点换行，并合并跨时间段的未完句。没有句末标点时，它按时间段换行。metadata 保留每段的 `start`、`end` 和 `text`。

## 命令一览

| 命令 | 缩写 | 说明 |
| --- | --- | --- |
| `bili2text transcribe` | `tx` | 转写视频或音频 |
| `bili2text batch` | - | 批量转写多条输入 |
| `bili2text download` | `dl` | 下载视频、跳过完整文件并断点续传 |
| `bili2text clean-downloads` | - | 预览或清理下载目录，需用 `--yes` 确认 |
| `bili2text audit` | - | 只读核验工作目录的文字稿、metadata 和数据库索引 |
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

## 使用 Web 或桌面窗口

`web` 和 `window` 共用页面、配置、任务服务和结果库。两者都支持批量转写、本地文件选择、任务日志、文字稿编辑、版本切换、分类、标签和文本导出。

安装桌面窗口时，`window` 已包含 Web 依赖：

```bash
uv sync --extra whisper --extra window
```

在浏览器中打开界面：

```bash
uv run bili2text ui
```

在桌面窗口中打开相同界面：

```bash
uv run bili2text window
```

浏览器模式默认监听 `127.0.0.1:8000`。按 Ctrl+C 停止服务。桌面窗口关闭后停止其内部服务。两个入口默认使用当前目录的 `.b2t`。使用 `--workspace /path/to/workspace` 切换工作目录。

选择本地文件时，页面会把文件复制到 `.b2t/downloads/imports/`。直接输入本机路径可避免复制。

如需在局域网使用服务模式：

```bash
uv run bili2text srv --host 0.0.0.0 --port 8000
```


## 开发

- [开发文档](docs/DEVELOPMENT.md)
- [更新日志](CHANGELOG.md)

## 许可证

MIT License

## 使用须知

使用本工具时，请遵守你所在地区的版权法律与平台规则。确保你有权下载和转写相关视频内容。

开发者不对任何非法使用行为负责。
