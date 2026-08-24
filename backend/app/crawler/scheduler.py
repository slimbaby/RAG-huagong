"""
爬虫调度器 - 定时爬取法规更新
"""

from typing import Dict, List, Optional, Callable
import json
import os
import logging
import asyncio

logger = logging.getLogger(__name__)


class CrawlerScheduler:
    """爬虫调度器"""

    def __init__(self, config=None):
        self.config = config
        self.targets_path = os.path.join(getattr(config, "STORAGE_LOCAL_PATH", "./law_archives") if config else "./law_archives", "crawler_targets.json")
        self.targets = self.load_targets()
        self._running = False
        self.on_document_added: Optional[Callable] = None

    def load_targets(self) -> List[Dict]:
        if os.path.exists(self.targets_path):
            with open(self.targets_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return []

    def save_targets(self, targets: List[Dict]):
        self.targets = targets
        os.makedirs(os.path.dirname(self.targets_path), exist_ok=True)
        with open(self.targets_path, "w", encoding="utf-8") as f:
            json.dump(targets, f, ensure_ascii=False, indent=2)

    def add_target(self, url: str, source: str = "", category: str = ""):
        self.targets.append({"url": url, "source": source, "category": category})
        self.save_targets(self.targets)

    def remove_target(self, url: str):
        self.targets = [t for t in self.targets if t["url"] != url]
        self.save_targets(self.targets)

    async def run_once(self):
        from .crawler import LawCrawler
        from .version_manager import VersionManager

        crawler = LawCrawler(self.config)
        version_mgr = VersionManager(getattr(self.config, "STORAGE_LOCAL_PATH", "./law_archives") if config else "./law_archives")

        docs = await crawler.crawl_multiple(self.targets)
        new_count = 0
        for doc in docs:
            version = version_mgr.add_version(url=doc.url, title=doc.title, content=doc.content, content_hash=doc.content_hash, publish_date=doc.publish_date, effective_date=doc.effective_date, source=doc.source, category=doc.category)
            if version:
                new_count += 1
                crawler.save_document(doc)
                if self.on_document_added:
                    await self.on_document_added(doc)
        logger.info(f"Crawl complete: {len(docs)} pages, {new_count} new versions")

    def start(self, cron_expression: str = "0 0 1 * *"):
        self._running = True
        logger.info(f"Crawler scheduler started with cron: {cron_expression}")

    def stop(self):
        self._running = False
        logger.info("Crawler scheduler stopped")

    def get_status(self) -> Dict:
        return {"running": self._running, "targets_count": len(self.targets), "targets": self.targets}

    def get_document_versions(self, url: str) -> List[Dict]:
        from .version_manager import VersionManager
        version_mgr = VersionManager(getattr(self.config, "STORAGE_LOCAL_PATH", "./law_archives") if config else "./law_archives")
        return version_mgr.get_version_history(url)
