"""Turn-taking state machine and speech chunking (no models)."""

from __future__ import annotations

import numpy as np

from orchestrator.voice.detectors import float_to_pcm16, pcm16_to_float
from orchestrator.voice.speech import SentenceChunker, speakable
from orchestrator.voice.turns import FRAME_MS, TurnConfig, TurnEvent, TurnTracker

FRAME = np.zeros(512, dtype=np.float32)


def run(tracker, probs, speaking=False):
    return [tracker.feed(FRAME, p, assistant_speaking=speaking) for p in probs]


def frames(ms):
    return int(ms / FRAME_MS)


def test_speech_start_needs_consecutive_frames():
    t = TurnTracker(TurnConfig(start_frames=2))
    assert run(t, [0.9, 0.1, 0.9]) == [None, None, None]
    assert run(t, [0.9]) == [TurnEvent.SPEECH_START]


def test_short_pause_then_turn_end_on_long_silence():
    cfg = TurnConfig(short_silence_ms=256, long_silence_ms=900)
    t = TurnTracker(cfg)
    events = run(t, [0.9] * 20 + [0.0] * frames(1000))
    assert events.count(TurnEvent.SPEECH_START) == 1
    pause_at = events.index(TurnEvent.PAUSE)
    end_at = events.index(TurnEvent.TURN_END)
    assert (pause_at - 20 + 1) * FRAME_MS >= 256 and (end_at - 20 + 1) * FRAME_MS >= 900
    assert t.speech_end_offset_ms() >= 900


def test_speech_resuming_after_pause_does_not_end_turn():
    t = TurnTracker(TurnConfig(short_silence_ms=256, long_silence_ms=900))
    events = run(t, [0.9] * 10 + [0.0] * frames(400) + [0.9] * 10 + [0.0] * frames(500))
    assert TurnEvent.TURN_END not in events and events.count(TurnEvent.PAUSE) == 2


def test_blips_are_discarded():
    t = TurnTracker(TurnConfig(start_frames=2, min_speech_ms=200, long_silence_ms=500))
    events = run(t, [0.9, 0.9] + [0.0] * frames(600))
    assert TurnEvent.TURN_END not in events and not t.s.in_speech


def test_barge_in_needs_stronger_evidence_while_assistant_speaks():
    cfg = TurnConfig(start_frames=2, barge_threshold=0.8, barge_frames=8)
    t = TurnTracker(cfg)
    assert TurnEvent.BARGE_IN not in run(t, [0.6] * 20, speaking=True)  # echo-level speech
    events = run(t, [0.95] * 8, speaking=True)
    assert events[-1] is TurnEvent.BARGE_IN


def test_preroll_is_kept_with_the_utterance():
    t = TurnTracker(TurnConfig(start_frames=2, preroll_ms=320))
    run(t, [0.0] * 20 + [0.9, 0.9])
    assert len(t.s.frames) == frames(320)  # preroll window includes the two trigger frames


def test_sensitivity_mapping():
    lo, hi = TurnConfig.from_sensitivity(0.0), TurnConfig.from_sensitivity(1.0)
    assert hi.start_threshold < lo.start_threshold and hi.long_silence_ms < lo.long_silence_ms


def test_pcm_roundtrip():
    x = np.array([0.0, 0.5, -0.5, 1.0], dtype=np.float32)
    assert np.allclose(pcm16_to_float(float_to_pcm16(x)), x, atol=1e-4)


# --- speech chunking ---------------------------------------------------------------------------
def test_speakable_strips_markdown_and_citations():
    s = speakable("**Canberra** is the capital [1]. See `code` and [the site](http://x).\n- item")
    assert s == "Canberra is the capital. See code and the site. item"
    assert "code shown on screen" in speakable("Here:\n```py\nprint(1)\n```")


def test_first_chunk_released_early_then_sentences():
    c = SentenceChunker(first_min_chars=24, min_chars=40)
    out = []
    reply = "The capital of Australia is Canberra. It was chosen as a compromise between Sydney and Melbourne. "
    for piece in reply.split(" "):
        out += c.feed(piece + " ")
    out += c.flush()
    assert out[0] == "The capital of Australia is Canberra."
    assert out[1].startswith("It was chosen") and len(out) == 2


def test_long_first_sentence_breaks_at_comma():
    c = SentenceChunker()
    out = c.feed("Well, when you consider all the different factors involved in this decision, the answer ")
    assert out and out[0].endswith(",")


def test_code_blocks_are_held_until_closed():
    c = SentenceChunker()
    assert c.feed("Here is the code:\n\n```python\nx = 1.\ny = 2.\n") == ["Here is the code:"]
    out = c.feed("```\n\nThat's it. ") + c.flush()
    assert any("code shown on screen" in o for o in out) and not any("x = 1" in o for o in out)


def test_what_the_user_says_while_the_assistant_is_busy():
    from orchestrator.voice.speech import while_busy

    # From Talk mode: this cut off a reply before its video tool ran.
    assert while_busy("Thank you, I'm waiting for the video.") == "ack"
    for t in ("Okay.", "Mm-hmm.", "Yes.", "Great, thanks!", "Take your time."):
        assert while_busy(t) == "ack", t
    for t in ("Stop.", "Cancel that.", "Never mind.", "Okay, stop!"):
        assert while_busy(t) == "stop", t
    for t in ("Is the video ready yet?", "Make it five seconds instead.", "Okay, what model are you using?",
              "Yes, and also draw a diagram of the attention layer for the slides tomorrow morning please"):
        assert while_busy(t) == "other", t
