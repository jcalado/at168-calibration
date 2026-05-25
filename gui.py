#!/usr/bin/env python3
"""AT-D168UV Calibration Backup — Dear PyGui GUI."""

import os
import queue
import threading
import time

import dearpygui.dearpygui as dpg
import serial
import serial.tools.list_ports

import radio

# ── Queue message types ──────────────────────────────────────────────────────
# ("log",      message: str, level: str)
# ("progress", fraction: float, label: str)
# ("done",     file_paths: list[str])
# ("error",    message: str)

MSG_QUEUE: queue.Queue = queue.Queue()

# ── Tag constants ────────────────────────────────────────────────────────────
TAG_PORT_COMBO = "port_combo"
TAG_PREFIX_INPUT = "prefix_input"
TAG_BACKUP_BTN = "backup_btn"
TAG_REFRESH_BTN = "refresh_btn"
TAG_BROWSE_BTN = "browse_btn"
TAG_PROGRESS = "progress_bar"
TAG_PROGRESS_OVERLAY = "progress_overlay_text"
TAG_LOG = "log_window"
TAG_FILE_DIALOG = "file_dialog"
TAG_BACKUP_TOOLTIP = "backup_tooltip"
TAG_BACKUP_TOOLTIP_TEXT = "backup_tooltip_text"
TAG_PORT_TOOLTIP = "port_tooltip"
TAG_PORT_TOOLTIP_TEXT = "port_tooltip_text"
TAG_DONE_MODAL = "done_modal"
TAG_DONE_MODAL_TEXT = "done_modal_text"

RUNNING = False
PORT_INFO: dict[str, serial.tools.list_ports_common.ListPortInfo] = {}


# ── Port scanning ────────────────────────────────────────────────────────────

def refresh_ports() -> None:
    global PORT_INFO
    PORT_INFO.clear()
    comports = serial.tools.list_ports.comports()
    if comports:
        items = []
        for p in comports:
            label = p.device
            if p.description and p.description != "n/a":
                label = f"{p.device} — {p.description}"
            items.append(label)
            PORT_INFO[label] = p
        dpg.configure_item(TAG_PORT_COMBO, items=items, default_value=items[0])
    else:
        dpg.configure_item(
            TAG_PORT_COMBO,
            items=["No serial ports detected"],
            default_value="No serial ports detected",
        )
    update_backup_button_state()
    update_port_tooltip()


def update_port_tooltip() -> None:
    selected = dpg.get_value(TAG_PORT_COMBO)
    info = PORT_INFO.get(selected)
    if info:
        lines = [f"Device: {info.device}"]
        if info.description and info.description != "n/a":
            lines.append(f"Description: {info.description}")
        if info.manufacturer:
            lines.append(f"Manufacturer: {info.manufacturer}")
        if info.hwid and info.hwid != "n/a":
            lines.append(f"HW ID: {info.hwid}")
        dpg.set_value(TAG_PORT_TOOLTIP_TEXT, "\n".join(lines))
    else:
        dpg.set_value(TAG_PORT_TOOLTIP_TEXT, "No port selected")


def on_refresh_clicked(sender=None, app_data=None) -> None:
    refresh_ports()


# ── File dialog ──────────────────────────────────────────────────────────────

def on_file_selected(sender, app_data) -> None:
    file_path = app_data["file_path_name"]
    base, ext = os.path.splitext(file_path)
    if ext:
        file_path = base
    dpg.set_value(TAG_PREFIX_INPUT, file_path)
    update_backup_button_state()


# ── Backup button state ─────────────────────────────────────────────────────

def update_backup_button_state() -> None:
    if RUNNING:
        dpg.configure_item(TAG_BACKUP_BTN, enabled=False)
        dpg.set_value(TAG_BACKUP_TOOLTIP_TEXT, "Backup in progress...")
        return
    port = dpg.get_value(TAG_PORT_COMBO)
    prefix = dpg.get_value(TAG_PREFIX_INPUT).strip()
    if not port or port == "No serial ports detected":
        dpg.configure_item(TAG_BACKUP_BTN, enabled=False)
        dpg.set_value(TAG_BACKUP_TOOLTIP_TEXT, "Select a serial port first")
    elif not prefix:
        dpg.configure_item(TAG_BACKUP_BTN, enabled=False)
        dpg.set_value(TAG_BACKUP_TOOLTIP_TEXT, "Enter an output file prefix first")
    else:
        dpg.configure_item(TAG_BACKUP_BTN, enabled=True)
        dpg.set_value(TAG_BACKUP_TOOLTIP_TEXT, "Start calibration data backup")


