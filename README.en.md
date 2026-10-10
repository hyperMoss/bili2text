<p align="center">
  <img src="assets/light_logo2.png" alt="bili2text logo" width="360" />
</p>

<p align="center">
  <a href="README.md">简体中文</a>
  ·
  <a href="CHANGELOG.en.md">Changelog</a>
</p>

<p align="center">
  <img src="https://img.shields.io/github/stars/lanbinleo/bili2text" alt="GitHub stars" />
  <img src="https://img.shields.io/github/license/lanbinleo/bili2text" alt="License" />
  <img src="https://img.shields.io/github/v/release/lanbinleo/bili2text" alt="Release" />
</p>

# bili2text

**bili2text** is a command-line tool that turns Bilibili videos into text.

Give it a URL or BV id, and it'll download the video, extract the audio, run speech recognition, and hand you a transcript. It supports multiple transcription engines — run everything locally and offline, or connect to a cloud service.

There's also a simple web UI and a desktop window for anyone who'd rather not use the terminal.

![Screenshot](assets/new_v_sc.png)

## Transcription Engines

| Engine | Type | Notes |
| --- | --- | --- |
| **Whisper** | Local model | OpenAI's open-source speech recognition model. Runs offline, general-purpose |
| **SenseVoice** | Local model | ONNX-based local model with strong Chinese recognition |
| **Volcengine** | Cloud API | ByteDance's commercial ASR service, good for batch or service-oriented workloads |

## Quick Start

### Install

