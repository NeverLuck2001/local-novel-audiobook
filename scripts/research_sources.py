"""Fetch official source snapshots for an auditable architecture decision."""
import concurrent.futures
import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "research" / "snapshots"
REPOS = {
    "bluefermion/qwen3-tts-audiobook": ["README.md", "LICENSE", "scripts/md_to_audio.py", "scripts/validate_audio.py"],
    "Anshuman01000001/audiobook-tts": ["README.md", "LICENSE", "main.py"],
    "onigirikiller/Qwen3-TTS-WebUI": ["README.md", "LICENSE", "app.py"],
    "zeropointnine/tts-audiobook-tool": ["README.md", "LICENSE", "pyproject.toml"],
    "breezeblue-ai/breeze-tts": ["README.md", "LICENSE", "pyproject.toml"],
    "fishaudio/fish-speech": ["README.md", "LICENSE", "pyproject.toml"],
    "index-tts/index-tts": ["README.md", "LICENSE", "pyproject.toml"],
    "QwenAudio/CosyVoice": ["README.md", "LICENSE", "requirements.txt"],
    "RVC-Boss/GPT-SoVITS": ["README.md", "LICENSE"],
    "FunAudioLLM/SenseVoice": ["README.md", "LICENSE"],
}

def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "local-audiobook-research"})
    with urllib.request.urlopen(request, timeout=45) as response:
        return response.read()

def snapshot(entry):
    repo, files = entry
    dest = ROOT / repo.replace("/", "__")
    dest.mkdir(parents=True, exist_ok=True)
    try:
        info = json.loads(fetch(f"https://api.github.com/repos/{repo}"))
        branch = info["default_branch"]
        tree = json.loads(fetch(f"https://api.github.com/repos/{repo}/git/trees/{branch}?recursive=1"))
        (dest / "repository.json").write_text(json.dumps({k:info.get(k) for k in ["full_name", "pushed_at", "updated_at", "archived", "license", "default_branch"]}, indent=2), encoding="utf-8")
        (dest / "tree.json").write_text(json.dumps(tree, indent=2), encoding="utf-8")
        for name in files:
            try:
                data = fetch(f"https://raw.githubusercontent.com/{repo}/{tree['sha']}/{name}")
                target = dest / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            except Exception as exc:
                print(f"Skipped {repo}/{name}: {exc}", flush=True)
        print(f"Fetched {repo} at {tree['sha']}", flush=True)
    except Exception as exc:
        print(f"Failed {repo}: {exc}", flush=True)

if __name__ == "__main__":
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(snapshot, REPOS.items()))
