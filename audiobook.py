"""Workspace entry point; also available as the installed audiobook command."""
import sys
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))


def main():
    local_python = ROOT / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if local_python.is_file() and Path(sys.executable).resolve() != local_python.resolve():
        return subprocess.call([str(local_python), str(Path(__file__).resolve()), *sys.argv[1:]])
    from local_audiobook.cli import main as cli_main
    return cli_main()

if __name__ == "__main__":
    raise SystemExit(main())
