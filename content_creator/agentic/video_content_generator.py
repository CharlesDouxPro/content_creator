#!/usr/bin/env python3
"""
Advanced TikTok Video Content Generator
Creates engaging videos with images synchronized to audio narration.
"""

import json
import time
import requests
import numpy as np
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass
from datetime import datetime
import re
from openai import OpenAI
from mutagen.mp3 import MP3
import wave
import struct
import os

from config import API_KEYS, AI_CONFIG, GCS_CONFIG
from modules import GCSManager


@dataclass
class VideoSegment:
    """Represents a video segment with timing and content."""
    start_time: float
    end_time: float
    text: str
    image_prompt: str
    transition_type: str
    keywords: List[str]


@dataclass
class ContentPlan:
    """Complete content plan for video generation."""
    segments: List[VideoSegment]
    total_duration: float
    style_theme: str
    color_palette: List[str]
    mood: str


class TikTokVideoContentGenerator:
    """
    Advanced video content generator that creates perfectly synchronized
    videos for TikTok with images matching audio narration.
    """

    def __init__(self):
        self.client = OpenAI(
            api_key=API_KEYS["deepinfra_api_key"],
            base_url=API_KEYS["deepinfra_base_url"]
        )
        self.model_name = AI_CONFIG["model_name"]
        self.gcs_manager = GCSManager()

    def analyze_audio_and_text(
        self,
        audio_path: str,
        transcript: str,
        audio_length: float
    ) -> Dict:
        """
        Analyze audio and text to create timing segments.

        Args:
            audio_path: Path to the audio file
            transcript: Text transcript of the audio
            audio_length: Total audio length in seconds

        Returns:
            Analysis results with sentence timings
        """
        # Split transcript into sentences
        sentences = self._split_into_sentences(transcript)

        # Estimate timing for each sentence based on character count
        total_chars = len(transcript)
        timings = []
        current_time = 0.0

        for sentence in sentences:
            # Calculate proportion of total time this sentence should take
            sentence_duration = (len(sentence) / total_chars) * audio_length

            timings.append({
                "text": sentence,
                "start": current_time,
                "end": current_time + sentence_duration,
                "duration": sentence_duration
            })

            current_time += sentence_duration

        # Detect pauses in audio for better segmentation (if needed)
        audio_analysis = self._analyze_audio_pauses(audio_path) if os.path.exists(audio_path) else None

        # Adjust timings based on audio analysis if available
        if audio_analysis:
            timings = self._adjust_timings_with_audio(timings, audio_analysis, audio_length)

        return {
            "sentences": sentences,
            "timings": timings,
            "total_duration": audio_length,
            "segments_count": len(sentences)
        }

    def create_content_plan(
        self,
        transcript: str,
        audio_length: float,
        article_context: Dict = None
    ) -> ContentPlan:
        """
        Create a detailed content plan with synchronized visuals.

        Args:
            transcript: The narration text
            audio_length: Total audio duration
            article_context: Optional context about the article (title, theme, etc.)

        Returns:
            Complete content plan with timed segments
        """

        # Determine video style based on content
        style_analysis = self._analyze_content_style(transcript, article_context)

        # Create optimal segments (3-5 seconds each for TikTok)
        segments = self._create_optimal_segments(
            transcript,
            audio_length,
            target_segment_duration=4.0  # Optimal for TikTok
        )

        # Generate image prompts for each segment
        segments_with_prompts = self._generate_image_prompts(
            segments,
            style_analysis,
            article_context
        )

        return ContentPlan(
            segments=segments_with_prompts,
            total_duration=audio_length,
            style_theme=style_analysis["theme"],
            color_palette=style_analysis["colors"],
            mood=style_analysis["mood"]
        )

    def _split_into_sentences(self, text: str) -> List[str]:
        """Split text into sentences intelligently."""
        # Handle French punctuation
        sentences = re.split(r'[.!?]+', text)
        # Clean and filter
        sentences = [s.strip() for s in sentences if s.strip()]

        # Merge very short sentences
        merged = []
        buffer = ""
        for sentence in sentences:
            if len(buffer) > 0 and len(buffer) < 30:
                buffer += " " + sentence
            else:
                if buffer:
                    merged.append(buffer)
                buffer = sentence
        if buffer:
            merged.append(buffer)

        return merged

    def _analyze_audio_pauses(self, audio_path: str) -> Optional[List[float]]:
        """
        Analyze audio file to detect natural pauses.
        Returns timestamps of significant pauses.
        """
        try:
            # This is a simplified pause detection
            # In production, you'd use librosa or similar for better analysis
            return None  # Placeholder for now
        except:
            return None

    def _adjust_timings_with_audio(
        self,
        timings: List[Dict],
        audio_pauses: List[float],
        total_duration: float
    ) -> List[Dict]:
        """Adjust text timings based on detected audio pauses."""
        # Implement smart timing adjustment based on audio pauses
        # This would align text boundaries with natural speech pauses
        return timings

    def _analyze_content_style(self, transcript: str, context: Dict = None) -> Dict:
        """Analyze content to determine visual style."""

        prompt = f"""
        Analyze this TikTok video transcript and suggest a visual style:

        Transcript: {transcript[:500]}

        Return a JSON with:
        - theme: visual theme (modern, dramatic, energetic, calm, etc.)
        - mood: emotional mood (exciting, serious, funny, inspiring, etc.)
        - colors: list of 3-4 hex color codes for the palette
        - visual_style: description of visual style in 10 words

        Return ONLY the JSON, no explanation.
        """

        response = self.client.chat.completions.create(
            model=self.model_name,
            messages=[
                {"role": "system", "content": "You are a visual design expert for social media."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=200
        )

        try:
            style_data = json.loads(response.choices[0].message.content)
            return style_data
        except:
            # Fallback style
            return {
                "theme": "modern",
                "mood": "engaging",
                "colors": ["#FF6B6B", "#4ECDC4", "#45B7D1", "#96CEB4"],
                "visual_style": "Dynamic modern visuals with high contrast and movement"
            }

    def _create_optimal_segments(
        self,
        transcript: str,
        audio_length: float,
        target_segment_duration: float = 4.0
    ) -> List[VideoSegment]:
        """
        Create optimal video segments for TikTok engagement.

        Args:
            transcript: Full transcript
            audio_length: Total duration
            target_segment_duration: Target duration per segment (3-5 seconds optimal)
        """

        sentences = self._split_into_sentences(transcript)
        num_segments = max(3, min(12, int(audio_length / target_segment_duration)))

        # Group sentences into segments
        sentences_per_segment = max(1, len(sentences) // num_segments)
        segments = []

        current_time = 0.0
        time_per_segment = audio_length / num_segments

        for i in range(0, len(sentences), sentences_per_segment):
            segment_sentences = sentences[i:i + sentences_per_segment]
            segment_text = " ".join(segment_sentences)

            # Extract keywords for this segment
            keywords = self._extract_keywords(segment_text)

            # Determine transition type based on position
            if i == 0:
                transition = "fade_in"
            elif i >= len(sentences) - sentences_per_segment:
                transition = "fade_out"
            else:
                # Vary transitions for engagement
                transitions = ["cut", "slide", "zoom", "blur_transition"]
                transition = transitions[i % len(transitions)]

            segment = VideoSegment(
                start_time=current_time,
                end_time=min(current_time + time_per_segment, audio_length),
                text=segment_text,
                image_prompt="",  # Will be filled next
                transition_type=transition,
                keywords=keywords
            )

            segments.append(segment)
            current_time = segment.end_time

        return segments

    def _extract_keywords(self, text: str) -> List[str]:
        """Extract key visual words from text."""
        # Remove common words
        stop_words = {"le", "la", "les", "un", "une", "des", "et", "ou", "de", "du", "pour", "avec", "dans", "sur"}
        words = text.lower().split()
        keywords = [w for w in words if len(w) > 4 and w not in stop_words]
        return keywords[:3]  # Top 3 keywords

    def _generate_image_prompts(
        self,
        segments: List[VideoSegment],
        style_analysis: Dict,
        context: Dict = None
    ) -> List[VideoSegment]:
        """
        Generate image prompts for each segment that match the narration.

        This is the KEY function that ensures images match the audio.
        """

        # Build context for prompt generation
        style_description = f"{style_analysis['theme']} style, {style_analysis['mood']} mood"
        color_info = f"Color palette: {', '.join(style_analysis['colors'])}"

        all_prompts = []

        for i, segment in enumerate(segments):
            # Create a prompt that matches the specific text content
            prompt = f"""
            Create a visual description for a TikTok video frame.

            Narration text: "{segment.text}"
            Keywords: {', '.join(segment.keywords)}
            Visual style: {style_description}
            {color_info}
            Frame {i+1} of {len(segments)}

            Requirements:
            - Match the narration content exactly
            - Use cinematic composition
            - Include dynamic elements for engagement
            - Avoid text or words in the image
            - Make it visually striking for social media

            Return ONLY a detailed image generation prompt in English (max 50 words).
            """

            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": "You are an expert at creating visual prompts for AI image generation."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=100
            )

            image_prompt = response.choices[0].message.content.strip()

            # Add style modifiers for consistency
            image_prompt = self._enhance_prompt_with_style(image_prompt, style_analysis, i, len(segments))

            segment.image_prompt = image_prompt
            all_prompts.append(image_prompt)

            # Small delay to avoid rate limiting
            time.sleep(0.1)

        # Ensure visual continuity
        segments = self._ensure_visual_continuity(segments)

        return segments

    def _enhance_prompt_with_style(
        self,
        prompt: str,
        style: Dict,
        index: int,
        total: int
    ) -> str:
        """Enhance prompt with consistent style elements."""

        # Add technical quality modifiers
        quality_modifiers = "ultra detailed, 4K, professional photography"

        # Add style-specific modifiers
        style_modifiers = {
            "modern": "clean minimal contemporary",
            "dramatic": "high contrast cinematic moody",
            "energetic": "vibrant dynamic action-packed",
            "calm": "serene peaceful soft lighting",
            "serious": "professional focused sharp",
            "funny": "colorful playful whimsical",
            "inspiring": "uplifting bright aspirational"
        }

        style_mod = style_modifiers.get(style["mood"], "professional")

        # Add progression modifiers
        if index == 0:
            progression = "establishing shot"
        elif index == total - 1:
            progression = "closing shot"
        else:
            progression = "medium shot"

        return f"{prompt}, {quality_modifiers}, {style_mod}, {progression}"

    def _ensure_visual_continuity(self, segments: List[VideoSegment]) -> List[VideoSegment]:
        """Ensure visual continuity between segments."""

        # Add continuity elements to prompts
        for i in range(1, len(segments)):
            # Check if current and previous segments should have visual connection
            if "similar visual style to previous" not in segments[i].image_prompt:
                segments[i].image_prompt += ", maintaining visual consistency"

        return segments

    def generate_videos(
        self,
        content_plan: ContentPlan,
        article_gcs_path: str,
        use_placeholder: bool = False
    ) -> List[str]:
        """
        Generate actual videos from the content plan.

        Args:
            content_plan: The complete content plan with segments
            article_gcs_path: GCS base path for storing videos
            use_placeholder: If True, generate placeholder URLs instead of real videos

        Returns:
            List of video URLs
        """

        video_urls = []
        video_base_path = article_gcs_path.replace("/audio.mp3", "").replace("media/audio/", "media/videos/")

        print(f"\n🎬 Generating {len(content_plan.segments)} synchronized videos...")
        print(f"   Style: {content_plan.style_theme}, Mood: {content_plan.mood}")

        for i, segment in enumerate(content_plan.segments):
            print(f"\n   Segment {i+1}/{len(content_plan.segments)}:")
            print(f"   Time: {segment.start_time:.1f}s - {segment.end_time:.1f}s")
            print(f"   Text: {segment.text[:50]}...")
            print(f"   Prompt: {segment.image_prompt[:80]}...")

            if use_placeholder:
                # Generate placeholder URL
                video_url = f"https://storage.googleapis.com/{GCS_CONFIG['bucket_name']}/{video_base_path}/video_{i+1}.mp4"
            else:
                # TODO: Integrate with actual video generation API (Veo3, Runway, etc.)
                video_url = self._generate_single_video(
                    segment.image_prompt,
                    duration=segment.end_time - segment.start_time,
                    style=content_plan.style_theme,
                    transition=segment.transition_type
                )

            video_urls.append(video_url)

        return video_urls

    def _generate_single_video(
        self,
        prompt: str,
        duration: float,
        style: str,
        transition: str
    ) -> str:
        """
        Generate a single video segment using AI.

        This is where you'd integrate with Veo3, Runway, Stability AI, etc.
        """

        # Placeholder for actual API integration
        # Example for when Veo3 or other API is available:

        """
        # Example Veo3 integration:
        response = requests.post(
            "https://api.veo3.google.com/generate",
            headers={"Authorization": f"Bearer {API_KEYS['veo3_api_key']}"},
            json={
                "prompt": prompt,
                "duration": duration,
                "style": style,
                "resolution": "720x1280",  # TikTok format
                "fps": 30,
                "transition_in": transition,
                "motion": "subtle"  # Subtle motion for image-like videos
            }
        )

        video_data = response.json()
        video_url = video_data["url"]

        # Download and upload to GCS
        # ...

        return gcs_url
        """

        # For now, return placeholder
        return f"placeholder_video_url_{hash(prompt)}.mp4"

    def create_optimized_tiktok_video(
        self,
        audio_path: str,
        transcript: str,
        article_context: Dict,
        output_path: str
    ) -> Dict:
        """
        Main function to create an optimized TikTok video.

        Args:
            audio_path: Path to audio narration
            transcript: Text transcript
            article_context: Article information (title, theme, etc.)
            output_path: Where to save the final video

        Returns:
            Dictionary with video URLs and metadata
        """

        print("\n🎯 Creating Optimized TikTok Video")
        print("=" * 50)

        # Get audio length
        audio = MP3(audio_path)
        audio_length = audio.info.length
        print(f"Audio duration: {audio_length:.1f} seconds")

        # Analyze audio and text
        print("\n📊 Analyzing audio and text synchronization...")
        analysis = self.analyze_audio_and_text(audio_path, transcript, audio_length)
        print(f"   Created {len(analysis['timings'])} timing segments")

        # Create content plan
        print("\n📝 Creating content plan...")
        content_plan = self.create_content_plan(transcript, audio_length, article_context)
        print(f"   Generated {len(content_plan.segments)} video segments")
        print(f"   Style: {content_plan.style_theme}")
        print(f"   Mood: {content_plan.mood}")

        # Generate videos
        print("\n🎥 Generating synchronized videos...")
        video_urls = self.generate_videos(
            content_plan,
            article_gcs_path=output_path,
            use_placeholder=True  # Set to False when you have real API
        )

        # Return results
        result = {
            "success": True,
            "audio_length": audio_length,
            "segments": [
                {
                    "index": i,
                    "start": seg.start_time,
                    "end": seg.end_time,
                    "duration": seg.end_time - seg.start_time,
                    "text": seg.text,
                    "prompt": seg.image_prompt,
                    "video_url": url,
                    "transition": seg.transition_type
                }
                for i, (seg, url) in enumerate(zip(content_plan.segments, video_urls))
            ],
            "style": {
                "theme": content_plan.style_theme,
                "mood": content_plan.mood,
                "colors": content_plan.color_palette
            },
            "total_videos": len(video_urls),
            "video_urls": video_urls
        }

        print("\n✅ Video content generation complete!")
        print(f"   Total segments: {len(video_urls)}")
        print(f"   Average segment duration: {audio_length/len(video_urls):.1f}s")

        return result


# Example usage
def main():
    """Example of how to use the TikTok video content generator."""

    generator = TikTokVideoContentGenerator()

    # Example inputs
    audio_path = "./output/output.mp3"
    transcript = """
    Le match de football d'hier soir a été extraordinaire.
    L'équipe de France a marqué trois buts magnifiques.
    Les supporters étaient en délire dans le stade.
    C'est une victoire historique pour le football français.
    Abonne-toi pour plus de contenu sportif.
    """

    article_context = {
        "title": "Victoire historique de l'équipe de France",
        "theme": "sport",
        "media": "www.20minutes.fr"
    }

    # Create the video
    result = generator.create_optimized_tiktok_video(
        audio_path=audio_path,
        transcript=transcript,
        article_context=article_context,
        output_path="media/videos/test/"
    )

    # Print results
    print("\n" + "=" * 50)
    print("VIDEO GENERATION RESULTS")
    print("=" * 50)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()