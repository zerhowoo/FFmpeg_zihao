#!/usr/bin/env python3
"""
文本转视频生成器 (Text-to-Video Generator)

Pipeline:
  文本 → 选择视频素材 → 裁剪拼接 → (人脸处理, 暂不实现) → 语音合成(Qwen) → 合成最终视频

Usage:
  python3 text_to_video.py --text "你的文本内容" --video-dir ./videos --mode auto
  python3 text_to_video.py --text "你的文本内容" --video-dir ./videos --mode manual
"""

import argparse
import json
import os
import subprocess
import sys
import shutil


# ============================================================
# 工具函数
# ============================================================

def get_video_duration(video_path):
    """使用 ffprobe 获取视频时长（秒）"""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json",
        video_path
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        info = json.loads(result.stdout)
        return float(info["format"]["duration"])
    except (subprocess.CalledProcessError, KeyError, ValueError) as e:
        print(f"[错误] 无法获取视频时长: {video_path}\n  {e}")
        return None


def trim_video(input_path, output_path, start_time, end_time):
    """使用 ffmpeg 裁剪视频，保留 [start_time, end_time] 区间"""
    duration = end_time - start_time
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start_time),
        "-i", input_path,
        "-t", str(duration),
        "-c", "copy",
        "-avoid_negative_ts", "make_zero",
        output_path
    ]
    print(f"  裁剪: {os.path.basename(input_path)} "
          f"[{start_time:.2f}s - {end_time:.2f}s] -> {os.path.basename(output_path)}")
    subprocess.run(cmd, capture_output=True, check=True)


def concat_videos(video_list, output_path):
    """使用 ffmpeg concat demuxer 拼接多个视频"""
    # 创建临时文件列表
    list_file = output_path + ".filelist.txt"
    with open(list_file, "w") as f:
        for v in video_list:
            # ffmpeg concat 要求路径中的单引号被转义
            escaped = v.replace("'", "'\\''")
            f.write(f"file '{escaped}'\n")

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", list_file,
        "-c", "copy",
        output_path
    ]
    print(f"\n拼接 {len(video_list)} 个视频片段 -> {output_path}")
    subprocess.run(cmd, capture_output=True, check=True)
    os.remove(list_file)


def merge_video_audio(video_path, audio_path, output_path):
    """合并视频和音频，以较短的为准"""
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", audio_path,
        "-c:v", "copy",
        "-c:a", "aac",
        "-shortest",
        output_path
    ]
    print(f"\n合成最终视频: {output_path}")
    subprocess.run(cmd, capture_output=True, check=True)


# ============================================================
# 自动裁剪逻辑
# ============================================================

def calculate_auto_trim(duration):
    """
    根据视频时长自动计算裁剪的起止时间。
    规则:
      - duration > 15s:  删掉前后各 5s
      - 10s < duration <= 15s: 删掉前后各 25%
      - 5s < duration <= 10s: 删掉前后各 (duration - 5) / 2 秒
      - duration <= 5s: 不处理
    返回 (start, end) 或 None（不处理）
    """
    if duration > 15:
        return (5.0, duration - 5.0)
    elif duration > 10:
        trim = duration * 0.25
        return (trim, duration - trim)
    elif duration > 5:
        trim = (duration - 5) / 2
        return (trim, duration - trim)
    else:
        return None  # 不处理


# ============================================================
# TTS 语音合成 (Qwen/DashScope)
# ============================================================

def generate_tts_qwen(text, output_audio_path):
    """
    使用阿里云 DashScope (Qwen) 的 TTS 接口生成语音。
    需要设置环境变量 DASHSCOPE_API_KEY。
    """
    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        print("[警告] 未设置 DASHSCOPE_API_KEY 环境变量，跳过语音合成。")
        print("  请设置: export DASHSCOPE_API_KEY='your-api-key'")
        return False

    try:
        import dashscope
        from dashscope.audio.tts import SpeechSynthesizer

        dashscope.api_key = api_key

        result = SpeechSynthesizer.call(
            model="sambert-zhichu-v1",
            text=text,
            sample_rate=48000,
            format="wav"
        )

        if result.get_audio_data() is not None:
            with open(output_audio_path, "wb") as f:
                f.write(result.get_audio_data())
            print(f"语音合成完成: {output_audio_path}")
            return True
        else:
            print(f"[错误] 语音合成失败: {result}")
            return False
    except ImportError:
        print("[警告] 未安装 dashscope 库，尝试使用 HTTP API 方式...")
        return generate_tts_qwen_http(text, output_audio_path, api_key)