def on_prefix_changed(sender, app_data) -> None:
    update_backup_button_state()


# ── Logging to GUI ──────────────────────────────────────────────────────────

LOG_COLORS = {
    "info": (255, 255, 255, 255),
    "success": (100, 255, 100, 255),
    "error": (255, 80, 80, 255),
}


def append_log(message: str, level: str = "info") -> None:
    timestamp = time.strftime("%H:%M:%S")
    color = LOG_COLORS.get(level, LOG_COLORS["info"])
    dpg.add_text(
        f"[{timestamp}] {message}",
        parent=TAG_LOG,
        color=color,
    )
    dpg.set_y_scroll(TAG_LOG, dpg.get_y_scroll_max(TAG_LOG) + 100)


# ── Set controls enabled/disabled ───────────────────────────────────────────

def set_controls_enabled(enabled: bool) -> None:
    dpg.configure_item(TAG_PORT_COMBO, enabled=enabled)
    dpg.configure_item(TAG_PREFIX_INPUT, enabled=enabled)
    dpg.configure_item(TAG_REFRESH_BTN, enabled=enabled)
    dpg.configure_item(TAG_BROWSE_BTN, enabled=enabled)
    if enabled:
        update_backup_button_state()
    else:
        dpg.configure_item(TAG_BACKUP_BTN, enabled=False)


# ── Worker thread ────────────────────────────────────────────────────────────

def backup_worker(port_name: str, file_prefix: str) -> None:
    def on_log(message: str, level: str) -> None:
        MSG_QUEUE.put(("log", message, level))

    def on_progress(fraction: float, label: str) -> None:
        MSG_QUEUE.put(("progress", fraction, label))

    try:
        written = radio.run_backup(port_name, file_prefix, on_progress, on_log)
        MSG_QUEUE.put(("done", written))
    except (RuntimeError, TimeoutError, serial.SerialException, OSError) as e:
        MSG_QUEUE.put(("error", str(e)))


# ── Backup button ───────────────────────────────────────────────────────────

def on_backup_clicked(sender=None, app_data=None) -> None:
    global RUNNING
    if RUNNING:
        return
    RUNNING = True
    set_controls_enabled(False)
    dpg.set_value(TAG_PROGRESS, 0.0)
    dpg.configure_item(TAG_PROGRESS, overlay="")

    selected = dpg.get_value(TAG_PORT_COMBO)
    info = PORT_INFO.get(selected)
    port_name = info.device if info else selected.split(" — ")[0]
    file_prefix = dpg.get_value(TAG_PREFIX_INPUT).strip()

    thread = threading.Thread(
        target=backup_worker,
        args=(port_name, file_prefix),
        daemon=True,
    )
    thread.start()


# ── Per-frame queue poll ─────────────────────────────────────────────────────

def poll_queue() -> None:
    global RUNNING
    while True:
        try:
            msg = MSG_QUEUE.get_nowait()
        except queue.Empty:
            break

        msg_type = msg[0]

        if msg_type == "log":
            _, message, level = msg
            append_log(message, level)

        elif msg_type == "progress":
            _, fraction, label = msg
            dpg.set_value(TAG_PROGRESS, fraction)
            pct = int(fraction * 100)
            dpg.configure_item(TAG_PROGRESS, overlay=f"{label}: {pct}%")

        elif msg_type == "done":
            _, file_paths = msg
            append_log("Backup complete!", "success")
            dpg.set_value(TAG_PROGRESS, 1.0)
            dpg.configure_item(TAG_PROGRESS, overlay="Complete")
            RUNNING = False
            set_controls_enabled(True)
            show_done_modal(file_paths)

        elif msg_type == "error":
            _, message = msg
            append_log(f"ERROR: {message}", "error")
            dpg.set_value(TAG_PROGRESS, 0.0)
            dpg.configure_item(TAG_PROGRESS, overlay="Error")
            RUNNING = False
            set_controls_enabled(True)


