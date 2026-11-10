import os, json, random, logging
from datetime import datetime, timezone, timedelta
from sqlalchemy import (create_engine, Column, Integer, BigInteger, String, DateTime,
                        Boolean, Text, inspect, text as sqltext)
from sqlalchemy.orm import declarative_base, sessionmaker

logger = logging.getLogger(__name__)
Base = declarative_base()


class UserState(Base):
    __tablename__ = 'user_states'
    id = Column(BigInteger, primary_key=True)
    user_id = Column(BigInteger, unique=True, nullable=False)
    username = Column(String(100))
    gender = Column(String(20), default=None)
    variant = Column(String(20), default=None)
    current_outfit = Column(String(100), default=None)
    outfit_items = Column(Text, default=None)
    outfit_detail = Column(Text, default=None)
    location = Column(String(50), default=None)
    custom_location = Column(String(100), default=None)
    location_detail = Column(Text, default=None)
    location_probes = Column(Text, default=None)
    rooms = Column(Text, default=None)
    features = Column(Text, default=None)
    venue = Column(String(50), default=None)
    venue_typical = Column(Text, default=None)
    venue_profile = Column(Text, default=None)
    hide_spot = Column(String(100), default=None)
    asked = Column(Text, default=None)
    slot_skipped = Column(Text, default=None)
    pending = Column(String(50), default=None)
    reward_model = Column(String(50), default=None)
    task_model = Column(String(50), default=None)
    recent_settings = Column(Text, default=None)
    last_kinks_used = Column(Text, default=None)
    context_alone = Column(Boolean, default=True)
    context_others = Column(String(100), default=None)
    context_privacy = Column(String(20), default=None)
    risk_level = Column(Integer, default=5)
    intensity = Column(String(20), default="standard")
    risk_pre_kids = Column(Integer, default=None)
    points = Column(Integer, default=0)
    streak = Column(Integer, default=0)
    consecutive_failures = Column(Integer, default=0)
    total_tasks = Column(Integer, default=0)
    completed_tasks = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_task_at = Column(DateTime(timezone=True), nullable=True)
    next_task_time = Column(DateTime(timezone=True), nullable=True)
    scheduling_enabled = Column(Boolean, default=True)
    min_interval = Column(Integer, default=30)
    max_interval = Column(Integer, default=180)
    avatar_url = Column(Text, nullable=True)
    avatar_gender = Column(String(20), default=None)
    avatar_race = Column(String(20), default=None)
    avatar_build = Column(String(20), default=None)
    avatar_hair = Column(String(20), default=None)
    avatar_genital_size = Column(String(20), default=None)
    challenges_since_reward = Column(Integer, default=0)
    last_avatar_sent_at = Column(DateTime(timezone=True), nullable=True)
    avatar_interval_minutes = Column(Integer, default=60)
    avatar_enabled = Column(Boolean, default=False)
    kink_exposure = Column(String(10), default="okay")
    kink_humiliation = Column(String(10), default="okay")
    kink_degradation = Column(String(10), default="okay")
    kink_bondage = Column(String(10), default="okay")
    kink_pain = Column(String(10), default="okay")
    kink_service = Column(String(10), default="okay")
    kink_edging = Column(String(10), default="okay")
    kink_watersports = Column(String(10), default="okay")
    kink_exhibitionism = Column(String(10), default="okay")
    kink_roleplay = Column(String(10), default="okay")
    kink_petplay = Column(String(10), default="okay")
    kink_feminization = Column(String(10), default="okay")
    kink_cbt = Column(String(10), default="okay")
    kink_breathplay = Column(String(10), default="okay")
    kink_sensory = Column(String(10), default="okay")
    kink_temperature = Column(String(10), default="okay")
    kink_marking = Column(String(10), default="okay")
    kink_spanking = Column(String(10), default="okay")
    kink_nipple = Column(String(10), default="okay")
    kink_anal = Column(String(10), default="okay")
    kink_gags = Column(String(10), default="okay")
    kink_social_media = Column(String(10), default="okay")


class Task(Base):
    __tablename__ = 'tasks'
    id = Column(BigInteger, primary_key=True)
    user_id = Column(BigInteger, nullable=False)
    task_text = Column(Text, nullable=False)
    risk_level = Column(Integer, nullable=False)
    status = Column(String(20), default="pending")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    photo_url = Column(Text, nullable=True)
    cloudinary_url = Column(Text, nullable=True)
    cloudinary_public_id = Column(Text, nullable=True)
    verification_attempts = Column(Integer, default=0)
    followup_target = Column(Text, nullable=True)
    blocked_count = Column(Integer, default=0)
    sharpness = Column(Integer, nullable=True)
    exposure = Column(Integer, default=0)
    exposure_label = Column(Text, nullable=True)


class AvatarImage(Base):
    __tablename__ = 'avatar_images'
    id = Column(BigInteger, primary_key=True)
    user_id = Column(BigInteger, nullable=False)
    image_url = Column(Text, nullable=False)
    cloudinary_url = Column(Text, nullable=True)
    cloudinary_public_id = Column(Text, nullable=True)
    gender = Column(String(20))
    race = Column(String(20))
    build = Column(String(20))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class UserImage(Base):
    __tablename__ = 'user_images'
    id = Column(BigInteger, primary_key=True)
    user_id = Column(BigInteger, nullable=False)
    telegram_file_id = Column(Text, nullable=False)
    cloudinary_url = Column(Text, nullable=True)
    cloudinary_public_id = Column(Text, nullable=True)
    image_type = Column(String(50), default='user_upload')
    uploaded_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class LocationConstraint(Base):
    __tablename__ = 'location_constraints'
    id = Column(BigInteger, primary_key=True)
    user_id = Column(BigInteger, nullable=False)
    location = Column(String(100), nullable=False)
    constraint = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime(timezone=True), nullable=False)
    times_hit = Column(Integer, default=1)


