import os
import logging
import io
import time
import sys
from typing import Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from datetime import datetime
import requests

# Configure logging - more detailed for debugging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Log startup
logger.info("Starting bot...")

# Get environment variables
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')

# Validate token
if not TELEGRAM_TOKEN:
    logger.error("TELEGRAM_TOKEN is not set in environment variables!")
    sys.exit(1)

logger.info("TELEGRAM_TOKEN loaded successfully")

# Initialize AI providers
providers = {}

# Try Gemini
if GEMINI_API_KEY:
    try:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        providers['gemini'] = genai.GenerativeModel('gemini-pro')
        logger.info("Gemini initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize Gemini: {e}")

# Try OpenAI
if OPENAI_API_KEY:
    try:
        import openai
        openai.api_key = OPENAI_API_KEY
        providers['openai'] = 'openai'
        logger.info("OpenAI initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize OpenAI: {e}")

if not providers:
    logger.error("No AI providers configured! Please set at least one API key.")
    # Continue anyway - we'll show a message to users

# Constants
MAX_PROMPT_LENGTH = 500
RATE_LIMIT_PER_USER = 10

# Simple rate limiting - no persistent storage needed
rate_limit_data = {}

def get_remaining_requests(user_id: int) -> int:
    """Get remaining requests for a user"""
    current_hour = int(time.time() / 3600)
    key = f"{user_id}_{current_hour}"
    
    if key not in rate_limit_data:
        rate_limit_data[key] = RATE_LIMIT_PER_USER
    
    return rate_limit_data[key]

def decrement_requests(user_id: int) -> bool:
    """Decrement user's request count, return False if limit exceeded"""
    current_hour = int(time.time() / 3600)
    key = f"{user_id}_{current_hour}"
    
    if key not in rate_limit_data:
        rate_limit_data[key] = RATE_LIMIT_PER_USER
    
    if rate_limit_data[key] <= 0:
        return False
    
    rate_limit_data[key] -= 1
    return True

def refund_request(user_id: int) -> None:
    """Refund a request if generation failed"""
    current_hour = int(time.time() / 3600)
    key = f"{user_id}_{current_hour}"
    if key in rate_limit_data:
        rate_limit_data[key] += 1

