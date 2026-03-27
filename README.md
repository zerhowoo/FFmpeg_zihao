# 文本转视频生成器 (Text-to-Video Generator)

根据输入文本，自动选择视频素材、裁剪拼接、生成语音并合成最终视频。

## 流程

```
文本 ──选择──> 视频素材 ──ffmpeg──> 拼接好的素材 ──> 合成视频
  │                                                    ↑
  └──────── qwen TTS ──> 语音 ──── ffmpeg ─────────────┘
```

## 依赖

- Python 3.8+
- ffmpeg / ffprobe（需系统安装）
- DashScope API Key（用于 Qwen TTS 语音合成，可选）

```bash
# Ubuntu
sudo apt install ffmpeg

# Mac
brew install ffmpeg

# TTS 依赖（可选）
pip install dashscope
export DASHSCOPE_API_KEY='your-api-key'
```

## 使用

```bash
# 自动裁剪模式
python3 text_to_video.py --text "你的文本" --video-dir ./videos --mode auto

# 手动裁剪模式
python3 text_to_video.py --text "你的文本" --video-dir ./videos --mode manual

# 跳过语音合成
python3 text_to_video.py --text "你的文本" --video-dir ./videos --skip-tts

# 使用已有音频文件
python3 text_to_video.py --text "你的文本" --video-dir ./videos --audio-file ./audio.wav
```

## 自动裁剪规则

| 视频时长 | 处理方式 |
|---------|---------|
| > 15秒 | 删掉前后各 5 秒 |
| 10-15秒 | 删掉前后各 25% |
| 5-10秒 | 删掉前后各 (时长-5)/2 秒 |
| < 5秒 | 不处理 |
