import os
import time
import torch
import gradio as gr
import threading
import traceback
import moduleconf
import transkun.transcribe
from pathlib import Path
import tempfile
import shutil

os.environ['NO_PROXY'] = "localhost, 127.0.0.1, ::1"

# 设置ffmpeg路径
current_dir = os.path.dirname(os.path.abspath(__file__))
ffmpeg_bin_path = os.path.join(current_dir, "ffmpeg_bin")

# 检查路径是否已在PATH中，避免重复添加
if ffmpeg_bin_path not in os.environ['PATH'].split(os.pathsep):
    os.environ['PATH'] = ffmpeg_bin_path + os.pathsep + os.environ['PATH']

# 检查CUDA是否可用
cuda_available = torch.cuda.is_available()

import mido
from collections import defaultdict, Counter

# 从原始代码复制MIDI处理函数
def midi_quantize(midi_path, debug=False, optimize_bpm=True):
    """
    分贝对于左右手（左右手可以通过C4 上下进行分隔）：
    对于当前同时按下的音符（按下时间间隔短），他们的时值统一到下一个音符被按下的时间
    注意只能拉伸尾端，音符的头端不能被动，相当于不能改按下事件

    optimize_bpm: 是否进行BPM优化
    """
    try:
        # 读取MIDI文件
        mid = mido.MidiFile(midi_path)

        # C4的MIDI音符号是60
        C4_NOTE = 60

        # 为每个音轨处理
        for track_idx, track in enumerate(mid.tracks):
            # 检查音轨是否包含音符事件
            has_notes = any(msg.type in ['note_on', 'note_off'] for msg in track)
            if not has_notes:
                continue

            # 收集所有音符事件
            notes_on = []  # 存储note_on事件
            notes_off = []  # 存储note_off事件
            other_events = []  # 存储其他事件

            current_time = 0

            # 解析音轨中的所有事件
            for msg in track:
                current_time += msg.time

                if msg.type == 'note_on' and msg.velocity > 0:
                    notes_on.append({
                        'time': current_time,
                        'note': msg.note,
                        'velocity': msg.velocity,
                        'channel': msg.channel,
                        'msg': msg
                    })
                elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                    notes_off.append({
                        'time': current_time,
                        'note': msg.note,
                        'velocity': msg.velocity if msg.type == 'note_off' else 0,
                        'channel': msg.channel,
                        'msg': msg
                    })
                else:
                    other_events.append({
                        'time': current_time,
                        'msg': msg
                    })

            # 按左右手分组（以C4为界）
            left_hand_notes = []  # C4以下（包含C4）
            right_hand_notes = []  # C4以上

            for note in notes_on:
                if note['note'] <= C4_NOTE:
                    left_hand_notes.append(note)
                else:
                    right_hand_notes.append(note)

            # 处理左右手的音符
            def process_hand_notes(hand_notes, hand_name=""):
                if len(hand_notes) < 1:
                    return

                # 按时间排序
                hand_notes.sort(key=lambda x: x['time'])

                if debug:
                    print(f"\n处理{hand_name}，共{len(hand_notes)}个音符:")
                    for note in hand_notes:
                        print(f"  音符{note['note']} 时间{note['time']}")

                # 找到同时按下的音符组
                TIME_THRESHOLD = 100  # 50 ticks内认为是同时按下

                i = 0
                while i < len(hand_notes):
                    # 找到当前时间点的所有同时按下的音符
                    current_time = hand_notes[i]['time']
                    simultaneous_notes = [hand_notes[i]]

                    j = i + 1
                    while j < len(hand_notes) and hand_notes[j]['time'] - current_time <= TIME_THRESHOLD:
                        simultaneous_notes.append(hand_notes[j])
                        j += 1

                    # 找到下一个音符组的开始时间
                    next_note_time = None
                    if j < len(hand_notes):
                        next_note_time = hand_notes[j]['time']

                    if debug:
                        note_names = [str(n['note']) for n in simultaneous_notes]
                        print(f"  同时音符组: {note_names} 在时间{current_time}, 下一组时间: {next_note_time}")

                    # 对于当前组的音符（不管是单个还是多个），都要调整时值
                    if next_note_time is not None:
                        # 调整当前组中所有音符的note_off时间
                        for note in simultaneous_notes:
                            # 找到对应的note_off事件（找到时间最近的且未被处理的那个）
                            best_off_event = None
                            min_time_diff = float('inf')

                            for off_event in notes_off:
                                if (off_event['note'] == note['note'] and
                                    off_event['channel'] == note['channel'] and
                                    off_event['time'] > note['time'] and
                                    not off_event.get('processed', False)):  # 确保未被处理过

                                    time_diff = off_event['time'] - note['time']
                                    if time_diff < min_time_diff:
                                        min_time_diff = time_diff
                                        best_off_event = off_event

                            if best_off_event is not None:
                                old_time = best_off_event['time']
                                # 将note_off时间设置到下一个音符开始前的一小段时间
                                # 确保不会太晚，也不会早于原始的最小持续时间
                                min_duration = 100  # 最小持续时间
                                target_time = next_note_time - 10
                                best_off_event['time'] = max(note['time'] + min_duration, target_time)
                                best_off_event['processed'] = True  # 标记为已处理

                                if debug:
                                    print(f"    音符{note['note']} off时间: {old_time} -> {best_off_event['time']}")

                    i = j

            # 处理左右手
            process_hand_notes(left_hand_notes, "左手")
            process_hand_notes(right_hand_notes, "右手")

            # 重建音轨
            all_events = []

            # 添加所有事件并按时间排序
            for note in notes_on:
                all_events.append(('note_on', note['time'], note))

            for note in notes_off:
                all_events.append(('note_off', note['time'], note))

            for event in other_events:
                all_events.append(('other', event['time'], event))

            # 按时间排序
            all_events.sort(key=lambda x: x[1])

            # 重建MIDI消息
            new_messages = []
            last_time = 0

            for event_type, event_time, event_data in all_events:
                delta_time = event_time - last_time

                if event_type == 'note_on':
                    msg = mido.Message('note_on',
                                       channel=event_data['channel'],
                                       note=event_data['note'],
                                       velocity=event_data['velocity'],
                                       time=delta_time)
                elif event_type == 'note_off':
                    msg = mido.Message('note_off',
                                       channel=event_data['channel'],
                                       note=event_data['note'],
                                       velocity=event_data['velocity'],
                                       time=delta_time)
                else:
                    msg = event_data['msg'].copy(time=delta_time)

                new_messages.append(msg)
                last_time = event_time

            # 替换原音轨
            track.clear()
            track.extend(new_messages)

        # 裁剪MIDI首尾空白
        if debug:
            print("裁剪MIDI首尾空白...")
        trim_midi_silence(mid, debug)

        # 保存处理后的文件
        output_path = os.path.splitext(midi_path)[0] + '_quantized.mid'
        mid.save(output_path)

        return output_path

    except Exception as e:
        raise Exception(f"处理MIDI文件时出错: {str(e)}")

