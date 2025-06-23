import flet as ft
from pathlib import Path
import threading
import time
import transkun.transcribe
import torch
import moduleconf
import os

# !!! 核心改动：在 pydub 导入之前，将 ffmpeg_bin 目录添加到 PATH 环境变量中 !!!
current_dir = os.path.dirname(os.path.abspath(__file__))
ffmpeg_bin_path = os.path.join(current_dir, "ffmpeg_bin")

# 检查路径是否已在 PATH 中，避免重复添加
# Windows 使用 os.pathsep (;) 作为分隔符
if ffmpeg_bin_path not in os.environ['PATH'].split(os.pathsep):
    os.environ['PATH'] = ffmpeg_bin_path + os.pathsep + os.environ['PATH']


def main(page: ft.Page):
    page.title = "Transkun - Piano Audio to MIDI"
    page.theme_mode = ft.ThemeMode.LIGHT
    page.padding = 25  # 减小页面边距
    page.window.width = 800
    page.window.height = 550  # 减小窗口高度

    # 定义统一的颜色方案
    THEME_PRIMARY = ft.Colors.BLUE_600
    THEME_SECONDARY = ft.Colors.BLUE_50
    THEME_BG = ft.Colors.with_opacity(0.05, ft.Colors.BLUE_GREY)

    # Status text and progress indicator
    status_text = ft.Text(
        "准备就绪",
        size=16,
        color=THEME_PRIMARY,
        weight=ft.FontWeight.W_500
    )
    progress_ring = ft.ProgressRing(
        visible=False,
        width=20,
        height=20,
        stroke_width=3,
        color=THEME_PRIMARY
    )

    status_row = ft.Row(
        [status_text, progress_ring],
        alignment=ft.MainAxisAlignment.CENTER,
        spacing=10,
    )

    def pick_input_file(e):
        pick_file_dialog.pick_files(
            allow_multiple=False,
            allowed_extensions=["mp3", "wav", "flac", "ogg"]
        )

    def pick_output_location(e):
        save_file_dialog.save_file(
            file_name="未命名.mid",
            allowed_extensions=["mid"]
        )

    def selected_input(e: ft.FilePickerResultEvent):
        if e.files and e.files[0]:
            input_path.value = e.files[0].path
            input_path.update()
            status_text.value = f"已选择输入文件: {Path(e.files[0].path).name}"
            status_text.update()

    def selected_output(e: ft.FilePickerResultEvent):
        if e.path:
            output_path.value = e.path
            output_path.update()
            status_text.value = f"已设置输出位置: {e.path}"
            status_text.update()

    def run_transkun_task():
        try:
            # 显示进度指示器
            progress_ring.visible = True
            status_text.value = "正在转换中..."
            status_row.update()

            input_file = input_path.value
            output_file = output_path.value

            # If no output file was selected, create one based on input file name
            if not output_file:
                input_name = Path(input_file).stem
                output_file = str(Path(input_file).parent / f"{input_name}.mid")
                output_path.value = output_file
                page.update()

            device = "cpu"

            start_time = time.time()
            try:
                default_weight = os.path.join(current_dir, "models\\2.0.pt")
                default_conf = os.path.join(current_dir, "models\\2.0.conf")

                # 检查模型文件是否存在
                if not os.path.exists(default_weight) or not os.path.exists(default_conf):
                    raise FileNotFoundError(
                        f"找不到模型文件！请确保以下文件存在：\n"
                        f"{default_weight}\n"
                        f"{default_conf}"
                    )

                # 加载配置
                conf_manager = moduleconf.parseFromFile(default_conf)
                TransKun = conf_manager["Model"].module.TransKun
                conf = conf_manager["Model"].config

                # 加载模型
                checkpoint = torch.load(default_weight, map_location=device)
                model = TransKun(conf=conf).to(device)
                if "best_state_dict" not in checkpoint:
                    model.load_state_dict(checkpoint["state_dict"], strict=False)
                else:
                    model.load_state_dict(checkpoint["best_state_dict"], strict=False)
                model.eval()

                # 读取并处理音频
                fs, audio = transkun.transcribe.readAudio(input_file)
                if fs != model.fs:
                    import soxr
                    audio = soxr.resample(audio, fs, model.fs)

                x = torch.from_numpy(audio).to(device)

                # 转录
                with torch.no_grad():
                    notes_est = model.transcribe(x)

                # 保存MIDI
                output_midi = transkun.transcribe.writeMidi(notes_est)
                output_midi.write(output_file)

                end_time = time.time()
                process_time = round(end_time - start_time, 2)

                status_text.value = f"转换完成！用时 {process_time}秒"
                success_snack = ft.SnackBar(
                    content=ft.Container(
                        content=ft.Row(
                            [
                                ft.Icon(ft.Icons.CHECK_CIRCLE, color=ft.Colors.WHITE, size=20),
                                ft.Text("转换成功！", weight=ft.FontWeight.W_500)
                            ],
                            spacing=10
                        ),
                        padding=10
                    ),
                    bgcolor=ft.Colors.GREEN_400
                )
                page.overlay.append(success_snack)
                success_snack.open = True

            except Exception as e:
                # 其他错误
                error_message = str(e)
                status_text.value = "转换失败"
                import traceback # <-- 添加这一行
                traceback.print_exc() # <-- 添加这一行，打印完整的堆栈信息
                error_snack = ft.SnackBar(
                    content=ft.Container(
                        content=ft.Column(
                            [
                                ft.Row(
                                    [
                                        ft.Icon(ft.Icons.ERROR, color=ft.Colors.WHITE, size=20),
                                        ft.Text("转换失败", weight=ft.FontWeight.W_500)
                                    ],
                                    spacing=10
                                ),
                                ft.Text(error_message, color=ft.Colors.WHITE, size=14)
                            ],
                            spacing=5,
                        ),
                        padding=10
                    ),
                    bgcolor=ft.Colors.RED_400,
                    duration=10000  # 显示10秒钟
                )
                page.overlay.append(error_snack)
                error_snack.open = True

        except Exception as e:
            status_text.value = "发生错误"
            page.snack_bar = ft.SnackBar(
                content=ft.Container(
                    content=ft.Row(
                        [
                            ft.Icon(ft.Icons.ERROR, color=ft.Colors.WHITE, size=20),
                            ft.Text(f"错误：{str(e)}", weight=ft.FontWeight.W_500)
                        ],
                        spacing=10
                    ),
                    padding=10
                ),
                bgcolor=ft.Colors.RED_400
            )
            page.snack_bar.open = True

        finally:
            progress_ring.visible = False
            status_row.update()
            page.update()

    def start_conversion(e):
        if not input_path.value:
            page.snack_bar = ft.SnackBar(ft.Text("请选择输入音频文件"))
            page.snack_bar.open = True
            page.update()
            return

        # Run the conversion in a separate thread to avoid UI freezing
        threading.Thread(target=run_transkun_task).start()

    # File pickers
    pick_file_dialog = ft.FilePicker(on_result=selected_input)
    save_file_dialog = ft.FilePicker(on_result=selected_output)

    page.overlay.extend([pick_file_dialog, save_file_dialog])

    # Input path field with browse button
    input_path = ft.TextField(
        label="输入音频文件",
        expand=True,
        read_only=True,
        hint_text="选择音频文件 (.mp3, .wav, .flac, .ogg)",
        border_radius=8,
        filled=True,
        bgcolor=THEME_BG
    )

    input_row = ft.Row(
        [
            input_path,
            ft.IconButton(
                icon=ft.Icons.FOLDER_OPEN_ROUNDED,
                tooltip="浏览",
                on_click=pick_input_file,
                icon_color=THEME_PRIMARY
            )
        ]
    )

    # Output path field with browse button
    output_path = ft.TextField(
        label="输出MIDI文件",
        expand=True,
        read_only=True,
        hint_text="选择保存位置 (默认与输入文件同目录)",
        border_radius=8,
        filled=True,
        bgcolor=THEME_BG
    )

    output_row = ft.Row(
        [
            output_path,
            ft.IconButton(
                icon=ft.Icons.FOLDER_OPEN_ROUNDED,
                tooltip="浏览",
                on_click=pick_output_location,
                icon_color=THEME_PRIMARY
            )
        ]
    )

    # Start button
    start_button = ft.ElevatedButton(
        content=ft.Row(
            [
                ft.Icon(ft.Icons.MUSIC_NOTE_ROUNDED, color=ft.Colors.WHITE),
                ft.Text(
                    "开始转换",
                    size=16,
                    weight=ft.FontWeight.W_500
                )
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            spacing=8
        ),
        style=ft.ButtonStyle(
            bgcolor=THEME_PRIMARY,
            color=ft.Colors.WHITE,
            padding=ft.padding.symmetric(horizontal=30, vertical=15),
            shape=ft.RoundedRectangleBorder(radius=8),
        ),
        on_click=start_conversion,
        width=200
    )

    # Section headers
    def section_header(text: str) -> ft.Text:
        return ft.Text(
            text,
            size=16,
            weight=ft.FontWeight.BOLD,
            color=THEME_PRIMARY
        )

    # Application header
    title = ft.Text(
        "Transkun - 钢琴音频转MIDI",
        size=24,
        weight=ft.FontWeight.BOLD,
        color=THEME_PRIMARY
    )

    subtitle = ft.Text(
        "将钢琴演奏音频转换为MIDI文件",
        size=14,
        italic=True,
        color=ft.Colors.GREY_700
    )

    # Layout with spacing
    page.add(
        ft.Container(
            content=ft.Column(
                [title, subtitle],
                alignment=ft.MainAxisAlignment.CENTER,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=2,
            ),
            alignment=ft.alignment.center,
            padding=ft.padding.only(top=15, bottom=15),
        ),
        ft.Divider(height=1, color=ft.Colors.BLUE_100),
        ft.Container(
            content=ft.Column(
                [
                    section_header("1. 选择输入音频文件"),
                    input_row,
                    ft.Container(height=10),                    section_header("2. 选择MIDI输出位置 (可选)"),
                    output_row,
                    ft.Container(height=15),
                    ft.Row(
                        [start_button],
                        alignment=ft.MainAxisAlignment.CENTER
                    ),
                    ft.Container(height=8),
                    status_row,
                ],
                spacing=8,
            ),
            padding=ft.padding.only(top=8)
        ),
    )

# 启动应用
if __name__ == "__main__":
    ft.app(target=main, name="TranskunGUI")