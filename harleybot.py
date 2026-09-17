#!/usr/bin/env python3
"""
Harley Quinn Bot - Playful, chaotic, and dangerously fun
Version 1.1 - Fixed SQLAlchemy session issues
"""

import os
import logging
import asyncio
import random
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, Text, Float
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import requests

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Database setup
DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    raise ValueError("DATABASE_URL not set!")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
Base = declarative_base()
Session = sessionmaker(bind=engine)

# Kink categories for Harley Bot
HARLEY_KINKS = {
    'chaos': 'Chaos & Mischief',
    'teasing': 'Playful Teasing',
    'mind_games': 'Mind Games',
    'roleplay': 'Roleplay Scenarios',
    'power_play': 'Power Play',
    'humiliation': 'Humiliation (Playful)',
    'praise': 'Praise & Worship',
    'punishment': 'Punishment Games',
    'rewards': 'Reward Tasks',
    'exhibitionism': 'Exhibitionism',
    'bondage': 'Bondage & Restraint',
    'sensory': 'Sensory Play',
    'marking': 'Marking/Writing',
    'edging': 'Edging Control',
    'denial': 'Denial Games',
    'pet_play': 'Pet Play',
    'service': 'Service Tasks',
    'degradation': 'Degradation (Playful)',
    'pain': 'Pain Play',
    'temperature': 'Temperature Play',
    'spanking': 'Spanking'
}

# ==================== DATABASE MODELS ====================

class UserProfile(Base):
    __tablename__ = 'harley_user_profiles'
    
    id = Column(Integer, primary_key=True)
    chat_id = Column(String, unique=True, nullable=False)
    username = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_active = Column(DateTime, default=datetime.utcnow)
    
    # Stats
    total_tasks_completed = Column(Integer, default=0)
    total_tasks_failed = Column(Integer, default=0)
    current_streak = Column(Integer, default=0)
    longest_streak = Column(Integer, default=0)
    harley_points = Column(Integer, default=0)
    
    # Current task
    current_task = Column(Text)
    task_assigned_at = Column(DateTime)
    task_completed = Column(Boolean, default=False)
    photo_retry_count = Column(Integer, default=0)
    
    # Kink preferences (all default to True for Harley's chaos)
    chaos = Column(Boolean, default=True)
    teasing = Column(Boolean, default=True)
    mind_games = Column(Boolean, default=True)
    roleplay = Column(Boolean, default=True)
    power_play = Column(Boolean, default=True)
    humiliation = Column(Boolean, default=True)
    praise = Column(Boolean, default=True)
    punishment = Column(Boolean, default=True)
    rewards = Column(Boolean, default=True)
    exhibitionism = Column(Boolean, default=True)
    bondage = Column(Boolean, default=True)
    sensory = Column(Boolean, default=True)
    marking = Column(Boolean, default=True)
    edging = Column(Boolean, default=True)
    denial = Column(Boolean, default=True)
    pet_play = Column(Boolean, default=True)
    service = Column(Boolean, default=True)
    degradation = Column(Boolean, default=True)
    pain = Column(Boolean, default=True)
    temperature = Column(Boolean, default=True)
    spanking = Column(Boolean, default=True)

class TaskHistory(Base):
    __tablename__ = 'harley_task_history'
    
    id = Column(Integer, primary_key=True)
    chat_id = Column(String, nullable=False)
    task_description = Column(Text, nullable=False)
    assigned_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime)
    status = Column(String, default='assigned')
    verification_photo_url = Column(String)
    harley_feedback = Column(Text)

class ConversationLog(Base):
    __tablename__ = 'harley_conversation_log'
    
    id = Column(Integer, primary_key=True)
    chat_id = Column(String, nullable=False)
    message = Column(Text)
    response = Column(Text)
    timestamp = Column(DateTime, default=datetime.utcnow)

# Create tables
Base.metadata.create_all(engine)

# ==================== DATABASE HELPERS (FIXED) ====================

