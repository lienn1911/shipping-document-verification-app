import os
import json
from dotenv import load_dotenv
import google.generativeai as genenv

load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
model = None

if GEMINI_API_KEY:
    genenv.configure(api_key=GEMINI_API_KEY)
    model = genenv.GenerativeModel("gemini-2.0-flash")

def classify_email_with_ai(subject, body, attachments):
    if not model:
        return {"category": "GENERAL", "confidence": 0.5, "reasoning": "AI not configured", "review_required": True}
    
    prompt = f"""Classify this shipping email:
Subject: {subject}
Body: {body[:1000]}
Attachments: {attachments}
Categories: BL_COMPARISON, SI_REQUEST, INVOICE_QUERY, GENERAL, SPAM
Return JSON: {{"category": "...", "confidence": 0.0-1.0, "reasoning": "...", "review_required": false}}"""
    
    try:
        resp = model.generate_content(prompt)
        txt = resp.text.strip()
        if "```json" in txt:
            txt = txt.split("```json")[1].split("```")[0]
        return json.loads(txt)
    except Exception as e:
        return {"category": "GENERAL", "confidence": 0, "reasoning": f"Error: {str(e)}", "review_required": True}