Requires Python 3.10–3.12 and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/lanbinleo/bili2text.git
cd bili2text
uv sync
```

This only installs core dependencies. Transcription engines and extra features are installed via extras — for example, to use Whisper and the web UI:

```bash
uv sync --extra whisper --extra web
```

Available extras: `whisper`, `sensevoice`, `volcengine`, `web`, `server`.

### Set Up

A setup wizard runs automatically the first time, or you can launch it manually:

```bash
uv run bili2text init
```

The wizard walks you through language, engine, and feature selection, then tells you what install command to run.

### Transcribe

```bash
uv run bili2text tx "https://www.bilibili.com/video/BV1kfDTBXEfu"
```

Local files work too:

```bash
uv run bili2text tx ./my-video.mp4
```

Specify an engine and model:

```bash
uv run bili2text tx "BV1kfDTBXEfu" --provider whisper --model medium
```

Submit multiple inputs in one batch:

```bash
uv run bili2text batch "BV1kfDTBXEfu" "https://www.bilibili.com/video/BV1xx411c7XD"
```

Or put one BV, URL, or local file path per line:

```bash
uv run bili2text batch --file sources.txt
```

### Download and Resume Videos

Resume video downloads from historical tasks, deduplicated by BV and part:

```bash
uv run bili2text download --from-tasks
uv run bili2text download --from-tasks --dry-run
```

Or supply inputs directly:

```bash
uv run bili2text download BV1kfDTBXEfu BV1xx411c7XD
uv run bili2text download --file sources.txt --workspace .b2t
```

This command downloads videos without loading a transcription model. It skips readable
MP4 files with both audio and video tracks, resumes `.part` files, and validates
new downloads against their expected duration. Downloads run sequentially, with
up to three attempts per video (`--attempts` adjusts this). Failed downloads do not
stop the remaining videos; the command exits with a nonzero status if any fail.
Ctrl+C preserves partial files; run the same command again to resume.
Output defaults to `.b2t/downloads/`. The alias is `dl`.

If you installed a temporary yt-dlp patch inside `.venv`, use
`uv run --no-sync bili2text download --from-tasks` to keep the current environment.
Recreating the environment replaces such patches.

### Subtitles First, ASR When Unavailable

Bilibili inputs in `tx`, `batch`, and `download --transcribe` first check platform
subtitles. Accessible captions are converted to readable `.txt` transcripts with
task metadata, without saving SRT files, downloading video, extracting audio, or initializing an ASR model.
Chinese captions are preferred, including automatic captions; danmaku is excluded.
Only a confirmed absence of captions falls back to downloading and ASR.
Login-required captions, network errors, and invalid subtitle data stop that task
without downloading video. Configure cookies or restore connectivity, then rerun.
Login-only captions need cookies via `B2T_COOKIE_FILE` or workspace `cookies.txt`.

```bash
uv run --no-sync bili2text download --from-tasks --transcribe
uv run --no-sync bili2text download --from-tasks --transcribe --provider whisper --model small
uv run --no-sync bili2text download --from-tasks --transcribe --audio-language zh
uv run --no-sync bili2text download --from-tasks --transcribe --dry-run
```

When a video has no subtitles, complete videos are reused locally. Each successful
download is transcribed before the next video, reusing one lazily initialized model.
Saved platform subtitle transcripts are skipped regardless of the ASR model.
ASR transcripts are skipped when their engine and model match.
Failed videos do not block the remaining inputs.
Ctrl+C preserves downloaded media and cancels the current task. Running again
skips completed transcripts; interrupted transcription restarts from the beginning.
Transcripts are saved in `.b2t/transcripts/original/`, with metadata in `.b2t/metadata/`.

Whisper defaults to configured Chinese (`zh`) to avoid detection errors based on
the first 30 seconds. `--audio-language` also works with `tx` and `batch`; pass
`auto` to detect or `en` to select another language. Saved ASR transcripts must
match the selected language to be skipped; platform subtitles ignore ASR language options.
Progress shows processed audio time and total
duration. Transcription occupies 55%–90% of overall progress.

### Whisper line breaks

Normal transcription now uses the Chinese style prompt, simplified conversion
and line breaks by default. Settings are under `whisper` in `.b2t/config.json`:

```json
"whisper": {
  "audio_language": "zh",
  "device": "auto",
  "initial_prompt": "以下是普通话的句子。 ignore noise, white space, musical background sounds, and transcribe the part that speaks.",
  "simplified": true
}
```

`auto` chooses CUDA when available, otherwise CPU. `tx`, `batch` and
`download --transcribe` accept `--device cpu` or `--device mps` overrides;
MPS requires explicit selection. `--prompt` overrides the configured prompt.
Set `simplified` to false to retain the recognized script. Selecting a different
language disables the default Chinese prompt. Restart running web/window
processes after changing configuration.

Whisper output uses one line per sentence when sentence-ending punctuation is
available, joining sentences that span timing chunks. Without punctuation, it
uses Whisper chunk boundaries; unfinished spans longer than 30 seconds also
fall back to those boundaries. Metadata preserves each chunk's start, end and text.
Use `--prompt "以下是普通话的句子。 ignore noise, white space, musical background sounds, and transcribe the part that speaks."` to encourage simplified Chinese and punctuation.

## Commands

Run `uv run --no-sync bili2text login` to display a QR code in the terminal.
Scan and confirm with the Bilibili app. After checking the authenticated session,
the command saves a Netscape cookie file to `.b2t/cookies.txt` with owner-only
read/write permissions. Subtitles and downloads use this file automatically.
`login --status` checks the saved session without generating a QR code.
`--workspace` selects the workspace; `B2T_COOKIE_FILE` overrides the save/load path.
The QR wait defaults to 180 seconds and can be shortened with `--timeout`.
Cancellation and failed login leave the previous cookie file intact.
Previously failed tasks must be resubmitted; new requests read the saved cookies.

If an existing environment contains a temporary yt-dlp patch, install just the new
QR dependency with `uv pip install --python .venv/bin/python 'qrcode>=8.0'` and keep
using `uv run --no-sync`. Login does not require an ASR engine or start media jobs.
Keep cookie files local; they contain login credentials.

| Command | Alias | What it does |
| --- | --- | --- |
| `bili2text transcribe` | `tx` | Transcribe a video or audio file |
| `bili2text batch` | - | Batch transcribe multiple inputs |
| `bili2text download` | `dl` | Download, skip complete files, and resume partial files |
| `bili2text login` | - | Scan to log into Bilibili; `--status` checks login |
| `bili2text bootstrap` | `init` | Run the setup wizard |
| `bili2text web` | `ui` | Start the web UI |
| `bili2text server` | `srv` | Start server mode |
| `bili2text window` | `win` | Start the desktop window |
| `bili2text doctor` | `diag` | Check runtime dependencies |
| `bili2text language` | `lang` | Switch the interface language |

```bash
uv run bili2text --help
```

## Web UI & Server Mode

Start the web interface (opens in your browser):

```bash
uv run bili2text ui
```

Run in server mode (good for Docker or LAN deployment):

```bash
uv run bili2text srv --host 0.0.0.0 --port 8000
```

## Development

- [Development Guide](docs/DEVELOPMENT.en.md)
- [Changelog](CHANGELOG.en.md)

## License

MIT License

## Notice

Please respect the copyright laws and platform rules in your region before downloading or processing any content.
