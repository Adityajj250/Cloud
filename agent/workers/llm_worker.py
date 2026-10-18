"""
R.I.F.T. Local LLM Worker (Phase 5)
Calls local Ollama or llama.cpp CPU HTTP instance and streams tokens back via MQTT.
"""

import json
import logging
import urllib.request
import urllib.error
from typing import Generator, Dict, Any

logger = logging.getLogger("rift.llm")

class LLMWorker:
    def __init__(self, base_url: str = "http://localhost:11434", default_model: str = "qwen2.5:3b"):
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model

    def is_available(self) -> bool:
        """Return true only when Ollama is reachable and the configured model exists."""
        try:
            return self.default_model in self._installed_models(timeout=1.5)
        except Exception:
            return False

    def _installed_models(self, timeout: float = 1.5) -> set[str]:
        req = urllib.request.Request(f"{self.base_url}/api/tags", headers={"User-Agent": "RIFT-Agent"})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return {
            str(model.get("name") or model.get("model") or "")
            for model in payload.get("models", [])
            if isinstance(model, dict)
        }

    def stream_generate(self, prompt: str, model: str = None) -> Generator[str, None, None]:
        """Stream real generated tokens from this computer's Ollama instance."""
        target_model = model or self.default_model
        try:
            installed_models = self._installed_models()
        except Exception as error:
            raise ConnectionError(
                f"Cannot connect to Ollama at {self.base_url}. Start Ollama on this computer and try again."
            ) from error
        if target_model not in installed_models:
            raise RuntimeError(f"Ollama model {target_model} is not installed. Run `ollama pull {target_model}` on this computer.")
        payload = json.dumps({
            "model": target_model,
            "prompt": prompt,
            "stream": True
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{self.base_url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=240) as response:
                for line in response:
                    if line:
                        data = json.loads(line.decode("utf-8"))
                        token = data.get("response", "")
                        if token:
                            yield token
                        if data.get("done", False):
                            break
        except urllib.error.HTTPError as error:
            detail = error.read(1000).decode("utf-8", errors="replace")
            raise RuntimeError(f"Ollama returned HTTP {error.code}: {detail}") from error
        except urllib.error.URLError as error:
            raise ConnectionError(f"Cannot reach Ollama at {self.base_url}: {error.reason}") from error
