import pytest
from unittest.mock import patch, MagicMock
import bs4

SAMPLE_HTML = """
<html>
<head><title>Test</title><script>console.log(1);</script><style>body{}</style></head>
<body>
<nav>Nav</nav>
<article>
<p>First paragraph of article.</p>
<p>Second paragraph with <b>bold</b> text.</p>
<script>remove me</script>
</article>
<footer>Footer</footer>
</body>
</html>
"""

EXPECTED_TEXT = "First paragraph of article.\n\nSecond paragraph with bold text."

@pytest.mark.asyncio
async def test_fetch_article_text_strips_noise():
    from cerebral.video.article_source import fetch_article_text
    
    with patch("cerebral.video.article_source.requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.text = SAMPLE_HTML
        mock_resp.apparent_encoding = "utf-8"
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp
        
        text = fetch_article_text("http://example.com")
        assert "Nav" not in text
        assert "Footer" not in text
        assert "console.log" not in text
        assert "First paragraph of article." in text

@pytest.mark.asyncio
async def test_fetch_article_text_raises_on_failure():
    from cerebral.video.article_source import fetch_article_text
    import requests
    
    with patch("cerebral.video.article_source.requests.get") as mock_get:
        mock_get.side_effect = requests.RequestException("Network error")
        
        with pytest.raises(RuntimeError, match="Failed to fetch article"):
            fetch_article_text("http://example.com")

@pytest.mark.asyncio
async def test_ingest_article_stores_source_and_triggers_extraction():
    from cerebral.video.article_source import ingest_article
    
    with patch("cerebral.video.article_source.fetch_article_text", return_value=EXPECTED_TEXT):
        with patch("cerebral.video.article_source.store.add_source") as mock_add:
            with patch("cerebral.video.channel.extract_and_cluster") as mock_extract:
                await ingest_article("http://example.com", "tech")
                
                mock_add.assert_called_once_with(
                    source_type="article",
                    channel="http://example.com",
                    url="http://example.com",
                    text=EXPECTED_TEXT
                )
                mock_extract.assert_called_once_with(collection="tech", verify=False)

@pytest.mark.asyncio
async def test_ingest_article_raises_on_empty_text():
    from cerebral.video.article_source import ingest_article
    
    with patch("cerebral.video.article_source.fetch_article_text", return_value=""):
        with patch("cerebral.video.article_source.store.add_source"):
            with patch("cerebral.video.channel.extract_and_cluster"):
                with pytest.raises(RuntimeError, match="empty"):
                    await ingest_article("http://example.com", "tech")
