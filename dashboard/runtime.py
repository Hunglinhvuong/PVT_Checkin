"""Load scalar application settings supplied through Streamlit Community Cloud Secrets."""
from __future__ import annotations

import os
from collections.abc import Mapping


def apply_streamlit_secrets(secrets: Mapping) -> None:
    for name, value in secrets.items():
        if isinstance(value, (str, int, float, bool)):
            name = str(name)
            if not os.getenv(name, "").strip():
                os.environ[name] = str(value)
