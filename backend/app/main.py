from fastapi import FastAPI

from app.agent import router as questions
from app.routers import accounts, auth, budgets, corrections, imports, insights, review, transactions

app = FastAPI(title="PocketWatch API")
app.include_router(auth.router)
app.include_router(accounts.router)
app.include_router(imports.router)
app.include_router(transactions.router)
app.include_router(review.router)
app.include_router(corrections.router)
app.include_router(insights.router)
app.include_router(budgets.router)
app.include_router(questions.router)


# Liveness check
@app.get("/health")
def health() -> dict[str, str]:
    """Health check: returns ok if the API is running."""
    return {"status": "ok"}
