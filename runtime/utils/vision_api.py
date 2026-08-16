"""
Utils - API Wrappers
Two separate backends:
  - oc2api (:31498) for text LLM
  - zero-token proxy (:3001) for vision VLM
"""

import base64
import logging
from typing import Optional

import cv2 as _cv2
import requests

from config import (
    TEXT_API_BASE, TEXT_API_KEY, TEXT_MODEL,
    VLM_BACKENDS,
)

logger = logging.getLogger("API")


class TextAPI:
    """Text-only LLM via oc2api (local inference)."""

    def __init__(self):
        self.base_url = TEXT_API_BASE.rstrip("/")
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {TEXT_API_KEY}",
            "Content-Type": "application/json",
        })

    def chat(self, message: str, model: Optional[str] = None) -> Optional[str]:
        model = model or TEXT_MODEL
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": message}],
        }

        try:
            resp = self._session.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except Exception as e:
            logger.error("Text API error: %s", e)
            return None

    def close(self):
        self._session.close()


class VisionAPI:
    """Vision VLM — round-robins across multiple backends to spread load.

    Each backend: {base_url, api_key, model}. Sessions are reused per base_url.
    """

    def __init__(self):
        self._backends = list(VLM_BACKENDS)
        self._idx = 0
        self._sessions: dict[str, requests.Session] = {}

        for b in self._backends:
            url = b["base_url"].rstrip("/")
            if url not in self._sessions:
                s = requests.Session()
                s.headers.update({
                    "Authorization": f"Bearer {b['api_key']}",
                    "Content-Type": "application/json",
                })
                self._sessions[url] = s

        names = [b["model"] for b in self._backends]
        logger.info("VisionAPI: %d backend(s) available: %s", len(self._backends), names)

    def analyze_frame(self, frame_bgr, prompt: str = "Describe what you see in this image in detail.") -> Optional[str]:
        backend = self._backends[self._idx]
        self._idx = (self._idx + 1) % len(self._backends)
        return self._call(backend, frame_bgr, prompt)

    def _call(self, backend: dict, frame_bgr, prompt: str) -> Optional[str]:
        base_url = backend["base_url"].rstrip("/")
        model = backend["model"]
        session = self._sessions[base_url]

        _, buffer = _cv2.imencode(".jpg", frame_bgr, [int(_cv2.IMWRITE_JPEG_QUALITY), 80])
        b64 = base64.b64encode(buffer).decode("utf-8")
        data_uri = f"data:image/jpeg;base64,{b64}"

        # DashScope uses /v1/chat/completions, zero-token uses /v1/chat/completions
        endpoint = f"{base_url}/v1/chat/completions"

        payload = {
            "model": model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            }],
        }

        try:
            resp = session.post(endpoint, json=payload, timeout=60)
            resp.raise_for_status()
            data = resp.json()

            # Try standard OpenAI format first
            try:
                content = data["choices"][0]["message"]["content"]
                logger.debug("VLM result from %s", model)
                return content
            except (KeyError, IndexError, TypeError):
                # zero-token proxy might use a different format — log & fallback
                logger.debug("Non-standard response from %s: %s", model,
                           str(data)[:200])
                return self._try_fallback(frame_bgr, prompt, backend)
        except Exception as e:
            logger.error("Vision API error (%s): %s", model, e)
            return self._try_fallback(frame_bgr, prompt, backend)

    def _try_fallback(self, frame_bgr, prompt: str, failed_backend: dict) -> Optional[str]:
        """Try other backends if the first one fails. No recursion — direct call."""
        for b in self._backends:
            if b is failed_backend:
                continue
            try:
                logger.info("Fallback to %s ...", b["model"])
                base_url = b["base_url"].rstrip("/")
                session = self._sessions[base_url]
                _, buffer = _cv2.imencode(".jpg", frame_bgr, [int(_cv2.IMWRITE_JPEG_QUALITY), 80])
                b64 = base64.b64encode(buffer).decode("utf-8")
                payload = {
                    "model": b["model"],
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                        ],
                    }],
                }
                resp = session.post(f"{base_url}/v1/chat/completions", json=payload, timeout=60)
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"]
            except Exception:
                continue
        return None

    def close(self):
        for s in self._sessions.values():
            s.close()
