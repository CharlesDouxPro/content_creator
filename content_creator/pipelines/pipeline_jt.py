#!/usr/bin/env python3
"""
Journal Télévisé (JT) pipeline script for TikTok/Instagram content generation.

This is an alternative pipeline that creates videos with:
- Pre-generated presenter videos (stored on GCS)
- Context images overlaid in Picture-in-Picture
- Intelligent timing via Claude Opus 4.5 analysis

To use:
1. Upload your presenter videos to GCS (see config_jt.py for paths)
2. Set ANTHROPIC_API_KEY environment variable
3. Run: python pipeline_jt.py

The original pipeline (pipeline_multi.py) remains unchanged.
"""

import os
import json
import hashlib
from typing import List, Optional
from datetime import datetime
from urllib.parse import urlparse
from dataclasses import dataclass

from config import PIPELINE_CONFIG, GCS_CONFIG
from config_jt import JT_CONFIG
from modules import (
    NewsScraper,
    ArticleSummarizer,
    GCSManager,
    VideoGenerator,
    FullArticle,
    RenderResponse,
)
from modules_jt import (
    ScriptAnalyzer,
    PresenterVideoManager,
    JTVideoComposer,
    ContextImageFetcher,
    TimedImageSegment,
)


@dataclass
class JTProcessingResult:
    """Result of processing a single article in JT mode."""

    media_domain: str
    thematic: str
    article_title: str
    article_url: str
    video_url: str
    success: bool
    context_images_count: int = 0
    presenter_segments_count: int = 0
    error_message: Optional[str] = None


