from ai_service import analyze_shipping_document


result = analyze_shipping_document(
    """
    Bill of Lading

    Shipper: ABC Trading
    Port of Loading: Port Klang
    Container Number: ABC12345
    Gross Weight: 20000 KG
    """
)

print(result)