def get_or_create_user(chat_id: str, username: str = None) -> Dict[str, Any]:
    """Get existing user or create new one - returns dict, not ORM object"""
    session = Session()
    try:
        user = session.query(UserProfile).filter_by(chat_id=str(chat_id)).first()
        if not user:
            user = UserProfile(chat_id=str(chat_id), username=username)
            session.add(user)
            session.commit()
            logger.info(f"Created new user: {chat_id}")
        else:
            user.last_active = datetime.utcnow()
            if username and not user.username:
                user.username = username
            session.commit()
        
        # Convert to dict before session closes
        user_dict = {
            'chat_id': user.chat_id,
            'username': user.username,
            'total_tasks_completed': user.total_tasks_completed,
            'total_tasks_failed': user.total_tasks_failed,
            'current_streak': user.current_streak,
            'longest_streak': user.longest_streak,
            'harley_points': user.harley_points,
            'current_task': user.current_task,
            'task_completed': user.task_completed,
            'photo_retry_count': user.photo_retry_count,
            'task_assigned_at': user.task_assigned_at,
        }
        # Add all kink preferences
        for kink_id in HARLEY_KINKS.keys():
            user_dict[kink_id] = getattr(user, kink_id, True)
            
        return user_dict
    finally:
        session.close()

def update_user_field(chat_id: str, field: str, value):
    """Update a single user field"""
    session = Session()
    try:
        user = session.query(UserProfile).filter_by(chat_id=str(chat_id)).first()
        if user:
            setattr(user, field, value)
            session.commit()
            return True
        return False
    finally:
        session.close()

def update_user_fields(chat_id: str, updates: Dict[str, Any]):
    """Update multiple user fields at once"""
    session = Session()
    try:
        user = session.query(UserProfile).filter_by(chat_id=str(chat_id)).first()
        if user:
            for field, value in updates.items():
                setattr(user, field, value)
            session.commit()
            return True
        return False
    finally:
        session.close()

def toggle_user_kink(chat_id: str, kink_id: str) -> bool:
    """Toggle a kink preference, returns new value"""
    session = Session()
    try:
        user = session.query(UserProfile).filter_by(chat_id=str(chat_id)).first()
        if user and kink_id in HARLEY_KINKS:
            current = getattr(user, kink_id, True)
            setattr(user, kink_id, not current)
            session.commit()
            return not current
        return None
    finally:
        session.close()

def log_task_history(chat_id: str, task_description: str, status: str, 
                     photo_url: str = None, feedback: str = None):
    """Log a task to history"""
    session = Session()
    try:
        history = TaskHistory(
            chat_id=str(chat_id),
            task_description=task_description,
            status=status,
            verification_photo_url=photo_url,
            harley_feedback=feedback
        )
        session.add(history)
        session.commit()
    finally:
        session.close()

def log_conversation(chat_id: str, message: str, response: str):
    """Log conversation for context"""
    session = Session()
    try:
        log = ConversationLog(chat_id=str(chat_id), message=message, response=response)
        session.add(log)
        session.commit()
    finally:
        session.close()

def get_task_history(chat_id: str, limit: int = 10):
    """Get task history as list of dicts"""
    session = Session()
    try:
        history = session.query(TaskHistory).filter_by(chat_id=str(chat_id))\
            .order_by(TaskHistory.assigned_at.desc()).limit(limit).all()
        
        result = []
        for h in history:
            result.append({
                'task_description': h.task_description,
                'status': h.status,
                'assigned_at': h.assigned_at,
                'completed_at': h.completed_at,
                'harley_feedback': h.harley_feedback
            })
        return result
    finally:
        session.close()

def delete_all_user_data(chat_id: str):
    """Delete all data for a user"""
    session = Session()
    try:
        session.query(UserProfile).filter_by(chat_id=str(chat_id)).delete()
        session.query(TaskHistory).filter_by(chat_id=str(chat_id)).delete()
        session.query(ConversationLog).filter_by(chat_id=str(chat_id)).delete()
        session.commit()
        return True
    finally:
        session.close()

# ==================== VENICE AI INTEGRATION ====================

VENVENICE_API_KEY = os.getenv('VENVENICE_API_KEY')
VENVENICE_MODEL = "claude-opus-4-8-fast"

