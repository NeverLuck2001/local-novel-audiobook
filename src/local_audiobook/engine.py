"""Official local Qwen inference with bounded GPU batches."""
import gc
import importlib.metadata
from pathlib import Path
import random
import time

import numpy as np
import soundfile as sf

from .config import Config
from .util import digest, file_hash, model_identity, offline
from . import GENERATOR_VERSION


class QwenEngine:
    def __init__(self, cfg: Config):
        offline()
        self.cfg = cfg
        self.model = None
        self.clone_prompt = None
        self.identity = model_identity(cfg.tts.model_path)
        self.versions = {name: importlib.metadata.version(name) for name in ["qwen-tts", "torch", "transformers"]}
        voice = cfg.voice.model_dump(mode="json")
        if cfg.voice.mode == "clone":
            if not cfg.voice.reference_audio.is_file():
                raise FileNotFoundError(f"Voice reference missing: {cfg.voice.reference_audio}")
            voice["reference_sha256"] = file_hash(cfg.voice.reference_audio)
        self.fingerprint = digest({"generator": GENERATOR_VERSION, "model": self.identity["sha256"],
                                   "versions": self.versions,
                                   "tts": cfg.tts.model_dump(mode="json", exclude={"model_path", "batch_size"}),
                                   "voice": voice})

    def load(self):
        if self.model is not None:
            return
        import torch
        from qwen_tts import Qwen3TTSModel
        tts = self.cfg.tts
        if tts.device.startswith("cuda"):
            if not torch.cuda.is_available():
                raise RuntimeError("CUDA unavailable; run doctor before converting")
            if torch.cuda.get_device_capability(tts.device)[0] >= 12 and "sm_120" not in torch.cuda.get_arch_list():
                raise RuntimeError("PyTorch does not contain sm_120 kernels; install the cu128 build")
        self.model = Qwen3TTSModel.from_pretrained(str(tts.model_path), device_map=tts.device,
                                                  dtype=getattr(torch, tts.dtype),
                                                  attn_implementation=tts.attention,
                                                  local_files_only=True)
        expected = "custom_voice" if self.cfg.voice.mode == "preset" else "base"
        if self.model.model.tts_model_type != expected:
            self.unload()
            raise ValueError(f"Selected voice mode requires model type {expected}")
        if self.cfg.voice.mode == "preset":
            speakers = self.model.get_supported_speakers() or []
            if self.cfg.voice.speaker.lower() not in {s.lower() for s in speakers}:
                self.unload()
                raise ValueError(f"Unsupported speaker. Available: {speakers}")
        else:
            audio, rate = sf.read(self.cfg.voice.reference_audio, dtype="float32")
            if audio.ndim > 1:
                audio = audio.mean(axis=1)
            self.clone_prompt = self.model.create_voice_clone_prompt(
                ref_audio=(audio, rate), ref_text=self.cfg.voice.reference_text, x_vector_only_mode=False)

    def generate(self, text: str, output: Path, seed: int) -> dict:
        return self.generate_batch([text], [output], [seed])[0]

    def recover(self):
        import torch
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    def generate_batch(self, texts: list[str], outputs: list[Path], seeds: list[int]) -> list[dict]:
        """One official list-input call, rather than threads sharing a model/RNG.

        Qwen has one RNG stream per batch. Persist its seed and membership; a
        batch is reproducible with the same members/order, not bit-identical to
        separately sampled single requests. Existing completed audio is reused.
        """
        if not texts or len(texts) != len(outputs) or len(texts) != len(seeds):
            raise ValueError("Expected equal, non-empty texts, outputs and seeds")
        import torch
        self.load()
        batch_id = digest(list(zip(texts, seeds, strict=True)))
        batch_seed = seeds[0] if len(texts) == 1 else int(batch_id[:8], 16) % (2**31 - 1)
        random.seed(batch_seed)
        np.random.seed(batch_seed)
        torch.manual_seed(batch_seed)
        cuda = self.cfg.tts.device.startswith("cuda")
        if cuda:
            torch.cuda.manual_seed_all(batch_seed)
            torch.cuda.reset_peak_memory_stats(self.cfg.tts.device)
            torch.cuda.synchronize(self.cfg.tts.device)
        started = time.perf_counter()
        args = {"text": texts[0] if len(texts) == 1 else texts,
                "language": self.cfg.voice.language, **self.cfg.tts.generation_args()}
        with torch.inference_mode():
            if self.cfg.voice.mode == "preset":
                waves, rate = self.model.generate_custom_voice(speaker=self.cfg.voice.speaker,
                                                              instruct=self.cfg.voice.instruct, **args)
            else:
                waves, rate = self.model.generate_voice_clone(voice_clone_prompt=self.clone_prompt, **args)
        if cuda:
            torch.cuda.synchronize(self.cfg.tts.device)
        elapsed = time.perf_counter() - started
        if len(waves) != len(texts):
            raise RuntimeError("Qwen returned the wrong number of batch outputs")
        durations = [len(wave) / rate for wave in waves]
        total_duration = sum(durations)
        peak = torch.cuda.max_memory_allocated(self.cfg.tts.device) / 1024**3 if cuda else 0
        results = []
        for wave, output, seed, duration in zip(waves, outputs, seeds, durations, strict=True):
            output.parent.mkdir(parents=True, exist_ok=True)
            sf.write(output, wave, rate, format="WAV", subtype="PCM_16")
            metric = {"generation_seconds": elapsed / len(texts), "duration": duration,
                      "sample_rate": rate, "rtf": elapsed / max(total_duration, .001),
                      "seed": seed, "peak_vram_gb": peak}
            if len(texts) > 1:
                metric.update(batch_size=len(texts), batch_seed=batch_seed, batch_id=batch_id,
                              batch_seconds=elapsed, batch_audio_seconds=total_duration)
            results.append(metric)
        return results

    def unload(self):
        self.model = None
        self.clone_prompt = None
        self.recover()
