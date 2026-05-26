import os
from contextlib import asynccontextmanager
from pathlib import Path

import typer
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .scheduler_bridge import bridge

logger = logger.bind(scheme="embykeeperapi")


class ProxyFixMiddleware(BaseHTTPMiddleware):
    """Fix proxy headers for reverse proxy deployments."""

    async def dispatch(self, request: Request, call_next):
        # Handle X-Forwarded-Proto for scheme
        forwarded_proto = request.headers.get("X-Forwarded-Proto")
        if forwarded_proto in {"http", "https"}:
            request.scope["scheme"] = forwarded_proto

        # Handle X-Forwarded-For / X-Real-Ip for client IP
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            request.scope["client"] = (forwarded_for.split(",")[0].strip(), 0)

        response = await call_next(request)
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize scheduler bridge on startup, cleanup on shutdown."""
    # Determine base directory
    basedir_env = os.environ.get("EK_BASEDIR")
    if basedir_env:
        basedir = Path(basedir_env)
    else:
        from appdirs import user_data_dir
        from embykeeper import __name__ as __product__
        basedir = Path(user_data_dir(__product__))

    basedir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Working directory: {basedir}")

    from .auth import init_jwt_secret_from_basedir
    init_jwt_secret_from_basedir(basedir)

    # Initialize scheduler bridge
    try:
        await bridge.initialize(basedir)
    except Exception as e:
        logger.error(f"Failed to initialize scheduler bridge: {e}")
        # Continue anyway - the API can work without the scheduler

    yield

    # Shutdown
    logger.info("Shutting down scheduler bridge...")
    await bridge.shutdown()
    logger.info("Shutdown complete.")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    prefix = os.environ.get("EK_BASE_PREFIX", "")

    app = FastAPI(
        title="EmbyKeeper API",
        description="Web management platform for Emby server keep-alive",
        version="0.1.0",
        root_path=prefix,
        lifespan=lifespan,
    )

    # Middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(ProxyFixMiddleware)

    # Register routers
    from .routers.auth_router import router as auth_router
    from .routers.servers import router as servers_router
    from .routers.scheduler import router as scheduler_router
    from .routers.config import router as config_router

    app.include_router(auth_router)
    app.include_router(servers_router)
    app.include_router(scheduler_router)
    app.include_router(config_router)

    # Serve SPA frontend
    static_dir = Path(__file__).parent / "static"
    if static_dir.is_dir():
        from starlette.responses import FileResponse, JSONResponse

        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

        @app.get("/")
        async def serve_root():
            return FileResponse(str(static_dir / "index.html"))

        @app.get("/{path:path}")
        async def serve_spa(path: str):
            if path.startswith("api/") or path == "healthz":
                return JSONResponse({"detail": "Not Found"}, status_code=404)
            return FileResponse(str(static_dir / "index.html"))

    return app


app = create_app()

cli = typer.Typer(pretty_exceptions_enable=False)


@cli.command()
def run(
    port: int = typer.Option(1818, envvar="PORT", help="Server port"),
    host: str = typer.Option("0.0.0.0", help="Server host"),
    basedir: str = typer.Option(None, "--basedir", "-B", envvar="EK_BASEDIR", help="Base directory"),
    debug: bool = typer.Option(False, "--debug", "-d", help="Enable debug mode"),
):
    """Start the EmbyKeeper API server."""
    if basedir:
        os.environ["EK_BASEDIR"] = basedir

    log_level = "debug" if debug else "info"

    logger.info(f"Starting EmbyKeeper API on {host}:{port}")

    uvicorn.run(
        "embykeeperapi.app:app",
        host=host,
        port=port,
        log_level=log_level,
        reload=debug,
    )
