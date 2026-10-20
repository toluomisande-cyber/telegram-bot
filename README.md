# Personal Telegram AI Assistant Bot

A personal Telegram assistant bot powered by Groq and Llama 3.3.

## Features
- 📰 **World News Digest (`/news [topic]`)**: Fetches latest RSS feeds (BBC, Al Jazeera, NPR) and summarizes them into structured bullet points with Groq.
- ⏰ **Scheduled Daily Briefing (`/daily HH:MM` | `/daily off`)**: Schedules an automated daily news briefing using Telegram's JobQueue.
- ✉️ **AI Email Drafter (`/email to | subject | what to say`)**: Writes concise emails with Groq, displays an inline preview with Send/Cancel confirmation, and sends via SMTP SSL.
- 📇 **Address Book (`/contact add|remove|list`)**: Stores contact nicknames mapped to email addresses in `contacts.json`.
- 💬 **Conversational AI Chat**: Natural chat with conversation history memory (persists last 12 turns per user).
- 🔒 **Owner Security**: Strict access control restricted to `OWNER_CHAT_ID`.

---

## Setup & Running

### 1. Create a Virtual Environment (Optional but recommended)
Open PowerShell in this directory:
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

### 2. Install Dependencies
```powershell
pip install -r requirements.txt
```
> **Note**: `python-telegram-bot[job-queue]` is required for the `/daily` scheduled jobs.

### 3. Configure Environment Variables
Create a `.env` file from `.env.example`:
```powershell
copy .env.example .env
```

Open `.env` and fill in your values:
- `TELEGRAM_TOKEN`: your bot token from [@BotFather](https://t.me/BotFather)
- `OWNER_CHAT_ID`: 
- `GROQ_API_KEY`: your key from [console.groq.com](https://console.groq.com)
- `SMTP_USER` & `SMTP_PASS`: For sending emails (e.g. Gmail with an [App Password](https://myaccount.google.com/apppasswords)).

### 4. Run the Bot
```powershell
python main.py
```
