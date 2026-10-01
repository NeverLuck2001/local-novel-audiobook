"""Start the local audiobook interface without modifying converter defaults."""
import argparse
from pathlib import Path
import subprocess
import sys
import webbrowser

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))


def main():
    parser = argparse.ArgumentParser(description="Local audiobook browser interface")
    parser.add_argument("--port", type=int, default=7860)
    parser.add_argument("--workspace", type=Path, default=ROOT,
                        help="Resolve existing models and ASR environment from this workspace")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    local_python = args.workspace / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if local_python.is_file() and Path(sys.executable).resolve() != local_python.resolve():
        return subprocess.call([str(local_python), str(Path(__file__).resolve()), *sys.argv[1:]])
    try:
        import uvicorn
        from local_audiobook.web import create_app
    except ImportError as exc:
        print(f"The UI dependencies are missing: {exc}. Install the ui package extra.", file=sys.stderr)
        return 1
    url = f"http://127.0.0.1:{args.port}"
    print(f"Local audiobook interface: {url}")
    if not args.no_browser:
        import threading
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(ROOT, args.workspace), host="127.0.0.1", port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
