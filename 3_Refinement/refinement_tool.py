import sys
import os
import re
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QPushButton, QFileDialog, QStyle, QSlider, QLabel, QTableWidget, 
    QTableWidgetItem, QHeaderView, QAbstractItemView, QMessageBox, 
    QSplitter, QGroupBox, QAction, QLineEdit, QShortcut, QMenu
)
from PyQt5.QtMultimedia import QMediaPlayer, QMediaContent
from PyQt5.QtMultimediaWidgets import QVideoWidget
from PyQt5.QtCore import Qt, QUrl, QTime, QTimer, QEvent
from PyQt5.QtGui import QKeySequence, QColor

# --- Helper Functions for Time Conversion ---
def seconds_to_str(seconds):
    """Converts seconds (float/int) to HH:MM:SS string."""
    seconds = int(round(seconds))
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"

def str_to_seconds(time_str):
    """Converts HH:MM:SS string to seconds (int). Returns None if invalid."""
    try:
        parts = list(map(int, time_str.split(':')))
        if len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
        elif len(parts) == 2:
            return parts[0] * 60 + parts[1]
    except ValueError:
        pass
    return None

class RefinementApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Race Log Refiner")
        self.resize(1200, 800)

        # State
        self.duration = 0
        self.is_auto_scrolling = True 
        self.current_log_file_path = None

        # Main Layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # Splitter to resize Video vs Logs
        splitter = QSplitter(Qt.Vertical)
        main_layout.addWidget(splitter)

        # --- Top: Video Player ---
        video_container = QWidget()
        video_layout = QVBoxLayout(video_container)
        video_layout.setContentsMargins(0, 0, 0, 0)

        self.video_widget = QVideoWidget()
        self.media_player = QMediaPlayer(None, QMediaPlayer.VideoSurface)
        self.media_player.setVideoOutput(self.video_widget)
        self.media_player.positionChanged.connect(self.on_position_changed)
        self.media_player.durationChanged.connect(self.on_duration_changed)
        self.media_player.error.connect(self.handle_errors)

        # Controls
        controls_layout = QHBoxLayout()
        
        self.play_btn = QPushButton()
        self.play_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        self.play_btn.clicked.connect(self.toggle_play)
        
        self.time_label = QLabel("00:00:00 / 00:00:00")
        
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.sliderMoved.connect(self.set_position)
        self.slider.sliderPressed.connect(self.pause_video) # Pause while dragging
        self.slider.sliderReleased.connect(self.play_video)

        controls_layout.addWidget(self.play_btn)
        controls_layout.addWidget(self.time_label)
        controls_layout.addWidget(self.slider)

        video_layout.addWidget(self.video_widget, 1) # Video takes expanding space
        video_layout.addLayout(controls_layout)

        splitter.addWidget(video_container)

        # --- Bottom: Log Editor ---
        log_container = QGroupBox("Event Log (Double-click time to jump, Select row to sync)")
        log_layout = QVBoxLayout(log_container)

        # Toolbar for Logs
        log_toolbar = QHBoxLayout()
        
        btn_load_vid = QPushButton("1. Load Video")
        btn_load_vid.clicked.connect(self.load_video)
        
        btn_load_log = QPushButton("2. Load Log")
        btn_load_log.clicked.connect(self.load_log)
        
        self.btn_merge_log = QPushButton("Merge Log")
        self.btn_merge_log.clicked.connect(self.merge_log)
        self.btn_merge_log.setEnabled(True)
        
        btn_save_log = QPushButton("3. Save Log")
        btn_save_log.clicked.connect(self.save_log)
        
        self.btn_sync = QPushButton("Sync All to Selected Row")
        self.btn_sync.setToolTip("Shifts ALL timestamps so the selected row matches the current video time.")
        self.btn_sync.clicked.connect(self.sync_logs_to_video)
        self.btn_sync.setEnabled(False)

        self.chk_autoscroll = QPushButton("Auto-Scroll: ON")
        self.chk_autoscroll.setCheckable(True)
        self.chk_autoscroll.setChecked(True)
        self.chk_autoscroll.clicked.connect(self.toggle_autoscroll)

        log_toolbar.addWidget(btn_load_vid)
        log_toolbar.addWidget(btn_load_log)
        log_toolbar.addWidget(self.btn_merge_log)
        log_toolbar.addWidget(btn_save_log)
        log_toolbar.addStretch()
        log_toolbar.addWidget(self.chk_autoscroll)
        log_toolbar.addWidget(self.btn_sync)
        
        # --- Event Input Area ---
        input_layout = QHBoxLayout()
        self.event_input = QLineEdit()
        self.event_input.setPlaceholderText("Press Space to pause and log event...")
        self.event_input.setEnabled(False)
        self.event_input.returnPressed.connect(self.submit_event)
        input_layout.addWidget(QLabel("New Event:"))
        input_layout.addWidget(self.event_input)

        # Table Widget
        self.table = QTableWidget()
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["Time", "Event Description"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection) # Allow multiple selection
        self.table.itemDoubleClicked.connect(self.on_table_double_click)
        self.table.itemSelectionChanged.connect(self.on_selection_changed)
        self.table.itemChanged.connect(self.on_item_changed)
        
        # Context Menu
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.open_context_menu)

        log_layout.addLayout(log_toolbar)
        log_layout.addLayout(input_layout)
        log_layout.addWidget(self.table)
        
        splitter.addWidget(log_container)

        # Set initial splitter sizes (Video bigger)
        splitter.setSizes([500, 300])

        # Style
        self.setStyleSheet("""
            QGroupBox { font-weight: bold; }
            QTableWidget::item:selected { background-color: #0078d7; color: white; }
        """)

        # Internal state for logging
        self.log_timestamp_sec = 0

        # --- QShortcuts ---
        self.space_shortcut = QShortcut(QKeySequence(Qt.Key_Space), self)
        self.space_shortcut.setContext(Qt.ApplicationShortcut)
        self.space_shortcut.activated.connect(self.handle_space_bar)

        self.delete_shortcut = QShortcut(QKeySequence(Qt.Key_Delete), self)
        self.delete_shortcut.setContext(Qt.WindowShortcut)
        self.delete_shortcut.activated.connect(self.delete_selected_rows)

        # Check for CLI args for auto-loading
        if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
            print(f"Auto-loading log file: {sys.argv[1]}")
            self.current_log_file_path = sys.argv[1]
            self.populate_table(sys.argv[1])
            self.btn_merge_log.setEnabled(True)

    def handle_space_bar(self):
        """Called when Space is pressed (unless consumed by focused input)."""
        # If input is already active, we shouldn't be here because we disable the shortcut
        # But for safety:
        if self.event_input.isEnabled():
            return

        # Trigger Logging
        self.start_logging_event()

    # --- Context Menu ---
    def open_context_menu(self, position):
        menu = QMenu()
        
        # Actions
        merge_action = menu.addAction("Merge Selected Events")
        delete_action = menu.addAction("Delete Selected")
        
        # Check selection for enabling merge
        selected_indexes = self.table.selectedIndexes()
        rows = set(index.row() for index in selected_indexes)
        if len(rows) < 2:
            merge_action.setEnabled(False)

        action = menu.exec_(self.table.mapToGlobal(position))
        
        if action == delete_action:
            self.delete_selected_rows()
        elif action == merge_action:
            self.merge_selected_events()

    def delete_selected_rows(self):
        """Deletes currently selected rows."""
        # If we're editing a cell or typing a new event, don't delete the row
        if self.event_input.hasFocus() or self.table.state() == QAbstractItemView.EditingState:
            return

        rows = sorted(set(index.row() for index in self.table.selectedIndexes()), reverse=True)
        if not rows: return

        confirm = QMessageBox.question(
            self, "Confirm Delete", 
            f"Delete {len(rows)} selected event(s)?", 
            QMessageBox.Yes | QMessageBox.No
        )
        if confirm == QMessageBox.Yes:
            for row in rows:
                self.table.removeRow(row)

    def merge_selected_events(self):
        """Merges multiple selected events into the earliest one."""
        rows = sorted(set(index.row() for index in self.table.selectedIndexes()))
        if len(rows) < 2: return
        
        first_row = rows[0]
        merged_text_parts = []
        
        # Collect text from all rows
        for row in rows:
            text = self.table.item(row, 1).text().strip()
            if text:
                merged_text_parts.append(text)
        
        merged_text = " | ".join(merged_text_parts)
        
        # Update first row
        self.table.item(first_row, 1).setText(merged_text)
        
        # Delete other rows (in reverse order to preserve indices)
        for row in sorted(rows[1:], reverse=True):
            self.table.removeRow(row)

    # --- Video Logic ---

    def load_video(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Open Video", "", "Video Files (*.mp4 *.avi *.mkv *.mov)")
        if file_path:
            self.media_player.setMedia(QMediaContent(QUrl.fromLocalFile(file_path)))
            self.play_btn.setEnabled(True)
            self.media_player.play()
            # Ensure focus returns to main window to capture space bar
            self.setFocus()

    def toggle_play(self):
        if self.media_player.state() == QMediaPlayer.PlayingState:
            self.media_player.pause()
        else:
            self.media_player.play()
        self.setFocus()

    def play_video(self):
        self.media_player.play()
        self.setFocus()

    def pause_video(self):
        self.media_player.pause()

    def on_position_changed(self, position):
        self.slider.setValue(position)
        self.update_time_label(position)

        # Only auto-scroll if enabled AND not currently editing or adding an event
        if self.is_auto_scrolling and not self.event_input.isEnabled() and self.table.state() != QAbstractItemView.EditingState:
            self.highlight_current_event(position)

    def on_duration_changed(self, duration):
        self.slider.setRange(0, duration)
        self.duration = duration
        self.update_time_label(0)

    def on_item_changed(self, item):
        """Called when a table item is edited."""
        self.apply_row_color(item.row())
        if item.column() == 0:
            # Re-sort table by time if timecode was changed
            self.table.blockSignals(True)
            self.table.sortItems(0, Qt.AscendingOrder)
            self.table.blockSignals(False)
            # Ensure the edited item is still visible and selected after sorting
            self.table.scrollToItem(item)
            self.table.selectRow(item.row())

    def apply_row_color(self, row):
        """Sets the background color of a row based on its event description."""
        event_item = self.table.item(row, 1)
        if not event_item:
            return
        
        description = event_item.text().lower()
        
        bg_color = None
        fg_color = None
        
        if "overtake" in description:
            bg_color = QColor(0, 128, 0) # Dark Green
            fg_color = Qt.white
        elif "accident" in description:
            bg_color = QColor(178, 34, 34) # Firebrick Red
            fg_color = Qt.white
            
        for col in range(self.table.columnCount()):
            item = self.table.item(row, col)
            if item:
                if bg_color:
                    item.setBackground(bg_color)
                    item.setForeground(fg_color)
                else:
                    # Reset to default - using setData with None clears the roles
                    item.setData(Qt.BackgroundRole, None)
                    item.setData(Qt.ForegroundRole, None)

    def set_position(self, position):
        self.media_player.setPosition(position)
        # Force a small update to ensure position is reflected
        self.update_time_label(position)
        self.setFocus()

    def update_time_label(self, current_ms):
        current_str = seconds_to_str(current_ms // 1000)
        total_str = seconds_to_str(self.duration // 1000)
        self.time_label.setText(f"{current_str} / {total_str}")

    def handle_errors(self):
        self.play_btn.setEnabled(False)
        QMessageBox.critical(self, "Error", "Video Player Error: " + self.media_player.errorString())

    # --- Log Logic ---

    def start_logging_event(self):
        """Pauses video and prepares input field for new event."""
        if self.media_player.state() == QMediaPlayer.PlayingState:
            self.media_player.pause()

        # Process events to ensure player state is updated
        QApplication.processEvents()
        
        # Capture the current position. If the player is paused and we just scrubbed,
        # the slider might be more accurate than the player's reported position
        # in some backends.
        current_ms = self.media_player.position()
        if self.media_player.state() != QMediaPlayer.PlayingState:
            current_ms = max(current_ms, self.slider.value())

        self.log_timestamp_sec = current_ms / 1000.0
        time_str = seconds_to_str(self.log_timestamp_sec)

        self.event_input.setEnabled(True)
        self.event_input.setPlaceholderText(f"Enter event description for {time_str}...")
        self.event_input.setFocus()

        # Disable shortcut so space can be typed
        self.space_shortcut.setEnabled(False)

    def submit_event(self):
        """Adds the event to the table and resumes video."""
        description = self.event_input.text().strip()
        if description:
            self.add_event_to_table(self.log_timestamp_sec, description)

        self.event_input.clear()
        self.event_input.setEnabled(False)
        self.event_input.setPlaceholderText("Press Space to pause and log event...")

        # Re-enable shortcut for next time
        self.space_shortcut.setEnabled(True)

        # Resume video and focus
        self.media_player.play()
        self.setFocus()

    def add_event_to_table(self, seconds, description):
        """Inserts a new event row, maintaining sort order."""
        time_str = seconds_to_str(seconds)

        # Find insertion index to keep sorted
        insert_row = self.table.rowCount() # Default to end
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item:
                row_seconds = str_to_seconds(item.text())
                if row_seconds is not None and row_seconds > seconds:
                    insert_row = row
                    break

        self.table.insertRow(insert_row)

        # Time Item
        time_item = QTableWidgetItem(time_str)
        time_item.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(insert_row, 0, time_item)

        # Event Item
        event_item = QTableWidgetItem(description)
        self.table.setItem(insert_row, 1, event_item)

        # Scroll to new item
        self.table.scrollToItem(time_item)
        self.table.selectRow(insert_row)

    def load_log(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Open Log", "", "Text Files (*.txt)")
        if file_path:
            self.current_log_file_path = file_path
            self.populate_table(file_path)
            self.btn_merge_log.setEnabled(True)
            self.setFocus()

    def merge_log(self):
        """Opens a file dialog to select another log and merges it into the current table."""
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Log to Merge", "", "Text Files (*.txt)")
        if file_path:
            self.populate_table(file_path, clear=False)
            QMessageBox.information(self, "Merged", f"Events from {os.path.basename(file_path)} have been merged and sorted.")
            self.setFocus()

    def populate_table(self, file_path, clear=True):
        if clear:
            self.table.setRowCount(0)
        
        self.table.blockSignals(True) # Block signals during batch add
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            for line in lines:
                if not line.strip(): continue
                # Parse "HH:MM:SS - Event"
                parts = line.split(' - ', 1)
                if len(parts) == 2:
                    time_str = parts[0].strip()
                    event_str = parts[1].strip()
                else:
                    # Fallback for bad lines
                    time_str = "00:00:00"
                    event_str = line.strip()

                row = self.table.rowCount()
                self.table.insertRow(row)

                # Time Item
                time_item = QTableWidgetItem(time_str)
                time_item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row, 0, time_item)

                # Event Item
                event_item = QTableWidgetItem(event_str)
                self.table.setItem(row, 1, event_item)
            
            # Sort the table by time (Column 0)
            self.table.sortItems(0, Qt.AscendingOrder)

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load log: {e}")
        finally:
            self.table.blockSignals(False) # Re-enable signals

    def save_log(self):
        # Determine initial directory and filename
        initial_dir = ""
        initial_file = ""
        
        project_path = os.environ.get("R3E_PROJECT_PATH")
        if project_path:
            initial_dir = project_path
        
        if self.current_log_file_path:
            initial_file = os.path.basename(self.current_log_file_path)
        
        save_path = os.path.join(initial_dir, initial_file) if initial_dir and initial_file else (self.current_log_file_path or "")
        
        file_path, _ = QFileDialog.getSaveFileName(self, "Save Log", save_path, "Text Files (*.txt)")
        if file_path:
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    for row in range(self.table.rowCount()):
                        time_text = self.table.item(row, 0).text()
                        event_text = self.table.item(row, 1).text()
                        f.write(f"{time_text} - {event_text}\n")

                self.current_log_file_path = file_path
                QMessageBox.information(self, "Success", "Log saved successfully.")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to save log: {e}")

    # --- Sync & Interaction Logic ---

    def highlight_current_event(self, video_ms):
        """Highlights the row corresponding to the current video time."""
        # Extra safety: Don't highlight if we are editing or the input is active
        if self.table.state() == QAbstractItemView.EditingState or self.event_input.isEnabled():
            return

        current_seconds = video_ms / 1000
        best_row = -1

        # Simple linear search (logs are usually sorted, but not guaranteed if edited)
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item:
                time_text = item.text()
                seconds = str_to_seconds(time_text)
                if seconds is not None:
                    if seconds <= current_seconds:
                        best_row = row
                    else:
                        break # Assuming sorted

        if best_row != -1:
            self.table.blockSignals(True) # Prevent triggering selection change logic
            self.table.selectRow(best_row)
            self.table.scrollToItem(self.table.item(best_row, 0), QAbstractItemView.PositionAtCenter)
            self.table.blockSignals(False)

    def on_table_double_click(self, item):
        """Seek video to the timestamp of the clicked row if Time column is clicked."""
        if item.column() != 0:
            return

        row = item.row()
        time_text = self.table.item(row, 0).text()
        seconds = str_to_seconds(time_text)
        if seconds is not None:
            self.media_player.setPosition(int(seconds * 1000))
            # DO NOT automatically play. This allows the user to edit the time
            # without the video immediately moving away and causing auto-scroll/re-selection.
            self.play_btn.setEnabled(True)
            # self.media_player.play() # Removed to avoid jumping while editing

    def on_selection_changed(self):
        """Enable sync button only if a row is selected."""
        has_selection = bool(self.table.selectedItems())
        self.btn_sync.setEnabled(has_selection)

    def toggle_autoscroll(self):
        self.is_auto_scrolling = self.chk_autoscroll.isChecked()
        self.chk_autoscroll.setText(f"Auto-Scroll: {'ON' if self.is_auto_scrolling else 'OFF'}")

    def sync_logs_to_video(self):
        """
        Calculates the offset between the Current Video Time and the Selected Row Time.
        Applies this offset to ALL rows in the table.
        """
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows: return

        row_idx = selected_rows[0].row()
        log_time_str = self.table.item(row_idx, 0).text()
        log_seconds = str_to_seconds(log_time_str)

        if log_seconds is None:
            QMessageBox.warning(self, "Error", "Selected row has an invalid timestamp.")
            return

        video_seconds = self.media_player.position() / 1000.0

        # Calculate Offset
        offset = video_seconds - log_seconds

        confirm = QMessageBox.question(
            self, "Confirm Sync", 
            f"Current Video Time: {seconds_to_str(video_seconds)}\n"
            f"Selected Log Time: {log_time_str}\n"
            f"Offset: {offset:+.2f} seconds\n\n"
            "This will shift ALL timestamps in the list by this amount. Proceed?",
            QMessageBox.Yes | QMessageBox.No
        )

        if confirm == QMessageBox.Yes:
            self.apply_offset(offset)

    def apply_offset(self, offset_seconds):
        self.table.blockSignals(True)
        try:
            for row in range(self.table.rowCount()):
                item = self.table.item(row, 0)
                original_seconds = str_to_seconds(item.text())
                if original_seconds is not None:
                    new_seconds = max(0, original_seconds + offset_seconds)
                    item.setText(seconds_to_str(new_seconds))
            
            # Sort once after all updates
            self.table.sortItems(0, Qt.AscendingOrder)
        finally:
            self.table.blockSignals(False)

        QMessageBox.information(self, "Synced", "All timestamps have been updated.")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = RefinementApp()
    window.show()
    sys.exit(app.exec_())
