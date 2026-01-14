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
    parser = argparse.ArgumentParser(description="Edge-TTS 批量语音合成工具 (Pro版)")
    parser.add_argument("-i", "--input", default="input.csv", help="输入的 CSV 文件路径")
    parser.add_argument("-v", "--voice", default="ja-JP-NanamiNeural,ja-JP-KeitaNeural", help="TTS 音色列表 (用逗号分隔)")
    parser.add_argument("-r", "--rate", default="-5%", help="语速 (如: +10%, -20%)")
    parser.add_argument("-n", "--repeat", type=int, default=3, help="单句重复朗读次数")
    parser.add_argument("-c", "--concurrent", type=int, default=5, help="最大并发下载任务数")
    parser.add_argument("-s", "--silence", type=int, default=800, help="句子之间的停顿毫秒数")
    parser.add_argument("-o", "--output_dir", default="edge_tts_output", help="结果输出目录")
    # 字幕格式参数，默认为 lrc
    parser.add_argument("-f", "--sub_format", choices=['lrc', 'srt', 'both'], default='lrc', help="字幕格式: lrc (默认), srt, 或 both")
    return parser.parse_args()

class MultiVoiceEngine:
    def __init__(self, args):
        self.args = args
        self.base_name = os.path.splitext(os.path.basename(args.input))[0]
        self.output_dir = args.output_dir
        self.snippets_dir = os.path.join(self.output_dir, f"{self.base_name}_snippets")
        self.progress_file = os.path.join(self.output_dir, f"{self.base_name}_checkpoint.json")
        
        # 解析音色列表
        self.voice_list = [v.strip() for v in args.voice.split(",")]
        self.semaphore = asyncio.Semaphore(args.concurrent)
        self.progress = self.load_progress()

        if not os.path.exists(self.snippets_dir):
            os.makedirs(self.snippets_dir)

    def load_progress(self):
        """加载断点续传进度"""
        if os.path.exists(self.progress_file):
            with open(self.progress_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}

    def save_progress(self, index, metadata):
        """保存当前处理进度"""
        self.progress[str(index)] = metadata
        with open(self.progress_file, 'w', encoding='utf-8') as f:
            json.dump(self.progress, f, ensure_ascii=False, indent=2)

    def format_srt_time(self, ms):
        """将毫秒转换为 SRT 时间格式 (00:00:00,000)"""
        td = timedelta(milliseconds=ms)
        total_sec = int(td.total_seconds())
        h, m, s = total_sec // 3600, (total_sec % 3600) // 60, total_sec % 60
        ms_part = int(td.microseconds / 1000)
        return f"{h:02}:{m:02}:{s:02},{ms_part:03}"

    def format_lrc_time(self, ms):
        """将毫秒转换为 LRC 时间格式 ([mm:ss.xx])"""
        total_seconds = ms // 1000
        m = total_seconds // 60
        s = total_seconds % 60
        cs = (ms % 1000) // 10  # 厘秒
        return f"[{m:02}:{s:02}.{cs:02}]"

    @retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def synthesize_single(self, text, voice, path):
        """异步调用 Edge-TTS 生成音频"""
        communicate = edge_tts.Communicate(text, voice, rate=self.args.rate)
        await communicate.save(path)

    async def process_entry(self, index, source_text, target_text):
        """处理单行文本：生成多次重复并带有音色切换的音频片段"""
        if str(index) in self.progress:
            return
            
        # --- 核心改进：读音纠错表 ---
        # 遇到这些汉字词时，强制让 TTS 读假名，避免多音字错误
        pronunciation_fixes = {
            "月曜日": "げつようび",
            "火曜日": "かようび",
            "水曜日": "すいようび",
            "木曜日": "もくようび",
            "金曜日": "きんようび",
            "土曜日": "どようび",
            "日曜日": "にちようび"
        }
        
        tts_text = source_text
        for kanji, kana in pronunciation_fixes.items():
            tts_text = tts_text.replace(kanji, kana)
        # ---------------------------

        async with self.semaphore:
            print(f"[处理中] 行 {index}: {source_text[:20]}...")
            combined_segment = AudioSegment.empty()
            
            for r in range(self.args.repeat):
                # 循环切换音色列表中的音色
                current_voice = self.voice_list[r % len(self.voice_list)]
                temp_snippet_path = os.path.join(self.snippets_dir, f"temp_{index}_{r}.mp3")
                
                # 注意：这里传入的是纠错后的 tts_text，但字幕依然使用 source_text
                await self.synthesize_single(tts_text, current_voice, temp_snippet_path)
                
                snippet_audio = AudioSegment.from_mp3(temp_snippet_path)
                combined_segment += snippet_audio
                
                # 重复间隔增加 200ms 的微小停顿，听感更自然
                if r < self.args.repeat - 1:
                    combined_segment += AudioSegment.silent(duration=200)
                
                if os.path.exists(temp_snippet_path):
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

        # 1. 下载与片段生成阶段
        df = pd.read_csv(self.args.input)
        tasks = [self.process_entry(i, r['source_text'], r['target_text']) for i, r in df.iterrows()]
        await asyncio.gather(*tasks)

        # 2. 合并音频与生成字幕阶段
        print(f"\n[合并] 正在生成最终作品 (字幕格式: {self.args.sub_format})...")
        final_audio = AudioSegment.empty()
        srt_lines = []
        
        # 初始化 LRC 头部信息标签
        lrc_lines = [
            f"[ti:{self.base_name}]",
            f"[ar:{','.join(self.voice_list[:2])}]",
            f"[by:Edge-TTS Batch Pro]",
            f"[offset:0]",
            "" 
        ]
        
        current_time_ms = 0

        for i in range(len(df)):
            meta = self.progress.get(str(i))
            if not meta: continue

            snippet_path = os.path.join(self.snippets_dir, meta['file'])
            snippet_audio = AudioSegment.from_mp3(snippet_path)
            
            start_ms = current_time_ms
            duration = meta['duration_ms']
            end_ms = start_ms + duration

            # 生成 SRT 内容
            if self.args.sub_format in ['srt', 'both']:
                s_t = self.format_srt_time(start_ms)
                e_t = self.format_srt_time(end_ms)
                srt_lines.append(f"{i+1}\n{s_t} --> {e_t}\n{meta['source']}\n{meta['target']}\n")

            # 生成 LRC 内容 (双语间使用空格分隔)
            if self.args.sub_format in ['lrc', 'both']:
                lrc_time = self.format_lrc_time(start_ms)
                lrc_lines.append(f"{lrc_time}{meta['source']} {meta['target']}")
                # 静音清屏戳：防止字幕在句子结束后一直挂在屏幕上
                lrc_lines.append(f"{self.format_lrc_time(end_ms)}")

            final_audio += snippet_audio
            # 添加句子间的物理停顿
            final_audio += AudioSegment.silent(duration=self.args.silence)
            current_time_ms += duration + self.args.silence

        # 3. 文件输出阶段
        final_audio_path = os.path.join(self.output_dir, f"{self.base_name}.mp3")
        final_audio.export(final_audio_path, format="mp3")

        if self.args.sub_format in ['srt', 'both']:
            with open(os.path.join(self.output_dir, f"{self.base_name}.srt"), "w", encoding="utf-8") as f:
                f.write("\n".join(srt_lines))
        
        if self.args.sub_format in ['lrc', 'both']:
            with open(os.path.join(self.output_dir, f"{self.base_name}.lrc"), "w", encoding="utf-8") as f:
                f.write("\n".join(lrc_lines))
        
        print(f"✅ 处理完成！")
        print(f"   音频: {final_audio_path}")
        if self.args.sub_format != 'srt': print(f"   字幕: {self.base_name}.lrc")

if __name__ == "__main__":
    args = parse_args()
    engine = MultiVoiceEngine(args)
    asyncio.run(engine.run())