def trim_midi_silence(mid, debug=False):
    """
    裁剪MIDI文件首尾的空白部分
    """
    try:
        # 找到第一个和最后一个音符事件的时间
        first_note_time = float('inf')
        last_note_time = 0

        for track in mid.tracks:
            current_time = 0
            track_first_note = None
            track_last_note = 0

            for msg in track:
                current_time += msg.time

                if msg.type == 'note_on' and msg.velocity > 0:
                    if track_first_note is None:
                        track_first_note = current_time
                    track_last_note = current_time

            if track_first_note is not None:
                first_note_time = min(first_note_time, track_first_note)
                last_note_time = max(last_note_time, track_last_note)

        if first_note_time == float('inf'):
            if debug:
                print("没有找到音符，跳过裁剪")
            return

        if debug:
            print(f"音符时间范围: {first_note_time} - {last_note_time}")

        # 调整所有音轨的时间
        for track in mid.tracks:
            if not track:
                continue

            # 重建消息，调整时间
            new_messages = []
            current_time = 0

            for msg in track:
                current_time += msg.time

                # 只保留在音符范围内的事件，或者是重要的元事件
                if (first_note_time <= current_time <= last_note_time + 1000 or  # 音符范围内
                    msg.type in ['set_tempo', 'key_signature', 'time_signature'] or  # 重要元事件
                    current_time < first_note_time):  # 开头的设置事件

                    # 调整时间：减去开头的空白时间
                    adjusted_time = max(0, current_time - first_note_time)
                    new_messages.append((adjusted_time, msg))

            # 重建track
            track.clear()
            if new_messages:
                last_time = 0
                for abs_time, msg in new_messages:
                    delta_time = abs_time - last_time
                    new_msg = msg.copy(time=delta_time)
                    track.append(new_msg)
                    last_time = abs_time

        if debug:
            print("MIDI裁剪完成")

    except Exception as e:
        if debug:
            print(f"MIDI裁剪失败: {e}")