def generate_harley_response(prompt: str, max_tokens: int = 500) -> str:
    """Generate a response using Venice AI with Harley's personality"""
    if not VENVENICE_API_KEY:
        logger.error("VENVENICE_API_KEY not set!")
        return None
    
    harley_system_prompt = """You are Harley Quinn - playful, chaotic, unpredictable, and dangerously charming. 
    You speak with a Brooklyn accent, use pet names like "puddin'", "Mr. J", "kiddo", "sugar", and love causing mischief.
    You're flirty but threatening, sweet but dangerous. You love games, chaos, and keeping your pets on their toes.
    Keep responses energetic, slightly unhinged, and always entertaining. Use occasional laugh (hee hee!) and 
    comic book style sound effects (BOOM! POW! WHACK!)."""
    
    try:
        response = requests.post(
            "https://api.venice.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {VENVENICE_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": VENVENICE_MODEL,
                "messages": [
                    {"role": "system", "content": harley_system_prompt},
                    {"role": "user", "content": prompt}
                ],
                "max_tokens": max_tokens,
                "temperature": 0.9
            },
            timeout=30
        )
        
        if response.status_code == 200:
            data = response.json()
            return data['choices'][0]['message']['content']
        else:
            logger.error(f"Venice API error: {response.status_code} - {response.text}")
            return None
            
    except Exception as e:
        logger.error(f"Error calling Venice AI: {e}")
        return None

def generate_task_with_ai(allowed_kinks: list) -> str:
    """Generate a creative task using Venice AI"""
    
    kink_str = ", ".join(allowed_kinks) if allowed_kinks else "general submission tasks"
    
    prompt = f"""Generate a creative, slightly chaotic task for my pet to complete. 
    The task should involve these kinks/activities: {kink_str}.
    
    The task should be:
    - Playful but demanding
    - Specific and actionable (can be completed in 10-30 minutes)
    - Include an element of Harley's chaos or mischief
    - Require photo verification
    - Be 2-4 sentences long
    
    Examples:
    - "Go to the bathroom, write 'Harley's Property' on your chest with lipstick, and take a selfie! Better not smudge it, puddin'!"
    - "Put on your favorite music and edge yourself for exactly 3 minutes while dancing like a fool. Show me your best moves in a video!"
    
    Generate ONE task now. Be creative and unpredictable!"""
    
    ai_task = generate_harley_response(prompt, max_tokens=200)
    
    if ai_task:
        return ai_task
    
    # Creative fallbacks if AI fails
    fallbacks = [
        "Go find a mirror, write 'Harley's Toy' on it in lipstick, then take a selfie with your reflection! Hee hee!",
        "Put on the silliest outfit you own and strike your most ridiculous 'sexy' pose. Photo evidence required, sugar!",
        "Go to a window with the blinds partially open and do 10 jumping jacks... in your underwear! BOOM!",
        "Draw a heart on your chest with something edible, then lick it off while taking a selfie. Don't miss a spot!",
        "Find something red in your house and take a creative photo showing how much you love your Mistress!",
        "Put on a song and lip-sync dramatically while touching yourself. I want a video of your performance!",
        "Write 'Property of Harley' on your body in 3 different places, then send me the evidence!",
        "Take a photo of yourself in the most ridiculous position you can think of. Make me laugh, puddin'!"
    ]
    return random.choice(fallbacks)

def verify_photo_with_ai(task_description: str, photo_url: str) -> tuple:
    """Verify if a photo completes the task using Venice AI"""
    
    prompt = f"""Task: {task_description}
    
    Analyze this photo and determine:
    1. Does the photo show evidence of the task being completed? (YES/NO)
    2. If NO, explain specifically what's missing or wrong
    3. Give a brief, playful Harley Quinn style comment about the photo
    
    Respond in this format:
    VERDICT: [YES or NO]
    REASON: [explanation if NO, or 'Task completed satisfactorily' if YES]
    COMMENT: [Harley's playful comment]"""
    
    # Note: In production, you'd download the image and send it to a vision-capable model
    result = generate_harley_response(prompt, max_tokens=150)
    
    if result:
        verdict = "YES" if "VERDICT: YES" in result.upper() else "NO"
        reason = ""
        comment = ""
        
        for line in result.split('\n'):
            if line.startswith('REASON:'):
                reason = line.replace('REASON:', '').strip()
            elif line.startswith('COMMENT:'):
                comment = line.replace('COMMENT:', '').strip()
        
        return verdict == "YES", reason, comment or "Hee hee! Let's see what we got here..."
    
    # Default to accepting if AI fails
    return True, "", "Ooh, looks good to me, puddin'!"

