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
    parser = argparse.ArgumentParser(description="Edge-TTS 批量语音合成工具 (适配 原文/平假名/翻译 CSV)")
    parser.add_argument("-i", "--input", default="dari01.csv", help="输入的 CSV 文件路径")
    parser.add_argument("-v", "--voice", default="ja-JP-NanamiNeural,ja-JP-KeitaNeural", help="TTS 音色列表")
    parser.add_argument("-r", "--rate", default="-5%", help="语速")
    parser.add_argument("-n", "--repeat", type=int, default=3, help="单句重复朗读次数")
    parser.add_argument("-c", "--concurrent", type=int, default=5, help="最大并发下载任务数")
    parser.add_argument("-s", "--silence", type=int, default=1000, help="句子之间的停顿毫秒数")
    parser.add_argument("-o", "--output_dir", default="edge_tts_output", help="结果输出目录")
    parser.add_argument("-f", "--sub_format", choices=['lrc', 'srt', 'both'], default='both', help="字幕格式")
    return parser.parse_args()

class MultiVoiceEngine:
    def __init__(self, args):
        self.args = args
        self.base_name = os.path.splitext(os.path.basename(args.input))[0]
        self.output_dir = args.output_dir
        self.snippets_dir = os.path.join(self.output_dir, f"{self.base_name}_snippets")
        self.progress_file = os.path.join(self.output_dir, f"{self.base_name}_checkpoint.json")
        
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

    def format_lrc_time(self, ms):
        total_seconds = ms // 1000
        m = total_seconds // 60
        s = total_seconds % 60
        cs = (ms % 1000) // 10
        return f"[{m:02}:{s:02}.{cs:02}]"

    @retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def synthesize_single(self, text, voice, path):
        communicate = edge_tts.Communicate(text, voice, rate=self.args.rate)
        await communicate.save(path)

    async def process_entry(self, index, original, hiragana, translation):
        """处理单行：使用平假名发音，原文和翻译作为字幕内容"""
        if str(index) in self.progress:
            return
            
        async with self.semaphore:
            print(f"[处理中] 行 {index}: {original[:15]}...")
            combined_segment = AudioSegment.empty()
            
            for r in range(self.args.repeat):
                current_voice = self.voice_list[r % len(self.voice_list)]
                temp_snippet_path = os.path.join(self.snippets_dir, f"temp_{index}_{r}.mp3")
                
                # 读音强制使用平假名列
                await self.synthesize_single(hiragana, current_voice, temp_snippet_path)
                
                snippet_audio = AudioSegment.from_mp3(temp_snippet_path)
                combined_segment += snippet_audio
                
                if r < self.args.repeat - 1:
                    combined_segment += AudioSegment.silent(duration=300) # 句间微停顿
                
                if os.path.exists(temp_snippet_path):
                    os.remove(temp_snippet_path)

            snippet_filename = f"snippet_{index}.mp3"
            snippet_path = os.path.join(self.snippets_dir, snippet_filename)
            combined_segment.export(snippet_path, format="mp3")
            
            self.save_progress(index, {
                "original": original,
                "translation": translation,
                "duration_ms": len(combined_segment),
                "file": snippet_filename
            })

    async def run(self):
        if not os.path.exists(self.args.input):
            print(f"❌ 错误: 找不到文件 {self.args.input}")
            return

        # 读取 CSV
        df = pd.read_csv(self.args.input)
        tasks = [self.process_entry(i, r['原文'], r['平假名'], r['翻译']) for i, r in df.iterrows()]
        await asyncio.gather(*tasks)

        print(f"\n[合并] 正在生成作品...")
        final_audio = AudioSegment.empty()
        srt_lines = []
        lrc_lines = [f"[ti:{self.base_name}]", "[by:Edge-TTS Batch]", ""]
        current_time_ms = 0

        for i in range(len(df)):
            meta = self.progress.get(str(i))
            if not meta: continue

            snippet_path = os.path.join(self.snippets_dir, meta['file'])
            snippet_audio = AudioSegment.from_mp3(snippet_path)
            
            start_ms = current_time_ms
            duration = meta['duration_ms']
            end_ms = start_ms + duration

            # 字幕逻辑：第一行原文，第二行翻译
            if self.args.sub_format in ['srt', 'both']:
                s_t = self.format_srt_time(start_ms)
                e_t = self.format_srt_time(end_ms)
                srt_lines.append(f"{i+1}\n{s_t} --> {e_t}\n{meta['original']}\n{meta['translation']}\n")

            if self.args.sub_format in ['lrc', 'both']:
                lrc_time = self.format_lrc_time(start_ms)
                # LRC 合并为一行显示
                lrc_lines.append(f"{lrc_time}{meta['original']} | {meta['translation']}")
                lrc_lines.append(f"{self.format_lrc_time(end_ms)}")

            final_audio += snippet_audio
            final_audio += AudioSegment.silent(duration=self.args.silence)
            current_time_ms += duration + self.args.silence

        # 输出文件
        output_base = os.path.join(self.output_dir, self.base_name)
        final_audio.export(f"{output_base}.mp3", format="mp3")

        if self.args.sub_format in ['srt', 'both']:
            with open(f"{output_base}.srt", "w", encoding="utf-8") as f:
                f.write("\n".join(srt_lines))
        
        if self.args.sub_format in ['lrc', 'both']:
            with open(f"{output_base}.lrc", "w", encoding="utf-8") as f:
                f.write("\n".join(lrc_lines))
        
        print(f"✅ 完成！文件已保存在 {self.output_dir} 目录下。")

if __name__ == "__main__":
    args = parse_args()
    engine = MultiVoiceEngine(args)
    asyncio.run(engine.run())