"""
Email related API endpoints
"""
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi import status as http_status
from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List, Dict, Any
from utils.email_service import send_email
from models.user_models import UserRole
from utils.unified_auth import require_role_unified
import httpx
import logging

router = APIRouter(tags=["Email"])

_EMAIL_ADMIN_ROLES = [
    UserRole.SUPER_ADMIN,
    UserRole.PARTNER_SUPER_ADMIN,
    UserRole.BRANCH_MANAGER,
    UserRole.COACH_ADMIN,
]

# Configure logging
logger = logging.getLogger(__name__)

class EmailRequest(BaseModel):
    """Request model for sending emails"""
    to_email: EmailStr = Field(..., description="Recipient email address")
    subject: str = Field(..., min_length=1, max_length=200, description="Email subject")
    message: str = Field(..., min_length=1, description="Email message body")
    html_message: Optional[str] = Field(None, description="Optional HTML formatted message")

class WebhookEmailRequest(BaseModel):
    """Request model for webhook-based email sending"""
    to_email: EmailStr = Field(..., description="Recipient email address")
    subject: str = Field(..., min_length=1, description="Email subject")
    message: str = Field(..., min_length=1, description="Email message body")
    html_message: str = Field(..., min_length=1, description="HTML formatted message")

class EmailEnvelope(BaseModel):
    """Email envelope information"""
    from_: str = Field(..., alias="from", description="Sender email address")
    to: List[str] = Field(..., description="Recipient email addresses")

class WebhookEmailResponse(BaseModel):
    """Response model for webhook email sending"""
    accepted: List[str] = Field(..., description="Accepted email addresses")
    rejected: List[str] = Field(..., description="Rejected email addresses")
    ehlo: List[str] = Field(..., description="EHLO response from SMTP server")
    envelopeTime: int = Field(..., description="Time taken for envelope processing in ms")
    messageTime: int = Field(..., description="Time taken for message processing in ms")
    messageSize: int = Field(..., description="Message size in bytes")
    response: str = Field(..., description="SMTP server response")
    envelope: EmailEnvelope = Field(..., description="Email envelope information")
    messageId: str = Field(..., description="Unique message identifier")

@router.post("/send-email", 
            status_code=http_status.HTTP_200_OK,
            summary="Send an email",
            response_description="Email sent successfully")
async def send_email_endpoint(
    email_data: EmailRequest,
    current_user: dict = Depends(require_role_unified(_EMAIL_ADMIN_ROLES)),
):
    """
    Send an email using the configured SMTP/Resend path (staff only).
    """
    _ = current_user
    try:
        await send_email(
            to_email=email_data.to_email,
            subject=email_data.subject,
            body=email_data.message,
            html_body=email_data.html_message
        )
        return {"status": "success", "message": "Email sent successfully"}
    except Exception as e:
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to send email: {str(e)}"
        )

@router.post("/send-webhook-email",
            status_code=http_status.HTTP_200_OK,
            summary="Send email via webhook",
            response_description="Email sent successfully via webhook",
            response_model=List[WebhookEmailResponse])
async def send_webhook_email(
    email_data: WebhookEmailRequest,
    current_user: dict = Depends(require_role_unified(_EMAIL_ADMIN_ROLES)),
):
    """
    Send an email using the external webhook service (staff only).
    """
    import os
    _ = current_user

    webhook_url = (os.getenv("EMAIL_WEBHOOK_URL") or "").strip()
    if not webhook_url or webhook_url.lower() in {"0", "false", "off", "disabled", "none"}:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Email webhook is not configured. Set EMAIL_WEBHOOK_URL or use Resend.",
        )

    try:
        logger.info(f"📧 Sending email via webhook to {email_data.to_email}")
        logger.info(f"📍 Webhook URL: {webhook_url}")
        logger.info(f"📋 Subject: {email_data.subject}")

        # Prepare payload for webhook
        webhook_payload = {
            "to_email": email_data.to_email,
            "subject": email_data.subject,
            "message": email_data.message,
            "html_message": email_data.html_message
        }

        # Send request to webhook
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                webhook_url,
                json=webhook_payload,
                headers={"Content-Type": "application/json"}
            )

            logger.info(f"📨 Webhook response status: {response.status_code}")

            if response.status_code != 200:
                logger.error(f"❌ Webhook failed with status {response.status_code}: {response.text}")
                raise HTTPException(
                    status_code=http_status.HTTP_502_BAD_GATEWAY,
                    detail=f"Webhook service failed: {response.status_code} - {response.text}"
                )

            # Parse webhook response
            webhook_response = response.json()
            logger.info(f"✅ Email sent successfully via webhook")
            logger.info(f"📊 Response: {webhook_response}")

            return webhook_response

    except httpx.TimeoutException:
        logger.error("❌ Webhook request timed out")
        raise HTTPException(
            status_code=http_status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Webhook service timed out"
        )
    except httpx.RequestError as e:
        logger.error(f"❌ Webhook request failed: {str(e)}")
        raise HTTPException(
            status_code=http_status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to connect to webhook service: {str(e)}"
        )
    except Exception as e:
        logger.error(f"❌ Unexpected error: {str(e)}")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to send email via webhook: {str(e)}"
        )