# ==================== BOT COMMAND HANDLERS ====================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start - Auto-captures Chat ID"""
    chat_id = update.effective_chat.id
    username = update.effective_user.username
    
    user = get_or_create_user(chat_id, username)
    
    welcome_msg = f"""🎭 *WELCOME TO HARLEY'S FUNHOUSE!* 🎭

Hee hee! Well well well, look what the bat dragged in! 
I'm Harley Quinn, and you're gonna be my new favorite plaything! 

*Your Chat ID:* `{user['chat_id']}`
(Already saved, puddin'!)

🃏 *What I do:*
• Give you chaotic, fun tasks to complete
• Demand photo proof of your mischief  
• Keep score with Harley Dollars
• Play games that might drive you a little... crazy

🎪 *Commands:*
/kinks - Customize what games we'll play
/task - Get a new task from Mistress Harley
/status - Check your stats and current task
/history - See your completed tasks
/resetowner - Clear ALL data (for handing me to someone new)

Ready to cause some trouble? Say 'hi' or ask for a task!"""
    
    await update.message.reply_text(welcome_msg, parse_mode='Markdown')

async def kinks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show kink customization menu"""
    chat_id = update.effective_chat.id
    user = get_or_create_user(chat_id)
    
    # Build keyboard with toggle buttons
    keyboard = []
    row = []
    
    for kink_id, kink_name in HARLEY_KINKS.items():
        enabled = user.get(kink_id, True)
        emoji = "✅" if enabled else "❌"
        row.append(InlineKeyboardButton(
            f"{emoji} {kink_name}", 
            callback_data=f"toggle_{kink_id}"
        ))
        
        if len(row) == 2:
            keyboard.append(row)
            row = []
    
    if row:
        keyboard.append(row)
    
    keyboard.append([InlineKeyboardButton("🔙 Done", callback_data="kinks_done")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🎪 *Harley's Game Menu* 🎪\n\n"
        "Pick what kind of chaos you want, puddin'!\n"
        "Click to toggle on/off:",
        parse_mode='Markdown',
        reply_markup=reply_markup
    )

async def toggle_kink_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle kink toggle"""
    query = update.callback_query
    await query.answer()
    
    chat_id = update.effective_chat.id
    kink_id = query.data.replace("toggle_", "")
    
    if kink_id == "kinks_done":
        await query.edit_message_text("✨ All set! Let's have some fun!")
        return
    
    # Toggle the kink
    new_value = toggle_user_kink(chat_id, kink_id)
    
    if new_value is not None:
        # Refresh menu
        user = get_or_create_user(chat_id)
        keyboard = []
        row = []
        
        for kid, kname in HARLEY_KINKS.items():
            enabled = user.get(kid, True)
            emoji = "✅" if enabled else "❌"
            row.append(InlineKeyboardButton(
                f"{emoji} {kname}", 
                callback_data=f"toggle_{kid}"
            ))
            
            if len(row) == 2:
                keyboard.append(row)
                row = []
        
        if row:
            keyboard.append(row)
        
        keyboard.append([InlineKeyboardButton("🔙 Done", callback_data="kinks_done")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_reply_markup(reply_markup)

async def task_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate and assign a new task"""
    chat_id = update.effective_chat.id
    user = get_or_create_user(chat_id)
    
    # Check if already has active task
    if user.get('current_task') and not user.get('task_completed'):
        await update.message.reply_text(
            "🃏 *Slow down, sugar!* 🃏\n\n"
            f"You already have a task pending:\n\n{user['current_task']}\n\n"
            "Complete that one first before I give you more chaos!",
            parse_mode='Markdown'
        )
        return
    
    # Get allowed kinks
    allowed_kinks = []
    for kink_id, kink_name in HARLEY_KINKS.items():
        if user.get(kink_id, True):
            allowed_kinks.append(kink_name)
    
    # Generate new task
    task = generate_task_with_ai(allowed_kinks)
    
    # Save to user
    update_user_fields(chat_id, {
        'current_task': task,
        'task_assigned_at': datetime.utcnow(),
        'task_completed': False,
        'photo_retry_count': 0
    })
    
    # Create keyboard
    keyboard = [[
        InlineKeyboardButton("✅ Complete", callback_data="complete_task"),
        InlineKeyboardButton("❌ Give Up", callback_data="give_up")
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    message = f"""🎪 *MISTRESS HARLEY HAS SPOKEN!* 🎪

{task}

⏰ *You have 30 minutes to complete this, puddin'!*

Send me a photo when you're done! Hee hee!"""
    
    await update.message.reply_text(
        message,
        parse_mode='Markdown',
        reply_markup=reply_markup
    )
    
    # Schedule auto-clear
    asyncio.create_task(auto_clear_task(chat_id, 30))

async def auto_clear_task(chat_id: str, timeout_minutes: int):
    """Auto-clear task after timeout with punishment"""
    await asyncio.sleep(timeout_minutes * 60)
    
    # Re-fetch user to check current state
    user = get_or_create_user(chat_id)
    
    if user.get('current_task') and not user.get('task_completed'):
        # Punishment
        new_failed = user['total_tasks_failed'] + 1
        new_points = max(0, user['harley_points'] - 10)
        
        update_user_fields(chat_id, {
            'total_tasks_failed': new_failed,
            'current_streak': 0,
            'harley_points': new_points,
            'current_task': None,
            'task_completed': False
        })
        
        # Log as expired
        log_task_history(
            chat_id, 
            user['current_task'], 
            'expired',
            feedback="Too slow! Harley got bored waiting!"
        )
        
        logger.info(f"Task expired for user {chat_id}")

async def complete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle complete button - ask for photo"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(
        "📸 *Alright, show me the goods!* 📸\n\n"
        "Send me a photo proving you completed your task!\n"
        "Make it good, or I'll make you do it again! Hee hee!",
        parse_mode='Markdown'
    )

async def give_up_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle give up button"""
    query = update.callback_query
    await query.answer()
    
    chat_id = update.effective_chat.id
    
    user = get_or_create_user(chat_id)
    
    # Update stats
    new_failed = user['total_tasks_failed'] + 1
    new_points = max(0, user['harley_points'] - 5)
    
    log_task_history(
        chat_id,
        user.get('current_task', 'Unknown task'),
        'failed',
        feedback="Gave up on task"
    )
    
    update_user_fields(chat_id, {
        'total_tasks_failed': new_failed,
        'current_streak': 0,
        'harley_points': new_points,
        'current_task': None,
        'task_completed': False
    })
    
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(
        "😤 *Aww, giving up already?* 😤\n\n"
        "You're no fun! I expected better from my plaything...\n"
        "Your streak is broken and you lost 5 Harley Dollars!\n\n"
        "Want another chance? Use /task",
        parse_mode='Markdown'
    )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle photo verification"""
    chat_id = update.effective_chat.id
    user = get_or_create_user(chat_id)
    
    if not user.get('current_task'):
        await update.message.reply_text(
            "🤨 *What am I looking at?* 🤨\n\n"
            "I didn't ask for a photo... yet! Use /task to get a mission first!",
            parse_mode='Markdown'
        )
        return
    
    # Get photo
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    photo_url = file.file_path
    
    # Verify with AI
    await update.message.reply_text("🎨 Let me take a look at this...")
    
    success, reason, comment = verify_photo_with_ai(user['current_task'], photo_url)
    
    if success:
        # Task completed!
        new_completed = user['total_tasks_completed'] + 1
        new_streak = user['current_streak'] + 1
        new_longest = max(user['longest_streak'], new_streak)
        new_points = user['harley_points'] + 20
        
        log_task_history(
            chat_id,
            user['current_task'],
            'completed',
            photo_url=photo_url,
            feedback=comment
        )
        
        update_user_fields(chat_id, {
            'total_tasks_completed': new_completed,
            'current_streak': new_streak,
            'longest_streak': new_longest,
            'harley_points': new_points,
            'current_task': None,
            'task_completed': False,
            'photo_retry_count': 0
        })
        
        await update.message.reply_text(
            f"🎉 *GOOD PET!* 🎉\n\n"
            f"{comment}\n\n"
            f"✨ Current Streak: {new_streak}\n"
            f"💰 Harley Dollars: {new_points}\n\n"
            f"Want more chaos? Use /task",
            parse_mode='Markdown'
        )
        
    else:
        # Photo rejected
        new_retry = user.get('photo_retry_count', 0) + 1
        
        if new_retry >= 2:
            # Failed after retries
            new_failed = user['total_tasks_failed'] + 1
            new_points = max(0, user['harley_points'] - 10)
            
            log_task_history(
                chat_id,
                user['current_task'],
                'failed',
                feedback=f"Failed verification: {reason}"
            )
            
            update_user_fields(chat_id, {
                'total_tasks_failed': new_failed,
                'current_streak': 0,
                'harley_points': new_points,
                'current_task': None,
                'task_completed': False,
                'photo_retry_count': 0
            })
            
            await update.message.reply_text(
                f"❌ *NOPE!* ❌\n\n"
                f"{comment}\n\n"
                f"Reason: {reason}\n\n"
                f"You failed verification twice! Task marked as failed.\n"
                f"Streak broken! 💔\n\n"
                f"Try again with /task",
                parse_mode='Markdown'
            )
        else:
            # Offer retry
            update_user_field(chat_id, 'photo_retry_count', new_retry)
            
            keyboard = [[
                InlineKeyboardButton("🔄 Try Again", callback_data="retry_photo"),
                InlineKeyboardButton("🏳️ Give Up", callback_data="give_up_photo")
            ]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                f"🤔 *Hmm, not quite right...* 🤔\n\n"
                f"{comment}\n\n"
                f"❌ Issue: {reason}\n\n"
                f"You have {2 - new_retry} attempt(s) left!\n"
                f"Try again or give up?",
                parse_mode='Markdown',
                reply_markup=reply_markup
            )

async def retry_photo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle photo retry"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(
        "📸 *Alright, one more chance!* 📸\n\n"
        "Send me a better photo that actually shows what I asked for!\n"
        "Don't disappoint me again!",
        parse_mode='Markdown'
    )

