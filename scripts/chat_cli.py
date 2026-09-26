"""Tiny terminal client for the backend API (useful before the web UI exists).

    uv run python scripts/chat_cli.py "What's 17 * 23? Use code."
    uv run python scripts/chat_cli.py --file tests/fixtures/chart.png "What is Q3?"
    uv run python scripts/chat_cli.py --conversation conv_x --thinking "Follow-up question"
"""

from __future__ import annotations

import argparse
import json
import sys

import httpx

BASE = "http://127.0.0.1:8000/api"


def stream(client: httpx.Client, url: str, body: dict, auto_approve: bool) -> dict:
    out: dict = {"text": "", "events": []}
    with client.stream("POST", url, json=body, timeout=None) as r:
        if r.status_code != 200:
            print(r.read().decode(), file=sys.stderr)
            sys.exit(1)
        event = None
        for line in r.iter_lines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:") and event:
                data = json.loads(line[5:].strip())
                out["events"].append((event, data))
                if event == "text_delta":
                    print(data["text"], end="", flush=True)
                    out["text"] += data["text"]
                elif event == "thinking_delta":
                    print(f"\033[2m{data['text']}\033[0m", end="", flush=True)
                elif event in ("tool_call", "tool_result", "status", "citation", "artifact", "title",
                               "media_warning", "error", "tool_confirmation", "text_replace"):
                    short = {k: (str(v)[:160] if isinstance(v, str) else v) for k, v in data.items()
                             if k not in ("data",)}
                    print(f"\n\033[36m[{event}] {json.dumps(short, default=str)[:400]}\033[0m", flush=True)
                    if event == "tool_confirmation" and auto_approve:
                        client.post(f"{BASE}/tool-calls/{data['id']}/decision", json={"approve": True})
                elif event == "message_start":
                    out["conversation_id"] = data["conversation_id"]
                    out["assistant_message_id"] = data["assistant_message_id"]
                elif event == "usage":
                    print(f"\n\033[2m[usage] {data}\033[0m")
    print()
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("text")
    ap.add_argument("--conversation")
    ap.add_argument("--file", action="append", default=[])
    ap.add_argument("--source", default="upload")
    ap.add_argument("--thinking", action="store_true")
    ap.add_argument("--incognito", action="store_true")
    ap.add_argument("--project")
    ap.add_argument("--approve", action="store_true", help="auto-approve tool confirmations")
    args = ap.parse_args()
    with httpx.Client(timeout=600) as client:
        cid = args.conversation or client.post(f"{BASE}/conversations", json={
            "incognito": args.incognito, "project_id": args.project}).json()["id"]
        ids = []
        for f in args.file:
            with open(f, "rb") as fh:
                ids.append(client.post(f"{BASE}/attachments", files={"file": (f.split("/")[-1], fh)},
                                       data={"conversation_id": cid, "source": args.source}).json()["id"])
        res = stream(client, f"{BASE}/conversations/{cid}/messages",
                     {"text": args.text, "attachment_ids": ids, "thinking": args.thinking}, args.approve)
        print(f"conversation: {cid}  message: {res.get('assistant_message_id')}")


if __name__ == "__main__":
    main()
