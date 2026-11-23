import os
from decimal import Decimal

from dotenv import load_dotenv

load_dotenv()

JWT_SECRET = os.environ.get("JWT_SECRET", "")
# In Docker without a JWT_SECRET, docker-entrypoint.sh generates one into this file, so that
# `docker compose exec` commands (e.g. the seed script) see the same secret as the API.
_jwt_secret_file = os.environ.get("JWT_SECRET_FILE", "")
if not JWT_SECRET and _jwt_secret_file and os.path.isfile(_jwt_secret_file):
    with open(_jwt_secret_file) as f:
        JWT_SECRET = f.read().strip()
if len(JWT_SECRET) < 32 or JWT_SECRET == "change-me":
    raise RuntimeError("Set JWT_SECRET in .env to a random value of 32+ characters (see .env.example)")

JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = int(os.environ.get("JWT_EXPIRE_MINUTES", "1440"))

# CSV upload limits
MAX_UPLOAD_BYTES = 1_000_000
MAX_IMPORT_ROWS = 5_000

# The one confidence cutoff for LLM category guesses. Below it a guess waits in the review queue
# (app/services/review.py) and verified answers report it as not categorized yet (app/agent/spending.py).
# 0.75 from the Nov 11 categorization evaluation (529 labeled rows): guesses at or above it were 98.8% correct
# (501/507) and 22 rows (4%) went to review; guesses below it were right only 91% of the time (20/22).
# Re-run scripts.eval_categorization before changing it.
REVIEW_CONFIDENCE_THRESHOLD = float(os.environ.get("REVIEW_CONFIDENCE_THRESHOLD", "0.75"))

# Anomaly flags (app/services/insights.py), tuned on the generator's data (the 529-row evaluation set and 8 more seeds).
# A category mixes very different purchases (movies and concert tickets), so comparing with it needs a higher bar:
# at 5x, Ticketmaster concert tickets were flagged against the Entertainment median in 4 of 8 seeds.
ANOMALY_LARGE_MULTIPLE = Decimal(os.environ.get("ANOMALY_LARGE_MULTIPLE", "5"))  # x the merchant's usual charge
ANOMALY_LARGE_CATEGORY_MULTIPLE = Decimal(os.environ.get("ANOMALY_LARGE_CATEGORY_MULTIPLE", "8"))  # x the category's
ANOMALY_UNFAMILIAR_MIN = Decimal(os.environ.get("ANOMALY_UNFAMILIAR_MIN", "75"))  # dollars, first-time merchant

# Agent (POST /questions). A missing LLM key must not stop the app: tests and CI run without one,
# and get_llm() then returns a stub whose every call ends as status `failed`.
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "")
LLM_TOOL_CHOICE = os.environ.get("LLM_TOOL_CHOICE", "required")  # "auto" if the course API rejects "required" (check_llm.py says)
LLM_TEMPERATURE = os.environ.get("LLM_TEMPERATURE", "0")  # empty = don't send it; some models reject temperature
# Cap per LLM call. Nov 11 evaluation: most planning calls take 1-9 s, but about 1 in 10 hangs past 15 s,
# so the cap is a little over the normal range and a timed-out call is retried once if 6 s remain.
LLM_CALL_TIMEOUT_S = float(os.environ.get("LLM_CALL_TIMEOUT_S", "11"))
AGENT_EXPLORATION = os.environ.get("AGENT_EXPLORATION", "false").lower() == "true"
AGENT_MAX_ROUNDS = int(os.environ.get("AGENT_MAX_ROUNDS", "4"))
AGENT_TIME_LIMIT_S = float(os.environ.get("AGENT_TIME_LIMIT_S", "20"))
AGENT_RETRY_MIN_TIME_S = 6.0  # don't start attempt 2 with less time than this left
AGENT_ROW_CAP = 50
AGENT_RATE_LIMIT_PER_MIN = 20  # per user, protects the course key
EXTRA_SENTENCE_MAX_CHARS = 200
EXTRA_SENTENCE_MIN_TIME_S = 3.0  # skip the extra call if less time than this remains
FAILURE_REASON_MAX_CHARS = 500  # matches the column; MySQL strict mode rejects longer values
PROMPT_VERSION = "m3-1"  # bump on any prompt or tool-schema change; stored with each answer

# Categorization pipeline (runs after each CSV import; an LLM failure leaves rows uncategorized, never fails the import)
# Measured on the course LLM (Nov 11): 5 groups ~4 s, 10 groups ~28 s, 30 groups ~44 s, with wide variance.
CATEGORIZE_BATCH_SIZE = int(os.environ.get("CATEGORIZE_BATCH_SIZE", "8"))  # merchant groups per LLM call
CATEGORIZE_PARALLEL = int(os.environ.get("CATEGORIZE_PARALLEL", "4"))  # LLM calls in flight at once
CATEGORIZE_CALL_TIMEOUT_S = float(os.environ.get("CATEGORIZE_CALL_TIMEOUT_S", "45"))
# Prompt injection defense: hold back descriptions that read like instructions, and reject guesses the amount
# contradicts (money out as Income). Only the injection demo turns it off, to show the attack working.
CATEGORIZE_INJECTION_GUARD = os.environ.get("CATEGORIZE_INJECTION_GUARD", "true").lower() == "true"
CATEGORIZE_TIME_LIMIT_S = float(os.environ.get("CATEGORIZE_TIME_LIMIT_S", "75"))  # whole import; the rest stay uncategorized

# Budgets (app/services/budgets.py): a budget is "near" its limit once spending reaches this fraction of it.
BUDGET_NEAR_FRACTION = Decimal(os.environ.get("BUDGET_NEAR_FRACTION", "0.8"))
