#!/usr/bin/env python3
"""Create and close the real main window using Qt's offscreen platform."""
import json
from pathlib import Path
import sys
from PySide6.QtCore import QTimer
from NepTrainKit.main import create_app, create_main_window

app = create_app(['nepkit'])
window = create_main_window(show=True)
assert window.isVisible()
QTimer.singleShot(1500, window.close)
QTimer.singleShot(2500, app.quit)
code = app.exec()
assert code == 0
Path(sys.argv[1]).write_text(json.dumps(dict(passed=True, platform=app.platformName(),
    window=type(window).__name__, exitcode=code,
    scope='offscreen main-window startup and clean shutdown; no remote display qualification'), indent=2) + '\n')
