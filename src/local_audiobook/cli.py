"""One-command conversion, planning, diagnostics, and batch status."""
import argparse
import importlib.metadata
import json
import logging
from pathlib import Path
import subprocess
import sys

from .config import load_config
from .pipeline import Pipeline
from .util import atomic_json, now, offline


def discover(path: Path, recursive: bool) -> list[Path]:
    if path.is_file() and path.suffix.lower() in {".epub", ".txt"}:
        return [path.resolve()]
    if path.is_dir():
        files = path.rglob("*") if recursive else path.iterdir()
        return sorted((p.resolve() for p in files if p.is_file() and p.suffix.lower() in {".epub", ".txt"}),
                      key=lambda p: str(p).casefold())
    raise FileNotFoundError(f"No input book or directory: {path}")


def parse_chapter_selection(value: str) -> set[int]:
    selected = set()
    for part in value.split(","):
        bounds = part.strip().split("-")
        if len(bounds) not in {1, 2} or any(not bound.strip().isdigit() for bound in bounds):
            raise argparse.ArgumentTypeError("Use chapter indexes such as 1,3-5")
        first, last = int(bounds[0]), int(bounds[-1])
        if first < 1 or last < first or last > 1_000_000:
            raise argparse.ArgumentTypeError("Chapter indexes must be positive ascending ranges <= 1000000")
        selected.update(range(first, last + 1))
    return selected


