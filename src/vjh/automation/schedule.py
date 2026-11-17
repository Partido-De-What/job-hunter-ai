"""Windows Task Scheduler integration via schtasks.exe and a task XML definition.

The task runs as the current user, only while logged on (Claude Code needs the user's login),
without administrator rights; it catches up missed runs, never overlaps, and stops after 2 h.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path
from xml.sax.saxutils import escape

TASK_XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Job Hunter AI: collect, filter and evaluate jobs, write the daily report. Never sends applications.</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>{start}</StartBoundary>
      <Enabled>true</Enabled>
      {schedule}
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
    <ExecutionTimeLimit>PT2H</ExecutionTimeLimit>
    <WakeToRun>false</WakeToRun>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{command}</Command>
      <Arguments>{arguments}</Arguments>
      <WorkingDirectory>{workdir}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


class ScheduleError(Exception):
    pass


def python_for_task() -> Path:
    """pythonw.exe (no console window) from the project's virtual environment."""
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    return pyw if pyw.exists() else exe


DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAY_XML = {"mon": "Monday", "tue": "Tuesday", "wed": "Wednesday", "thu": "Thursday", "fri": "Friday",
           "sat": "Saturday", "sun": "Sunday"}
# Ready-made choices for the setup wizard and the Settings page.
FREQUENCIES = {"Every day": DAYS, "Weekdays (Mon-Fri)": DAYS[:5], "Mon, Wed, Fri": ["mon", "wed", "fri"],
               "Twice a week (Mon, Thu)": ["mon", "thu"], "Once a week (Monday)": ["mon"]}


def describe_days(days: list[str]) -> str:
    days = [d for d in DAYS if d in set(days)]
    for label, ds in FREQUENCIES.items():
        if ds == days:
            return label.split(" (")[0].lower() if label != "Weekdays (Mon-Fri)" else "on weekdays"
    return "on " + ", ".join(DAY_XML[d][:3] for d in days)


def schedule_xml(days: list[str] | None) -> str:
    days = [d for d in DAYS if d in set(DAYS if days is None else days)]
    if not days:
        raise ScheduleError("choose at least one day")
    if len(days) == 7:
        return "<ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>"
    inner = "".join(f"<{DAY_XML[d]} />" for d in days)
    return f"<ScheduleByWeek><DaysOfWeek>{inner}</DaysOfWeek><WeeksInterval>1</WeeksInterval></ScheduleByWeek>"


def task_xml(*, time_hhmm: str, project_root: Path, python: Path, today: date | None = None,
             days: list[str] | None = None) -> str:
    start_day = (today or date.today()) + timedelta(days=1)
    return TASK_XML.format(start=f"{start_day.isoformat()}T{time_hhmm}:00", schedule=schedule_xml(days),
                           command=escape(str(python)),
                           arguments=escape("-m vjh.cli run-daily --log"),
                           workdir=escape(str(project_root)))


def _schtasks(args: list[str], runner=subprocess.run) -> subprocess.CompletedProcess:
    return runner(["schtasks", *args], capture_output=True, text=True, encoding="utf-8", errors="replace")


def install(name: str, time_hhmm: str, project_root: Path, xml_dir: Path, runner=subprocess.run,
            days: list[str] | None = None) -> str:
    xml_dir.mkdir(parents=True, exist_ok=True)
    xml_path = xml_dir / "vjh-daily-task.xml"
    xml_path.write_text(task_xml(time_hhmm=time_hhmm, project_root=project_root, python=python_for_task(),
                                 days=days), encoding="utf-16")
    proc = _schtasks(["/Create", "/TN", name, "/XML", str(xml_path), "/F"], runner)
    if proc.returncode != 0:
        raise ScheduleError((proc.stderr or proc.stdout).strip() or "schtasks /Create failed")
    return (proc.stdout or "").strip()


def remove(name: str, runner=subprocess.run) -> bool:
    proc = _schtasks(["/Delete", "/TN", name, "/F"], runner)
    return proc.returncode == 0


def run_now(name: str, runner=subprocess.run) -> None:
    proc = _schtasks(["/Run", "/TN", name], runner)
    if proc.returncode != 0:
        raise ScheduleError((proc.stderr or proc.stdout).strip() or "schtasks /Run failed")


STATUS_KEYS = {
    # English and German/Italian Windows labels (schtasks output follows the system language)
    "next_run": ("Next Run Time", "Nächste Laufzeit", "Prossima esecuzione"),
    "last_run": ("Last Run Time", "Letzte Laufzeit", "Ultima esecuzione"),
    "last_result": ("Last Result", "Letztes Ergebnis", "Ultimo esito"),
    "status": ("Status", "Stato"),
}
RESULT_CODES = {"0": "OK", "1": "finished with errors (see data\\logs\\daily-*.log)",
                "267009": "running now", "267011": "has not run yet",
                "267014": "stopped (took longer than 2 hours)"}


def describe_result(code: str) -> str:
    return f"{code} - {RESULT_CODES[code]}" if code in RESULT_CODES else code


def status(name: str, runner=subprocess.run) -> dict[str, str] | None:
    """None if the task does not exist."""
    proc = _schtasks(["/Query", "/TN", name, "/V", "/FO", "LIST"], runner)
    if proc.returncode != 0:
        return None
    out: dict[str, str] = {}
    for line in (proc.stdout or "").splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        for field, labels in STATUS_KEYS.items():
            if key.strip() in labels and field not in out:
                out[field] = value.strip()
    return out