def generate_tts_qwen_http(text, output_audio_path, api_key):
    """使用 HTTP 请求调用 DashScope TTS API (备选方案)"""
    import urllib.request
    import urllib.error

    url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2audio/generation"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = json.dumps({
        "model": "sambert-zhichu-v1",
        "input": {"text": text},
        "parameters": {"sample_rate": 48000, "format": "wav"}
    }).encode("utf-8")

    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            # DashScope 可能返回 JSON 或直接音频数据
            content_type = resp.headers.get("Content-Type", "")
            data = resp.read()

            if "audio" in content_type:
                with open(output_audio_path, "wb") as f:
                    f.write(data)
                print(f"语音合成完成: {output_audio_path}")
                return True
            else:
                # JSON 响应，解析获取音频 URL
                result = json.loads(data)
                audio_url = result.get("output", {}).get("audio", "")
                if audio_url:
                    urllib.request.urlretrieve(audio_url, output_audio_path)
                    print(f"语音合成完成: {output_audio_path}")
                    return True
                else:
                    print(f"[错误] TTS API 返回: {result}")
                    return False
    except urllib.error.URLError as e:
        print(f"[错误] TTS API 请求失败: {e}")
        return False


# ============================================================
# 主流程
# ============================================================

def list_videos(video_dir):
    """列出目录下所有视频文件"""
    video_exts = {".mp4", ".avi", ".mov", ".mkv", ".flv", ".wmv", ".webm", ".ts", ".m4v"}
    videos = []
    for f in sorted(os.listdir(video_dir)):
        ext = os.path.splitext(f)[1].lower()
        if ext in video_exts:
            videos.append(os.path.join(video_dir, f))
    return videos


def interactive_select_videos(video_dir):
    """交互式选择视频素材"""
    videos = list_videos(video_dir)
    if not videos:
        print(f"[错误] 视频目录中没有找到视频文件: {video_dir}")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"视频库: {video_dir}")
    print(f"{'='*60}")
    for i, v in enumerate(videos):
        dur = get_video_duration(v)
        dur_str = f"{dur:.1f}s" if dur else "未知"
        print(f"  [{i}] {os.path.basename(v)}  ({dur_str})")

    print(f"\n请输入要选择的视频编号（用逗号或空格分隔，按顺序排列）:")
    print(f"例如: 0,2,5 或 0 2 5")
    selection = input("> ").strip()

    indices = []
    for part in selection.replace(",", " ").split():
        try:
            idx = int(part.strip())
            if 0 <= idx < len(videos):
                indices.append(idx)
            else:
                print(f"  [警告] 忽略无效编号: {idx}")
        except ValueError:
            print(f"  [警告] 忽略无效输入: {part}")

    if not indices:
        print("[错误] 没有选择任何视频")
        sys.exit(1)

    selected = [videos[i] for i in indices]
    print(f"\n已选择 {len(selected)} 个视频:")
    for v in selected:
        print(f"  - {os.path.basename(v)}")
    return selected


def process_auto_mode(selected_videos, output_dir):
    """自动模式：根据时长规则裁剪视频"""
    print(f"\n{'='*60}")
    print("自动裁剪模式")
    print(f"{'='*60}")

    processed = []
    for video_path in selected_videos:
        duration = get_video_duration(video_path)
        if duration is None:
            print(f"  [跳过] 无法获取时长: {os.path.basename(video_path)}")
            continue

        basename = os.path.splitext(os.path.basename(video_path))[0]
        out_name = f"{basename}_trimmed.mp4"
        out_path = os.path.join(output_dir, out_name)

        trim_range = calculate_auto_trim(duration)
        if trim_range is None:
            # 时长 <= 5s，直接复制
            print(f"  {os.path.basename(video_path)} ({duration:.1f}s <= 5s): 不裁剪，直接使用")
            shutil.copy2(video_path, out_path)
        else:
            start, end = trim_range
            print(f"  {os.path.basename(video_path)} ({duration:.1f}s): "
                  f"裁剪 [{start:.2f}s - {end:.2f}s]")
            trim_video(video_path, out_path, start, end)

        processed.append(out_path)

    return processed


def process_manual_mode(selected_videos, output_dir):
    """手动模式：用户指定每个视频的裁剪起止时间"""
    print(f"\n{'='*60}")
    print("手动裁剪模式")
    print(f"{'='*60}")

    processed = []
    for video_path in selected_videos:
        duration = get_video_duration(video_path)
        dur_str = f" (时长: {duration:.1f}s)" if duration else ""
        name = os.path.basename(video_path)

        print(f"\n视频: {name}{dur_str}")
        print(f"  请输入两刀的位置（秒），用逗号分隔，只保留中间部分。")
        print(f"  例如: 3,12  表示保留 3s 到 12s 的内容")
        print(f"  直接回车跳过（不裁剪，直接使用原视频）")

        user_input = input("  > ").strip()

        basename = os.path.splitext(name)[0]
        out_name = f"{basename}_trimmed.mp4"
        out_path = os.path.join(output_dir, out_name)

        if not user_input:
            print(f"  -> 不裁剪，直接使用")
            shutil.copy2(video_path, out_path)
        else:
            try:
                parts = user_input.split(",")
                start = float(parts[0].strip())
                end = float(parts[1].strip())
                if start >= end:
                    print(f"  [错误] 起始时间必须小于结束时间，跳过此视频")
                    continue
                trim_video(video_path, out_path, start, end)
            except (ValueError, IndexError):
                print(f"  [错误] 输入格式不正确，跳过此视频")
                continue

        processed.append(out_path)

    return processed


