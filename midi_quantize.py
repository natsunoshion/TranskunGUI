import mido
import tkinter as tk
from tkinter import filedialog, messagebox
import os
from collections import defaultdict, Counter

def midi_quantize(midi_path, debug=False, optimize_bpm=True):
    """
    分别对于左右手（左右手可以通过C4 上下进行分隔）：
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

def select_and_process_midi():
    """使用tkinter选择文件并处理"""
    root = tk.Tk()
    root.withdraw()  # 隐藏主窗口

    # 选择MIDI文件
    file_path = filedialog.askopenfilename(
        title="选择MIDI文件",
        filetypes=[("MIDI files", "*.mid *.midi"), ("All files", "*.*")]
    )

    if not file_path:
        messagebox.showinfo("提示", "未选择文件")
        return

    try:
        output_path = midi_quantize(file_path)
        messagebox.showinfo("成功", f"处理完成！\n输出文件：{output_path}")
    except Exception as e:
        messagebox.showerror("错误", f"处理失败：{str(e)}")

    root.destroy()

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


if __name__ == "__main__":
    # 使用GUI选择文件
    select_and_process_midi()