# ==================== COMMAND HANDLERS ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command"""
    user_id = update.effective_user.id
    
    # Initialize user data
    if 'provider' not in context.user_data:
        context.user_data['provider'] = 'gemini' if 'gemini' in providers else list(providers.keys())[0] if providers else None
    
    welcome_message = (
        f"🎨 Welcome to ImageGen Bot!\n\n"
        "I can generate images from your text descriptions using AI.\n\n"
        "📝 **How to use:**\n"
        "Simply send me any text describing the image you want.\n\n"
        "✨ **Tips:**\n"
        "• Be specific and descriptive\n"
        "• Mention the style (photorealistic, cartoon, etc.)\n"
        "• Include details about lighting, colors, and composition\n\n"
        "📊 **Your Status:**\n"
        f"• Remaining images this hour: {get_remaining_requests(user_id)}\n"
        f"• Current AI provider: {context.user_data.get('provider', 'none').upper()}\n\n"
        "📌 **Commands:**\n"
        "/start - Show this message\n"
        "/help - Get detailed help\n"
        "/status - Check your usage limits\n"
        "/provider - Change AI provider (if available)"
    )
    
    keyboard = [
        [InlineKeyboardButton("💡 Examples", callback_data='examples')],
        [InlineKeyboardButton("ℹ️ Help", callback_data='help')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        welcome_message,
        parse_mode='Markdown',
        reply_markup=reply_markup
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command"""
    help_text = (
        "📖 **How to generate images:**\n\n"
        "1. **Describe your image** - Be specific about what you want\n"
        "2. **Add style keywords** - e.g., 'photorealistic', 'anime', 'oil painting'\n"
        "3. **Specify details** - colors, lighting, composition, mood\n\n"
        "🎯 **Example prompts:**\n\n"
        "• 'A majestic dragon flying over snow-capped mountains at sunset'\n"
        "• 'Digital art of a cyberpunk city with neon lights and rain'\n"
        "• 'Oil painting style portrait of an elderly wise wizard'\n"
        "• 'Photorealistic close-up of a dewdrop on a green leaf'\n\n"
        "🔄 **Rate Limits:** You can generate up to 10 images per hour.\n\n"
        "⚙️ **Commands:**\n"
        "/start - Welcome message\n"
        "/help - This help\n"
        "/status - Check your usage\n"
        "/provider - Change AI model"
    )
    
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show user's current usage status"""
    user_id = update.effective_user.id
    remaining = get_remaining_requests(user_id)
    provider = context.user_data.get('provider', 'none')
    
    status_message = (
        f"📊 **Your Status**\n\n"
        f"🔄 Images remaining this hour: {remaining}\n"
        f"⏰ Hour resets at: {':00'}\n\n"
        f"🤖 Current AI provider: {provider.upper()}\n"
        f"📝 Max prompt length: {MAX_PROMPT_LENGTH} characters\n"
        f"🔌 Available providers: {', '.join(providers.keys()).upper() if providers else 'None'}"
    )
    
    await update.message.reply_text(status_message, parse_mode='Markdown')

async def provider_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Change AI provider"""
    if not providers:
        await update.message.reply_text("❌ No AI providers available. Please contact the bot administrator.")
        return
    
    if len(providers) < 2:
        await update.message.reply_text(f"ℹ️ Only one AI provider is available: **{list(providers.keys())[0].upper()}**", parse_mode='Markdown')
        return
    
    keyboard = []
    for provider in providers.keys():
        is_current = provider == context.user_data.get('provider', list(providers.keys())[0])
        keyboard.append([InlineKeyboardButton(
            f"{'✅ ' if is_current else ''}{provider.upper()}",
            callback_data=f'provider_{provider}'
        )])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🤖 **Select AI Provider:**\n\nChoose which AI model to use for image generation:",
        parse_mode='Markdown',
        reply_markup=reply_markup
    )

# ==================== MESSAGE HANDLERS ====================

async def generate_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate image from text prompt"""
    user_id = update.effective_user.id
    prompt = update.message.text
    
    # Check if we have any AI provider
    if not providers:
        await update.message.reply_text(
            "❌ No AI providers are configured. Please contact the bot administrator to set up API keys."
        )
        return
    
    # Check prompt length
    if len(prompt) > MAX_PROMPT_LENGTH:
        await update.message.reply_text(
            f"❌ Prompt is too long! Maximum {MAX_PROMPT_LENGTH} characters. "
            f"Your prompt has {len(prompt)} characters."
        )
        return
    
    # Check rate limit
    if not decrement_requests(user_id):
        await update.message.reply_text(
            "⚠️ You've reached the hourly limit of 10 images. Please try again later."
        )
        return
    
    # Send typing indicator
    await update.message.chat.send_action(action="upload_photo")
    
    # Get provider
    provider = context.user_data.get('provider', list(providers.keys())[0] if providers else None)
    
    if provider not in providers:
        provider = list(providers.keys())[0]
        context.user_data['provider'] = provider
    
    # Send processing message
    processing_msg = await update.message.reply_text(
        f"🎨 Generating image with {provider.upper()}...\n"
        f"📝 Prompt: '{prompt[:50]}...'\n\n"
        f"⏳ Please wait a few seconds..."
    )
    
    try:
        # Generate image
        if provider == 'gemini':
            image_data = await generate_with_gemini(prompt)
        elif provider == 'openai':
            image_data = await generate_with_openai(prompt)
        else:
            raise ValueError(f"Unknown provider: {provider}")
        
        # Delete processing message
        await processing_msg.delete()
        
        # Send image
        photo_file = io.BytesIO(image_data)
        photo_file.name = f"image_{int(time.time())}.png"
        
        caption = (
            f"✨ **Generated Image**\n\n"
            f"📝 Prompt: {prompt}\n"
            f"🤖 Provider: {provider.upper()}"
        )
        
        await update.message.reply_photo(
            photo=photo_file,
            caption=caption,
            parse_mode='Markdown'
        )
        
        logger.info(f"Generated image for user {user_id} using {provider}")
        
    except Exception as e:
        logger.error(f"Error generating image: {e}")
        await processing_msg.edit_text(
            f"❌ Failed to generate image.\n\n"
            f"Error: {str(e)[:100]}\n\n"
            f"Please try again with a different prompt."
        )
        # Refund rate limit
        refund_request(user_id)

# ==================== AI PROVIDERS ====================

async def generate_with_gemini(prompt: str) -> bytes:
    """Generate image using Google Gemini"""
    try:
        import google.generativeai as genai
        
        # Simple approach: Gemini generates text, we'll use a placeholder
        # For actual image generation, you'd need the image generation model
        model = genai.GenerativeModel('gemini-pro')
        response = model.generate_content(f"Describe a detailed image: {prompt}")
        
        # For now, we'll use a placeholder image since Gemini doesn't generate images yet
        # You can replace this with an actual image generation API
        import requests
        placeholder_url = f"https://picsum.photos/800/600?random={int(time.time())}"
        image_response = requests.get(placeholder_url)
        return image_response.content
        
    except Exception as e:
        logger.error(f"Gemini error: {e}")
        raise

async def generate_with_openai(prompt: str) -> bytes:
    """Generate image using OpenAI DALL-E"""
    try:
        import openai
        
        response = openai.Image.create(
            prompt=prompt,
            n=1,
            size="1024x1024",
            quality="standard"
        )
        
        image_url = response['data'][0]['url']
        image_response = requests.get(image_url)
        return image_response.content
        
    except Exception as e:
        logger.error(f"OpenAI error: {e}")
        raise

# ==================== CALLBACK HANDLERS ====================

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline button callbacks"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data == 'examples':
        examples = (
            "📸 **Example Prompts:**\n\n"
            "1. 'A photorealistic sunset over a calm ocean with seagulls'\n"
            "2. 'A majestic dragon with iridescent scales flying over mountains'\n"
            "3. 'Cyberpunk cityscape at night, neon signs, rain-slicked streets'\n"
            "4. 'Anime-style portrait of a magical girl with glowing blue eyes'\n"
            "5. 'Oil painting of a majestic forest with ancient trees, golden sunlight'"
        )
        await query.edit_message_text(examples, parse_mode='Markdown')
        
    elif data == 'help':
        await help_command(update, context)
        
    elif data.startswith('provider_'):
        provider = data.replace('provider_', '')
        context.user_data['provider'] = provider
        await query.edit_message_text(
            f"✅ AI provider changed to: **{provider.upper()}**\n\n"
            f"Try generating an image now!"
        )

# ==================== MAIN ====================

def main() -> None:
    """Start the bot"""
    try:
        logger.info("Creating application...")
        app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
        
        # Add handlers
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("help", help_command))
        app.add_handler(CommandHandler("status", status_command))
        app.add_handler(CommandHandler("provider", provider_command))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, generate_image))
        app.add_handler(CallbackQueryHandler(button_callback))
        
        logger.info("Starting polling...")
        app.run_polling(allowed_updates=Update.ALL_TYPES)
        
    except Exception as e:
        logger.error(f"Fatal error in main: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
