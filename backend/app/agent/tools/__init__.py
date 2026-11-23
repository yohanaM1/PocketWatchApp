"""Tool registry: tool name -> function(db, user_id, args, today) -> JSON-ready dict."""

from app.agent.tools.compare import compare_periods
from app.agent.tools.insights import detect_anomalies, find_recurring
from app.agent.tools.spending import group_by_category
from app.agent.tools.transactions import get_transactions

TOOLS = {"group_by_category": group_by_category, "get_transactions": get_transactions, "compare_periods": compare_periods,
         "find_recurring": find_recurring, "detect_anomalies": detect_anomalies}
# list_plan intent -> the tool whose stored results answer it
LIST_TOOLS = {"recurring_list": "find_recurring", "flag_list": "detect_anomalies"}
