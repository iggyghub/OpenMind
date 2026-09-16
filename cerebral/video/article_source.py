import re
import logging
import requests
from bs4 import BeautifulSoup, Comment
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

def fetch_article_text(url: str) -> str:
    """Fetch a URL and return readable article text, stripping scripts, styles, nav, etc."""
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding
    except requests.RequestException as exc:
        raise RuntimeError(f"Failed to fetch article from {url}: {exc}") from exc

    soup = BeautifulSoup(resp.text, "html.parser")
    
    # Remove structural/script/style noise
    for el in soup(["script", "style", "meta", "link", "noscript", "head"]):
        el.decompose()
    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()
        
    for tag in soup.find_all(["nav", "footer", "header", "aside", "form", "iframe", "button"]):
        tag.decompose()
        
    # Prioritize semantic article tags, fallback to main or body
    article_el = (
        soup.find("article") 
        or soup.find("main") 
        or soup.find("div", class_=re.compile(r"article|post|content|entry", re.I))
    )
    body = article_el if article_el else soup.find("body") or soup
    text = body.get_text(separator="\n", strip=True)
    
    # Normalize whitespace
    text = re.sub(r"[^\S\n]+", " ", text)
    text = re.sub(r"\n\s*\n", "\n\n", text)
    return text.strip()

async def ingest_article(url: str, category: str) -> None:
    """Fetch article, store source item, trigger extraction spine."""
    from . import store
    from . import channel
    
    logger.info("Ingesting article: %s into category: %s", url, category)
    
    text = fetch_article_text(url)
    if not text:
        raise RuntimeError(f"Article text extraction returned empty for {url}")
        
    store.add_source(source_type="article", channel=url, url=url, text=text)
    
    channel.extract_and_cluster(collection=category, verify=False)
    logger.info("Article ingestion complete: %s", url)
