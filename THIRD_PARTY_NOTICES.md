# Third-party components

This repository contains the local orchestration and browser studio code. It does not redistribute model weights, FFmpeg binaries, downloaded research repositories, or user books/recordings.

| Component | Role | Upstream license/source |
|---|---|---|
| EbookLib 0.20 | EPUB parsing | [AGPL-3.0](https://github.com/aerkalov/ebooklib/blob/master/LICENSE.txt) |
| Qwen3-TTS | Official TTS package and separately downloaded weights | [Apache-2.0](https://github.com/QwenLM/Qwen3-TTS/blob/main/LICENSE), [CustomVoice model card](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice) |
| Qwen3-ASR | Separate local ASR process and weights | [Apache-2.0](https://github.com/QwenLM/Qwen3-ASR/blob/main/LICENSE), [ASR model card](https://huggingface.co/Qwen/Qwen3-ASR-0.6B) |
| FFmpeg | Locally installed audio processing/export executable | [License/build information](https://ffmpeg.org/legal.html); terms depend on the installed build |
| FastAPI / Uvicorn | Local HTTP server | [FastAPI](https://github.com/fastapi/fastapi/blob/master/LICENSE), [Uvicorn](https://github.com/encode/uvicorn/blob/main/LICENSE.md) |

Other Python dependencies retain their respective upstream licenses. See `pyproject.toml` and the Windows environment snapshots for package names/versions. Model installation records the downloaded repository and identity manifest. This project's code is AGPL-3.0-or-later; its license does not replace upstream component licenses.
