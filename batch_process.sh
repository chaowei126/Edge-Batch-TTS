#!/bin/bash

# --- 配置区 ---
# 输出目录（与 Python 脚本中的默认值保持一致或自定义）
OUTPUT_BASE="edge_tts_output"
# Python 脚本的文件名
PY_SCRIPT="csv_to_audio.py"
# 字幕格式 (lrc, srt, both)
SUB_FORMAT="lrc"

# 检查 Python 脚本是否存在
if [ ! -f "$PY_SCRIPT" ]; then
    echo "❌ 错误: 找不到 $PY_SCRIPT，请确保它在当前目录下。"
    exit 1
fi

# 创建输出目录
mkdir -p "$OUTPUT_BASE"

echo "🚀 开始批量处理当前目录下的 CSV 文件..."
echo "------------------------------------------"

# 计数器
count=0

# 遍历当前目录下所有的 csv 文件
for file in *.csv; do
    # 检查是否有匹配的文件，防止目录为空时报错
    [ -e "$file" ] || continue
    
    count=$((count + 1))
    echo "[$count] 正在处理: $file"
    
    # 执行 Python 脚本
    # 你可以在这里调整 -v (音色), -r (语速), -n (重复次数) 等参数
    python3 "$PY_SCRIPT" -i "$file" -f "$SUB_FORMAT" -o "$OUTPUT_BASE"
    
    if [ $? -eq 0 ]; then
        echo "✅ $file 处理完成。"
    else
        echo "❌ $file 处理失败，请检查错误日志。"
    fi
    echo "------------------------------------------"
done

echo "🎉 所有任务已处理完成！共处理 $count 个文件。"
echo "输出结果请查看目录: $OUTPUT_BASE"
