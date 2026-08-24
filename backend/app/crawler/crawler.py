"""
法规爬虫 - 爬取政府法规网站
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import datetime
import hashlib
import httpx
import logging
import os
import re

logger = logging.getLogger(__name__)


@dataclass
class CrawledDocument:
    url: str
    title: str
    content: str
    content_hash: str = ""
    publish_date: str = ""
    effective_date: str = ""
    source: str = ""
    category: str = ""
    crawled_at: str = ""
    raw_html: str = ""
    metadata: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return self.__dict__


class LawCrawler:
    """法规爬虫"""

    def __init__(self, config=None):
        self.config = config
        self.download_dir = getattr(config, "STORAGE_LOCAL_PATH", "./law_archives") if config else "./law_archives"
        self.timeout = 60
        self.user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        os.makedirs(self.download_dir, exist_ok=True)

    async def crawl_url(self, url: str, source: str = "", category: str = "") -> Optional[CrawledDocument]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout, headers={"User-Agent": self.user_agent}) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                html = resp.text

            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "lxml")

            title = self._extract_title(soup)
            content = self._extract_content(soup)
            publish_date = self._extract_date(soup, "publish")
            effective_date = self._extract_date(soup, "effective")
            metadata = self._extract_metadata(soup)

            if not content:
                return None

            content_hash = hashlib.md5(content.encode()).hexdigest()

            return CrawledDocument(url=url, title=title, content=content, content_hash=content_hash, publish_date=publish_date, effective_date=effective_date, source=source, category=category, crawled_at=datetime.now().isoformat(), raw_html=html[:10000], metadata=metadata)
        except Exception as e:
            logger.error(f"Failed to crawl {url}: {e}")
            return None

    async def crawl_multiple(self, urls: List[Dict]) -> List[CrawledDocument]:
        results = []
        for item in urls:
            doc = await self.crawl_url(item["url"], item.get("source", ""), item.get("category", ""))
            if doc:
                results.append(doc)
        return results

    def save_document(self, doc: CrawledDocument):
        safe_title = re.sub(r'[<>:"/\\|?*]', '_', doc.title)[:100]
        file_path = os.path.join(self.download_dir, f"{safe_title}.txt")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(f"标题: {doc.title}\n")
            f.write(f"URL: {doc.url}\n")
            f.write(f"来源: {doc.source}\n")
            f.write(f"发布日期: {doc.publish_date}\n")
            f.write(f"生效日期: {doc.effective_date}\n")
            f.write(f"---\n{doc.content}")

    def _extract_title(self, soup) -> str:
        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.get_text(strip=True)
            title = re.sub(r'[-_|].*$', '', title).strip()
            if title:
                return title
        h1 = soup.find("h1")
        if h1:
            return h1.get_text(strip=True)
        return "Untitled"

    def _extract_content(self, soup) -> str:
        for tag in soup.find_all(["script", "style", "nav", "footer"]):
            tag.decompose()

        content_div = soup.find(class_=re.compile(r"content|article-content|main-content|TRS_Editor"))
        if not content_div:
            content_div = soup.find("article") or soup.find("main") or soup.find("body")

        if not content_div:
            return ""

        paragraphs = []
        for elem in content_div.find_all(["p", "div", "li", "h2", "h3", "h4"]):
            text = elem.get_text(strip=True)
            if len(text) > 10:
                paragraphs.append(text)

        content = "\n".join(paragraphs)
        return content[:10000]

    def _extract_date(self, soup, date_type: str) -> str:
        text = soup.get_text()
        keywords = ["发布日期", "发布时间"] if date_type == "publish" else ["生效日期", "施行日期", "实施日期"]
        for kw in keywords:
            pattern = rf'{kw}[：:]\s*(\d{{4}}[年\-/]\d{{1,2}}[月\-/]\d{{1,2}}[日]?)'
            match = re.search(pattern, text)
            if match:
                return match.group(1)
        return ""

    def _extract_metadata(self, soup) -> Dict:
        metadata = {}
        for meta in soup.find_all("meta"):
            name = meta.get("name", "")
            content = meta.get("content", "")
            if name and content:
                metadata[name] = content
        return metadata
