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
    description: str = ""
    image: Optional[str] = None
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
    link_home: str = "Home"
    link_camp: str = "About"
    link_training: str = "Training"
    link_schedule: str = "Masters"
    link_levels: str = "Levels"
    link_journey: str = "Journey"
    link_rules: str = "Rules"
    link_register: str = "Register Now"
    mobile_register_label: str = "Register Now"


class ResidentialCampHero(_CampModel):
    hero_image: Optional[str] = None
    eyebrow: str = "TRADITIONAL • AUTHENTIC • TRANSFORMATIVE"
    eyebrow_part1: str = "TRADITIONAL"
    eyebrow_part2: str = "AUTHENTIC"
    eyebrow_part3: str = "TRANSFORMATIVE"
    h1_line1: str = "Shaolin"
    h1_line2: str = "Kung Fu"
    h2: str = "Train your body. Train your mind. Build your warrior spirit."
    paragraph: str = (
        "Authentic Shaolin Kung Fu training in Hyderabad at Rock Martial Arts Academy."
    )
    cta_primary_label: str = "Join Now"
    cta_whatsapp_label: str = "Book a Trial Class"
    whatsapp_url: str = "https://wa.me/918179941226"
    side_lines: List[str] = Field(
        default_factory=lambda: ["DISCIPLINE", "FOCUS", "STRENGTH", "A BETTER YOU"]
    )
    cta_primary_enabled: bool = True
    cta_secondary_enabled: bool = True
    calligraphy_text: str = "少林功夫"
    quote_text: str = "Not a fighter, a warrior."
    quote_author: str = "Deva"


class ResidentialCampCampSection(_CampModel):
    kicker: str = "ABOUT"
    h2: str = "SHAOLIN KUNG FU"
    lead: str = (
        "The Shaolin Kungfu Residential Camp at Rock Martial Arts Academy is an "
        "immersive experience of training, discipline, teamwork, self-control and "
        "personal transformation."
    )
    paragraph_1: str = (
        "Shaolin Kung Fu is a traditional Chinese martial art that combines physical "
        "training, martial techniques, flexibility, discipline and mental focus. "
        "It is more than fighting – it is a way of life."
    )
    paragraph_2: str = (
        "At Rock Martial Arts Academy, we offer structured and authentic Shaolin Kung Fu "
        "training for children, teenagers and adults, helping them develop strength, "
        "character and a positive mindset."
    )
    cta_label: str = "LEARN MORE"
    cta_href: str = "#training"
    cta_enabled: bool = True
    about_image: Optional[str] = "/campaign/aboutsection.png"
    cards: List[CampIconCard] = Field(default_factory=list)


class ResidentialCampTrainingSection(_CampModel):
    kicker: str = "What you will learn"
    h2: str = "OUR TRAINING PROGRAM"
    enabled: bool = True
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


class CampMasterProfile(_CampModel):
    title: str = ""
    name: str = ""
    designation: str = ""
    description: str = ""
    quote: str = ""
    image: Optional[str] = None
    enabled: bool = True


class ResidentialCampScheduleSection(_CampModel):
    kicker: str = "Daily routine"
    h2: str = "Train with purpose"
    masters: List[CampMasterProfile] = Field(default_factory=list)
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
    logo: Optional[str] = None
    description: str = "Become the Strongest Version of Yourself"
    social_instagram_url: str = ""
    social_instagram_icon: Optional[str] = None
    social_youtube_url: str = ""
    social_youtube_icon: Optional[str] = None
    social_facebook_url: str = ""
    social_facebook_icon: Optional[str] = None
    cta_label: str = "ENQUIRE NOW"
    cta_href: str = "#register"
    cta_enabled: bool = True
    cta_opens_register: bool = True


class CampFeatureBarItem(_CampModel):
    label: str = ""
    icon_image: Optional[str] = None
    icon_key: str = ""
    enabled: bool = True


class CampLevelCard(_CampModel):
    title: str = ""
    description: str = ""
    color: str = "#8f9a3a"
    enabled: bool = True


