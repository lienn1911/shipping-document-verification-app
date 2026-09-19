"""Fact-based correspondence prepared for a human to approve and send."""

def build_reply(email, detail):
    lines = ["Hello,", "", f"We have reviewed shipment request {email['email_id']} against the Shipping Instruction.", ""]
    if detail.get("status") == "MISMATCH":
        lines.append("The following differences remain in the draft Bill of Lading:")
        for item in detail.get("mismatches", []):
            lines.append(f"- {item['field'].replace('_', ' ').title()}: SI: {item['si_value']} / BL: {item['bl_value']}")
        lines.extend(["", "Please issue a revised draft BL with the values confirmed against the SI."])
    elif detail.get("status") == "OK":
        lines.append("No mismatch detected across the seven reviewed shipment fields.")
    else:
        lines.append("Further verification is needed before we can confirm the shipment details.")
    review = detail.get("human_review", {})
    original, current = review.get("original_extracted", {}), review.get("reviewed_values", {})
    changes = []
    for role, fields in current.items():
        for field, value in fields.items():
            before = original.get(role, {}).get(field, "Not extracted")
            if str(before).strip() != str(value).strip():
                changes.append(f"- {role.upper()} {field.replace('_', ' ')}: {before} → {value}")
    if changes:
        lines.extend(["", "Our verification record was updated after manual review:", *changes])
    lines.extend(["", "This review updates our verification record only; original shipping documents have not been amended.", "", "Regards,", "Shipping Operations"])
    return {"to": email.get("from", ""), "subject": "Re: " + email.get("subject", "Shipping document review"), "body": "\n".join(lines)}
