"""Desktop shortcut that opens the dashboard with a double-click (Windows).

The shortcut runs `vjh dashboard` in a minimised window. If the dashboard is already
running, it just opens the browser again (see cli_track.cmd_dashboard)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SHORTCUT_NAME = "Job Hunter AI.lnk"

# Paths come in through environment variables, so no quoting problems with spaces.
_CREATE = r"""
$desk = [Environment]::GetFolderPath('Desktop')
$path = Join-Path $desk $env:VJH_LNK_NAME
$s = (New-Object -ComObject WScript.Shell).CreateShortcut($path)
$s.TargetPath = $env:VJH_LNK_TARGET
$s.Arguments = 'dashboard'
$s.WorkingDirectory = $env:VJH_LNK_DIR
$s.WindowStyle = 7
$s.Description = 'Open the Job Hunter AI dashboard'
if ($env:VJH_LNK_ICON) { $s.IconLocation = $env:VJH_LNK_ICON }
$s.Save()
Write-Output $path
"""

_REMOVE = r"""
$path = Join-Path ([Environment]::GetFolderPath('Desktop')) $env:VJH_LNK_NAME
if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path; Write-Output $path }
"""


def vjh_executable() -> Path:
    exe = Path(sys.executable).parent / "vjh.exe"
    if not exe.exists():
        raise RuntimeError(f"{exe} not found. Install the project first: pip install -e .")
    return exe


def make_icon(target: Path) -> Path | None:
    """A simple green 'JH' icon (Pillow is installed with Streamlit). None if it cannot be drawn."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None
    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((8, 8, size - 8, size - 8), radius=56, fill=(46, 125, 50, 255))
    try:
        font = ImageFont.truetype("segoeuib.ttf", 118)
    except OSError:
        font = ImageFont.load_default()
    d.text((size / 2, size / 2), "JH", fill="white", font=font, anchor="mm")
    target.parent.mkdir(parents=True, exist_ok=True)
    img.save(target, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return target


def _powershell(script: str, env: dict[str, str]) -> str:
    out = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                         env={**os.environ, **env}, capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or "PowerShell failed")
    return out.stdout.strip()


def install(root: Path, icon_dir: Path) -> str:
    if sys.platform != "win32":
        raise RuntimeError("desktop shortcuts are only supported on Windows")
    icon = make_icon(icon_dir / "vjh.ico")
    return _powershell(_CREATE, {"VJH_LNK_NAME": SHORTCUT_NAME, "VJH_LNK_TARGET": str(vjh_executable()),
                                 "VJH_LNK_DIR": str(root), "VJH_LNK_ICON": str(icon) if icon else ""})


def remove() -> str:
    if sys.platform != "win32":
        raise RuntimeError("desktop shortcuts are only supported on Windows")
    return _powershell(_REMOVE, {"VJH_LNK_NAME": SHORTCUT_NAME})
