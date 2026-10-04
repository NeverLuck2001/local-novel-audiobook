# Release handoff: 0.2.3

## Voice defaults and record removal: 2026-10-04

- `config.yaml` changes new release jobs to Vivian, a mature/confident narration instruction, output rate 1.15 and pitch -1 semitone. Core schema defaults and generation algorithms are unchanged. The original workspace configuration and existing job snapshots remain untouched; changing speaker/instruction requires new speech, while rate/pitch remain output processing.
- The browser adds a combined mature narrator shortcut, migrates voice defaults once without replacing other saved controls, keeps later manual edits, and fixes shortcut wrapping. Versioned asset URLs avoid retaining older browser scripts/styles. Default batch size remains the previously validated 4; the interface recommends starting at 8 on 32GB and trying 16 on a short chapter. No actual 8/16 long-batch benchmark is claimed.
- `jobs.py` / `web.py` persist removed legacy display records separately from conversion state, allow restore, reject unknown IDs/actions, and conservatively prevent removal while an external converter runs. Studio records reuse existing recoverable archiving. The browser labels these controls Delete/Restore and offers a combined recovery list.
- Older loaded servers can serve the updated static UI without interrupting conversion. The browser keeps local remove/restore intents, checks for an external conversion before removal, and synchronizes intents after a newer backend starts. This also makes the voice preset/default available before server restart. The three stale workspace legacy records were removed from display and retained as recoverable metadata; no book, audio or progress files were deleted.
- Verification: all 44 existing checks passed in 55.90 seconds; no existing test sources changed. Python lint, JavaScript syntax and whitespace checks passed. Isolated API observations covered default values, record removal/restore, persistence across manager recreation, retained progress files, unknown IDs (404), invalid actions (400), and refusal while a lightweight external process remained alive (400). Browser actions verified current-server compatibility, all three removals, refresh persistence, recovery and removal again, and combined preset switching. The seven protected original configuration/progress/job snapshot files retain their hashes.
- Verify by reloading the studio, selecting the mature shortcut, and inspecting rate/pitch. Enable Show deleted records, restore a stopped record, then remove it again. Use a short voice preview before converting a full book. No new GPU synthesis or perceptual quality validation was run for this preset; its mature character remains an approximation of the selected official voice. Output pitch needs FFmpeg `rubberband`, or use zero pitch. Cancellation still waits for a safe inference/encoding boundary.

No tests were written in this update.

## Code review update: 2026-10-02

- Reviewed queue ownership, cancellation/requeue, saved results, cache reuse, text/config validation, local HTTP boundaries, ASR lifecycle, and audio delivery. Changes are limited to `jobs.py`, `asr.py`, `config.py`, `web.py`, version metadata, and documentation.
- Fixed a reproduced false completion: a worker exiting with code 1 could inherit an earlier completed `batch.json`. Each new attempt now retains a matching result receipt, completion requires a current batch, and known failures take precedence. Reopening the studio retains the exit code; missing/mismatched receipts cannot certify completion. Existing workers without an attempt ID remain compatible.
- ASR startup/protocol/pipe failures now release the worker and handles so the existing segment retry can start a fresh process. Dead workers also restart, with response queues isolated per process. CER rejection alone does not reload ASR.
- Queue shutdown waits for its scheduler before releasing ownership and checks shutdown again before launch. An inaccessible saved PID conservatively holds the queue. Conversion children remain independent and are not terminated by manager shutdown.
- Job totals now come from immutable copied inputs, avoiding planning a downloader file before it changes. Summary responses copy the outer cache dictionary so monitoring cannot remove cached manifests. Invalid text regexes become readable validation errors; malformed origins return 403. App shutdown closes the manager even when lifespan exits exceptionally.
- Verification: all 44 existing checks passed in 35.13 seconds; Python lint, JavaScript syntax, and whitespace checks passed. Isolated local API and real lightweight worker observations covered failed startup with an old completed batch, result recovery after reopening, mismatched receipts, pause/requeue/cancellation with input retention, snapshot totals, retained summary manifests, regex validation (400), invalid origins (403), and allowed origins (200). Controlled ASR protocol processes recovered after an error and after termination; shutdown during a delayed queue scan did not launch a job and released ownership only after the scheduler stopped.
- The final 0.2.2 wheel matches all 19 package source/static files; the four existing test source files are unchanged. Verification artifacts remain in ignored `work/` and `dist/` directories.
- Verify with the existing README check commands, then restart the studio and inspect retained jobs. Updating source does not replace an already loaded server module. Logs and result receipts remain under each private job directory. Application version is 0.2.2; dependencies, speech identities, model adapters, and neutral render identities are unchanged.
- This review did not run new GPU synthesis or an end-to-end ASR model benchmark. Protocol recovery used lightweight local processes. Cooperative cancellation still waits for an inference/encoding boundary; the previously documented clone, Linux/WSL, and endurance limits remain.

No tests were written in this update.

## Queue controls update: 2026-10-02

- `jobs.py` / `web.py` add durable cooperative cancellation, bulk cancellation of waiting jobs, stable queue order/positions, run-next priority, and reversible record archiving. Cancelled tasks retain inputs, speech and exports; explicit resume requeues them. External conversions remain read-only.
- The browser adds per-task cancel/requeue/hide/restore actions, queue positions, a bulk-cancel button, and hidden-record visibility. `ui.py` adds API-based `--list-jobs`, `--cancel-job` and `--cancel-queued`; these require the studio server to be running.
- Existing queue records remain compatible. Application version is 0.2.1; synthesis/cache identities are unchanged. No dependency changes or conversion-process termination is needed to apply this update.
- Requeuing cancelled/failed jobs retries unfinished failed segments as well as pending work. A requeue clears stale attempt timestamps/exit codes; stopped jobs no longer display a stale active ETA.
- Verification: the final 44 existing checks passed in 34.24 seconds; Python lint and JavaScript syntax passed. Manual API/controlled-process checks observed `cancelling` to `cancelled`, idempotent cancellation, queue order/position retention, requeue, archive/restore, bulk cancellation, retained inputs, and rejection of unknown external IDs/foreign origins. Browser clicks verified the queue controls; terminal commands verified the live local API. The final wheel matches the shipped Python/static files. Runtime observations stay under ignored `work/`.
- Cancellation remains cooperative and may wait for the current inference or encoding operation. Hidden records keep their files and can still supply compatible speech to future jobs; archiving does not reclaim disk space.

No tests were written in this update.

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
