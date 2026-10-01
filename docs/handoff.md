# Release handoff: 0.2.0

## Changes and affected components

- `web.py`, `jobs.py`, root `ui.py`, `scripts/start-ui.ps1`, and static `web/` add the local studio, file planning, per-job snapshots, persistent single-worker queue, read-only external monitoring, safe pause/recovery, and registered media serving.
- `config.py`, `text.py`, `cli.py`, `pipeline.py`, `generation.py`, and `audio.py` add explicit text/pronunciation controls, chapter selections, neutral-compatible cache identities, output rate/pitch, cooperative stop flags, richer metrics, and retained failure/QC reports.
- Default generation architecture, official model adapter, CLI interfaces, and source order remain intact. New transformations are neutral/disabled by default.
- Packaging includes static UI files and optional UI dependencies. Windows/WSL setup includes the UI extra. Bilingual READMEs, architecture/performance notes, dependency notices, license, and ignore rules prepare a source-only public release.
- Preset-mode studio jobs can import matching completed raw speech from prior studio jobs before invoking the CLI. The worker checks input/model/voice/segment identity, preserves per-job isolation and current QC, and reports imported cache counts. Conversion and cache preparation share the extracted segment-identification method.

## Verification

- Final integrated regression suite: all 44 existing checks passed in 46.18 seconds. No test files were added or modified.
- Python lint and JavaScript syntax validation passed.
- Actual FFmpeg rate/pitch behavior, rate-only fallback, and missing-filter rejection were checked. Neutral cache/render identities, selected chapter indexes, staged recovery, pronunciation/source retention, audit limits, and cooperative stopping were checked.
- A real cached Qwen speech sample completed the new production pipeline at rate 1.1 and pitch -1 semitone: four caches reused, zero synthesis calls, FLAC and a 185.93-second M4B with one chapter marker. ASR was disabled for this isolated delivery check to avoid competing with the running user's GPU task; its original four speech segments had previously passed ASR.
- Cross-job preset reuse imported four verified raw speech segments after batch/rate/pitch changes and exported FLAC/M4B with model loading and synthesis forbidden. Changing the speaker imported zero segments. Original WAV checksums remained unchanged. A pre-paused owned worker exited without entering CLI generation or loading ASR.
- Local API checks covered uploads, previews, settings validation, duplicate segment counts, foreign-origin/runtime-path rejection, allowlisted media, queue waiting, pause/resume, and saved process reattachment. A tracked benign validation child survived server-manager shutdown and was explicitly cleaned up afterward.
- The actual cached M4B was served through the media API with `audio/mp4` MIME and a successful HTTP 206 byte-range response; unknown media and foreign origins were rejected. The built wheel contains the exact final three static UI assets and license notices.
- Browser integration verifies the actual static page, file selection/upload, chapter preview, queue controls, and read-only legacy progress. The active workspace's original source/config files remain untouched.

Use the commands in the README for a fresh installation and existing checks. Start `ui.py`, preview an original example, then generate a short voice preview once no external conversion is running. Review audit output before enabling cleanup on a real book.

## Remaining limits

The new UI has not run an entire-novel endurance benchmark, and no new GPU synthesis is launched alongside the protected external task. Base-clone controls require separately downloaded weights and have not received audio validation here. Pausing waits for the currently running inference/FFmpeg operation. ETA is approximate and needs current-run synthesis data. Pronunciation mappings operate within segments and may miss a phrase split across a hard boundary. Strong pitch/rate changes can reduce naturalness. The launcher/file-picker target is Windows; Linux/WSL needs its own validation.

No tests were written in this update.
