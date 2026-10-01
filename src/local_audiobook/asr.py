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
        for line in process.stdout:
            try:
                responses.put(json.loads(line))
            except ValueError:
                pass
        responses.put({"error": "ASR worker exited"})

    def start(self):
        if self.process:
            return
        worker = Path(__file__).with_name("asr_worker.py")
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_handle = self.log_path.open("a", encoding="utf-8")
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

    def _response(self):
        try:
            response = self.responses.get(timeout=self.cfg.asr_timeout)
        except queue.Empty as exc:
            self.close()
            raise TimeoutError("Local ASR worker timed out") from exc
        if response.get("error"):
            raise RuntimeError(response["error"])
        return response

    def transcribe(self, audio: Path) -> str:
        self.start()
        self.process.stdin.write(json.dumps({"audio": str(audio.resolve())}) + "\n")
        self.process.stdin.flush()
        return self._response()["text"]

    def close(self):
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
            self.process = None
        if self.log_handle:
            self.log_handle.close()
            self.log_handle = None