def _url():
    url = os.environ["DATABASE_URL"]
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


engine = create_engine(_url(), pool_pre_ping=True, pool_size=10, max_overflow=20, pool_recycle=300)
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)


def get_session():
    return Session()


def ensure_schema():
    insp = inspect(engine)
    existing = set(insp.get_table_names())
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing:
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for column in table.columns:
                if column.name in have:
                    continue
                ddl = column.type.compile(engine.dialect)
                logger.warning(f"migrating: {table.name}.{column.name}")
                conn.execute(sqltext(
                    f'ALTER TABLE {table.name} ADD COLUMN IF NOT EXISTS "{column.name}" {ddl}'))


_PERSIST = {
    "variant": "variant", "privacy": "context_privacy", "location": "location",
    "location_notes": "location_detail", "rooms": "rooms", "probes": "location_probes",
    "features": "features", "venue": "venue", "venue_typical": "venue_typical",
    "venue_profile": "venue_profile", "hide_spot": "hide_spot",
    "asked": "asked", "slot_skipped": "slot_skipped",
    "pending": "pending",
    "reward_model": "reward_model", "task_model": "task_model",
    "recent_settings": "recent_settings", "last_kinks_used": "last_kinks_used",
    "outfit_detail": "outfit_detail", "current_outfit": "current_outfit",
    "outfit_items": "outfit_items", "gender": "gender",
    "risk_level": "risk_level", "intensity": "intensity", "risk_pre_kids": "risk_pre_kids",
    "points": "points", "streak": "streak", "completed_tasks": "completed_tasks",
    "consecutive_failures": "consecutive_failures",
    "challenges_since_reward": "challenges_since_reward",
    "scheduling_enabled": "scheduling_enabled", "next_task_time": "next_task_time",
    "min_interval": "min_interval", "max_interval": "max_interval",
    "context_others": "context_others", "context_alone": "context_alone",
    "avatar_gender": "avatar_gender", "avatar_race": "avatar_race",
    "avatar_build": "avatar_build", "avatar_hair": "avatar_hair",
    "avatar_genital_size": "avatar_genital_size",
    "avatar_enabled": "avatar_enabled", "avatar_interval_minutes": "avatar_interval_minutes",
    "last_avatar_sent_at": "last_avatar_sent_at",
}

for _k in ("exposure", "humiliation", "degradation", "bondage", "pain", "service", "edging", "watersports", "exhibitionism", "roleplay", "petplay", "feminization", "cbt", "breathplay", "sensory", "temperature", "marking", "spanking", "nipple", "anal", "gags", "social_media"):
    _PERSIST[f"kink_{_k}"] = f"kink_{_k}"

_JSON = {"rooms", "probes", "features", "recent_settings", "last_kinks_used", "outfit_items",
         "venue_profile", "asked", "slot_skipped"}
_VOLATILE = {"history", "probe_pool"}
_STATES = {}


class StateView(dict):
    def __init__(self, row, session):
        super().__init__()
        self._row = row
        self._session = session
        for key, col in _PERSIST.items():
            raw = getattr(row, col, None)
            if key in _JSON:
                empty = {} if key in ("venue_profile", "asked") else []
                if raw:
                    try:
                        val = json.loads(raw)
                        super().__setitem__(key, val if isinstance(val, type(empty)) else empty)
                        continue
                    except (ValueError, TypeError):
                        pass
                super().__setitem__(key, empty)
            else:
                super().__setitem__(key, raw)

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        col = _PERSIST.get(key)
        if not col or key in _VOLATILE:
            return
        try:
            setattr(self._row, col, json.dumps(value) if key in _JSON else value)
            self._session.commit()
        except Exception as exc:
            logger.warning(f"state write failed for {key}: {exc}")

    def save(self):
        try:
            self._session.commit()
        except Exception as exc:
            logger.warning(f"state save failed: {exc}")

    def setdefault(self, key, default=None):
        if key not in self or self[key] is None:
            self[key] = default
        return self[key]

    @property
    def user_id(self):
        return self._row.user_id


def get_state(user_id):
    view = _STATES.get(user_id)
    if view is not None:
        return view
    session = get_session()
    row = session.query(UserState).filter_by(user_id=user_id).first()
    if not row:
        row = UserState(
            user_id=user_id, username="",
            next_task_time=datetime.now(timezone.utc) + timedelta(minutes=random.randint(30, 180)))
        session.add(row)
        session.commit()
    view = StateView(row, session)
    _STATES[user_id] = view
    return view


def drop_state(user_id):
    view = _STATES.pop(user_id, None)
    if view is not None:
        view._session.close()


def all_user_ids():
    session = get_session()
    try:
        return [r.user_id for r in session.query(UserState.user_id).all()]
    finally:
        session.close()


def session_of(state):
    return state._session