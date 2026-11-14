"""영상 자동 편집.

결과물
1) 원본(편집 안 한) 버전 raw.mp4
   촬영 해상도(16:9) 그대로, 건축 전 장면 + 일정 속도 타임랩스 + 완성 회전 장면을 단순 연결.
   음악, 자막, 전환 효과, 색보정, 크롭 없음.
2) 편집 버전 edited.mp4 (YouTube Shorts, 1080x1920, 58초 이내)
3) 가로 편집 버전 edited_landscape.mp4 (1920x1080, 90초 이내, 선택)

편집 버전 기능
- 시작 훅 문구(슬라이드 인), 끝 댓글 유도 문구, 완성 문구
- 건축 중 블록 수 카운터, 진행 바, 배속 표시
- 속도 램프(시작/끝 느리게, 중간 빠르게) + 고속 구간 모션 블러
- 장면 길이를 음악 박자에 맞춰 전환이 박자 위에서 일어나게 함 (비트 싱크)
- 음악 클라이맥스를 완성 공개 순간에 맞춤, 공개 직전 상승음/공개 순간 타격음
- xfade 장면 전환, 공개 장면 줌, 색보정, 안개, 비네팅
- Before & After 분할 화면, 첫 장면으로 이어지는 루프 엔딩
- 썸네일 3가지 스타일
"""
from __future__ import annotations

import json
import math
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from mcbuildai.config import Config
from . import ff

TRANSITIONS_A = ["fade", "smoothup", "circleopen", "wipeleft", "slideup", "fadewhite"]
TRANSITIONS_B = ["dissolve", "radial", "smoothleft", "zoomin", "pixelize", "fade"]

GRADES = {
    "neutral": "eq=contrast=1.04:saturation=1.08",
    "vivid": "eq=contrast=1.07:saturation=1.25",
    "warm": "colorbalance=rs=0.06:gs=0.02:bs=-0.06,eq=contrast=1.05:saturation=1.15",
    "cool": "colorbalance=rs=-0.05:gs=0.0:bs=0.07,eq=contrast=1.05:saturation=1.05",
    "night": "eq=brightness=0.03:contrast=1.12:saturation=1.1,colorbalance=bs=0.06",
}

# 속도 램프: (원본 시작 비율, 원본 끝 비율, 출력 길이 비율)
RAMP = [(0.0, 0.08, 0.18), (0.08, 0.90, 0.57), (0.90, 1.0, 0.25)]


@dataclass
class EditOptions:
    variant: str = "A"
    orientation: str = "vertical"   # vertical / landscape
    ramp: bool = True
    transitions: list[str] = field(default_factory=lambda: list(TRANSITIONS_A))
    transition_sec: float = 0.4
    grade: str = "neutral"
    haze: float = 0.0
    title_pos: str = "top"          # top / center
    edit_style: str = "none"        # none / explosive / reverse
    max_sec: float = 58.0
    intro_sec: float = 2.6
    reveal_sec: float = 8.0
    env_sec: float = 1.6
    aerial_sec: float = 3.0
    interior_sec: float = 2.5
    ba_sec: float = 3.5
    loop_sec: float = 1.0
    zoom_reveal: bool = True
    beat_period: float | None = None
    # 화면 문구
    hook: str = ""
    hook_sub: str = ""
    complete_text: str = "COMPLETE"
    cta: str = ""
    cta_sub: str = ""
    before_label: str = "BEFORE"
    after_label: str = "AFTER"
    blocks_label: str = "BLOCKS"
    speed_fmt: str = "{x}x SPEED"
    block_count: int = 0
    show_counter: bool = True
    show_speed: bool = True
    motion_blur: bool = True
    loop_end: bool = True

    def variant_b(self) -> "EditOptions":
        """자동 콘텐츠 실험용 B 버전: 다른 전환/색감/구성."""
        b = EditOptions(**{**asdict(self), "variant": "B"})
        b.ramp = not self.ramp
        b.transitions = list(TRANSITIONS_B)
        b.grade = {"vivid": "warm", "warm": "vivid", "cool": "neutral", "night": "cool"}.get(self.grade, "vivid")
        b.title_pos = "center" if self.title_pos == "top" else "top"
        b.zoom_reveal = not self.zoom_reveal
        return b

    def landscape(self, max_sec: float) -> "EditOptions":
        """가로 편집본: 더 긴 공개/환경/항공 장면."""
        l = EditOptions(**{**asdict(self), "orientation": "landscape"})
        l.max_sec = max_sec
        l.intro_sec, l.reveal_sec, l.env_sec, l.aerial_sec, l.interior_sec, l.ba_sec = 3.5, 13.0, 2.6, 5.0, 4.0, 5.0
        return l


