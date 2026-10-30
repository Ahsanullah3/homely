import json
import os
import re
import gspread
from bs4 import BeautifulSoup
from curl_cffi import requests

def main():
    # 1. Authenticate with Google Sheets using the repository secret
    secret_key = os.environ.get("GCP_SERVICE_ACCOUNT_KEY")
    if not secret_key:
        raise ValueError("GCP_SERVICE_ACCOUNT_KEY environment variable is missing.")

    gcp_credentials = json.loads(secret_key)
    gc = gspread.service_account_from_dict(gcp_credentials)

    # 2. Open the Google Sheet (Ensure this matches your actual Sheet title)
    sheet = gc.open("Homely Agents Data").sheet1

    # 3. Ensure header row exists
    headers = ["URL", "Name", "Agency", "Phone", "Email", "Image URL", "Status"]
    existing_headers = sheet.row_values(1)
    if not existing_headers:
        sheet.update(range_name="A1:G1", values=[headers])

    # 4. Fetch all URLs from Column A (excluding header)
    urls_in_col_a = sheet.col_values(1)[1:]
    if not urls_in_col_a:
        print("No URLs found in Column A to process.")
        return

    session = requests.Session(impersonate="chrome120")
    updates = []
    start_row = 2

    for idx, url in enumerate(urls_in_col_a):
        current_row = start_row + idx
        url_clean = url.strip()

        if not url_clean.startswith("http"):
            print(f"Row {current_row}: Skipping non-URL input '{url_clean}'")
            continue

        print(f"Row {current_row}: Extracting {url_clean}")
        row_data = extract_agent_data(session, url_clean)

        updates.append({
            "range": f"B{current_row}:G{current_row}",
            "values": [row_data]
        })

    # 5. Execute batch update back to Google Sheets
    if updates:
        sheet.batch_update(updates)
        print(f"\nSuccessfully updated {len(updates)} records in Google Sheets!")

def extract_agent_data(session, url):
    try:
        response = session.get(url, timeout=15)
        if response.status_code != 200:
            print(f"HTTP Error {response.status_code} for {url}")
            return ["", "", "", "", "", f"Error: HTTP {response.status_code}"]

        # Search for embedded JSON-LD RealEstateAgent schema
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

        # Fallback to HTML header parsing
        soup = BeautifulSoup(response.text, "html.parser")
        h1 = soup.find("h1")
        name = h1.get_text(strip=True) if h1 else ""

        return [name, "", "", "", "", "Scraped (HTML Fallback)"]

    except Exception as e:
        print(f"Execution error on {url}: {e}")
        return ["", "", "", "", "", f"Error: {str(e)}"]

if __name__ == "__main__":
    main()