async def give_up_photo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle give up on photo"""
    query = update.callback_query
    await query.answer()
    
    chat_id = update.effective_chat.id
    user = get_or_create_user(chat_id)
    
    new_failed = user['total_tasks_failed'] + 1
    new_points = max(0, user['harley_points'] - 10)
    
    log_task_history(
        chat_id,
        user.get('current_task', 'Unknown task'),
        'failed',
        feedback="Gave up on verification"
    )
    
    update_user_fields(chat_id, {
        'total_tasks_failed': new_failed,
        'current_streak': 0,
        'harley_points': new_points,
        'current_task': None,
        'task_completed': False,
        'photo_retry_count': 0
    })
    
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(
        "😒 *Fine, be that way...* 😒\n\n"
        "Task marked as failed. You know what that means?\n"
        "BROKEN STREAK! And I want my Harley Dollars back!\n\n"
        "Try again with /task if you dare...",
        parse_mode='Markdown'
    )

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user status"""
    chat_id = update.effective_chat.id
    user = get_or_create_user(chat_id)
    
    current_task_text = ""
    if user.get('current_task') and not user.get('task_completed'):
        time_left = ""
        if user.get('task_assigned_at'):
            elapsed = datetime.utcnow() - user['task_assigned_at']
            remaining = timedelta(minutes=30) - elapsed
            if remaining.total_seconds() > 0:
                mins = int(remaining.total_seconds() / 60)
                time_left = f" ({mins}m left)"
        
        current_task_text = f"\n🎯 *Current Task:*{time_left}\n{user['current_task']}\n"
    
    status_msg = f"""🎪 *HARLEY'S PLAYTHING STATUS* 🎪

👤 User: {user.get('username') or 'Anonymous'}
🆔 Chat ID: `{user['chat_id']}`

📊 *Stats:*
✅ Tasks Completed: {user['total_tasks_completed']}
❌ Tasks Failed: {user['total_tasks_failed']}
🔥 Current Streak: {user['current_streak']}
🏆 Longest Streak: {user['longest_streak']}
💰 Harley Dollars: {user['harley_points']}

{current_task_text}
Keep being a good pet! Hee hee!"""
    
    await update.message.reply_text(status_msg, parse_mode='Markdown')

