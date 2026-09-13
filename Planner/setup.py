import os
import re
from setuptools import setup

HERE = os.path.dirname(os.path.abspath(__file__))


def _read_version():
    with open(os.path.join(HERE, "version.py"), encoding="utf-8") as fh:
        match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', fh.read(), re.M)
    if not match:
        raise RuntimeError("could not read __version__ from version.py")
    return match.group(1)


VERSION = _read_version()

APP = ["app.py"]
OPTIONS = {
    "argv_emulation": True,
    "iconfile": os.path.join(HERE, "static", "AppIcon.icns"),
    "plist": {
        "CFBundleName": "Academic Strategy",
        "CFBundleDisplayName": "Academic Strategy",
        "CFBundleIdentifier": "com.oznova.academicstrategy",
        "CFBundleVersion": VERSION,
        "CFBundleShortVersionString": VERSION,
        "NSHighResolutionCapable": True,
    },
    "packages": ["flask", "jinja2", "werkzeug", "markupsafe", "itsdangerous", "click", "blinker"],
}

setup(
    app=APP,
    name="Academic Strategy",
    version=VERSION,
    options={"py2app": OPTIONS},
)
