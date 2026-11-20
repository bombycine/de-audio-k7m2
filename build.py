#!/usr/bin/env python3
"""Build podcast episodes from episodes/*.yaml with Google Cloud TTS (Chirp 3 HD) and write docs/feed.xml.

Episode YAML:
  title: str            description: str          date: YYYY-MM-DD
  segments: list of
    - en: "English narration"
    - de: "German line"            voice: A|B (default A)   slow: bool
    - pause: seconds
    - drill: {en: "prompt", de: "answer", think: 4}   # EN prompt, pause, DE, pause, DE again
    - dialogue: [{A: "..."}, {B: "..."}]   slow: bool   shadow: bool  # shadow = pause after each line to repeat

Only episodes without an existing docs/audio/<slug>.mp3 are synthesized. Monthly character use is
tracked in usage.json; the build refuses to exceed MONTHLY_CHAR_CAP (free tier is 1M).
"""
import io, json, os, subprocess, sys, tempfile, wave
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

import yaml

ROOT = Path(__file__).resolve().parent
EP_DIR, DOCS = ROOT / "episodes", ROOT / "docs"
AUDIO = DOCS / "audio"
USAGE = ROOT / "usage.json"
MONTHLY_CHAR_CAP = int(os.environ.get("MONTHLY_CHAR_CAP", "900000"))
RATE = 24000
VOICES = {
    "EN": ("en-US", os.environ.get("VOICE_EN", "en-US-Chirp3-HD-Kore")),
    "A": ("de-DE", os.environ.get("VOICE_A", "de-DE-Chirp3-HD-Charon")),
    "B": ("de-DE", os.environ.get("VOICE_B", "de-DE-Chirp3-HD-Aoede")),
}
SLOW_RATE = 0.8
GAP = 0.6  # seconds between segments

_client = None
_rate_supported = True


def synth(text: str, voice: str, slow: bool = False) -> bytes:
    """Return raw 16-bit mono PCM at RATE."""
    global _client, _rate_supported
    if os.environ.get("TTS_MOCK"):  # local testing: silence proportional to text length
        return b"\x00\x00" * int(RATE * max(0.5, len(text) * 0.06))
    from google.cloud import texttospeech as tts
    from google.api_core import exceptions
    if _client is None:
        _client = tts.TextToSpeechClient()
    lang, name = VOICES[voice]
    cfg = dict(audio_encoding=tts.AudioEncoding.LINEAR16, sample_rate_hertz=RATE)
    if slow and _rate_supported:
        cfg["speaking_rate"] = SLOW_RATE
    req = dict(input=tts.SynthesisInput(text=text),
               voice=tts.VoiceSelectionParams(language_code=lang, name=name),
               audio_config=tts.AudioConfig(**cfg))
    try:
        resp = _client.synthesize_speech(**req)
    except exceptions.InvalidArgument:
        if "speaking_rate" not in cfg:
            raise
        _rate_supported = False
        print("note: speaking_rate not supported for this voice; using normal speed")
        return synth(text, voice, False)
    with wave.open(io.BytesIO(resp.audio_content)) as w:
        return w.readframes(w.getnframes())


def silence(sec: float) -> bytes:
    return b"\x00\x00" * int(RATE * sec)


def secs(pcm: bytes) -> float:
    return len(pcm) / 2 / RATE


def render(ep: dict, counter: list) -> bytes:
    out = []

    def say(text, voice, slow=False):
        counter[0] += len(text)
        pcm = synth(text, voice, slow)
        out.append(pcm); out.append(silence(GAP))
        return pcm

    for seg in ep["segments"]:
        if "en" in seg:
            say(seg["en"], "EN")
        elif "de" in seg:
            say(seg["de"], seg.get("voice", "A"), seg.get("slow", False))
        elif "pause" in seg:
            out.append(silence(float(seg["pause"])))
        elif "drill" in seg:
            d = seg["drill"]
            say(d["en"], "EN")
            out.append(silence(float(d.get("think", 4))))
            pcm = say(d["de"], d.get("voice", "A"))
            out.append(silence(max(2.0, secs(pcm) * 1.3)))
            say(d["de"], d.get("voice", "A"))
            out.append(silence(1.0))
        elif "dialogue" in seg:
            for line in seg["dialogue"]:
                (spk, text), = line.items()
                pcm = say(text, spk, seg.get("slow", False))
                if seg.get("shadow"):
                    out.append(silence(secs(pcm) * 1.3 + 0.5))
        else:
            raise ValueError(f"unknown segment: {seg}")
    return b"".join(out)


