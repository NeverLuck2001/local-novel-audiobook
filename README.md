# Local Novel Audiobook Studio

[简体中文](README.zh-CN.md) · [Architecture](docs/architecture.md) · [Performance](docs/performance.md)

Turn Chinese TXT and EPUB novels into audiobooks on your own computer. A local browser studio controls official Qwen3-TTS, keeps resumable segment caches, checks speech with optional local Qwen3-ASR, and exports chapter audio and an M4B audiobook.

Model installation requires a download. Conversion uses local model directories with offline inference settings; novel text is not sent to a hosted TTS service.

## Features

- TXT encoding detection and EPUB spine order, metadata, and cover extraction.
- Browser file selection and upload, chapter preview and selection, visible cleanup audit.
- Nine official CustomVoice presets, free-form delivery instructions, short voice previews.
- Independent output speed (0.75–1.5×), pitch (±3 semitones), pauses, and loudness controls.
- Explicit pronunciation replacements: retain source text and check ASR against the spoken text.
- Opt-in URL, downloader front matter, and user-specified line-pattern removal.
- One GPU queue with durable order/positions, run-next priority, safe pause/cancellation, resume, and failed-segment retries.
- Cancel all waiting jobs without stopping the active job; hide and restore stopped records without deleting audio.
- Progress, generated duration, measured generation time, RTF, approximate ETA, and logs.
- Read-only monitoring of a conversion started outside the studio; queued jobs wait for it.
- Official batched inference, SHA256-checked segment caches, retained QC and failure reports.
- FLAC/WAV/MP3 chapters and complete M4B exports with chapter markers and EPUB cover.

Version 0.2.2 corrects completion reporting after failed restarts, retains per-attempt results across studio restarts, and recovers ASR workers on the next segment retry. Existing jobs and speech caches remain compatible. See the [review handoff](docs/handoff.md) for validation and limits.

The browser studio exposes preset voices and enables its reference-cloning controls when separately downloaded **Base** weights are available. Cloning requires a reference recording and its exact transcript; it has not been audio-validated in this release environment. VoiceDesign is a separate model; installing CustomVoice does not enable it. Automatic character casting and LLM rewriting are not implemented.

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
