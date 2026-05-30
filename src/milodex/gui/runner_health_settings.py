"""Durable persistence for the GUI reap-interval setting (QSettings).

Uses an explicit ``(format, scope, org, app)`` ``QSettings`` so it does not
depend on ``QCoreApplication.setOrganizationName`` having run first — there is
no ordering hazard against an unconfigured application object.
"""

from __future__ import annotations

from PySide6.QtCore import QSettings

_ORG = "Milodex"
_APP = "Milodex"
_KEY = "runner_health/reap_interval_seconds"
_DEFAULT = 60
_FORMAT = QSettings.NativeFormat  # overridden to IniFormat in tests


def _settings() -> QSettings:
    return QSettings(_FORMAT, QSettings.UserScope, _ORG, _APP)


def read_reap_interval_seconds() -> int:
    raw = _settings().value(_KEY, _DEFAULT)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return _DEFAULT


def write_reap_interval_seconds(seconds: int) -> None:
    _settings().setValue(_KEY, int(seconds))
