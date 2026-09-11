from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class _CampReg(BaseModel):
    class Config:
        extra = "ignore"


class CampRegParticipant(_CampReg):
    full_name: str = ""
    date_of_birth: str = ""
    age: str = ""
    gender: str = ""
    parent_guardian_name: str = ""
    parent_guardian_mobile: str = ""
    alternate_contact: str = ""
    email: str = ""
    address: str = ""


class CampRegTraining(_CampReg):
    shaolin_beginner: str = ""
    trained_before: str = ""
    trained_details: str = ""
    current_sports: str = ""
    fitness_level: str = ""


class CampRegMedical(_CampReg):
    injury: str = ""
    injury_details: str = ""
    medical_condition: str = ""
    medical_condition_details: str = ""
    allergies: str = ""
    allergies_details: str = ""
    medication: str = ""
    medication_details: str = ""
    blood_group: str = ""


class CampRegFood(_CampReg):
    preference: str = ""
    dietary_restriction: str = ""
    dietary_details: str = ""


class CampRegEmergency(_CampReg):
    name: str = ""
    relationship: str = ""
    phone: str = ""
    alternate_phone: str = ""


class CampRegResidential(_CampReg):
    attended_before: str = ""
    special_requirements: str = ""
    special_requirements_details: str = ""


class CampRegPayment(_CampReg):
    payment_mode: str = ""
    other_mode: str = ""
    transaction_id: str = ""
    screenshot_url: str = ""
    agree_policy: bool = False


class CampRegRules(_CampReg):
    agree_rules: bool = False
    agree_instructions: bool = False
    agree_discipline: bool = False
    agree_property: bool = False
    agree_non_refundable: bool = False


class CampRegPhoto(_CampReg):
    consent: bool = False


class CampRegParentConsent(_CampReg):
    agreed: bool = False
    name: str = ""
    relationship: str = ""
    signature: str = ""
    date: str = ""


class CampRegHearAbout(_CampReg):
    sources: List[str] = Field(default_factory=list)
    other_source: str = ""
    referred_by: str = ""


class CampRegFinal(_CampReg):
    agreed: bool = False
    participant_name: str = ""
    parent_guardian_name: str = ""
    signature: str = ""
    registration_date: str = ""


class CampRegistrationEventSnapshot(_CampReg):
    event_id: str = ""
    event_name: str = ""
    event_dates: str = ""
    event_location: str = ""
    fee_total: str = ""
    fee_pay_now: str = ""
    fee_balance: str = ""
    refund_text: str = ""


class CampRegistrationCreate(_CampReg):
    participant: CampRegParticipant
    training: CampRegTraining
    medical: CampRegMedical
    food: CampRegFood
    emergency: CampRegEmergency
    residential: CampRegResidential
    payment: CampRegPayment
    rules: CampRegRules
    photo: CampRegPhoto
    parent_consent: CampRegParentConsent
    hear_about: CampRegHearAbout
    final: CampRegFinal


class CampRegistrationSummary(_CampReg):
    id: str
    status: str = "received"
    event_id: str = ""
    event_name: str = ""
    event_dates: str = ""
    event_location: str = ""
    full_name: str = ""
    parent_guardian_mobile: str = ""
    email: str = ""
    payment_mode: str = ""
    transaction_id: str = ""
    created_at: Optional[datetime] = None


class CampRegistrationResponse(CampRegistrationCreate):
    id: str
    status: str = "received"
    event: CampRegistrationEventSnapshot = Field(default_factory=CampRegistrationEventSnapshot)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class CampRegVerifyPayment(_CampReg):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str
