# Architecture and operational boundaries

## Components

The project is a small Python orchestration layer around official Qwen inference, EbookLib/BeautifulSoup, SQLite, and FFmpeg. The command-line pipeline remains the production path; the FastAPI browser studio creates independent CLI jobs instead of duplicating synthesis logic. Static HTML/CSS/JavaScript has no CDN or frontend build dependency.

`parser.py` and `text.py` extract EPUB spine order or TXT chapters, perform opt-in cleanup, and split at Chinese punctuation/paragraph boundaries. Literal pronunciation mappings retain the original text and add spoken text. `engine.py` adapts the official CustomVoice/Base interfaces. `generation.py` stages bounded batches. `pipeline.py` coordinates QC, resumability, and exports. `jobs.py` keeps the UI queue, job snapshots, process identities, and read-only monitoring. `web.py` exposes loopback HTTP endpoints.

`voices.py` prepares bounded local references without importing the TTS model. Reference preparation is another owned queue job, with immutable input copies, per-attempt worker receipts and the existing cancellation boundary. It performs FFmpeg conversion, conservative gain/edge trimming, optional isolated Chinese ASR, and ordered same-speaker concatenation. The parent registers the final WAV only after a matching successful worker receipt. Voice profiles retain their own audio copy and corrected transcript under ignored `work/ui/voices`; the library registry is written atomically under the manager lock. Media lookup requires a registered reference ID. In-flight conversions retain their original reference snapshot. Theme state is independent browser-local appearance data.

## Data flow

Reference video uploads (MP4/M4V/MOV/MKV/WebM/AVI, at most 512 MB and 10 minutes) are decoded locally in the HTTP thread pool. FFprobe checks for an audio track and a finite duration before FFmpeg explicitly selects the first audio stream and writes bounded 24 kHz mono PCM WAV. The registered reference points to this playable WAV and records the retained original video path/name/size. Failed uploads remove incomplete video/audio files. The existing range, transcription, queue and clone prompt workflow receives only audio; extraction does not load a GPU model, modify existing references or separate speakers/music. Audio upload limits remain 64 MB. Private video extensions are excluded from Git.

`downloads.py` streams ZIP_STORED entries through a bounded audio buffer and the HTTP response, retaining ZIP64 support without an archive-sized temporary file or browser Blob. `JobManager.download_records` accepts completed studio job IDs and an existing audio format, reuses the media allowlist, deduplicates exports and keeps task-relative output paths. POST `/api/downloads` validates the selection and returns metadata plus a GET URL; GET validates again before starting the attachment. Requests are bounded to 100 jobs/20,000 files, and streaming checks source size/mtime. Disconnect cleanup closes the iterator and source handles. Existing loopback/origin checks apply to both endpoints.

Download preferences/submission history are separate browser-local state. Automatic collection is opt-in, uses an activation timestamp, excludes preview/reference jobs and waits for the studio queue to become idle. The enabled page continues polling while hidden, subject to browser scheduling; a later visit can collect eligible results finished while closed. Web Locks serialize submission where supported, with persisted history checked under the lock. A submission is not proof of download completion; manual ZIP/per-file links allow retry. Source/job/cache/queue state is read-only during downloading.

1. Select/upload a book and preview chapters, cleanup, and segment counts without loading a model.
2. Create an immutable input copy and validated per-job configuration snapshot.
3. Wait for any identified external conversion and for the studio's active job.
4. Launch one owned worker using the existing environment and local model paths; prepare compatible speech caches, then invoke the existing CLI pipeline.
5. Generate similar-length segments in bounded batches, stage WAVs and hashes, then check each segment.
6. Apply acoustic QC and optional ASR against spoken text; retry failed segments individually.
7. Atomically retain successful audio and SQLite state, prepare pauses/speed/pitch, normalize chapters in two passes.
8. Export chapters and the complete selected M4B only when the selected material has no missing segments.

## Cache and recovery

Recognized downloader cleanup is a text-planning policy, enabled by default through `strip_downloader_metadata`. Shaft markers and contiguous header fields are recognized before optional URL removal; an unmarked header requires title/author plus a Pixiv source URL. Processing stops at the first ambiguous prose line. TXT title/author are recovered from the cleanup audit, and removed text is bounded/hash-retained for review. Source files remain immutable; changed speech text receives its existing content-derived segment identity.

