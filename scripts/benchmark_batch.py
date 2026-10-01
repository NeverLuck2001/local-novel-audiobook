"""Compare official serial/batched GPU inference and local ASR on original prose."""
import argparse
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from local_audiobook.asr import ASRWorker
from local_audiobook.config import load_config
from local_audiobook.engine import QwenEngine
from local_audiobook.quality import check_audio, compare_text
from local_audiobook.util import atomic_json, now, offline

TEXTS = [
    "雨停了，林溪推开书店的窗。街上的水洼映着天空，远处的钟楼敲了六下。她把那封信放在桌上，轻轻展开。",
    "陈先生看了看墙上的钟，说：今天是十月一日，我们下午三点半出发。你先检查行李，我去买两张车票。",
    "她没有立刻回答，只是把杯子握得更紧了。过了一会儿，她抬起头，低声说：我想回去看看，那条路还在吗？",
    "山风吹过树林，树叶沙沙作响。他沿着小路慢慢走到河边，看见一只白色的小船，静静停在旧木桥下面。",
]


def gpu_snapshot():
    result = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,utilization.gpu", "--format=csv,noheader"],
                            capture_output=True, text=True, timeout=15)
    return result.stdout.strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", nargs="+", type=int, default=[1, 2, 4])
    parser.add_argument("--report", type=Path, default=Path("docs/benchmark-batch.json"))
    parser.add_argument("--text-file", type=Path, help="TXT sample split with the normal chapter/segment planner")
    parser.add_argument("--no-asr", action="store_true")
    args = parser.parse_args()
    if any(size < 1 or size > 16 for size in args.sizes):
        parser.error("batch sizes must be between 1 and 16")
    offline()
    import torch
    root = Path(__file__).resolve().parents[1]
    cfg = load_config(root / "config.yaml")
    texts_to_test = TEXTS
    if args.text_file:
        from local_audiobook.pipeline import Pipeline
        chapters = Pipeline(cfg).plan(args.text_file)[4]
        texts_to_test = [s["text"] for c in chapters for s in c["segments"]]
        if not texts_to_test:
            parser.error("sample contains no segments")
    engine = QwenEngine(cfg)
    output = root / "output" / args.report.stem
    output.mkdir(parents=True, exist_ok=True)
    report = {"created_at": now(), "gpu": torch.cuda.get_device_name(0), "torch": torch.__version__,
              "gpu_before": gpu_snapshot(), "texts": texts_to_test, "runs": [],
              "note": "Short-sample throughput, including codec; excludes loading and ASR. "
                      "Batch sampling differs from serial. Other GPU jobs may contend; snapshots are recorded."}
    asr = None
    try:
        started = time.perf_counter()
        engine.load()
        report["load_seconds"] = time.perf_counter() - started
        engine.generate("窗外的雨停了。", output / "warmup.wav", cfg.tts.seed)
        for size in args.sizes:
            started = time.perf_counter()
            clips = []
            before = gpu_snapshot()
            for offset in range(0, len(texts_to_test), size):
                texts = texts_to_test[offset:offset + size]
                paths = [output / f"batch{size}-{i + 1}.wav" for i in range(offset, offset + len(texts))]
                seeds = [cfg.tts.seed + i for i in range(offset, offset + len(texts))]
                metrics = engine.generate_batch(texts, paths, seeds)
                for text, path, metric in zip(texts, paths, metrics, strict=True):
                    clips.append({"path": str(path), "text": text, **metric,
                                  "qc": check_audio(path, text, cfg.quality)})
            gen = sum(clip["generation_seconds"] for clip in clips)
            duration = sum(clip["duration"] for clip in clips)
            run = {"batch_size": size, "generation_seconds": gen, "audio_seconds": duration,
                   "actual_max_batch_size": max(clip.get("batch_size", 1) for clip in clips),
                   "rtf": gen / duration, "wall_seconds": time.perf_counter() - started,
                   "peak_vram_gb": max(clip["peak_vram_gb"] for clip in clips),
                   "gpu_before": before, "gpu_after": gpu_snapshot(), "clips": clips}
            report["runs"].append(run)
            atomic_json(args.report, report)
            print({k: v for k, v in run.items() if k != "clips"}, flush=True)
        engine.unload()
        if not args.no_asr:
            asr = ASRWorker(cfg.quality, output / "asr.log")
            for run in report["runs"]:
                for clip in run["clips"]:
                    comparison = compare_text(clip["text"], asr.transcribe(Path(clip["path"])))
                    clip["qc"]["asr"] = comparison
                    if comparison["cer"] > cfg.quality.max_cer:
                        clip["qc"]["pass"] = False
                        clip["qc"]["reasons"].append("asr_cer_exceeded")
                run["qc_passed"] = sum(clip["qc"]["pass"] for clip in run["clips"])
                run["max_cer"] = max(clip["qc"]["asr"]["cer"] for clip in run["clips"])
                atomic_json(args.report, report)
                print({"batch_size": run["batch_size"], "qc_passed": run["qc_passed"],
                       "max_cer": run["max_cer"]}, flush=True)
    finally:
        engine.unload()
        if asr:
            asr.close()


if __name__ == "__main__":
    main()
