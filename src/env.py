import os
from typing import Dict, Optional

def load_env(env_path: Optional[str] = None, override: bool = False) -> Dict[str, str]:
    """
    Loads key-value pairs from .env file into os.environ.
    
    Priority Invariant:
    If override is False (default), existing environment variables in os.environ
    (e.g., populated by `gopass env`, shell export, Docker, or CI) take HIGHEST
    precedence and will NOT be overwritten by .env values.

    Supports both KEY=VALUE and KEY: VALUE syntax, comments (#), and stripping quotes.
    """
    if env_path is None:
        # Search upwards for .env starting from cwd
        cur = os.path.abspath(os.getcwd())
        while True:
            candidate = os.path.join(cur, ".env")
            if os.path.isfile(candidate):
                env_path = candidate
                break
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent

    if not env_path or not os.path.isfile(env_path):
        return {}

    loaded = {}
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                elif ":" in line and not line.startswith("---"):
                    k, v = line.split(":", 1)
                else:
                    continue
                k = k.strip()
                v = v.strip().strip("'\"")
                if not k:
                    continue
                loaded[k] = v
                if override or k not in os.environ:
                    os.environ[k] = v
    except Exception:
        pass

    return loaded

# Automatically load .env on module import with override=False (os.environ takes precedence)
load_env(override=False)
