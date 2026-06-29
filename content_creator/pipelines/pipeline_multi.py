#!/usr/bin/env python3
"""
Multi-thematic pipeline script for TikTok/Instagram content generation.

To use:
1. Edit config.py to set your media sources and thematics
2. Run: python pipeline_multi.py

The script will:
- Process one unprocessed article per thematic from each enabled media source
- Generate summaries and videos for each article
- Upload to cloud storage

Modes:
- Multi-thematic mode: Process one article from each thematic in the configured media sources
- Single mode: Process one article from a specific thematic
"""

import os
import json
import asyncio
import hashlib
from typing import List, Optional
from datetime import datetime
from urllib.parse import urlparse
from dataclasses import dataclass

from content_creator.config.config import PIPELINE_CONFIG, GCS_CONFIG, VIDEO_CONFIG
from modules import (
    NewsScraper,
    ArticleSummarizer,
    GCSManager,
    VideoGenerator,
    FullArticle,
    RenderResponse,
)


@dataclass
class ProcessingResult:
    """Result of processing a single article."""

    media_domain: str
    thematic: str
    article_title: str
    article_url: str
    video_url: str
    success: bool
    error_message: Optional[str] = None


class ContentPipeline:
    """Main pipeline orchestrator."""

    def __init__(self):
        self.config = PIPELINE_CONFIG
        self.scraper = NewsScraper()
        self.summarizer = ArticleSummarizer()
        self.gcs = GCSManager()
        self.video_gen = VideoGenerator()

        # Create output directory if needed
        os.makedirs(self.config["output_dir"], exist_ok=True)

    def get_article_base_path(
        self, article_url: str, media_domain: str, thematic: str, sector: str
    ) -> str:
        """Generate a unique GCS base path for an article based on its URL.
        All media files (audio, videos, metadata) will be in this same folder.
        Structure: media/{sector}/{media_domain}/{thematic}/{date}/{article-slug-hash}/
        """
        # Create a hash of the URL to make a safe filename
        url_hash = hashlib.md5(str(article_url).encode()).hexdigest()[:10]

        # Parse the URL to get a readable part
        parsed = urlparse(str(article_url))
        path_parts = parsed.path.strip("/").split("/")

        # Get the last meaningful part of the URL
        article_slug = path_parts[-1] if path_parts else "article"

        # Clean the slug (remove .html, limit length)
        article_slug = article_slug.replace(".html", "")[:50]

        # Generate the base path: media/{sector}/{media_domain}/{thematic}/{date}/{article-slug-hash}/
        today = datetime.now().strftime("%Y-%m-%d")
        return f"media/{sector}/{media_domain}/{thematic}/{today}/{article_slug}-{url_hash}/"

    def find_unprocessed_article(
        self, articles: List[FullArticle], media_domain: str, thematic: str, sector: str
    ) -> Optional[FullArticle]:
        """Find the first article that hasn't been processed yet."""
        if not self.config.get("skip_processed", True):
            # If skip_processed is False, just return the first article
            return articles[0] if articles else None

        for article in articles:
            base_path = self.get_article_base_path(
                article.link.href, media_domain, thematic, sector
            )
            audio_path = f"{base_path}audio.mp3"

            # Check if this article has already been processed
            if not self.gcs.check_blob_exists(audio_path):
                return article

        return None

    def scrape_articles(self, media_domain: str, thematic: str) -> List[FullArticle]:
        """Scrape and process articles from a specific source."""
        url = f"https://{media_domain}/{thematic}/"

        # Get article links
        links = self.scraper.scrape_links_older_than_24h(url)

        # Remove duplicates
        unique_links = {link.href: link for link in links}.values()

        # Scrape full content
        articles = []
        for link in unique_links:
            blocks = self.scraper.scrape_article(link.href)
            if blocks:
                articles.append(FullArticle(link=link, content=blocks))

        return articles

    def process_single_article(
        self, article: FullArticle, media_domain: str, thematic: str, sector: str
    ) -> ProcessingResult:
        """Process a single article through the entire pipeline."""

        try:
            print(f"\n{'='*50}")
            print(f"📰 Processing: {article.link.title[:60]}...")
            print(f"   Media: {media_domain}")
            print(f"   Sector: {sector}")
            print(f"   Thematic: {thematic}")
            print(f"   URL: {article.link.href}")

            # Generate GCS base path for this article
            article_base_path = self.get_article_base_path(
                article.link.href, media_domain, thematic, sector
            )
            article_audio_path = f"{article_base_path}audio.mp3"

            # Generate summary
            print("   🤖 Generating summary...")
            summary = self.summarizer.summarize_article(article)
            print(summary)
            if not summary:
                return ProcessingResult(
                    media_domain=media_domain,
                    thematic=thematic,
                    article_title=article.link.title,
                    article_url=str(article.link.href),
                    video_url="",
                    success=False,
                    error_message="Failed to generate summary",
                )

            # Generate audio
            print("   🎙️ Generating audio...")
            # Replace / with _ in thematic to create valid local filename
            safe_thematic = thematic.replace("/", "_").strip("_")
            audio_filename = f"{media_domain}_{safe_thematic}_{hashlib.md5(str(article.link.href).encode()).hexdigest()[:8]}.mp3"
            audio_path = os.path.join(self.config["output_dir"], audio_filename)
            audio_file = self.summarizer.text_to_speech_google(summary, audio_path)
            if not audio_file:
                return ProcessingResult(
                    media_domain=media_domain,
                    thematic=thematic,
                    article_title=article.link.title,
                    article_url=str(article.link.href),
                    video_url="",
                    success=False,
                    error_message="Failed to generate audio",
                )

            audio_length = self.video_gen.get_audio_length(audio_path)

            # Generate video queries
            print("   🎨 Generating video prompts...")
            video_queries = self.summarizer.write_video_queries(
                summary, int(audio_length)
            )
            print(video_queries)

            if not video_queries:
                print("   ⚠️ Failed to generate video prompts, using fallback")
                video_queries = {
                    str(i): f"Abstract visual scene {i}" for i in range(1, 8)
                }

            # Upload audio to cloud
            print("   ☁️ Uploading audio...")
            audio_upload = self.gcs.upload_file(audio_path, article_audio_path)
            if not audio_upload:
                return ProcessingResult(
                    media_domain=media_domain,
                    thematic=thematic,
                    article_title=article.link.title,
                    article_url=str(article.link.href),
                    video_url="",
                    success=False,
                    error_message="Failed to upload audio",
                )
            audio_url = audio_upload["url"]

            # Generate videos from prompts using the configured video source
            video_source = VIDEO_CONFIG.get("video_source", "runway")

            if video_source == "pexels":
                print("   🎥 Searching videos on Pexels...")
                video_urls = asyncio.run(
                    self.video_gen.get_pexels_videos_from_prompts(
                        video_prompts=video_queries,
                        article_gcs_base_path=article_base_path,
                        gcs_manager=self.gcs,
                    )
                )
            else:
                print("   🎥 Generating videos with AI (RunwayML)...")
                video_urls = asyncio.run(
                    self.video_gen.generate_videos_from_prompts(
                        video_prompts=video_queries,
                        article_gcs_base_path=article_base_path,
                        gcs_manager=self.gcs,
                    )
                )

            if not video_urls:
                print(f"   ⚠️ No videos generated from {video_source}, cannot continue")
                return ProcessingResult(
                    media_domain=media_domain,
                    thematic=thematic,
                    article_title=article.link.title,
                    article_url=str(article.link.href),
                    video_url="",
                    success=False,
                    error_message=f"Failed to get videos from {video_source}",
                )

            # Create video with generated videos
            print("   🎬 Creating final video...")
            video_response = self.create_video_with_urls(
                audio_url, video_urls, audio_length
            )
            if not video_response:
                return ProcessingResult(
                    media_domain=media_domain,
                    thematic=thematic,
                    article_title=article.link.title,
                    article_url=str(article.link.href),
                    video_url="",
                    success=False,
                    error_message="Failed to create video",
                )

            # Upload video without subtitles to GCS
            print("   ☁️ Uploading video without subtitles to GCS...")
            video_no_subs_gcs_path = f"{article_base_path}video_no_subtitles.mp4"
            video_no_subs_uploaded = self.upload_video_to_gcs(
                str(video_response.url), video_no_subs_gcs_path
            )
            if video_no_subs_uploaded:
                print(f"   ✅ Video without subtitles saved: {video_no_subs_uploaded}")
            else:
                print("   ⚠️ Failed to upload video without subtitles")

            # Add subtitles
            print("   📝 Adding subtitles...")
            final_video = self.add_subtitles(str(video_response.url))
            if not final_video:
                return ProcessingResult(
                    media_domain=media_domain,
                    thematic=thematic,
                    article_title=article.link.title,
                    article_url=str(article.link.href),
                    video_url="",
                    success=False,
                    error_message="Failed to add subtitles",
                )

            # Save metadata to GCS (same folder as audio and videos)
            video_meta_path = f"{article_base_path}video_metadata.json"
            video_metadata = {
                "final_video_url": str(final_video.url),
                "video_no_subtitles_url": (
                    video_no_subs_uploaded if video_no_subs_uploaded else None
                ),
                "created_at": datetime.now().isoformat(),
                "audio_duration": audio_length,
                "summary": summary,
                "video_queries": video_queries.model_dump() if video_queries else {},
            }

            meta_path = os.path.join(self.config["output_dir"], "temp_metadata.json")
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(video_metadata, f, ensure_ascii=False, indent=2)
            self.gcs.upload_file(meta_path, video_meta_path)

            print(f"\033[92mSUCCESS : {final_video.url}\033[0m")

            return ProcessingResult(
                media_domain=media_domain,
                thematic=thematic,
                article_title=article.link.title,
                article_url=str(article.link.href),
                video_url=str(final_video.url),
                success=True,
            )

        except Exception as e:
            print(f"   ❌ Error: {str(e)}")
            return ProcessingResult(
                media_domain=media_domain,
                thematic=thematic,
                article_title=article.link.title if article else "Unknown",
                article_url=str(article.link.href) if article else "",
                video_url="",
                success=False,
                error_message=str(e),
            )

    def run_multi_thematic(self) -> List[ProcessingResult]:
        """Run pipeline for multiple thematics across media sources."""
        results = []

        print("=" * 60)
        print("MULTI-THEMATIC CONTENT GENERATION PIPELINE")
        print("=" * 60)

        # Process each enabled media source
        for media_domain, source_config in self.config["media_sources"].items():
            if not source_config.get("enabled", False):
                continue

            sector = source_config.get(
                "sector", GCS_CONFIG.get("default_sector", "articles")
            )

            print(f"\n📡 Processing media source: {media_domain}")
            print(f"   Sector: {sector}")
            print(f"   Thematics: {', '.join(source_config['thematics'])}")

            # Process each thematic
            for thematic in source_config["thematics"]:
                print(f"\n🎯 Checking thematic: {thematic}")

                try:
                    # Scrape articles for this thematic
                    articles = self.scrape_articles(media_domain, thematic)

                    if not articles:
                        print(f"   ⚠️ No articles found for {thematic}")
                        continue

                    print(f"   Found {len(articles)} articles")

                    # Find unprocessed article
                    article = self.find_unprocessed_article(
                        articles, media_domain, thematic, sector
                    )

                    if not article:
                        print(f"   ⏭️ All articles already processed for {thematic}")
                        continue

                    # Process the article
                    result = self.process_single_article(
                        article, media_domain, thematic, sector
                    )
                    results.append(result)

                    # Process only the configured number of articles per thematic
                    if len(
                        [r for r in results if r.thematic == thematic and r.success]
                    ) >= self.config.get("articles_per_thematic", 1):
                        continue

                except Exception as e:
                    print(f"   ❌ Error processing {thematic}: {str(e)}")
                    results.append(
                        ProcessingResult(
                            media_domain=media_domain,
                            thematic=thematic,
                            article_title="Error",
                            article_url="",
                            video_url="",
                            success=False,
                            error_message=str(e),
                        )
                    )

        return results

    def run_single(self) -> ProcessingResult:
        """Run pipeline for a single thematic."""
        media_domain = self.config["single_media_domain"]
        thematic = self.config["single_thematic"]

        # Get sector from media sources config or use default
        sector = (
            self.config["media_sources"]
            .get(media_domain, {})
            .get("sector", GCS_CONFIG.get("default_sector", "articles"))
        )

        print("=" * 60)
        print("SINGLE THEMATIC CONTENT GENERATION")
        print("=" * 60)
        print(f"Media: {media_domain}")
        print(f"Sector: {sector}")
        print(f"Thematic: {thematic}")

        # Scrape articles
        articles = self.scrape_articles(media_domain, thematic)

        if not articles:
            print("❌ No articles found")
            return ProcessingResult(
                media_domain=media_domain,
                thematic=thematic,
                article_title="No articles",
                article_url="",
                video_url="",
                success=False,
                error_message="No articles found",
            )

        # Find unprocessed article
        article = self.find_unprocessed_article(
            articles, media_domain, thematic, sector
        )

        if not article:
            print("❌ All articles already processed")
            return ProcessingResult(
                media_domain=media_domain,
                thematic=thematic,
                article_title="All processed",
                article_url="",
                video_url="",
                success=False,
                error_message="All articles already processed",
            )

        # Process the article
        return self.process_single_article(article, media_domain, thematic, sector)

    def run(self):
        """Execute the pipeline based on configured mode."""
        mode = self.config.get("processing_mode", "single")

        if mode == "multi_thematic":
            results = self.run_multi_thematic()
            self.print_summary(results)
            self.save_batch_results(results)
        else:
            result = self.run_single()
            if result.success:
                print("\n✅ Pipeline completed successfully!")
                print(f"Video URL: {result.video_url}")
            else:
                print(f"\n❌ Pipeline failed: {result.error_message}")

    def print_summary(self, results: List[ProcessingResult]):
        """Print a summary of all processing results."""
        print("\n" + "=" * 60)
        print("PROCESSING SUMMARY")
        print("=" * 60)

        successful = [r for r in results if r.success]
        failed = [r for r in results if not r.success]

        print(f"\n✅ Successful: {len(successful)}")
        for result in successful:
            print(f"   • {result.media_domain}/{result.thematic}")
            print(f"     {result.article_title[:60]}...")
            print(f"     Video: {result.video_url}")

        if failed:
            print(f"\n❌ Failed: {len(failed)}")
            for result in failed:
                print(f"   • {result.media_domain}/{result.thematic}")
                print(f"     Error: {result.error_message}")

        print("\n" + "=" * 60)
        print(f"Total processed: {len(results)}")
        print(
            f"Success rate: {len(successful)}/{len(results)} ({100*len(successful)/len(results):.0f}%)"
            if results
            else "N/A"
        )

    def save_batch_results(self, results: List[ProcessingResult]):
        """Save batch processing results to a JSON file."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        results_path = os.path.join(
            self.config["output_dir"], f"batch_results_{timestamp}.json"
        )

        results_data = [
            {
                "media_domain": r.media_domain,
                "thematic": r.thematic,
                "article_title": r.article_title,
                "article_url": r.article_url,
                "video_url": r.video_url,
                "success": r.success,
                "error_message": r.error_message,
            }
            for r in results
        ]

        with open(results_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "timestamp": datetime.now().isoformat(),
                    "mode": "multi_thematic",
                    "results": results_data,
                    "statistics": {
                        "total": len(results),
                        "successful": len([r for r in results if r.success]),
                        "failed": len([r for r in results if not r.success]),
                    },
                },
                f,
                ensure_ascii=False,
                indent=2,
            )

        print(f"\nResults saved to: {results_path}")

    def create_video_with_urls(
        self, audio_url: str, video_urls: List[str], audio_length: float
    ) -> Optional[RenderResponse]:
        """Create video with generated video URLs."""
        response = self.video_gen.create_video(
            audio_url=audio_url,
            video_urls=video_urls,
            audio_length=audio_length,
            outro_config=self.config["outro_text"],
            outro_duration=self.config["outro_duration"],
            extra_audio_padding=self.config["extra_audio_padding"],
        )

        if response:
            print(f"      Video render initiated (ID: {response.id})")
            response = self.video_gen.wait_for_render(
                response.id, max_wait=120, poll_interval=3
            )

        return response

    def add_subtitles(self, video_url: str) -> Optional[RenderResponse]:
        """Add subtitles to the video."""
        response = self.video_gen.add_subtitles(video_url)

        if response:
            print(f"      Subtitle render initiated (ID: {response.id})")
            response = self.video_gen.wait_for_render(
                response.id, max_wait=90, poll_interval=2
            )

        return response

    def upload_video_to_gcs(self, video_url: str, gcs_path: str) -> Optional[str]:
        """Download a video from URL and upload it to GCS.

        Args:
            video_url: URL of the video to download
            gcs_path: Target path in GCS bucket

        Returns:
            GCS public URL if successful, None otherwise
        """
        import requests
        import tempfile

        try:
            # Download video to temporary file
            print(f"      Downloading video from {video_url}...")
            response = requests.get(video_url, stream=True, timeout=60)
            response.raise_for_status()

            # Create temporary file
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp_file:
                tmp_path = tmp_file.name
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        tmp_file.write(chunk)

            # Upload to GCS
            print(f"      Uploading to GCS: {gcs_path}...")
            upload_result = self.gcs.upload_file(tmp_path, gcs_path)

            # Clean up temporary file
            try:
                os.unlink(tmp_path)
            except Exception as e:
                print(f"      ⚠️ Warning: Failed to delete temp file {tmp_path}: {e}")

            if upload_result:
                return upload_result.get("url")
            return None

        except Exception as e:
            print(f"      ❌ Error uploading video to GCS: {e}")
            return None

    def list_processed_content(self):
        """List all processed content organized by media and thematic."""
        print("\n📋 Listing all processed content...")

        for media_domain, source_config in self.config["media_sources"].items():
            if not source_config.get("enabled", False):
                continue

            sector = source_config.get(
                "sector", GCS_CONFIG.get("default_sector", "articles")
            )

            print(f"\n📡 {media_domain} (Sector: {sector})")

            for thematic in source_config["thematics"]:
                prefix = f"media/{sector}/{media_domain}/{thematic}/"
                blobs = self.gcs.list_blobs_with_prefix(prefix)

                audio_files = [b for b in blobs if b.endswith("/audio.mp3")]

                if audio_files:
                    print(f"   • {thematic}: {len(audio_files)} processed articles")
                else:
                    print(f"   • {thematic}: No processed articles")


def main():
    """Main entry point."""
    try:
        pipeline = ContentPipeline()

        # Command line options
        import sys

        if len(sys.argv) > 1:
            if sys.argv[1] == "--list":
                pipeline.list_processed_content()
            elif sys.argv[1] == "--single":
                # Force single mode
                pipeline.config["processing_mode"] = "single"
                pipeline.run()
            elif sys.argv[1] == "--multi":
                # Force multi mode
                pipeline.config["processing_mode"] = "multi_thematic"
                pipeline.run()
            else:
                print("Unknown option. Use --list, --single, or --multi")
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
