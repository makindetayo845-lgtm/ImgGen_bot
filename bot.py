import os
import logging
import io
import time
from typing import Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
import google.generativeai as genai
import openai
from PIL import Image
import requests
from datetime import datetime

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Get environment variables
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
REPLICATE_API_KEY = os.environ.get('REPLICATE_API_KEY')

# Default to Gemini if available
DEFAULT_PROVIDER = os.environ.get('DEFAULT_PROVIDER', 'gemini')

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN must be set in environment variables")

# Initialize AI providers
providers = {}

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    providers['gemini'] = genai.GenerativeModel('gemini-2.0-flash-exp-image-generation')

if OPENAI_API_KEY:
    openai.api_key = OPENAI_API_KEY
    providers['openai'] = 'openai'

if REPLICATE_API_KEY:
    providers['replicate'] = 'replicate'

# Check if at least one provider is available
if not providers:
    logger.warning("No AI providers configured! Please set at least one API key.")

# User data storage (in production, use a database)
user_data = {}
user_rate_limits = {}

# Constants
MAX_PROMPT_LENGTH = 500
RATE_LIMIT_PER_USER = 10  # images per hour

def get_user_limit(user_id: int) -> int:
    """Get remaining rate limit for a user"""
    current_hour = int(time.time() / 3600)
    key = f"{user_id}_{current_hour}"
    
    if key not in user_rate_limits:
        user_rate_limits[key] = RATE_LIMIT_PER_USER
    
    return user_rate_limits[key]

def decrement_user_limit(user_id: int) -> bool:
    """Decrement user's rate limit, return False if exceeded"""
    current_hour = int(time.time() / 3600)
    key = f"{user_id}_{current_hour}"
    
    if key not in user_rate_limits:
        user_rate_limits[key] = RATE_LIMIT_PER_USER
    
    if user_rate_limits[key] <= 0:
        return False
    
    user_rate_limits[key] -= 1
    return True

# ==================== COMMAND HANDLERS ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command"""
    welcome_message = (
        "🎨 Welcome to ImageGen Bot!\n\n"
        "I can generate images from your text descriptions using advanced AI.\n\n"
        "📝 **How to use:**\n"
        "Simply send me any text describing the image you want.\n\n"
        "✨ **Tips for best results:**\n"
        "• Be specific and descriptive\n"
        "• Mention the style (photorealistic, cartoon, oil painting, etc.)\n"
        "• Include details about lighting, colors, and composition\n"
        "• Try to be creative!\n\n"
        "🔧 **Available commands:**\n"
        "/start - Show this message\n"
        "/help - Get detailed help\n"
        "/status - Check your usage limits\n"
        "/provider - Change AI provider (if available)\n\n"
        "📸 **Example prompt:**\n"
        "'A photorealistic sunset over a calm ocean with seagulls flying in the distance, warm golden lighting'"
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
        "• 'Minimalist geometric abstract art in blue and gold'\n"
        "• 'Photorealistic close-up of a dewdrop on a green leaf'\n\n"
        "🔄 **Rate Limits:** You can generate up to 10 images per hour.\n\n"
        "⚙️ **Commands:**\n"
        "/start - Welcome message\n"
        "/help - This help\n"
        "/status - Check your usage\n"
        "/provider - Change AI model\n"
        "/cancel - Cancel current operation (if any)"
    )
    
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show user's current usage status"""
    user_id = update.effective_user.id
    remaining = get_user_limit(user_id)
    
    status_message = (
        f"📊 **Your Status**\n\n"
        f"🔄 Images remaining this hour: {remaining}\n"
        f"⏰ Hour resets at: {':00'}\n\n"
        f"🤖 Current AI provider: {context.user_data.get('provider', DEFAULT_PROVIDER)}\n"
        f"📝 Max prompt length: {MAX_PROMPT_LENGTH} characters"
    )
    
    await update.message.reply_text(status_message, parse_mode='Markdown')

async def provider_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Change AI provider"""
    if len(providers) < 2:
        await update.message.reply_text("ℹ️ Only one AI provider is available.")
        return
    
    keyboard = []
    for provider in providers.keys():
        keyboard.append([InlineKeyboardButton(
            f"{'✅ ' if provider == context.user_data.get('provider', DEFAULT_PROVIDER) else ''}{provider.upper()}",
            callback_data=f'provider_{provider}'
        )])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🤖 **Select AI Provider:**\n\n"
        "Choose which AI model to use for image generation:",
        parse_mode='Markdown',
        reply_markup=reply_markup
    )

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancel current operation"""
    context.user_data.clear()
    await update.message.reply_text("✅ Operation cancelled.")

# ==================== MESSAGE HANDLERS ====================

