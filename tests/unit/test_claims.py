"""Replies that claim an action no tool performed get corrected (real examples from Talk mode)."""

from __future__ import annotations

import pytest
import pytest_asyncio

from orchestrator.agent.claims import unbacked_claim
from orchestrator.storage.db import Database
from orchestrator.storage.store import Store
from tests.unit.fakes import DIMS, ScriptedProvider, text_turn, tool_turn
from tests.unit.test_orchestrator import EchoTool, _agent


@pytest_asyncio.fixture(name="store")
async def _store():
    db = await Database(":memory:", DIMS).open()
    yield Store(db)
    await db.close()


FALSE_CLAIMS = [
    ("I have created a visual diagram of how a neural network processes information and displayed it on the "
     "screen for you to see. You can view the chart in the side panel on the right."),
    ("I've found a photo of a random person and created a four-second video of them saying hello in Hindi. "
     "You can see the video playing on your screen now."),
    "The video should be playing on your screen right now.",
    "I have created a visualization of how a neural network processes information for you.",
    "I've saved that to your memory.",
    "I searched the web and the latest version is 3.2.",
]


@pytest.mark.parametrize("text", FALSE_CLAIMS)
def test_false_claims_are_caught(text):
    assert unbacked_claim(text, set(), voice=True)
    assert unbacked_claim(text, set(), voice=False)


@pytest.mark.parametrize("text,done", [
    ("I've created a four-second video of them saying hello.", {"generate_video"}),
    ("I've put a diagram of the network in the side panel.", {"create_artifact"}),
    ("I searched the web: the latest version is 3.2 [1].", {"web_search"}),
    ("I've animated the photo you sent into a short video.", {"generate_video"}),
])
def test_claims_backed_by_a_tool_pass(text, done):
    assert unbacked_claim(text, done, voice=True) is None


@pytest.mark.parametrize("text", [
    "Paris is the capital of France.",
    "I can make a four-second video, but I need a photo of the person first.",
    "I can't search the web right now because web access is turned off.",
    "Here's how attention works:\n\n```mermaid\ngraph LR; A-->B\n```\n\nI've drawn a diagram above.",
    "Let me make that for you.",
])
def test_ordinary_replies_pass(text):
    assert unbacked_claim(text, set(), voice=False) is None


def test_a_diagram_can_only_be_on_screen_in_voice_mode_through_a_tool():
    text = "I've drawn a diagram below.\n\n```mermaid\ngraph LR; A-->B\n```"
    assert unbacked_claim(text, set(), voice=False) is None        # typed chat: it's right there
    assert unbacked_claim(text, set(), voice=True)                 # spoken: nobody sees Markdown


async def test_agent_makes_the_model_correct_an_unbacked_claim(store):
    provider = ScriptedProvider([
        text_turn("I've created a video of a person saying hello. It's playing on your screen now."),
        text_turn("Sorry, I haven't made it yet."),
    ])
    agent, _ = await _agent(store, provider, EchoTool())
    agent.s.voice = True
    out = await agent.run([{"role": "user", "content": "make a video"}])
    note = provider.requests[1].messages[-1]["content"]
    assert note.startswith("[system note]") and "generate_video" in note
    assert out.text.endswith("Sorry, I haven't made it yet.")
    assert len(provider.requests) == 2                              # corrected once, not in a loop


async def test_agent_corrects_only_once(store):
    claim = "I've created a video. It's playing on your screen now."
    provider = ScriptedProvider([text_turn(claim), text_turn(claim)])
    agent, _ = await _agent(store, provider, EchoTool())
    await agent.run([{"role": "user", "content": "make a video"}])
    assert len(provider.requests) == 2


async def test_backed_claims_cost_nothing(store):
    class Search(EchoTool):
        name = "web_search"

    provider = ScriptedProvider([tool_turn("web_search", '{"text": "hi"}'), text_turn("I searched: it said hi [1].")])
    agent, _ = await _agent(store, provider, Search())
    agent.registry.builtins = {"web_search": agent.registry.builtins["echo"]}
    await agent.run([{"role": "user", "content": "search"}])
    assert len(provider.requests) == 2                              # tool round + answer, no correction


# Reported from Talk mode: the video was never started (the reply was cut off before the tool call).
@pytest.mark.parametrize("text", [
    "I'm generating the video of a person waving their hand for two seconds now. It should be ready in a few minutes.",
    "The video is still rendering and should be ready in a couple of minutes. I'll let you know as soon as it's done.",
    "It's still processing. I'll let you know when it's ready.",
])
def test_work_in_progress_claims_are_caught(text):
    claim = unbacked_claim(text, set(), voice=True)
    assert claim and claim[2] is True and "generate_video" in claim[1]


def test_progress_correction_explains_nothing_runs_in_the_background():
    from orchestrator.agent.claims import correction

    note = correction(*unbacked_claim("The video is still rendering.", set(), voice=True))
    assert "nothing continues in the background" in note and "can't come back" in note


def test_progress_words_after_a_real_tool_call_pass():
    assert unbacked_claim("I'm making it now.", {"generate_video", "create_artifact"}, voice=True) is None
