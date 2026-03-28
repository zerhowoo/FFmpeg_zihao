#!/usr/bin/env python3
"""
视频裁剪拼接工具 (Video Trim & Concat Tool)

PyQt5 GUI 工具，支持：
- 浏览视频文件夹，选择多个视频
- 裁剪：删掉前后各 x% 的视频，保留中间部分（滑动条设置）
- 拼接：支持排序、设置输出帧率和分辨率
- 效果预览
- 确认输出

依赖：
  pip install PyQt5
  sudo apt install ffmpeg
"""

import json
import os
import subprocess
import sys
import tempfile
import shutil

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFileDialog, QListWidget, QListWidgetItem,
    QSlider, QSpinBox, QComboBox, QGroupBox, QProgressBar,
    QMessageBox, QAbstractItemView, QSplitter, QGridLayout,
)
from PyQt5.QtCore import Qt, QProcess, QThread, pyqtSignal
from PyQt5.QtGui import QFont


# ============================================================
# FFmpeg 工具函数
# ============================================================

def get_video_duration(video_path):
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
    except Exception:
        return None


def get_video_info(video_path):
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate",
        "-show_entries", "format=duration",
        "-of", "json",
        video_path
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        info = json.loads(result.stdout)
        stream = info.get("streams", [{}])[0]
        fmt = info.get("format", {})
        w = stream.get("width", 0)
        h = stream.get("height", 0)
        rfr = stream.get("r_frame_rate", "30/1")
        num, den = rfr.split("/")
        fps = round(int(num) / max(int(den), 1), 2)
        dur = float(fmt.get("duration", 0))
        return {"width": w, "height": h, "fps": fps, "duration": dur}
    except Exception:
        return {"width": 0, "height": 0, "fps": 0, "duration": 0}


def format_time(seconds):
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


# ============================================================
# 后台处理线程
# ============================================================

class ProcessThread(QThread):
    progress = pyqtSignal(int, str)  # percent, message
    finished = pyqtSignal(bool, str)  # success, message

    def __init__(self, video_items, trim_pct, fps, width, height, output_path):
        super().__init__()
        self.video_items = video_items  # list of file paths in order
        self.trim_pct = trim_pct
        self.fps = fps
        self.width = width
        self.height = height
        self.output_path = output_path

    def run(self):
        try:
            tmpdir = tempfile.mkdtemp(prefix="vtool_")
            total = len(self.video_items)
            trimmed_files = []

            # Step 1: 裁剪每个视频
            for i, video_path in enumerate(self.video_items):
                self.progress.emit(
                    int((i / total) * 60),
                    f"裁剪中 ({i+1}/{total}): {os.path.basename(video_path)}"
                )
                out_file = os.path.join(tmpdir, f"trimmed_{i:04d}.mp4")
                duration = get_video_duration(video_path)
                if duration is None or duration <= 0:
                    continue

                trim_sec = duration * self.trim_pct / 100.0
                start = trim_sec
                end = duration - trim_sec

                if start >= end:
                    # trim 太多，跳过
                    continue

                cmd = [
                    "ffmpeg", "-y",
                    "-ss", str(start),
                    "-i", video_path,
                    "-t", str(end - start),
                    "-vf", f"scale={self.width}:{self.height}:force_original_aspect_ratio=decrease,"
                           f"pad={self.width}:{self.height}:(ow-iw)/2:(oh-ih)/2:black",
                    "-r", str(self.fps),
                    "-c:v", "libx264",
                    "-preset", "fast",
                    "-c:a", "aac",
                    "-ar", "44100",
                    "-ac", "2",
                    out_file
                ]
                subprocess.run(cmd, capture_output=True, check=True)
                trimmed_files.append(out_file)

            if not trimmed_files:
                self.finished.emit(False, "没有可处理的视频片段")
                return

            # Step 2: 拼接
            self.progress.emit(70, "拼接视频中...")
            list_file = os.path.join(tmpdir, "filelist.txt")
            with open(list_file, "w") as f:
                for tf in trimmed_files:
                    f.write(f"file '{tf}'\n")

            cmd = [
                "ffmpeg", "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", list_file,
                "-c", "copy",
                self.output_path
            ]
            subprocess.run(cmd, capture_output=True, check=True)

            # 清理临时文件
            shutil.rmtree(tmpdir, ignore_errors=True)

            self.progress.emit(100, "完成!")
            self.finished.emit(True, f"输出: {self.output_path}")

        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode() if e.stderr else str(e)
            self.finished.emit(False, f"FFmpeg 错误:\n{stderr[-500:]}")
        except Exception as e:
            self.finished.emit(False, f"错误: {str(e)}")


