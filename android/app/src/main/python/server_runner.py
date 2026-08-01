"""AllPrice Android — synchronous startup for Chaquopy."""
import os, sys, threading, traceback, time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

_STATUS_FILE = None


def _log(msg):
    if _STATUS_FILE:
        try:
            with open(_STATUS_FILE, "w") as f:
                f.write(f"{int(time.time())}|{msg}")
        except Exception:
            pass


def start_server(api_key, host, port, log_dir):
    """Synchronous: does all imports, writes progress, returns when done."""
    global _STATUS_FILE
    _STATUS_FILE = os.path.join(log_dir, "allprice_status.txt")

    try:
        _log("init")
        # 数据目录（可写）：价格快照 / 缓存
        data_dir = os.path.join(log_dir, "data")
        os.makedirs(data_dir, exist_ok=True)
        os.environ["ALLPRICE_DB_DIR"] = data_dir
        # 前端静态文件目录（打包进 APK 的 web/）
        os.environ["ALLPRICE_FRONTEND_DIR"] = os.path.join(_HERE, "web")
        # DeepSeek API key（Android 设置页传入；为空则 AI 降级为规则推荐）
        if api_key:
            os.environ["DEEPSEEK_API_KEY"] = api_key

        _log("import_fastapi")
        import fastapi
        _log("import_uvicorn")
        import uvicorn

        _log("import_app")
        from app.main import app
        _log("app_ok")

        def serve():
            try:
                config = uvicorn.Config(app, host=host, port=port, log_level="error")
                srv = uvicorn.Server(config)
                _log("server_ready")
                import asyncio
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(srv.serve())
            except Exception:
                _log(f"serve_error_{traceback.format_exc()}")

        t = threading.Thread(target=serve, daemon=True)
        t.start()
        time.sleep(1)
        return {"status": "done"}

    except Exception:
        _log(f"error_{traceback.format_exc()}")
        return {"status": "error"}
