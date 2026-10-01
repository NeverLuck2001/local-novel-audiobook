"""Persistent local ASR worker in a separate, dependency-compatible environment."""
import json
from pathlib import Path
import queue
import subprocess
import threading

from .config import QualityConfig
from .util import model_identity


class ASRWorker:
    def __init__(self, cfg: QualityConfig, log_path: Path):
        if not cfg.asr_python.is_file():
            raise FileNotFoundError(f"ASR environment missing: {cfg.asr_python}. Run scripts/setup-asr.ps1")
        self.cfg = cfg
        self.identity = model_identity(cfg.asr_model_path)
        self.process = None
        self.responses = queue.Queue()
        self.log_path = log_path
        self.log_handle = None

    @staticmethod
    def _read(process, responses):
        try:
            for line in process.stdout:
                try:
                    responses.put(json.loads(line))
                except ValueError:
                    pass
        except (OSError, ValueError):
            pass
        finally:
            responses.put({"error": "ASR worker exited"})

    def start(self):
        if self.process and self.process.poll() is None:
            return
        self.close()
        worker = Path(__file__).with_name("asr_worker.py")
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_handle = self.log_path.open("a", encoding="utf-8")
        try:
            self.process = subprocess.Popen([str(self.cfg.asr_python), "-u", str(worker),
                                             str(self.cfg.asr_model_path)], stdin=subprocess.PIPE,
                                            stdout=subprocess.PIPE, stderr=self.log_handle,
                                            text=True, encoding="utf-8", bufsize=1,
                                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.responses = queue.Queue()
            threading.Thread(target=self._read, args=(self.process, self.responses), daemon=True).start()
            response = self._response()
            if not response.get("ready"):
                raise RuntimeError(f"ASR worker failed: {response}")
        except Exception:
            self.close()
            raise

    def _response(self):
        try:
            response = self.responses.get(timeout=self.cfg.asr_timeout)
        except queue.Empty as exc:
            self.close()
            raise TimeoutError("Local ASR worker timed out") from exc
        if not isinstance(response, dict) or response.get("error"):
            self.close()
            raise RuntimeError(response.get("error") if isinstance(response, dict) else "Invalid ASR response")
        return response

    def transcribe(self, audio: Path) -> str:
        self.start()
        try:
            self.process.stdin.write(json.dumps({"audio": str(audio.resolve())}) + "\n")
            self.process.stdin.flush()
            return self._response()["text"]
        except Exception:
            self.close()
            raise

    def close(self):
        if self.process:
            process = self.process
            try:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            finally:
                for stream in (process.stdin, process.stdout):
                    if stream:
                        stream.close()
                self.process = None
        if self.log_handle:
            self.log_handle.close()
            self.log_handle = None