class CampLevelPanel(_CampModel):
    title: str = ""
    description: str = ""
    bullets: List[str] = Field(default_factory=list)
    cta_label: str = ""
    cta_href: str = "#"
    cta_enabled: bool = True
    cta_opens_register: bool = False
    enabled: bool = True


class ResidentialCampLevelsSection(_CampModel):
    enabled: bool = True
    background_image: Optional[str] = "/campaign/levelbg.png"
    h2: str = "TRAINING FOR EVERY LEVEL"
    cards: List[CampLevelCard] = Field(default_factory=list)
    panels: List[CampLevelPanel] = Field(default_factory=list)


class ResidentialCampJourneyCta(_CampModel):
    enabled: bool = True
    background_image: Optional[str] = "/campaign/ctabg.png"
    title_line1: str = "START YOUR"
    title_line2: str = "SHAOLIN JOURNEY TODAY"
    description: str = "A STRONGER BODY. A CALMER MIND. A BRIGHTER FUTURE."
    cta_primary_label: str = "REGISTER NOW"
    cta_primary_enabled: bool = True
    cta_primary_opens_register: bool = True
    cta_primary_href: str = "#register"
    cta_secondary_label: str = "BOOK A TRIAL CLASS"
    cta_secondary_enabled: bool = True
    cta_secondary_href: str = "https://wa.me/918179941226"


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
    feature_bar: List[CampFeatureBarItem] = Field(
        default_factory=lambda: [
            CampFeatureBarItem(
                label="Physical Fitness", icon_key="fitness", icon_image="/campaign/f1.png"
            ),
            CampFeatureBarItem(
                label="Mental Discipline", icon_key="discipline", icon_image="/campaign/f2.png"
            ),
            CampFeatureBarItem(
                label="Self Confidence", icon_key="confidence", icon_image="/campaign/f3.png"
            ),
            CampFeatureBarItem(
                label="Traditional Training", icon_key="training", icon_image="/campaign/f4.png"
            ),
            CampFeatureBarItem(
                label="Better Lifestyle", icon_key="lifestyle", icon_image="/campaign/f5.png"
            ),
        ]
    )
    camp: ResidentialCampCampSection = Field(default_factory=ResidentialCampCampSection)
    training: ResidentialCampTrainingSection = Field(default_factory=ResidentialCampTrainingSection)
    schedule: ResidentialCampScheduleSection = Field(default_factory=ResidentialCampScheduleSection)
    rules: ResidentialCampRulesSection = Field(default_factory=ResidentialCampRulesSection)
    levels: ResidentialCampLevelsSection = Field(default_factory=ResidentialCampLevelsSection)
    journey_cta: ResidentialCampJourneyCta = Field(default_factory=ResidentialCampJourneyCta)
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
        feature_bar=[
            CampFeatureBarItem(
                label="Physical Fitness",
                icon_key="fitness",
                icon_image="/campaign/f1.png",
            ),
            CampFeatureBarItem(
                label="Mental Discipline",
                icon_key="discipline",
                icon_image="/campaign/f2.png",
            ),
            CampFeatureBarItem(
                label="Self Confidence",
                icon_key="confidence",
                icon_image="/campaign/f3.png",
            ),
            CampFeatureBarItem(
                label="Traditional Training",
                icon_key="training",
                icon_image="/campaign/f4.png",
            ),
            CampFeatureBarItem(
                label="Better Lifestyle",
                icon_key="lifestyle",
                icon_image="/campaign/f5.png",
            ),
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
            h2="OUR TRAINING PROGRAM",
            cards=[
                CampTrainingCard(
                    title="SHAOLIN FORMS",
                    description="Traditional hand forms, stances and techniques.",
                    image="/campaign/t1.png",
                ),
                CampTrainingCard(
                    title="WEAPONS TRAINING",
                    description="Learn traditional Shaolin weapons like staff, spear, etc.",
                    image="/campaign/t2.png",
                ),
                CampTrainingCard(
                    title="FLEXIBILITY & MOBILITY",
                    description="Improve flexibility, balance and body control.",
                    image="/campaign/t3.png",
                ),
                CampTrainingCard(
                    title="STRENGTH & CONDITIONING",
                    description="Build functional strength, stamina and endurance.",
                    image="/campaign/t4.png",
                ),
                CampTrainingCard(
                    title="COMBAT TRAINING",
                    description="Practical application through drills and partner training.",
                    image="/campaign/t5.png",
                ),
                CampTrainingCard(
                    title="DISCIPLINE & MINDSET",
                    description="Develop patience, focus and a warrior mindset.",
                    image="/campaign/t6.png",
                ),
            ]
        ),
        schedule=ResidentialCampScheduleSection(
            masters=[
                CampMasterProfile(
                    title="OUR SHAOLIN LINEAGE",
                    name="MASTER DEVARAJU",
                    designation="Founder & Master Coach",
                    description=(
                        "Deva is a dedicated Shaolin Kung Fu practitioner and coach, known for his "
                        "discipline, strength and traditional training approach. He focuses on building "
                        "strong fundamentals, mental toughness and authentic Shaolin values in every student."
                    ),
                    quote="NOT A FIGHTER, A WARRIOR.",
                    image="/campaign/master1.png",
                ),
                CampMasterProfile(
                    title="MEET YOUR MASTER",
                    name="MASTER JANARDHAN",
                    designation="16th Generation Shaolin Disciple | Founder - Rock Martial Arts Academy",
                    description=(
                        "Master Janardhan is a 16th Generation Shaolin Disciple and the Founder of "
                        "Rock Martial Arts Academy. With years of dedicated training and teaching experience, "
                        "he specialises in traditional Shaolin Kung Fu, discipline-based coaching and "
                        "holistic martial arts development for students of all ages."
                    ),
                    quote="NOT A FIGHTER, A WARRIOR.",
                    image="/campaign/master2.png",
                ),
            ],
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
        levels=ResidentialCampLevelsSection(
            background_image="/campaign/levelbg.png",
            h2="TRAINING FOR EVERY LEVEL",
            cards=[
                CampLevelCard(
                    title="BEGINNER",
                    description="Learn the fundamentals. No experience needed.",
                    color="#8f9a3a",
                ),
                CampLevelCard(
                    title="INTERMEDIATE",
                    description="Develop your techniques and skills.",
                    color="#1f4d36",
                ),
                CampLevelCard(
                    title="ADVANCED",
                    description="For dedicated students seeking deeper training.",
                    color="#b85a28",
                ),
                CampLevelCard(
                    title="PROFESSIONAL PLAYER TRAINING",
                    description="Intensive training for demonstrations, tournaments and advanced development.",
                    color="#8b1a1a",
                ),
            ],
            panels=[
                CampLevelPanel(
                    title="SHAOLIN FOR CHILDREN",
                    description=(
                        "Help your child grow with discipline, confidence and focus "
                        "through Shaolin Kung Fu training."
                    ),
                    bullets=[
                        "Fitness & flexibility",
                        "Discipline & respect",
                        "Confidence & self-control",
                    ],
                    cta_label="ENROLL YOUR CHILD",
                    cta_href="#register",
                    cta_enabled=True,
                    cta_opens_register=True,
                ),
                CampLevelPanel(
                    title="RESIDENTIAL CAMPS",
                    description=(
                        "Experience intensive Shaolin Kung Fu training in our special residential camps."
                    ),
                    bullets=[
                        "Intensive training",
                        "Weapons practice",
                        "Discipline and routine",
                        "Group activities & more",
                    ],
                    cta_label="VIEW UPCOMING CAMPS",
                    cta_href="#camp",
                    cta_enabled=True,
                    cta_opens_register=False,
                ),
            ],
        ),
    )
    if hasattr(content, "model_dump"):
        return content.model_dump()
    return content.dict()
