"""Record real local TTS latency, RTF and memory; never extrapolate README claims."""
import argparse
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from local_audiobook.config import load_config
from local_audiobook.engine import QwenEngine
from local_audiobook.quality import check_audio
from local_audiobook.util import atomic_json, offline, now


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dtypes", nargs="+", default=["bfloat16", "float16"], choices=["bfloat16", "float16"])
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--text", default="雨停的时候，林溪推开书店的窗。街道上积着浅浅的水，远处的钟楼刚好敲了六下。她把一封没有署名的信放在桌面上，纸角微微发黄，像是被人珍藏了很久。")
    args = parser.parse_args()
    offline()
    import torch
    torch.set_num_threads(args.threads)
    root = Path(__file__).resolve().parents[1]
    output = root / "output/benchmarks"
    output.mkdir(parents=True, exist_ok=True)
    report = {"created_at": now(), "gpu": torch.cuda.get_device_name(0), "torch": torch.__version__,
              "threads": args.threads, "text": args.text, "runs": [],
              "note": "Serial SDPA, generation includes codec decode; model loading is measured separately. One take per dtype, not a sustained throughput estimate."}
    for dtype in args.dtypes:
        cfg = load_config(root / "config.yaml")
        cfg.tts.dtype = dtype
        engine = QwenEngine(cfg)
        started = time.perf_counter()
        engine.load()
        load_seconds = time.perf_counter() - started
        path = output / f"{dtype}-threads{args.threads}.wav"
        try:
            metrics = engine.generate(args.text, path, cfg.tts.seed)
            result = {"dtype": dtype, "attention": cfg.tts.attention, "path": str(path),
                      "load_seconds": load_seconds, **metrics, "qc": check_audio(path, args.text, cfg.quality)}
            report["runs"].append(result)
            print(result, flush=True)
        finally:
            engine.unload()
        atomic_json(root / f"docs/benchmark-threads{args.threads}.json", report)


if __name__ == "__main__":
    main()
