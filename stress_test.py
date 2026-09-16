import asyncio
import random
import hashlib
from invoice_auditor.ingestion.gemini import GeminiInvoiceExtractor
from invoice_auditor.config import get_settings

VENDORS = [
    ("Acme Corp", "US-9871234"),
    ("CloudScale Inc", "US-1122334"),
    ("CyberShield Ltd", "UK-8877665"),
    ("Global Logistics", "EU-4455667"),
    ("Office Supplies Hub", "US-3344556")
]

ITEMS = [
    ("Database Hosting", 200, 800),
    ("Consulting Retainer", 1000, 3000),
    ("Security Audit", 500, 1500),
    ("Software License", 100, 400),
    ("Hardware Maintenance", 300, 900)
]

def generate_pdf_bytes(inv_num, vendor, tax_id, date_str, line_items, subtotal, tax, total):
    items_text = ""
    for idx, (desc, price) in enumerate(line_items, 1):
        items_text += f"0 -20 Td ({idx}. {desc} - {price:.2f} USD) Tj\n"

    stream_content = f'''BT
/F1 12 Tf
50 720 Td (INVOICE #{inv_num}) Tj
0 -25 Td (Vendor: {vendor}) Tj
0 -20 Td (Tax ID: {tax_id}) Tj
0 -20 Td (Date: {date_str}) Tj
0 -30 Td (--- LINE ITEMS ---) Tj
{items_text}0 -30 Td (Subtotal: {subtotal:.2f} USD) Tj
0 -20 Td (Tax: {tax:.2f} USD) Tj
0 -20 Td (Total: {total:.2f} USD) Tj
ET'''.encode()

    pdf_template = b'''%PDF-1.4
1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj
2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj
3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj
4 0 obj << /Length ''' + str(len(stream_content)).encode() + b''' >>
stream
''' + stream_content + b'''
endstream
endobj
5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj
xref
0 6
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
0000000115 00000 n 
0000000261 00000 n 
0000000620 00000 n 
trailer << /Size 6 /Root 1 0 R >>
startxref
700
%%EOF'''
    return pdf_template

async def run_batch_test(total_invoices=5):
    settings = get_settings()
    extractor = GeminiInvoiceExtractor.from_settings(settings)
    
    print(f"\n==========================================")
    print(f"  STARTING STRESS TEST: {total_invoices} Invoices")
    print(f"==========================================")
    
    passed_clean = 0
    caught_math_fraud = 0
    ai_errors = 0

    for i in range(1, total_invoices + 1):
        inv_num = f"INV-TEST-{1000 + i}"
        vendor, tax_id = random.choice(VENDORS)
        date_str = f"2026-09-{random.randint(1, 28):02d}"
        
        selected_items = random.sample(ITEMS, 2)
        line_items = [(name, random.randint(low, high)) for name, low, high in selected_items]
        subtotal = sum(price for _, price in line_items)
        tax = round(subtotal * 0.10, 2)
        
        # Test Case Scenarios
        is_fraud = (i % 2 == 0) # Har doosra bill intentionally ghalat math wala hoga
        if is_fraud:
            scenario = "TAMPERED_MATH (Extra  added)"
            total = subtotal + tax + 50.0
        else:
            scenario = "CLEAN_BILL"
            total = subtotal + tax

        pdf_bytes = generate_pdf_bytes(inv_num, vendor, tax_id, date_str, line_items, subtotal, tax, total)
        
        print(f"\n[{i}/{total_invoices}] Sending {inv_num} [{scenario}] to Gemini...")
        
        try:
            extraction = await extractor.extract(pdf_bytes, "application/pdf")
            ext_subtotal = float(extraction.subtotal)
            ext_tax = float(extraction.tax_amount)
            ext_total = float(extraction.total_amount)
            
            # Audit Logic: Math Check
            expected_calc = round(ext_subtotal + ext_tax, 2)
            has_math_error = abs(expected_calc - ext_total) > 0.01

            if has_math_error:
                print(f"  [FLAGGED] Fraud/Math Error Caught!")
                print(f"     Subtotal ({ext_subtotal}) + Tax ({ext_tax}) = {expected_calc} != Total ({ext_total})")
                caught_math_fraud += 1
            else:
                print(f"  [APPROVED] Clean Match: {ext_subtotal} + {ext_tax} == {ext_total}")
                passed_clean += 1

        except Exception as e:
            print(f"  [ERROR] Extraction failed: {e}")
            ai_errors += 1

        await asyncio.sleep(2) # Rate limit safety

    print("\n" + "="*40)
    print("=== AUDIT VERIFICATION REPORT ===")
    print(f"Total Processed       : {total_invoices}")
    print(f"Clean Bills Approved  : {passed_clean}")
    print(f"Math Fraud Caught     : {caught_math_fraud}")
    print(f"AI/System Failures    : {ai_errors}")
    print("="*40)

if __name__ == '__main__':
    asyncio.run(run_batch_test(5))
