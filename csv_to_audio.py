import asyncio
import json
import os
import argparse
import edge_tts
import pandas as pd
from datetime import timedelta
from pydub import AudioSegment
from tenacity import retry, stop_after_attempt, wait_exponential

def parse_args():
    parser = argparse.ArgumentParser(description="Edge-TTS 多音色交叉生成工具")
    parser.add_argument("-i", "--input", default="anki_all.csv", help="输入 CSV 文件路径")
    # 支持多个音色，用逗号分隔
    parser.add_argument("-v", "--voice", default="ja-JP-NanamiNeural,ja-JP-KeitaNeural", help="TTS 音色列表 (用逗号分隔)")
    parser.add_argument("-r", "--rate", default="-5%", help="语速")
    parser.add_argument("-n", "--repeat", type=int, default=3, help="单句重复次数")
    parser.add_argument("-c", "--concurrent", type=int, default=5, help="最大并发数")
    parser.add_argument("-s", "--silence", type=int, default=800, help="句子间停顿毫秒数")
    parser.add_argument("-o", "--output_dir", default="tts_output", help="输出目录")
    return parser.parse_args()

class MultiVoiceEngine:
    def __init__(self, args):
        self.args = args
        self.base_name = os.path.splitext(os.path.basename(args.input))[0]
        self.output_dir = args.output_dir
        self.snippets_dir = os.path.join(self.output_dir, f"{self.base_name}_snippets")
        self.progress_file = os.path.join(self.output_dir, f"{self.base_name}_checkpoint.json")
        
        # 将音色字符串转换为列表
        self.voice_list = [v.strip() for v in args.voice.split(",")]
        self.semaphore = asyncio.Semaphore(args.concurrent)
        self.progress = self.load_progress()

        if not os.path.exists(self.snippets_dir):
            os.makedirs(self.snippets_dir)

    def load_progress(self):
        if os.path.exists(self.progress_file):
            with open(self.progress_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}

    def save_progress(self, index, metadata):
        self.progress[str(index)] = metadata
        with open(self.progress_file, 'w', encoding='utf-8') as f:
            json.dump(self.progress, f, ensure_ascii=False, indent=2)

    def format_srt_time(self, ms):
        td = timedelta(milliseconds=ms)
        total_sec = int(td.total_seconds())
        h, m, s = total_sec // 3600, (total_sec % 3600) // 60, total_sec % 60
        ms_part = int(td.microseconds / 1000)
        return f"{h:02}:{m:02}:{s:02},{ms_part:03}"

    @retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def synthesize_single(self, text, voice, path):
        """调用单个音色生成音频"""
        communicate = edge_tts.Communicate(text, voice, rate=self.args.rate)
        await communicate.save(path)

    async def process_entry(self, index, source_text, target_text):
        if str(index) in self.progress:
            return

        async with self.semaphore:
            print(f"[处理中] 行 {index}: {source_text[:20]}")
            
            combined_segment = AudioSegment.empty()
            
            # 核心变动：循环 REPEAT_COUNT 次，每次切换音色
            for r in range(self.args.repeat):
                current_voice = self.voice_list[r % len(self.voice_list)]
                temp_snippet_path = os.path.join(self.snippets_dir, f"temp_{index}_{r}.mp3")
                
                await self.synthesize_single(source_text, current_voice, temp_snippet_path)
                
                # 读取并拼接到该行的总音频中
                snippet_audio = AudioSegment.from_mp3(temp_snippet_path)
                combined_segment += snippet_audio
                
                # 重复朗读之间的微小停顿 (200ms) 让听感更自然
                if r < self.args.repeat - 1:
                    combined_segment += AudioSegment.silent(duration=200)
                
                os.remove(temp_snippet_path)

            snippet_filename = f"snippet_{index}.mp3"
            snippet_path = os.path.join(self.snippets_dir, snippet_filename)
            combined_segment.export(snippet_path, format="mp3")
            
            self.save_progress(index, {
                "source": source_text,
                "target": target_text,
                "duration_ms": len(combined_segment),
                "file": snippet_filename
            })

    async def run(self):
        if not os.path.exists(self.args.input):
            print(f"❌ 错误: 找不到文件 {self.args.input}")
            return

        df = pd.read_csv(self.args.input)
        tasks = [self.process_entry(i, r['source_text'], r['target_text']) for i, r in df.iterrows()]
        await asyncio.gather(*tasks)

        # --- 合并阶段 ---
        print(f"\n[合并] 正在生成最终作品...")
        final_audio = AudioSegment.empty()
        srt_lines = []
        current_time_ms = 0

        for i in range(len(df)):
            meta = self.progress.get(str(i))
            if not meta: continue

            snippet_path = os.path.join(self.snippets_dir, meta['file'])
            snippet_audio = AudioSegment.from_mp3(snippet_path)
            
            final_audio += snippet_audio
            
            start_t = self.format_srt_time(current_time_ms)
            end_t = self.format_srt_time(current_time_ms + meta['duration_ms'])
            srt_lines.append(f"{i+1}\n{start_t} --> {end_t}\n{meta['source']}\n{meta['target']}\n")
            
            final_audio += AudioSegment.silent(duration=self.args.silence)
            current_time_ms += meta['duration_ms'] + self.args.silence

        final_audio_path = os.path.join(self.output_dir, f"{self.base_name}.mp3")
        final_srt_path = os.path.join(self.output_dir, f"{self.base_name}.srt")

        final_audio.export(final_audio_path, format="mp3")
        with open(final_srt_path, "w", encoding="utf-8") as f:
            f.write("\n".join(srt_lines))
        
        print(f"✅ 完成！输出: {final_audio_path}")

if __name__ == "__main__":
    args = parse_args()
    engine = MultiVoiceEngine(args)
    asyncio.run(engine.run())