"""Fixed replies (no facts, so nothing to verify) and the fixed failure text. All digit-free on purpose."""

FIXED_REPLIES = {
    "greeting": "Hi! Ask me about your spending, for example: how much did I spend on dining last month?",
    "capabilities": "I can tell you how much you spent in a category over a month or date range, "
                    "and how many purchases you made. I can also list your recurring payments, and show "
                    "transactions flagged as unusual or possible duplicates.",
    "out_of_scope": "I can only answer questions about your own spending in PocketWatch.",
    # used when code (extract_facts, PeriodError) or the LLM decides M2 can't answer
    "unsupported": "I can't answer that kind of question yet. I can tell you how much you spent in a category "
                   "over a month or date range, and how many purchases you made. I can also list your "
                   "recurring payments, and show transactions flagged as unusual or possible duplicates.",
    "future_period": "That period hasn't happened yet, so I don't have any transactions for it.",
    "range_too_long": "I can answer for up to one year at a time. Try a shorter period.",
}
FAILED_REPLY = "Sorry, I couldn't answer that right now. Please try again or rephrase."
