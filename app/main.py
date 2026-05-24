import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .database import Base, engine
from .routers import categories, jobs, movies

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_STATIC = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Creating database tables if needed...")
    Base.metadata.create_all(bind=engine)
    logger.info("Startup complete.")
    yield


app = FastAPI(title="Media Manager", lifespan=lifespan)

app.include_router(categories.router, prefix="/api")
app.include_router(jobs.router, prefix="/api")
app.include_router(movies.router, prefix="/api")

app.mount("/static", StaticFiles(directory=_STATIC), name="static")


@app.get("/")
async def root():
    return FileResponse(os.path.join(_STATIC, "index.html"))


@app.get("/{full_path:path}")
async def spa_fallback(full_path: str):
    # API routes are matched first; anything else serves the SPA shell
    return FileResponse(os.path.join(_STATIC, "index.html"))
