from fastapi import APIRouter, status
from sqlalchemy import select

from app.deps import CurrentUser, DbSession
from app.models import Account
from app.schemas import AccountCreate, AccountOut

router = APIRouter(prefix="/accounts", tags=["accounts"])


# GET /accounts
@router.get("", response_model=list[AccountOut])
def list_accounts(user: CurrentUser, db: DbSession) -> list[Account]:
    """List the logged-in user's accounts."""
    return list(db.scalars(select(Account).where(Account.user_id == user.id).order_by(Account.id)))


# POST /accounts
@router.post("", response_model=AccountOut, status_code=status.HTTP_201_CREATED)
def create_account(body: AccountCreate, user: CurrentUser, db: DbSession) -> Account:
    """Add an account (checking, savings, or credit) for the logged-in user."""
    account = Account(user_id=user.id, name=body.name, type=body.type)
    db.add(account)
    db.commit()
    return account