class JTContentPipeline:
    """
    Pipeline spécialisé pour le format Journal Télévisé.

    Différences avec le pipeline classique:
    - Utilise des vidéos présentateur pré-générées (pas de Pexels/Runway)
    - Analyse le script avec Claude Opus 4.5 pour placer les images
    - Superpose les images de contexte en PiP
    """

    def __init__(self):
        self.config = PIPELINE_CONFIG
        self.jt_config = JT_CONFIG
        self.scraper = NewsScraper()
        self.summarizer = ArticleSummarizer()
        self.gcs = GCSManager()
        self.video_gen = VideoGenerator()

        # JT-specific components
        self.script_analyzer = ScriptAnalyzer()
        self.presenter_manager = PresenterVideoManager(self.jt_config)
        self.jt_composer = JTVideoComposer(self.jt_config)
        self.image_fetcher = ContextImageFetcher()

        # Create output directory if needed
        os.makedirs(self.config["output_dir"], exist_ok=True)

    def get_article_base_path(
        self, article_url: str, media_domain: str, thematic: str, sector: str
    ) -> str:
        """Generate a unique GCS base path for an article."""
        url_hash = hashlib.md5(str(article_url).encode()).hexdigest()[:10]
        parsed = urlparse(str(article_url))
        path_parts = parsed.path.strip("/").split("/")
        article_slug = path_parts[-1] if path_parts else "article"
        article_slug = article_slug.replace(".html", "")[:50]

        today = datetime.now().strftime("%Y-%m-%d")
        # Add 'jt' suffix to distinguish from regular pipeline
        return f"media/{sector}/{media_domain}/{thematic}/{today}/{article_slug}-{url_hash}-jt/"

    def find_unprocessed_article(
        self, articles: List[FullArticle], media_domain: str, thematic: str, sector: str
    ) -> Optional[FullArticle]:
        """Find the first article that hasn't been processed yet."""
        if not self.config.get("skip_processed", True):
            return articles[0] if articles else None

        for article in articles:
            base_path = self.get_article_base_path(
                article.link.href, media_domain, thematic, sector
            )
            audio_path = f"{base_path}audio.mp3"

            if not self.gcs.check_blob_exists(audio_path):
                return article

        return None

    def scrape_articles(self, media_domain: str, thematic: str) -> List[FullArticle]:
        """Scrape and process articles from a specific source."""
        url = f"https://{media_domain}/{thematic}/"
        links = self.scraper.scrape_links_older_than_24h(url)
        unique_links = {link.href: link for link in links}.values()

        articles = []
        for link in unique_links:
            blocks = self.scraper.scrape_article(link.href)
            if blocks:
                articles.append(FullArticle(link=link, content=blocks))

        return articles

    def process_single_article(
        self, article: FullArticle, media_domain: str, thematic: str, sector: str
    ) -> JTProcessingResult:
        """Process a single article through the JT pipeline."""

        try:
            print(f"\n{'='*50}")
            print(f"📺 [JT MODE] Processing: {article.link.title[:60]}...")
            print(f"   Media: {media_domain}")
            print(f"   Sector: {sector}")
            print(f"   Thematic: {thematic}")
            print(f"   URL: {article.link.href}")

            # Generate GCS base path for this article
            article_base_path = self.get_article_base_path(
                article.link.href, media_domain, thematic, sector
            )
            article_audio_path = f"{article_base_path}audio.mp3"

            # 1. Generate summary (same as original)
            print("   🤖 Generating summary...")
            summary = self.summarizer.summarize_article(article)
            if not summary:
                return JTProcessingResult(
                    media_domain=media_domain,
                    thematic=thematic,
                    article_title=article.link.title,
                    article_url=str(article.link.href),
                    video_url="",
                    success=False,
                    error_message="Failed to generate summary",
                )
            print(f"   Summary: {summary[:100]}...")

            # 2. Generate audio (same as original)
            print("   🎙️ Generating audio...")
            safe_thematic = thematic.replace("/", "_").strip("_")
            audio_filename = f"jt_{media_domain}_{safe_thematic}_{hashlib.md5(str(article.link.href).encode()).hexdigest()[:8]}.mp3"
            audio_path = os.path.join(self.config["output_dir"], audio_filename)
            audio_file = self.summarizer.text_to_speech_google(summary, audio_path)
            if not audio_file:
                return JTProcessingResult(
                    media_domain=media_domain,
                    thematic=thematic,
                    article_title=article.link.title,
                    article_url=str(article.link.href),
                    video_url="",
                    success=False,
                    error_message="Failed to generate audio",
                )

            audio_length = self.video_gen.get_audio_length(audio_path)
            print(f"   Audio length: {audio_length:.1f}s")

            # 3. Upload audio to cloud
            print("   ☁️ Uploading audio...")
            audio_upload = self.gcs.upload_file(audio_path, article_audio_path)
            if not audio_upload:
                return JTProcessingResult(
                    media_domain=media_domain,
                    thematic=thematic,
                    article_title=article.link.title,
                    article_url=str(article.link.href),
                    video_url="",
                    success=False,
                    error_message="Failed to upload audio",
                )
            audio_url = audio_upload["url"]

            # 4. NEW: Analyze script with Claude Opus 4.5
            print("   🧠 Analyzing script for context images (Claude Opus 4.5)...")
            timed_segments = self.script_analyzer.analyze_script(summary, audio_length)
            print(f"   Found {len(timed_segments)} context image moments")

            for seg in timed_segments:
                print(f"      - [{seg.start_time:.1f}s-{seg.end_time:.1f}s] {seg.image_query} ({seg.importance})")

            # 5. NEW: Build presenter timeline
            print("   👤 Building presenter timeline...")
            presenter_timeline = self.presenter_manager.build_presenter_timeline(audio_length)
            print(f"   Created {len(presenter_timeline)} presenter segments")

            # 6. NEW: Fetch context images
            print("   🖼️ Fetching context images...")
            context_images = self.image_fetcher.fetch_images(
                timed_segments, article_base_path
            )
            print(f"   Retrieved {len(context_images)} context images")

            # 7. NEW: Compose JT video with Creatomate
            print("   🎬 Composing JT video...")
            payload = self.jt_composer.build_creatomate_payload(
                audio_url=audio_url,
                audio_length=audio_length,
                presenter_timeline=presenter_timeline,
                context_images=context_images,
                outro_config=self.config["outro_text"],
                outro_duration=self.jt_config.get("outro_duration", 4.0),
                extra_audio_padding=self.jt_config.get("extra_audio_padding", 3.0),
            )

            # Save payload for debugging
            payload_path = os.path.join(self.config["output_dir"], "last_jt_payload.json")
            with open(payload_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)

            # 8. Render video
            print("   🎥 Rendering video...")
            render_response = self.jt_composer.render_video(payload)
            if not render_response:
                return JTProcessingResult(
                    media_domain=media_domain,
                    thematic=thematic,
                    article_title=article.link.title,
                    article_url=str(article.link.href),
                    video_url="",
                    success=False,
                    context_images_count=len(context_images),
                    presenter_segments_count=len(presenter_timeline),
                    error_message="Failed to render video",
                )

            render_id = render_response.get("id")
            print(f"      Render initiated (ID: {render_id})")

            # Wait for render completion
            video_response = self.video_gen.wait_for_render(
                render_id, max_wait=180, poll_interval=3
            )
            if not video_response:
                return JTProcessingResult(
                    media_domain=media_domain,
                    thematic=thematic,
                    article_title=article.link.title,
                    article_url=str(article.link.href),
                    video_url="",
                    success=False,
                    context_images_count=len(context_images),
                    presenter_segments_count=len(presenter_timeline),
                    error_message="Video render timed out",
                )

            # Upload video without subtitles to GCS
            print("   ☁️ Uploading video to GCS...")
            video_no_subs_gcs_path = f"{article_base_path}video_no_subtitles.mp4"
            video_no_subs_uploaded = self._upload_video_to_gcs(
                str(video_response.url), video_no_subs_gcs_path
            )

            # 9. Add subtitles
            print("   📝 Adding subtitles...")
            subtitle_response = self.video_gen.add_subtitles(str(video_response.url))
            if not subtitle_response:
                return JTProcessingResult(
                    media_domain=media_domain,
                    thematic=thematic,
                    article_title=article.link.title,
                    article_url=str(article.link.href),
                    video_url=str(video_response.url),  # Return video without subtitles
                    success=True,
                    context_images_count=len(context_images),
                    presenter_segments_count=len(presenter_timeline),
                    error_message="Subtitles failed but video is ready",
                )

            print(f"      Subtitle render initiated (ID: {subtitle_response.id})")
            final_video = self.video_gen.wait_for_render(
                subtitle_response.id, max_wait=90, poll_interval=2
            )

            if not final_video:
                # Return video without subtitles if subtitle render fails
                return JTProcessingResult(
                    media_domain=media_domain,
                    thematic=thematic,
                    article_title=article.link.title,
                    article_url=str(article.link.href),
                    video_url=str(video_response.url),
                    success=True,
                    context_images_count=len(context_images),
                    presenter_segments_count=len(presenter_timeline),
                    error_message="Subtitles timed out but video is ready",
                )

            # Upload final video with subtitles
            final_video_gcs_path = f"{article_base_path}final_video.mp4"
            self._upload_video_to_gcs(str(final_video.url), final_video_gcs_path)

            # Save metadata
            self._save_metadata(
                article_base_path,
                article,
                summary,
                audio_length,
                timed_segments,
                context_images,
                presenter_timeline,
                final_video,
            )

            print(f"\n\033[92m✅ SUCCESS: {final_video.url}\033[0m")

            return JTProcessingResult(
                media_domain=media_domain,
                thematic=thematic,
                article_title=article.link.title,
                article_url=str(article.link.href),
                video_url=str(final_video.url),
                success=True,
                context_images_count=len(context_images),
                presenter_segments_count=len(presenter_timeline),
            )

        except Exception as e:
            print(f"   ❌ Error: {str(e)}")
            import traceback
            traceback.print_exc()
            return JTProcessingResult(
                media_domain=media_domain,
                thematic=thematic,
                article_title=article.link.title if article else "Unknown",
                article_url=str(article.link.href) if article else "",
                video_url="",
                success=False,
                error_message=str(e),
            )

    def _upload_video_to_gcs(self, video_url: str, gcs_path: str) -> Optional[str]:
        """Download a video from URL and upload it to GCS."""
        import requests
        import tempfile

        try:
            response = requests.get(video_url, stream=True, timeout=60)
            response.raise_for_status()

            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp_file:
                tmp_path = tmp_file.name
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        tmp_file.write(chunk)

            upload_result = self.gcs.upload_file(tmp_path, gcs_path)

            try:
                os.unlink(tmp_path)
            except:
                pass

            if upload_result:
                return upload_result.get("url")
            return None

        except Exception as e:
            print(f"      ❌ Error uploading video to GCS: {e}")
            return None

    def _save_metadata(
        self,
        article_base_path: str,
        article: FullArticle,
        summary: str,
        audio_length: float,
        timed_segments: List[TimedImageSegment],
        context_images: List[dict],
        presenter_timeline: List[dict],
        final_video: RenderResponse,
    ):
        """Save JT-specific metadata to GCS."""
        meta_path = f"{article_base_path}jt_metadata.json"
        metadata = {
            "pipeline_mode": "journal_televise",
            "article_title": article.link.title,
            "article_url": str(article.link.href),
            "final_video_url": str(final_video.url),
            "created_at": datetime.now().isoformat(),
            "audio_duration": audio_length,
            "summary": summary,
            "context_images": [
                {
                    "url": img["url"],
                    "start_time": img["start_time"],
                    "duration": img["duration"],
                    "query": img.get("query", ""),
                }
                for img in context_images
            ],
            "timed_segments": [
                {
                    "text": seg.text[:100],
                    "start_time": seg.start_time,
                    "end_time": seg.end_time,
                    "image_query": seg.image_query,
                    "importance": seg.importance,
                }
                for seg in timed_segments
            ],
            "presenter_timeline": presenter_timeline,
        }

        local_meta_path = os.path.join(self.config["output_dir"], "temp_jt_metadata.json")
        with open(local_meta_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)
        self.gcs.upload_file(local_meta_path, meta_path)

    def run_multi_thematic(self) -> List[JTProcessingResult]:
        """Run JT pipeline for multiple thematics across media sources."""
        results = []

        print("=" * 60)
        print("📺 JOURNAL TÉLÉVISÉ - MULTI-THEMATIC PIPELINE")
        print("=" * 60)

        # Verify presenter videos exist
        print("\n🔍 Verifying presenter videos...")
        video_status = self.presenter_manager.verify_presenter_videos_exist()
        available = [name for name, exists in video_status.items() if exists]
        missing = [name for name, exists in video_status.items() if not exists]

        if not available:
            print("❌ No presenter videos found on GCS!")
            print("   Please upload videos to:")
            bucket = self.jt_config["presenter_videos"]["bucket"]
            base_path = self.jt_config["presenter_videos"]["base_path"]
            print(f"   gs://{bucket}/{base_path}")
            return results

        print(f"   ✅ Found {len(available)} presenter videos")
        if missing:
            print(f"   ⚠️ Missing {len(missing)} videos: {', '.join(missing)}")

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

            for thematic in source_config["thematics"]:
                print(f"\n🎯 Checking thematic: {thematic}")

                try:
                    articles = self.scrape_articles(media_domain, thematic)

                    if not articles:
                        print(f"   ⚠️ No articles found for {thematic}")
                        continue

                    print(f"   Found {len(articles)} articles")

                    article = self.find_unprocessed_article(
                        articles, media_domain, thematic, sector
                    )

                    if not article:
                        print(f"   ⏭️ All articles already processed for {thematic}")
                        continue

                    result = self.process_single_article(
                        article, media_domain, thematic, sector
                    )
                    results.append(result)

                except Exception as e:
                    print(f"   ❌ Error processing {thematic}: {str(e)}")
                    results.append(
                        JTProcessingResult(
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

    def run_single(self) -> JTProcessingResult:
        """Run JT pipeline for a single thematic."""
        media_domain = self.config["single_media_domain"]
        thematic = self.config["single_thematic"]
        sector = (
            self.config["media_sources"]
            .get(media_domain, {})
            .get("sector", GCS_CONFIG.get("default_sector", "articles"))
        )

        print("=" * 60)
        print("📺 JOURNAL TÉLÉVISÉ - SINGLE THEMATIC")
        print("=" * 60)
        print(f"Media: {media_domain}")
        print(f"Sector: {sector}")
        print(f"Thematic: {thematic}")

        # Verify presenter videos
        video_status = self.presenter_manager.verify_presenter_videos_exist()
        available = [name for name, exists in video_status.items() if exists]
        if not available:
            return JTProcessingResult(
                media_domain=media_domain,
                thematic=thematic,
                article_title="Error",
                article_url="",
                video_url="",
                success=False,
                error_message="No presenter videos found on GCS",
            )

        articles = self.scrape_articles(media_domain, thematic)

        if not articles:
            return JTProcessingResult(
                media_domain=media_domain,
                thematic=thematic,
                article_title="No articles",
                article_url="",
                video_url="",
                success=False,
                error_message="No articles found",
            )

        article = self.find_unprocessed_article(
            articles, media_domain, thematic, sector
        )

        if not article:
            return JTProcessingResult(
                media_domain=media_domain,
                thematic=thematic,
                article_title="All processed",
                article_url="",
                video_url="",
                success=False,
                error_message="All articles already processed",
            )

        return self.process_single_article(article, media_domain, thematic, sector)

    def run(self):
        """Execute the JT pipeline based on configured mode."""
        mode = self.config.get("processing_mode", "single")

        if mode == "multi_thematic":
            results = self.run_multi_thematic()
            self.print_summary(results)
            self.save_batch_results(results)
        else:
            result = self.run_single()
            if result.success:
                print("\n✅ JT Pipeline completed successfully!")
                print(f"Video URL: {result.video_url}")
                print(f"Context images: {result.context_images_count}")
                print(f"Presenter segments: {result.presenter_segments_count}")
            else:
                print(f"\n❌ JT Pipeline failed: {result.error_message}")

    def print_summary(self, results: List[JTProcessingResult]):
        """Print a summary of all processing results."""
        print("\n" + "=" * 60)
        print("📺 JT PROCESSING SUMMARY")
        print("=" * 60)

        successful = [r for r in results if r.success]
        failed = [r for r in results if not r.success]

        print(f"\n✅ Successful: {len(successful)}")
        for result in successful:
            print(f"   • {result.media_domain}/{result.thematic}")
            print(f"     {result.article_title[:60]}...")
            print(f"     Video: {result.video_url}")
            print(f"     Context images: {result.context_images_count}, Presenter segments: {result.presenter_segments_count}")

        if failed:
            print(f"\n❌ Failed: {len(failed)}")
            for result in failed:
                print(f"   • {result.media_domain}/{result.thematic}")
                print(f"     Error: {result.error_message}")

        print("\n" + "=" * 60)
        print(f"Total processed: {len(results)}")
        if results:
            print(
                f"Success rate: {len(successful)}/{len(results)} ({100*len(successful)/len(results):.0f}%)"
            )

    def save_batch_results(self, results: List[JTProcessingResult]):
        """Save batch processing results to a JSON file."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        results_path = os.path.join(
            self.config["output_dir"], f"jt_batch_results_{timestamp}.json"
        )

        results_data = [
            {
                "media_domain": r.media_domain,
                "thematic": r.thematic,
                "article_title": r.article_title,
                "article_url": r.article_url,
                "video_url": r.video_url,
                "success": r.success,
                "context_images_count": r.context_images_count,
                "presenter_segments_count": r.presenter_segments_count,
                "error_message": r.error_message,
            }
            for r in results
        ]

        with open(results_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "timestamp": datetime.now().isoformat(),
                    "mode": "journal_televise",
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


def main():
    """Main entry point for JT pipeline."""
    try:
        pipeline = JTContentPipeline()

        import sys

        if len(sys.argv) > 1:
            if sys.argv[1] == "--single":
                pipeline.config["processing_mode"] = "single"
                pipeline.run()
            elif sys.argv[1] == "--multi":
                pipeline.config["processing_mode"] = "multi_thematic"
                pipeline.run()
            elif sys.argv[1] == "--verify":
                # Just verify presenter videos exist
                print("🔍 Verifying presenter videos on GCS...")
                status = pipeline.presenter_manager.verify_presenter_videos_exist()
                for name, exists in status.items():
                    icon = "✅" if exists else "❌"
                    print(f"   {icon} {name}")
            else:
                print("Unknown option. Use --single, --multi, or --verify")
        else:
            pipeline.run()

    except KeyboardInterrupt:
        print("\n\n⚠️ JT Pipeline interrupted by user")
    except Exception as e:
        print(f"\n\n❌ JT Pipeline failed with error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
