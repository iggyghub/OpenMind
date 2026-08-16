"""Oldest-turn summarization (harness parity H1-S3 / #732). Hermetic: no
model/DB/net -- summarize_fn is injected, mirroring the router.complete seam
used elsewhere in this suite."""

from cerebral.llm.context_summarizer import should_summarize, summarize_oldest


def test_should_summarize_matches_threshold():
    assert should_summarize("x" * 4000, 1000) is True   # 1000 tokens > 700
    assert should_summarize("x" * 40, 1000) is False


async def test_nothing_to_summarize_when_at_or_under_keep_recent():
    turns = [{"who": "User", "text": f"msg {i}"} for i in range(4)]

    async def summarize_fn(prompt):
        raise AssertionError("must not be called -- nothing to summarize")

    result = await summarize_oldest(turns, summarize_fn, keep_recent=4)
    assert result is None


async def test_summarizes_oldest_keeps_recent_count_out_of_result():
    turns = [{"who": "User", "text": f"msg {i}"} for i in range(10)]
    captured = {}

    async def summarize_fn(prompt):
        captured["prompt"] = prompt
        return "  A concise recap of the earlier messages.  "

    result = await summarize_oldest(turns, summarize_fn, keep_recent=4)

    assert result is not None
    assert result["turns_summarized"] == 6          # 10 - keep_recent(4)
    assert result["text"] == "A concise recap of the earlier messages."  # stripped
    assert "msg 0" in captured["prompt"]              # oldest turn present
    assert "msg 5" in captured["prompt"]              # last of the "oldest" batch
    assert "msg 6" not in captured["prompt"]          # first "recent" turn excluded


async def test_skips_turns_with_no_text():
    turns = [{"who": "User", "text": ""}, {"who": "Felix", "text": ""},
             {"who": "User", "text": "real content"}, {"who": "Felix", "text": "a"},
             {"who": "User", "text": "b"}]
    captured = {}

    async def summarize_fn(prompt):
        captured["prompt"] = prompt
        return "summary"

    # keep_recent=2 -> to_summarize = the first 3 (2 blank + "real content").
    result = await summarize_oldest(turns, summarize_fn, keep_recent=2)
    assert result is not None
    assert "real content" in captured["prompt"]


async def test_all_blank_oldest_turns_returns_none():
    turns = [{"who": "User", "text": ""}] * 5 + [{"who": "User", "text": "recent"}] * 4

    async def summarize_fn(prompt):
        raise AssertionError("must not be called -- nothing but blanks to summarize")

    result = await summarize_oldest(turns, summarize_fn, keep_recent=4)
    assert result is None
