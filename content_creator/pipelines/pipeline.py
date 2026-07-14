#!/usr/bin/env python3
"""
Main pipeline script for TikTok/Instagram content generation.

To use:
1. Edit config.py to set your desired thematic and media_domain
2. Run: python pipeline.py

The script will:
- Scrape articles from the configured source
- Find the first unprocessed article (checks GCS for existing content)
- Generate a summary
- Create text-to-speech audio
- Generate video with subtitles
- Upload to cloud storage
"""

import os
import json
import time
import hashlib
from typing import List, Optional
from datetime import datetime
from urllib.parse import urlparse

from config import PIPELINE_CONFIG, GCS_CONFIG, get_generated_paths
from modules import (
    NewsScraper,
    ArticleSummarizer,
    GCSManager,
    VideoGenerator,
    FullArticle,
    RenderResponse,
)


class ContentPipeline:
    """Main pipeline orchestrator."""

    def __init__(self):
        self.config = PIPELINE_CONFIG
        self.paths = get_generated_paths()
        self.scraper = NewsScraper()
        self.summarizer = ArticleSummarizer()
        self.gcs = GCSManager()
        self.video_gen = VideoGenerator()

        # Create output directory if needed
        os.makedirs(self.config["output_dir"], exist_ok=True)

    def get_article_gcs_path(self, article_url: str) -> str:
        """Generate a unique GCS path for an article based on its URL."""
        # Create a hash of the URL to make a safe filename
        url_hash = hashlib.md5(str(article_url).encode()).hexdigest()[:10]

        # Parse the URL to get a readable part
        parsed = urlparse(str(article_url))
        path_parts = parsed.path.strip("/").split("/")

        # Get the last meaningful part of the URL
        article_slug = path_parts[-1] if path_parts else "article"

        # Clean the slug (remove .html, limit length)
        article_slug = article_slug.replace(".html", "")[:50]

        # Generate the path: media/audio/thematic/date/article-slug-hash/audio.mp3
        today = datetime.now().strftime("%Y-%m-%d")
        return f"{GCS_CONFIG['media_paths']['audio']}{self.config['thematic']}/{today}/{article_slug}-{url_hash}/audio.mp3"

    def find_unprocessed_article(
        self, articles: List[FullArticle]
    ) -> Optional[FullArticle]:
        """Find the first article that hasn't been processed yet."""
        if not self.config.get("skip_processed", True):
            # If skip_processed is False, just return the first article
            return articles[0] if articles else None

        print("\n🔍 Checking for already processed articles...")

        for i, article in enumerate(articles):
            gcs_path = self.get_article_gcs_path(article.link.href)

            # Check if this article has already been processed
            exists = self.gcs.check_blob_exists(gcs_path)

            if exists:
                print(
                    f"  ⏭️  Article #{i} already processed: {article.link.title[:50]}..."
                )
            else:
                print(
                    f"  ✅ Article #{i} not processed yet: {article.link.title[:50]}..."
                )
                return article

        return None

    def run(self):
        """Execute the complete content generation pipeline."""
        print("=" * 60)
        print("CONTENT GENERATION PIPELINE")
        print("=" * 60)
        print(f"Media: {self.config['media_domain']}")
        print(f"Thematic: {self.config['thematic']}")
        print(f"URL: {self.paths['scrape_url']}")
        print("=" * 60)

        # Step 1: Scrape articles
        print("\n📰 Step 1: Scraping articles...")
        articles = self.scrape_articles()
        if not articles:
            print("❌ No articles found. Exiting.")
            return

        # Step 2: Find first unprocessed article
        print("\n🔎 Step 2: Finding unprocessed article...")
        article = self.find_unprocessed_article(articles)

        if not article:
            print("❌ All articles have already been processed. Nothing to do.")
            print(
                "\nTip: Change the thematic or media_domain in config.py to process different content."
            )
            return

        print("\n✏️ Processing article:")
        print(f"  Title: {article.link.title}")
        print(f"  URL: {article.link.href}")
        print(f"  Published: {article.link.published_at.strftime('%Y-%m-%d %H:%M')}")

        # Generate GCS path for this article
        article_gcs_path = self.get_article_gcs_path(article.link.href)

        # Step 3: Generate summary
        print("\n🤖 Step 3: Generating AI summary...")
        summary = self.summarizer.summarize_article(article)
        if not summary:
            print("❌ Failed to generate summary. Exiting.")
            return
        print(f"Summary length: {len(summary)} characters")

        # Step 4: Generate audio
        print("\n🎙️ Step 4: Generating text-to-speech audio...")
        audio_path = os.path.join(
            self.config["output_dir"], self.config["audio_filename"]
        )
        audio_file = self.summarizer.text_to_speech_google(summary, audio_path)
        if not audio_file:
            print("❌ Failed to generate audio. Exiting.")
            return

        audio_length = self.video_gen.get_audio_length(audio_path)
        print(f"Audio duration: {audio_length:.1f} seconds")

        # Step 5: Generate video queries (optional, for future AI video generation)
        print("\n🎨 Step 5: Generating video prompts...")
        video_queries = self.summarizer.write_video_queries(summary, int(audio_length))
        if video_queries:
            print(f"Generated {len(video_queries)} video prompts")
            # Save for future use
            queries_path = os.path.join(self.config["output_dir"], "video_queries.json")
            with open(queries_path, "w", encoding="utf-8") as f:
                json.dump(video_queries.model_dump(), f, ensure_ascii=False, indent=2)

        # Step 6: Upload audio to cloud (using article-specific path)
        print("\n☁️ Step 6: Uploading audio to cloud storage...")
        print(f"  GCS Path: {article_gcs_path}")
        audio_upload = self.gcs.upload_file(audio_path, article_gcs_path)
        if not audio_upload:
            print("❌ Failed to upload audio. Exiting.")
            return
        audio_url = audio_upload["url"]
        print(f"  Audio URL: {audio_url}")

        # Step 7: Create video
        print("\n🎬 Step 7: Creating video...")
        video_response = self.create_video(audio_url, audio_length)
        if not video_response:
            print("❌ Failed to create video. Exiting.")
            return

        # Step 8: Add subtitles
        print("\n📝 Step 8: Adding subtitles...")
        final_video = self.add_subtitles(video_response.url)
        if not final_video:
            print("❌ Failed to add subtitles. Exiting.")
            return

        # Save video URL to GCS alongside audio (optional)
        video_meta_path = article_gcs_path.replace("/audio.mp3", "/video_metadata.json")
        video_metadata = {
            "final_video_url": str(final_video.url),
            "created_at": datetime.now().isoformat(),
            "audio_duration": audio_length,
        }

        # Save metadata locally first
        meta_path = os.path.join(self.config["output_dir"], "video_metadata.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(video_metadata, f, ensure_ascii=False, indent=2)

        # Upload metadata to GCS
        self.gcs.upload_file(meta_path, video_meta_path)

        # Success!
        print("\n" + "=" * 60)
        print("✅ PIPELINE COMPLETED SUCCESSFULLY!")
        print("=" * 60)
        print(f"\033[92mSUCCESS : {final_video.url}\033[0m")
        print(f"Article processed: {article.link.title}")
        print("\nNext steps:")
        print("- Download the video from the URL above")
        print("- Upload to TikTok/Instagram")
        print("- Add hashtags and description")
        print("\nRun the pipeline again to process the next unprocessed article!")

        # Save results
        self.save_results(
            {
                "timestamp": datetime.now().isoformat(),
                "config": self.config,
                "article": {
                    "title": article.link.title,
                    "url": str(article.link.href),
                    "published": article.link.published_at.isoformat(),
                },
                "summary": summary,
                "audio_url": audio_url,
                "audio_gcs_path": article_gcs_path,
                "final_video_url": str(final_video.url),
                "video_queries": video_queries if video_queries else {},
            }
        )

    def scrape_articles(self) -> List[FullArticle]:
        """Scrape and process articles from the configured source."""
        # Get article links
        links = self.scraper.scrape_links_older_than_24h(self.paths["scrape_url"])

        # Remove duplicates
        unique_links = {link.href: link for link in links}.values()
        print(f"Found {len(unique_links)} unique articles")

        # Scrape full content
        articles = []
        for i, link in enumerate(unique_links):
            print(f"  Scraping article {i+1}/{len(unique_links)}...")
            blocks = self.scraper.scrape_article(link.href)
            if blocks:
                articles.append(FullArticle(link=link, content=blocks))

        return articles

    def create_video(
        self, audio_url: str, audio_length: float
    ) -> Optional[RenderResponse]:
        """Create video with configured settings."""
        response = self.video_gen.create_video(
            audio_url=audio_url,
            video_urls=self.config["video_urls"],
            audio_length=audio_length,
            outro_config=self.config["outro_text"],
            outro_duration=self.config["outro_duration"],
            extra_audio_padding=self.config["extra_audio_padding"],
        )

        if response:
            print("Video creation initiated. Waiting for processing...")
            time.sleep(40)  # Wait for video processing
            print("Video ready!")

        return response

    def add_subtitles(self, video_url: str) -> Optional[RenderResponse]:
        """Add subtitles to the video."""
        response = self.video_gen.add_subtitles(video_url)

        if response:
            print("Subtitle generation initiated. Waiting for processing...")
            time.sleep(30)  # Wait for subtitle processing
            print("Subtitles added!")

        return response

    def save_results(self, results: dict):
        """Save pipeline results to a JSON file."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        results_path = os.path.join(
            self.config["output_dir"], f"pipeline_results_{timestamp}.json"
        )

        with open(results_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

        print(f"\nResults saved to: {results_path}")

    def list_processed_articles(self):
        """List all processed articles for the current thematic."""
        print("\n📋 Listing all processed articles...")
        prefix = f"{GCS_CONFIG['media_paths']['audio']}{self.config['thematic']}/"
        blobs = self.gcs.list_blobs_with_prefix(prefix)

        audio_files = [b for b in blobs if b.endswith("/audio.mp3")]

        if audio_files:
            print(f"Found {len(audio_files)} processed articles:")
            for blob in audio_files[:10]:  # Show first 10
                print(f"  - {blob}")
            if len(audio_files) > 10:
                print(f"  ... and {len(audio_files) - 10} more")
        else:
            print("No processed articles found for this thematic.")


def main():
    """Main entry point."""
    try:
        pipeline = ContentPipeline()

        # Optional: Add a command to list processed articles
        import sys

        if len(sys.argv) > 1 and sys.argv[1] == "--list":
            pipeline.list_processed_articles()
        else:
            pipeline.run()

    except KeyboardInterrupt:
        print("\n\n⚠️ Pipeline interrupted by user")
    except Exception as e:
        print(f"\n\n❌ Pipeline failed with error: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    main()