def to_mp3(pcm: bytes, dest: Path):
    with tempfile.NamedTemporaryFile(suffix=".wav") as tmp:
        with wave.open(tmp.name, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE); w.writeframes(pcm)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", tmp.name, "-ac", "1",
                        "-b:a", "48k", str(dest)], check=True)


def load_usage():
    return json.loads(USAGE.read_text()) if USAGE.exists() else {}


def main():
    AUDIO.mkdir(parents=True, exist_ok=True)
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    usage = load_usage()
    meta = json.loads((DOCS / "episodes.json").read_text()) if (DOCS / "episodes.json").exists() else {}

    for path in sorted(EP_DIR.glob("*.yaml")):
        slug = path.stem
        mp3 = AUDIO / f"{slug}.mp3"
        ep = yaml.safe_load(path.read_text())
        if mp3.exists() and slug in meta:
            continue
        est = sum(len(str(v)) for v in _texts(ep["segments"]))
        if usage.get(month, 0) + est > MONTHLY_CHAR_CAP:
            print(f"skip {slug}: would exceed monthly cap ({usage.get(month, 0)} + {est})")
            continue
        counter = [0]
        pcm = render(ep, counter)
        to_mp3(pcm, mp3)
        usage[month] = usage.get(month, 0) + counter[0]
        meta[slug] = {"title": ep["title"], "description": ep.get("description", ""),
                      "date": str(ep["date"]), "seconds": round(secs(pcm)), "bytes": mp3.stat().st_size}
        print(f"built {slug}: {meta[slug]['seconds']}s, {counter[0]} chars")
        USAGE.write_text(json.dumps(usage, indent=2))

    # drop metadata for episodes whose yaml was removed
    meta = {k: v for k, v in meta.items() if (EP_DIR / f"{k}.yaml").exists()}
    for mp3 in AUDIO.glob("*.mp3"):  # audio for pruned episodes
        if not (EP_DIR / f"{mp3.stem}.yaml").exists():
            mp3.unlink(); print(f"removed {mp3.name}")
    (DOCS / "episodes.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    write_feed(meta)
    print(f"usage {month}: {usage.get(month, 0)} chars")


def _texts(segs):
    for s in segs:
        for k in ("en", "de"):
            if k in s:
                yield s[k]
        if "drill" in s:
            yield s["drill"]["en"]; yield s["drill"]["de"] * 2
        if "dialogue" in s:
            for line in s["dialogue"]:
                yield from line.values()


def write_feed(meta):
    repo = os.environ.get("GITHUB_REPOSITORY", "bombycine/de-audio")
    owner, name = repo.split("/")
    base = os.environ.get("FEED_BASE", f"https://{owner}.github.io/{name}")
    items = []
    for slug, m in sorted(meta.items(), key=lambda kv: (kv[1]["date"], kv[0]), reverse=True):
        d = datetime.fromisoformat(m["date"]).replace(hour=12, tzinfo=timezone.utc)
        mins, s = divmod(m["seconds"], 60)
        items.append(f"""    <item>
      <title>{escape(m['title'])}</title>
      <description>{escape(m['description'])}</description>
      <guid isPermaLink="false">{slug}</guid>
      <pubDate>{format_datetime(d)}</pubDate>
      <enclosure url="{base}/audio/{slug}.mp3" length="{m['bytes']}" type="audio/mpeg"/>
      <itunes:duration>{mins}:{s:02d}</itunes:duration>
    </item>""")
    feed = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>Josh's German</title>
    <link>{base}/</link>
    <description>Personal German practice: drills, dialogues and stories built from my Anki decks.</description>
    <language>de</language>
    <itunes:author>Josh</itunes:author>
    <itunes:image href="{base}/cover.png"/>
    <itunes:category text="Education"/>
    <itunes:explicit>false</itunes:explicit>
    <itunes:block>Yes</itunes:block>
{chr(10).join(items)}
  </channel>
</rss>
"""
    (DOCS / "feed.xml").write_text(feed)


if __name__ == "__main__":
    main()