async def generate_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate image from text prompt"""
    user_id = update.effective_user.id
    prompt = update.message.text
    
    # Check prompt length
    if len(prompt) > MAX_PROMPT_LENGTH:
        await update.message.reply_text(
            f"❌ Prompt is too long! Maximum {MAX_PROMPT_LENGTH} characters. "
            f"Your prompt has {len(prompt)} characters."
        )
        return
    
    # Check rate limit
    if not decrement_user_limit(user_id):
        await update.message.reply_text(
            "⚠️ You've reached the hourly limit of 10 images. Please try again later."
        )
        return
    
    # Send typing indicator
    await update.message.chat.send_action(action="typing")
    
    # Get provider
    provider = context.user_data.get('provider', DEFAULT_PROVIDER)
    
    if provider not in providers:
        await update.message.reply_text(
            f"❌ Provider '{provider}' is not available. Using default provider."
        )
        provider = DEFAULT_PROVIDER
    
    # Send initial processing message
    processing_msg = await update.message.reply_text(
        f"🎨 Generating image...\n"
        f"📝 Prompt: '{prompt[:50]}...'\n"
        f"🤖 Provider: {provider.upper()}\n\n"
        f"⏳ This may take a few seconds..."
    )
    
    try:
        # Generate image based on provider
        if provider == 'gemini':
            image_data = await generate_with_gemini(prompt)
        elif provider == 'openai':
            image_data = await generate_with_openai(prompt)
        elif provider == 'replicate':
            image_data = await generate_with_replicate(prompt)
        else:
            raise ValueError(f"Unknown provider: {provider}")
        
        # Delete the processing message
        await processing_msg.delete()
        
        # Send the generated image
        photo_file = io.BytesIO(image_data)
        photo_file.name = f"image_{int(time.time())}.png"
        
        caption = (
            f"✨ **Generated Image**\n\n"
            f"📝 Prompt: {prompt}\n"
            f"🤖 Provider: {provider.upper()}\n"
            f"📅 Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )
        
        await update.message.reply_photo(
            photo=photo_file,
            caption=caption,
            parse_mode='Markdown'
        )
        
        logger.info(f"Generated image for user {user_id} using {provider}: {prompt[:50]}...")
        
    except Exception as e:
        logger.error(f"Error generating image: {e}")
        await processing_msg.edit_text(
            f"❌ Failed to generate image.\n\n"
            f"Error: {str(e)[:100]}\n\n"
            f"Please try again with a different prompt or try again later."
        )
        # Refund the rate limit on error
        current_hour = int(time.time() / 3600)
        key = f"{user_id}_{current_hour}"
        if key in user_rate_limits:
            user_rate_limits[key] += 1

# ==================== AI PROVIDERS ====================

async def generate_with_gemini(prompt: str) -> bytes:
    """Generate image using Google Gemini"""
    try:
        response = model.generate_content(
            f"Generate an image: {prompt}",
            generation_config=genai.types.GenerationConfig(
                temperature=1.0,
                candidate_count=1
            )
        )
        
        # Extract image data from response
        if response._result.candidates:
            for candidate in response._result.candidates:
                if hasattr(candidate, 'content') and candidate.content.parts:
                    for part in candidate.content.parts:
                        if hasattr(part, 'inline_data') and part.inline_data.mime_type.startswith('image/'):
                            return part.inline_data.data
        
        raise ValueError("No image data found in Gemini response")
        
    except Exception as e:
        logger.error(f"Gemini error: {e}")
        raise

async def generate_with_openai(prompt: str) -> bytes:
    """Generate image using OpenAI DALL-E"""
    try:
        # Use the older DALL-E 2 model which is faster and cheaper
        response = openai.Image.create(
            prompt=prompt,
            n=1,
            size="1024x1024",
            quality="standard"
        )
        
        # Download the image
        image_url = response['data'][0]['url']
        image_response = requests.get(image_url)
        return image_response.content
        
    except Exception as e:
        logger.error(f"OpenAI error: {e}")
        raise

async def generate_with_replicate(prompt: str) -> bytes:
    """Generate image using Replicate (Stable Diffusion)"""
    try:
        # Using Stable Diffusion XL
        output = replicate.run(
            "stability-ai/stable-diffusion:db21e45d3f7023abc2a46ee38a23973f6dce16bb082a930b0c49861f96d1e5bf",
            input={
                "prompt": prompt,
                "negative_prompt": "ugly, deformed, blurry",
                "width": 1024,
                "height": 1024,
                "num_outputs": 1,
                "num_inference_steps": 30,
                "guidance_scale": 7.5
            }
        )
        
        # Download the image
        image_response = requests.get(output[0])
        return image_response.content
        
    except Exception as e:
        logger.error(f"Replicate error: {e}")
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
            "1. 'A photorealistic sunset over a calm ocean with seagulls flying in the distance, warm golden lighting'\n"
            "2. 'A majestic dragon with iridescent scales flying over ancient mountains at dusk'\n"
            "3. 'Cyberpunk cityscape at night, neon signs, rain-slicked streets, reflections, high-tech'\n"
            "4. 'Anime-style portrait of a magical girl with glowing blue eyes in a starry background'\n"
            "5. 'Oil painting of a majestic forest with ancient trees, golden sunlight rays, mystical atmosphere'"
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
    # Validate configuration
    if not providers:
        logger.error("No AI providers configured! Please set at least one API key.")
        logger.error("Supported providers: GEMINI_API_KEY, OPENAI_API_KEY, REPLICATE_API_KEY")
        return
    
    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_TOKEN is not set!")
        return
    
    # Create application
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    # Add command handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("provider", provider_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    
    # Add message handler for text messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, generate_image))
    
    # Add callback handler for inline buttons
    app.add_handler(CallbackQueryHandler(button_callback))
    
    logger.info("Starting bot with long polling...")
    logger.info(f"Available providers: {', '.join(providers.keys())}")
    logger.info(f"Default provider: {DEFAULT_PROVIDER}")
    
    # Start the bot using long polling
    app.run_polling()

if __name__ == "__main__":
    main()
