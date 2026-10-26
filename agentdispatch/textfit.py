"""Fitting text into a model's prompt without silent cuts, and sizing a local model's context window to the prompt.

A model that is shown a cut-off text does not know it was cut: it reviews half a diff, finishes half a sentence of an objective, or answers a
question whose last line it never saw. So nothing that is sent to a model is cut silently any more:
  - fit() keeps the text whole when it fits; when it really cannot (a size the model's context cannot hold), it keeps the head AND the tail and
    puts a visible marker in the middle saying how many characters were left out;
  - ollama_ctx() picks an Ollama num_ctx big enough for the prompt plus the reply (Ollama's own default, 2048-8192 tokens, silently drops the
    FRONT of a longer prompt - which is where the instructions are)."""

CHARS_PER_TOKEN = 3                     # conservative for code / JSON (English prose is ~4): over-sizing the window costs memory, under-sizing loses the prompt
CTX_MIN, CTX_MAX = 8192, 131072


def marker(n, what="text"):
    return "\n[... %d characters of this %s omitted here to fit the model's context ...]\n" % (n, what)


def fit(text, limit, what="text", head=0.67):
    """-> text unchanged when len(text) <= limit; else head + visible marker + tail, at most about `limit` characters. limit None/0 = no limit."""
    text = "" if text is None else str(text)
    if not limit or limit <= 0 or len(text) <= limit:
        return text
    m = marker(len(text) - limit, what)
    keep = max(0, limit - len(m))
    h = int(keep * head)
    t = keep - h
    left = len(text) - h - t
    m = marker(left, what)
    return text[:h] + m + (text[len(text) - t:] if t else "")


def tail(text, limit, what="output"):
    """the LAST `limit` characters (where a program's error usually is), with a visible marker in front when anything was left out"""
    text = "" if text is None else str(text)
    if not limit or len(text) <= limit:
        return text
    return "[... first %d characters of this %s omitted ...]\n" % (len(text) - limit, what) + text[-limit:]


def est_tokens(chars):
    return int(chars) // CHARS_PER_TOKEN + 1


def ollama_ctx(prompt_chars, reply_tokens=4096, floor=CTX_MIN, ceiling=CTX_MAX):
    """the smallest power-of-two window >= floor that holds the prompt, the reply and a margin (capped at ceiling)"""
    need = est_tokens(prompt_chars) + int(reply_tokens or 0) + 512
    n = max(int(floor or CTX_MIN), 1024)
    while n < need and n < ceiling:
        n *= 2
    return min(n, ceiling)


def max_prompt_chars(ctx_tokens, reply_tokens=4096):
    """how many prompt characters a window of ctx_tokens can really hold next to the reply"""
    return max(4000, (int(ctx_tokens) - int(reply_tokens or 0) - 512) * CHARS_PER_TOKEN)
