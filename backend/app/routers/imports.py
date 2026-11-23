from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app import config
from app.agent.llm import LLM, get_llm
from app.deps import CurrentUser, DbSession, get_owned_account
from app.models import Transaction
from app.schemas import ImportResult
from app.services.categorization import categorize_new_transactions
from app.services.csv_import import CsvImportError, import_hashes, parse_csv
from app.services.insights import safe_refresh_insights
from app.services.merchant import normalize_merchant
from app.services.review import needs_review

router = APIRouter(prefix="/accounts/{account_id}/imports", tags=["imports"])


# POST /accounts/{account_id}/imports
@router.post("", response_model=ImportResult)
async def import_csv(account_id: int, file: UploadFile, user: CurrentUser, db: DbSession,
                     llm: Annotated[LLM, Depends(get_llm)]) -> ImportResult:
    """Upload a `date,description,amount` CSV into one of your accounts.

    All-or-nothing: if any row is invalid, nothing is imported and the errors are returned.
    Rows already imported into this account are skipped. New rows are categorized by your saved
    corrections, then by the LLM; rows it is unsure about stay uncategorized for review. Then your
    recurring payments and flags are updated from all your transactions.
    """
    account = get_owned_account(db, account_id, user)

    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="File must be a .csv")
    data = await file.read(config.MAX_UPLOAD_BYTES + 1)
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE, detail=f"File is larger than {config.MAX_UPLOAD_BYTES // 1000} KB"
        )

    try:
        rows = parse_csv(data)
    except CsvImportError as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"message": e.message, "errors": [{"line": err.line, "message": err.message} for err in e.errors]},
        )

    hashes = import_hashes(rows)
    existing = set(
        db.scalars(
            select(Transaction.import_hash).where(
                Transaction.account_id == account.id, Transaction.import_hash.in_(hashes)
            )
        )
    )
    new = [
        Transaction(
            account_id=account.id,
            date=row.date,
            amount=row.amount,
            raw_description=row.description,
            merchant=normalize_merchant(row.description),
            import_hash=h,
        )
        for row, h in zip(rows, hashes)
        if h not in existing
    ]
    db.add_all(new)
    try:
        db.flush()
    except IntegrityError:  # the same rows were imported concurrently
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail="This file is already being imported; try again")
    ids = [t.id for t in new]
    # In a worker thread: the LLM step blocks for seconds, and this endpoint is async.
    await run_in_threadpool(categorize_new_transactions, db, ids, llm)
    safe_refresh_insights(db, user.id)  # recurring series and flags; an error here never fails the import
    db.commit()

    uncategorized = db.scalar(  # the new rows the review queue now holds
        select(func.count()).select_from(Transaction).where(Transaction.id.in_(ids), needs_review())
    ) if ids else 0
    return ImportResult(total_rows=len(rows), imported=len(new), skipped_duplicates=len(rows) - len(new),
                        categorized=len(new) - uncategorized, needs_review=uncategorized)
