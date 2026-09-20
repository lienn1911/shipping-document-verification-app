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

def extract_fields_with_ai(doc_text, doc_type):
    if not model:
        return {}
    
    prompt = f"""Extract these 7 fields from this {doc_type} document:
{doc_text[:3000]}
Fields: shipper, consignee, notify_party, port_of_loading, port_of_discharge, container_count, gross_weight_kg
Return JSON with each field having: raw_value, normalized_value, confidence (0.0-1.0)"""
    
    try:
        resp = model.generate_content(prompt)
        txt = resp.text.strip()
        if "```json" in txt:
            txt = txt.split("```json")[1].split("```")[0]
        return json.loads(txt)
    except Exception as e:
        print(f"Extract error: {e}")
        return {}