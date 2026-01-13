# 🎙️ Edge-TTS Batch & Subtitle Generator (Pro)

这是一个基于 Microsoft Edge TTS 的高性能批量语音合成工具。它能够将 CSV 文本转换为带有同步字幕（SRT）的高质量音频文件，特别针对**外语磨耳朵训练、词汇背诵和短视频配音**进行了深度优化。

## ✨ 核心亮点

* **命令行驱动**：支持通过参数灵活配置语速、音色、重复次数等，无需修改代码。
* **智能命名**：输出文件名自动跟随输入文件名，方便管理多个学习任务。
* **物理重复 (Physical Repeat)**：支持单句音频多次重复，适合加强记忆。
* **断点续传**：内置 Checkpoint 机制，任务中途中断后重启，将跳过已完成部分。
* **精准字幕**：自动生成与音频完美同步的 `.srt` 字幕文件，支持双语显示。
* **异步并发**：采用 `asyncio` 异步技术，多线程快速下载音频片段。

## 🛠️ 环境安装

### 1. 系统依赖 (FFmpeg)

本工具合并音频需要系统安装 FFmpeg。

* **Ubuntu/Debian**: `apt update && apt install ffmpeg -y`
* **macOS**: `brew install ffmpeg`
* **Windows**: 下载 FFmpeg 官网包并配置环境变量。

### 2. Python 依赖库

建议使用以下命令安装（在 VPS 或最新 Linux 系统上需要加参数）：

```bash
python3 -m pip install edge-tts pandas pydub tenacity --break-system-packages

```

## 📂 输入格式 (CSV)

准备一个 UTF-8 编码的 CSV 文件（例如 `nihongo.csv`），需包含以下表头：

| source_text | target_text |
| --- | --- |
| こんにちは | 你好 |
| お元気ですか？ | 你好吗？ |

## 🚀 使用指南

### 基本运行

直接使用默认配置（日本语 Nanami 音色，语速 -5%，重复 3 次）：

```bash
python3 csv_to_audio.py -i nihongo.csv

```

* **输出**：将在 `edge_tts_output/` 下生成 `nihongo.mp3` 和 `nihongo.srt`。

### 自定义运行

你可以通过命令行参数覆盖默认设置：

```bash
python3 csv_to_audio.py -i lesson1.csv -v ja-JP-KeitaNeural -r +10% -n 2 -s 1000
python3 csv_to_audio.py -i lesson1.csv -v ja-JP-KeitaNeural --rate="-5%" -n 3 -s 800

```

### 参数详解

| 短参数 | 长参数 | 默认值 | 说明 |
| --- | --- | --- | --- |
| **`-i`** | `--input` | `input.csv` | 输入的 CSV 文件路径 |
| **`-v`** | `--voice` | `ja-JP-NanamiNeural` | 微软 Edge 音色 ID 支持单个音色，或多个音色（用逗号分隔，如 voice1,voice2）。设置多个音色时，程序会在重复朗读时自动循环切换音色。|
| **`-r`** | `--rate` | `-5%` | 语速 (例如: +10%, -20%) |
| **`-n`** | `--repeat` | `3` | 每句话连续重复朗读的次数 |
| **`-c`** | `--concurrent` | `5` | 最大并发下载任务数 |
| **`-s`** | `--silence` | `800` | 句与句之间的停顿时间 (毫秒) |
| **`-o`** | `--output_dir` | `edge_tts_output` | 结果存放的文件夹 |

## 📁 目录结构说明

执行后，工具会创建如下结构：

* **`edge_tts_output/`**: 总输出目录。
* **`[文件名]_snippets/`**: 存放单句生成的临时片段，合并完成后可手动删除。
* **`[文件名]_checkpoint.json`**: 记录处理进度，用于断点续传。
* **`[文件名].mp3`**: 最终合并好的长音频。
* **`[文件名].srt`**: 对应的字幕文件。

## ⚠️ 注意事项

1. **音色选择**：可以使用 `edge-tts --list-voices` 查看所有可用音色。
2. **网络环境**：脚本需联网访问微软服务，若在 VPS 上运行请确保网络通畅。
3. **并发控制**：`-c` 参数建议设置在 5-10 之间，过高可能会导致 IP 被临时封锁。
