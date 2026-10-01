"""Start the local audiobook interface without modifying converter defaults."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import webbrowser
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))


def control_queue(args) -> int:
    """Control the live queue through its API instead of editing shared files."""
    path = "/api/jobs" if args.list_jobs else "/api/queue/cancel" if args.cancel_queued else (
        "/api/jobs/" + quote(args.cancel_job, safe="") + "/cancel")
    request = Request(f"http://127.0.0.1:{args.port}{path}",
                      method="GET" if args.list_jobs else "POST")
    try:
        with urlopen(request, timeout=10) as response:
            result = json.load(response)
    except HTTPError as exc:
        try:
            message = json.load(exc).get("detail", str(exc))
        except (ValueError, OSError):
            message = str(exc)
        print(f"Queue request failed: {message}", file=sys.stderr)
        return 1
    except (URLError, TimeoutError, OSError) as exc:
        print(f"The local UI server is unavailable. Start ui.py first. {exc}", file=sys.stderr)
        return 1
    if args.list_jobs:
        result = [{key: job.get(key) for key in ("id", "title", "status", "queue_position", "archived")}
                  for job in result.get("jobs", [])]
    else:
        result = {key: result[key] for key in ("id", "status", "cancel_requested_at", "count", "cancelled")
                  if key in result}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def main():
    parser = argparse.ArgumentParser(description="Local audiobook browser interface")
    parser.add_argument("--port", type=int, default=7860)
    parser.add_argument("--workspace", type=Path, default=ROOT,
                        help="Resolve existing models and ASR environment from this workspace")
    parser.add_argument("--no-browser", action="store_true")
    controls = parser.add_mutually_exclusive_group()
    controls.add_argument("--list-jobs", action="store_true", help="List jobs from the running UI server")
    controls.add_argument("--cancel-job", metavar="JOB_ID", help="Cancel one owned UI job safely")
    controls.add_argument("--cancel-queued", action="store_true", help="Cancel queued jobs without stopping the active job")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    local_python = args.workspace / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if local_python.is_file() and Path(sys.executable).resolve() != local_python.resolve():
        return subprocess.call([str(local_python), str(Path(__file__).resolve()), *sys.argv[1:]])
    if args.list_jobs or args.cancel_job or args.cancel_queued:
        return control_queue(args)
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