def doctor(cfg, output: Path | None = None) -> int:
    report = {"python": sys.version, "executable": sys.executable,
              "model_path": str(cfg.tts.model_path), "model_config_exists": (cfg.tts.model_path / "config.json").exists()}
    errors = []
    for name in ["torch", "torchaudio", "qwen-tts", "transformers", "EbookLib"]:
        try:
            report[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            errors.append(f"Missing dependency: {name}")
    for name in ["ffmpeg", "ffprobe"]:
        try:
            result = subprocess.run([getattr(cfg, name), "-version"], capture_output=True, text=True,
                                    timeout=30, encoding="utf-8", errors="replace")
            if result.returncode:
                raise RuntimeError(result.stderr)
            report[name] = result.stdout.splitlines()[0]
        except Exception as exc:
            errors.append(f"{name}: {exc}")
    try:
        import torch
        report["cuda_available"] = torch.cuda.is_available()
        report["cuda_version"] = torch.version.cuda
        if torch.cuda.is_available():
            report["gpu"] = torch.cuda.get_device_name(0)
            report["vram_gb"] = torch.cuda.get_device_properties(0).total_memory / 1024**3
            report["capability"] = torch.cuda.get_device_capability(0)
            report["arch_list"] = torch.cuda.get_arch_list()
            x = torch.ones((128, 128), dtype=torch.bfloat16, device="cuda:0")
            report["bf16_matmul_ok"] = bool(torch.isfinite(x @ x).all().item())
        elif cfg.tts.device.startswith("cuda"):
            errors.append("CUDA unavailable for configured device")
    except Exception as exc:
        errors.append(f"Torch CUDA test: {exc}")
    report["errors"] = errors
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if output:
        atomic_json(output, report)
    return 1 if errors else 0


def main(argv=None) -> int:
    for stream in [sys.stdout, sys.stderr]:
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Offline novel to audiobook; rerun the same command to resume")
    parser.add_argument("input", nargs="?", help="TXT/EPUB file or directory")
    parser.add_argument("--config", type=Path, default=Path(__file__).resolve().parents[2] / "config.yaml")
    parser.add_argument("--dry-run", action="store_true", help="Parse and segment only; no models required")
    parser.add_argument("--doctor", action="store_true", help="Check local environment and GPU")
    parser.add_argument("--report", type=Path, help="Save the doctor result as JSON")
    parser.add_argument("--status", action="store_true", help="Print current book progress from local state")
    parser.add_argument("--recursive", action="store_true", help="Discover books in subdirectories")
    parser.add_argument("--max-segments", type=int, help="Gracefully stop after N newly successful segments")
    parser.add_argument("--retry-failed", action="store_true", help="Reset exhausted segment retry budgets")
    parser.add_argument("--stop-file", type=Path, help="Pause safely when this file exists; preserve it until resume")
    parser.add_argument("--chapters", type=parse_chapter_selection,
                        help="Convert selected chapter indexes, e.g. 1,3-5; original indexes remain unchanged")
    parser.add_argument("--voice", help="Override preset speaker, e.g. Serena or Vivian")
    parser.add_argument("--batch-size", type=int, choices=range(1, 17),
                        help="GPU segments per batch (1 restores serial generation)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    if args.max_segments is not None and args.max_segments < 1:
        parser.error("--max-segments must be positive")
    offline()
    try:
        cfg = load_config(args.config)
        if args.voice:
            cfg.voice.speaker = args.voice
        if args.batch_size is not None:
            cfg.tts.batch_size = args.batch_size
        if args.doctor:
            return doctor(cfg, args.report)
        if not args.input:
            parser.error("input is required unless --doctor is used")
        sources = discover(Path(args.input), args.recursive)
        if not sources:
            raise ValueError("No TXT/EPUB books found")
        cfg.work_root.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                            format="%(asctime)s %(levelname)s %(message)s",
                            handlers=[logging.StreamHandler(), logging.FileHandler(cfg.work_root / "pipeline.log", encoding="utf-8")])
        if not args.dry_run and not args.status:
            for executable in [cfg.ffmpeg, cfg.ffprobe]:
                subprocess.run([executable, "-version"], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=30, check=True)
        pipeline = Pipeline(cfg)
        batch = {"updated_at": now(), "books": [{"source": str(s), "status": "pending"} for s in sources]}
        batch_path = cfg.work_root / "batch.json"
        if not args.dry_run and not args.status:
            atomic_json(batch_path, batch)
        exit_code = 0
        try:
            for entry, source in zip(batch["books"], sources, strict=True):
                try:
                    if args.dry_run or args.status:
                        book, book_id, work, _, chapters = pipeline.plan(source, args.chapters)
                        if args.status:
                            progress = work / "progress.json"
                            print(json.dumps({"source": str(source), "book_id": book_id,
                                              "progress": json.loads(progress.read_text(encoding="utf-8"))
                                              if progress.exists() else {"status": "pending"}}, ensure_ascii=False))
                        else:
                            print(json.dumps({"source": str(source), "title": book.title, "author": book.author,
                                              "chapters": len(chapters), "segments": sum(len(c["segments"]) for c in chapters),
                                              "chars": sum(len(c["text"]) for c in chapters),
                                              "chapter_titles": [c["title"] for c in chapters],
                                              "chapter_indexes": [c["index"] for c in chapters],
                                              "hard_splits": sum(s["boundary"] == "hard" for c in chapters for s in c["segments"]),
                                              "audit": book.audit}, ensure_ascii=False, indent=2))
                        continue
                    entry["status"] = "running"
                    atomic_json(batch_path, batch)
                    manifest = pipeline.convert(source, max_segments=args.max_segments, retry_failed=args.retry_failed,
                                                stop_file=args.stop_file, chapter_indexes=args.chapters)
                    entry.update(status=manifest["status"], book_id=manifest["book_id"],
                                 generated=manifest["generated_this_run"], cached=manifest["cached_this_run"],
                                 exports=manifest["exports"])
                    if manifest["status"] == "failed":
                        exit_code = 1
                    if manifest["status"] == "pending" and args.stop_file is not None and args.stop_file.exists():
                        batch["updated_at"] = now()
                        atomic_json(batch_path, batch)
                        break
                    logging.info("Book %s: %s, generated=%d cached=%d", manifest["title"],
                                 manifest["status"], manifest["generated_this_run"], manifest["cached_this_run"])
                except KeyboardInterrupt:
                    entry["status"] = "pending"
                    atomic_json(batch_path, batch)
                    raise
                except Exception as exc:
                    entry.update(status="failed", error=f"{type(exc).__name__}: {exc}")
                    logging.exception("Book failed; proceeding to next input: %s", source)
                    exit_code = 1
                if not args.dry_run and not args.status:
                    batch["updated_at"] = now()
                    atomic_json(batch_path, batch)
        finally:
            pipeline.close()
        return exit_code
    except KeyboardInterrupt:
        print("Interrupted. Re-run the same command to resume.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
