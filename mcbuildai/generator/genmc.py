"""genMC (https://genmc.app) 연동.

genMC 는 브라우저 스튜디오 탭에서 AI 에이전트가 블록 단위로 건축물을 짓는 서비스다.
MCP 서버(https://genmc.app/api/mcp, Streamable HTTP)에 접속해 스튜디오 탭과 페어링하고,
genmc_call 로 스튜디오 도구(build_preview_program, build_render_scene, build_export 등)를 호출한다.

동작 조건
- genMC 빌드 스튜디오(https://genmc.app/build) 탭이 열려 있어야 한다.
- 처음 한 번 스튜디오에 표시된 페어링 코드(10분 유효)를 대시보드 AI Studio 에 입력한다.
  이후에는 저장된 resumeKey 로 같은 탭에 다시 연결한다.

설계 흐름
1) Claude API 가 에이전트가 되어 genMC 도구로 건축물을 설계한다 (GenMCDesigner).
2) 완성 후 이 모듈이 직접 build_export(mcfunction) 로 블록 데이터를 받아 VoxelModel 로 변환한다.
3) build_render_scene 로 미리보기 PNG 를 받는다.
"""
from __future__ import annotations

import base64
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcbuildai.config import Config
from mcbuildai.db import DB

PROTOCOL_VERSION = "2025-06-18"


class GenMCError(RuntimeError):
    pass


class MCPHttpClient:
    """MCP Streamable HTTP 최소 구현 (JSON 응답과 SSE 응답 모두 처리)."""

    def __init__(self, url: str, timeout: float = 120.0):
        self.url = url
        self.timeout = timeout
        self.session_id: str | None = None
        self._id = 0
        self.server_info: dict = {}

    def _post(self, payload: dict) -> tuple[dict | None, dict]:
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                   "User-Agent": "mcbuildai/1.0"}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
            headers["MCP-Protocol-Version"] = PROTOCOL_VERSION
        req = urllib.request.Request(self.url, data=json.dumps(payload).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                sid = resp.headers.get("Mcp-Session-Id")
                if sid:
                    self.session_id = sid
                ctype = resp.headers.get("Content-Type", "")
                body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            raise GenMCError(f"genMC HTTP {e.code}: {e.read().decode('utf-8', errors='replace')[:500]}") from e
        except urllib.error.URLError as e:
            raise GenMCError(f"genMC 접속 실패: {e.reason}") from e
        if "id" not in payload:
            return None, {}
        if "text/event-stream" in ctype:
            for block in body.split("\n\n"):
                data = "\n".join(l[5:].lstrip() for l in block.splitlines() if l.startswith("data:"))
                if not data:
                    continue
                try:
                    msg = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if msg.get("id") == payload["id"]:
                    return msg, {}
            raise GenMCError("genMC SSE 응답에서 결과를 찾지 못했습니다")
        return (json.loads(body) if body.strip() else None), {}

    def request(self, method: str, params: dict | None = None) -> Any:
        self._id += 1
        msg, _ = self._post({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}})
        if msg is None:
            raise GenMCError(f"{method}: 빈 응답")
        if "error" in msg:
            raise GenMCError(f"{method}: {msg['error']}")
        return msg.get("result")

    def notify(self, method: str, params: dict | None = None) -> None:
        self._post({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def initialize(self) -> None:
        res = self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION, "capabilities": {},
            "clientInfo": {"name": "mcbuildai", "version": "1.0"}})
        self.server_info = res or {}
        self.notify("notifications/initialized")

    def list_tools(self) -> list[dict]:
        tools, cursor = [], None
        while True:
            res = self.request("tools/list", {"cursor": cursor} if cursor else {})
            tools += res.get("tools", [])
            cursor = res.get("nextCursor")
            if not cursor:
                return tools

    def call_tool(self, name: str, arguments: dict) -> dict:
        return self.request("tools/call", {"name": name, "arguments": arguments})


def result_text(result: dict) -> str:
    return "\n".join(c.get("text", "") for c in (result or {}).get("content", []) if c.get("type") == "text")