# ============================================================
# 预览线程 (生成单个裁剪后的预览)
# ============================================================

class PreviewThread(QThread):
    finished = pyqtSignal(bool, str)  # success, preview_file_path or error

    def __init__(self, video_path, trim_pct):
        super().__init__()
        self.video_path = video_path
        self.trim_pct = trim_pct

    def run(self):
        try:
            duration = get_video_duration(self.video_path)
            if duration is None:
                self.finished.emit(False, "无法获取视频时长")
                return

            trim_sec = duration * self.trim_pct / 100.0
            start = trim_sec
            end = duration - trim_sec

            if start >= end:
                self.finished.emit(False, "裁剪比例过大，无可用片段")
                return

            preview_file = tempfile.mktemp(suffix=".mp4", prefix="preview_")
            cmd = [
                "ffmpeg", "-y",
                "-ss", str(start),
                "-i", self.video_path,
                "-t", str(end - start),
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-c:a", "aac",
                preview_file
            ]
            subprocess.run(cmd, capture_output=True, check=True)
            self.finished.emit(True, preview_file)
        except Exception as e:
            self.finished.emit(False, str(e))


# ============================================================
# 主窗口
# ============================================================

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("视频裁剪拼接工具")
        self.setMinimumSize(900, 650)

        self.video_folder = ""
        self.output_path = ""
        self.process_thread = None
        self.preview_thread = None

        self.init_ui()

    def init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        # ---- 输入文件夹 ----
        input_group = QGroupBox("视频源文件夹")
        input_layout = QHBoxLayout(input_group)
        self.folder_label = QLabel("未选择")
        self.folder_label.setStyleSheet("color: gray;")
        btn_browse_input = QPushButton("Browse...")
        btn_browse_input.setFixedWidth(100)
        btn_browse_input.clicked.connect(self.browse_input_folder)
        input_layout.addWidget(self.folder_label, 1)
        input_layout.addWidget(btn_browse_input)
        main_layout.addWidget(input_group)

        # ---- 中间区域: 左侧视频列表 + 右侧已选列表 ----
        splitter = QSplitter(Qt.Horizontal)

        # 左: 文件夹内视频
        left_group = QGroupBox("可选视频")
        left_layout = QVBoxLayout(left_group)
        self.video_list = QListWidget()
        self.video_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        left_layout.addWidget(self.video_list)
        btn_add = QPushButton("添加选中 >>")
        btn_add.clicked.connect(self.add_selected)
        left_layout.addWidget(btn_add)
        splitter.addWidget(left_group)

        # 右: 已选视频（拼接顺序）
        right_group = QGroupBox("拼接队列（从上到下）")
        right_layout = QVBoxLayout(right_group)
        self.queue_list = QListWidget()
        self.queue_list.setDragDropMode(QAbstractItemView.InternalMove)
        self.queue_list.currentRowChanged.connect(self.on_queue_selection_changed)
        right_layout.addWidget(self.queue_list)

        queue_btn_layout = QHBoxLayout()
        btn_up = QPushButton("上移 ↑")
        btn_up.clicked.connect(self.move_up)
        btn_down = QPushButton("下移 ↓")
        btn_down.clicked.connect(self.move_down)
        btn_remove = QPushButton("移除")
        btn_remove.clicked.connect(self.remove_selected)
        queue_btn_layout.addWidget(btn_up)
        queue_btn_layout.addWidget(btn_down)
        queue_btn_layout.addWidget(btn_remove)
        right_layout.addLayout(queue_btn_layout)
        splitter.addWidget(right_group)

        splitter.setSizes([400, 400])
        main_layout.addWidget(splitter)

        # ---- 设置区域 ----
        settings_layout = QHBoxLayout()

        # 裁剪设置
        trim_group = QGroupBox("裁剪设置")
        trim_layout = QGridLayout(trim_group)

        trim_layout.addWidget(QLabel("删掉前后各:"), 0, 0)
        self.trim_slider = QSlider(Qt.Horizontal)
        self.trim_slider.setRange(0, 50)
        self.trim_slider.setValue(0)
        self.trim_slider.setTickPosition(QSlider.TicksBelow)
        self.trim_slider.setTickInterval(5)
        self.trim_slider.valueChanged.connect(self.on_trim_changed)
        trim_layout.addWidget(self.trim_slider, 0, 1)

        self.trim_label = QLabel("0%")
        self.trim_label.setFixedWidth(50)
        self.trim_label.setAlignment(Qt.AlignCenter)
        font = QFont()
        font.setBold(True)
        self.trim_label.setFont(font)
        trim_layout.addWidget(self.trim_label, 0, 2)

        self.trim_info_label = QLabel("")
        self.trim_info_label.setStyleSheet("color: #555;")
        trim_layout.addWidget(self.trim_info_label, 1, 0, 1, 3)

        settings_layout.addWidget(trim_group)

        # 输出设置
        output_group = QGroupBox("输出设置")
        out_layout = QGridLayout(output_group)

        out_layout.addWidget(QLabel("帧率 (FPS):"), 0, 0)
        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(1, 120)
        self.fps_spin.setValue(30)
        out_layout.addWidget(self.fps_spin, 0, 1)

        out_layout.addWidget(QLabel("分辨率:"), 1, 0)
        self.resolution_combo = QComboBox()
        self.resolution_combo.addItems([
            "1920x1080 (1080p)",
            "1280x720 (720p)",
            "3840x2160 (4K)",
            "854x480 (480p)",
            "640x360 (360p)",
        ])
        self.resolution_combo.setCurrentIndex(0)
        out_layout.addWidget(self.resolution_combo, 1, 1)

        settings_layout.addWidget(output_group)
        main_layout.addLayout(settings_layout)

        # ---- 操作按钮 ----
        action_layout = QHBoxLayout()

        btn_preview = QPushButton("效果预览")
        btn_preview.setFixedHeight(40)
        btn_preview.clicked.connect(self.preview)
        action_layout.addWidget(btn_preview)

        # 输出路径
        self.output_label = QLabel("未选择输出路径")
        self.output_label.setStyleSheet("color: gray;")
        action_layout.addWidget(self.output_label, 1)

        btn_browse_output = QPushButton("输出路径...")
        btn_browse_output.clicked.connect(self.browse_output)
        action_layout.addWidget(btn_browse_output)

        btn_export = QPushButton("确认输出")
        btn_export.setFixedHeight(40)
        btn_export.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold;")
        btn_export.clicked.connect(self.export_video)
        action_layout.addWidget(btn_export)

        main_layout.addLayout(action_layout)

        # ---- 进度条 ----
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
        main_layout.addWidget(self.progress_bar)

        self.status_label = QLabel("")
        main_layout.addWidget(self.status_label)

    # ---- 浏览文件夹 ----
    def browse_input_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择视频文件夹")
        if not folder:
            return
        self.video_folder = folder
        self.folder_label.setText(folder)
        self.folder_label.setStyleSheet("")
        self.load_videos(folder)

    def load_videos(self, folder):
        self.video_list.clear()
        exts = {".mp4", ".avi", ".mov", ".mkv", ".flv", ".wmv", ".webm", ".ts", ".m4v"}
        for f in sorted(os.listdir(folder)):
            ext = os.path.splitext(f)[1].lower()
            if ext in exts:
                filepath = os.path.join(folder, f)
                info = get_video_info(filepath)
                dur_str = format_time(info["duration"]) if info["duration"] > 0 else "?"
                res_str = f"{info['width']}x{info['height']}" if info["width"] > 0 else "?"
                label = f"{f}  [{dur_str} | {res_str} | {info['fps']}fps]"
                item = QListWidgetItem(label)
                item.setData(Qt.UserRole, filepath)
                self.video_list.addItem(item)

    # ---- 添加/移除/排序 ----
    def add_selected(self):
        for item in self.video_list.selectedItems():
            filepath = item.data(Qt.UserRole)
            # 避免重复
            exists = False
            for i in range(self.queue_list.count()):
                if self.queue_list.item(i).data(Qt.UserRole) == filepath:
                    exists = True
                    break
            if not exists:
                new_item = QListWidgetItem(item.text())
                new_item.setData(Qt.UserRole, filepath)
                self.queue_list.addItem(new_item)

    def remove_selected(self):
        for item in self.queue_list.selectedItems():
            self.queue_list.takeItem(self.queue_list.row(item))

    def move_up(self):
        row = self.queue_list.currentRow()
        if row > 0:
            item = self.queue_list.takeItem(row)
            self.queue_list.insertItem(row - 1, item)
            self.queue_list.setCurrentRow(row - 1)

    def move_down(self):
        row = self.queue_list.currentRow()
        if row < self.queue_list.count() - 1:
            item = self.queue_list.takeItem(row)
            self.queue_list.insertItem(row + 1, item)
            self.queue_list.setCurrentRow(row + 1)

    # ---- 裁剪滑动条 ----
    def on_trim_changed(self, value):
        self.trim_label.setText(f"{value}%")
        self.update_trim_info()

    def on_queue_selection_changed(self):
        self.update_trim_info()

    def update_trim_info(self):
        row = self.queue_list.currentRow()
        pct = self.trim_slider.value()
        if row < 0 or row >= self.queue_list.count():
            if pct == 0:
                self.trim_info_label.setText("不裁剪")
            else:
                self.trim_info_label.setText(f"前后各删 {pct}%，保留中间 {100 - 2*pct}%")
            return

        filepath = self.queue_list.item(row).data(Qt.UserRole)
        dur = get_video_duration(filepath)
        if dur and dur > 0:
            trim_sec = dur * pct / 100.0
            keep = dur - 2 * trim_sec
            self.trim_info_label.setText(
                f"当前选中: {os.path.basename(filepath)} | "
                f"原长 {format_time(dur)} → 保留 {format_time(max(keep, 0))} "
                f"(前后各删 {trim_sec:.1f}s)"
            )
        else:
            self.trim_info_label.setText(f"前后各删 {pct}%，保留中间 {100 - 2*pct}%")

    # ---- 输出路径 ----
    def browse_output(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "选择输出路径", "", "MP4 (*.mp4);;All Files (*)"
        )
        if path:
            if not path.lower().endswith(".mp4"):
                path += ".mp4"
            self.output_path = path
            self.output_label.setText(path)
            self.output_label.setStyleSheet("")

    # ---- 解析分辨率 ----
    def get_resolution(self):
        text = self.resolution_combo.currentText()
        res = text.split(" ")[0]
        w, h = res.split("x")
        return int(w), int(h)

    # ---- 获取队列中的视频路径列表 ----
    def get_queue_paths(self):
        paths = []
        for i in range(self.queue_list.count()):
            paths.append(self.queue_list.item(i).data(Qt.UserRole))
        return paths

    # ---- 效果预览 ----
    def preview(self):
        row = self.queue_list.currentRow()
        if row < 0:
            QMessageBox.warning(self, "提示", "请先在拼接队列中选择一个视频进行预览")
            return

        filepath = self.queue_list.item(row).data(Qt.UserRole)
        pct = self.trim_slider.value()

        self.status_label.setText("正在生成预览...")
        self.preview_thread = PreviewThread(filepath, pct)
        self.preview_thread.finished.connect(self.on_preview_done)
        self.preview_thread.start()

    def on_preview_done(self, success, result):
        if success:
            self.status_label.setText(f"预览已生成，正在播放...")
            # 用系统默认播放器打开
            if sys.platform == "linux":
                subprocess.Popen(["xdg-open", result])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", result])
            else:
                os.startfile(result)
        else:
            self.status_label.setText("")
            QMessageBox.warning(self, "预览失败", result)

    # ---- 确认输出 ----
    def export_video(self):
        paths = self.get_queue_paths()
        if not paths:
            QMessageBox.warning(self, "提示", "拼接队列为空，请先添加视频")
            return

        if not self.output_path:
            QMessageBox.warning(self, "提示", "请先选择输出路径")
            return

        if self.process_thread and self.process_thread.isRunning():
            QMessageBox.warning(self, "提示", "正在处理中，请等待完成")
            return

        w, h = self.get_resolution()
        fps = self.fps_spin.value()
        trim_pct = self.trim_slider.value()

        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        self.process_thread = ProcessThread(
            paths, trim_pct, fps, w, h, self.output_path
        )
        self.process_thread.progress.connect(self.on_process_progress)
        self.process_thread.finished.connect(self.on_process_done)
        self.process_thread.start()

    def on_process_progress(self, pct, msg):
        self.progress_bar.setValue(pct)
        self.status_label.setText(msg)

    def on_process_done(self, success, msg):
        self.progress_bar.setValue(100 if success else 0)
        if success:
            self.status_label.setText(msg)
            QMessageBox.information(self, "完成", msg)
        else:
            self.status_label.setText("处理失败")
            QMessageBox.critical(self, "错误", msg)
        self.progress_bar.setVisible(False)


# ============================================================
# 入口
# ============================================================

def main():
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        print("[错误] 未找到 ffmpeg/ffprobe，请先安装: sudo apt install ffmpeg")
        sys.exit(1)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
