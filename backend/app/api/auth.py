from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class AuthCheckResponse(BaseModel):
    ok: bool


@router.get("/auth/check", response_model=AuthCheckResponse)
def auth_check() -> AuthCheckResponse:
    """Lets the Android "Test connection" button confirm the API key works."""
    return AuthCheckResponse(ok=True)
