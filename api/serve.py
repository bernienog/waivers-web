"""Entry-point del sidecar empaquetado (PyInstaller/Tauri).

Sin CLI: lee WAIVERS_PORT (default 3001) y sirve api.main:app en
127.0.0.1. WAIVERS_DATA_DIR / WAIVERS_ENV_FILE los resuelven core/db.py
y api/main.py. Uso dev: .venv\\Scripts\\python api/serve.py
"""

import os


def main():
    from api.main import app

    import uvicorn

    port = int(os.environ.get("WAIVERS_PORT", "3001"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