class Editor:
    def __init__(self, cfg: Config, work: Path, orientation: str = "vertical"):
        self.cfg = cfg
        self.work = work
        self.orientation = orientation
        self.tmp = work / ("edit_tmp" if orientation == "vertical" else "edit_tmp_land")
        self.tmp.mkdir(parents=True, exist_ok=True)
        if orientation == "vertical":
            self.W, self.H = int(cfg.get("video.edited_width")), int(cfg.get("video.edited_height"))
        else:
            self.W, self.H = int(cfg.get("video.landscape_width", 1920)), int(cfg.get("video.landscape_height", 1080))
        self.fps = 30

    # ------------------------------------------------------------------
    def _enc(self, crf: int | None = None) -> list[str]:
        return ["-c:v", "libx264", "-preset", self.cfg.get("video.preset"), "-crf", str(crf or 16),
                "-pix_fmt", "yuv420p", "-an"]

    def _fit(self, src_w: int, src_h: int, W: int | None = None, H: int | None = None) -> str:
        W, H = W or self.W, H or self.H
        if abs(src_w / src_h - W / H) < 0.01:
            return f"scale={W}:{H}:flags=lanczos"
        if src_w / src_h > W / H:
            cw = int(src_h * W / H) // 2 * 2
            return f"crop={cw}:{src_h}:(iw-{cw})/2:0,scale={W}:{H}:flags=lanczos"
        ch = int(src_w * H / W) // 2 * 2
        return f"crop={src_w}:{ch}:0:(ih-{ch})/2,scale={W}:{H}:flags=lanczos"

    def piece(self, src: Path, out: Path, ss: float, src_dur: float, out_dur: float, fit: bool = True,
              zoom: tuple[float, float] | None = None, reverse: bool = False, flash: bool = False,
              blur: bool = False) -> Path:
        """원본 구간 [ss, ss+src_dur] 를 out_dur 길이로 변환한 중간 파일."""
        speed = max(1e-3, src_dur / out_dur)
        w, h = ff.video_size(src, self.cfg)
        vf = [f"setpts=(PTS-STARTPTS)/{speed:.6f}"]
        if blur and speed > 4:
            # 고속 구간 모션 블러: 프레임을 줄이기 전에 인접 프레임을 섞는다
            vf.append(f"fps={self.fps * 3}")
            vf.append("tmix=frames=3:weights='1 1 1'")
        vf.append(f"fps={self.fps}")
        if fit:
            vf.append(self._fit(w, h))
            if zoom:
                n = max(1, int(out_dur * self.fps))
                z0, z1 = zoom
                vf.append(f"scale={int(self.W * 1.5)}:{int(self.H * 1.5)}")
                vf.append(f"zoompan=z='{z0}+({z1}-{z0})*on/{n}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                          f":d=1:s={self.W}x{self.H}:fps={self.fps}")
        if reverse:
            vf.append("reverse")
        if flash:
            vf.append("fade=t=in:st=0:d=0.45:color=white")
        ff.run(["-ss", f"{ss:.3f}", "-t", f"{src_dur:.3f}", "-i", str(src), "-vf", ",".join(vf),
                "-t", f"{out_dur:.3f}", *self._enc(), str(out)], self.cfg)
        return out

    def concat_plain(self, parts: list[Path], out: Path) -> Path:
        lst = self.tmp / (out.stem + "_list.txt")
        lst.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in parts), encoding="utf-8")
        ff.run(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out)], self.cfg)
        return out

    def xfade_chain(self, parts: list[Path], out: Path, transitions: list[str], T: float) -> tuple[Path, list[float], list[float]]:
        """반환: (결과, 전환 중심 시각 목록, 각 조각의 시작 시각 목록)"""
        durs = [ff.duration(p, self.cfg) for p in parts]
        starts, t = [], 0.0
        for i, d in enumerate(durs):
            starts.append(t)
            t += d - T
        if len(parts) == 1:
            shutil.copy(parts[0], out)
            return out, [], starts
        inputs = []
        for p in parts:
            inputs += ["-i", str(p)]
        chain, prev, offset, marks = [], "[0:v]", 0.0, []
        for k in range(1, len(parts)):
            offset += durs[k - 1] - T
            tr = transitions[(k - 1) % len(transitions)]
            lab = f"[v{k}]"
            chain.append(f"{prev}[{k}:v]xfade=transition={tr}:duration={T}:offset={offset:.3f}{lab}")
            prev = lab
            marks.append(offset + T / 2)
        ff.run([*inputs, "-filter_complex", ";".join(chain), "-map", prev, *self._enc(), str(out)], self.cfg)
        return out, marks, starts

    def before_after(self, before: Path, after: Path, out: Path, dur: float, opts: EditOptions) -> Path:
        """Before & After: 같은 구도의 건축 전과 완성을 나란히 (세로: 위/아래, 가로: 왼쪽/오른쪽)."""
        font = ff.filter_path(ff.default_font(self.cfg))
        bt = self.tmp / "label_before.txt"; bt.write_text(opts.before_label, encoding="utf-8")
        at = self.tmp / "label_after.txt"; at.write_text(opts.after_label, encoding="utf-8")
        vertical = self.orientation == "vertical"
        pw, ph = (self.W, self.H // 2) if vertical else (self.W // 2, self.H)
        fs = int(min(self.W, self.H) * 0.06)
        fc = []
        for i, (lab, txt) in enumerate((("b", bt), ("a", at))):
            w, h = ff.video_size(before if i == 0 else after, self.cfg)
            fc.append(f"[{i}:v]setpts=PTS-STARTPTS,fps={self.fps},{self._fit(w, h, pw, ph)},"
                      f"drawtext=fontfile='{font}':textfile='{ff.filter_path(txt)}':fontsize={fs}:"
                      f"fontcolor=white:borderw=4:bordercolor=black@0.7:x=40:y=40[{lab}]")
        if vertical:
            fc.append(f"[b][a]vstack=inputs=2,drawbox=x=0:y={ph - 3}:w={self.W}:h=6:color=white@0.9:t=fill[v]")
        else:
            fc.append(f"[b][a]hstack=inputs=2,drawbox=x={pw - 3}:y=0:w=6:h={self.H}:color=white@0.9:t=fill[v]")
        ff.run(["-i", str(before), "-i", str(after), "-filter_complex", ";".join(fc), "-map", "[v]",
                "-t", f"{dur:.3f}", *self._enc(), str(out)], self.cfg)
        return out

    # ------------------------------------------------------------------
    def build_source(self, segs: dict) -> tuple[Path, float]:
        parts = [Path(p) for p in segs["build"]]
        if len(parts) == 1:
            return parts[0], ff.duration(parts[0], self.cfg)
        out = self.concat_plain(parts, self.tmp / "build_full.mp4")
        return out, ff.duration(out, self.cfg)

    def render_raw(self, segs: dict, out: Path) -> Path:
        """편집 안 한 원본 버전."""
        raw_build = float(self.cfg.get("video.raw_build_sec", 90))
        build, bdur = self.build_source(segs)
        target = raw_build if bdur > raw_build else bdur
        pieces = []
        if segs.get("before"):
            p = Path(segs["before"]); d = ff.duration(p, self.cfg)
            pieces.append(self.piece(p, self.tmp / "raw_0.mp4", 0, d, d, fit=False))
        pieces.append(self.piece(build, self.tmp / "raw_1.mp4", 0, bdur, target, fit=False))
        if segs.get("reveal"):
            p = Path(segs["reveal"]); d = ff.duration(p, self.cfg)
            pieces.append(self.piece(p, self.tmp / "raw_2.mp4", 0, d, d, fit=False))
        tmp = self.concat_plain(pieces, self.tmp / "raw_concat.mp4")
        ff.run(["-i", str(tmp), "-c:v", "libx264", "-preset", self.cfg.get("video.preset"),
                "-crf", str(self.cfg.get("video.crf")), "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                str(out)], self.cfg)
        return out

    # ------------------------------------------------------------------
    def render_edited(self, segs: dict, title: str, opts: EditOptions, out: Path,
                      soundtrack_fn=None) -> tuple[Path, dict]:
        T = opts.transition_sec
        P = opts.beat_period if opts.beat_period and 0.25 <= opts.beat_period <= 1.2 else None

        def q(d: float, minimum: int = 1) -> float:
            """비트 싱크: 조각 길이를 (박자 x k + 전환 길이) 로 맞추면 모든 전환이 박자 위에서 시작한다."""
            if not P:
                return d
            k = max(minimum, round((d - T) / P))
            return k * P + T

        build, bdur = self.build_source(segs)
        extras = []
        for name, key, sec in (("aerial", "aerial", opts.aerial_sec), ("interior", "interior", opts.interior_sec)):
            if segs.get(key):
                extras.append((name, Path(segs[key]), q(sec)))
        envs = [(k, Path(v)) for k, v in sorted(segs.items()) if k.startswith("env_") and v][:3]
        has_ba = bool(segs.get("before") and segs.get("after"))
        teaser = opts.edit_style == "reverse"
        loop = opts.loop_end and bool(segs.get("before"))
        intro_d, reveal_d, env_d, ba_d = q(opts.intro_sec), q(opts.reveal_sec), q(opts.env_sec), q(opts.ba_sec)
        teaser_d, rewind_d = q(2.0), q(2.5)
        loop_d = opts.loop_sec
        fixed = (intro_d + reveal_d + sum(e[2] for e in extras) + env_d * len(envs) + (ba_d if has_ba else 0)
                 + (teaser_d + rewind_d if teaser else 0) + (loop_d if loop else 0))
        n_pieces = 3 + len(extras) + len(envs) + (1 if has_ba else 0) + (2 if teaser else 0) + (1 if loop else 0)
        B = max(12.0, opts.max_sec - fixed + (n_pieces - 1) * T)
        if opts.edit_style == "explosive":
            B *= 0.6
        if P:
            B = max(1, math.floor((B - T) / P)) * P + T
        pieces: list[tuple[str, Path]] = []
        idx = 0

        def nxt(name):
            nonlocal idx
            idx += 1
            return self.tmp / f"e{idx:02d}_{name}.mp4"

        reveal = Path(segs["reveal"]); rdur = ff.duration(reveal, self.cfg)
        if teaser:
            # 역방향 연출: 완성 장면을 먼저 보여주고 건축 과정을 거꾸로 감은 뒤 시작
            pieces.append(("teaser", self.piece(reveal, nxt("teaser"), max(0, rdur - teaser_d), teaser_d, teaser_d)))
            tail = min(bdur, max(3.0, bdur * 0.35))
            pieces.append(("rewind", self.piece(build, nxt("rewind"), bdur - tail, tail, rewind_d, reverse=True)))
        before = Path(segs["before"]) if segs.get("before") else build
        bd = ff.duration(before, self.cfg)
        pieces.append(("intro", self.piece(before, nxt("intro"), 0, min(bd, intro_d), intro_d)))
        # 건축 구간: 속도 램프 + 모션 블러
        ramp_bounds = None
        if opts.ramp and bdur > 10:
            outs = [f * B for _, _, f in RAMP]
            if P:
                outs[0] = max(P, round(outs[0] / P) * P)
                outs[1] = max(P, round(outs[1] / P) * P)
                outs[2] = max(0.5, B - outs[0] - outs[1])
            subs = []
            for i, ((a, b, _), od) in enumerate(zip(RAMP, outs)):
                subs.append(self.piece(build, self.tmp / f"build_ramp_{i}.mp4", a * bdur, (b - a) * bdur, od,
                                       blur=opts.motion_blur and i == 1))
            bpath = self.concat_plain(subs, nxt("build"))
            ramp_bounds = outs
            speed_x = round(((RAMP[1][1] - RAMP[1][0]) * bdur) / outs[1])
        else:
            bpath = self.piece(build, nxt("build"), 0, bdur, B, blur=opts.motion_blur)
            speed_x = round(bdur / B)
        pieces.append(("build", bpath))
        r_src = min(rdur, reveal_d * 1.5)
        pieces.append(("reveal", self.piece(reveal, nxt("reveal"), 0, r_src, reveal_d,
                                            zoom=(1.0, 1.18) if opts.zoom_reveal else None,
                                            flash=opts.edit_style == "explosive")))
        for k, p in envs:
            d = ff.duration(p, self.cfg)
            pieces.append((k, self.piece(p, nxt(k), 0, min(d, env_d), env_d)))
        for name, p, sec in extras:
            d = ff.duration(p, self.cfg)
            pieces.append((name, self.piece(p, nxt(name), 0, min(d, sec * 1.5), sec)))
        if has_ba:
            pieces.append(("before_after", self.before_after(Path(segs["before"]), Path(segs["after"]),
                                                              nxt("ba"), ba_d, opts)))
        if loop:
            # 루프 엔딩: 첫 장면과 같은 프레임으로 끝나 처음으로 자연스럽게 이어진다
            pieces.append(("loop", self.piece(before, nxt("loop"), 0, loop_d, loop_d)))
        trans = list(opts.transitions)
        chain, marks, starts_l = self.xfade_chain([p for _, p in pieces], self.tmp / "chain.mp4", trans, T)
        total = ff.duration(chain, self.cfg)
        starts = {name: s for (name, _), s in zip(pieces, starts_l)}
        reveal_t = starts["reveal"]
        b_start = starts["build"]
        b_len = ff.duration(bpath, self.cfg)
        build_span = (b_start, b_start + b_len)
        overlays = self._overlay_filters(opts, title, starts, total, build_span, ramp_bounds, speed_x,
                                         end_t=starts.get("before_after", starts.get("loop", total - 3)))
        info = {"total": round(total, 2), "reveal_t": round(reveal_t, 2), "pieces": list(starts.keys()),
                "variant": opts.variant, "orientation": opts.orientation, "speed_x": speed_x,
                "beat_period": P, "options": asdict(opts)}
        audio_args: list[str] = []
        inputs = ["-i", str(chain)]
        if soundtrack_fn:
            wav = self.tmp / "soundtrack.wav"
            ainfo = soundtrack_fn(total, reveal_t, build_span, marks, wav)
            info["audio"] = ainfo
            if ainfo.get("has_audio"):
                tl = float(self.cfg.get("video.target_lufs"))
                norm = ff.loudnorm_two_pass(wav, self.tmp / "soundtrack_norm.wav", tl, self.cfg)
                inputs += ["-i", str(norm)]
                audio_args = ["-map", "1:a", "-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
        bar_h = max(8, self.H // 160)
        inputs += ["-f", "lavfi", "-i", f"color=c=0xFFD166:s={self.W}x{bar_h}:r={self.fps}"]
        bar_idx = 2 if audio_args else 1
        fc = self._compose_graph(opts, overlays, bar_idx, bar_h, build_span, ramp_bounds)
        ff.run([*inputs, "-filter_complex", fc, "-map", "[v]", *audio_args,
                "-c:v", "libx264", "-preset", self.cfg.get("video.preset"), "-crf", str(self.cfg.get("video.crf")),
                "-pix_fmt", "yuv420p", "-r", str(self.fps), "-movflags", "+faststart", "-t", f"{total:.3f}",
                str(out)], self.cfg)
        return out, info

    # ------------------------------------------------------------------
    def _progress_expr(self, build_span, ramp_bounds) -> str:
        """건축 진행률 p(t) (0~1). 속도 램프 구간별 원본 진행률을 이어 붙인 식."""
        S, E = build_span
        if not ramp_bounds:
            return f"clip((t-{S:.3f})/{max(0.01, E - S):.3f},0,1)"
        a, b, c = ramp_bounds
        u = f"(t-{S:.3f})"
        return (f"clip(if(lt({u},{a:.3f}),0.08*{u}/{a:.3f},if(lt({u},{a + b:.3f}),0.08+0.82*({u}-{a:.3f})/{b:.3f},"
                f"0.90+0.10*({u}-{a + b:.3f})/{c:.3f})),0,1)")

    def _overlay_filters(self, opts: EditOptions, title: str, starts: dict, total: float, build_span,
                         ramp_bounds, speed_x: int, end_t: float) -> list[str]:
        font = ff.filter_path(ff.default_font(self.cfg))
        W, H = self.W, self.H
        vertical = self.orientation == "vertical"
        base = min(W, H)
        out = []

        def txt(name: str, text: str) -> str:
            p = self.tmp / f"{name}.txt"
            p.write_text(text, encoding="utf-8")
            return ff.filter_path(p)

        def fade_alpha(t0: float, t1: float, f: float = 0.35) -> str:
            return (f"if(lt(t,{t0:.3f}),0,if(lt(t,{t0 + f:.3f}),(t-{t0:.3f})/{f},"
                    f"if(lt(t,{t1 - f:.3f}),1,if(lt(t,{t1:.3f}),({t1:.3f}-t)/{f},0))))")

        def slide_x(t0: float) -> str:
            # 왼쪽에서 미끄러져 들어오는 움직임 (ease-out)
            return f"(w-text_w)/2-{int(W * 0.08)}*pow(1-min(1,max(0,(t-{t0:.3f})/0.45)),3)"

        def draw(name, text, size, y, t0, t1, x=None, color="white", border=6):
            return (f"drawtext=fontfile='{font}':textfile='{txt(name, text)}':fontsize={size}:fontcolor={color}:"
                    f"borderw={border}:bordercolor=black@0.75:x='{x or slide_x(t0)}':y='{y}':"
                    f"alpha='{fade_alpha(t0, t1)}':enable='between(t,{t0:.3f},{t1:.3f})'")

        # 시작 훅
        i0 = starts["intro"]
        it1 = starts.get("build", i0 + opts.intro_sec) + 0.6
        hook = opts.hook or title
        hs = int(base * (0.085 if len(hook) <= 18 else 0.085 * 18 / len(hook)))
        hy = f"h*{0.12 if opts.title_pos == 'top' or not vertical else 0.40}"
        out.append(draw("hook", hook, max(36, hs), hy, i0 + 0.1, it1))
        sub = opts.hook_sub or (title if opts.hook else "")
        if sub:
            out.append(draw("hook_sub", sub, int(base * 0.045), f"{hy}+{int(hs * 1.35)}", i0 + 0.3, it1,
                            color="0xFFE7A0"))
        # 건축 중 카운터 / 배속
        S, E = build_span
        if opts.show_counter and opts.block_count:
            counter = f"%{{eif:{opts.block_count}*{self._progress_expr(build_span, ramp_bounds)}:d}} {opts.blocks_label}"
            cp = self.tmp / "counter.txt"
            cp.write_text(counter, encoding="utf-8")
            cy = f"h*{0.80 if vertical else 0.84}"
            out.append(f"drawtext=fontfile='{font}':textfile='{ff.filter_path(cp)}':expansion=normal:"
                       f"fontsize={int(base * 0.07)}:fontcolor=white:borderw=6:bordercolor=black@0.75:"
                       f"x=(w-text_w)/2:y='{cy}':alpha='{fade_alpha(S + 0.2, E)}':enable='between(t,{S:.3f},{E:.3f})'")
        if opts.show_speed and speed_x >= 2:
            out.append(draw("speed", opts.speed_fmt.format(x=speed_x), int(base * 0.035), "h*0.05", S + 0.3, E,
                            x="w-text_w-40", color="0xFFE7A0", border=4))
        # 완성 문구
        r0 = starts["reveal"]
        out.append(draw("complete", opts.complete_text, int(base * 0.11), f"h*{0.76 if vertical else 0.70}",
                        r0 + 0.25, r0 + min(3.6, opts.reveal_sec - 0.5)))
        # 끝 댓글 유도
        if opts.cta:
            cs = int(base * (0.06 if len(opts.cta) <= 28 else 0.06 * 28 / len(opts.cta)))
            cy = "h*0.36" if vertical else "h*0.40"
            out.append(draw("cta", opts.cta, max(30, cs), cy, end_t + 0.2, total))
            if opts.cta_sub:
                out.append(draw("cta_sub", opts.cta_sub, int(base * 0.04), f"{cy}+{int(cs * 1.4)}", end_t + 0.4, total,
                                color="0xFFE7A0"))
        return out

    def _compose_graph(self, opts: EditOptions, overlays: list[str], bar_idx: int, bar_h: int, build_span,
                       ramp_bounds) -> str:
        S, E = build_span
        vchain = [GRADES.get(opts.grade, GRADES["neutral"]), "vignette=PI/6"]
        parts = []
        if opts.haze > 0:
            parts.append(f"[0:v]split[a][b];[b]gblur=sigma=18,eq=brightness=0.06[hz];"
                         f"[a][hz]blend=all_mode=screen:all_opacity={opts.haze:.2f},{','.join(vchain)}[g]")
        else:
            parts.append(f"[0:v]{','.join(vchain)}[g]")
        if opts.show_counter:
            # 진행 바: 화면 아래 전체 폭, 왼쪽 밖에서 들어오며 진행률만큼 보인다
            p = self._progress_expr(build_span, ramp_bounds)
            parts.append(f"[g]drawbox=x=0:y={self.H - bar_h}:w={self.W}:h={bar_h}:color=black@0.45:t=fill:"
                         f"enable='between(t,{S:.3f},{E:.3f})'[g2]")
            parts.append(f"[{bar_idx}:v]format=rgba,colorchannelmixer=aa=0.92[bar]")
            parts.append(f"[g2][bar]overlay=x='-{self.W}+{self.W}*{p}':y={self.H - bar_h}:eof_action=pass:"
                         f"shortest=0:enable='between(t,{S:.3f},{E:.3f})'[g3]")
            last = "g3"
        else:
            last = "g"
        parts.append(f"[{last}]{','.join(overlays)}[v]" if overlays else f"[{last}]null[v]")
        return ";".join(parts)

    def preview(self, src: Path, out: Path) -> Path:
        ff.run(["-i", str(src), "-vf", "scale=-2:960" if self.orientation == "vertical" else "scale=960:-2",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-c:a", "aac", "-b:a", "96k", str(out)],
               self.cfg)
        return out

    def cleanup(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 썸네일 (3가지 스타일)
# ---------------------------------------------------------------------------
THUMB_STYLES = ["bold", "banner", "split"]


def _cover(img: Image.Image, size) -> Image.Image:
    tw, th = size
    sw, sh = img.size
    scale = max(tw / sw, th / sh)
    im = img.resize((int(sw * scale) + 1, int(sh * scale) + 1), Image.LANCZOS)
    x0 = (im.width - tw) // 2; y0 = (im.height - th) // 2
    return im.crop((x0, y0, x0 + tw, y0 + th))


def _fit_font(draw, text, font_path, start, max_w, stroke_ratio=14):
    fs = start
    while True:
        font = ImageFont.truetype(font_path, fs)
        sw = max(2, fs // stroke_ratio)
        bb = draw.textbbox((0, 0), text, font=font, stroke_width=sw)
        if bb[2] - bb[0] <= max_w or fs <= 20:
            return font, sw, bb
        fs = int(fs * 0.92)


def render_thumbnail(after_img: Image.Image, before_img: Image.Image | None, title: str, subtitle: str,
                     size, style: str, font_path: str) -> Image.Image:
    tw, th = size
    im = _cover(after_img, size)
    d = ImageDraw.Draw(im)
    if style == "split" and before_img is not None:
        b = _cover(before_img, size).convert("RGB")
        mask = Image.new("L", size, 0)
        md = ImageDraw.Draw(mask)
        md.polygon([(0, 0), (int(tw * 0.42), 0), (int(tw * 0.30), th), (0, th)], fill=255)
        im.paste(b, (0, 0), mask)
        d = ImageDraw.Draw(im)
        d.line([(int(tw * 0.42), 0), (int(tw * 0.30), th)], fill=(255, 255, 255), width=max(6, tw // 160))
    if style == "banner":
        bh = int(th * (0.22 if tw > th else 0.14))
        overlay = Image.new("RGBA", (tw, bh), (15, 18, 25, 210))
        im.paste(overlay, (0, 0), overlay)
        font, sw, bb = _fit_font(d, title, font_path, int(bh * 0.55), tw * 0.92)
        d.text(((tw - (bb[2] - bb[0])) // 2, (bh - (bb[3] - bb[1])) // 2 - bb[1]), title, font=font,
               fill=(255, 231, 160), stroke_width=0)
    else:
        start = int(th * (0.13 if tw > th else 0.065))
        font, sw, bb = _fit_font(d, title, font_path, start, tw * 0.92)
        y = int(th * (0.62 if subtitle else 0.72)) if tw > th else int(th * 0.12)
        d.text(((tw - (bb[2] - bb[0])) // 2, y), title, font=font, fill=(255, 255, 255), stroke_width=sw,
               stroke_fill=(0, 0, 0))
        if subtitle:
            f2, sw2, bb2 = _fit_font(d, subtitle, font_path, int(start * 0.5), tw * 0.9)
            d.text(((tw - (bb2[2] - bb2[0])) // 2, y + (bb[3] - bb[1]) + int(start * 0.35)), subtitle, font=f2,
                   fill=(255, 231, 160), stroke_width=sw2, stroke_fill=(0, 0, 0))
    return im


def make_thumbnails(cfg: Config, after_src: Path, t: float, title: str, out_dir: Path,
                    before_src: Path | None = None, style: str = "bold", subtitle: str = "") -> dict:
    """thumb.jpg(1280x720, 업로드용 = 선택 스타일), thumb_vertical.jpg, thumb_<스타일>.jpg 전부."""
    frame = ff.extract_frame(after_src, t, out_dir / "frame_after.jpg", cfg)
    after_img = Image.open(frame).convert("RGB")
    before_img = None
    if before_src and Path(before_src).exists():
        bf = ff.extract_frame(Path(before_src), 1.0, out_dir / "frame_before.jpg", cfg)
        before_img = Image.open(bf).convert("RGB")
    font_path = ff.default_font(cfg)
    res = {}
    for st in THUMB_STYLES:
        if st == "split" and before_img is None:
            continue
        im = render_thumbnail(after_img, before_img, title, subtitle, (1280, 720), st, font_path)
        p = out_dir / f"thumb_{st}.jpg"
        im.save(p, quality=90)
        res[f"thumb_{st}.jpg"] = str(p)
    chosen = style if f"thumb_{style}.jpg" in res else "bold"
    shutil.copy(res[f"thumb_{chosen}.jpg"], out_dir / "thumb.jpg")
    res["thumb.jpg"] = str(out_dir / "thumb.jpg")
    res["style"] = chosen
    v = render_thumbnail(after_img, before_img, title, subtitle, (1080, 1920), "bold" if chosen == "split" else chosen,
                         font_path)
    v.save(out_dir / "thumb_vertical.jpg", quality=90)
    res["thumb_vertical.jpg"] = str(out_dir / "thumb_vertical.jpg")
    for f in ("frame_after.jpg", "frame_before.jpg"):
        (out_dir / f).unlink(missing_ok=True)
    return res
