from datetime import datetime
from typing import List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


def new_camp_event_id() -> str:
    return uuid4().hex


class _CampModel(BaseModel):
    class Config:
        extra = "ignore"


class CampLabelValue(_CampModel):
    label: str = ""
    value: str = ""


class CampIconCard(_CampModel):
    icon: str = ""
    icon_image: Optional[str] = None
    title: str = ""
    text: str = ""
    enabled: bool = True


class CampTrainingCard(_CampModel):
    title: str = ""
    bullets: List[str] = Field(default_factory=list)
    enabled: bool = True


class CampTimelineItem(_CampModel):
    time_label: str = ""
    title: str = ""
    text: str = ""


class ResidentialCampNav(_CampModel):
    logo: Optional[str] = None
    brand_prefix: str = "ROCK"
    brand_accent: str = "MARTIAL ARTS ACADEMY"
    link_camp: str = "Camp"
    link_training: str = "Training"
    link_schedule: str = "Schedule"
    link_register: str = "Register"
    mobile_register_label: str = "Register"


class ResidentialCampHero(_CampModel):
    hero_image: Optional[str] = None
    eyebrow: str = "5-Day Residential Training"
    h1_line1: str = "Shaolin"
    h1_line2: str = "Kungfu"
    h2: str = "Residential Camp"
    paragraph: str = (
        "Train. Discipline. Transform. Step away from your daily routine and immerse "
        "yourself in intensive Shaolin Kungfu training designed to build strength, "
        "confidence, focus and a warrior mindset."
    )
    cta_primary_label: str = "Register Now →"
    cta_whatsapp_label: str = "WhatsApp Us"
    whatsapp_url: str = "https://wa.me/918179941226"


class ResidentialCampCampSection(_CampModel):
    kicker: str = "More than training"
    h2: str = "Experience the Shaolin lifestyle"
    lead: str = (
        "The Shaolin Kungfu Residential Camp at Rock Martial Arts Academy is an "
        "immersive experience of training, discipline, teamwork, self-control and "
        "personal transformation."
    )
    cards: List[CampIconCard] = Field(default_factory=list)


class ResidentialCampTrainingSection(_CampModel):
    kicker: str = "What you will learn"
    h2: str = "Build skills that stay with you"
    cards: List[CampTrainingCard] = Field(default_factory=list)


class ResidentialCampPrice(_CampModel):
    label: str = "FULL CAMP FEE"
    amount: str = "₹15,000"
    includes_text: str = "Includes residential stay, food & training."
    pay_now_title: str = "Pay ₹7,500 now"
    pay_now_subtitle: str = "Balance ₹7,500 payable after reaching the camp location."
    refund_label: str = "Refund policy:"
    refund_text: str = "Registration/payment is non-refundable once confirmed."
    cta_label: str = "Secure Your Seat"


class ResidentialCampScheduleSection(_CampModel):
    kicker: str = "Daily routine"
    h2: str = "Train with purpose"
    timeline: List[CampTimelineItem] = Field(default_factory=list)
    price: ResidentialCampPrice = Field(default_factory=ResidentialCampPrice)


class ResidentialCampRulesSection(_CampModel):
    kicker: str = "Camp discipline"
    h2: str = "Discipline is part of the training"
    rules: List[str] = Field(default_factory=list)


class ResidentialCampRegisterSection(_CampModel):
    kicker: str = "Ready to begin?"
    h2: str = "Build the warrior within."
    paragraph: str = (
        "Limited seats available. Book your place for the Shaolin Kungfu Residential "
        "Camp with Rock Martial Arts Academy."
    )
    call_label: str = "Call 8179941226"
    phone: str = "8179941226"
    whatsapp_register_label: str = "Register on WhatsApp"
    whatsapp_url: str = "https://wa.me/918179941226"


class ResidentialCampFooter(_CampModel):
    academy_name: str = "ROCK MARTIAL ARTS ACADEMY"
    tagline: str = "Become the Strongest Version of Yourself"
    camp_line: str = "Shaolin Kungfu Residential Camp • Hyderabad • 22–26 September 2026"


