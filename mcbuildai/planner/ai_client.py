"""Claude API 래퍼. API 키가 없으면 available() 이 False 이고, 호출하는 쪽이 비 AI 경로를 사용한다."""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
from pathlib import Path
from typing import Any

from mcbuildai.config import Config


class AIUnavailable(RuntimeError):
    pass


class AIClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.model = cfg.get("ai.model")
        self._client = None

    def available(self) -> bool:
        return bool(os.environ.get(self.cfg.get("ai.api_key_env"), ""))

    @property
    def client(self):
        if self._client is None:
            if not self.available():
                raise AIUnavailable(f"환경 변수 {self.cfg.get('ai.api_key_env')} 가 설정되지 않았습니다")
            import anthropic
            self._client = anthropic.Anthropic(api_key=os.environ[self.cfg.get("ai.api_key_env")])
        return self._client

    @staticmethod
    def image_block(path: str | Path) -> dict:
        p = Path(path)
        mt = mimetypes.guess_type(p.name)[0] or "image/png"
        return {"type": "image", "source": {"type": "base64", "media_type": mt,
                                            "data": base64.b64encode(p.read_bytes()).decode()}}

    def text(self, system: str, prompt: str, images: list[str | Path] | None = None,
             max_tokens: int = 2000) -> str:
        content: list[dict] = [self.image_block(i) for i in (images or [])]
        content.append({"type": "text", "text": prompt})
        resp = self.client.messages.create(model=self.model, max_tokens=max_tokens, system=system,
                                           messages=[{"role": "user", "content": content}])
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")

    def json(self, system: str, prompt: str, images: list[str | Path] | None = None,
             max_tokens: int = 2000) -> Any:
        raw = self.text(system + "\n반드시 JSON 하나만 출력한다. 설명 문장이나 코드 블록 표시는 쓰지 않는다.",
                        prompt, images, max_tokens)
        return parse_json(raw)

    def messages(self, **kwargs):
        kwargs.setdefault("model", self.model)
        return self.client.messages.create(**kwargs)


def parse_json(raw: str) -> Any:
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"(\{.*\}|\[.*\])", raw, flags=re.S)
        if not m:
            raise
        return json.loads(m.group(1))
