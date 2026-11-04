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
    link_daily: str = "Schedule"
    link_gallery: str = "Gallery"
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


class CampDailySession(_CampModel):
    time_label: str = ""
    title: str = ""
    image: Optional[str] = None
    bullets: List[str] = Field(default_factory=list)
    footer_label: str = ""
    enabled: bool = True


class CampPackingGroup(_CampModel):
    title: str = ""
    items: List[str] = Field(default_factory=list)


class CampGalleryItem(_CampModel):
    type: str = "image"
    url: str = ""
    caption: str = ""
    enabled: bool = True


class ResidentialCampDailyLife(_CampModel):
    enabled: bool = True
    kicker: str = "RESIDENTIAL SHAOLIN KUNGFU CAMP"
    h2: str = "Daily Training Schedule"
    hours_label: str = "5 Hours Per Day"
    quote: str = "Discipline today\nA stronger tomorrow"
    sessions: List[CampDailySession] = Field(default_factory=list)
    principle_title: str = "Camp Training Principle"
    principle_steps: List[str] = Field(default_factory=list)
    principle_text: str = ""
    principle_image: Optional[str] = None
    total_title: str = "Total Training"
    total_value: str = "5 Hours Per Day"
    rules_title: str = "Camp Rules & Discipline"
    rules: List[str] = Field(default_factory=list)
    rules_image: Optional[str] = "/campaign/camp-rules-bg.jpg"
    rules_principle_title: str = "Shaolin Camp Principle"
    rules_principle_words: List[str] = Field(default_factory=list)
    rules_tagline: str = "Train the Body. Discipline the Mind. Build the Skill."
    food_title: str = "Food & Cleanliness"
    food_lead: str = ""
    food_items: List[str] = Field(default_factory=list)
    food_image: Optional[str] = "/campaign/camp-food-plate.jpg"
    packing_title: str = "Things to Carry"
    packing_groups: List[CampPackingGroup] = Field(default_factory=list)
    packing_important_title: str = "Important"
    packing_important: List[str] = Field(default_factory=list)


class ResidentialCampGallery(_CampModel):
    enabled: bool = True
    kicker: str = "Camp moments"
    h2: str = "Training Gallery"
    items: List[CampGalleryItem] = Field(default_factory=list)


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
    daily_life: ResidentialCampDailyLife = Field(default_factory=ResidentialCampDailyLife)
    gallery: ResidentialCampGallery = Field(default_factory=ResidentialCampGallery)
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
        daily_life=ResidentialCampDailyLife(
            sessions=[
                CampDailySession(
                    time_label="6:30 AM – 8:00 AM",
                    title="Physical Preparation",
                    image="/campaign/camp-session-morning.jpg",
                    bullets=[
                        "Warm-up",
                        "Mobility training",
                        "Flexibility",
                        "Stance conditioning",
                        "Basic body conditioning",
                    ],
                    footer_label="Morning\nBuild the body",
                ),
                CampDailySession(
                    time_label="11:00 AM – 12:00 PM",
                    title="Form Training",
                    image="/campaign/camp-session-midday.jpg",
                    bullets=[
                        "Shaolin basic forms",
                        "Wubu practice",
                        "Chai combinations",
                        "Form movements and techniques",
                        "Form correction and refinement",
                    ],
                    footer_label="Midday\nLearn the skill",
                ),
                CampDailySession(
                    time_label="5:00 PM – 6:30 PM",
                    title="Form & Basic Foundation",
                    image="/campaign/camp-session-evening.jpg",
                    bullets=[
                        "Shaolin basic foundation",
                        "Stances & footwork",
                        "Basic hand techniques",
                        "Basic kicks",
                        "Wubu footwork",
                        "Chai form practice",
                        "Form applications",
                        "Repetition & technique correction",
                    ],
                    footer_label="Evening\nRefine and apply",
                ),
            ],
            principle_steps=["Foundation", "Technique", "Form", "Application", "Discipline"],
            principle_text=(
                "Every student will train according to their level and ability, with emphasis on "
                "correct technique, discipline, flexibility, conditioning and consistent practice."
            ),
            principle_image="/campaign/camp-principle.jpg",
            rules_image="/campaign/camp-rules-bg.jpg",
            food_image="/campaign/camp-food-plate.jpg",
            rules=[
                "Punctuality is compulsory — Students must report to every training session on time.",
                "Training Schedule must be followed — All students must attend the complete daily training program.",
                "Proper Uniform is Mandatory — Students must wear the prescribed academy/camp training uniform during training.",
                "Respect & Discipline — Students must respect the Master, coaches, staff and fellow students at all times.",
                "No Mobile Phones & Gadgets — Mobile phones, gaming devices and unnecessary gadgets are not permitted during the camp.",
                "No Fighting or Bullying — Fighting, bullying, teasing, abusive language or disrespectful behaviour will not be tolerated.",
                "Training Instructions Must Be Followed — Students must follow the instructions given by the Master and coaches during all training sessions.",
                "Residential Discipline — Students must maintain cleanliness, order and discipline in dormitory and common areas.",
                "No Leaving the Camp Premises — Students are not permitted to leave the camp premises without permission from the camp management.",
                "Food & Hydration — Students must follow the camp meal schedule and maintain proper hydration.",
                "Equipment Safety — Training equipment, including weapons where applicable, must be handled only under coach supervision.",
                "Health & Safety — Any injury, illness, discomfort or medical concern must be immediately reported to the coach or camp staff.",
                "Respect the Training Environment — Students must maintain silence and discipline during meditation, demonstrations and technical instruction.",
                "No Damage to Property — Any intentional damage to academy or camp property will be the responsibility of the student/parent.",
                "Level-Based Training — Students must complete the required foundation and demonstrate proper technique before progressing to the next level.",
                "No Unauthorised Recording — Photography or video recording during training is permitted only with camp management approval.",
                "Serious Misconduct — Repeated or serious violation of camp rules may result in removal from the training program, subject to the camp management’s decision.",
            ],
            rules_principle_words=["Discipline", "Respect", "Consistency", "Hard Work"],
            food_lead=(
                "After every meal, each student must clean their own plate, glass and eating area "
                "and place them in the designated washing area."
            ),
            food_items=[
                "Do not leave used plates or food behind.",
                "Do not waste food.",
                "Keep the dining area clean and organized.",
                "Follow the meal timings strictly.",
                "Maintain proper table manners and discipline.",
            ],
            packing_groups=[
                CampPackingGroup(
                    title="Essential Items",
                    items=[
                        "3–5 sets of comfortable training clothes",
                        "Academy/Camp uniform",
                        "Sports shoes",
                        "Slippers",
                        "Towel",
                        "Personal toiletries",
                        "Toothbrush & toothpaste",
                        "Soap & shampoo",
                        "Water bottle",
                        "Personal medicines, if required",
                        "Small backpack for daily training",
                        "Extra innerwear and socks",
                        "Nightwear",
                        "Bedsheet/blanket, if instructed by camp management",
                    ],
                ),
                CampPackingGroup(
                    title="Training Essentials",
                    items=[
                        "Kungfu training uniform",
                        "Stretchable training pants",
                        "Hand towel",
                        "Notebook & pen for learning techniques and instructions",
                        "Any prescribed protective equipment",
                    ],
                ),
            ],
            packing_important=[
                "Do not carry mobile phones, gaming devices, expensive jewellery, or unnecessary valuables unless specifically permitted by camp management.",
                "Label all personal belongings with the student’s name.",
            ],
        ),
        gallery=ResidentialCampGallery(items=[]),
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
