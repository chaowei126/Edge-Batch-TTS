import asyncio
import json
import os
import argparse
import base64
import httpx
import pandas as pd
from datetime import timedelta
from pydub import AudioSegment
from tenacity import retry, stop_after_attempt, wait_exponential

def parse_args():
    parser = argparse.ArgumentParser(description="Google Cloud TTS 批量语音合成工具 (多女声修正版)")
    parser.add_argument("-i", "--input", default="input.csv", help="输入的 CSV 文件路径")
    parser.add_argument("-k", "--key", required=True, help="Google Cloud API Key")
    parser.add_argument("-r", "--rate", type=float, default=0.9, help="语速 (0.25-4.0)")
    parser.add_argument("-n", "--repeat", type=int, default=3, help="单句重复次数")
    parser.add_argument("-c", "--concurrent", type=int, default=5, help="最大并发数")
    parser.add_argument("-s", "--silence", type=int, default=1000, help="句子间停顿(ms)")
    parser.add_argument("-o", "--output_dir", default="google_tts_output", help="输出目录")
    parser.add_argument("-f", "--sub_format", choices=['lrc', 'srt', 'both'], default='lrc', help="字幕格式")
    return parser.parse_args()

class GoogleMultiVoiceEngine:
    def __init__(self, args):
        self.args = args
        self.base_name = os.path.splitext(os.path.basename(args.input))[0]
        self.output_dir = args.output_dir
        self.snippets_dir = os.path.join(self.output_dir, f"{self.base_name}_snippets")
        self.progress_file = os.path.join(self.output_dir, f"{self.base_name}_checkpoint.json")
        
        # 修正：Neural2-F を Neural2-C に変更（安定した女性音色の組み合わせ）
        self.voice_list = ["ja-JP-Neural2-B", "ja-JP-Neural2-C", "ja-JP-Wavenet-B"]
        self.semaphore = asyncio.Semaphore(args.concurrent)
        
        os.makedirs(self.snippets_dir, exist_ok=True)
        self.progress = self.load_progress()

    def load_progress(self):
        if os.path.exists(self.progress_file):
            with open(self.progress_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}

    def save_progress(self, index, metadata):
        self.progress[str(index)] = metadata
        with open(self.progress_file, 'w', encoding='utf-8') as f:
            json.dump(self.progress, f, ensure_ascii=False, indent=2)

    def format_lrc_time(self, ms):
        m, s = divmod(ms // 1000, 60)
        cs = (ms % 1000) // 10
        return f"[{m:02d}:{s:02d}.{cs:02d}]"

    def format_srt_time(self, ms):
        td = timedelta(milliseconds=ms)
        h, m, s = td.seconds // 3600, (td.seconds % 3600) // 60, td.seconds % 60
        return f"{h:02d}:{m:02d}:{s:02d},{td.microseconds // 1000:03d}"

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=6))
    async def synthesize_api(self, text, voice_name):
        url = f"https://texttospeech.googleapis.com/v1/text:synthesize?key={self.args.key}"
        payload = {
            "input": {"text": text},
            "voice": {"languageCode": "ja-JP", "name": voice_name},
            "audioConfig": {"audioEncoding": "MP3", "speakingRate": self.args.rate}
        }
        async with httpx.AsyncClient() as client:
            r = await client.post(url, json=payload, timeout=20)
            if r.status_code == 200:
                return base64.b64decode(r.json()["audioContent"])
            # 音色エラーの場合はリトライせずに例外を投げる
            if "does not exist" in r.text:
                print(f"❌ エラー: 音色 {voice_name} が見つかりません。")
                return None
            raise Exception(f"API Error: {r.text}")

    async def process_entry(self, index, source, target):
        if str(index) in self.progress: return
            
        async with self.semaphore:
            print(f"[处理中] {index}: {source[:15]}...")
            combined = AudioSegment.empty()
            
            for r in range(self.args.repeat):
                current_voice = self.voice_list[r % len(self.voice_list)]
                audio_bytes = await self.synthesize_api(source, current_voice)
                
                if audio_bytes:
                    temp_path = f"temp_{index}_{r}.mp3"
                    with open(temp_path, "wb") as f: f.write(audio_bytes)
                    snippet = AudioSegment.from_mp3(temp_path)
                    combined += snippet
                    if r < self.args.repeat - 1:
                        combined += AudioSegment.silent(duration=400) # 句内停顿
                    os.remove(temp_path)
                else:
                    return # 音声取得失敗時はスキップ

            file_name = f"snippet_{index}.mp3"
            combined.export(os.path.join(self.snippets_dir, file_name), format="mp3")
            self.save_progress(index, {
                "source": source, "target": target, "duration_ms": len(combined), "file": file_name
            })

    async def run(self):
        df = pd.read_csv(self.args.input)
        tasks = [self.process_entry(i, row['source_text'], row['target_text']) for i, row in df.iterrows()]
        await asyncio.gather(*tasks)

        print("\n[合并] 制作最終音声と字幕...")
        final_audio = AudioSegment.empty()
        srt_lines, lrc_lines = [], [f"[ti:{self.base_name}]", ""]
        current_ms = 0

        for i in range(len(df)):
            meta = self.progress.get(str(i))
            if not meta: continue
            
            snippet = AudioSegment.from_mp3(os.path.join(self.snippets_dir, meta['file']))
            final_audio += snippet
            
            start_lrc = self.format_lrc_time(current_ms)
            lrc_lines.append(f"{start_lrc}{meta['source']} | {meta['target']}")
            
            final_audio += AudioSegment.silent(duration=self.args.silence)
            current_ms += meta['duration_ms'] + self.args.silence
            lrc_lines.append(f"{self.format_lrc_time(current_ms)}") # 清屏

        final_audio.export(os.path.join(self.output_dir, f"{self.base_name}.mp3"), format="mp3")
        with open(os.path.join(self.output_dir, f"{self.base_name}.lrc"), "w", encoding="utf-8") as f:
            f.write("\n".join(lrc_lines))
        print(f"✅ 完了: {self.output_dir}")

if __name__ == "__main__":
    args = parse_args()
    asyncio.run(GoogleMultiVoiceEngine(args).run())