# 核心转换函数
def process_audio(input_file, use_cuda=True, use_quantize=True, progress=gr.Progress(), file_progress_offset=0.0, file_progress_scale=1.0):
    """
    处理音频文件并生成MIDI文件。

    :param input_file: 输入音频文件路径。
    :param use_cuda: 是否使用CUDA加速。
    :param use_quantize: 是否对生成的MIDI文件进行量化处理。
    :param progress: Gradio进度条对象。
    :param file_progress_offset: 进度条的起始偏移量，用于批量处理。
    :param file_progress_scale: 进度条的缩放比例，用于批量处理。
    :return: 包含处理结果的字典。
    """
    temp_dir = None
    try:
        # The fix: create a temporary directory to store all output files
        # 修复：创建一个临时目录来存储所有的输出文件
        temp_dir = tempfile.mkdtemp()

        # Get a meaningful filename from the input file
        # 从输入文件中获取一个有意义的文件名
        input_name = Path(input_file).stem

        # Create the path for the non-quantized MIDI file inside the temp directory
        # 在临时目录中创建非量化MIDI文件的路径
        output_file = Path(temp_dir) / f"{input_name}.mid"

        quantized_output_file = None

        device = "cuda" if use_cuda and cuda_available else "cpu"

        start_time = time.time()
        progress(file_progress_offset, desc="准备模型...")

        # 加载模型和配置
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

        progress(file_progress_offset + 0.2 * file_progress_scale, desc="读取音频...")
        # 读取并处理音频
        fs, audio = transkun.transcribe.readAudio(input_file)
        if fs != model.fs:
            import soxr
            audio = soxr.resample(audio, fs, model.fs)

        x = torch.from_numpy(audio).to(device)

        progress(file_progress_offset + 0.4 * file_progress_scale, desc="转录中...")
        # 转录
        with torch.no_grad():
            notes_est = model.transcribe(x)

        progress(file_progress_offset + 0.7 * file_progress_scale, desc="保存MIDI...")
        # 保存MIDI到临时目录，将 Path 对象转换为字符串
        output_midi = transkun.transcribe.writeMidi(notes_est)
        output_midi.write(str(output_file))

        # 如果勾选了规整化选项，则进行MIDI规整化
        if use_quantize:
            progress(file_progress_offset + 0.8 * file_progress_scale, desc="规整化MIDI...")
            try:
                # The midi_quantize function will now write the output file with the expected name
                # midi_quantize函数现在将以预期的名称写入输出文件
                quantized_output_file = midi_quantize(str(output_file), debug=False, optimize_bpm=True)
            except Exception as e:
                print(f"规整化处理失败: {str(e)}")
                # 规整化失败不影响主流程

        end_time = time.time()
        process_time = round(end_time - start_time, 2)

        progress(file_progress_offset + 1.0 * file_progress_scale, desc="完成！")

        # 返回结果
        result_files = [str(output_file)]
        if quantized_output_file:
            result_files.append(quantized_output_file)

        return {
            "output": f"转换完成！用时 {process_time}秒",
            "files": result_files
        }

    except Exception as e:
        traceback.print_exc()
        return {
            "output": f"转换失败: {str(e)}",
            "files": []
        }
    # Removed the manual cleanup block, Gradio will handle this now.
    # 删除了手动清理代码块，现在由 Gradio 来处理。

