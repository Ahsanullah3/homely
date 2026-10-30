import json
import os
import re
import gspread
from bs4 import BeautifulSoup
from curl_cffi import requests

# 1. Authenticate Google Sheets via Environment Secret
gcp_credentials = json.loads(os.environ["GCP_SERVICE_ACCOUNT_KEY"])
gc = gspread.service_account_from_dict(gcp_credentials)

# 2. Open Target Sheet
sheet = gc.open("Homely Agents Data").sheet1

# Ensure column headers exist in row 1
headers = ["URL", "Name", "Agency", "Phone", "Email", "Image URL", "Status"]
existing_headers = sheet.row_values(1)
if not existing_headers:
    sheet.update(range_name="A1:G1", values=[headers])

# 3. Read All Profile URLs from Column A (starting from Row 2)
urls_in_col_a = sheet.col_values(1)[1:]  # Excludes the header

if not urls_in_col_a:
    print("No URLs found in Column A to process.")
    exit()

session = requests.Session(impersonate="chrome120")

def extract_agent_data(url):
    try:
        response = session.get(url, timeout=15)
        if response.status_code != 200:
            print(f"Failed to fetch {url} (Status: {response.status_code})")
            return ["", "", "", "", "", f"Error: HTTP {response.status_code}"]

        # Extract structured data from Next.js JSON-LD Schema
        match = re.search(r'(\{"@context":"https://schema\.org","@type":"RealEstateAgent".*?\})', response.text)
        if match:
            json_str = match.group(1).replace(r'\"', '"')
            data = json.loads(json_str)
            
            description = data.get("description", "")
            agency = description.split("- ")[-1].split(".")[0].strip() if "-" in description else ""

            return [
                data.get("name", ""),
                agency,
                data.get("telephone", ""),
                data.get("email", ""),
                data.get("image", ""),
                "Scraped"
            ]

        # Fallback to HTML parsing if script tag is missing
        soup = BeautifulSoup(response.text, "html.parser")
        h1 = soup.find("h1")
        name = h1.get_text(strip=True) if h1 else ""
        
        return [name, "", "", "", "", "Scraped (HTML Fallback)"]

    except Exception as e:
        print(f"Error scraping {url}: {e}")
        return ["", "", "", "", "", f"Error: {str(e)}"]

# 4. Process Each URL and Build Range Update Payload
updates = []
start_row = 2

for idx, url in enumerate(urls_in_col_a):
    current_row = start_row + idx
    url_clean = url.strip()
    
    if not url_clean.startswith("http"):
        print(f"Row {current_row}: Skipping invalid URL '{url}'")
        continue

    print(f"Row {current_row}: Processing {url_clean}")
    row_data = extract_agent_data(url_clean)
    
    # Store cell update range (B{row}:G{row})
    updates.append({
        "range": f"B{current_row}:G{current_row}",
        "values": [row_data]
    })

# 5. Batch Update Google Sheet
if updates:
    sheet.batch_update(updates)
    print(f"\nSuccessfully updated {len(updates)} rows in Google Sheets!")
