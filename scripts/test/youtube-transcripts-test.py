

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.formatters import SRTFormatter

# 提供完整 URL 或 ID 都可以，先提取出干净的 video_id
raw_video_id = "_BrFKp-U8GI&t=292s"
video_id = raw_video_id.split("&")[0].split("?")[-1]

api = YouTubeTranscriptApi()
transcript = api.fetch(video_id, languages=["en"])

formatter = SRTFormatter()
srt = formatter.format_transcript(transcript)

with open("output.srt", "w", encoding="utf-8") as f:
    f.write(srt)
