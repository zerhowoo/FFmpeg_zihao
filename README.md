# 视频裁剪拼接工具

PyQt5 GUI 工具，支持批量视频裁剪和拼接。

## 功能

- 浏览视频文件夹，选择多个视频
- 裁剪：滑动条设置前后各删 0-50%，保留中间部分
- 拼接：支持拖拽排序，设置输出帧率和分辨率
- 效果预览
- 确认输出

## 依赖

```bash
sudo apt install ffmpeg
pip install PyQt5
```

## 使用

```bash
python3 text_to_video.py
```
