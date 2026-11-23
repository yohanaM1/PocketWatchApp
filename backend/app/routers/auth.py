from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.deps import CurrentUser, DbSession
from app.models import Account, AccountType, User
from app.schemas import RegisterRequest, Token, UserOut
from app.security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


# POST /auth/register
@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest, db: DbSession) -> User:
    """Create an account with an email and password, plus a default Checking account. 409 if the email is taken."""
    email = body.email.lower()
    if db.scalar(select(User.id).where(User.email == email)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    user = User(email=email, password_hash=hash_password(body.password))
    # A default account so the user can import a CSV right away.
    user.accounts.append(Account(name="Checking", type=AccountType.CHECKING))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:  # two registrations for the same email at once
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")
    return user


# POST /auth/login
@router.post("/login", response_model=Token)
def login(form: Annotated[OAuth2PasswordRequestForm, Depends()], db: DbSession) -> Token:
    """Log in with email and password and get an access token. OAuth2 form fields: `username` is the email."""
    user = db.scalar(select(User).where(User.email == form.username.strip().lower()))
    if not verify_password(form.password, user.password_hash if user else None):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return Token(access_token=create_access_token(user.id))


# GET /auth/me
@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> User:
    """Return the logged-in user."""
    return user