def result_images(result: dict) -> list[tuple[str, bytes]]:
    out = []
    for c in (result or {}).get("content", []):
        if c.get("type") == "image" and c.get("data"):
            out.append((c.get("mimeType", "image/png"), base64.b64decode(c["data"])))
    return out


def result_blobs(result: dict) -> list[tuple[str, bytes]]:
    """embedded resource(blob) 로 돌아오는 파일 데이터."""
    out = []
    for c in (result or {}).get("content", []):
        res = c.get("resource") if c.get("type") == "resource" else None
        if res and res.get("blob"):
            out.append((res.get("uri", ""), base64.b64decode(res["blob"])))
    sc = (result or {}).get("structuredContent") or {}
    for key in ("base64", "data", "fileBase64"):
        if isinstance(sc.get(key), str):
            try:
                out.append((sc.get("filename", key), base64.b64decode(sc[key])))
            except Exception:
                pass
    return out


def _find_key(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = _find_key(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_key(v, key)
            if r is not None:
                return r
    return None


def extract_field(result: dict, key: str) -> Any:
    """구조화 결과 또는 텍스트(JSON/문장)에서 필드를 찾는다."""
    sc = (result or {}).get("structuredContent")
    if sc is not None:
        v = _find_key(sc, key)
        if v is not None:
            return v
    text = result_text(result)
    try:
        v = _find_key(json.loads(text), key)
        if v is not None:
            return v
    except Exception:
        pass
    m = re.search(rf'"?{re.escape(key)}"?\s*[:=]\s*"?([A-Za-z0-9_\-\.]+)"?', text)
    return m.group(1) if m else None


class GenMC:
    """페어링/재연결을 관리하고 스튜디오 도구를 호출한다."""

    KV_RESUME = "genmc_resume_key"

    def __init__(self, cfg: Config, db: DB):
        self.cfg = cfg
        self.db = db
        self.url = cfg.get("genmc.mcp_url", "https://genmc.app/api/mcp")
        self.mcp = MCPHttpClient(self.url)
        self.tools: list[dict] = []
        self._ready = False

    @property
    def resume_key(self) -> str | None:
        return self.db.kv_get(self.KV_RESUME)

    def connect(self) -> None:
        if self._ready:
            return
        self.mcp.initialize()
        self.tools = self.mcp.list_tools()
        self._ready = True

    def pair(self, code: str) -> str:
        self.connect()
        res = self.mcp.call_tool("genmc_pair", {"code": code.strip()})
        if res.get("isError"):
            raise GenMCError(result_text(res) or "페어링 실패")
        key = extract_field(res, "resumeKey")
        if not key:
            raise GenMCError("페어링 응답에서 resumeKey 를 찾지 못했습니다: " + result_text(res)[:300])
        self.db.kv_set(self.KV_RESUME, key)
        self.db.log("INFO", "genmc", "genMC 스튜디오 탭과 페어링 완료")
        return key

    def resume(self) -> None:
        self.connect()
        key = self.resume_key
        if not key:
            raise GenMCError("genMC 페어링이 필요합니다. 대시보드 AI Studio 에서 페어링 코드를 입력하세요.")
        res = self.mcp.call_tool("genmc_pair", {"resumeKey": key})
        if res.get("isError"):
            raise GenMCError("genMC 재연결 실패 (스튜디오 탭이 열려 있는지 확인): " + result_text(res)[:300])
        self.refresh_tools()

    def refresh_tools(self) -> None:
        """genmc_status 는 에이전트 도구 목록을 갱신한다."""
        self.mcp.call_tool("genmc_status", {"resumeKey": self.resume_key} if self.resume_key else {})
        self.tools = self.mcp.list_tools()

    def status(self) -> str:
        self.connect()
        res = self.mcp.call_tool("genmc_status", {"resumeKey": self.resume_key} if self.resume_key else {})
        return result_text(res)

    def call(self, name: str, input: dict | None = None) -> dict:
        """스튜디오 도구 호출 (genmc_call 경유)."""
        args = {"name": name, "input": input or {}}
        if self.resume_key:
            args["resumeKey"] = self.resume_key
        res = self.mcp.call_tool("genmc_call", args)
        if res.get("isError"):
            raise GenMCError(f"{name}: {result_text(res)[:500]}")
        return res

    def raw_tool(self, name: str, arguments: dict) -> dict:
        args = dict(arguments)
        if name in ("genmc_call", "genmc_status") and self.resume_key and "resumeKey" not in args:
            args["resumeKey"] = self.resume_key
        return self.mcp.call_tool(name, args)

    # ---------------- 상태/가드 ----------------
    def guards(self) -> dict:
        st = self.call("build_get_state")
        out = {}
        for k in ("expectedProjectId", "expectedRevision", "expectedContextRevision"):
            src = {"expectedProjectId": "projectId", "expectedRevision": "revision",
                   "expectedContextRevision": "contextRevision"}[k]
            v = extract_field(st, src)
            if v is not None:
                out[k] = int(v) if str(v).isdigit() else v
        if "expectedRevision" not in out:
            raise GenMCError("build_get_state 에서 revision 을 찾지 못했습니다: " + result_text(st)[:300])
        return out

    # ---------------- 내보내기 ----------------
    def export_model(self, mc_version: str, work_dir: Path):
        """현재 프로젝트를 블록 모델로 가져온다. mcfunction 우선, 실패 시 schem 시도."""
        from .schematic import read_mcfunction, read_schem
        g = self.guards()
        errors = []
        try:
            res = self.call("build_export", {"format": "mcfunction", "version": mc_version,
                                             "expectedRevision": g["expectedRevision"]})
            text = result_text(res)
            if "setblock" in text or "fill " in text:
                (work_dir / "genmc_export.mcfunction").write_text(text, encoding="utf-8")
                model, skipped = read_mcfunction(text)
                if len(model):
                    return model, skipped
            errors.append("mcfunction: 명령이 없음")
        except GenMCError as e:
            errors.append(f"mcfunction: {e}")
        try:
            res = self.call("build_export", {"format": "schem", "version": mc_version,
                                             "expectedRevision": g["expectedRevision"]})
            for name, blob in result_blobs(res):
                p = work_dir / "genmc_export.schem"
                p.write_bytes(blob)
                return read_schem(p), []
            errors.append("schem: 파일 데이터가 응답에 포함되지 않음")
        except GenMCError as e:
            errors.append(f"schem: {e}")
        raise GenMCError("genMC 내보내기 실패: " + " / ".join(errors))

    def render_png(self, out: Path, view: dict | None = None) -> Path | None:
        g = self.guards()
        args = {"expectedRevision": g["expectedRevision"]}
        if view:
            args["view"] = view
        res = self.call("build_render_scene", args)
        imgs = result_images(res)
        if not imgs:
            return None
        out.write_bytes(imgs[0][1])
        return out

    def components(self) -> str:
        return result_text(self.call("build_get_components"))

    def materials(self) -> str:
        return result_text(self.call("build_get_materials"))


# ---------------------------------------------------------------------------
# Claude 에이전트가 genMC 도구로 설계
# ---------------------------------------------------------------------------
DESIGNER_SYSTEM = """너는 genMC 빌드 스튜디오에 연결된 Minecraft 건축 디자이너다.
genmc_call 도구로 스튜디오 도구를 호출한다. 모든 genmc_call 에는 name 과 input 을 넣는다 (resumeKey 는 자동으로 붙는다).

작업 순서
1. genmc_call build_get_capabilities 로 프로그래밍 API 와 한계를 읽는다. 필요하면 genmc_docs 로 agent-guide 를 읽는다.
2. genmc_call build_get_state 로 projectId, revision, contextRevision 을 확인한다 (이후 가드 파라미터에 사용).
3. build_create_project 로 지정된 이름의 새 프로젝트를 만든다.
4. build_preview_program 으로 voxel JavaScript 를 실행해 건축물을 만든다. 구역마다 voxel.part 로 이름을 붙인다
   (foundation, walls, floors, windows, roof, interior, decor, terrain, landscape 등 영문 소문자).
5. build_render_scene 로 결과를 보고 고친 다음 build_commit_preview 로 확정한다.
6. 설계 조건(크기, 양식, 재료, 지형)을 지킨다. 지면은 y=0 이고 건물 중심은 x=0, z=0 이다.
7. 중력 블록(모래, 자갈, 콘크리트 가루)은 공중에 두지 않는다. 판유리와 울타리 대신 유리 블록과 담장 블록을 쓴다.
8. 다 끝나면 마지막 응답 맨 끝 줄에 JSON 한 줄을 쓴다:
   {"done": true, "projectName": "...", "parts": ["..."], "summary": "한국어 한두 문장"}
"""


@dataclass
class DesignResult:
    model: Any
    preview_png: Path | None
    parts: list[str] = field(default_factory=list)
    summary: str = ""
    skipped_lines: int = 0
    transcript_path: Path | None = None


class GenMCDesigner:
    def __init__(self, cfg: Config, db: DB, ai, genmc: GenMC | None = None):
        self.cfg = cfg
        self.db = db
        self.ai = ai
        self.genmc = genmc or GenMC(cfg, db)

    def _anthropic_tools(self) -> list[dict]:
        tools = []
        for t in self.genmc.tools:
            if t["name"] in ("genmc_pair", "genmc_open_studio"):
                continue
            schema = dict(t.get("inputSchema") or {"type": "object", "properties": {}})
            # resumeKey 는 이 클라이언트가 붙이므로 모델에게 필수로 요구하지 않는다
            if "required" in schema:
                schema["required"] = [r for r in schema["required"] if r != "resumeKey"]
            tools.append({"name": t["name"], "description": (t.get("description") or "")[:1024],
                          "input_schema": schema})
        return tools

    def _openai_tools(self) -> list[dict]:
        """genMC 는 MCP 표준 서버라 Claude뿐 아니라 ChatGPT/Codex 등 tool-calling 가능한 모델이면
        다 에이전트로 쓸 수 있다 (https://genmc.app/docs/connect-agent). OpenRouter 는 OpenAI 호환
        함수 호출 스키마라 여기서 변환해서 그대로 쓴다 (전용 Anthropic 키가 따로 필요 없음)."""
        out = []
        for t in self._anthropic_tools():
            out.append({"type": "function", "function": {
                "name": t["name"], "description": t["description"], "parameters": t["input_schema"]}})
        return out

    def _call_genmc_tool(self, name: str, args: dict) -> tuple[str, bool, list[tuple[str, bytes]]]:
        try:
            res = self.genmc.raw_tool(name, args)
            return (result_text(res) or "(빈 결과)"), bool(res.get("isError")), result_images(res)[:2]
        except GenMCError as e:
            return str(e), True, []

    def design(self, brief: str, project_name: str, work_dir: Path, images: list[Path] | None = None,
               max_turns: int = 60, base_project: str | None = None) -> DesignResult:
        work_dir.mkdir(parents=True, exist_ok=True)
        self.genmc.resume()
        text = f"프로젝트 이름: {project_name}\n\n설계 조건:\n{brief}"
        if base_project:
            text += (f"\n\n기존 프로젝트 '{base_project}' 를 바탕으로 변형한다. build_list_projects 와 build_select_project 로 "
                     "열어 build_get_recipe 로 프로그램을 읽은 뒤, 새 프로젝트를 만들어 변형한 프로그램을 실행한다.")
        if self.ai.provider == "openrouter":
            final_text, transcript = self._design_loop_openai(text, images, max_turns)
        else:
            final_text, transcript = self._design_loop_anthropic(text, images, max_turns)
        tpath = work_dir / "genmc_transcript.json"
        tpath.write_text(json.dumps(transcript, ensure_ascii=False, indent=1), encoding="utf-8")
        summary, parts = "", []
        m = re.search(r"\{[^{}]*\"done\"[^{}]*\}\s*$", final_text.strip(), flags=re.S)
        if m:
            try:
                info = json.loads(m.group(0))
                summary, parts = info.get("summary", ""), info.get("parts", [])
            except json.JSONDecodeError:
                pass
        mc_version = self.cfg.get("genmc.export_version", "1.21.1")
        model, skipped = self.genmc.export_model(mc_version, work_dir)
        preview = None
        try:
            preview = self.genmc.render_png(work_dir / "genmc_render.png")
        except GenMCError as e:
            self.db.log("WARN", "genmc", f"렌더 실패: {e}")
        return DesignResult(model=model, preview_png=preview, parts=parts, summary=summary,
                            skipped_lines=len(skipped), transcript_path=tpath)

    def _design_loop_anthropic(self, text: str, images: list[Path] | None,
                               max_turns: int) -> tuple[str, list[dict]]:
        tools = self._anthropic_tools()
        first: list[dict] = [self.ai.image_block(p) for p in (images or [])]
        first.append({"type": "text", "text": text})
        messages: list[dict] = [{"role": "user", "content": first}]
        transcript: list[dict] = []
        final_text = ""
        for turn in range(max_turns):
            resp = self.ai.messages(max_tokens=8000, system=DESIGNER_SYSTEM, tools=tools, messages=messages)
            messages.append({"role": "assistant", "content": [b.model_dump() for b in resp.content]})
            tool_results = []
            for b in resp.content:
                if b.type == "text":
                    final_text = b.text
                    transcript.append({"turn": turn, "text": b.text[:2000]})
                elif b.type == "tool_use":
                    transcript.append({"turn": turn, "tool": b.name, "input": json.dumps(b.input, ensure_ascii=False)[:2000]})
                    txt, is_error, imgs = self._call_genmc_tool(b.name, b.input)
                    content = [{"type": "text", "text": txt[:20000]}]
                    for mt, data in imgs:
                        content.append({"type": "image", "source": {"type": "base64", "media_type": mt,
                                                                    "data": base64.b64encode(data).decode()}})
                    tool_results.append({"type": "tool_result", "tool_use_id": b.id, "content": content,
                                         "is_error": is_error})
            if resp.stop_reason != "tool_use":
                break
            messages.append({"role": "user", "content": tool_results})
        return final_text, transcript

    def _design_loop_openai(self, text: str, images: list[Path] | None,
                            max_turns: int) -> tuple[str, list[dict]]:
        """OpenRouter(OpenAI 호환 함수 호출)로 genMC 를 직접 운전한다. genMC 는 특정 LLM 제공자에
        종속되지 않는 MCP 서버라서 이 경로로도 똑같이 동작한다."""
        tools = self._openai_tools()
        user_content: list[dict] = [self.ai.image_block(p, "openrouter") for p in (images or [])]
        user_content.append({"type": "text", "text": text})
        messages: list[dict] = [{"role": "system", "content": DESIGNER_SYSTEM},
                                {"role": "user", "content": user_content}]
        transcript: list[dict] = []
        final_text = ""
        for turn in range(max_turns):
            resp = self.ai.client.chat.completions.create(
                model=self.ai.model, max_tokens=8000, messages=messages, tools=tools, tool_choice="auto")
            msg = resp.choices[0].message
            messages.append(msg.model_dump(exclude_none=True))
            tool_calls = msg.tool_calls or []
            if msg.content:
                final_text = msg.content
                transcript.append({"turn": turn, "text": msg.content[:2000]})
            if not tool_calls:
                break
            for tc in tool_calls:
                args = json.loads(tc.function.arguments or "{}")
                transcript.append({"turn": turn, "tool": tc.function.name,
                                   "input": json.dumps(args, ensure_ascii=False)[:2000]})
                txt, is_error, _imgs = self._call_genmc_tool(tc.function.name, args)
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": txt[:20000]})
        return final_text, transcript
