"""
Test: Extrahera bara Q1 från Vitrolife för snabb test
"""

import asyncio
import os
import json
from extract_data import extract_tables_from_pdf

async def test_q1():
    print("🚀 Starting Q1 extraction test...\n")
    
    result = await extract_tables_from_pdf(
        "vitrolife_reports/vitrolife-q1-2025.pdf",
        "vitrolife",
        "q1",
        2025
    )
    
    print("\n" + "="*60)
    print("✅ Q1 Extraction Result:")
    print("="*60)
    
    print(f"\nFull result keys: {result.keys()}")
    print(f"Has data: {'data' in result}")
    print(f"Data value: {result.get('data')}")
    
    if result.get("error"):
        print(f"\n❌ Error: {result.get('error')}")
        return
    
    if result.get("data"):
        data = result["data"]
        print(f"\nData keys: {data.keys()}")
        
        print(f"\n📊 Income Statement (first 5 rows):")
        for row in data.get("income_statement", [])[:5]:
            print(f"  {row}")
        
        print(f"\n💰 Balance Sheet (first 5 rows):")
        for row in data.get("balance_sheet", [])[:5]:
            print(f"  {row}")
        
        print(f"\n💵 Cash Flow (first 5 rows):")
        for row in data.get("cash_flow", [])[:5]:
            print(f"  {row}")
    else:
        print(f"❌ No data extracted")

if __name__ == "__main__":
    # Set ANTHROPIC_API_KEY environment variable before running
    asyncio.run(test_q1())
