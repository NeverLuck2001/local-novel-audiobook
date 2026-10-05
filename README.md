# Local Novel Audiobook Studio

[简体中文](README.zh-CN.md) · [Architecture](docs/architecture.md) · [Performance](docs/performance.md)

Turn Chinese TXT and EPUB novels into audiobooks on your own computer. A local browser studio controls official Qwen3-TTS, keeps resumable segment caches, checks speech with optional local Qwen3-ASR, and exports chapter audio and an M4B audiobook.

Model installation requires a download. Conversion uses local model directories with offline inference settings; novel text is not sent to a hosted TTS service.

## Features

- TXT encoding detection and EPUB spine order, metadata, and cover extraction.
- Browser file selection and upload, chapter preview and selection, visible cleanup audit.
- Nine official CustomVoice presets, free-form delivery instructions, short voice previews.
- Base voice cloning from 1–5 audio/video clips, automatic local video audio extraction, optional local Chinese transcription, reference playback, and a reusable local voice library.
- Light/dark/system themes, remembered in the browser without changing generation settings.
- Independent output speed (0.75–1.5×), pitch (±3 semitones), pauses, and loudness controls.
- Explicit pronunciation replacements: retain source text and check ASR against the spoken text.
- Automatic recognized Shaft/Pixiv metadata cleanup; optional URL, chapter-prefix front matter, and user-specified line-pattern removal.
- One GPU queue with durable order/positions, run-next priority, safe pause/cancellation, resume, and failed-segment retries.
- Cancel all waiting jobs without stopping the active job; hide and restore stopped records without deleting audio.
- Progress, generated duration, measured generation time, RTF, approximate ETA, and logs.
- Read-only monitoring of a conversion started outside the studio; queued jobs wait for it.
- Official batched inference, SHA256-checked segment caches, retained QC and failure reports.
- FLAC/WAV/MP3 chapters and complete M4B exports with chapter markers and EPUB cover.
- Multi-task ZIP downloads and optional automatic collection when the queue is idle; download preferences are independent of synthesis settings.

Version 0.4.0 uses scrollable, searchable lists for books, jobs and saved voices, with select-all, bulk deletion and restoration. The task list sits beside its progress detail on desktop and stacks on narrower screens; long chapters, reference clips and exports also scroll within bounded panels. Book deletion is now stored on the server and survives reloads. **Show deleted** exposes recoverable records; original books, recordings, caches and exports are retained. Deleting a queued job cancels it; deleting a running studio job requests cooperative cancellation and removes its record after the worker stops. External conversions remain read-only until they stop. Download search does not restrict automatic collection.

**Automatically clean Shaft / Pixiv headers** is enabled by default (`text.strip_downloader_metadata`). It recognizes standalone Shaft start/end markers and contiguous metadata fields, or an initial title/author block with a Pixiv source URL. It works for short stories without chapter headings, keeps title/author as TXT book metadata and shows removed material in the cleanup audit. Normal prose and ordinary author/description lines remain intact; ambiguous multiline synopsis prose is retained for review. URL removal also understands Markdown links, preserving meaningful link labels. Toggle this option off to retain recognized headers, and inspect the reading preview before generating. Existing source files and task snapshots are not rewritten.

Version 0.2.3 adds a **Mature narrator / slightly faster** shortcut and makes it the default for new studio jobs: Vivian, a composed/confident delivery instruction, 1.15x output rate, and -1 semitone. The first browser migration applies only these voice/output defaults; other saved controls remain intact. Existing jobs keep their snapshots. CustomVoice style control is an approximation, not an arbitrary new voice design; listen to a short preview before a long book. Disable pitch adjustment if FFmpeg lacks `rubberband`.

Stopped studio and legacy CLI records have **Delete record / Restore record** controls. Removal keeps original books, cached speech, outputs and logs. Enable **Show deleted records** to restore them. A running external conversion prevents removal of its display record. An already loaded older studio server can use browser-local recoverable records, which sync to the new API after restart. See the [handoff](docs/handoff.md) for validation and limits.

