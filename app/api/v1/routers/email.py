from fastapi import APIRouter

router = APIRouter()

@router.get("/")
async def email_health():
    return {"status": "Email service active"}
