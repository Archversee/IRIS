"""Make the avatar's lip-sync clips for MAVIS's fixed sentences (voice/lipsync.py).

Run from MAVIS/ai-instructor with its venv, one step at a time:

  python tools/avatar_lines.py collect   # list every fixed sentence -> avatar/lines/lines.json
  python tools/avatar_lines.py audio     # each sentence in MAVIS's own voice -> <key>.wav
  python tools/avatar_lines.py still     # a still of the avatar from idle.mp4 -> _source.png
  python tools/avatar_lines.py video     # the lip-sync tool on each WAV -> <key>.mp4
  python tools/avatar_lines.py status    # how many are done

audio and video skip what's already made and finish with `build` (manifest.json,
what MAVIS reads), so they can be stopped and run again. MAVIS picks up new
clips while it runs. To redo a sentence, delete its .wav / .mp4.

collect lists the scripted dialogue only: each lesson's briefing and closing
words, and MAVIS's own set lines (the demo and hand-over lines, the debrief
openers, the night reveal...: DIALOGUE_CODES), in every mode's wording.
Coaching instructions (step explanations, callouts, corrections) get no clip
and are spoken over the idle loop as before. `collect --all` lists those as
well, plus data/avatar_lines/heard.json (sentences MAVIS has actually spoken
without a clip, counted at run time). A sentence holding a live number is never
fixed and is left out. In lines.json, set "enabled": false on any sentence not
worth a clip; collect keeps that when run again.

video runs whatever lip-sync tool is installed, through a command template
(config.yaml avatar_lipsync.cmd, or --cmd) with these filled in:
  {audio}   the sentence's WAV            {source}  the avatar image or video
  {out}     the MP4 to write              {outdir}  an empty folder for its output
  {key}     the sentence's file stem
If the tool writes somewhere other than {out}, the newest .mp4 in {outdir} is
taken. Its own audio track is ignored: the tablet plays the WAV.
A tool that does them all in one run (models loaded once) goes in
avatar_lipsync.batch_cmd instead, run once with {lines} (this folder: WAVs in,
<key>.mp4 out), {source} and {limit} -- e.g. tools/musetalk_batch.py.
"""
from __future__ import annotations

import argparse
import json
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import yaml  # noqa: E402

from voice.lipsync import LINES_DIR, has_live_value, key, norm  # noqa: E402
from voice.tts import split_sentences  # noqa: E402
from voice.units import standard_units  # noqa: E402

CFG = yaml.safe_load((HERE / "config.yaml").read_text(encoding="utf-8"))
_media = Path(CFG["web"]["media_dir"])
MEDIA = _media if _media.is_absolute() else HERE / _media
LINES = MEDIA / LINES_DIR
LIST = LINES / "lines.json"
MANIFEST = LINES / "manifest.json"
HEARD = HERE / "data" / "avatar_lines" / "heard.json"
SOURCE_STILL = LINES / "_source.png"

# Shown as text / played as their own video, never spoken by the TTS
NOT_SPOKEN = {"INTRO_BEGINNER", "INTRO_TRAINING"}
LESSON_TEXT_KEYS = ("layman", "pilot", "demo_layman", "demo_pilot")
# The scripted dialogue (collect's default): MAVIS's set lines that aren't
# coaching instructions, and the lesson file's own talk (briefing, closing).
DIALOGUE_CODES = {
    "BRIEFING_1", "BRIEFING_2", "BRIEFING_1_REF",
    "FAM_DEMO_START", "FAM_DEMO_END", "FAM_DEMO_SKIPPED", "FAM_YOU_HAVE_CONTROL",
    "FAM_UPSET_LOOK_DOWN", "FAM_UPSET_HANDOVER",
    "PHASE_ENTER_DEBRIEF", "FAM_DEBRIEF", "FAM_DEBRIEF_DONE",
    "NIGHT_ANNOUNCE", "NIGHT_AFTER",
    "TELEPORT", "TELEPORT_FAIL", "FORCE_REPEAT",
}
DIALOGUE_LESSON_KEYS = ("briefing", "closing")