Version 0.3.0 adds a complete local reference preparation and voice library workflow plus dark mode. Cloning uses separately downloaded **Qwen3-TTS-12Hz-1.7B-Base** weights and an exact reference transcript. VoiceDesign is a separate model; installing CustomVoice does not enable it. Automatic character casting and LLM rewriting are not implemented.

## Voice cloning and themes

Install the official Base model once, then restart the studio backend to enable the new reference endpoints:

```powershell
.\.venv\Scripts\python.exe scripts/download_models.py clone --provider modelscope
python ui.py
```

1. Select **Voice cloning** and add 1–5 recordings of the same speaker (WAV, MP3, FLAC, M4A or OGG), or videos (MP4, M4V, MOV, MKV, WebM or AVI). Videos automatically become playable 24 kHz mono WAV references using their first audio track; originals are retained locally. Audio uploads are limited to 64 MB, videos to 512 MB, and each source to 10 minutes. Videos without audio or with unreadable media are rejected with a helpful message. Start with a clean 5–15 second sample; more recordings do not automatically improve similarity. Avoid music, overlapping speakers and strong room echo. Selected reference audio, including joining pauses, is limited to 60 seconds.
2. Optionally select start/end times in seconds. An empty end means the end of the file. Enter exactly what was spoken in each selected range, or leave it empty for local **Chinese** recognition. Supply transcripts manually for other languages. Choose whole words and sentences when trimming.
3. Click **Prepare recordings and recognize text**. This is a persistent queue job, serialized with audiobook generation, with pause/cancel/resume controls. Preparation converts to 24 kHz mono WAV, trims only edge silence, adjusts gain conservatively and joins clips in order. It retains original recordings and reports quiet, clipping, silence and length warnings; it does not detect speaker identity, remove music or guarantee noise reduction.
4. Play the prepared reference and correct its combined transcript, especially names and numbers. Save a named voice to reuse it later, then generate a short preview of **new** text before a full book. Saved voices contain local audio and text; no fine-tuning or cloud upload occurs. Removing a voice from the library keeps recordings and existing jobs.