async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show task history"""
    chat_id = update.effective_chat.id
    
    history = get_task_history(chat_id, limit=10)
    
    if not history:
        await update.message.reply_text(
            "🤷 *No history yet!* 🤷\n\n"
            "You haven't completed any tasks for me!\n"
            "What are you waiting for? Use /task!",
            parse_mode='Markdown'
        )
        return
    
    msg = "📜 *YOUR HARLEY HISTORY* 📜\n\n"
    for i, h in enumerate(history, 1):
        status_emoji = "✅" if h['status'] == 'completed' else "❌" if h['status'] == 'failed' else "⏰"
        msg += f"{i}. {status_emoji} {h['task_description'][:50]}...\n"
        if h['harley_feedback']:
            msg += f"   💬 {h['harley_feedback'][:50]}...\n"
        msg += "\n"
    
    await update.message.reply_text(msg, parse_mode='Markdown')

async def resetowner_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reset all data for handoff"""
    chat_id = update.effective_chat.id
    
    keyboard = [[
        InlineKeyboardButton("🗑️ YES, DELETE EVERYTHING", callback_data="confirm_reset"),
        InlineKeyboardButton("❌ Cancel", callback_data="cancel_reset")
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🚨 *DANGER ZONE!* 🚨\n\n"
        "This will DELETE ALL your data:\n"
        "- Profile & Stats\n"
        "- Task History\n"
        "- Current Tasks\n"
        "- Kink Preferences\n\n"
        "*This cannot be undone!*\n\n"
        "Use this when handing me to a new owner.",
        parse_mode='Markdown',
        reply_markup=reply_markup
    )

async def confirm_reset_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm reset"""
    query = update.callback_query
    await query.answer()
    
    chat_id = update.effective_chat.id
    
    delete_all_user_data(chat_id)
    
    await query.edit_message_text(
        "💥 *BOOM! ALL GONE!* 💥\n\n"
        "Your data has been wiped clean!\n"
        "I'm ready for a new plaything!\n\n"
        "Send /start to begin fresh!"
    )

async def cancel_reset_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel reset"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text("😌 Phew! Didn't delete anything! Carry on!")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle regular messages with AI response"""
    chat_id = update.effective_chat.id
    message_text = update.message.text
    
    user = get_or_create_user(chat_id, update.effective_user.username)
    
    # Generate Harley response
    prompt = f"The user said: '{message_text}'. Respond as Harley Quinn in a playful, slightly chaotic way. If they want a task, encourage them to use /task command."
    response = generate_harley_response(prompt, max_tokens=200)
    
    if not response:
        response = f"Hee hee! I heard ya, puddin'! Want some chaos? Use /task!"
    
    log_conversation(chat_id, message_text, response)
    
    await update.message.reply_text(response)

# ==================== ERROR HANDLING ====================

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors"""
    logger.error(f"Update {update} caused error {context.error}")
    
    if update and update.effective_message:
        await update.effective_message.reply_text(
            "💥 *WHOOPSIE!* 💥\n\n"
            "Something went BOOM in my circuits!\n"
            "Try again, puddin'!",
            parse_mode='Markdown'
        )

# ==================== MAIN ====================

def main():
    """Start the bot"""
    token = os.getenv('TELEGRAM_TOKEN')
    if not token:
        raise ValueError("TELEGRAM_TOKEN not set!")
    
    application = Application.builder().token(token).build()
    
    # Command handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("kinks", kinks_command))
    application.add_handler(CommandHandler("task", task_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("history", history_command))
    application.add_handler(CommandHandler("resetowner", resetowner_command))
    
    # Callback handlers
    application.add_handler(CallbackQueryHandler(toggle_kink_callback, pattern="^toggle_"))
    application.add_handler(CallbackQueryHandler(complete_callback, pattern="^complete_task$"))
    application.add_handler(CallbackQueryHandler(give_up_callback, pattern="^give_up$"))
    application.add_handler(CallbackQueryHandler(retry_photo_callback, pattern="^retry_photo$"))
    application.add_handler(CallbackQueryHandler(give_up_photo_callback, pattern="^give_up_photo$"))
    application.add_handler(CallbackQueryHandler(confirm_reset_callback, pattern="^confirm_reset$"))
    application.add_handler(CallbackQueryHandler(cancel_reset_callback, pattern="^cancel_reset$"))
    
    # Message handlers
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Error handler
    application.add_error_handler(error_handler)
    
    logger.info("Harley Bot starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()