# ── Completion modal ─────────────────────────────────────────────────────────

def show_done_modal(file_paths: list[str]) -> None:
    lines = ["Backup completed successfully!\n", "Files written:"]
    for path in file_paths:
        try:
            size = os.path.getsize(path)
            lines.append(f"  {path}  ({size} bytes)")
        except OSError:
            lines.append(f"  {path}")
    dpg.set_value(TAG_DONE_MODAL_TEXT, "\n".join(lines))
    dpg.configure_item(TAG_DONE_MODAL, show=True)


def on_done_modal_close(sender=None, app_data=None) -> None:
    dpg.configure_item(TAG_DONE_MODAL, show=False)


# ── Build UI ─────────────────────────────────────────────────────────────────

def create_ui() -> None:
    dpg.create_context()
    dpg.create_viewport(
        title="AT-D168UV Calibration Backup",
        width=620,
        height=420,
        resizable=True,
        min_width=500,
        min_height=300,
    )

    # File dialog (hidden until Browse clicked)
    with dpg.file_dialog(
        tag=TAG_FILE_DIALOG,
        callback=on_file_selected,
        show=False,
        default_filename="backup",
        width=500,
        height=350,
        modal=True,
    ):
        dpg.add_file_extension(".bin")
        dpg.add_file_extension(".*")

    # Completion modal (hidden until backup finishes)
    with dpg.window(
        tag=TAG_DONE_MODAL,
        label="Backup Complete",
        modal=True,
        show=False,
        no_resize=True,
        width=400,
        height=180,
    ):
        dpg.add_text(tag=TAG_DONE_MODAL_TEXT, default_value="")
        dpg.add_spacer(height=10)
        dpg.add_button(label="OK", callback=on_done_modal_close, width=-1)

    # Main window
    with dpg.window(tag="primary"):
        # Row 1: Port selector + Refresh
        with dpg.group(horizontal=True):
            dpg.add_text("Port:")
            dpg.add_combo(
                tag=TAG_PORT_COMBO,
                items=["Scanning..."],
                default_value="Scanning...",
                width=-80,
                callback=lambda s, a: update_port_tooltip(),
            )
            dpg.add_button(
                tag=TAG_REFRESH_BTN,
                label="Refresh",
                callback=on_refresh_clicked,
            )
        with dpg.tooltip(TAG_PORT_COMBO, tag=TAG_PORT_TOOLTIP):
            dpg.add_text(tag=TAG_PORT_TOOLTIP_TEXT, default_value="No port selected")

        # Row 2: File output + Browse
        with dpg.group(horizontal=True):
            dpg.add_text("File:")
            dpg.add_input_text(
                tag=TAG_PREFIX_INPUT,
                hint="output file prefix",
                width=-40,
                callback=on_prefix_changed,
                on_enter=False,
            )
            dpg.add_button(
                tag=TAG_BROWSE_BTN,
                label="...",
                callback=lambda: dpg.show_item(TAG_FILE_DIALOG),
            )

        dpg.add_spacer(height=5)

        # Row 3: Backup button
        dpg.add_button(
            tag=TAG_BACKUP_BTN,
            label="Backup",
            callback=on_backup_clicked,
            enabled=False,
            width=-1,
        )
        with dpg.tooltip(TAG_BACKUP_BTN, tag=TAG_BACKUP_TOOLTIP):
            dpg.add_text(
                tag=TAG_BACKUP_TOOLTIP_TEXT,
                default_value="Select a serial port first",
            )

        # Progress bar
        dpg.add_progress_bar(
            tag=TAG_PROGRESS,
            default_value=0.0,
            overlay="",
            width=-1,
        )

        # Log area
        with dpg.child_window(tag=TAG_LOG, autosize_x=True, autosize_y=True):
            dpg.add_text(
                "Ready. Select a serial port and file prefix, then click Backup.",
                color=(180, 180, 180, 255),
            )

    dpg.set_primary_window("primary", True)


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    create_ui()
    dpg.setup_dearpygui()
    dpg.show_viewport()
    refresh_ports()

    while dpg.is_dearpygui_running():
        poll_queue()
        dpg.render_dearpygui_frame()

    dpg.destroy_context()


if __name__ == "__main__":
    main()
