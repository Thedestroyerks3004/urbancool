"""
FastAPI application entrypoint.

Run with: uvicorn app.main:app --reload --port 8000
Auto-generated interactive docs at /docs once running.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.state import initialize_app_state

app = FastAPI(
    title="Urban Cool -- Heat Vulnerability API",
    description=(
        "Serves a native-10m, structural/vegetation-based Heat Vulnerability Index for the "
        "Anna University - OMR-ECR corridor, Chennai. LST-excluded by design; every response "
        "carries a caveats field disclosing this."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.on_event("startup")
def on_startup():
    print("[main] Loading feature stack and trained model into memory ...", flush=True)
    initialize_app_state()
    print("[main] Ready.", flush=True)