class ResidentialCampContent(_CampModel):
    event_id: Optional[str] = None
    event_name: str = "Dussehra Special – Shaolin Kungfu Residential Camp"
    start_date: str = ""
    end_date: str = ""
    min_age: str = ""
    max_age: str = ""
    camp_fee: str = ""
    event_location: str = ""
    meta_title: str = "Shaolin Kungfu Residential Camp | Rock Martial Arts Academy"
    meta_description: str = (
        "Shaolin Kungfu Residential Camp by Rock Martial Arts Academy. "
        "22–26 September 2026, Hyderabad. Age 6–15. Camp fee ₹15,000."
    )
    topbar_text: str = "LIMITED SEATS • 22–26 SEPTEMBER 2026 • HYDERABAD"
    nav: ResidentialCampNav = Field(default_factory=ResidentialCampNav)
    hero: ResidentialCampHero = Field(default_factory=ResidentialCampHero)
    facts: List[CampLabelValue] = Field(default_factory=list)
    camp: ResidentialCampCampSection = Field(default_factory=ResidentialCampCampSection)
    training: ResidentialCampTrainingSection = Field(default_factory=ResidentialCampTrainingSection)
    schedule: ResidentialCampScheduleSection = Field(default_factory=ResidentialCampScheduleSection)
    rules: ResidentialCampRulesSection = Field(default_factory=ResidentialCampRulesSection)
    register: ResidentialCampRegisterSection = Field(default_factory=ResidentialCampRegisterSection)
    footer: ResidentialCampFooter = Field(default_factory=ResidentialCampFooter)


class ResidentialCampResponse(ResidentialCampContent):
    id: str = ""
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class CampEventFields(_CampModel):
    event_name: str = ""
    start_date: str = ""
    end_date: str = ""
    min_age: str = ""
    max_age: str = ""
    camp_fee: str = ""
    event_location: str = ""


class CampEventResponse(CampEventFields):
    event_id: str
    status: str = "current"
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


def default_residential_camp_dict() -> dict:
    content = ResidentialCampContent(
        event_id=new_camp_event_id(),
        event_name="Dussehra Special – Shaolin Kungfu Residential Camp",
        facts=[
            CampLabelValue(label="Dates", value="22–26 Sep 2026"),
            CampLabelValue(label="Location", value="Hyderabad"),
            CampLabelValue(label="Age Group", value="6–15 Years"),
            CampLabelValue(label="Camp Fee", value="₹15,000/-"),
        ],
        camp=ResidentialCampCampSection(
            cards=[
                CampIconCard(
                    icon="🥋",
                    title="Shaolin Kungfu",
                    text="Stances, punches, kicks, blocks, footwork, traditional forms and conditioning.",
                ),
                CampIconCard(
                    icon="⚔️",
                    title="Martial Arts",
                    text="Combat drills, partner work, pad training, self-defense, reaction, speed and agility.",
                ),
                CampIconCard(
                    icon="🧘",
                    title="Mind & Discipline",
                    text="Focus, self-control, meditation, teamwork and a stronger warrior mindset.",
                ),
            ]
        ),
        training=ResidentialCampTrainingSection(
            cards=[
                CampTrainingCard(
                    title="Foundation",
                    bullets=[
                        "Basic Shaolin stances",
                        "Punches and strikes",
                        "Kicks and blocks",
                        "Footwork and movement",
                    ],
                ),
                CampTrainingCard(
                    title="Traditional Training",
                    bullets=[
                        "Traditional forms (Taolu)",
                        "Advanced forms for eligible students",
                        "Balance and coordination",
                        "Shaolin conditioning",
                    ],
                ),
                CampTrainingCard(
                    title="Personal Development",
                    bullets=[
                        "Strength & flexibility",
                        "Self-defense awareness",
                        "Teamwork & leadership",
                        "Focus & confidence",
                    ],
                ),
            ]
        ),
        schedule=ResidentialCampScheduleSection(
            timeline=[
                CampTimelineItem(
                    time_label="Morning Session",
                    title="Physical conditioning & Shaolin practice",
                    text="Warm-up, mobility, conditioning and technical training.",
                ),
                CampTimelineItem(
                    time_label="Day Session",
                    title="Skills, forms & guided activities",
                    text="Traditional forms, partner drills, teamwork and learning activities.",
                ),
                CampTimelineItem(
                    time_label="Evening Session",
                    title="Martial arts practice & recovery",
                    text="Technique refinement, controlled drills, stretching and recovery.",
                ),
                CampTimelineItem(
                    time_label="Note",
                    title="Final camp timetable",
                    text="The exact daily timetable will be shared with registered participants before camp.",
                ),
            ]
        ),
        rules=ResidentialCampRulesSection(
            rules=[
                "Respect coaches and fellow participants.",
                "Follow the daily schedule and instructions.",
                "Maintain cleanliness and personal responsibility.",
                "Attend assigned training sessions.",
                "Follow all safety and residential guidelines.",
                "No unauthorized gadgets during designated periods.",
            ]
        ),
    )
    if hasattr(content, "model_dump"):
        return content.model_dump()
    return content.dict()
