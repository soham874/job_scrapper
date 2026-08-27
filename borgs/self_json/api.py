import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI

from common.logger import get_logger
from borgs.self_json.cron import run_once, start_cron

logger = get_logger("self_json")


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_cron()
    yield

app = FastAPI(title="Self JSON Borg", lifespan=lifespan)


@app.get("/health")
def health():
    logger.debug("Health check hit")
    return {"status": "ok", "borg": "self_json"}


@app.post("/trigger")
def trigger():
    logger.info("Manual trigger received")
    try:
        results = run_once()
        return {"status": "ok", "jobs_found": len(results)}
    except Exception as e:
        logger.exception("Manual trigger failed")
        return {"status": "error", "message": str(e)}


def main(host="0.0.0.0", port=5005):
    logger.info("Starting Self JSON borg API on %s:%d", host, port)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