List management stores reversible `archived` flags in the file/voice registries and job records. Bulk routes validate all IDs before mutation, accept at most 200 IDs per request and deduplicate them; the browser batches larger selections. Job removal reuses cooperative cancellation and persists `archive_when_stopped` until worker completion, including after a server restart. Restoring a same-name voice archives the other active version, retaining all audio. Restoring records does not automatically launch a worker. Scroll/search/selection are browser view state; downloader search is separate from automatic download eligibility. Existing single-record APIs remain compatible.

Generation identity includes local model identity, relevant package versions, voice/reference identity, text, seed, and sampling parameters. Batch size is an execution policy and does not invalidate completed audio. New pronunciation text creates a distinct segment identity only when the spoken text actually changes.

QC and render identities are separate. A QC threshold change rechecks speech; output rate, pitch, loudness, pauses, and formats reuse raw audio. Neutral new controls are omitted from legacy fingerprint comparisons so an unchanged configuration retains existing caches/exports. SHA256 checks detect corrupted cached audio.

For preset-mode studio jobs, matching input hashes/names, official engine fingerprints, and exact planned segment identities permit copying previously completed raw speech and segment state into the new isolated runtime. The production planner supplies the same segment identities used by conversion; no separate cache-ID formula is maintained. Artifact/export records are not copied, and current QC still applies. Preparation runs in the owned worker, so large cache copies do not block the HTTP server. Historical jobs remain unchanged. Cross-job reference-clone import is not currently supported.

Batch results are persisted as `generated` before individual QC. Only QC success changes a segment to `completed`. Interrupted staged audio can be reused after hash validation. Batch failures reduce batch size and eventually fall back to individual requests. A shared official batch random stream means changing grouping can change sampled delivery; batch membership and actual batch seed are retained.

## Protecting an existing conversion

The studio has its own runtime directories under its checkout. `--workspace` selects an existing model/ASR workspace and legacy progress to observe. It does not upgrade environments, rewrite that workspace's default configuration, or control an external worker. External converter detection checks executable names and relevant launcher paths; unrelated Python applications are ignored.

Pause/resume controls apply only to studio records. Process identity is checked using PID, creation time, and the job's exact configuration argument. Pausing writes a cooperative stop flag and waits for a safe boundary. The server's shutdown leaves a running conversion alive and saves enough information to reattach after restart.

The HTTP server binds to loopback, validates loopback hosts/origins, and serves only allowlisted job exports. Uploaded names do not become arbitrary filesystem destinations. The UI does not accept shell commands or arbitrary output/runtime paths.

## Quality controls and limits

Cleanup is conservative and disabled by default. Removing a downloader preamble requires a recognized chapter; user patterns explicitly remove whole lines. The preview/audit should be reviewed because a real preface or a URL quoted in a story may be legitimate content. The pipeline retains original input and original/spoken text; it does not ask a language model to rewrite a novel.

ASR flags likely omissions/repetitions and retains transcript/error details and retry history, but it can misrecognize proper names, numbers, and dialect. A CER failure is a quality decision, not evidence of a model content filter or missing FlashAttention. Failed chapters are not silently stitched into a supposedly complete book.

Output pitch uses FFmpeg's rubberband filter and fails explicitly when that filter is unavailable. Output speed can use atempo without pitch adjustment. This is independent postprocessing, not voice training. Stronger adjustments may sound less natural; preview before a long run.

This release does not implement automatic character casting, streaming playback during inference, distributed/multi-GPU scheduling, arbitrary VoiceDesign generation, or cloud TTS. The verified target is Windows with an NVIDIA GPU. Linux/WSL setup is provided but has not received the same end-to-end local validation.

## Primary references

- [Official Qwen3-TTS and speaker/instruction interfaces](https://github.com/QwenLM/Qwen3-TTS)
- [CustomVoice model card](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice)
- [Official Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR)
- [FFmpeg rubberband and atempo filters](https://ffmpeg.org/ffmpeg-filters.html)
- [FastAPI file uploads](https://fastapi.tiangolo.com/tutorial/request-files/)

See [performance.md](performance.md) for actual measured speed and its limits, and [handoff.md](handoff.md) for release verification.
