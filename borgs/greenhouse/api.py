from flask import Flask, jsonify

from common.logger import get_logger
from borgs.greenhouse.cron import run_once, start_cron

logger = get_logger("greenhouse")

app = Flask(__name__)


@app.route("/health", methods=["GET"])
def health():
    logger.debug("Health check hit")
    return jsonify({"status": "ok", "borg": "greenhouse"}), 200


@app.route("/trigger", methods=["POST"])
def trigger():
    logger.info("Manual trigger received")
    try:
        results = run_once()
        return jsonify({"status": "ok", "jobs_found": len(results)}), 200
    except Exception as e:
        logger.exception("Manual trigger failed")
        return jsonify({"status": "error", "message": str(e)}), 500


def main(host="0.0.0.0", port=5002):
    logger.info("Starting Greenhouse borg API on %s:%d", host, port)
    start_cron()
    app.run(host=host, port=port)


if __name__ == "__main__":
    main()
