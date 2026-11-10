"""Render user service units with directive-appropriate systemd escaping."""
from pathlib import Path
import sys

NAMES = ("airspace-analytics.service", "airspace-analytics-updater.service")


def render(source, destination, root, port):
    root = str(Path(root).expanduser().resolve())
    if any(c in root for c in "\n\r\x00"):
        raise ValueError("Installation path contains an invalid character")
    if not str(port).isdigit() or not 1024 <= int(port) <= 65535:
        raise ValueError("Invalid service port")
    # WorkingDirectory is a single path, not a quoted word list. Quotation marks
    # would become literal filename characters and make its leading slash invalid.
    working_directory = root.replace("%", "%%") + "/current"
    # ExecStart, EnvironmentFile and ReadWritePaths parse quoted words instead.
    quoted_root = root.replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"')
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for name in NAMES:
        text = (Path(source) / name).read_text()
        text = text.replace("@WORKING_DIRECTORY@", working_directory).replace("@ROOT@", quoted_root).replace("@PORT@", str(port))
        (destination / name).write_text(text)


if __name__ == "__main__":
    render(*sys.argv[1:])
