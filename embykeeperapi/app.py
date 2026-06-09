import os
from ipaddress import ip_address, ip_network
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

from .automation_runtime import automation_runtime
from .scheduler_bridge import bridge

logger = logger.bind(scheme="embykeeperapi")
TRUST_PROXY_HEADERS_ENV = "EK_TRUST_PROXY"
TRUSTED_PROXIES_ENV = "EK_TRUSTED_PROXIES"
DEFAULT_TRUSTED_PROXY_NETWORKS = (
    ip_network("127.0.0.0/8"),
    ip_network("::1/128"),
)


class ProxyFixMiddleware(BaseHTTPMiddleware):
    """Fix proxy headers for reverse proxy deployments."""

    @staticmethod
    def _env_flag_enabled(name: str) -> bool:
        value = os.environ.get(name)
        if not isinstance(value, str):
            return False
        return value.strip().casefold() in {"1", "true", "yes", "on", "all", "*"}

    @staticmethod
    def _trusted_proxy_networks():
        raw = os.environ.get(TRUSTED_PROXIES_ENV)
        networks = list(DEFAULT_TRUSTED_PROXY_NETWORKS)
        if not isinstance(raw, str) or not raw.strip():
            return networks
        for item in raw.split(","):
            item = item.strip()
            if not item:
                continue
            try:
                networks.append(ip_network(item, strict=False))
            except ValueError:
                logger.warning(f"Ignoring invalid trusted proxy network: {item}")
        return networks

    @classmethod
    def _is_trusted_proxy(cls, host: str) -> bool:
        if cls._env_flag_enabled(TRUST_PROXY_HEADERS_ENV):
            return True
        try:
            client_ip = ip_address(host)
        except ValueError:
            return False
        return any(client_ip in network for network in cls._trusted_proxy_networks())

    @staticmethod
    def _first_forwarded_value(value: str):
        for item in value.split(","):
            item = item.strip()
            if item and item.lower() != "unknown":
                return item
        return None

    async def dispatch(self, request: Request, call_next):
        client_host = request.client.host if request.client else ""
        if not self._is_trusted_proxy(client_host):
            return await call_next(request)

        # Handle X-Forwarded-Proto for scheme
        forwarded_proto = request.headers.get("X-Forwarded-Proto")
        if forwarded_proto:
            for proto in forwarded_proto.split(","):
                proto = proto.strip().lower()
                if proto in {"http", "https"}:
                    request.scope["scheme"] = proto
                    break

        # Handle X-Forwarded-For / X-Real-Ip for client IP
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            forwarded_host = self._first_forwarded_value(forwarded_for)
            if forwarded_host:
                request.scope["client"] = (forwarded_host, 0)
        else:
            real_ip = (request.headers.get("X-Real-Ip") or "").strip()
            if real_ip and real_ip.lower() != "unknown":
                request.scope["client"] = (real_ip, 0)

        response = await call_next(request)
        return response


def _normalize_root_path(prefix: str) -> str:
    prefix = (prefix or "").strip()
    if not prefix or prefix == "/":
        return ""
    return "/" + prefix.strip("/")


def _get_env_text(name: str):
    value = os.environ.get(name)
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _is_reserved_spa_path(path: str) -> bool:
    return path == "api" or path.startswith("api/") or path == "healthz"


async def _shutdown_bridge(reason: str):
    try:
        await bridge.shutdown()
    except Exception as e:
        logger.warning(f"Failed to shutdown scheduler bridge during {reason}: {e}")


async def _shutdown_registrar_tasks(reason: str):
    try:
        from .routers.registrar import shutdown_registrar_tasks

        await shutdown_registrar_tasks()
    except Exception as e:
        logger.warning(f"Failed to shutdown registrar tasks during {reason}: {type(e).__name__}")


async def _start_notifier(reason: str):
    try:
        from embykeeper.notify import start_notifier

        await start_notifier()
    except Exception as e:
        logger.warning(f"Failed to start notifier during {reason}: {type(e).__name__}")


async def _stop_notifier(reason: str):
    try:
        from embykeeper.notify import _stop_notifier

        await _stop_notifier(unregister_callback=True)
    except Exception as e:
        logger.warning(f"Failed to stop notifier during {reason}: {type(e).__name__}")


async def _start_automation_runtime(reason: str):
    try:
        await automation_runtime.start()
    except Exception as e:
        logger.warning(f"Failed to start Telegram automation runtime during {reason}: {type(e).__name__}")


async def _shutdown_automation_runtime(reason: str):
    try:
        await automation_runtime.shutdown()
    except Exception as e:
        logger.warning(f"Failed to shutdown Telegram automation runtime during {reason}: {type(e).__name__}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize scheduler bridge on startup, cleanup on shutdown."""
    # Determine base directory
    basedir_env = _get_env_text("EK_BASEDIR")
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
        await _shutdown_bridge("startup cleanup")
        # Continue anyway - the API can work without the scheduler
    await _start_automation_runtime("application startup")
    await _start_notifier("application startup")

    yield

    # Shutdown
    logger.info("Shutting down scheduler bridge...")
    await _shutdown_registrar_tasks("application shutdown")
    await _shutdown_automation_runtime("application shutdown")
    await _shutdown_bridge("application shutdown")
    await _stop_notifier("application shutdown")
    logger.info("Shutdown complete.")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    prefix = _normalize_root_path(os.environ.get("EK_BASE_PREFIX", ""))

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
    from .routers.registrar import router as registrar_router

    app.include_router(auth_router)
    app.include_router(servers_router)
    app.include_router(scheduler_router)
    app.include_router(config_router)
    app.include_router(registrar_router)

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
            if _is_reserved_spa_path(path):
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
    basedir = basedir.strip() if isinstance(basedir, str) else basedir
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
