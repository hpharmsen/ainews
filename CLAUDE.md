# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is an AI-powered newsletter generation system that:
1. Fetches AI-related emails from Gmail using IMAP (filtered by 'ai_news' label)
2. Uses AI models to generate summaries and select relevant content
3. Creates HTML newsletters with generated images
4. Sends newsletters via SMTP to subscribers
5. Stores newsletter data in PostgreSQL database

## Architecture

- **main.py**: Entry point with command parsing for daily/weekly newsletters
- **gmail.py**: IMAP email fetching and processing (Mail class)
- **ai.py**: AI content generation using JustAI library (summaries, images)
- **formatter.py**: HTML email template generation
- **mailer.py**: SMTP newsletter sending with logging
- **database.py**: PostgreSQL database operations using SQLAlchemy
- **subscribers.py**: Subscriber management
- **s3.py**: S3 integration for image storage

## Dependencies

- **justai**: Custom AI wrapper library for multiple LLM providers
- **justdays**: Custom date/time utility library
- **sqlalchemy**: Database ORM
- **psycopg2-binary**: PostgreSQL adapter
- **boto3**: AWS S3 integration
- **python-dotenv**: Environment variable management

## Commands

- **Run newsletter**: `python main.py daily` or `python main.py weekly`
- **Use cached data**: `python main.py daily --cached` (skips email fetching)
- **Install dependencies**: `pip install -r requirements.txt`

## Configuration

Requires `.env` file with:
- EMAIL_HOST_USER, EMAIL_HOST_PASSWORD: Gmail credentials
- EMAIL_IMAP_SERVER, EMAIL_IMAP_PORT: IMAP settings
- EMAIL_HOST, EMAIL_PORT: SMTP settings
- DATABASE_URL: PostgreSQL connection string
- AWS credentials for S3 image storage

## AI Models Used

- **Copy writing**: Gemini 2.5 Pro (Dutch newsletter content)
- **Art direction**: GPT-5 (image concept generation)
- **Image generation**: Gemini 2.5 Flash Image Preview

## Data Storage

- **cache/**: Cached AI responses and email data
- **data/**: Runtime data (last_sent.json, mailerlog.txt, logo.png)
- Database stores newsletter content and metadata

## Newsletter Process

1. Parse command line (daily/weekly, --cached flag)
2. Fetch raw emails from Gmail with ai_news label
3. Generate AI summary from email content (Dutch language)
4. Create newsletter image using AI art direction + generation
5. Format as HTML email with branding
6. Store in database and send to subscribers via SMTP
7. Log delivery and track sent emails

## Content Style

The AI generates content in Dutch, following specific style guidelines:
- Professional but accessible tone for business/tech readers
- Evidence-first approach with concrete dates and numbers
- Actionable insights for readers
- Critical perspective on AI developments while remaining optimistic