Base uses the recording's voice and delivery rather than CustomVoice instructions. Output speed/pitch still apply; use 1.0x and zero pitch when evaluating similarity. The official model's transcript-free speaker-embedding mode is deliberately not exposed because its quality may be lower. Multiple same-speaker clips become one ordered reference; they are not separate training examples. Official prompt features are computed once and reused across batches within a conversion. See the [official clone API](https://github.com/QwenLM/Qwen3-TTS#voice-clone).

Use the **Theme** selector in the header for light, dark or system appearance. The browser remembers the choice and follows operating-system changes in system mode. Reference uploads, voice profiles, generated audio and downloaded weights stay excluded from Git. An already running older backend needs a restart for reference preparation/library APIs; loading new static files alone is insufficient. Existing conversion children and immutable job snapshots are retained.

## Batch downloads and automatic collection

Version 0.3.1 adds **Downloads and automatic collection** below the task progress panel. Audio is always saved in `output/ui/`; browser downloading is an additional copy.

- Choose FLAC (the default), M4B, MP3 or WAV, select completed tasks and click **Download ZIP**. Each bundle contains the chosen format under separate task/book folders, preserving chapter filenames and audio quality. It includes existing exports only; this selector does not synthesize or convert a missing format. Previews can be selected manually. Removed records appear only when **Show deleted records** is enabled.
- **Automatically download when the queue is idle** is off by default. Enabling it collects novel tasks completed after activation, combining eligible tasks in one ZIP once no studio task is running or queued. Changing the format or re-enabling the switch starts a new collection window; older results remain available for manual selection. Previews and reference preparation do not trigger automatic downloads.
- Keep the studio page open. If it is closed while enabled, generation continues and the next page visit collects eligible completed tasks that have not been submitted. Browser-local preferences and submission records prevent normal refreshes from repeating requests; browsers supporting Web Locks also coordinate multiple tabs. Download acceptance/completion is controlled by the browser, so confirm the browser download list and allow site downloads if prompted. A blocked/interrupted download can be retried manually. Save location comes from browser settings.
- ZIP output streams the original files without staging a large archive on the server or loading the entire download into JavaScript memory. Each request accepts at most 100 completed jobs and 20,000 audio files. Existing per-file links remain available. Downloading never changes job snapshots, generated speech, or queue order.

Restart an older backend to load the batch-download endpoints, then reload the page. Updating static files alone does not upgrade the running Python server.

## Installation on Windows

Use Python 3.12, an NVIDIA CUDA GPU, and FFmpeg/FFprobe on PATH. FFmpeg's `rubberband` filter enables independent pitch adjustment; rate alone can fall back to `atempo`. The verified environment used an RTX 5090 D 32 GB with PyTorch 2.8.0+cu128, BF16, and SDPA. Other hardware has not been certified.

```powershell
git clone https://github.com/NeverLuck2001/local-novel-audiobook.git
cd local-novel-audiobook
pwsh -File scripts/setup.ps1 -DownloadModels
```

This creates `.venv` for TTS/UI and `.venv-asr` for ASR. The separate environments are intentional: `qwen-tts==0.1.1` requires Transformers 4.57.3, while `qwen-asr==0.0.6` requires 4.57.6. Do not install ASR into the TTS environment. The download script uses official repositories and saves model identity manifests.

To install without downloading immediately, omit `-DownloadModels`, then run:

```powershell
.\.venv\Scripts\python.exe scripts/download_models.py preset asr --provider modelscope
.\.venv\Scripts\python.exe audiobook.py --doctor
```

Hugging Face is also supported with `--provider huggingface`. Package snapshots in `requirements-lock-*-windows.txt` document the verified Windows environment; the setup scripts are the primary installation path. Linux/WSL users can invoke `bash scripts/setup-wsl.sh`, install FFmpeg, and set `quality.asr_python` to `.venv-asr-linux/bin/python` in a local configuration. The Windows browser/file-picker path is the verified target.

## Open the studio

```powershell
.\.venv\Scripts\python.exe ui.py
```

Open the localhost address printed by the launcher. Select or upload novels, inspect the chapter and cleanup preview, choose a voice and delivery settings, and add a conversion to the queue. Try a short preview before committing to a long novel.

Presets and generation parameters change the voice/audio cache identity. In preset mode, a new job with matching input and synthesis identity can import completed speech from earlier studio jobs; changing output speed, pitch, loudness, or encoding can then reuse raw speech. Imported audio still receives file/QC validation, while each job retains independent records and outputs. Reference cloning currently reuses cache within a resumed job; cross-job import is limited to presets.

The server binds to loopback only. It has no remote login or Internet deployment mode. Browser controls operate studio-owned jobs; conversions started elsewhere are monitored read-only. Pausing waits for a safe segment/batch boundary and can take time during inference or encoding. Closing the server does not force-kill a conversion; persisted process identity allows reattachment on restart.

**Cancel task** immediately removes waiting/paused jobs from the queue. Running jobs show **Cancelling** until the current operation reaches a safe boundary; cancellation never kills an unrelated process or deletes input, cached speech, or exports. A cancelled job stays stopped across server restarts and can be explicitly requeued, retrying unfinished/failed segments while retaining successful speech. **Run next** changes only waiting order. **Hide record** removes a stopped job from the default view; enable **Show hidden records** to restore it. Completed jobs can be hidden instead of cancelled.

The running studio can also be controlled from a terminal (use `--port` for a non-default port):

```powershell
python ui.py --list-jobs
python ui.py --cancel-job <job-id>
python ui.py --cancel-queued
```

These commands use the live local API, so they do not edit queue files behind the server's back. Start the studio first. Bulk cancellation affects waiting jobs only.

For a new studio checkout beside a running older checkout, reuse its models and environment without editing its files:

```powershell
<existing-workspace>\.venv\Scripts\python.exe ui.py --workspace <existing-workspace>
```

Studio uploads, snapshots, and output stay under the new checkout's `work/ui` and `output/ui`. Existing workspace progress is read-only. A new queued GPU task waits for an identified external conversion to finish. Do not run setup or upgrade dependencies while another job is using that environment.

## CLI

Place TXT/EPUB files under `books/` or supply any input path:

```powershell
python main.py books/ --dry-run
python main.py books/ --recursive
python main.py books/ --status
python main.py books/ --retry-failed
python main.py examples/sample.epub --chapters 1 --voice Serena --batch-size 4
```

Run the same command again to resume. `--config config.local.yaml` selects a separate configuration. `--batch-size 1` uses independent segment sampling. The allowed maximum is 16, but larger batches have not been validated as an optimum; start with 2–4 and inspect quality and memory. Different batch composition can change sampled speech because the official interface uses a shared random stream. Completed audio remains reusable when only the batch policy changes.

`--max-segments N` stops after a small number of new successful segments. `--stop-file <path>` pauses cooperatively when that file exists; remove it before resuming. `--chapters 1,3-5` selects original chapter indexes; preview the actual indexes before using it. A chapter selection intentionally exports only that selection.

## Text and pronunciation settings

All cleanup is disabled by default. Preview it before processing your own novels. Front matter removal requires a recognized first chapter and removes everything before it; it may remove a legitimate preface. Line patterns explicitly remove whole matching lines. Pronunciation replacements are literal and may apply inside longer words, so use precise phrases.

```yaml
text:
  remove_urls: true
  strip_front_matter: false
  remove_line_patterns: ['^Downloaded from.*$']
  pronunciation_map:
    重庆: 崇庆
output:
  speech_rate: 1.1
  pitch_semitones: -1.0
```

The replacement above is an illustrative phonetic workaround, not a universal linguistic correction. Inspect and listen to your own text. The audit records removed material and replacement counts. ASR is a useful screening tool, but recognition mistakes can reject correctly spoken names, numbers, and dialect. Reports retain transcript and error details for review; do not disable all quality checks just to remove a warning.

## Output and recovery

```text
src/local_audiobook/     CLI, parsing, TTS, QC, exports, job server, static UI
examples/               Original demonstration text and EPUB
work/<book-id>/          Parsed text, audit, SQLite state, cached segment WAVs
output/<book-id>/        Chapter audio, M4B, manifest, failures
work/ui/                Private UI uploads, job records, configuration snapshots
output/ui/              Private studio exports
models/                 Separately downloaded official weights
```

Speech caches include the model identity, voice/reference identity, text, seed, and synthesis parameters. QC and rendering have separate identities. Corrupt caches are rejected. A failed segment prevents a chapter/full audiobook from being presented as complete, while other segments/books can continue. Retrying reuses valid audio. Keep `work/` if you want resumability; deleting it discards segment state.

## Performance and validation

Measured on one Windows RTX 5090 D using four original short passages: serial synthesis took 111.48 s; a batch of four took 34.80 s (approximately 3.20× text throughput). All twelve outputs across batch sizes 1/2/4 passed the existing QC threshold. A separate 750-character production run produced 202.16 s of raw audio in 138.64 s of synthesis (RTF 0.686), with all four segments passing ASR. Model loading, ASR, and export add time. These runs shared the GPU with another conversion and are not an entire-novel endurance benchmark. See [method and limits](docs/performance.md).

RTF means synthesis seconds divided by generated audio seconds; lower is faster. ETA is an estimate from completed work and becomes meaningful only after enough progress. GPU inference still dominates; Python multithreading alone does not multiply one GPU's capacity. FlashAttention, vLLM-Omni, multi-GPU support, and automatic model switching are not part of this release.

For existing checks (no weights required for the regression suite):

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src scripts tests audiobook.py main.py ui.py
```

## Publication and licenses

`.gitignore` excludes input novels, voices, model weights, generated audio, job data, logs, local settings, environments, and machine reports. Only source, original examples, existing regression checks, configuration defaults, and documentation belong in a public checkout. Never add your private books or reference recordings to Git.

Project code is distributed under **AGPL-3.0-or-later**; see [LICENSE](LICENSE). EbookLib 0.20 is AGPL-3.0. Official Qwen3-TTS and Qwen3-ASR repositories/weights have their own Apache-2.0 licenses. Models and FFmpeg are installed separately, not redistributed in this repository. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for primary sources.
