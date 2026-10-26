"""Asking a chat model for a JSON reply and getting all of it.

A reply that stops because it hit the output limit (finish_reason "length", Ollama done_reason "length") used to be passed on as it was: half a
JSON object, which the parser then rejected as "no usable answer" and an attempt was spent. Now the model is shown what it wrote and asked
for exactly the rest, a bounded number of times; the pieces are joined and checked."""
import re

from ..jsonx import extract_json

ROUNDS = 2


def unclosed(text):
    """True when the text opens a JSON object and never closes it (string-aware)"""
    from .realadapters import json_unclosed
    return json_unclosed(text)


def continue_prompt(prompt, partial):
    return (prompt + "\n\n=== YOUR PREVIOUS REPLY WAS CUT OFF ===\nIt stopped before the JSON object was closed (the reply ran out of room). This is everything you wrote so far:\n"
            "<<<PARTIAL\n" + partial + "\nPARTIAL>>>\nContinue EXACTLY where it stops: write only the characters that come next, so that the partial reply followed by your text is the one complete "
            "JSON object. Do not repeat anything, do not start a new object, no fences, no commentary.")


def _clean(more):
    """drop a code fence around a continuation; keep its leading spaces (it may resume mid-word or mid-string: " 1" after "x =")"""
    more = (more or "").rstrip("\r\n")
    if more.strip().startswith("```"):
        more = re.sub(r"^\s*```[a-zA-Z]*\n?", "", more)
        more = re.sub(r"\n?```\s*$", "", more)
    return more


def join(text, more):
    """-> the reply after one continuation: the model's fresh complete object when it started over, else the two pieces joined
    (a CLI ends its output with a newline: that is dropped from the partial, or it would land inside a JSON string)"""
    more = _clean(more)
    if more.strip().startswith("{") and isinstance(extract_json(more, None), dict) and not unclosed(more):
        return more.strip()
    return (text or "").rstrip("\r\n") + more


def complete(send, prompt, rounds=ROUNDS):
    """send(prompt) -> (text, finish_reason). Returns (text, continued_rounds): a reply cut off by the output limit inside its JSON object is continued."""
    text, why = send(prompt)
    n = 0
    while n < rounds and why == "length" and (text or "").strip() and unclosed(text) and extract_json(text, None) is None:
        n += 1
        more, why = send(continue_prompt(prompt, text))
        if not (more or "").strip():
            break
        text = join(text, more)
    return text, n
