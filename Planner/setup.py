import os
from setuptools import setup

APP = ["app.py"]
OPTIONS = {
    "argv_emulation": True,
    "iconfile": os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "AppIcon.icns"),
    "plist": {
        "CFBundleName": "Academic Strategy",
        "CFBundleDisplayName": "Academic Strategy",
        "CFBundleIdentifier": "com.oznova.academicstrategy",
        "CFBundleVersion": "1.0.0",
        "CFBundleShortVersionString": "1.0.0",
        "NSHighResolutionCapable": True,
    },
    "packages": ["flask", "jinja2", "werkzeug", "markupsafe", "itsdangerous", "click", "blinker"],
}

setup(
    app=APP,
    name="Academic Strategy",
    options={"py2app": OPTIONS},
)