def main():
    parser = argparse.ArgumentParser(
        description="文本转视频生成器 - Text to Video Generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 自动模式
  python3 text_to_video.py --text "今天天气真好" --video-dir ./videos --mode auto

  # 手动模式
  python3 text_to_video.py --text "今天天气真好" --video-dir ./videos --mode manual

  # 指定输出目录
  python3 text_to_video.py --text "今天天气真好" --video-dir ./videos --mode auto --output-dir ./output
        """
    )
    parser.add_argument("--text", required=True, help="要转换为视频的文本内容")
    parser.add_argument("--video-dir", required=True, help="视频素材库目录路径")
    parser.add_argument("--mode", choices=["auto", "manual"], default="auto",
                        help="裁剪模式: auto(自动) 或 manual(手动), 默认 auto")
    parser.add_argument("--output-dir", default="./output", help="输出目录, 默认 ./output")
    parser.add_argument("--skip-tts", action="store_true", help="跳过语音合成步骤")
    parser.add_argument("--audio-file", default=None, help="直接使用指定的音频文件，跳过TTS")

    args = parser.parse_args()

    # 检查 ffmpeg
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        print("[错误] 未找到 ffmpeg/ffprobe，请先安装 ffmpeg。")
        print("  Ubuntu: sudo apt install ffmpeg")
        print("  Mac: brew install ffmpeg")
        sys.exit(1)

    # 检查视频目录
    if not os.path.isdir(args.video_dir):
        print(f"[错误] 视频目录不存在: {args.video_dir}")
        sys.exit(1)

    # 创建输出目录
    processed_dir = os.path.join(args.output_dir, "processed")
    os.makedirs(processed_dir, exist_ok=True)

    print(f"\n文本内容: {args.text}")
    print(f"裁剪模式: {'自动' if args.mode == 'auto' else '手动'}")

    # ---- Step 1: 选择视频素材 ----
    selected_videos = interactive_select_videos(args.video_dir)

    # ---- Step 2: 裁剪处理 ----
    if args.mode == "auto":
        processed_videos = process_auto_mode(selected_videos, processed_dir)
    else:
        processed_videos = process_manual_mode(selected_videos, processed_dir)

    if not processed_videos:
        print("\n[错误] 没有成功处理任何视频")
        sys.exit(1)

    print(f"\n处理完成的视频素材已保存到: {processed_dir}")
    for v in processed_videos:
        print(f"  - {os.path.basename(v)}")

    # ---- Step 3: 拼接所有处理后的视频 ----
    concat_output = os.path.join(args.output_dir, "concatenated.mp4")
    if len(processed_videos) == 1:
        shutil.copy2(processed_videos[0], concat_output)
        print(f"\n只有一个视频片段，直接使用: {concat_output}")
    else:
        concat_videos(processed_videos, concat_output)

    # ---- Step 4: 语音合成 (Qwen TTS) ----
    audio_path = None
    if args.audio_file:
        if os.path.isfile(args.audio_file):
            audio_path = args.audio_file
            print(f"\n使用指定音频文件: {audio_path}")
        else:
            print(f"[错误] 指定的音频文件不存在: {args.audio_file}")
    elif not args.skip_tts:
        audio_path = os.path.join(args.output_dir, "tts_audio.wav")
        print(f"\n{'='*60}")
        print("语音合成 (Qwen TTS)")
        print(f"{'='*60}")
        success = generate_tts_qwen(args.text, audio_path)
        if not success:
            audio_path = None
    else:
        print("\n已跳过语音合成步骤。")

    # ---- Step 5: 合成最终视频 ----
    final_output = os.path.join(args.output_dir, "final_video.mp4")
    if audio_path and os.path.isfile(audio_path):
        merge_video_audio(concat_output, audio_path, final_output)
        print(f"\n{'='*60}")
        print(f"最终视频已生成: {final_output}")
        print(f"{'='*60}")
    else:
        # 没有音频，拼接后的视频即为最终视频
        shutil.copy2(concat_output, final_output)
        print(f"\n{'='*60}")
        print(f"最终视频（无语音）: {final_output}")
        print(f"{'='*60}")

    print("\n完成!")


if __name__ == "__main__":
    main()
