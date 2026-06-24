import uvicorn
from fastapi import FastAPI, Depends, HTTPException, APIRouter, status, Request
from pydantic import BaseModel
# from agent.graph import run
from agent.new_graph import run

from fastapi.middleware.cors import CORSMiddleware
from redis_client import cache_pr_review, store_refresh_token
from fastapi.responses import JSONResponse
from features.signup import SignupValidator
from fastapi.security import HTTPBearer
from database.database import get_db
from sqlalchemy.orm import Session
from auth.login import LoginValidator
from auth.tokens import create_access_token, create_refresh_token
from jose import JWTError, jwt
from auth.auth_config import SECRET_KEY, ALGORITHM, REFRESH_TOKEN_EXPIRE_DAYS, ACCESS_TOKEN_EXPIRE_MINUTES
from database.models import User
from auth.tokens import validate_refresh_token, delete_refresh_token
from datetime import timedelta

class PublicRepo(BaseModel):
    pr_url: str

class RefreshRequest(BaseModel):
    refresh_token: str

class LogoutRequest(BaseModel):
    refresh_token: str

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5500",
        "http://0.0.0.0:5500",
        "http://localhost:5500",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://0.0.0.0:8000",
        "https://ameerhamzamalik19.github.io",
        "https://ameerhamzamalik19.github.io/Code-Reviewer-Frontend/html"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],  # Expose all headers to the client
)

security = HTTPBearer()     # Getting refresh token from the Authorization header

def get_current_user(
    request: Request,
    token: str = Depends(security),
    db: Session = Depends(get_db)
):
    try:
        payload = jwt.decode(
            token.credentials,  # token is a HTTPAuthorizationCredentials object
            SECRET_KEY,
            algorithms=[ALGORITHM]
        )

        username = payload.get("sub")

        if username is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_access_token")

    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_access_token")

    user = (
        db.query(User)
        .filter(User.user_name == username)
        .first()
    )

    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_access_token")

    request.state.user = user       # Store the user in the request state for later reuse

    return user

auth_router = APIRouter(
    prefix="/api",
    dependencies=[Depends(get_current_user)]
)

@auth_router.post("/logout")
def logout(request: LogoutRequest, db: Session = Depends(get_db)):
    """
    Logout: delete the refresh token from Redis.
    (The client should also discard the access token on its side.)
    """

    refresh_token = request.refresh_token
    try:
        payload = jwt.decode(refresh_token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if username:
            delete_refresh_token(username)
    except JWTError:
        pass  # but we don't have username to delete from Redis; just clear cookie

    response = JSONResponse(content={"message": "Logged out"})

    return response

@app.get("/")
def health():
    return {"message": "Hello World"}

@app.post("/refresh")
def refresh_access_token(
    request: RefreshRequest,
    db: Session = Depends(get_db)
):
    """
    Refresh the access token using the refresh token from the cookie.
    Rotates the refresh token: old one is invalidated, new one is issued.
    """

    try:
        refresh_token = request.refresh_token


        # 2. Validate it (signature, expiry, Redis match, user exists)
        user = validate_refresh_token(refresh_token, db)

        # 3. Rotate: generate new tokens
        new_access_token = create_access_token(data={"sub": user.user_name})
        new_refresh_token = create_refresh_token(data={"sub": user.user_name}, expires_delta=timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS))

        # 4. Store the new refresh token in Redis (overwrites old one)
        store_refresh_token(user.user_name, new_refresh_token)

        # 5. Build response with new access token (body) and new refresh token (cookie)
        response = JSONResponse(content={
            "access_token": new_access_token,
            "token_type": "bearer",
            "refresh_token": new_refresh_token
        })

        return response
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(ve))

@app.post("/signup")
def signup(signup_data: SignupValidator, db: Session = Depends(get_db)):
    try:
        signup_data.signup_user(db=db)  # Pass the actual database session here
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    return {"message": f"Signup endpoint - to be implemented for user: {signup_data}"}

@app.post("/login")
def login(login_data: LoginValidator, db: Session = Depends(get_db)):

    try:
        user = login_data.validate_login(db=db)  # This will raise ValueError if invalid
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password"
        )
    
    # 2. Generate tokens
    access_token = create_access_token(data={"sub": user.user_name})
    refresh_token = create_refresh_token(data={"sub": user.user_name}, expires_delta=timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS))
    
    # 3. Store refresh token in Redis (for revocation/logout)
    store_refresh_token(user.user_name, refresh_token)
    
    # 4. Create the JSON response body (contains only the access token)
    response = JSONResponse(content={
        "access_token": access_token,
        "token_type": "bearer",
        "refresh_token": refresh_token  # Include refresh token in response body for client-side storage (optional)
    })
    
    return response


@auth_router.post("/pr")
def get_results(pr_url: PublicRepo, request: Request, db: Session = Depends(get_db)):

    print("Executing /pr endpoint...")

    user = request.state.user

    print(f"The user: {user.user_name} has user_id: {user.user_id}")

    pull_repo = pr_url.pr_url

    response = run(db, pull_repo, user)
    cache_pr_review(pull_repo, response)

    return response

app.include_router(auth_router)

if __name__ == "__main__":

    # PRODUCTION ENVIRONMENT
    uvicorn.run("api:app", host='0.0.0.0', port=8000)

    #   TESTING ENVIRONMENT
    # uvicorn.run("api:app", host='127.0.0.1', port=8000, reload=True)
    # uvicorn.run("api:app", host='localhost', port=8000, reload=True)