def sentences(text: str) -> list[str]:
    """The parts runtime/speech.py would speak this text as."""
    return [norm(p) for p in split_sentences(standard_units(text)) if norm(p)]


# ---------------------------------------------------------------- collect
def from_phrases(drill: bool, everything: bool = False) -> dict[str, set]:
    from coach import phrases
    out: dict[str, set] = {}
    tones = ("warm", "drill") if drill else ("warm",)
    for flaps in ("notches", "switch"):
        for jargon in ("layman", "pilot"):
            for guidance in ("direct", "warn", "indirect"):
                for tone in tones:
                    phrases.configure(flaps, jargon, guidance=guidance, tone=tone)
                    for code, opts in phrases.TEMPLATES.items():
                        if code in NOT_SPOKEN or not (everything or code in DIALOGUE_CODES):
                            continue
                        for t in opts:
                            try:
                                txt = t.format()
                            except (KeyError, IndexError, ValueError):
                                continue        # needs live values
                            for s in sentences(txt):
                                out.setdefault(s, set()).add(f"phrases:{code}")
    phrases.configure()
    return out


def _walk_lines(node, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            if k in LESSON_TEXT_KEYS and isinstance(v, str):
                yield f"{path}.{k}".lstrip("."), v
            else:
                yield from _walk_lines(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk_lines(v, f"{path}[{i}]")


def from_lessons(everything: bool = False) -> dict[str, set]:
    out: dict[str, set] = {}
    for p in sorted((HERE / "lessons").glob("*.yaml")):
        lesson = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        alt = (lesson.get("airborne_start") or {}).get("alt_msl_ft")
        for where, txt in _walk_lines(lesson):
            if not (everything or where.split(".")[0] in DIALOGUE_LESSON_KEYS):
                continue
            if alt is not None:
                txt = txt.replace("{alt}", f"{alt:,}")      # as the airborne briefing says it
            for s in sentences(txt):
                out.setdefault(s, set()).add(f"{p.stem}:{where}")
    return out


def cmd_collect(a) -> None:
    found: dict[str, set] = {}
    for part in (from_phrases(a.drill, a.all), from_lessons(a.all)):
        for s, src in part.items():
            found.setdefault(s, set()).update(src)
    heard = {}
    if HEARD.exists():
        heard = json.loads(HEARD.read_text(encoding="utf-8"))
        for s, n in heard.items():
            if a.all and n >= a.min_heard:
                found.setdefault(norm(s), set()).add("heard")
    old = {}
    if LIST.exists():
        old = {e["key"]: e for e in json.loads(LIST.read_text(encoding="utf-8")).get("lines", [])}
    rows = []
    for s in sorted(found):
        if has_live_value(s) or len(s) < 2:
            continue
        k = key(s)
        rows.append({"key": k, "text": s, "enabled": old.get(k, {}).get("enabled", True),
                     "heard": heard.get(s, 0), "sources": sorted(found[s])[:6]})
    LINES.mkdir(parents=True, exist_ok=True)
    LIST.write_text(json.dumps({"made": time.strftime("%Y-%m-%d %H:%M"), "lines": rows},
                               indent=1, ensure_ascii=False), encoding="utf-8")
    on = sum(r["enabled"] for r in rows)
    print(f"{len(rows)} fixed sentences ({on} enabled) -> {LIST}")
    print(f"  from phrases/lessons: {sum(1 for r in rows if any(x != 'heard' for x in r['sources']))}, "
          f"heard at run time: {sum(1 for r in rows if 'heard' in r['sources'])}")


# ---------------------------------------------------------------- audio / video
def enabled_lines() -> list[dict]:
    if not LIST.exists():
        sys.exit(f"No {LIST.name} yet: run `collect` first.")
    return [e for e in json.loads(LIST.read_text(encoding="utf-8"))["lines"] if e.get("enabled", True)]


def wav_seconds(p: Path) -> float:
    with wave.open(str(p), "rb") as w:
        return w.getnframes() / w.getframerate()


def cmd_audio(a) -> None:
    from voice.tts import TTS
    todo = [e for e in enabled_lines() if not (LINES / f"{e['key']}.wav").exists()]
    if a.limit:
        todo = todo[:a.limit]
    if not todo:
        print("Every enabled sentence has its audio.")
        return cmd_build(a)
    tts = TTS(CFG["voice"])           # the same voice settings MAVIS speaks with
    if not tts.engine:
        sys.exit("No TTS engine available (config.yaml voice.engine).")
    print(f"Making {len(todo)} WAVs with {tts.engine} ...")
    for i, e in enumerate(todo, 1):
        out = LINES / f"{e['key']}.wav"
        if not tts.synth(e["text"], str(out)):
            print(f"  [{i}/{len(todo)}] FAILED: {e['text'][:70]}")
            continue
        print(f"  [{i}/{len(todo)}] {wav_seconds(out):4.1f}s  {e['text'][:70]}")
    cmd_build(a)


def cmd_still(a) -> None:
    """One frame of the idle loop, as the source image for an image-driven tool."""
    import av
    src = MEDIA / "avatar" / "idle.mp4"
    with av.open(str(src)) as c:
        for i, frame in enumerate(c.decode(video=0)):
            if i >= a.frame:
                LINES.mkdir(parents=True, exist_ok=True)
                frame.to_image().save(SOURCE_STILL)
                print(f"frame {i} of {src.name} -> {SOURCE_STILL}")
                return
    sys.exit("idle.mp4 has fewer frames than that")


def cmd_video(a) -> None:
    ls = CFG.get("avatar_lipsync") or {}
    batch = None if a.cmd else ls.get("batch_cmd")
    tmpl = a.cmd or ls.get("cmd")
    if not (batch or tmpl):
        sys.exit("No lip-sync command: set avatar_lipsync.batch_cmd or .cmd in config.yaml, or pass --cmd.")
    source = Path(a.source or ls.get("source")
                  or (SOURCE_STILL if SOURCE_STILL.exists() else MEDIA / "avatar" / "idle.mp4"))
    if not source.is_absolute():
        source = (HERE / source).resolve()
    cwd = ls.get("cwd") or a.cwd or None
    if batch:
        # one run for all of them: the tool skips what's already made
        cmd = batch.format(lines=str(LINES.resolve()), source=str(source), limit=a.limit or 0,
                           here=str(HERE))
        print(f"Running: {cmd}")
        r = subprocess.run(cmd, shell=True, cwd=cwd)
        if r.returncode:
            print(f"lip-sync tool exited with {r.returncode}")
        return cmd_build(a)
    todo = [e for e in enabled_lines()
            if (LINES / f"{e['key']}.wav").exists() and not (LINES / f"{e['key']}.mp4").exists()]
    if a.limit:
        todo = todo[:a.limit]
    if not todo:
        print("Nothing to make: every sentence with audio has its video (run `audio` first?).")
        return cmd_build(a)
    print(f"Making {len(todo)} clips from {source.name} ...")
    for i, e in enumerate(todo, 1):
        out = LINES / f"{e['key']}.mp4"
        with tempfile.TemporaryDirectory(prefix="mavis_lipsync_") as od:
            vals = {"audio": str(LINES / f"{e['key']}.wav"), "source": str(source),
                    "out": str(out), "outdir": od, "key": e["key"]}
            cmd = tmpl.format(**{k: shlex.quote(v) if a.quote else v for k, v in vals.items()})
            t0 = time.time()
            r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)
            if not out.exists():
                made = sorted(Path(od).rglob("*.mp4"), key=lambda p: p.stat().st_mtime)
                if made:
                    shutil.move(str(made[-1]), out)
            if r.returncode or not out.exists():
                print(f"  [{i}/{len(todo)}] FAILED ({r.returncode}): {e['text'][:60]}")
                print("    " + (r.stderr or r.stdout or "").strip()[-600:].replace("\n", "\n    "))
                if a.stop_on_error:
                    break
                continue
            print(f"  [{i}/{len(todo)}] {time.time() - t0:5.0f}s  {e['text'][:60]}")
    cmd_build(a)


# ---------------------------------------------------------------- build / status
def cmd_build(a=None) -> None:
    rows = {e["key"]: e for e in enabled_lines()}
    lines = {}
    for k, e in rows.items():
        w, v = LINES / f"{k}.wav", LINES / f"{k}.mp4"
        if w.exists() and v.exists():
            lines[k] = {"text": e["text"], "wav": w.name, "video": v.name, "dur": round(wav_seconds(w), 3)}
    tmp = MANIFEST.with_suffix(".tmp")
    tmp.write_text(json.dumps({"made": time.strftime("%Y-%m-%d %H:%M"), "lines": lines},
                              indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(MANIFEST)
    print(f"manifest: {len(lines)} of {len(rows)} enabled sentences have a clip -> {MANIFEST}")


def cmd_status(a) -> None:
    rows = enabled_lines()
    w = sum((LINES / f"{e['key']}.wav").exists() for e in rows)
    v = sum((LINES / f"{e['key']}.mp4").exists() for e in rows)
    print(f"{len(rows)} enabled sentences: {w} with audio, {v} with video")
    if HEARD.exists():
        heard = json.loads(HEARD.read_text(encoding="utf-8"))
        listed = {e["text"] for e in rows}
        new = sorted(((n, s) for s, n in heard.items() if s not in listed), reverse=True)[:10]
        if new:
            print("Spoken often without a clip (run `collect` to add them):")
            for n, s in new:
                print(f"  {n:4}x  {s[:80]}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd_name", required=True)
    c = sub.add_parser("collect", help="list the fixed sentences")
    c.add_argument("--min-heard", type=int, default=2,
                   help="a sentence heard at run time counts as fixed once spoken this often")
    c.add_argument("--drill", action="store_true", help="include the hardcore tone's lines")
    c.add_argument("--all", action="store_true",
                   help="the coaching instructions too, not just the scripted dialogue")
    c.set_defaults(fn=cmd_collect)
    au = sub.add_parser("audio", help="make each sentence's WAV in MAVIS's voice")
    au.add_argument("--limit", type=int, default=0, help="only this many (try a few first)")
    au.set_defaults(fn=cmd_audio)
    s = sub.add_parser("still", help="save a frame of idle.mp4 as the source image")
    s.add_argument("--frame", type=int, default=0)
    s.set_defaults(fn=cmd_still)
    v = sub.add_parser("video", help="run the lip-sync tool for each sentence")
    v.add_argument("--cmd", help="command template (default: config.yaml avatar_lipsync.cmd)")
    v.add_argument("--source", help="avatar image or video (default: _source.png, else idle.mp4)")
    v.add_argument("--cwd", help="folder to run the command in (the tool's checkout)")
    v.add_argument("--limit", type=int, default=0, help="only this many (try a few first)")
    v.add_argument("--quote", action="store_true", help="shell-quote the filled-in paths")
    v.add_argument("--stop-on-error", action="store_true")
    v.set_defaults(fn=cmd_video)
    sub.add_parser("build", help="rewrite manifest.json from what's made").set_defaults(fn=cmd_build)
    sub.add_parser("status", help="how many are done").set_defaults(fn=cmd_status)
    a = ap.parse_args()
    a.cmd = getattr(a, "cmd", None)
    a.fn(a)


if __name__ == "__main__":
    main()