# 创建Gradio界面
def create_interface():
    with gr.Blocks(title="Transkun - Piano Audio to MIDI", theme=gr.themes.Soft(primary_hue="blue")) as app:
        gr.Markdown(
            """
            # Transkun - 钢琴音频转MIDI
            将钢琴演奏音频转换为MIDI文件
            """
        )

        with gr.Row():
            with gr.Column(scale=2):
                # 输入部分
                gr.Markdown("### 1. 选择输入音频文件")
                input_audio = gr.File(label="输入音频文件", file_count="multiple", file_types=["audio"], interactive=True)

                gr.Markdown("### 2. 选择转换选项")
                with gr.Row():
                    use_cuda = gr.Checkbox(
                        label=f"启用CUDA加速 (CUDA {'可用 ✓' if cuda_available else '不可用 ✗'})",
                        value=cuda_available,
                        interactive=cuda_available
                    )

                use_quantize = gr.Checkbox(
                    label="使用MIDI规整化，让AI扒谱的输出更加美观易读（附带有_quantized后缀的输出文件）",
                    value=True,
                    info="基于简单的算法，不会影响扒谱的精确性"
                )

                convert_btn = gr.Button("开始转换", variant="primary")

            with gr.Column(scale=1):
                # 输出部分
                status_output = gr.Textbox(label="状态", value="准备就绪", interactive=False)
                file_output = gr.File(label="生成的MIDI文件", interactive=False, file_count="multiple")

                # 创建一个隐藏的文本框来存储文件路径
                file_paths_store = gr.State([])

                # 下载按钮
                with gr.Row():
                    download_all_btn = gr.Button("一键下载全部文件", variant="secondary", visible=False)
                    download_status = gr.Textbox(label="下载状态", value="", visible=False, interactive=False)

        # 处理函数
        def on_convert(audio_paths, use_cuda, use_quantize, progress=gr.Progress()):
            if not audio_paths:
                return "请选择输入音频文件", [], gr.update(visible=False)

            all_files = []
            results = []
            total_files = len(audio_paths)

            for i, audio_path in enumerate(audio_paths):
                file_name = Path(audio_path).name
                progress_offset = (i / total_files)
                progress_scale = (1 / total_files) * 0.9

                progress(progress_offset, desc=f"处理文件 {i+1}/{total_files}: {file_name}")
                result = process_audio(audio_path, use_cuda, use_quantize, progress,
                                      file_progress_offset=progress_offset,
                                      file_progress_scale=progress_scale)
                results.append(result["output"])
                all_files.extend(result["files"])

            progress(1.0, desc="全部完成！")
            download_btn_update = gr.update(visible=True) if all_files else gr.update(visible=False)
            download_status_update = gr.update(visible=False)
            return f"转换完成！共处理 {total_files} 个文件\n" + "\n".join(results), all_files, download_btn_update, download_status_update, all_files

        # 下载所有文件的函数
        def download_all_files(file_paths, status_output=None):
            import tempfile
            import os
            import shutil
            import zipfile
            from pathlib import Path

            if not file_paths or len(file_paths) == 0:
                return None, gr.update(value="没有文件可下载", visible=True), gr.update(visible=False)

            try:
                # 创建临时目录用于存放文件
                temp_dir = tempfile.mkdtemp(prefix="midi_files_")

                # 创建ZIP文件
                zip_path = os.path.join(temp_dir, "all_midi_files.zip")

                # 直接创建ZIP文件，不使用shutil.make_archive
                with zipfile.ZipFile(zip_path, 'w') as zipf:
                    for file_path in file_paths:
                        if os.path.exists(file_path):
                            # 只添加文件名，不包含路径
                            zipf.write(file_path, os.path.basename(file_path))

                return zip_path, gr.update(value="下载准备完成，请点击上方文件链接下载", visible=True), gr.update(visible=False)
            except Exception as e:
                return None, gr.update(value=f"下载准备失败: {str(e)}", visible=True), gr.update(visible=True)

        # 绑定按钮事件
        convert_btn.click(
            fn=on_convert,
            inputs=[input_audio, use_cuda, use_quantize],
            outputs=[status_output, file_output, download_all_btn, download_status, file_paths_store]
        )

        # 绑定下载按钮事件
        download_all_btn.click(
            fn=download_all_files,
            inputs=[file_paths_store, download_status],
            outputs=[file_output, download_status, download_all_btn]
        )

    return app

# 启动应用
def main():
    app = create_interface()
    # It's better to launch on 0.0.0.0 for broader access, though 127.0.0.1 is fine for local.
    # 最好在0.0.0.0上启动以便更广泛的访问，不过127.0.0.1用于本地也是可以的。
    app.launch(server_name="0.0.0.0", server_port=7860, share=False, inbrowser=True)

if __name__ == "__main__":
    main()
