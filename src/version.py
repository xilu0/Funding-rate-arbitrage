import sys
import os
import shutil
import platform

__version__ = "1.2.0"
__app_name__ = "Capital Funding Rate Arbitrage"

def get_diagnostics() -> dict:
    """Returns diagnostic information about runtime environment, dependencies, and tools."""
    # Check dependencies
    deps = {}
    for mod in ["rich", "hyperliquid", "eth_account", "web3", "requests", "urllib3", "websocket"]:
        try:
            m = __import__(mod)
            v = getattr(m, "__version__", "available")
            deps[mod] = {"status": "available", "version": str(v)}
        except ImportError:
            deps[mod] = {"status": "missing", "version": None}

    # Check gopass
    gopass_path = shutil.which("gopass")
    gopass_version = None
    if gopass_path:
        import subprocess
        try:
            res = subprocess.run(["gopass", "version"], capture_output=True, text=True, timeout=3)
            gopass_version = res.stdout.strip().splitlines()[0] if res.stdout else "installed"
        except Exception:
            gopass_version = "installed"

    # Virtual environment
    in_venv = (sys.prefix != sys.base_prefix) or ("VIRTUAL_ENV" in os.environ)

    return {
        "app_name": __app_name__,
        "version": __version__,
        "python_version": platform.python_version(),
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "in_virtualenv": in_venv,
        "virtualenv_path": os.environ.get("VIRTUAL_ENV", sys.prefix if in_venv else None),
        "gopass": {
            "available": bool(gopass_path),
            "path": gopass_path,
            "version": gopass_version
        },
        "dependencies": deps
    }

def format_version_text() -> str:
    info = get_diagnostics()
    g = info["gopass"]
    lines = [
        f"🚀 {info['app_name']} v{info['version']}",
        f"  • Python: {info['python_version']} ({info['python_executable']})",
        f"  • Platform: {info['platform']}",
        f"  • Virtualenv: {'Active (' + str(info['virtualenv_path']) + ')' if info['in_virtualenv'] else 'System Environment (None)'}",
        f"  • Gopass CLI: {'Installed (' + str(g['version']) + ')' if g['available'] else 'Not Found'}",
        "  • Dependencies:",
    ]
    for dep, data in info["dependencies"].items():
        if data["status"] == "available":
            ver = f" (v{data['version']})" if data['version'] and data['version'] != "available" else ""
            lines.append(f"    - {dep}: [OK]{ver}")
        else:
            lines.append(f"    - {dep}: [MISSING]")
    return "\n".join(lines)
